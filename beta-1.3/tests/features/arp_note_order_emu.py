#!/usr/bin/env python3
"""The arp's fixed note orders (features/0008: Converge, Diverge, Up & Down excl., Thumb, Pinky) in the v1.3 build, with
the v1.3 rig (rig13.py). For each, a song with one synth holding C E G B (60 64 67 71) for the whole bar, the arp on it
in 16ths over 2 octaves (octave mode Up), the note mode as the song file names it ("converge", "diverge",
"upDownExcl", "thumb", "pinky": so loading it is checked too). Every note-on after the arp (Sound::noteOnPostArpeggiator)
is logged:

  (1) the first 16 notes: the order's cycle through the four notes, then the same an octave up, then again from the
      bottom octave (for 4 notes: Converge C B E G, Diverge G E B C, Up & Down excl. C E G B G E, Thumb C E C G C B,
      Pinky C B E B G B)
  (2) no crash, freeze, hang, error popup or access outside RAM and the peripherals
On the build without them (s7) the song's note mode reads as Up: (1) fails.

Usage: arp_note_order_emu.py <deluge.elf> [mode ...] --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (with the failed checks); exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))  # beta-1.3/tests
import rig13  # noqa: E402,F401
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402

sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "mastertune-1.2.1", "tests", "arp")))
import make_sd  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R3  # noqa: E402

K = make_sd.knob
CHORD = [60, 64, 67, 71]
CYCLES = {  # indices into CHORD (0 the lowest), one octave
    "converge": [0, 3, 1, 2],
    "diverge": [2, 1, 3, 0],
    "upDownExcl": [0, 1, 2, 3, 2, 1],
    "thumb": [0, 1, 0, 2, 0, 3],
    "pinky": [0, 3, 1, 3, 2, 3],
}
TICKS_PER_BAR = 384


def expected(mode, count=16):
    out, octave = [], 0
    while len(out) < count:
        out += [CHORD[i] + 12 * octave for i in CYCLES[mode]]
        octave = (octave + 1) % 2
    return out[:count]


def build_card(path, mode):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda *args, **kwargs: ""
    env = dict(attack=K(0), decay=K(10), sustain=K(50), release=K(0))
    p = dict(ratchetProbability=K(0), arpeggiatorGate=K(25), reverbAmount=K(0), delayFeedback=K(0))
    a = dict(arpMode="arp", noteMode=mode, octaveMode="up", numOctaves=2, syncLevel=6)
    rows = [("y", note, [(0, TICKS_PER_BAR, 90)], None) for note in CHORD]
    make_sd.chord_rows = lambda octave, velocity=90: rows
    synth = make_sd.synth("PING", make_sd.SAW, make_sd.SQUARE, 1, p, env, make_sd.PAD_ENV2,
                          [("velocity", "volume", 25)], notes_octave=0, arp=a)
    make_sd.synths = lambda: [synth]
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 1).encode()
    make_sd.fat32.build(path, files)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("modes", nargs="*", default=list(CYCLES))
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    fails, report = [], {}
    for mode in a.modes:
        image = os.path.join(a.out, f"{mode}.img")
        build_card(image, mode)
        rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
        emu, sym = rig.emu, rig.emu.sym
        notes = []
        emu.intercept(sym.find("_ZN5Sound21noteOnPostArpeggiator"), lambda e: notes.append(e.uc.reg_read(UC_ARM_REG_R3)))
        emu.uc.ctl_flush_tb()
        inp = fuzz_ui.Inputs(rig, "v13")
        try:
            inp.button("PLAY", True)
            rig.tm(0.02, "PLAY")
            inp.button("PLAY", False)
            rig.tm(2.1, f"{mode}: a bar")
        except su.Stop:
            pass
        want = expected(mode)
        got = notes[:16]
        report[mode] = dict(played=got, expected=want, problems=list(rig.problems))
        if got != want:
            fails.append(f"{mode}: (1) played {got}, expected {want}")
        if rig.problems or rig.error_popups() or rig.invalid:
            fails.append(f"{mode}: (2) {rig.problems[:3]} {rig.error_popups()[:3]} {list(rig.invalid)[:3]}")
        print(mode, json.dumps(report[mode]), flush=True)
    json.dump(report, open(os.path.join(a.out, "arp_note_order.json"), "w"), indent=1)
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
