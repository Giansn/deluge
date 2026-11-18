#!/usr/bin/env python3
"""Checks the samples in a folder of a Deluge SD card (or a copy of it): whether the Deluge loads them, and what
makes them sound or play worse than they could (one-shots such as kicks in particular). The kits' XML itself (well
formed, missing samples, firmware) is song_check.py's job; this tool looks at the audio and at how the card's kits,
synths and songs use it. Python 3 with numpy, like retune_library.py, whose readers it uses.

Per WAV/AIFF file in the folder (subfolders too):
- Loadable (ERROR): a format the firmware reads (AudioFile::loadFile(): PCM 8 to 32 bit or 32-bit float, mono or
  stereo, no WAVE_FORMAT_EXTENSIBLE, no RF64, plain AIFF); every character of the path in code page 437 (the
  firmware's FAT has no Unicode: FF_CODE_PAGE 437, FF_LFN_UNICODE 0); the path at most 255 characters.
- Level (WARN): clipped runs (3 or more samples in a row at full scale; float above full scale, which the firmware
  limits as it loads); a DC offset above 1 %; a silent file.
- Start (WARN): more than 2 ms before the sound passes -60 dBFS, so a one-shot sounds late. NOTE: a first sample
  louder than 5 % (a click, unless it is the attack).
- End (WARN): the last 5 ms louder than -50 dBFS, cut off rather than faded (a click where the sample stops). NOTE:
  more than 500 ms of silence at the end (memory for nothing).
- Format (NOTE): a sample rate other than 44.1 kHz (resampled on every note); stereo with two equal channels (twice
  the memory); 8 bit.
- The tuning tag (mastertune's "mtun" chunk): shown; files of one folder with different tags, WARN.
- The same audio under two names: NOTE.
- A rough pitch (autocorrelation from 50 to 350 ms, after a kick's sweep; 30 to 500 Hz) and the nearest note at the
  file's tuning: shown, to see whether kicks meant to be tuned alike are.
With --card: the card's KITS/, SYNTHS/ and SONGS/ XML read (retune_library.py's parser and path lookup: from the
card's root, case-insensitive, CP437 or UTF-8). Per file the kits and sounds that use it; a file nothing uses: NOTE;
a start or end position (zone) beyond the file's length: WARN.

Output: a Markdown report (a table of all files, then the findings per file), to the console or -o FILE.
Exit status: 1 if any file has an ERROR, else 0.

Usage: sample_check.py <folder> [--card <card root>] [-o report.md]
"""
import argparse
import hashlib
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retune_library as rl  # noqa: E402

SILENCE = 10 ** (-60 / 20)  # -60 dBFS: where the sound starts and ends
END_LOUD = 10 ** (-50 / 20)  # The last 5 ms louder than this: cut off
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def db(v):
    return 20 * math.log10(v) if v > 0 else -math.inf


def note_name(f, tenths):
    """Nearest note to f at the tuning tenths (A4 = tenths / 10 Hz), with the cents off it."""
    midi = 69 + 12 * math.log2(f / (tenths / 10))
    n = round(midi)
    return f"{NOTES[n % 12]}{n // 12 - 1} {100 * (midi - n):+.0f} ct"


def rough_pitch(m, rate):
    """Fundamental of a mono signal from 50 to 350 ms (after a kick's sweep) by autocorrelation, or None if nothing
    periodic enough."""
    seg = m[int(0.05 * rate):int(0.35 * rate)]
    if len(seg) < int(0.05 * rate) or np.max(np.abs(seg)) < SILENCE:
        return None
    seg = seg - np.mean(seg)
    n = 1 << (2 * len(seg) - 1).bit_length()
    spec = np.fft.rfft(seg, n)
    ac = np.fft.irfft(spec * np.conj(spec), n)[:len(seg)]
    # Normalized by the energy of the two overlapping parts: a decaying sound (a kick) doesn't pull the peak shorter
    e = np.concatenate(([0.0], np.cumsum(seg * seg)))
    k = np.arange(len(seg))
    norm = np.sqrt(e[len(seg) - k] * (e[-1] - e[k]))
    if norm[0] <= 0:
        return None
    ac = np.where(norm > 0, ac / np.maximum(norm, 1e-30), 0.0)
    lo, hi = int(rate / 500), min(int(rate / 30), len(seg) - 1)
    neg = np.flatnonzero(ac[:hi] < 0)  # The first period's peak lies after the first dip below zero
    if not len(neg):
        return None
    lo = max(lo, int(neg[0]))
    if hi - lo < 3:
        return None
    w = ac[lo:hi]
    peaks = np.flatnonzero((w[1:-1] >= w[:-2]) & (w[1:-1] >= w[2:]) & (w[1:-1] >= 0.9 * np.max(w))) + 1
    if not len(peaks):
        return None
    lag = lo + int(peaks[0])  # The first peak nearly as high as the highest: not a multiple of the period
    if ac[lag] < 0.5 or not lo < lag < hi - 1:
        return None
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]  # Parabolic interpolation around the peak
    d = a - 2 * b + c
    lag = lag + (max(-0.5, min(0.5, 0.5 * (a - c) / d)) if d else 0)
    return rate / lag


def clipped_runs(x, info):
    """Runs of 3 or more samples in a row at (or, float, above) full scale, over all channels."""
    top = 1.0 if info.float else (2 ** (info.bits - 1) - 1) / 2 ** (info.bits - 1)
    over = (x > 1.0) | (x < -1.0) if info.float else (x >= top) | (x <= -1.0)
    runs = 0
    for ch in range(x.shape[1]):
        o = over[:, ch].astype(np.int8)
        if not o.any():
            continue
        edges = np.diff(np.concatenate(([0], o, [0])))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        runs += int(np.count_nonzero(ends - starts >= 3))
    return runs


def analyse(path, rel):
    r = dict(rel=rel, findings=[], level="OK", used=[])

    def find(level, text):
        r["findings"].append((level, text))
        order = ("OK", "NOTE", "WARN", "ERROR")
        if order.index(level) > order.index(r["level"]):
            r["level"] = level

    try:
        rel.encode("cp437")
    except UnicodeEncodeError:
        bad = "".join(sorted({c for c in rel if not c.encode("cp437", "ignore")}))
        find("ERROR", f"characters outside code page 437 in the path ({bad}): the Deluge can't find the file")
    if len(rel) > 255:
        find("ERROR", f"the path has {len(rel)} characters, FAT allows 255")
    with open(path, "rb") as fh:
        info = rl.read_audio_info(fh)
        r["info"] = info
        if info.kind is None:
            find("ERROR", f"the Deluge can't load it: {info.error}")
            return r
        fh.seek(info.data_pos)
        raw = fh.read(info.frames * info.block_align)
    r["hash"] = hashlib.sha1(raw).hexdigest()
    x = rl.decode(raw, info)
    rate = info.rate
    r["seconds"] = info.frames / rate if rate else 0
    env = np.max(np.abs(x), axis=1) if len(x) else np.zeros(0)
    peak = float(env.max()) if len(env) else 0.0
    r["peak_db"] = db(peak)
    r["pitch"] = None
    if peak < SILENCE:
        find("WARN", "silent (nothing above -60 dBFS)")
        return r
    runs = clipped_runs(x, info)
    if runs:
        find("WARN", f"clipped: {runs} run(s) of 3+ samples at full scale")
    dc = float(np.max(np.abs(np.mean(x, axis=0))))
    if dc > 0.01:
        find("WARN", f"DC offset {100 * dc:.1f} % of full scale")
    loud = np.flatnonzero(env > SILENCE)
    lead_ms = 1000 * loud[0] / rate
    r["lead_ms"] = lead_ms
    if lead_ms > 2:
        find("WARN", f"{lead_ms:.1f} ms before the sound starts (-60 dBFS): a one-shot plays late")
    if env[0] > 0.05:
        find("NOTE", f"starts at {db(env[0]):.1f} dBFS, not at zero (a click, unless it is the attack)")
    tail = env[-max(1, int(0.005 * rate)):]
    r["end_db"] = db(float(np.max(tail)))
    if np.max(tail) > END_LOUD:
        find("WARN", f"the last 5 ms are at {r['end_db']:.1f} dBFS: cut off, not faded (a click where it stops)")
    trail_ms = 1000 * (len(env) - 1 - loud[-1]) / rate
    if trail_ms > 500:
        find("NOTE", f"{trail_ms:.0f} ms of silence at the end (memory for nothing)")
    if rate != 44100:
        find("NOTE", f"{rate} Hz: the Deluge resamples it on every note (44100 Hz plays as it is)")
    if info.channels == 2 and np.max(np.abs(x[:, 0] - x[:, 1])) < 1e-6:
        find("NOTE", "stereo with two equal channels: mono needs half the memory")
    if info.bits == 8 and not info.float:
        find("NOTE", "8 bit: audible noise in quiet passages")
    f = rough_pitch(np.mean(x, axis=1), rate)
    if f is not None:
        r["pitch"] = f
        r["note"] = note_name(f, info.tuning)
    return r


def kit_usage(card, by_key, results):
    """Which XML on the card use which file of results (keyed by path_key of the path from the card's root)."""
    warnings = []
    for rel in rl.card_files(card):
        if rel.split("/")[0].upper() not in rl.XML_FOLDERS or not rel.lower().endswith(".xml"):
            continue
        if rel.split("/")[-1].startswith("._"):
            continue
        with open(os.path.join(card, rel), "rb") as fh:
            text = fh.read().decode("utf-8", errors="surrogateescape")
        try:
            root = rl.parse_xml(text)
        except ValueError as e:
            warnings.append(f"{rel}: not readable as XML ({e}): song_check.py says more")
            continue
        refs, _ = rl.refs_in_xml(rel, root, warnings)
        for ref in refs:
            target = rl.card_path(ref.path[0], by_key)[0]
            r = results.get(rl.path_key(target))
            if r is None:
                continue
            r["used"].append(f"{rel} ({rl.readable(ref.context)})")
            info = r.get("info")
            if info is None or info.kind is None:
                continue
            pos = {k: rl.as_int(v, -1) for k, v in ref.positions.items()}
            start, end = pos.get("startSamplePos", 0), pos.get("endSamplePos", -1)
            if end > info.frames or start >= info.frames or (end >= 0 and start >= end):
                r["findings"].append(("WARN", f"{rel} ({rl.readable(ref.context)}): zone {start} to {end} doesn't "
                                              f"fit the file's {info.frames} frames"))
                if r["level"] in ("OK", "NOTE"):
                    r["level"] = "WARN"
    return warnings


def report(folder, card, results, warnings):
    out = [f"# Sample check: {folder}", ""]
    if card:
        out += [f"Card root: `{card}`", ""]
    counts = defaultdict(int)
    for r in results.values():
        counts[r["level"]] += 1
    out += [f"{len(results)} files: " + ", ".join(f"{counts[k]} {k}" for k in ("ERROR", "WARN", "NOTE", "OK")
                                                  if counts[k]), ""]
    out += ["| File | Result | Format | Length | Peak | Start | End | Pitch (rough) | mtun | Used by |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(results.values(), key=lambda r: r["rel"].lower()):
        info = r.get("info")
        if info is None or info.kind is None:
            out.append(f"| `{r['rel']}` | {r['level']} | – | – | – | – | – | – | – | {len(r['used'])} |")
            continue
        fmt = f"{info.rate / 1000:g} kHz {'float' if info.float else info.bits} {'st' if info.channels == 2 else 'mo'}"
        pitch = f"{r['pitch']:.1f} Hz {r['note']}" if r.get("pitch") else "–"
        mtun = f"{info.mtun / 10:g}" if info.mtun is not None else "–"
        out.append(f"| `{r['rel']}` | {r['level']} | {fmt} | {r.get('seconds', 0):.3f} s | "
                   f"{r.get('peak_db', -math.inf):.1f} dB | {r.get('lead_ms', 0):.1f} ms | "
                   f"{r.get('end_db', -math.inf):.0f} dB | {pitch} | {mtun} | {len(r['used'])} |")
    out += ["", "## Findings", ""]
    any_finding = False
    for r in sorted(results.values(), key=lambda r: r["rel"].lower()):
        if r["findings"]:
            any_finding = True
            out.append(f"- **{r['rel']}** ({r['level']})")
            out += [f"  - {lvl}: {text}" for lvl, text in r["findings"]]
    if not any_finding:
        out.append("None.")
    if card:
        out += ["", "## Use in kits, synths and songs", ""]
        for r in sorted(results.values(), key=lambda r: r["rel"].lower()):
            out.append(f"- `{r['rel']}`: " + ("; ".join(sorted(set(r["used"]))) if r["used"] else "**not used**"))
    if warnings:
        out += ["", "## Other", ""] + [f"- {w}" for w in warnings]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", help="the sample folder, e.g. <card>/SAMPLES/KickHm")
    ap.add_argument("--card", help="the card's root (with KITS/, SYNTHS/, SONGS/): also check how they use the files")
    ap.add_argument("-o", "--out", help="write the report to this file (Markdown) instead of the console")
    args = ap.parse_args()
    folder = os.path.abspath(os.path.expanduser(args.folder))
    card = os.path.abspath(os.path.expanduser(args.card)) if args.card else None
    if not os.path.isdir(folder):
        sys.exit(f"{folder}: no such folder")
    try:
        base = card if card and os.path.commonpath([card, folder]) == card else folder
    except ValueError:  # Windows: another drive
        base = folder
    results, warnings = {}, []
    for rel in rl.card_files(folder):
        name = rel.split("/")[-1]
        if name.startswith("._") or not name.lower().endswith(rl.AUDIO_EXTENSIONS):
            if name.startswith("._") or name in (".DS_Store", "Thumbs.db"):
                warnings.append(f"{rel}: a computer's hidden file, can be deleted from the card")
            continue
        full = os.path.join(folder, rel)
        card_rel = os.path.relpath(full, base).replace(os.sep, "/")
        results[rl.path_key(card_rel)] = analyse(full, card_rel)
    if not results:
        sys.exit(f"{folder}: no WAV or AIFF files")
    hashes = defaultdict(list)
    for r in results.values():
        if r.get("hash"):
            hashes[r["hash"]].append(r)
    for same in hashes.values():
        for r in same[1:]:
            r["findings"].append(("NOTE", f"the same audio as {same[0]['rel']}"))
            r["level"] = "NOTE" if r["level"] == "OK" else r["level"]
    tags = defaultdict(set)
    for r in results.values():
        info = r.get("info")
        if info is not None and info.kind:
            tags[os.path.dirname(r["rel"])].add(info.mtun)
    for d, t in tags.items():
        if len(t) > 1:
            names = ", ".join("none" if v is None else f"{v / 10:g} Hz" for v in sorted(t, key=lambda v: v or 0))
            warnings.append(f"{d or '.'}: files with different tuning tags ({names}): retune_library.py or "
                            f"DelugeTuner brings them to one tuning")
    if card:
        by_key = {rl.path_key(f): f for f in rl.card_files(card)}
        warnings += kit_usage(card, by_key, results)
        for r in results.values():
            if not r["used"]:
                r["findings"].append(("NOTE", "no kit, synth or song on the card uses it"))
                r["level"] = "NOTE" if r["level"] == "OK" else r["level"]
    text = report(args.folder, args.card, results, warnings)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"{len(results)} files checked, report: {args.out}")
    else:
        sys.stdout.write(text)
    return 1 if any(r["level"] == "ERROR" for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
