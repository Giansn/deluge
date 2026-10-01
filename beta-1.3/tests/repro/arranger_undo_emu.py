#!/usr/bin/env python3
"""Undo in the arranger's automation view goes back to the arranger (patch 0106). On the v1.3 beta (62a516c2)
ActionLogger::revert() took an action from another view, undone in the automation view, as "exit automation view"
(Animation::EXIT_AUTOMATION_VIEW) and went into the current Clip's view regardless. The arranger's automation view
(CLIP in arranger view) has no Clip of its own, and after a song load there is no current Clip at all: the instrument
clip view's graphicsRoutine() then froze with E369 (no timeline counter). That is the fuzzer's seed 109 (--mode deep
--7seg, inputs 198-203: arranger, CLIP, BACK).

One boot of the real firmware in the emulator (OLED), make_card's card:
  (1) SONG: the arranger; the tempo encoder turned (an undoable tempo change, logged in the arranger); CLIP: the
      arranger's automation view; BACK (undo): the arranger again, the tempo back where it was
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0105: (1) freezes with E369.

Usage: arranger_undo_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot() and Inputs)

se, su = kr.se, kr.su


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
    (tempo_off,) = su.gdb_ints(emu, ["list Song::Song", "print (int)&((Song*)0)->timePerTimerTickBig"])

    def tempo():
        p = rig.song() + tempo_off
        return emu.u32(p) | (emu.u32(p + 4) << 32)

    def press(name, seconds=0.8):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)
        return rig.ui_name()

    res = {}
    try:
        arranger = press("SONG", 1.0)
        before = tempo()
        inp.turn("tempo", 4)
        rig.tm(0.5, "tempo turned")
        changed = tempo()
        automation = press("CLIP", 1.0)
        after_undo = press("BACK", 1.5)
        res["1"] = dict(arranger=arranger, automation=automation, after_undo=after_undo,
                        tempo_changed=changed != before, tempo_restored=tempo() == before,
                        ok=arranger == "arrangerView" and automation == "automationView" and changed != before
                        and after_undo == "arrangerView" and tempo() == before)
        print("1", json.dumps(res["1"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "arranger_undo.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1",) if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
