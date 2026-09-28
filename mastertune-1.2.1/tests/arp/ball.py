#!/usr/bin/env python3
"""PETTRA BALL: the Pettra arp as the arp mode Ball (ratchet notes = Ball, firmware v18.4), a synth preset saved by
the firmware itself (emulated), and demos rendered with it: A5 held, 8 bars at 138 BPM.

Ball: the plates close in and part again within the ratchet. The first half is a roll into its middle (each gap the
one before times r, r from the ratchet bounce), where the ball buzzes at 15 ms; the second half a roll backwards with
its own, faster ratio, so that the ball ends slower than it started. Over both excerpts of the song the gaps between
25 and 180 ms sit on a ladder of x0.67 (references/pettra-arp/METHODS.md); the shortest gaps can't be measured under
the song's bass, so the buzz is set by ear. Even velocity: the single hits at the start and the end as clear as the
buzz.

Usage: ball.py <deluge.elf> <out dir> [variant ...]   variants: two (+7 over 2 beats, PETTRA BALL), beat (+3 over a
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
    # One beat, +3 (x0.85): from 33 ms into the buzz on the offbeat, out with x0.775 to 49 ms
    "beat": dict(ratchetBounce=3, ratchetBounceLength=2),
    # Two beats, +7 (x0.65): 152, 99, 64, 42, 27, 18 ms into the buzz (15 ms) on beats 2 and 4, then out with the
    # parting's own x0.475: 22, 24, 52, 108, 228 ms
    "two": dict(ratchetBounce=7, ratchetBounceLength=4),
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
    for v in variants:
        pettra.NAME, pettra.ARP = NAMES[v], arp(v)
        pettra.use_voice()
        print(gen_presets.save_preset(elf, pettra.NAME, pettra.ARP, out_dir))
        pettra.render(elf, out_dir)


if __name__ == "__main__":
    main()
