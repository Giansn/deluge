#!/usr/bin/env python3
"""Undo and redo of an edit in the arranger's automation view stay with the arranger (patch 0113). On the v1.3 beta
(62a516c2) an Action records the view it was made in (getCurrentUI()). The arranger's automation view is
automationView, a ClipMinder, so ActionLogger::revert() took its Actions for a Clip view's: from the arranger
(ARRANGEMENT_TO_CLIP_MINDER) or from song view (SESSION_TO_CLIP_MINDER) undo or redo went into a Clip's view, and
with no current Clip (after a song load) the instrument clip view rendered without one: E369 in
ModelStackWithNoteRow::getLoopLength() (the fuzzer's seed 176, --mode deep, input 354: BACK in arranger view).
0106 covered the same mistake for undo inside the arranger's automation view.

One boot of the real firmware in the emulator (OLED), the startup song loaded (no current Clip):
  (1) SONG (the arranger), CLIP (its automation view), pad 8,7 (LPF frequency), mod1 -2 (its value: an Action), CLIP
      (back to the arranger), BACK (undo): the arranger's automation view or the arranger, not a Clip's view
  (2) then to song view, SHIFT+BACK (redo): the arranger (or its automation view), not a Clip's view
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0112: (1) freezes with E369.

Usage: arranger_automation_redo_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot(), Inputs and Song)

se, su = kr.se, kr.su
ARRANGER = ("arrangerView", "automationView")


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
    on_arranger, = su.gdb_ints(emu, ["print (int)&automationView.onArrangerView"])
    song = kr.Song(rig)

    def press(name, seconds=0.6):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)

    def where():
        ui = rig.ui_name()
        return dict(ui=ui, arranger_mode=emu.u8(on_arranger) if ui == "automationView" else None)

    def in_arranger(w):
        return w["ui"] == "arrangerView" or (w["ui"] == "automationView" and w["arranger_mode"] == 1)

    res = {}
    try:
        current = song.current()
        press("SONG", 1.0)
        press("CLIP", 1.0)
        inp.pad(8, 7, 100)
        rig.tm(0.1, "pad 8,7")
        inp.pad(8, 7, 0)
        rig.tm(0.4, "LPF frequency selected")
        inp.turn("mod1", -2)  # its value changed: an Action
        rig.tm(0.5, "mod1 -2")
        editor = where()
        press("CLIP", 1.0)
        back = where()
        press("BACK", 1.0)  # undo the node
        undone = where()
        res["1"] = dict(current=hex(current), editor=editor, back=back, undone=undone,
                        ok=current == 0 and editor == dict(ui="automationView", arranger_mode=1)
                        and back["ui"] == "arrangerView" and in_arranger(undone))
        print("1", json.dumps(res["1"]), flush=True)
        if undone["ui"] == "automationView":
            press("SONG", 1.0)  # (to the arranger)
        press("SONG", 1.0)  # (song view)
        session = where()
        inp.button("SHIFT", True)
        press("BACK", 1.0)  # redo
        inp.button("SHIFT", False)
        rig.tm(0.5, "SHIFT released")
        redone = where()
        res["2"] = dict(session=session, redone=redone, ok=session["ui"] == "sessionView" and in_arranger(redone))
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "arranger_automation_redo.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
