#!/usr/bin/env python3
"""A Clip deleted while MIDI follow still remembers a note of it, on the v1.3 beta (62a516c2): MIDI follow keeps the
freed Clip. MidiFollow::sendNoteToClip() remembers, per note, the Clip a note-on went to (clipForLastNoteReceived[]), so
that the note-off reaches that Clip even if the context has changed meanwhile; "all notes off" goes to every Clip
remembered. Only SessionView::removeClip() makes it forget a Clip (MidiFollow::removeClip()). The other ways a Clip is
deleted don't: undo of its creation (once the action log is cleared), the arranger deleting an arrangement-only Clip,
Song::swapClips() (a Clip's type changed), Song::deletePendingOverdubs() (an overdub that never began). A key held
meanwhile then sends its note-off through the freed Clip (its output, the instrument's receivedNote() through a vtable
in freed memory), and so does "all notes off" for every note still remembered. The fuzzer sends no MIDI, so this came
from reading the code after 0019's audit (which Clip pointers outlive their Clip).

One boot of the real firmware in the emulator (OLED), make_card's song. Two session Clips remembered for notes 60 and
61, as held note-ons through MIDI follow leave them (written into clipForLastNoteReceived[]). The second deleted by the
session view's own delete (SessionView::removeClip(), the control: forgotten on 62a516c2 too), the first by
Song::deletePendingOverdubs() after marking it a pending overdub (as an overdub that never began ends). Checked: both
Clips gone from sessionClips, MIDI follow remembers neither, and no freeze, crash or access outside the mapped memory.

Usage: midi_follow_clip_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot and Song; puts the rig on the path)
import oom_robust_emu as oom  # noqa: E402  (call(): a direct call under the rig's watchdog)
import stress_ui_emu as su  # noqa: E402

NOTE_DELETED, NOTE_CONTROL = 60, 61


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rig, inp = kr.boot(a, "boot")
    emu = rig.emu
    sym = emu.sym
    song = kr.Song(rig)
    pending_off, = su.gdb_ints(emu, ["list Song::Song", "print (int)&((Clip*)0)->isPendingOverdub"])
    table = sym["clipForLastNoteReceived"]  # MidiFollow's Clip* clipForLastNoteReceived[128]

    def remembered(note):
        return emu.u32(table + 4 * note)
    res = {}
    try:
        clips = song.clips()
        first, second = clips[0], clips[1]
        res["clips_before"] = len(clips)
        emu.uc.mem_write(table + 4 * NOTE_DELETED, struct.pack("<I", first))
        emu.uc.mem_write(table + 4 * NOTE_CONTROL, struct.pack("<I", second))

        # the control: the session view's delete
        oom.call(rig, "SessionView::removeClip(the second Clip)", sym.find("_ZN11SessionView10removeClipEP4Clip"),
                 (sym["sessionView"], second))
        rig.tm(0.3, "after the session view's delete")
        res["control_gone"] = second not in song.clips()
        res["control_remembered"] = hex(remembered(NOTE_CONTROL))

        # the first Clip as a pending overdub, deleted as an overdub that never began
        emu.uc.mem_write(first + pending_off, b"\x01")
        oom.call(rig, "Song::deletePendingOverdubs()", sym.find("_ZN4Song21deletePendingOverdubsEP6OutputPlb"),
                 (rig.song(), 0, 0, 0))
        rig.tm(0.3, "after the pending overdub's delete")
        res["gone"] = first not in song.clips()
        res["remembered"] = hex(remembered(NOTE_DELETED))
        res["clips_after"] = len(song.clips())
        res["ui_after"] = rig.ui_name()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["invalid"] = [(hex(k[0]), k[1], k[2]) for k in rig.invalid]
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    ok = (res.get("control_gone") and res.get("control_remembered") == "0x0" and res.get("gone")
          and res.get("remembered") == "0x0" and not res["invalid"] and not res["problems"])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
