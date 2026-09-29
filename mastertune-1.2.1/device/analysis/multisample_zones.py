#!/usr/bin/env python3
"""The zones of a song's or preset's multisamples, against the notes their files are named after, and how far the
song's notes are played from their zone's root.

For each sound with 2 or more sample ranges: per zone the notes it covers, the root the XML sets (60 - transpose, as
the Deluge plays a zone's sample at its own pitch there), the note in the file's name ("..._A#2_...", counted with
C3 = 60 as the libraries on the card do) and the difference. A difference far from 0 means the Deluge detected the root
wrongly at import (the files carry no smpl or inst chunk). For a song also the notes each such sound plays: the zone
each one falls in and how many semitones up or down from its root. Far down puts the attack's noise into the low
register (device/analysis/2026-09-29-multisample-undertone.md).

Usage: multisample_zones.py <song or preset .XML>...
"""
import re
import sys

NAMES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def named_note(file_name):
    """The note after the file name's underscore (C3 = 60), or None."""
    m = re.search(r"_([A-G]#?)(-?\d)_", file_name.rsplit("/", 1)[-1])
    return 12 * (int(m.group(2)) + 2) + NAMES[m.group(1)] if m else None


def zones(sound):
    """[(lowest note, highest note, root as set, the file's note, file name)] of a sound's sample ranges."""
    out, low = [], 0
    for r in re.findall(r"<sampleRange(.*?)</sampleRange>", sound, re.S):
        top = re.search(r'rangeTopNote="(-?\d+)"', r)
        name = re.search(r'fileName="([^"]*)"', r)
        transpose = re.search(r'transpose="(-?\d+)"', r)
        high = int(top.group(1)) if top else 127
        file_name = name.group(1) if name else "?"
        out.append((low, high, 60 - (int(transpose.group(1)) if transpose else 0), named_note(file_name), file_name))
        low = high + 1
    return out


def played(xml):
    """{preset name: sorted notes with note data} of a song's instrument clips."""
    notes = {}
    for m in re.finditer(r"<instrumentClip\b(.*?)</instrumentClip>", xml, re.S):
        body = m.group(1)
        name = re.search(r'instrumentPresetName="([^"]*)"', body)
        for r in re.finditer(r"<noteRow\b([^>]*?)/?>", body, re.S):
            y = re.search(r'\by="(-?\d+)"', r.group(1))
            if name and y and re.search(r'noteData(WithLift)?="0x[0-9A-Fa-f]{2,}', r.group(1)):
                notes.setdefault(name.group(1), set()).add(int(y.group(1)))
    return {k: sorted(v) for k, v in notes.items()}


def main():
    for path in sys.argv[1:]:
        xml = open(path, encoding="utf-8", errors="replace").read()
        notes = played(xml)
        print(path)
        for m in re.finditer(r"<(sound|instrument)\b(.*?)</\1>", xml, re.S):
            sound = m.group(2)
            z = zones(sound)
            if len(z) < 2:
                continue
            name = re.search(r'\bname="([^"]*)"', sound) or re.search(r'presetName="([^"]*)"', sound)
            name = name.group(1) if name else "?"
            print(f"  {name}: {len(z)} zones")
            for low, high, root, real, file_name in z:
                off = "" if real is None else f"{root - real:+d}" + ("  WRONG ROOT" if abs(root - real) > 1 else "")
                print(f"    notes {low:3d}-{high:3d}  root {root:3d}  file {str(real):>4}  {off:<16} "
                      f"{file_name.rsplit('/', 1)[-1]}")
            for preset, ns in notes.items():
                if preset != name:
                    continue
                far = []
                for n in ns:
                    zone = next(x for x in z if x[0] <= n <= x[1])  # Its sample, played n - root semitones up
                    far.append(f"{n} ({n - zone[2]:+d} from {zone[4].rsplit('/', 1)[-1]})")
                print(f"    played: {', '.join(far)}")


if __name__ == "__main__":
    main()
