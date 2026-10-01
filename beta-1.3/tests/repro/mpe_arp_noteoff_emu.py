#!/usr/bin/env python3
"""The fuzz crash seed 3001 (--mode all, --7seg, input 791) on the v1.3 beta: UC_ERR_MAP in Patcher::performPatching()
under Sound::render(), a synth's voices_ buffer full of foreign data. The cause is a MIDI clip with MPE output (the
MPE lower or upper zone as its channel) and the arpeggiator on: every note-off of a held note reaches
MIDIInstrument::noteOffPostArp() a second time from NonAudioInstrument::sendNote() with the member channel
MIDI_CHANNEL_NONE (255). Arpeggiator::noteOff() hands back the notes[] entry's outputMemberChannel, which is never set
(switchNoteOn() copies the entry into active_note, and MIDIInstrument::noteOnPostArp() writes the channel there only).
The MPE branch then writes mpeOutputMemberChannels[255] (lastNoteCode, noteOffOrder; 16 entries of 8 bytes), 2 KB past
the array: whatever object lies there gets two int16s. In the fuzz run that was a CVInstrument's arpeggiator: its
notes.memorySize took the note code, its notes array then wrote over a synth's voices_ buffer, and the render read a
voice pointer 0xffffffff.

One boot (OLED): session view, the synth clip PADA entered, MIDI (the clip becomes a MIDI clip), its channel set to
the MPE lower zone (written into NonAudioInstrument::channel, as the select encoder would set it), SHIFT + pad 11,4
(the arp preset menu of a MIDI clip) and select +1 (UP: the arpeggiator on), BACK. Then three audition pads, each held
0.5 s and released. Checked:
  (1) the scenario: the clip is MIDI, sends to MPE, its arp mode is ARP, and noteOffPostArp() was called with a
      member channel >= 16 at least once (the path is reached)
  (2) no write from MIDIInstrument code into the 2 KB after mpeOutputMemberChannels[15] (an out-of-bounds write)
  (3) no crash, freeze or hang
On 62a516c2 (also with 0001-0016, 0101, 0102): (2) fails, two writes at mpeOutputMemberChannels[255] per release;
with 0017: PASS.

Usage: mpe_arp_noteoff_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS (all checks hold) or FAIL (which ones didn't); exit 0/1."""
import argparse
import collections
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # beta-1.3/tests
sys.path.insert(0, RIG)
import rig13  # noqa: E402,F401  (sets up the paths of mastertune's rig)
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402
import unicorn  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R2  # noqa: E402

NUM_MEMBER_CHANNELS = 16


class Song:
    """The song's session clips (make_card's song with 2 synths: PADA, PADB, the kit, the audio track) and the session
    view's scroll: clip i sits on y = i - songViewYScroll (as in kitrow_emu.py)."""

    def __init__(self, rig):
        self.rig = rig
        self.clips_off, self.current_off, self.yscroll_off, self.output_off = su.gdb_ints(rig.emu, [
            "list Song::Song", "print (int)&((Song*)0)->sessionClips", "print (int)&((Song*)0)->currentClip",
            "print (int)&((Song*)0)->songViewYScroll", "print (int)&((Clip*)0)->output"])

    def clips(self):
        emu, rig = self.rig.emu, self.rig
        _, _, memory, count, msize, mstart, esize = rig.heap_offsets
        arr = rig.song() + self.clips_off
        mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
        return [emu.u32(mem + ((first + i) % max(size, 1)) * es) for i in range(n)]

    def scroll(self):
        return struct.unpack("<i", self.rig.emu.uc.mem_read(self.rig.song() + self.yscroll_off, 4))[0]

    def current(self):
        return self.rig.emu.u32(self.rig.song() + self.current_off)

    def enter(self, inp, index):
        rig = self.rig
        for _ in range(16):
            if index - self.scroll() == 6:
                break
            inp.turn("scrollY", 1 if index - self.scroll() > 6 else -1)
            rig.tm(0.05, "scrollY")
        inp.pad(0, 6, 100)
        rig.tm(0.05, "clip pad held")
        inp.pad(0, 6, 0)
        rig.tm(1.0, "enter clip")
        return self.current() == self.clips()[index]


def press(rig, inp, name, hold=0.05, after=0.5):
    inp.button(name, True)
    rig.tm(hold, f"{name} held")
    inp.button(name, False)
    rig.tm(after, f"after {name}")


def run(a, res):
    out = os.path.join(a.out, "boot")
    os.makedirs(out, exist_ok=True)
    image = os.path.join(out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    fuzz_ui.make_card(image, 2, os.path.join(a.out, "card-template.img"))
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    emu, sym = rig.emu, rig.emu.sym
    inp = fuzz_ui.Inputs(rig, "v13")
    (mpe_off, ch_off, type_off, arp_mode_off, midi_out, mpe_lower, arp_on) = su.gdb_ints(emu, [
        "list Sound::render", "print (int)&((MIDIInstrument*)0)->mpeOutputMemberChannels",
        "print (int)&((NonAudioInstrument*)0)->channel", "print (int)&((Output*)0)->type",
        "print (int)&((InstrumentClip*)0)->arpSettings.mode", "print (int)OutputType::MIDI_OUT",
        "print (int)MIDI_CHANNEL_MPE_LOWER_ZONE", "print (int)ArpMode::ARP"])
    calls = collections.Counter()  # noteOffPostArp() by member channel: "valid" / "none"
    oob = []

    def on_note_off(e):
        if e.uc.reg_read(UC_ARM_REG_R0) == res.get("instrument_int"):
            calls["none" if e.uc.reg_read(UC_ARM_REG_R2) >= NUM_MEMBER_CHANNELS else "valid"] += 1
    emu.intercept(sym["_ZN14MIDIInstrument14noteOffPostArpEllll"], on_note_off)
    emu.uc.ctl_flush_tb()
    try:
        song = Song(rig)
        res["entered"] = song.enter(inp, 0)  # PADA, a synth
        clip = song.current()
        press(rig, inp, "MIDI", after=1.0)  # the clip becomes a MIDI clip
        inst = emu.u32(clip + song.output_off)
        res["instrument"], res["instrument_int"] = hex(inst), inst
        res["is_midi"] = emu.u8(inst + type_off) == midi_out
        # The MPE lower zone as its channel (the select encoder steps through it after the 16 channels)
        emu.uc.mem_write(inst + ch_off, struct.pack("<i", mpe_lower))
        # The arpeggiator on: SHIFT + pad 11,4 (arp preset, MIDI/CV clips), select +1 (UP), BACK
        inp.button("SHIFT", True)
        inp.pad(11, 4, 100)
        rig.tm(0.05, "arp preset shortcut")
        inp.pad(11, 4, 0)
        inp.button("SHIFT", False)
        rig.tm(0.5, "sound editor")
        res["editor"] = rig.ui_name()
        inp.turn("select", 1)
        rig.tm(0.5, "arp preset UP")
        press(rig, inp, "BACK")
        res["arp_mode_on"] = emu.u8(clip + arp_mode_off) == arp_on
        res["ui"] = rig.ui_name()
        # Out-of-bounds writes: the 2 KB after mpeOutputMemberChannels[15], from MIDIInstrument code
        lo = inst + mpe_off + NUM_MEMBER_CHANNELS * 8
        hi = inst + mpe_off + 256 * 8

        def on_write(uc, access, address, size, value, _):
            where = sym.name_at(uc.reg_read(UC_ARM_REG_PC))
            if "MIDIInstrument::" in where and len(oob) < 50:
                oob.append(dict(index=(address - inst - mpe_off) // 8, at=hex(address), size=size,
                                value=hex(value & 0xFFFFFFFF), pc=where, during=rig.action_name))
        emu.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, on_write, begin=lo, end=hi - 1)
        for y in (2, 3, 4):  # three audition pads, held 0.5 s each
            inp.pad(17, y, 100)
            rig.tm(0.5, f"audition {y} held")
            inp.pad(17, y, 0)
            rig.tm(0.5, f"audition {y} released")
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res.pop("instrument_int", None)
    res["note_off_calls"] = dict(calls)
    res["oob_writes"] = oob
    res["problems"] = rig.problems
    res["error_popups"] = rig.error_popups()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    res = dict(elf=a.elf)
    run(a, res)
    json.dump(res, open(os.path.join(a.out, "mpe_arp_noteoff.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "oob_writes"}, default=str))
    for w in res["oob_writes"][:6]:
        print("  oob write:", json.dumps(w))
    scenario = (res.get("entered") and res.get("is_midi") and res.get("arp_mode_on")
                and res["note_off_calls"].get("none", 0) >= 1)
    checks = {"(1) MPE MIDI clip, arp on, a note-off with no member channel": bool(scenario),
              "(2) no write past mpeOutputMemberChannels[15]": scenario and not res["oob_writes"],
              "(3) no crash, freeze or hang": not res["problems"]}
    for k, v in checks.items():
        print(f"  {k}: {'ok' if v else 'FAILED'}")
    failed = [k for k, v in checks.items() if not v]
    print("PASS: all three checks hold" if not failed else "FAIL: " + "; ".join(failed))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
