#!/usr/bin/env python3
"""Pegel von «New Sitar Grii 10» im Emulator bei anderen Reglerstellungen (Baseline Master, 27.09.2026).

Baut aus geraet/karte vier Karten und misst jede mit sitar_emu.py (--all-kits, 440 Hz, 4 Takte):
  ref     der Song wie gespeichert
  kits35  alle Kits (kitParams volume) auf 35.4, den Standard (0x3504F334)
  kits40  alle Kits auf 40 (0x4CCCCCA8)
  rows50  alle Synths und Kit-Reihen auf 40 (soundParams volume 0x4CCCCCA8) auf 50 (0x7FFFFFFF)
Die Samples der Karte werden verlinkt, nicht kopiert. Ergebnis: <out>/baseline-pegel.json mit Spitze und RMS in dBFS
(vor dem Clip am Ausgang gemessen) und der Zahl der Samples über Vollaussteuerung.

Usage: baseline_pegel.py <deluge.elf> <karte> <out> --song-dir <tests/song> --build <dir mit blockcount.so>
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
    ap.add_argument("karte")
    ap.add_argument("out")
    ap.add_argument("--song-dir", required=True)
    ap.add_argument("--build", required=True)
    args = ap.parse_args()
    song = open(os.path.join(args.karte, "SONGS", "New Sitar Grii 10.XML"), encoding="utf-8").read()
    results = {}
    for name, change in VARIANTS.items():
        card = os.path.join(args.out, "karte-" + name)
        os.makedirs(os.path.join(card, "SONGS"), exist_ok=True)
        if not os.path.exists(os.path.join(card, "SAMPLES")):
            os.symlink(os.path.abspath(os.path.join(args.karte, "SAMPLES")), os.path.join(card, "SAMPLES"))
        with open(os.path.join(card, "SONGS", "New Sitar Grii 10.XML"), "w", encoding="utf-8") as f:
            f.write(change(song))
        run = os.path.join(args.out, name)
        subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sitar_emu.py"),
                        args.elf, card, run, "--song-dir", args.song_dir, "--build", args.build, "--tenths", "4400",
                        "--all-kits"], check=True, stdout=subprocess.DEVNULL)
        with open(os.path.join(run, "result.json")) as f:
            results[name] = json.load(f)["output"]
        print(name, results[name])
    with open(os.path.join(args.out, "baseline-pegel.json"), "w") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
