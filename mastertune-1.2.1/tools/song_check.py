#!/usr/bin/env python3
"""Checks the songs, synth presets and kit presets on a Deluge SD card (or a copy of it) for what the Deluge itself
doesn't report and what makes other tools refuse a file. Pure Python 3, no other packages; Windows, macOS, Linux.

Every *.XML under SONGS/, SYNTHS/ and KITS/ (with their subfolders):
- XML: well-formed (expat). The Deluge writes attribute values as they are, so a bare "&" in a name is its own style
  (a note, not an error). A file cut short, garbled or with bytes after its end is an error.
- Duplicate attributes: an element with the same attribute twice. Each audio track's <audioClip> has six of them
  (colourOffset, isArmedForRecording, isPlaying, isSoloing, length, section): AudioClip::writeDataToFile() calls
  Clip::writeDataToFile() twice (upstream #4917; community 1.2.1 and mastertune too). The Deluge reads such a file (it
  takes the later value; both are the same), strict XML readers refuse it. A warning; differing values are named.
- Samples: every fileName and filePath (oscillators, multisample ranges, kit rows, audio clips) looked up as the
  Deluge looks it up (AudioFileManager::getAudioFileFromFilename()): the path from the card's root, case-insensitive
  (FAT), names in code page 437; where it isn't there, in the folder named after the song or preset next to it
  (SONGS/<song>/): first the path after "SAMPLES/" with "/" as "_", then the file name alone. Missing is an error.
- CV instruments: a named CV instrument (<cvChannel presetName="...">) has no channel in the file: the firmware writes
  the channel only for unnamed ones (Instrument::writeDataToFile(), 1.2.1 and the v1.3 beta). It loads on channel 0
  (CV 1), and clips saved on another channel (cvChannel="N") find no CV instrument there. A warning.
- Firmware: firmwareVersion (shown) and earliestCompatibleFirmware. The Deluge refuses a file that needs a newer
  firmware than its own; --firmware sets the one to check against (default c1.2.1, the base of mastertune).

Output: one line per file (OK, NOTE, WARN or ERROR, the file, the firmware that saved it, what was found), then a
summary. -v adds the details under each file (every missing sample, the duplicate attributes per element and line).
Exit status: 1 if any file has an error, else 0.

Usage: song_check.py <card folder> [-v] [--firmware VERSION]
       song_check.py <file.XML>... --card <card folder> [-v]
"""
import argparse
import bisect
import os
import re
import sys
import xml.parsers.expat

FOLDERS = ("SONGS", "SYNTHS", "KITS")
SAMPLE_NAMES = ("fileName", "filePath")
QUOTED = r'"[^"]*"|\'[^\']*\''
START_TAG = re.compile(r"<([A-Za-z_][\w.:-]*)((?:\s+[^\s=/<>]+\s*=\s*(?:" + QUOTED + r"))*)\s*/?>")
ATTR = re.compile(r"(\s*)([^\s=/<>]+)\s*=\s*(" + QUOTED + r")")
TEXT_ELEMENT = re.compile(r"<(fileName|filePath|firmwareVersion|earliestCompatibleFirmware)>([^<]*)</\1>")
BARE_AMP = re.compile(r"&(?!(?:[A-Za-z][\w.-]*|#[0-9]+|#x[0-9A-Fa-f]+);)")
SEVERITY = ("OK", "NOTE", "WARN", "ERROR")


def parse_version(text):
    """(community?, (major, minor, patch)) as FirmwareVersion::parse() reads it, or None."""
    m = re.match(r"\s*(c?)(\d+)\.(\d+)\.(\d+)", text or "")
    return (m.group(1) == "c", tuple(int(g) for g in m.group(2, 3, 4))) if m else None


class Card:
    """The card's files and folders by their path from the root, case-insensitive as FAT is."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.paths = set()
        for folder, dirs, files in os.walk(self.root):
            rel = os.path.relpath(folder, self.root).replace(os.sep, "/")
            rel = "" if rel == "." else rel + "/"
            for name in files:
                self.paths.add((rel + name).casefold())

    def has(self, path):
        return path.strip("/").casefold() in self.paths

    def rel(self, path):
        return os.path.relpath(os.path.abspath(path), self.root).replace(os.sep, "/")


class Element:
    __slots__ = ("tag", "attrs", "start", "end")

    def __init__(self, tag, attrs, start, end):
        self.tag, self.attrs, self.start, self.end = tag, attrs, start, end

    def get(self, name):
        """The value the Deluge reads: the last one of that name, as written (no entities decoded)."""
        values = [v for n, v, _, _ in self.attrs if n == name]
        return values[-1] if values else None


def elements(text):
    out = []
    for m in START_TAG.finditer(text):
        attrs = [(a.group(2), a.group(3)[1:-1], m.start(2) + a.start(), m.start(2) + a.end())
                 for a in ATTR.finditer(m.group(2))]
        out.append(Element(m.group(1), attrs, m.start(), m.end()))
    return out


def check_file(path, card, firmware, verbose):
    """(severity, the file's line, [detail lines])."""
    rel = card.rel(path)
    try:
        text = open(path, "rb").read().decode("cp437")  # Every byte is a character, as the Deluge's FAT names are
    except OSError as e:
        return 3, f"{rel}  cannot be read: {e.strerror}", []
    lines_at = [i for i, c in enumerate(text) if c == "\n"]
    line_of = lambda pos: bisect.bisect_right(lines_at, pos) + 1  # noqa: E731
    elems = elements(text)
    severity, notes, details = 0, [], []

    def found(level, note):
        nonlocal severity
        severity = max(severity, level)
        notes.append(note)

    # Firmware
    root = elems[0] if elems else None
    saved_by = root.get("firmwareVersion") if root else None
    earliest = root.get("earliestCompatibleFirmware") if root else None
    for name, value in TEXT_ELEMENT.findall(text):  # Very old files write them as elements
        saved_by = value if name == "firmwareVersion" and saved_by is None else saved_by
        earliest = value if name == "earliestCompatibleFirmware" and earliest is None else earliest
    needs, own = parse_version(earliest), parse_version(firmware)
    if needs and own and needs > own:
        found(2, f"needs firmware {earliest} (a {firmware} Deluge refuses it)")

    # Duplicate attributes, then the XML as strict readers see it: the duplicates' earlier values and bare "&" taken out
    # (line numbers kept)
    dupes, cuts = {}, []
    for e in elems:
        names = [n for n, _, _, _ in e.attrs]
        twice = sorted({n for n in names if names.count(n) > 1})
        if not twice:
            continue
        differ = [n for n in twice if len({v for m, v, _, _ in e.attrs if m == n}) > 1]
        dupes.setdefault(e.tag, []).append((line_of(e.start), twice, differ))
        last = {n: i for i, (n, _, _, _) in enumerate(e.attrs)}
        cuts += [(s, t) for i, (n, _, s, t) in enumerate(e.attrs) if n in twice and last[n] != i]
    clean, pos = [], 0
    for s, t in sorted(cuts):
        clean += [text[pos:s], "\n" * text.count("\n", s, t)]
        pos = t
    clean = "".join(clean) + text[pos:]
    clean, amps = BARE_AMP.subn("&amp;", clean)
    parser = xml.parsers.expat.ParserCreate()
    try:
        parser.Parse(clean, True)
    except xml.parsers.expat.ExpatError as e:
        found(3, f"not well-formed: line {e.lineno}, column {e.offset + 1}: {xml.parsers.expat.ErrorString(e.code)}")
    for tag, where in dupes.items():
        names = sorted({n for _, twice, _ in where for n in twice})
        differ = sorted({n for _, _, d in where for n in d})
        found(2, f"duplicate attributes in {len(where)} <{tag}> ({', '.join(names)})"
              + (f", differing: {', '.join(differ)}" if differ else "")
              + (" (#4917)" if tag == "audioClip" else ""))
        details += [f"line {line}: <{tag}> {', '.join(twice)} twice" + (f" (differing: {', '.join(d)})" if d else "")
                    for line, twice, d in where]
    if amps:
        found(1, f"{amps} bare '&' (the Deluge's style; strict XML readers refuse it)")

    # Samples, looked up as the Deluge does
    folder, stem = os.path.split(rel)
    alternate = (folder + "/" if folder else "") + os.path.splitext(stem)[0]
    paths = [v for e in elems for n, v, _, _ in e.attrs if n in SAMPLE_NAMES and v]
    paths += [v for n, v in TEXT_ELEMENT.findall(text) if n in SAMPLE_NAMES and v]
    missing, elsewhere = [], 0
    for p in sorted(set(paths)):
        if card.has(p):
            continue
        if card.has(f"{alternate}/{p[8:].replace('/', '_')}") or card.has(f"{alternate}/{p.rsplit('/', 1)[-1]}"):
            elsewhere += 1
            continue
        missing.append(p)
    if missing:
        found(3, f"{len(missing)} of {len(set(paths))} samples missing")
        details += [f"missing: {p}" for p in missing]
    if elsewhere:
        found(1, f"{elsewhere} samples found in {alternate}/ (the song's own folder)")

    # CV instruments: named ones without a channel
    cv = [e for e in elems if e.tag == "cvChannel"]
    clips = {e.get("cvChannel") for e in elems if e.tag == "instrumentClip" and e.get("cvChannel") is not None}
    channels = {e.get("channel") or "0" for e in cv}
    for e in cv:
        if e.get("channel") is None:
            name = e.get("presetName")
            lost = sorted(clips - channels)
            found(2, (f"CV instrument '{name}'" if name else "a CV instrument") + " has no channel (loads on channel 0)"
                  + (f"; clips on channel {', '.join(lost)} find no CV instrument there" if lost else ""))

    summary = f"{len(set(paths))} samples" if paths else "no samples"
    line = f"{SEVERITY[severity]:<5}  {rel}  [{saved_by or 'version?'}]  " + "; ".join([summary] + notes)
    return severity, line, details if verbose else []


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="the card's folder, or single XML files (with --card)")
    ap.add_argument("--card", help="the card's folder, for single files (sample paths start there)")
    ap.add_argument("--firmware", default="c1.2.1", help="the firmware to check earliestCompatibleFirmware against")
    ap.add_argument("-v", "--verbose", action="store_true", help="the details under each file")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # A Windows console can't show every name

    if a.card:
        card, files = Card(a.card), [os.path.abspath(p) for p in a.paths]
    elif len(a.paths) == 1 and os.path.isdir(a.paths[0]):
        card, files = Card(a.paths[0]), []
        for top in os.listdir(card.root):
            if top.upper() not in FOLDERS or not os.path.isdir(os.path.join(card.root, top)):
                continue
            for folder, dirs, names in os.walk(os.path.join(card.root, top)):
                dirs.sort()
                files += [os.path.join(folder, n) for n in sorted(names)
                          if n.upper().endswith(".XML") and not n.startswith("._")]  # "._": a Mac's metadata
    else:
        ap.error("give the card's folder, or XML files with --card")

    counts = [0] * len(SEVERITY)
    for path in files:
        severity, line, details = check_file(path, card, a.firmware, a.verbose)
        counts[severity] += 1
        print(line)
        for d in details:
            print("         " + d)
    print(f"\n{len(files)} files: " + ", ".join(f"{n} {s}" for s, n in zip(SEVERITY, counts) if n or s == "OK"))
    sys.exit(1 if counts[3] else 0)


if __name__ == "__main__":
    main()
