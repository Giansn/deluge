#!/usr/bin/env python3
"""SAVE with an audition pad held opens the kit-row save only for a kit's sound row (patch 0101). On the v1.3 beta
(62a516c2) InstrumentClipView::buttonAction() opened SaveKitRowUI in any clip: in a synth, MIDI or CV clip the row has
no drum, and a row without notes has no NoteRow at all, yet it read noteRow->drum->type and handed the save a garbage
drum (the downcast of a null Drum*, 0xfffffad0 in this build) and the ParamManager of a melodic row, or of no row
(0x1c). Saving that froze with E411 (the fuzzer's seed 1011 in --mode deep: inputs 13 and 170), or worse.

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track):
  (1) the synth clip PADA entered, each audition pad 17,0-17,7 held + SAVE: SaveKitRowUI must not open
  (2) the kit clip entered, each audition pad held + SAVE: it opens for the kit's sound rows, every time with the row's
      SoundDrum (a pointer into RAM) and a ParamManager with collections; BACK closes it
Throughout: no crash, freeze or hang.
On 62a516c2 with 0001-0009: (1) opens eight times with the garbage drum.

Usage: savekitrow_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot(), Inputs and Song)

se, su = kr.se, kr.su
RAM = ((0x20000000, 0x20300000), (0x0C000000, 0x10000000))  # internal SRAM, SDRAM


def in_ram(p):
    return any(lo <= p < hi for lo, hi in RAM)


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
    emu, sym = rig.emu, rig.emu.sym
    rig.ui_names[sym["saveKitRowUI"]] = "saveKitRowUI"
    drum_off, pm_off, summ_off = se.gdb_values(emu, [
        "(int)&saveKitRowUI.soundDrumToSave - (int)&saveKitRowUI",
        "(int)&saveKitRowUI.paramManagerToSave - (int)&saveKitRowUI", "(int)&((ParamManager*)0)->summaries"])
    ui = sym["saveKitRowUI"]
    opened = []

    def on_opened(e):
        drum, pm = e.u32(ui + drum_off), e.u32(ui + pm_off)
        coll = e.u32(pm + summ_off) if in_ram(pm) else 0
        opened.append(dict(at=rig.action_name, drum=hex(drum), param_manager=hex(pm),
                           ok=in_ram(drum) and in_ram(pm) and coll != 0))
    emu.intercept(sym.find("_ZN12SaveKitRowUI6openedEv"), on_opened)
    emu.uc.ctl_flush_tb()

    def audition_save(y):
        """Audition pad 17,y held + SAVE; if the save opened, BACK closes it. Returns the save's record or None."""
        n = len(opened)
        inp.pad(17, y, 100)
        rig.tm(0.05, "audition pad held")
        inp.button("SAVE", True)
        rig.tm(0.05, "audition + SAVE")
        inp.button("SAVE", False)
        inp.pad(17, y, 0)
        rig.tm(0.3, "after SAVE")
        if rig.ui_name() == "saveKitRowUI":
            inp.button("BACK", True)
            inp.button("BACK", False)
            rig.tm(0.5, "BACK")
        return opened[n] if len(opened) > n else None

    res = {}
    try:
        song = kr.Song(rig)
        kit = next(i for i, c in enumerate(song.clips()) if song.is_kit(c))
        entered = song.enter(inp, 0)
        synth = [audition_save(y) for y in range(8)]
        res["1"] = dict(entered=entered, opened=[s for s in synth if s], ok=entered and not any(synth))
        print("1", json.dumps(res["1"]), flush=True)
        inp.button("SONG", True)
        inp.button("SONG", False)
        rig.tm(1.0, "back to Song view")
        entered = song.enter(inp, kit)
        kit_rows = [audition_save(y) for y in range(8)]
        got = [k for k in kit_rows if k]
        res["2"] = dict(entered=entered, opened=got, ok=entered and bool(got) and all(k["ok"] for k in got)
                        and rig.ui_name() == "instrumentClipView")
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "savekitrow.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
