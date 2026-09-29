#!/usr/bin/env python3
"""The zones of a song's or preset's multisamples, against the notes their files are named after, and how far the
song's notes are played from their zone's root; with --fix, the zones whose root is wrong corrected.

For each sound with 2 or more sample ranges: per zone the notes it covers, the root the XML sets (60 - transpose -
cents / 100: the note at which the Deluge plays the zone's sample at its own pitch), the note in the file's name
("..._A#2_...", counted with C3 = 60 as the libraries on the card do) and the difference. A difference far from 0 means
the Deluge detected the root wrongly at import (the files carry no smpl or inst chunk). For a song also the notes each
such sound plays: the zone each one falls in and how many semitones up or down from its root. Far down puts the
attack's noise into the low register (device/analysis/2026-09-29-multisample-undertone.md).

--fix OUT --card DIR: each zone's file is measured (the note in its name, the cents by ear as DelugeTuner hears them,
tools/retune_library.py). Where a zone plays its file more than 10 cents off, it gets the measured root; then the zones
are sorted by root and each note goes to the nearest (the range tops at the midpoints). In a song every note of these
sounds is moved to the note that plays it at the pitch it sounded at before (to the nearest semitone), so that the
music stays as it was, only in tune and from the nearest sample. Writes the result to OUT. Needs numpy and soxr.

Usage: multisample_zones.py <song or preset .XML>...
       multisample_zones.py <song or preset .XML> --fix OUT --card <the card's folder>
"""
import argparse
import math
import os
import re
import sys

NAMES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
TOLERANCE = 0.10  # Semitones a zone may play its file off before --fix corrects it


def named_note(file_name):
    """The note after the file name's underscore (C3 = 60), or None."""
    m = re.search(r"_([A-G]#?)(-?\d)_", file_name.rsplit("/", 1)[-1])
    return 12 * (int(m.group(2)) + 2) + NAMES[m.group(1)] if m else None


class Zone:
    """A <sampleRange>: its attributes and the rest of it (the <zone> element) as written."""

    def __init__(self, text):
        self.text = text
        top = re.search(r'rangeTopNote="(-?\d+)"', text)
        self.top = int(top.group(1)) if top else None
        self.file = re.search(r'fileName="([^"]*)"', text).group(1)
        transpose = re.search(r'transpose="(-?\d+)"', text)
        cents = re.search(r'cents="(-?\d+)"', text)
        self.transpose = int(transpose.group(1)) if transpose else 0
        self.cents = int(cents.group(1)) if cents else 0
        self.inner = text[text.index(">") + 1:]  # After the opening tag: the <zone> and the indent before </...>

    @property
    def root(self):
        return 60 - self.transpose - self.cents / 100

    def written(self, indent, top):
        """The <sampleRange> as the Deluge writes it, with this top note (None: the last range)."""
        attrs = ([f'rangeTopNote="{top}"'] if top is not None else []) + [f'fileName="{self.file}"']
        attrs += [f'transpose="{self.transpose}"'] if self.transpose else []
        attrs += [f'cents="{self.cents}"'] if self.cents else []
        return "<sampleRange\n" + "\n".join(indent + "\t" + a for a in attrs) + ">" + self.inner


def sounds(xml):
    """[(name, start, end)] of the sounds (song) or the preset's sound that have 2 or more sample ranges."""
    out = []
    for m in re.finditer(r"<(sound|instrument)\b(.*?)</\1>", xml, re.S):
        body = m.group(2)
        if len(re.findall(r"<sampleRange\b", body)) < 2:
            continue
        name = re.search(r'\b(?:name|presetName)="([^"]*)"', body)
        out.append((name.group(1) if name else "?", m.start(2), m.end(2)))
    return out


def zone_lists(body):
    """[(start, end, [Zone])] of the <sampleRanges> blocks in a sound (osc1, osc2)."""
    out = []
    for m in re.finditer(r"<sampleRanges>(.*?)</sampleRanges>", body, re.S):
        texts = re.findall(r"<sampleRange\b(.*?</sampleRange>)", m.group(1), re.S)
        out.append((m.start(1), m.end(1), [Zone(t) for t in texts]))
    return out


def ranges(zones):
    """[(lowest note, highest note, Zone)]."""
    out, low = [], 0
    for z in zones:
        high = z.top if z.top is not None else 127
        out.append((low, high, z))
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


def show(path):
    xml = open(path, encoding="utf-8", errors="replace").read()
    notes = played(xml)
    print(path)
    for name, start, end in sounds(xml):
        for _, _, zones in zone_lists(xml[start:end]):
            print(f"  {name}: {len(zones)} zones")
            for low, high, z in ranges(zones):
                real = named_note(z.file)
                off = "" if real is None else f"{z.root - real:+.2f}" + ("  WRONG ROOT" if abs(z.root - real) > 1 else "")
                print(f"    notes {low:3d}-{high:3d}  root {z.root:6.2f}  file {str(real):>4}  {off:<18} "
                      f"{z.file.rsplit('/', 1)[-1]}")
            far = []
            for n in notes.get(name, []):
                low, high, z = next(r for r in ranges(zones) if r[0] <= n <= r[1])
                far.append(f"{n} ({n - z.root:+.1f} from {z.file.rsplit('/', 1)[-1]})")
            if far:
                print(f"    played: {', '.join(far)}")


def measure(card, zones):
    """{file: its pitch as a note with cents, from the note in its name and the cents heard}, where both are known."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
    import retune_library as rl  # numpy, soxr

    out = {}
    for z in zones:
        note, path = named_note(z.file), os.path.join(card, z.file)
        if z.file in out or note is None or not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            cents = rl.hear(fh, rl.read_audio_info(fh))
        if cents is not None:
            out[z.file] = note + cents / 100
    return out


def fix(path, out_path, card):
    xml = open(path, encoding="utf-8").read()
    pieces, last, moves = [], 0, {}
    for name, start, end in sounds(xml):
        body = xml[start:end]
        for s, e, zones in zone_lists(body):
            pitch = measure(card, zones)
            wrong = [z for z in zones if z.file in pitch and abs(z.root - pitch[z.file]) > TOLERANCE]
            if not wrong:
                continue
            # Where each note sounded before: the file's pitch plus the note's distance from the zone's root as set
            before = [(low, high, pitch.get(z.file, z.root), z.root) for low, high, z in ranges(zones)]
            for z in wrong:
                note = named_note(z.file)
                print(f"  {name}: {z.file.rsplit('/', 1)[-1]} root {z.root:.2f} -> {pitch[z.file]:.2f}")
                z.transpose, z.cents = 60 - note, -round(100 * (pitch[z.file] - note))
            kept, seen = [], set()
            for z in sorted(zones, key=lambda z: z.root):
                if z.file not in seen:  # The same file twice: once
                    seen.add(z.file)
                    kept.append(z)
            tops = [math.floor((a.root + b.root) / 2) for a, b in zip(kept, kept[1:])]
            for i in range(1, len(tops)):
                tops[i] = max(tops[i], tops[i - 1] + 1)
            indent = re.search(r"\n(\t*)<sampleRange\b", body[s:e]).group(1)
            block = "\n" + indent + ("\n" + indent).join(
                z.written(indent, tops[i] if i < len(tops) else None) for i, z in enumerate(kept))
            block += body[s:e][body[s:e].rindex("</sampleRange>") + len("</sampleRange>"):]
            pieces += [xml[last:start + s], block]
            last = start + e
            # And so which note plays that pitch now, to the nearest semitone
            moves[name] = lambda n, before=before: round(
                next(real + n - root for low, high, real, root in before if low <= n <= high))
    xml = "".join(pieces) + xml[last:]

    # The songs' notes of these sounds, each to the note that plays the pitch it sounded at
    def clip(m):
        body = m.group(0)
        name = re.search(r'instrumentPresetName="([^"]*)"', body)
        if not name or name.group(1) not in moves:
            return body
        move = moves[name.group(1)]
        rows = [(r, int(re.search(r'\by="(-?\d+)"', r.group(1)).group(1)))
                for r in re.finditer(r"<noteRow\b([^>]*?)/?>", body, re.S) if re.search(r'\by="', r.group(1))]
        new = [move(y) for _, y in rows]
        if any(b <= a for a, b in zip(new, new[1:])):
            print(f"  {name.group(1)}: the rows would collide or change order ({[y for _, y in rows]} -> {new}): "
                  "not moved")
            return body
        print(f"  {name.group(1)}: rows {[y for _, y in rows]} -> {new}")
        out, last = [], 0
        for (r, y), y2 in zip(rows, new):
            out += [body[last:r.start()], re.sub(r'\by="-?\d+"', f'y="{y2}"', r.group(0), count=1)]
            last = r.end()
        body = "".join(out) + body[last:]
        if rows:  # The view on the same notes
            shift = new[0] - rows[0][1]
            body = re.sub(r'\byScroll="(-?\d+)"', lambda s: f'yScroll="{int(s.group(1)) + shift}"', body, count=1)
        return body

    xml = re.sub(r"<instrumentClip\b.*?</instrumentClip>", clip, xml, flags=re.S)
    open(out_path, "w", encoding="utf-8", newline="").write(xml)
    print(f"written: {out_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xml", nargs="+")
    ap.add_argument("--fix", metavar="OUT", help="write the XML with the wrong zones corrected to OUT")
    ap.add_argument("--card", help="the card's folder the XML's fileNames start from (for --fix)")
    a = ap.parse_args()
    if a.fix:
        if len(a.xml) != 1 or not a.card:
            ap.error("--fix takes one XML and --card")
        fix(a.xml[0], a.fix, a.card)
        show(a.fix)
        return
    for path in a.xml:
        show(path)


if __name__ == "__main__":
    main()
