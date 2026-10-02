#!/usr/bin/env python3
"""E170 at a song swap (patch 0112): the random-input run that found it, replayed exactly. Seed 152 of fuzz_ui.py's
--mode deep (OLED), as stop run 4 ran it on 2 October 2026: the song browser loads SONG001 while playing at input 955
(SELECT_ENC). Before the swap Song::deleteSoundsWhichWontSound() thins out the old song, which keeps a synth (PETTRA
ARP) with no Clip and no ParamManager left; Song::stopAllAuditioning() then called MelodicInstrument::
stopAnyAuditioning() for it, which asked for its ParamManager although nothing was auditioned, and
Output::getParamManager() froze with E170.

The replay uses the fuzzer's own harness (NullPage, ModalRecorder, QuickLoadTap, GridTrackCreation) and runs 980
inputs: the swap and 25 inputs in the new song.

Usage: seed152_replay_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR      (about 20 minutes)
Prints PASS (all inputs, no problem) or FAIL as its last line; exit status 0 or 1."""
import argparse
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..")))  # beta-1.3/tests
import fuzz_ui as fz  # noqa: E402
import stress_ui_emu as su  # noqa: E402

SEED, STEPS = 152, 980


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    fz.make_card(image, 2, os.path.join(a.out, "card-template.img"))
    rig = fz.boot("v13", a.elf, a.tools, a.build, image, True)
    emu = rig.emu
    inp = fz.Inputs(rig, "v13")
    null = fz.NullPage(emu)
    fz.ModalRecorder(emu)
    fz.QuickLoadTap(emu)
    fz.GridTrackCreation(emu, inp)
    emu.uc.ctl_flush_tb()
    rng = random.Random(SEED)
    n = 0
    try:
        while n < STEPS:
            d = fz.step_deep(rng, inp, rig, print)
            null.check(d)
            n += 1
            if n % 100 == 0:
                print(f"{n} inputs, ui {rig.ui_name()}", flush=True)
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        print(f"stopped at input {n + 1}: {p.get('kind')} {p.get('detail')} during {p.get('what')}")
        print("FAIL")
        sys.exit(1)
    if null.writes:
        print("writes through a null pointer:", null.writes[:5])
        print("FAIL")
        sys.exit(1)
    print(f"all {STEPS} inputs, no problem")
    print("PASS")


if __name__ == "__main__":
    main()
