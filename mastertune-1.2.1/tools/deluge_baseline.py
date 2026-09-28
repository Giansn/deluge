#!/usr/bin/env python3
"""DelugeBaseline: checks the songs of a Deluge card against the baseline master and sets them and the samples to it
(geraet/analyse/2026-09-27-baseline-master.md). Built into DelugeBaseline-vN.exe for Windows by
.github/workflows/deluge-baseline-windows.yml; runs as a script anywhere.

Its window (in DelugeRec's look): a function, PEGEL or NORM, in a mode, LESEN (only reads and shows) or ANPASSEN
(shows what it would change, and writes on the second START), written onto the SD card directly or into a copy folder
(boxes to tick). ZURÜCK restores a backup, BERICHT opens the whole report as text. German or English: the switch in
the window, --lang en on the command line. What it does, in its window or on the command line:
- Pegel lesen (check): the report of baseline_check.py. Only reads.
- Pegel anpassen (levels): sets the songs to the baseline. The song's volume above 35 down to 35, the master
  compressor off, kits and audio tracks above 35 down to 35, synths and kit rows down to where they are as loud as a
  full-scale sample at 40 (their sample's peak within its zone and their oscillator's level counted, as
  baseline_check.py judges them). Automation keeps its shape: all its values scale alike. What distorts on purpose
  (SATURATION, compressors of tracks, analog delay, filters) stays: that's sound, not level. The report lists it.
- NORM (normalize; lesen shows it, anpassen does it): raises every sample under SAMPLES/ whose peak is below the
  target (-1 dBFS unless chosen otherwise) to the target, never above: no peak is cut, no sample clips. Samples at
  the target or above stay as they are. The format stays (bits, float, channels, every chunk), only the audio
  changes. The files an oscillator plays together (the ranges of a multisample) get one gain, the one of the
  loudest, so they keep their balance.
  Ausgleichen (compensate, on unless switched off): every oscillator that plays a raised sample gets its level (osc A
  or B volume) lowered by just as much, in every clip of every song and in the kits and synths of KITS/ and SYNTHS/.
  The voice gets the same signal as before, before its filters and effects, so the songs sound as before, only with
  the knobs meaning the same everywhere. A sample whose every use can't be compensated that way stays as it is: an
  oscillator level that isn't saved or has a patch cable to it, FM, a format from before 2017, a copy in a song's own
  folder (Collect media) the firmware may play instead. Never changed: wavetables and audio clips.
- Zurück (restore): puts back the files of a backup.

Where it writes: onto the card, keeping a copy of every file it changes in BASELINE-BACKUP/<date> <time> <function>/
on the card first (not under SONGS, so the Deluge doesn't list them), or into a folder of its own: only the changed
files, in the card's layout, to copy onto the card. It never writes anything before showing what it would do.

Firmware (mastertune v17 on release_1_2_1): see baseline_check.py. The oscillator levels LOCAL_OSC_A/B_VOLUME are
volume params like the rest (getFinalParameterValueVolume(): the gain goes with the knob squared) and scale the sample
in Voice::render() before anything else happens to it.

Usage (Windows: py instead of python3; without arguments it opens its window):
  python3 deluge_baseline.py [--lang en] check CARD [--out REPORT.md]
  python3 deluge_baseline.py [--lang en] levels CARD [--to FOLDER] [--yes]
  python3 deluge_baseline.py [--lang en] normalize CARD [--target DB] [--no-compensate] [--to FOLDER] [--yes]
  python3 deluge_baseline.py [--lang en] restore BACKUP [--yes]
  Without --yes it only shows what it would do. CARD: the card's root (with SONGS in it) or its SONGS folder.
Needs numpy to normalize (pip install numpy); everything else only Python 3.8.

Versions:
  1  the first build: levels and normalize, read or apply, onto the SD card or into a copy folder, German and English
  2  the display's text smaller: 28 characters on 5 lines instead of 21 on 4
"""
import argparse
import datetime
import json
import math
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_check as bc  # noqa: E402

try:
    import numpy as np
except ImportError:  # Only normalizing needs it: checked there
    np = None

VERSION = 2
AUDIO_EXTENSIONS = (".wav", ".aif", ".aiff")
XML_FOLDERS = ("SONGS", "KITS", "SYNTHS")
# Why a sample stays as it is. Wavetables and audio clips always; the other reasons only when it compensates
REASONS = {"wavetable": ("Wavetable", "wavetable"), "audio": ("Audio-Clip", "audio clip"),
           "fm": ("FM oder Ringmod", "FM or ring mod"),
           "format": ("anderes Format, zum Beispiel von 2016", "another format, from 2016 for example")}
ALWAYS_KEPT = ("wavetable", "audio")
ACTIONS = {"levels": ("Pegel", "Levels"), "normalize": ("Normalisieren", "Normalize"),
           "restore": ("Zurückspielen", "Restore")}
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
            raise ValueError(f"{self.rel}: " + bc.t("zwei Änderungen an derselben Stelle", "two changes at one place"))
        self.edits[span] = new

    def result(self):
        out, last = [], 0
        for (start, end), new in sorted(self.edits.items()):
            if start < last:
                raise ValueError(f"{self.rel}: " + bc.t("Änderungen überlappen", "changes overlap"))
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
    raise ValueError(f"{path}: " + bc.t("kein Ordner SONGS darin, das ist keine Deluge-Karte",
                                        "no SONGS folder in it, that's no Deluge card"))


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
    """A file a plan writes: its bytes, or for samples and restored files how to get them when it writes (so that
    not all of them are in memory at once), with their size."""

    def __init__(self, rel, data, lines, size=None):
        self.rel, self.lines, self.make = rel, lines, data
        self.size = len(data) if isinstance(data, (bytes, bytearray)) else size

    @property
    def data(self):
        return self.make() if callable(self.make) else self.make


class Plan:
    """What a function would write: files by their path on the card, and its report."""

    def __init__(self, root, kind, title):
        self.root, self.kind, self.title = Path(root), kind, title  # kind: levels, normalize or restore
        self.changes = {}  # rel -> Change
        self.sections = []  # (heading, lines)
        self.summary = []

    @property
    def action(self):
        return bc.t(*ACTIONS[self.kind])

    def add(self, rel, data, lines, size=None):
        self.changes[rel] = Change(rel, data, lines, size)

    def report(self):
        out = [f"# {self.title}", ""] + [f"- {s}" for s in self.summary]
        for heading, lines in self.sections:
            out += ["", f"## {heading}", ""] + [f"- {line}" for line in lines]
        if not self.changes:
            out += ["", bc.t("Nichts zu ändern.", "Nothing to change.")]
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
    edit.lines.append(f"{label}{where}: {bc.num(old)} " + bc.t("auf", "to") +
                      f" {bc.num(new)}, {bc.signed_db(bc.db_between(old, new))}" + suffix)


def sound_top(sound, params, card, label, edit):
    """The highest volume a synth or row may have (a param value), or None if its samples can't be read."""
    samples, other = bc.sources(sound, params)
    if other or not samples:
        return bc.SOUND_LIMIT
    level, problems = bc.sample_level(samples, card)
    if problems:
        line = f"{label}: " + bc.t("nicht geändert", "not changed") + f" ({'; '.join(problems)})"
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
    plan = Plan(root, "levels", bc.t("Pegel auf die Baseline", "Levels to the baseline"))
    card = bc.Card(str(root))
    songs = xml_files(root, ("SONGS",))
    changed = unreadable = 0
    for rel, path in songs:
        try:
            text, tree = read_xml(path)
        except (OSError, ValueError) as e:
            plan.sections.append((rel, [bc.t("nicht lesbar: ", "unreadable: ") + str(e)]))
            unreadable += 1
            continue
        song = tree.child("song")
        if song is None:
            continue
        edit = XmlEdit(rel, text)
        params = song.child("songParams")
        limit(edit, params, "volume", bc.SONG_KIT_LIMIT, bc.t("Song-Lautstärke", "Song volume"), None)
        comp = bc.loudest(params, "compressorThreshold") if params is not None else None
        span = params.span("compressorThreshold") if params is not None else None
        if comp is not None and comp > 0 and span is not None:
            edit.replace(span, "0x00000000")
            was = round(bc.unipolar_knob(comp))
            edit.lines.append(bc.t(f"Master-Kompressor aus (Threshold war {was})",
                                   f"Master compressor off (threshold was {was})"))
        instruments = song.child("instruments")
        kits, synths = {}, {}
        for inst in instruments.children if instruments is not None else []:
            if inst.name == "kit":
                kits[bc.instrument_key(inst, "preset", "kit")] = inst
            elif inst.name == "sound":
                synths[bc.instrument_key(inst, "preset", "synth")] = inst
        number = {}
        for clip in song.iter():
            if clip.name == "audioClip":
                track = bc.readable(clip.get("trackName") or "?")
                key = ("audio", track, "")
            elif clip.name == "instrumentClip":
                track = bc.readable(bc.track_key(clip, "instrumentPreset"))
                key = bc.instrument_key(clip, "instrumentPreset",
                                        "kit" if clip.child("kitParams") is not None else "synth")
            else:
                continue
            number[key] = number.get(key, 0) + 1
            name = clip_name(clip, number[key])
            if clip.name == "audioClip":
                limit(edit, clip.child("params"), "volume", bc.SONG_KIT_LIMIT,
                      bc.t("Audio-Spur", "Audio track") + f" «{track}»", name)
            elif clip.child("kitParams") is not None:
                label = f"Kit «{track}»"
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
                    row_label = bc.t("Reihe", "Row") + f" «{bc.readable(drum.get('name'))}» in {label}"
                    top = sound_top(drum, [sp], card, row_label, edit)
                    if top is not None:
                        limit(edit, sp, "volume", top, row_label, name,
                              "" if bc.has_notes(nr) else bc.t(", ohne Noten", ", no notes"))
            elif clip.child("soundParams") is not None:
                label = f"Synth «{track}»"
                sp = clip.child("soundParams")
                top = sound_top(synths.get(key), [sp], card, label, edit)
                if top is not None:
                    limit(edit, sp, "volume", top, label, name)
        if edit.edits:
            plan.add(rel, edit.result(), edit.lines)
            changed += 1
        if edit.lines:
            plan.sections.append((rel, edit.lines))
    n = len(songs)
    plan.summary.append(bc.t(f"{n} {'Song' if n == 1 else 'Songs'} gelesen, {changed} zu ändern",
                             f"{n} {'song' if n == 1 else 'songs'} read, {changed} to change")
                        + (bc.t(f", {unreadable} nicht lesbar", f", {unreadable} unreadable") if unreadable else ""))
    plan.summary.append(bc.t("Nicht angefasst: SATURATION, Kompressoren der Spuren, Analog-Delay und Filter. Das ist "
                             "Klang, kein Pegel. «Pegel lesen» listet sie.",
                             "Not touched: SATURATION, compressors of tracks, analog delay and filters. That's sound, "
                             "not level. «Levels, read» lists them."))
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
            self.problem = bc.t("Osc-Pegel nirgends gespeichert", "osc level saved nowhere")
        for p in params:
            if not bc.param_values(p.get(self.level)) or p.span(self.level) is None:
                self.problem = bc.t("Osc-Pegel nicht gespeichert", "osc level not saved")
        cables = [c for n in [sound] + params for c in n.iter() if c.name == "patchCable"]
        if any(c.get("destination") == self.level for c in cables):
            self.problem = bc.t("Kabel auf den Osc-Pegel", "patch cable to the osc level")


def sound_refs(rel, sound, params, label, other, handled):
    """The sample oscillators of a sound; its other uses of files go to other (file value -> {REASONS keys}). The
    nodes that name its files go to handled."""
    refs = []
    defaults = sound.child("defaultParams")
    params = [p for p in params if p is not None] + ([defaults] if defaults is not None else [])
    for osc in LEVELS:
        node = sound.child(osc)
        if node is None:
            continue
        ranges = [r for g in node.children if g.name in ("sampleRanges", "wavetableRanges") for r in g.children]
        holders = [h for h in [node] + ranges if h.get("fileName")]
        if not holders:
            continue
        handled.update(id(h) for h in holders)
        if node.get("type") == "wavetable":
            reason = "wavetable"
        elif (sound.get("mode") or "subtractive") != "subtractive":
            reason = "fm"
        else:
            refs.append(OscRef(rel, sound, osc, params, holders, label))
            continue
        for h in holders:
            other.setdefault(h.get("fileName"), set()).add(reason)
    return refs


def xml_refs(rel, tree):
    """(sample oscillators, {file value: {REASONS keys}} for the other files it names) of one XML of the card. What it
    doesn't know (formats before 2017 among them) goes to the other files: a sample it plays isn't raised if the
    songs are to sound as before."""
    refs, other, handled = [], {}, set()
    top = next((c for c in tree.children if c.name in ("song", "kit", "sound", "synth")), None)
    if top is None:
        pass
    elif top.name == "song":
        clips = [c for c in top.iter() if c.name == "instrumentClip"]
        instruments = top.child("instruments")
        for inst in instruments.children if instruments is not None else []:
            kind = {"sound": "synth", "kit": "kit"}.get(inst.name)
            if kind is None:
                continue
            key, name = bc.instrument_key(inst, "preset", kind), bc.readable(bc.track_key(inst, "preset"))
            mine = [c for c in clips if bc.instrument_key(
                c, "instrumentPreset", "kit" if c.child("kitParams") is not None else "synth") == key]
            if inst.name == "sound":
                params = [c.child("soundParams") for c in mine]
                refs += sound_refs(rel, inst, params, f"Synth «{name}»", other, handled)
            elif inst.name == "kit":
                sources = inst.child("soundSources")
                for i, drum in enumerate(sources.children if sources is not None else []):
                    if drum.name != "sound":
                        continue
                    params = [nr.child("soundParams") for c in mine if c.child("kitParams") is not None
                              for nr in (c.child("noteRows").children if c.child("noteRows") is not None else [])
                              if nr.get("drumIndex") == str(i)]
                    label = bc.t("Reihe", "Row") + f" «{bc.readable(drum.get('name'))}» in Kit «{name}»"
                    refs += sound_refs(rel, drum, params, label, other, handled)
        for c in top.iter():
            if c.name == "audioClip" and c.get("filePath"):
                other.setdefault(c.get("filePath"), set()).add("audio")
    elif top.name == "kit":
        sources = top.child("soundSources")
        for drum in sources.children if sources is not None else []:
            if drum.name == "sound":
                refs += sound_refs(rel, drum, [], bc.t("Reihe", "Row") + f" «{bc.readable(drum.get('name'))}» "
                                   + bc.t("im Kit", "in the kit"), other, handled)
    else:  # A synth: <sound>, or <synth> in old files
        refs += sound_refs(rel, top, [], "Synth", other, handled)
    for node in tree.iter():  # Every other file it names, as attribute or as tag
        for key in ("fileName", "filePath"):
            v = node.attrs.get(key)
            if v and v.lower().endswith(AUDIO_EXTENSIONS) and id(node) not in handled:
                other.setdefault(v, set()).add("format")
        v = (node.text or "").strip()
        if node.name in ("fileName", "filePath") and v.lower().endswith(AUDIO_EXTENSIONS) and \
                id(node.parent) not in handled:
            other.setdefault(v, set()).add("format")
    return refs, other


def alternate_copies(rel, value, card):
    """Copies of a sample the firmware may play instead of it (AudioFileManager::getAudioFileFromFilename()): once
    a file of a song is missing and the song has a folder of its own (SONGS/<song>/, where «Collect media» puts its
    files; KITS/<kit>/ and SYNTHS/<synth>/ alike), every file after is looked for there first, by its path with _
    for / and by its name."""
    folder = rel[:-4] if rel.lower().endswith(".xml") else rel
    names = [value[8:].replace("/", "_")] if value[:8].upper() == "SAMPLES/" else []
    names.append(value.rsplit("/", 1)[-1])
    return {f for f in (card.find(f"{folder}/{n}") for n in names) if f}


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
            raise ValueError(f"{path}: " + bc.t("würde clippen", "would clip"))  # Can't happen with the cap above
    data[f["pos"]:f["pos"] + f["size"]] = encode(y, f)
    return bytes(data), gain


def raised_by(path, gain):
    """The file raised by exactly gain, when it is written: the oscillators that play it are compensated for it."""
    data, g = raised(path, gain)
    if abs(g - gain) > 1e-9 * gain:  # Can't happen: its target is below full scale (unless it changed since)
        raise ValueError(f"{path}: " + bc.t("würde clippen, nicht geschrieben", "would clip, not written"))
    return data


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
        raise RuntimeError(bc.t("Normalisieren braucht numpy", "Normalizing needs numpy") + ": pip install numpy")
    if target_db > 0:
        raise ValueError(bc.t("Das Ziel muss bei 0 dBFS oder darunter liegen", "The target must be 0 dBFS or below"))
    root = Path(root)
    title = bc.t(f"Samples normalisieren: Ziel {bc.num(target_db)} dBFS, ",
                 f"Normalize samples: target {bc.num(target_db)} dBFS, ") + (
        bc.t("Songs, Kits und Synths ausgeglichen", "songs, kits and synths compensated") if compensate
        else bc.t("ohne Ausgleich", "without compensation"))
    plan = Plan(root, "normalize", title)
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
            plan.sections.append((rel, [bc.t("nicht lesbar, seine Samples bleiben wie sie sind: ",
                                             "unreadable, its samples stay as they are: ") + str(e)]))
            texts[rel] = None
            continue
        r, o = xml_refs(rel, texts[rel][1])
        refs += r
        for value, reasons in o.items():
            found = card.find(value)
            if found:
                other.setdefault(found, set()).update(reasons)
    unreadable_xml = [rel for rel, t in texts.items() if t is None]

    groups, uses = Groups(), {}
    for r in refs:
        r.files = [card.find(h.get("fileName")) for h in r.holders]
        copies = {c for h, f in zip(r.holders, r.files) for c in alternate_copies(r.rel, h.get("fileName"), card)
                  if c != f}
        if copies and not r.problem:
            r.problem = "spielt vielleicht die Kopie " + bc.readable(
                Path(min(copies)).relative_to(root).as_posix())
        keys = [f or "missing:" + h.get("fileName") for f, h in zip(r.files, r.holders)]
        groups.union(keys)
        for k in keys:
            uses.setdefault(k, []).append(r)

    # Each sample's own gain: to the target, only up
    target = 10 ** (target_db / 20)
    skipped, loud, gains = {}, 0, {}
    for i, path in enumerate(sample_files):
        if progress:
            progress(bc.t("Lese Samples", "Reading samples") + f" {i + 1}/{len(sample_files)}")
        try:
            with open(path, "rb") as fh:
                f = bc.audio_format(fh)
            if isinstance(f, str):
                skipped[path] = f
                continue
            if is_wavetable_file(path):
                skipped[path] = bc.t(*REASONS["wavetable"])
                continue
            peak, error = bc.sample_peak(path)
        except (OSError, ValueError) as e:
            skipped[path] = bc.t("nicht lesbar", "unreadable") + f" ({e})"
            continue
        kept = sorted(other.get(path, set()) & set(ALWAYS_KEPT))
        if kept or (compensate and path in other):  # Wavetables and audio clips never, the rest if it can't be matched
            skipped[path] = ", ".join(bc.t(*REASONS[r]) for r in (kept or sorted(other[path])))
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
            skipped[path] = bc.t("ein XML der Karte ist nicht lesbar", "an XML of the card is unreadable")

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
                    skipped[k] = bc.t("Multisample mit einem Sample, das bleibt",
                                      "multisample with a sample that stays")
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
    for path, g in sorted(gains.items(), key=lambda kv: kv[0].lower()):
        applied[path] = g
        rel = Path(path).relative_to(root).as_posix()
        line = f"{bc.readable(rel)}: {bc.signed_db(20 * math.log10(g))}"
        plan.add(rel, lambda path=path, g=g: raised_by(path, g), [line], os.path.getsize(path))
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
            plan.sections.append((bc.t("Ausgeglichen", "Compensated") + f": {rel}", edit.lines))
    if applied:
        db = [20 * math.log10(g) for g in applied.values()]
        n = len(applied)
        plan.summary.append(bc.t(f"{n} {'Sample' if n == 1 else 'Samples'} angehoben, um "
                                 f"{bc.num(min(db))} bis {bc.num(max(db))} dB",
                                 f"{n} {'sample' if n == 1 else 'samples'} raised, by "
                                 f"{bc.num(min(db))} to {bc.num(max(db))} dB"))
    plan.summary.append(bc.t(f"{loud} {'Sample' if loud == 1 else 'Samples'} schon laut genug (höchstens "
                             f"{bc.num(RAISE_FROM_DB)} dB unter dem Ziel)",
                             f"{loud} {'sample' if loud == 1 else 'samples'} loud enough already (at most "
                             f"{bc.num(RAISE_FROM_DB)} dB below the target)"))
    if skipped:
        one = len(skipped) == 1
        plan.summary.append(bc.t(f"{len(skipped)} {'Sample bleibt' if one else 'Samples bleiben'} wie "
                                 f"{'es ist' if one else 'sie sind'}, siehe unten",
                                 f"{len(skipped)} {'sample stays as it is' if one else 'samples stay as they are'}, "
                                 f"see below"))
    if compensate:
        n, k = sum(compensated.values()), len(compensated)
        plan.summary.append(bc.t(f"Ausgeglichen: {n} {'Oszillator' if n == 1 else 'Oszillatoren'} in {files(k)} "
                                 f"(Songs, Kits, Synths). Die Songs klingen wie vorher.",
                                 f"Compensated: {n} {'oscillator' if n == 1 else 'oscillators'} in {files(k)} "
                                 f"(songs, kits, synths). The songs sound as before."))
    else:
        plan.summary.append(bc.t("Ohne Ausgleich: Songs, die angehobene Samples spielen, werden an diesen Stellen "
                                 "lauter.", "Without compensation: songs that play raised samples get louder there."))
    if lines:
        plan.sections.insert(0, (bc.t("Angehoben", "Raised"), lines))
    if skipped:
        plan.sections.append((bc.t("Bleiben wie sie sind", "Stay as they are"), [
            f"{bc.readable(Path(p).relative_to(root).as_posix())}: {why}"
            for p, why in sorted(skipped.items(), key=lambda kv: kv[0].lower())]))
    return plan


# --------------------------------------------------------------------------------------------------------------------
# Zurückspielen, and writing


def plan_restore(backup):
    backup = Path(backup).resolve()
    if backup.parent.name != BACKUP:
        raise ValueError(f"{backup}: " + bc.t(f"keine Sicherung (die liegen in {BACKUP}/ auf der Karte)",
                                              f"no backup (they are in {BACKUP}/ on the card)"))
    root = backup.parent.parent
    plan = Plan(root, "restore", bc.t("Sicherung zurückspielen: ", "Restore backup: ") + backup.name)
    lines = []
    for d, _, names in os.walk(backup):
        for n in names:
            full = Path(d) / n
            rel = full.relative_to(backup).as_posix()
            if rel == REPORT_NAME:
                continue
            plan.add(rel, full.read_bytes, [bc.readable(rel)], full.stat().st_size)
            lines.append(bc.readable(rel))
    plan.summary.append(files(len(lines)) + bc.t(" zurück auf die Karte", " back onto the card"))
    plan.sections.append((bc.t("Dateien", "Files"), sorted(lines, key=str.lower)))
    return plan


def files(n):
    return f"{n} " + (bc.t("Datei", "file") if n == 1 else bc.t("Dateien", "files"))


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


def write_plan(plan, to=None, now=None, progress=None):
    """Writes a plan: onto the card with a backup of every file it replaces, or into the folder to. Returns the
    report of what it did."""
    if not plan.changes:
        return bc.t("Nichts zu ändern.", "Nothing to change.") + "\n"
    stamp = (now or datetime.datetime.now()).strftime("%Y-%m-%d %H-%M-%S")
    report = plan.report()
    n = len(plan.changes)
    if to is not None:
        to = Path(to).resolve()
        if to == plan.root.resolve():
            raise ValueError(bc.t("Der Ordner ist die Karte selbst: dafür «SD-Karte direkt» wählen",
                                  "The folder is the card itself: choose «SD card directly» for that"))
        for i, (rel, c) in enumerate(plan.changes.items()):
            if progress:
                progress(bc.t("Datei", "File") + f" {i + 1}/{n}")
            write_atomic(to / rel, c.data)
        (to / REPORT_NAME).write_text(report, encoding="utf-8")
        return bc.t(f"{files(n)} geschrieben in {to}.\nKopiere den Inhalt dieses Ordners auf die Karte (Ordner "
                    f"zusammenführen, Dateien ersetzen).\n",
                    f"{files(n)} written to {to}.\nCopy the contents of this folder onto the card (merge folders, "
                    f"replace files).\n")
    backup, k = plan.root / BACKUP / f"{stamp} {plan.action}", 2
    while backup.exists():
        backup, k = plan.root / BACKUP / f"{stamp} {plan.action} ({k})", k + 1
    need = sum(c.size for c in plan.changes.values())
    need += sum((plan.root / rel).stat().st_size for rel in plan.changes if (plan.root / rel).exists())
    free = shutil.disk_usage(plan.root).free
    if need + (16 << 20) > free:
        raise ValueError(bc.t(f"Zu wenig Platz auf der Karte: {need >> 20} MB nötig, {free >> 20} MB frei. "
                              f"Wähle «Kopie-Ordner».",
                              f"Not enough space on the card: {need >> 20} MB needed, {free >> 20} MB free. "
                              f"Choose «Copy folder»."))
    for i, rel in enumerate(plan.changes):
        if progress:
            progress(bc.t("Sicherung", "Backup") + f" {i + 1}/{n}")
        src = plan.root / rel
        if src.exists():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup / rel)
    backup.mkdir(parents=True, exist_ok=True)
    (backup / REPORT_NAME).write_text(report, encoding="utf-8")
    done = 0
    try:
        for rel, c in plan.changes.items():
            if progress:
                progress(bc.t("Datei", "File") + f" {done + 1}/{n}")
            write_atomic(plan.root / rel, c.data)
            done += 1
    except (OSError, ValueError) as e:
        raise OSError(bc.t(f"Nach {done} von {n} Dateien abgebrochen: {e}. Die Sicherung liegt in {backup}: mit "
                           f"«Zurück» holst du den alten Stand.",
                           f"Stopped after {done} of {n} files: {e}. The backup is in {backup}: «Restore» brings "
                           f"back the old state.")) from e
    return bc.t(f"{files(done)} auf der Karte geändert. Sicherung: {backup}\n",
                f"{files(done)} changed on the card. Backup: {backup}\n")


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
    (root / "SONGS" / "Demo.XML").write_bytes(song.encode("utf-8"))  # "\n" as the Deluge writes, on Windows too
    (root / "KITS").mkdir(exist_ok=True)
    (root / "KITS" / "Drums.XML").write_bytes((
        '<?xml version="1.0" encoding="UTF-8"?>\n<kit><soundSources>'
        '<sound name="Tick" mode="subtractive"><osc1 type="sample" fileName="SAMPLES/quiet.wav" />'
        '<osc2 type="square" />'
        '<defaultParams volume="0x4CCCCCA8" oscAVolume="0x7FFFFFFF" oscBVolume="0x80000000" /></sound>'
        '</soundSources></kit>\n').encode("utf-8"))
    return root


# --------------------------------------------------------------------------------------------------------------------
# Self-test (for the build: runs every function on a demo card, and opens and closes the window)


def settle(root, app, seconds=30.0):
    """The window's work done, its threads too (the self-test and the tests)."""
    end = time.monotonic() + seconds
    while app.working and time.monotonic() < end:
        root.update()
        time.sleep(0.01)
    root.update()
    assert not app.working, "the window's work didn't end"


def selftest(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lines, ok = [f"version: v{VERSION}"], True
    bc.LANG = "de"

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

        def window():  # Its buttons pressed: levels read, then shown and written with a second START
            import tkinter as tk
            fresh = demo_card(Path(tmp) / "window")
            root = tk.Tk()
            try:
                app = App(root, None, card=str(fresh), lang="de")
                for keys, title in ((("levels", "read", "start"), "1 SONG, 1 MIT HINWEISEN"),
                                    (("apply", "start"), "Pegel: 1 Datei"), (("start",), "GESCHRIEBEN")):
                    for key in keys:
                        app.press(key)
                    settle(root, app)
                    assert app.list and app.list["title"] == title, app.list
                assert [s["status"] for s in app.songs] == ["ok"], app.songs
                assert "| Demo | 35 | aus | 35 | 50 | ok |" in bc.report(bc.song_files([str(fresh)]))
                app.set_lang("en")
                for key in ("read", "start"):
                    app.press(key)
                settle(root, app)
                assert app.list["title"] == "1 SONG, 0 WITH NOTES", app.list
            finally:
                root.destroy()
                bc.LANG = "de"

        def english():  # The same card, its texts in English
            bc.LANG = "en"
            try:
                fresh = demo_card(Path(tmp) / "english")
                text = bc.report(bc.song_files([str(fresh)]))
                assert "Song volume 40" in text and "Master compressor on" in text, text
                plan = plan_levels(fresh)
                assert plan.summary[0] == "1 song read, 1 to change", plan.summary
                assert "Song volume: 40.0 to 35.4, -2.1 dB" in plan.changes["SONGS/Demo.XML"].lines, plan.report()
            finally:
                bc.LANG = "de"

        for name, fn in (("check", check), ("levels", levels), ("normalize", normalize), ("restore", restore),
                         ("window", window), ("english", english)):
            step(name, fn)
    (out / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ok


# --------------------------------------------------------------------------------------------------------------------
# The window: DelugeRec's look (deluge_rec.py), the Deluge's own, a little bigger for the text. A panel in its
# proportions, the OLED with its pixel font, pads (one per song), round buttons with their LEDs in boxes, boxes to
# tick, the gold knob, a language switch (German, English). Plain Python: the window doesn't need numpy.

PANEL, PLATE, EDGE, BEZEL, LABEL, SMALL = "#0e0e10", "#18181b", "#26262b", "#050506", "#d8d8de", "#8c8c96"
BOX, BOX_EDGE = "#1d1d21", "#35353d"  # The box around each control
OLED_ON, OLED_OFF = (226, 238, 255), (7, 9, 13)
GREEN, AMBER, RED, CYAN, WHITE, BLUE = "#2fdc6e", "#ffae1c", "#ff2d2d", "#35d4e8", "#e8e8f0", "#3d8bff"
# The window's icon, 64 x 64: the Deluge's rain of squares under a gold line (deluge_rec_icon.py --baseline draws it,
# and deluge_baseline.ico for the .exe)
ICON_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAB20lEQVR42u2bwWrCQBCG9xG0vQhipTRIlVJ7LeTUg8c+QE95gr6GD9RH"
            "8OC9l1Do3WOgVw/bnbBbJiElJm5idvcX/osmYf7PmXGEHSFOeK1WT7HSVmmndFCSA9VBx0ixxuLcl3pIopQO2HCdKPakjfFIac8ftlyu"
            "ZRTdy/n8Ts5mt4MUxUYxUqwlEOQlEg3SPTM3LxYPcjq9kePxtRyNrpwQxUoxU+wMQlZbFtr80dxEVF0yXgWCPDAIx38h6LTPTLpPJlNn"
            "jZdFXlhZZJXlwGveJ/McAu8JVd3+L+19M29UKoeEA0hNw3O55k/pCawxprzx5W9S5/TVvBF5ZFkQCz015U3C52+fZwFriFuhR8d8gPDd"
            "vBF51QB2wsz2NEU1echm81qQ7eu7FHk1/x1E2+7vMgD+awAAAGBpAHIJCAA0AfDyHMmvjzcnRbEDgK0S+Pl8LKguzbq+vvceAAAAEDgA"
            "n4AAAAAAgP1RuM8mBgAAAADDA1DW+vu9oKafn3s9AAAAAHQLwGZAfQMBAAAAAPtN0LaBLoEAAAAAwOUHoa6bKgAAAABcFsCQgQAAABQB"
            "tDom5zKA8jG54A9KBn9UNuzD0sEfl8fCBFZmsDSFtTksTmJ1FsvTwa7P/wKH+aq0pxWiLQAAAABJRU5ErkJggg==")

# DelugeRec's 5 x 7 pixel font (7 rows of 5 pixels), with the German letters and a few signs more
FONT = {
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11100 10010 10001 10001 10001 10010 11100",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 10001 01010 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    " ": "00000 00000 00000 00000 00000 00000 00000", ".": "00000 00000 00000 00000 00000 01100 01100",
    ":": "00000 01100 01100 00000 01100 01100 00000", "-": "00000 00000 00000 11111 00000 00000 00000",
    "_": "00000 00000 00000 00000 00000 00000 11111", "/": "00000 00001 00010 00100 01000 10000 00000",
    "%": "11000 11001 00010 00100 01000 10011 00011", "+": "00000 00100 00100 11111 00100 00100 00000",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    "!": "00100 00100 00100 00100 00100 00000 00100", "?": "01110 10001 00001 00010 00100 00000 00100",
    ",": "00000 00000 00000 00000 01100 00100 01000", "'": "01100 00100 01000 00000 00000 00000 00000",
    ">": "01000 00100 00010 00001 00010 00100 01000", "<": "00010 00100 01000 10000 01000 00100 00010",
    "=": "00000 00000 11111 00000 11111 00000 00000", "*": "00000 00100 10101 01110 10101 00100 00000",
    "Ä": "01010 00000 01110 10001 11111 10001 10001", "Ö": "01010 00000 01110 10001 10001 10001 01110",
    "Ü": "01010 00000 10001 10001 10001 10001 01110", "«": "00000 00101 01010 10100 01010 00101 00000",
    "»": "00000 10100 01010 00101 01010 10100 00000", "#": "01010 01010 11111 01010 11111 01010 01010",
    "[": "01110 01000 01000 01000 01000 01000 01110", "]": "01110 00010 00010 00010 00010 00010 01110",
    "&": "01100 10010 10100 01000 10101 10010 01101", ";": "00000 01100 01100 00000 01100 00100 01000",
    '"': "01010 01010 00000 00000 00000 00000 00000", "^": "00100 01010 10001 00000 00000 00000 00000",
    "|": "00100 00100 00100 00100 00100 00100 00100", "\\": "00000 10000 01000 00100 00010 00001 00000",
}
_glyphs = {}


def glyph(ch):
    """A character's 7 rows of 5 pixels, 1 where it is lit."""
    g = _glyphs.get(ch)
    if g is None:
        g = _glyphs[ch] = tuple(tuple(int(b) for b in row) for row in FONT[ch].split())
    return g


def oled_text(s):
    """Text as the OLED can show it: upper case, a letter it lacks without its accent, else '?'."""
    out = []
    for ch in str(s).upper():
        if ch not in FONT:
            ch = unicodedata.normalize("NFKD", ch)[:1].upper()
            ch = ch if ch in FONT else "?"
        out.append(ch)
    return "".join(out)


class Oled:
    """The Deluge's OLED as DelugeRec draws it: w x h pixels (the Deluge's 128 x 48 unless told otherwise), each
    scale x scale on the screen (3 or more), with a visible pixel grid and scanlines."""

    def __init__(self, scale, w=128, h=48):
        self.scale, self.W, self.H = scale, w, h
        s = scale
        self.fb = [bytearray(w) for _ in range(h)]

        def pixel(colour, shade):  # One pixel of a line on the screen: its last column darker (the grid)
            return b"".join(bytes(int(c * shade * (0.8 if i == s - 1 else 1.0)) for c in colour) for i in range(s))
        # (off, on) for the lines of a pixel, and for its last line, darker (the scanline)
        self.line, self.scan = ((pixel(OLED_OFF, f), pixel(OLED_ON, f)) for f in (1.0, 0.45))
        self.header = b"P6 %d %d 255\n" % (self.W * s, self.H * s)

    def clear(self):
        for row in self.fb:
            row[:] = bytes(self.W)

    @staticmethod
    def width(s, size=1):
        return len(oled_text(s)) * 6 * size - size

    def text(self, x, y, s, size=1, invert=False):
        for ch in oled_text(s):
            for r, bits in enumerate(glyph(ch)):
                for yy in range(y + r * size, y + (r + 1) * size):
                    if not 0 <= yy < self.H:
                        continue
                    row = self.fb[yy]
                    for col, bit in enumerate(bits):
                        if bit or invert:
                            for xx in range(max(x + col * size, 0), min(x + (col + 1) * size, self.W)):
                                row[xx] = 1 - bit if invert else 1
            x += 6 * size
        return x

    def rect(self, x, y, w, h, on=True):
        x0, x1 = max(x, 0), min(x + w, self.W)
        if x1 > x0:
            fill = (b"\x01" if on else b"\x00") * (x1 - x0)
            for yy in range(max(y, 0), min(y + h, self.H)):
                self.fb[yy][x0:x1] = fill

    def frame(self):
        return b"".join(self.fb)

    def ppm(self):
        out = [self.header]
        for row in self.fb:
            out += [b"".join([self.line[v] for v in row])] * (self.scale - 1)
            out.append(b"".join([self.scan[v] for v in row]))
        return b"".join(out)


def settings_path():
    base = os.environ.get("APPDATA") if sys.platform.startswith("win") else None
    return (Path(base) / "DelugeBaseline" if base else Path.home() / ".config" / "deluge_baseline") / "settings.json"


def system_language():
    """de if the computer speaks German, else en: the window's language until it is switched."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            return "de" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x07 else "en"
        import locale
        code = locale.getlocale()[0] or os.environ.get("LC_ALL") or os.environ.get("LANG") or ""
        return "de" if code.lower().startswith("de") else "en"
    except Exception:
        return "de"


def find_card():
    """A Deluge card among the drives (Windows): the first with a SONGS folder."""
    if not sys.platform.startswith("win"):
        return ""
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        if os.path.isdir(f"{letter}:\\SONGS"):
            return f"{letter}:\\"
    return ""


def song_name(rel):
    """A song's name from its path on the card: SONGS/sub/Name.XML -> sub/Name."""
    rel = rel[6:] if rel.upper().startswith("SONGS/") else rel
    return rel[:-4] if rel.upper().endswith(".XML") else rel


def backup_name(name):
    """A backup's folder name short enough for a line: 2026-09-28 07-18-35 Pegel -> 28.09.26 07:18 Pegel."""
    m = re.match(r"\d\d(\d\d)-(\d\d)-(\d\d) (\d\d)-(\d\d)-\d\d (.*)", name)
    return f"{m[3]}.{m[2]}.{m[1]} {m[4]}:{m[5]} {m[6]}" if m else name


def check_items(results):
    """The OLED's list after reading the levels: per song a heading, then its notes. (text, heading, song index)"""
    items = []
    for i, r in enumerate(results):
        if r["error"] is not None:
            items += [(f"{r['name']}: " + bc.t("NICHT LESBAR", "UNREADABLE"), True, i), (r["error"], False, i)]
        elif r["notes"]:
            n = len(r["notes"])
            items.append((f"{r['name']}: {n} " + (bc.t("HINWEIS", "NOTE") if n == 1 else bc.t("HINWEISE", "NOTES")),
                          True, i))
            items += [(note, False, i) for note in r["notes"]]
        else:
            items.append((f"{r['name']}: OK", True, i))
    return items


def plan_items(plan, song_index):
    """The OLED's list of a plan: its summary, then per section a heading and its lines."""
    items = [(line, False, None) for line in plan.summary]
    for heading, lines in plan.sections:
        what, _, rel = heading.rpartition(": ")
        song = song_index.get(rel_key(rel))
        title = song_name(rel) if rel.upper().startswith("SONGS/") else rel
        items.append((f"{what}: {title}" if what else title, True, song))
        items += [(line, False, song) for line in lines]
    return items


class App:
    """The window. What it does: a function (PEGEL or NORM) in a mode (LESEN: only read, ANPASSEN: show, then write
    on the second START), written onto the SD card directly or into a copy folder."""
    W, H = 900, 614  # The panel at scale 1, in the Deluge's (and DelugeRec's) proportions: 305 x 208 mm
    ROWS, PADS = 5, 32  # Lines of a list on the display; pads (two rows of 16)
    DISPLAY = (170, 64)  # The display's pixels: 28 characters on 5 lines and a bottom line, at 3 x as big as the
    # Deluge's 128 x 48 at 4 x
    COLOURS = {"levels": AMBER, "normalize": CYAN, "read": WHITE, "apply": RED, "start": GREEN, "card": GREEN,
               "report": WHITE, "restore": BLUE}
    # The computer's keys, by language (shown small under each button)
    LETTERS = {"de": {"levels": "P", "normalize": "N", "read": "L", "apply": "A", "card": "K", "report": "B",
                      "restore": "Z"},
               "en": {"levels": "L", "normalize": "N", "read": "R", "apply": "A", "card": "C", "report": "T",
                      "restore": "B"}}

    def __init__(self, root, settings, card=None, z=1.0, lang=None):
        import tkinter as tk
        from tkinter import filedialog
        self.tk, self.filedialog = tk, filedialog
        self.root, self.settings = root, settings
        saved = {}
        if settings:
            try:
                saved = json.loads(Path(settings).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
        self.card = card or saved.get("card") or find_card()
        self.lang = lang or (saved.get("lang") if saved.get("lang") in ("de", "en") else system_language())
        bc.LANG = self.lang
        self.function = saved.get("function") if saved.get("function") in ("levels", "normalize") else "levels"
        self.mode = saved.get("mode") if saved.get("mode") in ("read", "apply") else "read"
        self.dest = saved.get("dest") if saved.get("dest") in ("card", "folder") else "card"
        self.out = saved.get("out", "")
        try:
            self.target = float(str(saved.get("target", -1.0)).replace(",", "."))
        except ValueError:
            self.target = -1.0
        self.target = min(TARGETS, key=lambda t: abs(t - self.target))
        self.compensate = bool(saved.get("compensate", True))
        # A little bigger than DelugeRec (more text), as big as the screen's scale asks, never bigger than the screen
        scale = max(3, int(3 * z + 0.5))
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        while scale > 3 and (self.W * scale / 3 > sw * 0.98 or self.H * scale / 3 > sh - 90):
            scale -= 1
        self.oled = Oled(scale, *self.DISPLAY)
        s = scale / 3  # Everything follows the display's size
        self.Z = lambda v: int(round(v * s))  # noqa: E731
        self.boot_until = time.monotonic() + 1.4
        self.view = "home"  # home, list, backups
        self.list = None  # {"title", "items": [(text, heading, song)], "sel", "since"}
        self.backup_list = []
        self.pending = None  # (kind, plan): shown, waits for the second START
        self.preview = None  # A plan only read: its songs on the pads
        self.busy = self.running = None  # What runs (its title) and on which button, None when nothing does
        self.working = 0  # Threads not done yet, the quiet ones too
        self.progress = ""
        self.message, self.message_since, self.message_until = "", 0.0, 0.0
        self.songs = []  # [{"name", "rel", "status"}], status: None (not read), ok, notes, error
        self.song_index = {}  # rel key -> index
        self.report_text = ""
        self.jobs = queue.Queue()
        self.drag = None
        self.bound = []  # The keys bound for the language set

        root.title(f"DELUGE BASELINE v{VERSION}")
        self.icon = tk.PhotoImage(data=ICON_PNG)
        root.iconphoto(True, self.icon)
        root.configure(bg=PANEL)
        root.resizable(False, False)
        self.c = tk.Canvas(root, width=self.Z(self.W), height=self.Z(self.H), bg=PANEL, highlightthickness=0)
        self.c.pack()
        self.img = tk.PhotoImage(width=self.oled.W * scale, height=self.oled.H * scale)
        self.build()
        self.bind_keys()
        for k in ("<Return>", "<KP_Enter>"):
            root.bind(k, lambda e: self.press("start"))
        root.bind("<Escape>", lambda e: self.escape())
        for k, step in (("<Up>", -1), ("<Down>", 1), ("<Prior>", -self.ROWS), ("<Next>", self.ROWS)):
            root.bind(k, lambda e, s=step: self.move(s))
        root.bind("<Home>", lambda e: self.move(-10 ** 6))
        root.bind("<End>", lambda e: self.move(10 ** 6))
        for k in ("<plus>", "<KP_Add>"):
            root.bind(k, lambda e: self.turn_gold(1))
        for k in ("<minus>", "<KP_Subtract>"):
            root.bind(k, lambda e: self.turn_gold(-1))
        root.bind("<MouseWheel>", lambda e: self.wheel(e, 1 if e.delta > 0 else -1))
        root.bind("<Button-4>", lambda e: self.wheel(e, 1))
        root.bind("<Button-5>", lambda e: self.wheel(e, -1))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        if self.card:
            self.load_songs()
        self.tick()

    # --- the panel, drawn anew when the language changes

    def build(self):
        c, Z, t = self.c, self.Z, bc.t
        c.delete("all")
        self.shown = {}  # What the LEDs, boxes, pads and OLED show: only a change is drawn
        self.leds, self.checks = {}, {}
        W, H = Z(self.W), Z(self.H)
        c.create_rectangle(Z(8), Z(8), W - Z(8), H - Z(8), fill=PLATE, outline=EDGE, width=Z(1))
        self.label(Z(26), Z(18), "DELUGE", Z(3))
        self.label(Z(26) + self.label_width("DELUGE", Z(3)) + Z(12), Z(25), "BASELINE", Z(2))
        # The OLED in its bezel: a click chooses a line, a double click a backup (never a write: that is START's)
        self.ox, self.oy = ox, oy = Z(40), Z(58)
        ow, oh = self.oled.W * self.oled.scale, self.oled.H * self.oled.scale
        c.create_rectangle(ox - Z(6), oy - Z(6), ox + ow + Z(6), oy + oh + Z(6), fill=BEZEL, outline=EDGE)
        screen = c.create_image(ox, oy, image=self.img, anchor="nw")
        c.tag_bind(screen, "<Button-1>", self.click_oled)
        c.tag_bind(screen, "<Double-Button-1>", lambda e: self.press("start") if self.view == "backups" else None)
        # Pads: one per song, its colour its state, 32 at a time
        self.pads = []
        for i in range(self.PADS):
            x, y = ox + (i % 16) * Z(32), Z(270) + (i // 16) * Z(28)
            pad = c.create_rectangle(x, y, x + Z(28), y + Z(24), fill=self.dim(WHITE, 0.03), outline="#0a0a0c",
                                     width=Z(1))
            c.tag_bind(pad, "<Button-1>", lambda e, i=i: self.click_pad(i))
            c.tag_bind(pad, "<Enter>", lambda e, i=i: self.hover_pad(i))
            self.pads.append(pad)
        # Below: what it does, how, and go; then the card, the report and the backups
        self.button_row([(t("FUNKTION", "FUNCTION"), [("levels", t("PEGEL", "LEVELS")), ("normalize", "NORM")]),
                         (t("MODUS", "MODE"), [("read", t("LESEN", "READ")), ("apply", t("ANPASSEN", "APPLY"))]),
                         ("", [("start", "START")])], Z(26), Z(566), Z(338), Z(468), Z(62))
        report_w = max(self.label_width(t("BERICHT", "REPORT"), Z(2)), Z(50)) + Z(40)
        restore_w = max(self.label_width(t("ZURÜCK", "RESTORE"), Z(2)), Z(50)) + Z(40)
        x1 = Z(566) - report_w - restore_w - Z(20)
        self.box(Z(26), Z(484), x1, Z(596))
        self.card_area = (Z(26) + Z(96), Z(484))
        self.button("card", t("KARTE", "CARD"), Z(26) + Z(46), Z(522))
        self.button_row([("", [("report", t("BERICHT", "REPORT"))]), ("", [("restore", t("ZURÜCK", "RESTORE"))])],
                        x1 + Z(10), Z(566), Z(484), Z(596), Z(38))
        # On the right: where it writes, the samples' settings, the language
        rx0, rx1 = Z(578), Z(874)
        self.box(rx0, Z(52), rx1, Z(184))
        self.label(rx0 + Z(14), Z(62), t("SCHREIBEN NACH", "WRITE TO"), Z(2), SMALL)
        self.checkbox("sd", t("SD-KARTE DIREKT", "SD CARD DIRECTLY"), rx0 + Z(16), Z(98), GREEN)
        self.checkbox("folder", t("KOPIE-ORDNER", "COPY FOLDER"), rx0 + Z(16), Z(130), BLUE)
        self.out_area = (rx0 + Z(44), Z(152))
        c.tag_bind("out", "<Button-1>", lambda e: self.choose_out() and self.tick_box("folder"))
        self.box(rx0, Z(194), rx1, Z(468))
        self.label(rx0 + Z(14), Z(204), t("NORMALISIEREN", "NORMALIZE"), Z(2), SMALL)
        self.checkbox("compensate", t("AUSGLEICHEN", "COMPENSATE"), rx0 + Z(16), Z(240), CYAN)
        for i, line in enumerate(t(("SENKT DEN OSC-PEGEL UM GLEICH VIEL:", "DIE SONGS KLINGEN WIE VORHER."),
                                   ("LOWERS THE OSC LEVEL BY AS MUCH:", "THE SONGS SOUND AS BEFORE."))):
            self.label(rx0 + Z(44), Z(260) + i * Z(11), line, Z(1), SMALL)
        gx, gy = self.gold_knob = (rx0 + Z(62), Z(366))
        knob = [c.create_oval(gx - Z(26), gy - Z(26), gx + Z(26), gy + Z(26), fill="#8a6a28", outline="#4e3b14",
                              width=Z(2)),
                c.create_oval(gx - Z(20), gy - Z(20), gx + Z(20), gy + Z(20), fill="#d9b35a", outline="#f0d58c",
                              width=Z(1))]
        self.gold_line = c.create_line(gx, gy, gx, gy - Z(18), fill="#2a1e08", width=Z(3), capstyle="round")
        knob.append(self.gold_line)
        ziel = t("ZIEL", "TARGET")
        self.label(gx - self.label_width(ziel, Z(2)) // 2, gy + Z(36), ziel, Z(2))
        self.target_area = (gx + Z(44), gy - Z(18))
        self.label(gx + Z(44), gy + Z(6), t("SPITZE DER SAMPLES", "PEAK OF THE SAMPLES"), Z(1), SMALL)
        self.label(gx + Z(44), gy + Z(18), t("ZIEHEN, RAD, + UND -", "DRAG, WHEEL, + AND -"), Z(1), SMALL)
        for item in knob:
            c.tag_bind(item, "<Button-1>", self.grab)
            c.tag_bind(item, "<B1-Motion>", self.drag_knob)
            c.tag_bind(item, "<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
            c.tag_bind(item, "<Enter>", lambda e: c.configure(cursor="sb_v_double_arrow"))
            c.tag_bind(item, "<Leave>", lambda e: c.configure(cursor=""))
        self.update_knob()
        self.box(rx0, Z(484), rx1, Z(596))
        self.label(rx0 + Z(14), Z(494), "SPRACHE / LANGUAGE", Z(2), SMALL)
        # The language switch: a click on DE or EN chooses it, a click on the switch flips it
        mid, y = (rx0 + rx1) // 2, Z(548)
        self.label(mid - Z(44) - self.label_width("DE", Z(3)), y - Z(10), "DE", Z(3), tag=("lang", "lang_de"))
        self.label(mid + Z(44), y - Z(10), "EN", Z(3), tag=("lang", "lang_en"))
        c.create_rectangle(mid - Z(32), y - Z(13), mid + Z(32), y + Z(13), fill=BEZEL, outline=EDGE, width=Z(1),
                           tags=("lang", "lang_switch"))
        self.lang_knob = c.create_rectangle(0, 0, 0, 0, fill="#c8c8d0", outline="#f0f0f4", width=Z(1),
                                            tags=("lang", "lang_switch"))
        c.tag_bind("lang_de", "<Button-1>", lambda e: self.set_lang("de"))
        c.tag_bind("lang_en", "<Button-1>", lambda e: self.set_lang("en"))
        c.tag_bind("lang_switch", "<Button-1>", lambda e: self.set_lang("en" if self.lang == "de" else "de"))
        c.tag_bind("lang", "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind("lang", "<Leave>", lambda e: c.configure(cursor=""))

    def button_row(self, groups, x0, x1, top, bottom, cy):
        """Boxes of round buttons side by side over x0..x1, each with its title; cy: the buttons' centre from top."""
        Z = self.Z
        sizes = []
        for title, keys in groups:
            slots = [max(self.label_width(text, Z(2)), Z(50)) + Z(16) for _, text in keys]
            sizes.append((max(sum(slots) + Z(24), self.label_width(title, Z(2)) + Z(28) if title else 0), slots))
        gap = (x1 - x0 - sum(w for w, _ in sizes)) / max(1, len(groups) - 1)
        x = x0
        for (title, keys), (w, slots) in zip(groups, sizes):
            self.box(int(x), top, int(x + w), bottom)
            if title:
                self.label(int(x) + Z(14), top + Z(10), title, Z(2), SMALL)
            bx = x + (w - sum(slots)) / 2
            for (key, text), slot in zip(keys, slots):
                self.button(key, text, int(bx + slot / 2), top + cy)
                bx += slot
            x += w + gap

    def button(self, key, text, cx, cy):
        """A round button with its LED, its name under it and its key small below."""
        c, Z = self.c, self.Z
        colour = self.COLOURS[key]
        ring = c.create_oval(cx - Z(19), cy - Z(19), cx + Z(19), cy + Z(19), fill="#26262a", outline="#3c3c42",
                             width=Z(2))
        led = c.create_oval(cx - Z(7), cy - Z(7), cx + Z(7), cy + Z(7), fill=self.dim(colour, 0.22), outline="")
        self.label(cx - self.label_width(text, Z(2)) // 2, cy + Z(28), text, Z(2))
        letter = "ENTER" if key == "start" else self.LETTERS[self.lang][key]
        self.label(cx - self.label_width(letter, Z(1)) // 2, cy + Z(48), letter, Z(1), SMALL)
        for item in (ring, led):
            self.clickable(item, lambda e: self.press(key))
        self.leds[key] = (led, colour)

    def checkbox(self, key, text, x, y, colour):
        """A box to tick, with its name: all of it clickable."""
        c, Z = self.c, self.Z
        tag, s = "check_" + key, Z(18)
        c.create_rectangle(x - Z(6), y - Z(13), x + s + Z(16) + self.label_width(text, Z(2)), y + Z(13), fill=BOX,
                           outline="", tags=tag)
        c.create_rectangle(x, y - s // 2, x + s, y + s // 2, fill=BEZEL, outline="#6a6a74", width=Z(2), tags=tag)
        marks = [c.create_line(x + Z(5), y - s // 2 + Z(5), x + s - Z(5), y + s // 2 - Z(5), fill=colour, width=Z(3),
                               tags=tag),
                 c.create_line(x + Z(5), y + s // 2 - Z(5), x + s - Z(5), y - s // 2 + Z(5), fill=colour, width=Z(3),
                               tags=tag)]
        self.label(x + s + Z(10), y - Z(7), text, Z(2), tag=tag)
        c.tag_bind(tag, "<Button-1>", lambda e: self.tick_box(key))
        c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
        self.checks[key] = marks

    def bind_keys(self):
        for k in self.bound:
            self.root.unbind(k)
        self.bound = []
        for key, letter in self.LETTERS[self.lang].items():
            for k in (letter.lower(), letter.upper()):
                self.root.bind(k, lambda e, key=key: self.press(key))
                self.bound.append(k)
        for k, lang in (("d", "de"), ("D", "de"), ("e", "en"), ("E", "en")):  # Deutsch, English
            self.root.bind(k, lambda e, lang=lang: self.set_lang(lang))
            self.bound.append(k)

    # --- drawing helpers, as in DelugeRec

    def label(self, x, y, s, p, fill=LABEL, tag=None):
        """Lettering in the OLED's pixel font, p screen pixels per font pixel."""
        for ch in oled_text(s):
            for r, bits in enumerate(glyph(ch)):
                for col, bit in enumerate(bits):
                    if bit:
                        self.c.create_rectangle(x + col * p, y + r * p, x + (col + 1) * p, y + (r + 1) * p,
                                                fill=fill, width=0, tags=tag)
            x += 6 * p

    def box(self, x0, y0, x1, y1):
        """The box around controls on the panel."""
        self.c.create_rectangle(x0, y0, x1, y1, fill=BOX, outline=BOX_EDGE, width=self.Z(1))

    def clickable(self, item, action):
        self.c.tag_bind(item, "<Button-1>", action)
        self.c.tag_bind(item, "<Enter>", lambda e: self.c.configure(cursor="hand2"))
        self.c.tag_bind(item, "<Leave>", lambda e: self.c.configure(cursor=""))

    @staticmethod
    def label_width(s, p):
        return len(oled_text(s)) * 6 * p - p

    @staticmethod
    def dim(colour, f=0.14):
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % (int(24 + (r - 24) * f), int(24 + (g - 24) * f), int(26 + (b - 26) * f))

    def update_knob(self):
        gx, gy = self.gold_knob
        a = math.radians(-135 + 270 * TARGETS[::-1].index(self.target) / (len(TARGETS) - 1))
        r = self.Z(18)
        self.c.coords(self.gold_line, gx, gy, gx + r * math.sin(a), gy - r * math.cos(a))

    def say(self, text, seconds=2.0):
        """A message in the OLED's bottom line; a long one stays until it has scrolled through."""
        now = time.monotonic()
        over = max(0, Oled.width(text) - (self.oled.W - 1)) / 30
        self.message, self.message_since = text, now
        self.message_until = now + (max(seconds, over + 3.0) if over else seconds)

    def show(self, item, fill):
        if self.shown.get(item) != fill:
            self.shown[item] = fill
            self.c.itemconfigure(item, fill=fill)

    def redraw(self, tag, text, x, y, p, fill=LABEL):
        """Lettering that changes (the card, the folder, the target): drawn again when its text does."""
        if self.shown.get(tag) != text:
            self.shown[tag] = text
            self.c.delete(tag)
            self.label(x, y, text, p, fill, tag)
            return True
        return False

    # --- the gold knob, the wheel, the list

    def grab(self, event):
        self.drag = {"y": event.y}

    def drag_knob(self, event):
        d, step = self.drag, self.Z(12)
        while d and abs(event.y - d["y"]) >= step:
            up = event.y < d["y"]
            d["y"] += -step if up else step
            self.turn_gold(1 if up else -1)

    def wheel(self, event, step):
        gx, gy = self.gold_knob
        if (event.x - gx) ** 2 + (event.y - gy) ** 2 <= self.Z(34) ** 2:
            self.turn_gold(step)
        else:
            self.move(-step)

    def turn_gold(self, step):
        if self.busy:
            return
        i = TARGETS.index(self.target)
        self.target = TARGETS[min(len(TARGETS) - 1, max(0, i - step))]  # Up: louder, towards 0 dBFS
        self.update_knob()
        if self.pending and self.pending[0] == "normalize":  # Shown for the old target: START anew
            self.pending = None
        self.say(bc.t("ZIEL ", "TARGET ") + f"{bc.num(self.target)} DBFS")
        self.save()

    def move(self, step):
        """Through the list on the OLED: wheel, arrow keys."""
        if self.view in ("list", "backups") and self.list:
            sel = min(len(self.list["items"]) - 1, max(0, self.list["sel"] + step))
            if sel != self.list["sel"]:
                self.list["sel"], self.list["since"] = sel, time.monotonic()

    def click_oled(self, event):
        row = ((event.y - self.oy) // self.oled.scale - 11) // 9
        if self.view in ("list", "backups") and self.list and 0 <= row < self.visible_rows():
            top = self.list_top()
            if top + row < len(self.list["items"]):
                self.list["sel"], self.list["since"] = top + row, time.monotonic()

    def click_pad(self, i):
        n = self.pad_page() * self.PADS + i
        if n >= len(self.songs):
            return
        if self.list and self.view == "list":
            for j, (_, heading, song) in enumerate(self.list["items"]):
                if heading and song == n:
                    self.list["sel"], self.list["since"] = j, time.monotonic()
                    return
        self.say(self.songs[n]["name"], 2.5)

    def hover_pad(self, i):
        n = self.pad_page() * self.PADS + i
        if n < len(self.songs):
            status = {"ok": "OK", "notes": bc.t("HINWEISE", "NOTES"),
                      "error": bc.t("NICHT LESBAR", "UNREADABLE")}.get(self.songs[n]["status"], "")
            self.say(f"{self.songs[n]['name']} {status}".strip(), 1.6)

    # --- the buttons and boxes

    def press(self, key):
        if self.busy:
            self.say(bc.t("BITTE WARTEN", "PLEASE WAIT"))
            return
        if key in ("levels", "normalize", "read", "apply"):  # What START will do
            if key in ("levels", "normalize"):
                self.function = key
            else:
                self.mode = key
            self.pending = self.preview = None
            self.view = "home"
            self.save()
        elif key == "start":
            self.go()
        elif key == "card":
            self.choose_card()
        elif key == "report":
            self.open_report()
        elif key == "restore":
            self.backups()

    def go(self):
        """START: the second press writes what is shown; else the backup chosen, or the function in its mode."""
        if self.pending:
            self.write()
            return
        if self.view == "backups" and self.list and self.backup_list:
            backup = self.backup_list[self.list["sel"]]
            self.start(bc.t("LESE SICHERUNG", "READING BACKUP"), lambda: plan_restore(backup),
                       lambda plan: self.show_plan(plan, True), "restore")
            return
        root = self.card_or_say()
        if root is None:
            return
        apply = self.mode == "apply"
        if self.function == "levels" and not apply:
            self.start(bc.t("LESE PEGEL", "READING LEVELS"), lambda: self.checked(root), self.show_check, "start")
        elif self.function == "levels":
            self.start(bc.t("SUCHE PEGEL", "FINDING LEVELS"), lambda: plan_levels(root),
                       lambda plan: self.show_plan(plan, True), "start")
        else:
            target, compensate = self.target, self.compensate
            self.start(bc.t("LESE SAMPLES", "READING SAMPLES"),
                       lambda: plan_normalize(root, target, compensate, lambda t: self.jobs.put(("progress", t))),
                       lambda plan: self.show_plan(plan, apply), "start")

    def backups(self):
        root = self.card_or_say()
        if root is None:
            return
        folder = root / BACKUP
        found = sorted((p for p in folder.iterdir() if p.is_dir()), reverse=True) if folder.is_dir() else []
        if not found:
            self.say(bc.t("KEINE SICHERUNG AUF DER KARTE", "NO BACKUP ON THE CARD"), 3)
            return
        self.pending = self.preview = None
        self.backup_list = found
        self.list = {"title": bc.t("SICHERUNG WÄHLEN", "CHOOSE A BACKUP"),
                     "items": [(backup_name(b.name), False, None) for b in found], "sel": 0, "since": time.monotonic()}
        self.view = "backups"
        self.say(bc.t("START: ZEIGEN, WAS ZURÜCKKOMMT", "START: SHOW WHAT COMES BACK"), 3)

    def escape(self):
        if self.pending:
            self.pending = None
            self.say(bc.t("NICHTS GESCHRIEBEN", "NOTHING WRITTEN"))
        else:
            self.view, self.preview = "home", None

    def tick_box(self, key):
        if self.busy:
            return
        if key == "compensate":
            self.compensate = not self.compensate
            if self.pending and self.pending[0] == "normalize":  # Shown with the other setting: START anew
                self.pending = None
            self.say(bc.t("AUSGLEICHEN AN", "COMPENSATE ON") if self.compensate else
                     bc.t("AUSGLEICHEN AUS: SONGS WERDEN LAUTER", "COMPENSATE OFF: SONGS GET LOUDER"), 3)
        elif key == "folder":
            if not self.out and not self.choose_out():
                return
            self.dest = "folder"
            self.say(bc.t("SCHREIBT IN DEN KOPIE-ORDNER ", "WRITES TO THE COPY FOLDER ") + self.out, 3)
        else:
            self.dest = "card"
            self.say(bc.t("SCHREIBT DIREKT AUF DIE SD-KARTE", "WRITES TO THE SD CARD DIRECTLY"), 3)
        self.save()

    def set_lang(self, lang):
        """The language switch: the panel and every text anew. What was shown goes (it was in the other language)."""
        if self.busy or lang == self.lang:
            return
        self.lang = bc.LANG = lang
        self.pending = self.preview = self.list = None
        self.report_text, self.view = "", "home"
        self.build()
        self.bind_keys()
        self.save()
        self.say(bc.t("DEUTSCH", "ENGLISH"))

    def choose_card(self):
        if self.busy:
            return
        path = self.filedialog.askdirectory(title=bc.t("Deluge-Karte (oder ihr Ordner SONGS)",
                                                       "Deluge card (or its SONGS folder)"),
                                            initialdir=self.card or None)
        if not path:
            return
        try:
            root = card_root(path)
        except (OSError, ValueError):
            self.say(bc.t("KEINE DELUGE-KARTE: OHNE ORDNER SONGS", "NO DELUGE CARD: NO SONGS FOLDER"), 3)
            return
        self.card = str(root)
        self.pending = self.preview = self.list = None
        self.view, self.report_text = "home", ""
        self.save()
        self.load_songs()
        self.say(bc.t("KARTE ", "CARD ") + self.card, 3)

    def choose_out(self):
        if self.busy:
            return False
        path = self.filedialog.askdirectory(title=bc.t("Kopie-Ordner für die geänderten Dateien",
                                                       "Copy folder for the changed files"),
                                            initialdir=self.out or None)
        if path:
            self.out = path
            self.save()
            return True
        return False

    def open_report(self):
        if not self.report_text:
            self.say(bc.t("NOCH KEIN BERICHT: ERST START", "NO REPORT YET: START FIRST"), 3)
            return
        path = (Path(self.settings).parent if self.settings else Path(tempfile.gettempdir())) / bc.t(
            "Bericht.txt", "Report.txt")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.report_text, encoding="utf-8-sig")
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # noqa: S606 (the report, in the computer's text viewer)
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
            self.say(bc.t("BERICHT OFFEN", "REPORT OPEN"))
        except OSError:
            self.say(bc.t("BERICHT: ", "REPORT: ") + str(path), 4)

    def card_or_say(self):
        """The card's root, or None with the reason on the OLED."""
        if not self.card:
            self.say(bc.t("KEINE KARTE: KARTE WÄHLEN", "NO CARD: CHOOSE A CARD"), 3)
            return None
        try:
            return card_root(self.card)
        except (OSError, ValueError):
            self.say(bc.t("KARTE NICHT DA: ", "CARD NOT THERE: ") + self.card, 3)
            return None

    def quit(self):
        if self.busy == bc.t("SCHREIBE", "WRITING"):  # Not in the middle of writing the card
            self.say(bc.t("SCHREIBT: BITTE WARTEN", "WRITING: PLEASE WAIT"), 2.5)
            return
        self.root.destroy()

    # --- work in a thread, its results through a queue

    def start(self, title, work, done, key, quiet=False):
        if not quiet:
            self.busy, self.running, self.progress = title, key, ""
        self.working += 1

        def worker():
            try:
                self.jobs.put(("done", done, work(), None, quiet))
            except Exception as e:  # Shown on the OLED
                self.jobs.put(("done", done, None, e, quiet))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while True:
            try:
                item = self.jobs.get_nowait()
            except queue.Empty:
                return
            if item[0] == "progress":
                self.progress = item[1]
                continue
            _, done, result, error, quiet = item
            self.working -= 1
            if not quiet:
                self.busy = self.running = None
            if error is None:
                done(result)
            elif not quiet:
                self.show_list(bc.t("FEHLER", "ERROR"), [(f"{type(error).__name__}: {error}", False, None)])
                self.say(bc.t("FEHLER BEIM SCHREIBEN", "ERROR WHILE WRITING") if done == self.written else
                         bc.t("FEHLER: NICHTS GEÄNDERT", "ERROR: NOTHING CHANGED"), 4)

    def checked(self, root):
        files = bc.song_files([str(root)])
        results = bc.check_all(files, lambda i, n: self.jobs.put(("progress", f"SONG {i + 1}/{n}")))
        return root, files, results

    def set_songs(self, root, results):
        self.songs = [{"name": r["name"], "rel": Path(r["path"]).relative_to(root).as_posix(),
                       "status": "error" if r["error"] else "notes" if r["notes"] else "ok"} for r in results]
        self.song_index = {rel_key(s["rel"]): i for i, s in enumerate(self.songs)}

    def load_songs(self):
        """The songs on the pads before they are read: grey."""
        try:
            root = card_root(self.card)
        except (OSError, ValueError):
            self.songs, self.song_index = [], {}
            return
        files = bc.song_files([str(root)])
        self.songs = [{"name": os.path.splitext(os.path.basename(p))[0],
                       "rel": Path(p).relative_to(root).as_posix(), "status": None} for p, _ in files]
        self.song_index = {rel_key(s["rel"]): i for i, s in enumerate(self.songs)}

    def show_check(self, result):
        root, files, results = result
        self.report_text = bc.report(files, results)
        self.set_songs(root, results)
        with_notes = sum(s["status"] != "ok" for s in self.songs)
        n = len(results)
        self.show_list(f"{n} SONG{'' if n == 1 else 'S'}, {with_notes} " + bc.t("MIT HINWEISEN", "WITH NOTES"),
                       check_items(results))
        self.say(bc.t("NUR GELESEN, NICHTS GEÄNDERT", "ONLY READ, NOTHING CHANGED"), 2.5)

    def show_plan(self, plan, pending):
        """A plan on the OLED: to write on the second START (pending), or only read."""
        self.report_text = plan.report()
        n = len(plan.changes)
        self.show_list(f"{plan.action}: {files(n)}", plan_items(plan, self.song_index))
        if not plan.changes:
            self.say(bc.t("NICHTS ZU ÄNDERN", "NOTHING TO CHANGE"), 3)
        elif pending:
            self.pending = (plan.kind, plan)
            self.say(bc.t("VORSCHAU", "PREVIEW"), 2.0)
        else:
            self.preview = plan
            self.say(bc.t("NUR GELESEN, NICHTS GEÄNDERT", "ONLY READ, NOTHING CHANGED"), 2.5)

    def write(self):
        kind, plan = self.pending
        self.pending = None
        to = self.out if self.dest == "folder" and kind != "restore" else None
        self.start(bc.t("SCHREIBE", "WRITING"),
                   lambda: write_plan(plan, to, progress=lambda t: self.jobs.put(("progress", t))), self.written,
                   "restore" if kind == "restore" else "start")

    def written(self, text):
        self.report_text += bc.t("\n## Geschrieben\n\n", "\n## Written\n\n") + text
        lines = [part for line in text.strip().splitlines() for part in re.split(r"(?<=\.) (?=[A-ZÄÖÜ])", line)]
        self.show_list(bc.t("GESCHRIEBEN", "WRITTEN"), [(line, False, None) for line in lines])
        self.say(bc.t("FERTIG", "DONE"))
        root = self.card_or_say()
        if root is not None:  # The pads anew, quietly
            self.start("", lambda: self.checked(root), lambda result: self.set_songs(*result[::2]), None, quiet=True)

    def show_list(self, title, items):
        self.list = {"title": title, "items": items or [("-", False, None)], "sel": 0, "since": time.monotonic()}
        self.view = "list"

    def save(self):
        if not self.settings:
            return
        try:
            Path(self.settings).parent.mkdir(parents=True, exist_ok=True)
            Path(self.settings).write_text(json.dumps({
                "card": self.card, "lang": self.lang, "function": self.function, "mode": self.mode,
                "dest": self.dest, "out": self.out, "target": self.target, "compensate": self.compensate}),
                encoding="utf-8")
        except OSError:
            pass

    # --- the loop: LEDs, boxes, pads, OLED, 25 times a second

    def visible_rows(self):
        return self.ROWS  # The question and the messages have a line of their own, at the bottom

    def list_top(self):
        items, sel, rows = self.list["items"], self.list["sel"], self.visible_rows()
        return min(max(0, sel - 1), max(0, len(items) - rows))

    def selected_song(self):
        if self.list and self.view == "list":
            return self.list["items"][self.list["sel"]][2]
        return None

    def pad_page(self):
        song = self.selected_song()
        return song // self.PADS if song is not None else 0

    def tick(self):
        now = time.monotonic()
        self.poll()
        blink = int(now * 2.5) % 2 == 0
        Z = self.Z
        lit = {"levels": self.function == "levels", "normalize": self.function == "normalize",
               "read": self.mode == "read", "apply": self.mode == "apply",
               "start": self.running == "start" or (bool(self.pending) and blink),
               "card": bool(self.songs) or (not self.card and blink),
               "report": bool(self.report_text),
               "restore": self.running == "restore" or self.view == "backups" or
               (bool(self.pending) and self.pending[0] == "restore")}
        for key, (led, colour) in self.leds.items():
            self.show(led, colour if lit[key] else self.dim(colour, 0.22))
        ticked = {"sd": self.dest == "card", "folder": self.dest == "folder", "compensate": self.compensate}
        for key, marks in self.checks.items():
            for m in marks:
                if self.shown.get(m) != ticked[key]:
                    self.shown[m] = ticked[key]
                    self.c.itemconfigure(m, state="normal" if ticked[key] else "hidden")
        mid = (Z(578) + Z(874)) // 2
        x = mid - Z(30) if self.lang == "de" else mid + Z(2)
        if self.shown.get("lang_knob") != x:
            self.shown["lang_knob"] = x
            self.c.coords(self.lang_knob, x, Z(548) - Z(11), x + Z(28), Z(548) + Z(11))
            for tag, on in (("lang_de", self.lang == "de"), ("lang_en", self.lang == "en")):
                self.c.itemconfigure(tag, fill=LABEL if on else self.dim(LABEL, 0.35))
        # The card, the copy folder, the target: their text on the panel
        cx, cy = self.card_area
        card = self.card or bc.t("KEINE KARTE", "NO CARD")
        name = Path(card).name or card
        self.redraw("card_name", name[:15], cx, cy + Z(24), Z(2))
        self.redraw("card_path", card if len(card) <= 30 else ".." + card[-28:], cx, cy + Z(46), Z(1), SMALL)
        n = len(self.songs)
        self.redraw("card_songs", f"{n} SONG{'' if n == 1 else 'S'}" if self.card else "", cx, cy + Z(60), Z(1),
                    SMALL)
        ox, oy = self.out_area
        out = self.out or bc.t("(NOCH KEIN ORDNER)", "(NO FOLDER YET)")
        if self.redraw("out", out if len(out) <= 36 else ".." + out[-34:], ox, oy, Z(1), SMALL):
            self.c.tag_bind("out", "<Enter>", lambda e: self.c.configure(cursor="hand2"))
            self.c.tag_bind("out", "<Leave>", lambda e: self.c.configure(cursor=""))
        tx, ty = self.target_area
        self.redraw("target", f"{bc.num(self.target)} DBFS", tx, ty, Z(2))
        # The pads: after reading the levels green, amber, red; what a plan touches in its function's colour
        page, selected = self.pad_page(), self.selected_song()
        plan = self.pending[1] if self.pending else self.preview
        touched = {self.song_index.get(rel_key(rel)) for rel in plan.changes} if plan else ()
        colour = {"levels": AMBER, "normalize": CYAN, "restore": BLUE}[plan.kind] if plan else None
        for i, pad in enumerate(self.pads):
            n = page * self.PADS + i
            if n >= len(self.songs):
                fill = self.dim(WHITE, 0.03)
            elif plan:  # Blinking: it would be written on START
                on = n in touched and (blink or not self.pending)
                fill = colour if on else self.dim(colour, 0.45 if n in touched else 0.08)
            else:
                c = {"ok": GREEN, "notes": AMBER, "error": RED}.get(self.songs[n]["status"], WHITE)
                fill = c if self.songs[n]["status"] else self.dim(c, 0.3)
                if n == selected and not blink:
                    fill = self.dim(c, 0.5)
            self.show(pad, fill)
        self.draw_oled(now, blink)
        frame = self.oled.frame()
        if frame != self.shown.get("oled"):
            self.shown["oled"] = frame
            self.img.configure(data=self.oled.ppm())
        self.root.after(40, self.tick)

    def marquee(self, o, x, y, text, since, invert=False, width=None):
        """A line too long for the OLED scrolls, as on the Deluge: a pause, then along, a pause at its end."""
        width = width or o.W - x
        span = o.width(text) - width
        shift = 0
        if span > 0:
            t = time.monotonic() - since - 1.0
            shift = int(min(span, (t % (span / 30 + 2.0)) * 30)) if t > 0 else 0
        o.text(x - shift, y, text, invert=invert)

    def draw_oled(self, now, blink):
        o = self.oled
        o.clear()
        message = self.message if self.message and now < self.message_until else ""
        cols = o.W // 6
        if now < self.boot_until:  # At start the name and the version, as the Deluge shows its own
            o.text((o.W - o.width("DELUGE", 2)) // 2, 14, "DELUGE", 2)
            o.text((o.W - o.width(f"BASELINE V{VERSION}")) // 2, 38, f"BASELINE V{VERSION}")
            return
        if self.busy:
            o.text(0, 0, self.busy)
            o.rect(0, 9, o.W, 1)
            o.text(0, 16, self.progress[:cols] if self.progress else bc.t("BITTE WARTEN", "PLEASE WAIT"))
            for x, y, w, h in ((0, 32, o.W, 1), (0, 44, o.W, 1), (0, 32, 1, 13), (o.W - 1, 32, 1, 13)):
                o.rect(x, y, w, h)
            m = re.search(r"(\d+)/(\d+)", self.progress)
            if m and int(m.group(2)):
                o.rect(2, 34, (o.W - 4) * int(m.group(1)) // int(m.group(2)), 9)
            else:
                o.rect(2 + int(now * 50) % (o.W - 26), 34, 22, 9)
            return
        if self.view in ("list", "backups") and self.list:
            items, rows = self.list["items"], self.visible_rows()
            title = self.list["title"]
            if self.pending:  # Where it would write
                title += " > " + (bc.t("ORDNER", "FOLDER") if self.dest == "folder" and self.pending[0] != "restore"
                                  else bc.t("SD-KARTE", "SD CARD"))
            self.marquee(o, 0, 0, title, self.list["since"])
            o.rect(0, 9, o.W, 1)
            top = self.list_top()
            for row, (text, heading, _) in enumerate(items[top:top + rows]):
                y, sel = 12 + row * 9, top + row == self.list["sel"]
                line = ("» " if heading else "  ") + text
                if sel:
                    o.rect(0, y - 1, o.W - 3, 9)
                    self.marquee(o, 0, y, line, self.list["since"], invert=True, width=o.W - 3)
                else:
                    o.text(0, y, line[:cols])
            if len(items) > rows:  # Where in the list: a thin bar on the right
                h = max(3, 45 * rows // len(items))
                o.rect(o.W - 1, 11 + (45 - h) * self.list["sel"] // max(1, len(items) - 1), 1, h)
            if self.pending and not message:  # Asks: START again writes
                o.rect(0, 56, o.W, 8, on=blink)
                o.text(1, 57, bc.t("SCHREIBEN? START = JA", "WRITE? START = YES"), invert=blink)
        else:
            self.draw_home(o)
        if message:
            o.rect(0, 56, o.W, 8)
            self.marquee(o, 1, 57, message, self.message_since, invert=True)

    def draw_home(self, o):
        """What START will do, from the buttons and boxes set."""
        t = bc.t
        if not self.card:
            o.text((o.W - o.width(t("KARTE?", "CARD?"), 2)) // 2, 6, t("KARTE?", "CARD?"), 2)
            o.text(1, 30, t("KNOPF KARTE: DIE SD-KARTE", "CARD BUTTON: CHOOSE THE SD"))
            o.text(1, 39, t("ODER EINE KOPIE WÄHLEN", "CARD OR A COPY OF IT"))
            return
        function = t("PEGEL", "LEVELS") if self.function == "levels" else "NORM"
        mode = t("LESEN", "READ") if self.mode == "read" else t("ANPASSEN", "APPLY")
        o.text(0, 0, f"{function} {mode}")
        n = len(self.songs)
        count = f"{n} SONG{'' if n == 1 else 'S'}"
        o.text(o.W - o.width(count), 0, count)
        o.rect(0, 9, o.W, 1)
        what = {("levels", "read"): t("ZEIGT, WAS ZU LAUT IST", "SHOWS WHAT IS TOO LOUD"),
                ("levels", "apply"): t("SETZT DIE SONGS AUF BASELINE", "SETS THE SONGS TO BASELINE"),
                ("normalize", "read"): t("ZEIGT, WAS ZU LEISE IST", "SHOWS WHAT IS TOO QUIET"),
                ("normalize", "apply"): t("HEBT LEISE SAMPLES ANS ZIEL", "RAISES QUIET SAMPLES")}[(self.function,
                                                                                                   self.mode)]
        self.marquee(o, 0, 12, what, self.boot_until)
        if self.mode == "read":
            o.text(0, 21, t("ÄNDERT NICHTS", "CHANGES NOTHING"))
        elif self.dest == "folder":
            o.text(0, 21, t("SCHREIBT NACH: KOPIE-ORDNER", "WRITES TO: COPY FOLDER"))
        else:
            o.text(0, 21, t("SCHREIBT NACH: SD-KARTE", "WRITES TO: SD CARD"))
        if self.function == "normalize":
            o.text(0, 30, t("ZIEL ", "TARGET ") + f"{bc.num(self.target)} DBFS, " + (
                t("AUSGL AN", "COMP ON") if self.compensate else t("AUSGL AUS", "COMP OFF")))
        else:
            o.text(0, 30, "SONG 35, SYNTH 40")
        card = self.card if len(self.card) <= 21 else ".." + self.card[-19:]
        o.text(0, 39, t("KARTE ", "CARD ") + card)
        if self.songs and all(song["status"] for song in self.songs):
            bad = sum(song["status"] != "ok" for song in self.songs)
            o.text(0, 48, t(f"GELESEN: {n - bad} OK, {bad} HINWEISE", f"READ: {n - bad} OK, {bad} WITH NOTES"))
        o.text(1, 57, t("START: LESEN", "START: READ") if self.mode == "read" else t("START: VORSCHAU",
                                                                                      "START: PREVIEW"))


# --------------------------------------------------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(prog="DelugeBaseline", description="Deluge-Songs und -Samples auf die Baseline. "
                                                                     "Deluge songs and samples to the baseline.")
    ap.add_argument("--version", action="version", version=f"DelugeBaseline v{VERSION}")
    ap.add_argument("--lang", choices=("de", "en"), help="Sprache, language (Standard/default: de)")
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)  # The self-test's folder
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("check", help="prüfen, liest nur / check, only reads")
    p.add_argument("card")
    p.add_argument("--out", dest="report")
    for name, text in (("levels", "Pegel der Songs auf die Baseline / the songs' levels to the baseline"),
                       ("normalize", "Samples normalisieren / normalize the samples")):
        p = sub.add_parser(name, help=text)
        p.add_argument("card")
        p.add_argument("--to", help="in diesen Ordner statt auf die Karte / into this folder instead of the card")
        p.add_argument("--yes", action="store_true", help="wirklich schreiben / really write (else only show)")
        if name == "normalize":
            p.add_argument("--target", type=float, default=-1.0, help="Ziel/target dBFS <= 0 (-1)")
            p.add_argument("--no-compensate", action="store_true", help="nicht ausgleichen / don't compensate")
    p = sub.add_parser("restore", help="eine Sicherung zurückspielen / restore a backup")
    p.add_argument("backup")
    p.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return 0 if selftest(args.out or ".") else 1
    bc.LANG = args.lang or "de"
    if args.cmd is None:
        if sys.platform.startswith("win"):
            try:  # Sharp text on scaled Windows displays
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        import tkinter as tk
        root = tk.Tk()
        App(root, settings_path(), z=min(3.0, max(1.0, root.winfo_fpixels("1i") / 96)), lang=args.lang)
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
            print(bc.t("\nNur gezeigt, nichts geschrieben: mit --yes schreiben.",
                       "\nOnly shown, nothing written: --yes writes."))
        return 0
    print(write_plan(plan, getattr(args, "to", None), progress=lambda t: print(t, end="\r", file=sys.stderr)), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
