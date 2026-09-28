#!/usr/bin/env python3
"""DelugeBaseline: checks the songs of a Deluge card against the baseline master and sets them and the samples to it
(geraet/analyse/2026-09-27-baseline-master.md). Built into DelugeBaseline-vN.exe for Windows by
.github/workflows/deluge-baseline-windows.yml; runs as a script anywhere.

Four functions, in its window or on the command line:
- Prüfen (check): the report of baseline_check.py. Only reads.
- Pegel (levels): sets the songs to the baseline. The song's volume above 35 down to 35, the master compressor off,
  kits and audio tracks above 35 down to 35, synths and kit rows down to where they are as loud as a full-scale sample
  at 40 (their sample's peak within its zone and their oscillator's level counted, as baseline_check.py judges them).
  Automation keeps its shape: all its values scale alike. What distorts on purpose (SATURATION, compressors of tracks,
  analog delay, filters) stays: that's sound, not level. The report lists it.
- Normalisieren (samples): raises every sample under SAMPLES/ whose peak is below the target (-1 dBFS unless chosen
  otherwise) to the target, never above: no peak is cut, no sample clips. Samples at the target or above stay as they
  are. The format stays (bits, float, channels, every chunk), only the audio changes. The files an oscillator plays
  together (the ranges of a multisample) get one gain, the one of the loudest, so they keep their balance.
  Ausgleichen (compensate, on unless switched off): every oscillator that plays a raised sample gets its level (osc A
  or B volume) lowered by just as much, in every clip of every song and in the kits and synths of KITS/ and SYNTHS/.
  The voice gets the same signal as before, before its filters and effects, so the songs sound as before, only with
  the knobs meaning the same everywhere. A sample whose every use can't be compensated that way stays as it is: an
  oscillator level that isn't saved or has a patch cable to it. Never changed: wavetables and audio clips.
- Zurückspielen (restore): puts back the files of a backup.

Where it writes: onto the card, keeping a copy of every file it changes in BASELINE-BACKUP/<date> <time> <function>/
on the card first (not under SONGS, so the Deluge doesn't list them), or into a folder of its own: only the changed
files, in the card's layout, to copy onto the card. It never writes anything before showing what it would do.

Firmware (mastertune v17 on release_1_2_1): see baseline_check.py. The oscillator levels LOCAL_OSC_A/B_VOLUME are
volume params like the rest (getFinalParameterValueVolume(): the gain goes with the knob squared) and scale the sample
in Voice::render() before anything else happens to it.

Usage (Windows: py instead of python3; without arguments it opens its window):
  python3 deluge_baseline.py check CARD [--out REPORT.md]
  python3 deluge_baseline.py levels CARD [--to FOLDER] [--yes]
  python3 deluge_baseline.py normalize CARD [--target DB] [--no-compensate] [--to FOLDER] [--yes]
  python3 deluge_baseline.py restore BACKUP [--yes]
  Without --yes it only shows what it would do. CARD: the card's root (with SONGS in it) or its SONGS folder.
Needs numpy to normalize (pip install numpy); everything else only Python 3.8.

Versions:
  1  the first build: check, levels, normalize with compensation, restore; onto the card with a backup or into a folder
"""
import argparse
import datetime
import json
import math
import os
import queue
import shutil
import sys
import tempfile
import threading
import unicodedata
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_check as bc  # noqa: E402

try:
    import numpy as np
except ImportError:  # Only normalizing needs it: checked there
    np = None

VERSION = 1
AUDIO_EXTENSIONS = (".wav", ".aif", ".aiff")
XML_FOLDERS = ("SONGS", "KITS", "SYNTHS")
ALWAYS_KEPT = ("Wavetable", "Audio-Clip")  # Samples that are never normalized; other uses only matter to compensate
BACKUP = "BASELINE-BACKUP"
REPORT_NAME = "BASELINE.txt"
TARGETS = (0.0, -0.3, -1.0, -3.0, -6.0)  # dBFS, for the window's choice
RAISE_FROM_DB = 0.1  # A sample closer to the target than this stays as it is
LEVELS = {"osc1": "oscAVolume", "osc2": "oscBVolume"}


# --------------------------------------------------------------------------------------------------------------------
# Params: a volume knob's value v (0-50) is the param v * 85899345 - 2^31, its gain goes with v squared


def hex8(v):
    return f"{v & 0xFFFFFFFF:08X}"


def param_of_knob(k):
    """The param for a knob value, rounded down: never louder than asked."""
    return max(-2 ** 31, min(2 ** 31 - 1, math.floor(k * bc.KNOB_STEP) - 2 ** 31))


def scale_text(text, factor):
    """A param's text with every value, automation's too, scaled as a knob by factor; the automation's positions and
    the rest of the text stay as they are."""
    t = text.strip()
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]

    def scaled(word):
        v = int(word, 16)
        v = v - 2 ** 32 if v >= 2 ** 31 else v
        return hex8(param_of_knob(bc.knob(v) * factor))

    if t[:2].lower() == "0x":
        words = [t[i:i + 8] for i in range(2, len(t), 8)]
        out = [scaled(w) if i == 0 or i % 2 == 1 else w for i, w in enumerate(words)]  # Values; positions stay
        return lead + t[:2] + "".join(out) + trail
    v = int(t, 10)
    return lead + str(param_of_knob(bc.knob(v) * factor)) + trail


class XmlEdit:
    """Changes to one XML text, by position: the rest stays byte for byte."""

    def __init__(self, rel, text):
        self.rel, self.text = rel, text
        self.edits = {}  # (start, end) -> new text
        self.lines = []  # What changed, for the report

    def replace(self, span, new):
        old = self.edits.get(span)
        if old is not None and old != new:
            raise ValueError(f"{self.rel}: zwei Änderungen an derselben Stelle")
        self.edits[span] = new

    def result(self):
        out, last = [], 0
        for (start, end), new in sorted(self.edits.items()):
            if start < last:
                raise ValueError(f"{self.rel}: Änderungen überlappen")
            out += [self.text[last:start], new]
            last = end
        out.append(self.text[last:])
        return "".join(out).encode("utf-8", "surrogateescape")


# --------------------------------------------------------------------------------------------------------------------
# The card


def card_root(path):
    """The card's root for a path: the folder that holds SONGS, or the parent of a SONGS folder."""
    p = Path(path).resolve()
    if p.name.upper() == "SONGS" and p.is_dir():
        return p.parent
    if p.is_dir() and any(c.name.upper() == "SONGS" and c.is_dir() for c in p.iterdir()):
        return p
    raise ValueError(f"{path}: kein Ordner SONGS darin, das ist keine Deluge-Karte")


def folder(root, name):
    """A folder of the card by name, as FAT compares names (case ignored), or None."""
    return next((c for c in root.iterdir() if c.name.upper() == name and c.is_dir()), None)


def xml_files(root, folders=XML_FOLDERS):
    """(relative path with /, path) of every XML file in the card's SONGS, KITS and SYNTHS."""
    found = []
    for name in folders:
        top = folder(root, name)
        for d, _, names in os.walk(top) if top else []:
            for n in names:
                if n.upper().endswith(".XML") and not n.startswith("._"):
                    full = Path(d) / n
                    found.append((full.relative_to(root).as_posix(), full))
    return sorted(found, key=lambda f: f[0].lower())


def read_xml(path):
    text = path.read_bytes().decode("utf-8", "surrogateescape")
    return text, bc.parse_xml(text)


def rel_key(rel):
    return unicodedata.normalize("NFC", rel.replace("\\", "/")).lower()


class Change:
    def __init__(self, rel, data, lines):
        self.rel, self.data, self.lines = rel, data, lines


class Plan:
    """What a function would write: files by their path on the card, and its report."""

    def __init__(self, root, action, title):
        self.root, self.action, self.title = Path(root), action, title
        self.changes = {}  # rel -> Change
        self.sections = []  # (heading, lines)
        self.summary = []

    def add(self, rel, data, lines):
        self.changes[rel] = Change(rel, data, lines)

    def report(self):
        out = [f"# {self.title}", ""] + [f"- {s}" for s in self.summary]
        for heading, lines in self.sections:
            out += ["", f"## {heading}", ""] + [f"- {line}" for line in lines]
        if not self.changes:
            out += ["", "Nichts zu ändern."]
        return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------------------------------------
# Pegel: the songs' levels to the baseline


def limit(edit, node, name, top, label, clip, suffix=""):
    """Lowers a volume param whose loudest value is above top (a param value) to top, automation alike."""
    values = bc.param_values(node.get(name)) if node is not None else []
    span = node.span(name) if node is not None else None
    if not values or bc.knob(max(values)) <= bc.knob(top) + 1e-3 or span is None:  # 0x7FFFFFFF is 50 too
        return
    old = bc.knob(max(values))
    new = bc.knob(top)
    if len(values) == 1:
        edit.replace(span, "0x" + hex8(top))
    else:
        edit.replace(span, scale_text(edit.text[span[0]:span[1]], new / old))
    where = f" (Clip {clip})" if clip else ""
    edit.lines.append(f"{label}{where}: {bc.num(old)} auf {bc.num(new)}, {bc.signed_db(bc.db_between(old, new))}"
                      + suffix)


def sound_top(sound, params, card, label, edit):
    """The highest volume a synth or row may have (a param value), or None if its samples can't be read."""
    samples, other = bc.sources(sound, params)
    if other or not samples:
        return bc.SOUND_LIMIT
    level, problems = bc.sample_level(samples, card)
    if problems:
        line = f"{label}: nicht geändert ({'; '.join(problems)})"
        if line not in edit.lines:
            edit.lines.append(line)
        return None
    if level[0] <= 0:
        return None
    k = 40 / level[0]
    return param_of_knob(40 if abs(k - 40) < 0.05 else min(50.0, k))  # A full-scale sample: 40 exactly


def clip_name(clip, number):
    return bc.readable(clip.get("clipName")) or str(number)


def plan_levels(root):
    root = Path(root)
    plan = Plan(root, "Pegel", "Pegel auf die Baseline")
    card = bc.Card(str(root))
    songs = xml_files(root, ("SONGS",))
    changed = unreadable = 0
    for rel, path in songs:
        try:
            text, tree = read_xml(path)
        except (OSError, ValueError) as e:
            plan.sections.append((rel, [f"nicht lesbar: {e}"]))
            unreadable += 1
            continue
        song = tree.child("song")
        if song is None:
            continue
        edit = XmlEdit(rel, text)
        params = song.child("songParams")
        limit(edit, params, "volume", bc.SONG_KIT_LIMIT, "Song-Lautstärke", None)
        comp = bc.loudest(params, "compressorThreshold") if params is not None else None
        span = params.span("compressorThreshold") if params is not None else None
        if comp is not None and comp > 0 and span is not None:
            edit.replace(span, "0x00000000")
            edit.lines.append(f"Master-Kompressor aus (Threshold war {round(bc.unipolar_knob(comp))})")
        instruments = song.child("instruments")
        kits, synths = {}, {}
        for inst in instruments.children if instruments is not None else []:
            if inst.name == "kit":
                kits[bc.track_key(inst, "preset")] = inst
            elif inst.name == "sound":
                synths[bc.track_key(inst, "preset")] = inst
        number = {}
        for clip in song.iter():
            if clip.name not in ("instrumentClip", "audioClip"):
                continue
            key = bc.track_key(clip, "instrumentPreset") if clip.name == "instrumentClip" else clip.get("trackName")
            number[key] = number.get(key, 0) + 1
            name = clip_name(clip, number[key])
            if clip.name == "audioClip":
                limit(edit, clip.child("params"), "volume", bc.SONG_KIT_LIMIT, f"Audio-Spur «{bc.readable(key)}»",
                      name)
            elif clip.child("kitParams") is not None:
                label = f"Kit «{bc.readable(key)}»"
                limit(edit, clip.child("kitParams"), "volume", bc.SONG_KIT_LIMIT, label, name)
                kit = kits.get(key)
                sources = kit.child("soundSources") if kit is not None else None
                drums = sources.children if sources is not None else []
                rows = clip.child("noteRows")
                for nr in rows.children if rows is not None else []:
                    sp, index = nr.child("soundParams"), nr.get("drumIndex")
                    if sp is None or index is None or not index.isdigit() or int(index) >= len(drums):
                        continue
                    drum = drums[int(index)]
                    if drum.name != "sound":
                        continue
                    row_label = f"Reihe «{bc.readable(drum.get('name'))}» in {label}"
                    top = sound_top(drum, [sp], card, row_label, edit)
                    if top is not None:
                        limit(edit, sp, "volume", top, row_label, name, "" if bc.has_notes(nr) else ", ohne Noten")
            elif clip.child("soundParams") is not None:
                label = f"Synth «{bc.readable(key)}»"
                sp = clip.child("soundParams")
                top = sound_top(synths.get(key), [sp], card, label, edit)
                if top is not None:
                    limit(edit, sp, "volume", top, label, name)
        if edit.edits:
            plan.add(rel, edit.result(), edit.lines)
            changed += 1
        if edit.lines:
            plan.sections.append((rel, edit.lines))
    plan.summary.append(f"{len(songs)} Songs gelesen, {changed} zu ändern" + (f", {unreadable} nicht lesbar"
                                                                               if unreadable else ""))
    plan.summary.append("Nicht angefasst: SATURATION, Kompressoren der Spuren, Analog-Delay und Filter. Das ist Klang, "
                        "kein Pegel. «Prüfen» listet sie.")
    return plan


# --------------------------------------------------------------------------------------------------------------------
# Normalisieren: the samples to the target, the oscillators that play them lowered by as much


class OscRef:
    """An oscillator that plays samples, with every place its level is kept (clips of songs, defaultParams of kits
    and synths) and the reason, if any, why that level can't compensate a raised sample."""

    def __init__(self, rel, sound, osc, params, holders, label):
        self.rel, self.sound, self.osc, self.params, self.holders, self.label = rel, sound, osc, params, holders, label
        self.level = LEVELS[osc]
        self.files = []  # The card files its holders name (None where missing)
        self.problem = None
        if not params:
            self.problem = "Osc-Pegel nirgends gespeichert"
        for p in params:
            if not bc.param_values(p.get(self.level)) or p.span(self.level) is None:
                self.problem = "Osc-Pegel nicht gespeichert"
        cables = [c for n in [sound] + params for c in n.iter() if c.name == "patchCable"]
        if any(c.get("destination") == self.level for c in cables):
            self.problem = "Kabel auf den Osc-Pegel"


def sound_refs(rel, sound, params, label, other):
    """The sample oscillators of a sound; its other uses of files go to other (file value -> reason)."""
    refs = []
    defaults = sound.child("defaultParams")
    params = [p for p in params if p is not None] + ([defaults] if defaults is not None else [])
    for osc in LEVELS:
        node = sound.child(osc)
        if node is None:
            continue
        ranges = [r for g in node.children if g.name == "sampleRanges" for r in g.children]
        holders = [h for h in [node] + ranges if h.get("fileName")]
        if not holders:
            continue
        if node.get("type") == "wavetable":
            for h in holders:
                other[h.get("fileName")] = "Wavetable"
            continue
        if (sound.get("mode") or "subtractive") != "subtractive":
            for h in holders:
                other.setdefault(h.get("fileName"), "FM oder Ringmod")
            continue
        refs.append(OscRef(rel, sound, osc, params, holders, label))
    return refs


def xml_refs(rel, tree):
    """(sample oscillators, {file value: reason} for the other files it names) of one XML of the card."""
    refs, other = [], {}
    top = next((c for c in tree.children if c.name in ("song", "kit", "sound")), None)
    if top is None:
        return refs, other
    if top.name == "song":
        clips = [c for c in top.iter() if c.name == "instrumentClip"]
        instruments = top.child("instruments")
        for inst in instruments.children if instruments is not None else []:
            key = bc.track_key(inst, "preset")
            mine = [c for c in clips if bc.track_key(c, "instrumentPreset") == key]
            if inst.name == "sound":
                params = [c.child("soundParams") for c in mine if c.child("kitParams") is None]
                refs += sound_refs(rel, inst, params, f"Synth «{bc.readable(key)}»", other)
            elif inst.name == "kit":
                sources = inst.child("soundSources")
                for i, drum in enumerate(sources.children if sources is not None else []):
                    if drum.name != "sound":
                        continue
                    params = [nr.child("soundParams") for c in mine if c.child("kitParams") is not None
                              for nr in (c.child("noteRows").children if c.child("noteRows") is not None else [])
                              if nr.get("drumIndex") == str(i)]
                    label = f"Reihe «{bc.readable(drum.get('name'))}» in Kit «{bc.readable(key)}»"
                    refs += sound_refs(rel, drum, params, label, other)
        for c in top.iter():
            if c.name == "audioClip" and c.get("filePath"):
                other[c.get("filePath")] = "Audio-Clip"
    elif top.name == "kit":
        sources = top.child("soundSources")
        for drum in sources.children if sources is not None else []:
            if drum.name == "sound":
                refs += sound_refs(rel, drum, [], f"Reihe «{bc.readable(drum.get('name'))}» im Kit", other)
    else:
        refs += sound_refs(rel, top, [], "Synth", other)
    handled = {id(h) for r in refs for h in r.holders}
    for node in tree.iter():
        for key in ("fileName", "filePath"):
            v = node.attrs.get(key)
            if v and v.lower().endswith(AUDIO_EXTENSIONS) and id(node) not in handled:
                other.setdefault(v, "anderes Format, zum Beispiel von 2016")
    return refs, other


def is_wavetable_file(path):
    """A Serum wavetable: a WAV with a "clm " chunk (the firmware reads it as one)."""
    with open(path, "rb") as fh:
        head = fh.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return False
        size = os.fstat(fh.fileno()).st_size
        return any(cid == b"clm " for cid, _, _ in bc._chunks(fh, size, "<I"))


def decode(raw, f):
    """Audio as numbers: floats for float files, else the integers (8 bit from -128)."""
    e = ">" if f["big"] else "<"
    if f["float"]:
        return np.frombuffer(raw, e + "f4").astype(np.float64)
    if f["bits"] == 8:
        return np.frombuffer(raw, np.int8 if f["big"] else np.uint8).astype(np.int64) - (0 if f["big"] else 128)
    if f["bits"] == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int64)
        if f["big"]:
            b = b[:, ::-1]
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        return v - ((v & 0x800000) << 1)
    return np.frombuffer(raw, f"{e}i{f['bits'] // 8}").astype(np.int64)


def encode(x, f):
    e = ">" if f["big"] else "<"
    if f["float"]:
        return x.astype(e + "f4").tobytes()
    if f["bits"] == 8:
        return (x.astype(np.int8) if f["big"] else (x + 128).astype(np.uint8)).tobytes()
    if f["bits"] == 24:
        v = (x & 0xFFFFFF).astype(np.uint32)
        b = np.stack([v & 0xFF, (v >> 8) & 0xFF, v >> 16], axis=1).astype(np.uint8)
        return (b[:, ::-1] if f["big"] else b).tobytes()
    return x.astype(f"{e}i{f['bits'] // 8}").tobytes()


def raised(path, gain):
    """The file's bytes with its audio multiplied by gain, which is capped so that no sample goes over full scale."""
    data = bytearray(Path(path).read_bytes())
    with open(path, "rb") as fh:
        f = bc.audio_format(fh)
    raw = bytes(data[f["pos"]:f["pos"] + f["size"]])
    x = decode(raw, f)
    if f["float"]:
        peak = float(np.max(np.abs(x))) if x.size else 0.0
        gain = min(gain, 1.0 / peak) if peak > 0 else gain
        y = x * gain
    else:
        top = 2 ** (f["bits"] - 1)
        hi, lo = (int(x.max()), int(x.min())) if x.size else (0, 0)
        if hi > 0:
            gain = min(gain, (top - 1) / hi)
        if lo < 0:
            gain = min(gain, top / -lo)
        y = np.rint(x * gain).astype(np.int64)
        if y.size and (y.max() > top - 1 or y.min() < -top):
            raise ValueError(f"{path}: würde clippen")  # Can't happen with the cap above
    data[f["pos"]:f["pos"] + f["size"]] = encode(y, f)
    return bytes(data), gain


class Groups:
    """Files played by one oscillator together (the ranges of a multisample): they get one gain."""

    def __init__(self):
        self.parent = {}

    def find(self, k):
        self.parent.setdefault(k, k)
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def union(self, keys):
        keys = list(keys)
        for k in keys[1:]:
            self.parent[self.find(k)] = self.find(keys[0])


def plan_normalize(root, target_db=-1.0, compensate=True, progress=None):
    if np is None:
        raise RuntimeError("Normalisieren braucht numpy: pip install numpy")
    if target_db > 0:
        raise ValueError("Das Ziel muss bei 0 dBFS oder darunter liegen")
    root = Path(root)
    title = f"Samples normalisieren: Ziel {bc.num(target_db)} dBFS, " + (
        "Songs, Kits und Synths ausgeglichen" if compensate else "ohne Ausgleich")
    plan = Plan(root, "Normalisieren", title)
    card = bc.Card(str(root))
    card.find("")  # Reads the card's list of files, card.index
    sample_files = sorted({p for k, p in card.index.items() if k.startswith("samples/")
                           and k.endswith(AUDIO_EXTENSIONS)}, key=str.lower)

    # Every XML: its sample oscillators and its other uses of files
    refs, other, texts = [], {}, {}
    for rel, path in xml_files(root):
        try:
            texts[rel] = read_xml(path)
        except (OSError, ValueError) as e:
            plan.sections.append((rel, [f"nicht lesbar, seine Samples bleiben wie sie sind: {e}"]))
            texts[rel] = None
            continue
        r, o = xml_refs(rel, texts[rel][1])
        refs += r
        for value, reason in o.items():
            found = card.find(value)
            if found:
                other.setdefault(found, reason)
    unreadable_xml = [rel for rel, t in texts.items() if t is None]

    groups, uses = Groups(), {}
    for r in refs:
        r.files = [card.find(h.get("fileName")) for h in r.holders]
        keys = [f or "missing:" + h.get("fileName") for f, h in zip(r.files, r.holders)]
        groups.union(keys)
        for k in keys:
            uses.setdefault(k, []).append(r)

    # Each sample's own gain: to the target, only up
    target = 10 ** (target_db / 20)
    skipped, loud, gains = {}, 0, {}
    for i, path in enumerate(sample_files):
        if progress:
            progress(f"Lese Samples {i + 1}/{len(sample_files)}")
        try:
            with open(path, "rb") as fh:
                f = bc.audio_format(fh)
            if isinstance(f, str):
                skipped[path] = f
                continue
            if is_wavetable_file(path):
                skipped[path] = "Wavetable"
                continue
            peak, error = bc.sample_peak(path)
        except (OSError, ValueError) as e:
            skipped[path] = f"nicht lesbar ({e})"
            continue
        if path in other and (compensate or other[path] in ALWAYS_KEPT):
            skipped[path] = other[path]
        elif peak is None or peak <= 0:
            skipped[path] = error or "still"
        elif compensate and any(r.problem for r in uses.get(path, [])):
            skipped[path] = next(r.problem for r in uses[path] if r.problem) + " (" + next(
                r.label for r in uses[path] if r.problem) + ")"
        else:
            # An integer file's target: at most one step below full scale, so that no sample needs capping and all
            # files of a multisample get exactly the gain of their group
            t = target if f["float"] else min(target, 1 - 2.0 ** (1 - f["bits"]))
            if 20 * math.log10(t / peak) < RAISE_FROM_DB:
                loud += 1
            else:
                gains[path] = t / peak
    if unreadable_xml and compensate:  # Its uses are unknown: nothing may change
        for path in list(gains):
            del gains[path]
            skipped[path] = "ein XML der Karte ist nicht lesbar"

    # One gain per group: the smallest, and none if one of its files can't change
    members = {}
    for k in list(groups.parent):
        members.setdefault(groups.find(k), []).append(k)
    for group in members.values():
        if len(group) < 2:
            continue
        if any(k not in gains for k in group):
            for k in group:
                if k in gains:
                    del gains[k]
                    skipped[k] = "Multisample mit einem Sample, das bleibt"
            continue
        g = min(gains[k] for k in group)
        if 20 * math.log10(g) < RAISE_FROM_DB:
            for k in group:
                del gains[k]
            loud += len(group)
            continue
        for k in group:
            gains[k] = g

    # The files, then the oscillators that play them
    applied, lines = {}, []
    for i, (path, g) in enumerate(sorted(gains.items(), key=lambda kv: kv[0].lower())):
        if progress:
            progress(f"Rechne Samples {i + 1}/{len(gains)}")
        data, g = raised(path, g)
        applied[path] = g
        rel = Path(path).relative_to(root).as_posix()
        line = f"{bc.readable(rel)}: {bc.signed_db(20 * math.log10(g))}"
        plan.add(rel, data, [line])
        lines.append(line)
    compensated = {}
    if compensate:
        edits = {}
        for r in refs:
            gs = {applied[f] for f in r.files if f in applied}
            if not gs:
                continue
            g = gs.pop()  # One group: one gain
            text, _ = texts[r.rel]
            edit = edits.setdefault(r.rel, XmlEdit(r.rel, text))
            for p in r.params:
                span = p.span(r.level)
                edit.replace(span, scale_text(text[span[0]:span[1]], 1 / math.sqrt(g)))
            edit.lines.append(f"{r.label}: Osc {'A' if r.osc == 'osc1' else 'B'} {bc.signed_db(-20 * math.log10(g))}")
            compensated[r.rel] = compensated.get(r.rel, 0) + 1
        for rel, edit in edits.items():
            plan.add(rel, edit.result(), edit.lines)
            plan.sections.append((f"Ausgeglichen: {rel}", edit.lines))
    if applied:
        db = [20 * math.log10(g) for g in applied.values()]
        plan.summary.append(f"{len(applied)} {'Sample' if len(applied) == 1 else 'Samples'} angehoben, um "
                            f"{bc.num(min(db))} bis {bc.num(max(db))} dB")
    plan.summary.append(f"{loud} {'Sample' if loud == 1 else 'Samples'} schon laut genug (höchstens "
                        f"{bc.num(RAISE_FROM_DB)} dB unter dem Ziel)")
    if skipped:
        plan.summary.append(f"{len(skipped)} {'Sample bleibt' if len(skipped) == 1 else 'Samples bleiben'} wie "
                            f"{'es ist' if len(skipped) == 1 else 'sie sind'}, siehe unten")
    if compensate:
        n, files = sum(compensated.values()), len(compensated)
        plan.summary.append(f"Ausgeglichen: {n} {'Oszillator' if n == 1 else 'Oszillatoren'} in {files} "
                            f"{'Datei' if files == 1 else 'Dateien'} (Songs, Kits, Synths). Die Songs klingen wie "
                            f"vorher.")
    else:
        plan.summary.append("Ohne Ausgleich: Songs, die angehobene Samples spielen, werden an diesen Stellen lauter.")
    if lines:
        plan.sections.insert(0, ("Angehoben", lines))
    if skipped:
        plan.sections.append(("Bleiben wie sie sind", [
            f"{bc.readable(Path(p).relative_to(root).as_posix())}: {why}"
            for p, why in sorted(skipped.items(), key=lambda kv: kv[0].lower())]))
    return plan


# --------------------------------------------------------------------------------------------------------------------
# Zurückspielen, and writing


def plan_restore(backup):
    backup = Path(backup).resolve()
    if backup.parent.name != BACKUP:
        raise ValueError(f"{backup}: keine Sicherung (die liegen in {BACKUP}/ auf der Karte)")
    root = backup.parent.parent
    plan = Plan(root, "Zurückspielen", f"Sicherung zurückspielen: {backup.name}")
    lines = []
    for d, _, names in os.walk(backup):
        for n in names:
            full = Path(d) / n
            rel = full.relative_to(backup).as_posix()
            if rel == REPORT_NAME:
                continue
            plan.add(rel, full.read_bytes(), [bc.readable(rel)])
            lines.append(bc.readable(rel))
    plan.summary.append(f"{files(len(lines))} zurück auf die Karte")
    plan.sections.append(("Dateien", sorted(lines, key=str.lower)))
    return plan


def files(n):
    return f"{n} {'Datei' if n == 1 else 'Dateien'}"


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".baseline-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_plan(plan, to=None, now=None):
    """Writes a plan: onto the card with a backup of every file it replaces, or into the folder to. Returns the
    report of what it did."""
    if not plan.changes:
        return "Nichts zu ändern.\n"
    stamp = (now or datetime.datetime.now()).strftime("%Y-%m-%d %H-%M-%S")
    report = plan.report()
    if to is not None:
        to = Path(to).resolve()
        if to == plan.root.resolve():
            raise ValueError("Der Ordner ist die Karte selbst: dafür «Auf die Karte» wählen")
        for rel, c in plan.changes.items():
            write_atomic(to / rel, c.data)
        (to / REPORT_NAME).write_text(report, encoding="utf-8")
        return (f"{files(len(plan.changes))} geschrieben in {to}.\nKopiere den Inhalt dieses Ordners auf die Karte "
                f"(Ordner zusammenführen, Dateien ersetzen).\n")
    backup, n = plan.root / BACKUP / f"{stamp} {plan.action}", 2
    while backup.exists():
        backup, n = plan.root / BACKUP / f"{stamp} {plan.action} ({n})", n + 1
    need = sum(len(c.data) for c in plan.changes.values())
    need += sum((plan.root / rel).stat().st_size for rel in plan.changes if (plan.root / rel).exists())
    free = shutil.disk_usage(plan.root).free
    if need + (16 << 20) > free:
        raise ValueError(f"Zu wenig Platz auf der Karte: {need >> 20} MB nötig, {free >> 20} MB frei. "
                         f"Wähle «In einen Ordner».")
    for rel in plan.changes:
        src = plan.root / rel
        if src.exists():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup / rel)
    backup.mkdir(parents=True, exist_ok=True)
    (backup / REPORT_NAME).write_text(report, encoding="utf-8")
    done = 0
    try:
        for rel, c in plan.changes.items():
            write_atomic(plan.root / rel, c.data)
            done += 1
    except OSError as e:
        raise OSError(f"Nach {done} von {len(plan.changes)} Dateien abgebrochen: {e}. Die Sicherung liegt in "
                      f"{backup}: mit «Zurückspielen» holst du den alten Stand.") from e
    return f"{files(done)} auf der Karte geändert. Sicherung: {backup}\n"


# --------------------------------------------------------------------------------------------------------------------
# A small card for the self-test and the tests


def wav_bytes(samples, bits=24, channels=1, rate=44100, chunks=b""):
    if bits == 24:
        raw = b"".join(int(s).to_bytes(4, "little", signed=True)[:3] for s in samples)
    else:
        raw = b"".join(int(s).to_bytes(bits // 8, "little", signed=True) for s in samples)
    fmt = (1).to_bytes(2, "little") + channels.to_bytes(2, "little") + rate.to_bytes(4, "little") + \
        (rate * channels * bits // 8).to_bytes(4, "little") + (channels * bits // 8).to_bytes(2, "little") + \
        bits.to_bytes(2, "little")
    body = b"WAVE" + b"fmt " + len(fmt).to_bytes(4, "little") + fmt + chunks + b"data" + len(raw).to_bytes(4, "little")
    body += raw + (b"\0" if len(raw) & 1 else b"")
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def tone(db, n=2000, bits=24):
    top = 2 ** (bits - 1) - 1
    v = round(top * 10 ** (db / 20))
    return [round(v * math.sin(i * 0.05)) for i in range(n)]


def demo_card(root):
    """A card with what the functions change: a song too loud, rows at 50 with a loud and a quiet sample, a kit and a
    synth preset, a multisample, a wavetable and an audio clip."""
    root = Path(root)
    samples = {"SAMPLES/loud.wav": tone(0), "SAMPLES/quiet.wav": tone(-12), "SAMPLES/lo.wav": tone(-9),
               "SAMPLES/hi.wav": tone(-4), "SAMPLES/table.wav": tone(-10), "SAMPLES/take.wav": tone(-15)}
    for rel, x in samples.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(wav_bytes(x))

    def drum(name, path):
        return (f'<sound name="{name}" mode="subtractive"><osc1 type="sample" fileName="{path}">'
                f'<zone startSamplePos="0" endSamplePos="2000" /></osc1><osc2 type="square" /></sound>')

    def row(i, volume):
        return (f'<noteRow drumIndex="{i}" noteDataWithLift="0x0000000000000060400000"><soundParams volume="{volume}" '
                f'oscAVolume="0x7FFFFFFF" '
                f'oscBVolume="0x80000000" noiseVolume="0x80000000" /></noteRow>')

    song = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<song firmwareVersion="c1.2.1">\n'
        '<instruments>\n'
        f'<kit presetName="Drums"><soundSources>{drum("Kick", "SAMPLES/loud.wav")}{drum("Tick", "SAMPLES/quiet.wav")}'
        '</soundSources></kit>\n'
        '<sound presetName="Keys" mode="subtractive"><osc1 type="sample"><sampleRanges>'
        '<sampleRange rangeTopNote="60" fileName="SAMPLES/lo.wav"><zone startSamplePos="0" endSamplePos="2000" />'
        '</sampleRange><sampleRange fileName="SAMPLES/hi.wav"><zone startSamplePos="0" endSamplePos="2000" />'
        '</sampleRange></sampleRanges></osc1><osc2 type="square" /></sound>\n'
        '<sound presetName="Pad" mode="subtractive"><osc1 type="wavetable" fileName="SAMPLES/table.wav" />'
        '<osc2 type="square" /></sound>\n'
        '<audioTrack name="Take" />\n'
        '</instruments>\n'
        '<songParams volume="0x4CCCCCA8" compressorThreshold="0x40000000" />\n'
        '<sessionClips>\n'
        f'<instrumentClip instrumentPresetName="Drums"><kitParams volume="0x4CCCCCA8" />'
        f'<noteRows>{row(0, "0x7FFFFFFF")}{row(1, "0x7FFFFFFF")}</noteRows></instrumentClip>\n'
        '<instrumentClip instrumentPresetName="Keys"><soundParams volume="0x66666662" oscAVolume="0x7FFFFFFF" '
        'oscBVolume="0x80000000" /></instrumentClip>\n'
        '<audioClip trackName="Take" filePath="SAMPLES/take.wav"><params volume="0xE0000000" /></audioClip>\n'
        '</sessionClips>\n</song>\n')
    (root / "SONGS").mkdir(parents=True, exist_ok=True)
    (root / "SONGS" / "Demo.XML").write_text(song, encoding="utf-8")
    (root / "KITS").mkdir(exist_ok=True)
    (root / "KITS" / "Drums.XML").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<kit><soundSources>'
        '<sound name="Tick" mode="subtractive"><osc1 type="sample" fileName="SAMPLES/quiet.wav" /><osc2 type="square" />'
        '<defaultParams volume="0x4CCCCCA8" oscAVolume="0x7FFFFFFF" oscBVolume="0x80000000" /></sound>'
        '</soundSources></kit>\n', encoding="utf-8")
    return root


# --------------------------------------------------------------------------------------------------------------------
# Self-test (for the build: runs every function on a demo card, and opens and closes the window)


def selftest(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lines, ok = [f"version: v{VERSION}"], True

    def step(name, fn):
        nonlocal ok
        try:
            fn()
            lines.append(f"{name}: ok")
        except Exception as e:  # The self-test reports everything
            ok = False
            lines.append(f"{name}: FAILED {type(e).__name__}: {e}")

    with tempfile.TemporaryDirectory() as tmp:
        card = demo_card(Path(tmp) / "card")

        def check():
            text = bc.report(bc.song_files([str(card)]))
            assert "Song-Lautstärke 40" in text and "Master-Kompressor an" in text, text

        def levels():
            write_plan(plan_levels(card))
            text = bc.report(bc.song_files([str(card)]))
            assert "| Demo | 35 | aus | 35 | 50 | ok |" in text, text

        def normalize():
            plan = plan_normalize(card, -1.0, True)
            write_plan(plan, to=Path(tmp) / "copy")
            assert (Path(tmp) / "copy" / "SAMPLES" / "quiet.wav").exists()
            write_plan(plan_normalize(card, -1.0, True))
            peak = bc.sample_peak(str(card / "SAMPLES" / "quiet.wav"))[0]
            assert abs(20 * math.log10(peak) + 1) < 0.01, peak
            assert bc.sample_peak(str(card / "SAMPLES" / "table.wav"))[0] < 0.4  # A wavetable stays

        def restore():
            backup = next(b for b in (card / BACKUP).iterdir() if b.name.endswith("Pegel"))
            write_plan(plan_restore(backup))  # The state before levels
            text = bc.report(bc.song_files([str(card)]))
            assert "Master-Kompressor an" in text, text

        def window():
            import tkinter as tk
            root = tk.Tk()
            App(root, None, card=str(card))
            root.update()
            root.destroy()

        for name, fn in (("check", check), ("levels", levels), ("normalize", normalize), ("restore", restore),
                         ("window", window)):
            step(name, fn)
    (out / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ok


# --------------------------------------------------------------------------------------------------------------------
# The window

PANEL, PLATE, EDGE, LABEL, SMALL, GOLD, OLED = "#0e0e10", "#18181b", "#26262b", "#d8d8de", "#8c8c96", "#d9b35a", \
    "#07090d"
# The window's icon, drawn by deluge_rec_icon.py --baseline (as deluge_baseline.ico for the .exe)
ICON_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAB20lEQVR42u2bwWrCQBCG9xG0vQhipTRIlVJ7LeTUg8c+QE95gr6GD9RH"
            "8OC9l1Do3WOgVw/bnbBbJiElJm5idvcX/osmYf7PmXGEHSFOeK1WT7HSVmmndFCSA9VBx0ixxuLcl3pIopQO2HCdKPakjfFIac8ftlyu"
            "ZRTdy/n8Ts5mt4MUxUYxUqwlEOQlEg3SPTM3LxYPcjq9kePxtRyNrpwQxUoxU+wMQlZbFtr80dxEVF0yXgWCPDAIx38h6LTPTLpPJlNn"
            "jZdFXlhZZJXlwGveJ/McAu8JVd3+L+19M29UKoeEA0hNw3O55k/pCawxprzx5W9S5/TVvBF5ZFkQCz015U3C52+fZwFriFuhR8d8gPDd"
            "vBF51QB2wsz2NEU1echm81qQ7eu7FHk1/x1E2+7vMgD+awAAAGBpAHIJCAA0AfDyHMmvjzcnRbEDgK0S+Pl8LKguzbq+vvceAAAAEDgA"
            "n4AAAAAAgP1RuM8mBgAAAADDA1DW+vu9oKafn3s9AAAAAHQLwGZAfQMBAAAAAPtN0LaBLoEAAAAAwOUHoa6bKgAAAABcFsCQgQAAABQB"
            "tDom5zKA8jG54A9KBn9UNuzD0sEfl8fCBFZmsDSFtTksTmJ1FsvTwa7P/wKH+aq0pxWiLQAAAABJRU5ErkJggg==")


def settings_path():
    base = os.environ.get("APPDATA") if sys.platform.startswith("win") else None
    return (Path(base) / "DelugeBaseline" if base else Path.home() / ".config" / "deluge_baseline") / "settings.json"


def rendered(text):
    """(line, tag) for the window: Markdown headings, tables with aligned columns, list items with a bullet."""
    out, table = [], []

    def flush():
        if not table:
            return
        rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in table]
        rows = [r for r in rows if not all(set(c) <= set("-: ") and c for c in r)]  # Without the separator row
        widths = [max(len(r[i]) for r in rows if i < len(r)) for i in range(max(len(r) for r in rows))]
        for n, r in enumerate(rows):
            out.append(("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip(), "head" if n == 0 else "row"))
        table.clear()

    for line in text.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("## "):
            out.append((line[3:], "h2"))
        elif line.startswith("# "):
            out.append((line[2:], "h1"))
        elif line.startswith("- "):
            out.append(("• " + line[2:], "item"))
        elif line.startswith("  - "):
            out.append(("    ◦ " + line[4:], "item"))
        else:
            out.append((line, None))
    flush()
    return out


def find_card():
    """A Deluge card among the drives (Windows): the first with a SONGS folder."""
    if not sys.platform.startswith("win"):
        return ""
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        if os.path.isdir(f"{letter}:\\SONGS"):
            return f"{letter}:\\"
    return ""


class App:
    def __init__(self, root, settings, card=None):
        import tkinter as tk
        from tkinter import filedialog, messagebox
        self.tk, self.filedialog, self.messagebox = tk, filedialog, messagebox
        self.root, self.settings = root, settings
        saved = {}
        if settings:
            try:
                saved = json.loads(Path(settings).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
        root.title(f"DelugeBaseline v{VERSION}")
        self.icon = tk.PhotoImage(data=ICON_PNG)
        root.iconphoto(True, self.icon)
        root.configure(bg=PANEL)
        root.minsize(760, 520)
        self.card = tk.StringVar(value=card or saved.get("card") or find_card())
        self.mode = tk.StringVar(value=saved.get("mode", "card"))
        self.out = tk.StringVar(value=saved.get("out", ""))
        self.target = tk.StringVar(value=saved.get("target", bc.num(-1.0)))
        self.compensate = tk.BooleanVar(value=saved.get("compensate", True))
        self.status = tk.StringVar(value="Karte wählen, dann «Prüfen».")
        self.jobs = queue.Queue()
        self.busy = False
        font = ("Segoe UI", 10) if sys.platform.startswith("win") else ("TkDefaultFont", 10)
        mono = ("Consolas", 10) if sys.platform.startswith("win") else ("Courier", 10)

        def label(parent, text, **kw):
            return tk.Label(parent, text=text, bg=PANEL, fg=kw.pop("fg", LABEL), font=font, **kw)

        def button(parent, text, cmd, accent=False):
            return tk.Button(parent, text=text, command=cmd, font=font, bg=GOLD if accent else PLATE,
                             fg=PANEL if accent else LABEL, activebackground=EDGE, activeforeground=LABEL,
                             relief="flat", padx=12, pady=5, highlightthickness=0, bd=0)

        def entry(parent, var):
            return tk.Entry(parent, textvariable=var, font=font, bg=PLATE, fg=LABEL, insertbackground=LABEL,
                            relief="flat", highlightthickness=1, highlightbackground=EDGE, highlightcolor=GOLD)

        pad = {"padx": 10, "pady": 4}
        top = tk.Frame(root, bg=PANEL)
        top.pack(fill="x", **pad)
        label(top, "Karte").grid(row=0, column=0, sticky="w")
        entry(top, self.card).grid(row=0, column=1, sticky="ew", padx=6)
        button(top, "Wählen …", self.choose_card).grid(row=0, column=2)
        label(top, "Schreiben").grid(row=1, column=0, sticky="w", pady=(8, 0))
        modes = tk.Frame(top, bg=PANEL)
        modes.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0))
        for value, text in (("card", "Auf die Karte, alte Dateien in BASELINE-BACKUP"), ("folder", "In einen Ordner")):
            tk.Radiobutton(modes, text=text, value=value, variable=self.mode, font=font, bg=PANEL, fg=LABEL,
                           selectcolor=PLATE, activebackground=PANEL, activeforeground=LABEL, highlightthickness=0,
                           bd=0).pack(side="left", padx=(0, 16))
        label(top, "Ordner").grid(row=2, column=0, sticky="w")
        entry(top, self.out).grid(row=2, column=1, sticky="ew", padx=6)
        button(top, "Wählen …", self.choose_out).grid(row=2, column=2)
        top.columnconfigure(1, weight=1)

        norm = tk.Frame(root, bg=PANEL)
        norm.pack(fill="x", **pad)
        label(norm, "Samples: Ziel").pack(side="left")
        menu = tk.OptionMenu(norm, self.target, *[bc.num(t) for t in TARGETS])
        menu.configure(font=font, bg=PLATE, fg=LABEL, activebackground=EDGE, activeforeground=LABEL, relief="flat",
                       highlightthickness=0, bd=0, width=5)
        menu["menu"].configure(font=font, bg=PLATE, fg=LABEL, activebackground=GOLD, activeforeground=PANEL)
        menu.pack(side="left", padx=6)
        label(norm, "dBFS").pack(side="left")
        tk.Checkbutton(norm, text="Songs, Kits und Synths ausgleichen (Mix bleibt gleich)", variable=self.compensate,
                       font=font, bg=PANEL, fg=LABEL, selectcolor=PLATE, activebackground=PANEL,
                       activeforeground=LABEL, highlightthickness=0, bd=0).pack(side="left", padx=12)

        actions = tk.Frame(root, bg=PANEL)
        actions.pack(fill="x", **pad)
        self.buttons = [button(actions, "Prüfen", self.check, accent=True),
                        button(actions, "Pegel anwenden", self.levels),
                        button(actions, "Samples normalisieren", self.normalize),
                        button(actions, "Sicherung zurückspielen", self.restore)]
        for b in self.buttons:
            b.pack(side="left", padx=(0, 8))

        label(root, "", textvariable=self.status, fg=SMALL, anchor="w").pack(side="bottom", fill="x", padx=10, pady=6)
        body = tk.Frame(root, bg=EDGE)
        body.pack(fill="both", expand=True, padx=10, pady=(4, 0))
        self.text = tk.Text(body, font=mono, bg=OLED, fg=LABEL, insertbackground=LABEL, relief="flat", wrap="word",
                            padx=10, pady=8, highlightthickness=0, spacing1=1, spacing3=1)
        size = mono[1]
        self.text.tag_configure("h1", font=(mono[0], size + 3, "bold"), foreground=GOLD, spacing3=6)
        self.text.tag_configure("h2", font=(mono[0], size + 1, "bold"), foreground=LABEL, spacing1=8, spacing3=4)
        self.text.tag_configure("head", font=(mono[0], size, "bold"), foreground=SMALL)
        self.text.tag_configure("row", lmargin1=0, lmargin2=0)
        self.text.tag_configure("item", lmargin1=0, lmargin2=18)
        scroll = tk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.show("DelugeBaseline prüft die Songs einer Deluge-Karte gegen die Baseline Master und setzt sie darauf.\n\n"
                  "- Prüfen: liest nur.\n"
                  "- Pegel anwenden: Song, Kits und Audio-Spuren höchstens 35, Synths und Reihen höchstens wie 40 mit "
                  "einem voll ausgesteuerten Sample, Master-Kompressor aus.\n"
                  "- Samples normalisieren: hebt leise Samples bis zum Ziel an, nie darüber. Mit Ausgleich klingen die "
                  "Songs danach gleich.\n"
                  "- Vor dem Schreiben zeigt es immer, was es ändern würde, und fragt.\n")
        root.after(100, self.poll)

    # --- plumbing: work in a thread, results through a queue
    def run(self, work, then, what):
        if self.busy:
            return
        self.busy = True
        self.status.set(what + " …")
        for b in self.buttons:
            b.configure(state="disabled")

        def worker():
            try:
                self.jobs.put((then, work(), None))
            except Exception as e:  # Shown in the window
                self.jobs.put((then, None, e))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                item = self.jobs.get_nowait()
                if item[0] == "status":
                    self.status.set(item[1])
                    continue
                then, result, error = item
                self.busy = False
                for b in self.buttons:
                    b.configure(state="normal")
                if error is not None:
                    self.status.set("Fehler")
                    self.show(f"Fehler: {error}\n")
                else:
                    then(result)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def progress(self, text):
        self.jobs.put(("status", text))

    def show(self, text, keep=False):
        """The report, its Markdown drawn: headings, tables as aligned columns, list items."""
        self.text.configure(state="normal")
        if not keep:
            self.text.delete("1.0", "end")
        for line, tag in rendered(text):
            self.text.insert("end", line + "\n", tag)
        self.text.configure(state="disabled")

    def save(self):
        if not self.settings:
            return
        try:
            Path(self.settings).parent.mkdir(parents=True, exist_ok=True)
            Path(self.settings).write_text(json.dumps({
                "card": self.card.get(), "mode": self.mode.get(), "out": self.out.get(), "target": self.target.get(),
                "compensate": self.compensate.get()}), encoding="utf-8")
        except OSError:
            pass

    def root_or_warn(self):
        try:
            root = card_root(self.card.get())
        except (OSError, ValueError) as e:
            self.messagebox.showwarning("DelugeBaseline", str(e))
            return None
        self.save()
        return root

    # --- the buttons
    def choose_card(self):
        path = self.filedialog.askdirectory(title="Deluge-Karte (oder ihr Ordner SONGS)",
                                            initialdir=self.card.get() or None)
        if path:
            self.card.set(path)

    def choose_out(self):
        path = self.filedialog.askdirectory(title="Ordner für die geänderten Dateien", initialdir=self.out.get() or None)
        if path:
            self.out.set(path)
            self.mode.set("folder")

    def check(self):
        root = self.root_or_warn()
        if root:
            self.run(lambda: bc.report(bc.song_files([str(root)])), self.done_check, "Prüfe")

    def done_check(self, text):
        self.show(text)
        self.status.set("Geprüft. Die Karte ist unverändert.")

    def levels(self):
        root = self.root_or_warn()
        if root:
            self.run(lambda: plan_levels(root), self.confirm, "Suche, was sich ändert")

    def normalize(self):
        root = self.root_or_warn()
        if root:
            target = float(self.target.get().replace(",", "."))
            self.run(lambda: plan_normalize(root, target, self.compensate.get(), self.progress), self.confirm,
                     "Lese die Samples")

    def restore(self):
        root = self.root_or_warn()
        if not root:
            return
        path = self.filedialog.askdirectory(title="Welche Sicherung?", initialdir=str(root / BACKUP)
                                            if (root / BACKUP).is_dir() else str(root))
        if path:
            self.run(lambda: plan_restore(path), self.confirm, "Lese die Sicherung")

    def confirm(self, plan):
        self.show(plan.report())
        if not plan.changes:
            self.status.set("Nichts zu ändern.")
            return
        to = None
        if self.mode.get() == "folder" and plan.action != "Zurückspielen":
            if not self.out.get():
                self.messagebox.showwarning("DelugeBaseline", "Erst einen Ordner wählen.")
                return
            to = self.out.get()
        where = f"in den Ordner {to}" if to else "auf die Karte (die alten Dateien kommen nach BASELINE-BACKUP)"
        self.status.set(f"{files(len(plan.changes))} würden sich ändern.")
        if self.messagebox.askyesno("DelugeBaseline", f"{plan.title}\n\n{files(len(plan.changes))} {where} "
                                                      f"schreiben?"):
            self.run(lambda: write_plan(plan, to), self.done_write, "Schreibe")
        else:
            self.status.set("Nichts geschrieben.")

    def done_write(self, text):
        self.show("# Geschrieben\n\n" + text + "\n", keep=True)
        self.text.see("end")
        self.status.set(text.splitlines()[0])


# --------------------------------------------------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(prog="DelugeBaseline", description="Deluge-Songs und -Samples auf die Baseline")
    ap.add_argument("--version", action="version", version=f"DelugeBaseline v{VERSION}")
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)  # The self-test's folder
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("check", help="prüfen (liest nur)")
    p.add_argument("card")
    p.add_argument("--out", dest="report")
    for name, text in (("levels", "Pegel der Songs auf die Baseline"), ("normalize", "Samples normalisieren")):
        p = sub.add_parser(name, help=text)
        p.add_argument("card")
        p.add_argument("--to", help="in diesen Ordner statt auf die Karte")
        p.add_argument("--yes", action="store_true", help="wirklich schreiben (sonst nur zeigen)")
        if name == "normalize":
            p.add_argument("--target", type=float, default=-1.0, help="Ziel in dBFS, höchstens 0 (Standard -1)")
            p.add_argument("--no-compensate", action="store_true", help="Songs, Kits und Synths nicht ausgleichen")
    p = sub.add_parser("restore", help="eine Sicherung zurückspielen")
    p.add_argument("backup")
    p.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return 0 if selftest(args.out or ".") else 1
    if args.cmd is None:
        if sys.platform.startswith("win"):
            try:  # Sharp text on scaled Windows displays
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        import tkinter as tk
        root = tk.Tk()
        App(root, settings_path())
        root.mainloop()
        return 0
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.cmd == "check":
        text = bc.report(bc.song_files([str(card_root(args.card))]))
        print(text, end="")
        if args.report:
            Path(args.report).write_text(text, encoding="utf-8")
        return 0
    if args.cmd == "levels":
        plan = plan_levels(card_root(args.card))
    elif args.cmd == "normalize":
        plan = plan_normalize(card_root(args.card), args.target, not args.no_compensate,
                              lambda s: print(s, end="\r", file=sys.stderr))
    else:
        plan = plan_restore(args.backup)
    print(plan.report(), end="")
    if not args.yes or not plan.changes:
        if plan.changes:
            print("\nNur gezeigt, nichts geschrieben: mit --yes schreiben.")
        return 0
    print(write_plan(plan, getattr(args, "to", None)), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
