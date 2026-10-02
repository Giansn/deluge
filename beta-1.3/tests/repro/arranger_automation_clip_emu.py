#!/usr/bin/env python3
"""The arranger's automation view takes no type from the song's current Clip (patch 0110). On the v1.3 beta
(62a516c2) AutomationView::padAction() and selectEncoderAction() took the output type from getCurrentClip() also in
the arranger's automation view (automationView.onArrangerView), which shows no Clip, and steered by it. After a song
load the song has no current Clip: the type was read through null (0: SYNTH), and the pads of the expression
parameters (14,7, 15,0, 15,7) selected one for that "synth", writing it through the null pointer. With a current
Clip, its type steered the arranger: an audio Clip made the arranger's status and audition pads do nothing, and with
a MIDI Clip the select encoder changed that Clip's CC instead of the song's parameter.

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track), the null page writable and checked after every input (fuzz_ui.NullPage); each time in the arranger's
automation view (SONG from song view, CLIP: its overview):
  (1) no Clip entered (no current Clip): pads 14,7, 15,0 and 15,7: nothing written through null, the song's
      parameter and its shortcut stay unselected
  (2) the audio track's Clip entered before (the current Clip): the status pad of the arranger's row 7 toggles that
      Output's mute in the arrangement
  (3) PADA made MIDI and entered before: select +1 selects the song's next parameter; PADA's CC stays
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0109: (1) writes through null, (2) the mute stays, (3) PADA's CC changes and the
song's parameter doesn't.

Usage: arranger_automation_clip_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot(), Inputs and Song)

se, su, fuzz_ui = kr.se, kr.su, kr.fuzz_ui


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    a.elf, a.out = os.path.abspath(a.elf), os.path.abspath(a.out)
    os.makedirs(a.out, exist_ok=True)
    rig, inp = kr.boot(a, "boot")
    emu = rig.emu
    rig.ui_names[emu.sym["automationView"]] = "automationView"
    rig.ui_names[emu.sym["arrangerView"]] = "arrangerView"
    (param_off, short_off, clip_param_off, muted_off, rows_addr, on_arranger) = su.gdb_ints(emu, [
        "list Song::Song",  # (a context in which gdb finds the types)
        "print (int)&((Song*)0)->lastSelectedParamID", "print (int)&((Song*)0)->lastSelectedParamShortcutX",
        "print (int)&((Clip*)0)->lastSelectedParamID", "print (int)&((Output*)0)->mutedInArrangementMode",
        "print (int)&arrangerView.outputsOnScreen", "print (int)&automationView.onArrangerView"])
    null = fuzz_ui.NullPage(emu)
    song = kr.Song(rig)

    def s32(address):
        return struct.unpack("<i", emu.uc.mem_read(address, 4))[0]

    def selected():
        return dict(param=s32(rig.song() + param_off), shortcut_x=s32(rig.song() + short_off))

    def where():
        return rig.ui_name() == "automationView" and emu.u8(on_arranger) == 1

    def press(name, seconds=0.5):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)
        null.check(name)

    def pad(x, y):
        inp.pad(x, y, 100)
        rig.tm(0.05, f"pad {x},{y}")
        inp.pad(x, y, 0)
        rig.tm(0.3, f"pad {x},{y} released")
        null.check(f"pad {x},{y}")

    def arranger_overview(from_clip):
        """To the arranger's automation view (its overview): from a Clip's view SONG (song view) first."""
        if from_clip:
            press("SONG", 1.0)
        press("SONG", 1.0)
        press("CLIP", 1.0)
        return where()

    res = {}
    try:
        # (1) no current Clip: the expression parameters' pads
        current = song.current()
        overview = arranger_overview(False)
        before = selected()
        for x, y in ((14, 7), (15, 0), (15, 7)):
            pad(x, y)
        after = selected()
        res["1"] = dict(current=hex(current), overview=overview, selected=(before, after), null_writes=null.writes,
                        ok=current == 0 and overview and not null.writes and after == before and where())
        print("1", json.dumps(res["1"]), flush=True)
        press("SONG", 1.0)  # (the arranger)
        press("SONG", 1.0)  # (song view)
        # (2) an audio Clip current: the arranger's status pad
        entered = song.enter(inp, 3)
        overview = arranger_overview(True)
        output = emu.u32(rows_addr + 7 * 4)
        muted = emu.u8(output + muted_off) if output else None
        pad(16, 7)
        toggled = emu.u8(output + muted_off) if output else None
        pad(16, 7)  # (back)
        res["2"] = dict(entered=entered, overview=overview, output=hex(output), muted=(muted, toggled),
                        ok=entered and overview and output != 0 and toggled != muted and where())
        print("2", json.dumps(res["2"]), flush=True)
        press("SONG", 1.0)
        press("SONG", 1.0)
        # (3) a MIDI Clip current: the select encoder
        clips = song.clips()
        for _ in range(16):  # PADA onto y = 6, as Song.enter() does
            if 0 - song.scroll() == 6:
                break
            inp.turn("scrollY", 1 if 0 - song.scroll() > 6 else -1)
            rig.tm(0.05, "scrollY")
        inp.pad(0, 6, 100)
        rig.tm(0.05, "PADA's pad held")
        press("MIDI", 0.3)
        inp.pad(0, 6, 0)
        rig.tm(0.5, "PADA made MIDI")
        entered = song.enter(inp, 0)
        overview = arranger_overview(True)
        cc = s32(clips[0] + clip_param_off)
        before = selected()
        inp.turn("select", 1)
        rig.tm(0.5, "select +1")
        after = selected()
        cc_after = s32(clips[0] + clip_param_off)
        res["3"] = dict(entered=entered, overview=overview, cc=(cc, cc_after), selected=(before, after),
                        ok=entered and overview and cc_after == cc and after["param"] != before["param"] and where())
        print("3", json.dumps(res["3"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "arranger_automation_clip.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2", "3") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
