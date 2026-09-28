#!/usr/bin/env python3
"""Levels of "New Sitar Grii 10" in the emulator at other control settings (baseline master, 27.09.2026).

Builds four cards from device/card and measures each with sitar_emu.py (--all-kits, 440 Hz, 4 bars):
  ref     the song as saved
  kits35  all kits (kitParams volume) at 35.4, the default (0x3504F334)
  kits40  all kits at 40 (0x4CCCCCA8)
  rows50  all synths and kit rows at 40 (soundParams volume 0x4CCCCCA8) to 50 (0x7FFFFFFF)
The card's samples are linked, not copied. Result: <out>/baseline-levels.json with peak and RMS in dBFS
(measured at the output before the clip) and the number of samples above full scale.

Usage: baseline_levels.py <deluge.elf> <card> <out> --song-dir <tests/song> --build <dir with blockcount.so>
"""
import argparse
import json
import os
import re
import subprocess
import sys

VARIANTS = {
    "ref": lambda x: x,
    "kits35": lambda x: re.sub(r'(<kitParams\b[^>]*?\bvolume=")0x[0-9A-Fa-f]{8}"', r'\g<1>0x3504F334"', x),
    "kits40": lambda x: re.sub(r'(<kitParams\b[^>]*?\bvolume=")0x[0-9A-Fa-f]{8}"', r'\g<1>0x4CCCCCA8"', x),
    "rows50": lambda x: re.sub(r'(<soundParams\b[^>]*?\bvolume=")0x4CCCCCA8"', r'\g<1>0x7FFFFFFF"', x),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("card")
    ap.add_argument("out")
    ap.add_argument("--song-dir", required=True)
    ap.add_argument("--build", required=True)
    args = ap.parse_args()
    song = open(os.path.join(args.card, "SONGS", "New Sitar Grii 10.XML"), encoding="utf-8").read()
    results = {}
    for name, change in VARIANTS.items():
        card = os.path.join(args.out, "card-" + name)
        os.makedirs(os.path.join(card, "SONGS"), exist_ok=True)
        if not os.path.exists(os.path.join(card, "SAMPLES")):
            os.symlink(os.path.abspath(os.path.join(args.card, "SAMPLES")), os.path.join(card, "SAMPLES"))
        with open(os.path.join(card, "SONGS", "New Sitar Grii 10.XML"), "w", encoding="utf-8") as f:
            f.write(change(song))
        run = os.path.join(args.out, name)
        subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sitar_emu.py"),
                        args.elf, card, run, "--song-dir", args.song_dir, "--build", args.build, "--tenths", "4400",
                        "--all-kits"], check=True, stdout=subprocess.DEVNULL)
        with open(os.path.join(run, "result.json")) as f:
            results[name] = json.load(f)["output"]
        print(name, results[name])
    with open(os.path.join(args.out, "baseline-levels.json"), "w") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
