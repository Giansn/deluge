#!/usr/bin/env python3
"""PETTRA BALL: the Pettra arp as the arp mode Ball (ratchet notes = Ball, firmware v18.4), a synth preset saved by
the firmware itself (emulated), and demos rendered with it: A5 held, 8 bars at 138 BPM.

Ball: the plates close in and part again within the ratchet. The first step is a roll into the meeting point (each
gap the one before times r, r from the ratchet bounce), where the ball buzzes at 15 ms; all the other steps a roll
backwards with the same ratio, so that the ball runs out slower and slower for as long as the bounce length leaves
(patch 0120; up to v19.0 the plates met in the middle). Into the meeting it plays the song's figure into the
beat (102, 67, 52 ms, references/pettra-arp/ANALYSIS.md); over both excerpts the gaps between 25 and 180 ms sit on a
ladder of x0.67 (METHODS.md). The shortest gaps can't be measured under the song's bass, so the buzz is set by ear.
Even velocity: the single hits at the start and the end as clear as the buzz.

Usage: ball.py <deluge.elf> <out dir> [variant ...]   variants: two (+7 over a bar, PETTRA BALL), beat (+3 over a
beat, PETTRA BALL BUZZ: nearly all buzz)
(BLOCKCOUNT_DIR: where blockcount.so is, see run.sh)"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_presets  # noqa: E402
import pettra  # noqa: E402

BALL = 254  # kRatchetNotesBall
VARIANTS = {
    # One beat, +3 (x0.85): from 33 ms into the buzz over the first step, out over the second to 33 ms
    "beat": dict(ratchetBounce=3, ratchetBounceLength=2),
    # One bar, +7 (x0.65): 76, 49, 32, 21 ms into the buzz (15 ms) over the first step, then out over the other
    # seven: 15, 17, 26, 40, 62, 95, 146, 225, 346, 533 ms, each hit slower than the one before (patch 0120)
    "two": dict(ratchetBounce=7, ratchetBounceLength=8),
}
NAMES = {"beat": "PETTRA BALL BUZZ", "two": "PETTRA BALL"}


def arp(variant):
    a = dict(pettra.ARP)
    a.update(noteMode="up", numOctaves=1, ratchetNotes=BALL, ratchetBounceFade=0)   # Even: the single hits as clear
    a.update(VARIANTS[variant])
    return a


def main():
    elf, out_dir = sys.argv[1], sys.argv[2]
    variants = sys.argv[3:] or list(VARIANTS)
    os.makedirs(out_dir, exist_ok=True)
    pettra.A_MAJOR = [81]                      # A5 held: the ball repeats one note, as the song's plucks mostly do
    pettra.PARAMS["ratchetProbability"] = gen_presets.k(50)   # every ratchet a ball
    # A snappier pluck, so that the buzz's hits (15 ms apart) stay single hits instead of a held tone
    pettra.PARAMS["arpeggiatorGate"] = gen_presets.k(20)
    pettra.ENV1.update(decay=gen_presets.k(14), sustain=gen_presets.k(0), release=gen_presets.k(4))
    # Every hit somewhere else in the stereo field: random (new at each note-on) on pan at full depth. In the song a
    # third of the plucks with a bright attack lie hard left or right (12 dB or more), the rest between, left and
    # right equally often and in no fixed order (references/pettra-arp/METHODS.md, method 5)
    pettra.CABLES = pettra.CABLES + [("random", "pan", 50)]
    for v in variants:
        pettra.NAME, pettra.ARP = NAMES[v], arp(v)
        pettra.use_voice()
        print(gen_presets.save_preset(elf, pettra.NAME, pettra.ARP, out_dir))
        pettra.render(elf, out_dir)


if __name__ == "__main__":
    main()
