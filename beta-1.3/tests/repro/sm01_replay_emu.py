#!/usr/bin/env python3
"""SM01 on the v1.3 beta (62a516c2): the random-input run that found it, replayed exactly. Seed 1 of fuzz_ui.py's
plain mode (as on 1 October 2026, when the v1.3 encoder turns didn't reach the firmware: replayed the same way here),
908 inputs. In it, recording in the keyboard view put D# into a CV clip, a long BACK (undo) set the scale back without
D#, and SHIFT+SCALE at input 908 froze in ScaleMapper::computeChangeFrom() with SM01.

After every input from 835 on, the rule ScaleMapper relies on is checked: the notes in scale-mode clips lie in the
song's scale (Song::notesInScaleModeClips() against key.modeNotes).

Usage: sm01_replay_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR      (about 35 minutes)
Prints PASS (all 908 inputs, the rule held) or FAIL as its last line; exit status 0 or 1."""
import argparse
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..")))  # beta-1.3/tests
import fuzz_ui as fz  # noqa: E402
import stress_ui_emu as su  # noqa: E402

STEPS, CHECK_FROM = 908, 835


class OldInputs(fz.Inputs):
    """The inputs as the original run made them: encoder turns written but the v1.3 encoder task not woken."""

    def __init__(self, rig, firmware):
        super().__init__(rig, firmware)
        self.unblock = None


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
    inp = OldInputs(rig, "v13")
    null = fz.NullPage(emu)
    fz.ModalRecorder(emu)
    emu.uc.ctl_flush_tb()
    # MusicalKey's NoteSet, 12 bits (gdb finds Song only in the context of its constructor)
    key_offset, = su.gdb_ints(emu, ["list Song::Song", "print (int)&((Song*)0)->key.modeNotes"])
    sym = emu.sym
    rng = random.Random(1)
    broken = []
    n = 0
    try:
        while n < STEPS:
            d = fz.step(rng, inp, rig, print)
            null.check(d)
            n += 1
            if n >= CHECK_FROM:
                song = rig.song()
                present = emu.call(sym["_ZN4Song21notesInScaleModeClipsEv"], song) & 0xFFF
                mode_notes = emu.u32(song + key_offset) & 0xFFF
                if present & ~mode_notes & 0xFFE:  # the root (bit 0) is counted as present anyway
                    broken.append((n, d, f"{mode_notes:012b}", f"{present:012b}"))
            if n % 100 == 0:
                print(f"{n} inputs", flush=True)
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        print(f"stopped at input {n + 1}: {p.get('kind')} {p.get('detail')} during {p.get('what')}")
        print("FAIL")
        sys.exit(1)
    if broken:
        print("the scale lost a note still in a clip at:", broken[:5])
        print("FAIL")
        sys.exit(1)
    print(f"all {STEPS} inputs, no freeze")
    print("PASS")


if __name__ == "__main__":
    main()
