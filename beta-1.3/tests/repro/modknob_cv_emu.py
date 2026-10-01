#!/usr/bin/env python3
"""The MOD buttons in a CV clip write nothing through a null pointer (patch 0103). On the v1.3 beta (62a516c2)
View::modButtonAction() stored the pressed button in *modControllable->getModKnobMode(). A CV instrument has no mod
knob modes, and its getModKnobMode() returns nullptr (ModControllable's default), so the button's number went to
address 0. These are the fuzz campaign's "null writes": one site, in nearly every seed (MOD0 writes 0, unseen).
View::potentiallyRenderVUMeter() read through the same pointer (with the VU meter on and a CV clip active; not run
here: right after boot song view has no active mod controllable, so MOD0 can't switch the VU meter on).

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track), the null page writable and checked after every input (fuzz_ui.NullPage):
  (1) song view: PADA's pad held + CV, then PADA entered: MOD0 to MOD7 write nothing to the null page
  (2) control: PADB (a synth) entered, MOD3: the synth's mod knob mode (Output::modKnobMode) becomes 3
Throughout: no crash, freeze or hang.
On 62a516c2 with 0001-0012, 0101 and 0102: (1) writes the numbers of MOD1 to MOD7 to address 0.

Usage: modknob_cv_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
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
    output_off, mode_off = su.gdb_ints(emu, ["list Song::Song",  # (a context in which gdb finds the types)
                                            "print (int)&((Clip*)0)->output", "print (int)&((Output*)0)->modKnobMode"])
    null = fuzz_ui.NullPage(emu)
    song = kr.Song(rig)

    def press(name, seconds=0.3):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)
        null.check(name)

    def hold_clip_pad(button=None, seconds=0.5):
        """In song view: the pad at y = 6 (where Song.enter() put the clip) held, with button pressed if given."""
        inp.pad(0, 6, 100)
        rig.tm(0.05, "clip pad held")
        if button:
            press(button)
        rig.tm(seconds, "clip pad held")
        null.check("clip pad held")

    res = {}
    try:
        # (1) PADA made CV, entered, MOD0-MOD7
        entered = song.enter(inp, 0)
        press("SONG", 1.0)
        hold_clip_pad("CV")
        inp.pad(0, 6, 0)
        rig.tm(0.5, "clip pad released")
        entered = song.enter(inp, 0) and entered
        before = len(null.writes)
        for b in range(8):
            press(f"MOD{b}")
        res["1"] = dict(entered=entered, ui=rig.ui_name(), null_writes=null.writes[before:],
                        ok=entered and rig.ui_name() == "instrumentClipView" and len(null.writes) == before)
        print("1", json.dumps(res["1"]), flush=True)
        press("SONG", 1.0)
        # (2) control: a synth's mode
        entered = song.enter(inp, 1)
        padb_output = emu.u32(song.clips()[1] + output_off)
        press("MOD3")
        mode = emu.u8(padb_output + mode_off)
        press("MOD0")
        res["2"] = dict(entered=entered, mode_after_mod3=mode, ok=entered and mode == 3)
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "modknob_cv.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
