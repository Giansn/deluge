#!/usr/bin/env python3
"""Checks every song on a Deluge card against the baseline master (geraet/analyse/2026-09-27-baseline-master.md).

It only reads: the card (or a copy of it) stays as it is. Per song it reports what goes over the baseline:
- the song's volume above 35, its default (0 dB there);
- the master compressor on;
- a kit or an audio track above 35;
- a synth or a kit row above 40, unless its sample is quiet enough: the knob times 10^(peak/40) at most 40. A row at 50
  whose sample peaks at -3.9 dBFS is as loud as a full-scale sample at 40. The peak is the sample file's, within the
  part the row plays (its zone), as the firmware reads the file;
- stages after the knobs that distort the more, the louder it comes in: SATURATION, an analog delay with feedback and
  a compressor on a kit, an audio track or the song, the compressor of a synth or row, a low-pass filter in drive
  mode, and a filter of a kit, an audio track or the song with resonance from 25 on.
Whether a song clips it can't tell: that needs measuring (in DelugeRec the pad -3 stays dark). With automation the
loudest point counts.

The firmware behind it (mastertune v17 on release_1_2_1): a volume knob's value v (0-50) is the param v * 85899345 -
2^31, and the gain follows a parabola in it, so from a to b the level changes by 40 * log10(b / a) dB
(getFinalParameterValueVolume() in util/functions.cpp). Song and kit have 0 dB at 35.36 (0x3504F334), synths and rows
at 25; their default is 40 (0x4CCCCCA8). The only hard limit is the output's clip at 0 dBFS.

Usage (Windows: py instead of python3):
  python3 baseline_check.py PATH... [--card ROOT] [--out REPORT.md]
  PATH: the card's root (with SONGS in it), a folder of songs or song files. Samples are looked up in the card's root:
  the folder that holds SONGS, or ROOT. The report goes to the console and, with --out, into a file (UTF-8).
Needs Python 3.8 or newer and nothing else.
"""
import argparse
import array
import math
import os
import re
import struct
import sys
import unicodedata

KNOB_STEP = 85899345  # One step of a 0-50 knob as a param value (value_scaling.cpp)
SONG_KIT_LIMIT = 889516852  # 0x3504F334, "35": the default and 0 dB of song, kit and audio track volume
SOUND_LIMIT = 1288490152  # 0x4CCCCCA8, "40": the default of synth and kit row volume
PARAM_MIN = -2 ** 31  # A filter's resonance or morph and a delay's feedback at 0
LPF_OPEN = 2147483602  # From here up the low-pass filter is off (GlobalEffectable::getFilterModesForRender())
RESONANCE_HINT = 25  # Resonance knob value from which an active filter is reported
EQUIVALENT_TOLERANCE = 0.4  # A full-scale sample at 40 must not be reported for rounding


# --------------------------------------------------------------------------------------------------------------------
# The XML: the same tolerant reading as retune_library.py. The Deluge writes attributes twice at times, and the paths
# in CP437 bytes (its FAT has code page 437 and no Unicode API): the text is read as UTF-8 with surrogateescape.


class Node:
    __slots__ = ("name", "attrs", "children", "text", "parent")

    def __init__(self, name, parent):
        self.name, self.parent = name, parent
        self.attrs = {}
        self.children = []
        self.text = None

    def get(self, name):
        """A value as the firmware's readTagOrAttributeValue() finds it: attribute or child tag."""
        if name in self.attrs:
            return self.attrs[name]
        c = self.child(name)
        return None if c is None else (c.text or "")

    def child(self, name):
        return next((c for c in self.children if c.name == name), None)

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()


TOKEN = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<![^>]*>|<(/?)([^\s/>]+)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>",
                   re.S)
ATTR = re.compile(r"([^\s=/]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


def parse_xml(text):
    root = Node("#root", None)
    cur, pos = root, 0
    for m in TOKEN.finditer(text):
        if m.start() > pos and cur.text is None and not cur.children and text[pos:m.start()].strip():
            cur.text = text[pos:m.start()].strip()
        pos = m.end()
        if m.group(2) is None:
            continue
        closing, name, rest = m.group(1), m.group(2), m.group(3)
        if closing:
            if cur.name != name:
                raise ValueError(f"</{name}> schliesst <{cur.name}>")
            cur = cur.parent
            continue
        node = Node(name, cur)
        for a in ATTR.finditer(rest):
            node.attrs[a.group(1)] = a.group(2) if a.group(2) is not None else a.group(3)
        cur.children.append(node)
        if not rest.rstrip().endswith("/"):
            cur = node
    if cur is not root:
        raise ValueError(f"<{cur.name}> nicht geschlossen")
    return root


def readable(s):
    """Report text: bytes that aren't UTF-8 (surrogates) shown as the firmware reads them, as CP437."""
    return re.sub("[\udc80-\udcff]+", lambda m: m.group().encode("utf-8", "surrogateescape").decode("cp437"),
                  s or "").strip()


def param_values(text):
    """A param as the XML holds it: its value, then with automation (value, position) per node
    (AutoParam::writeToFile()). All values, signed; [] if it isn't a param."""
    if text is None:
        return []
    text = text.strip()
    try:
        if text[:2].lower() == "0x" and len(text) > 10:
            words = [int(text[i:i + 8], 16) for i in range(2, len(text) - 7, 8)]
            values = words[:1] + words[1::2]
        else:
            values = [int(text, 16 if text[:2].lower() == "0x" else 10) & 0xFFFFFFFF]
    except ValueError:
        return []
    return [v - 2 ** 32 if v >= 2 ** 31 else v for v in values]


def loudest(node, name):
    """The largest value a param reaches (automation included), or None."""
    values = param_values(node.get(name)) if node is not None else []
    return max(values) if values else None


def knob(param):
    """The 0-50 value the Deluge shows for a volume-like param."""
    return (param + 2 ** 31) / KNOB_STEP


def unipolar_knob(param):
    """The 0-50 value of a param that starts at 0 (compressor threshold, value_scaling.cpp)."""
    return max(param, 0) * 50 / 2 ** 31


def db_between(a, b):
    return 40 * math.log10(b / a) if a > 0 and b > 0 else -math.inf


def num(x, digits=1):
    return f"{round(x, digits) + 0.0:.{digits}f}".replace(".", ",")  # + 0.0: no "-0,0"


def signed_db(x):
    return ("+" if x >= 0 else "") + num(x) + " dB"


# --------------------------------------------------------------------------------------------------------------------
# Samples: their peak as the firmware reads the file (AudioFile::loadFile(), mirrored in retune_library.py)


def _chunks(fh, size, size_format):
    pos = 12
    while pos + 8 <= size:
        fh.seek(pos)
        h = fh.read(8)
        cid, n = h[:4], struct.unpack(size_format, h[4:])[0]
        yield cid, pos + 8, max(0, min(n, size - pos - 8))
        pos += 8 + n + (n & 1)


def _block_peak(b, bits, is_float, big):
    """The peak of raw audio, as a fraction of full scale."""
    if bits == 8:
        a = array.array("b" if big else "B", b)  # AIFF: signed, WAV: offset by 128
        return max(max(a), -min(a)) / 128 if big else max(max(a) - 128, 128 - min(a)) / 128
    if bits == 24:  # To 32 bits: the three bytes on top of a zero byte
        w = bytearray(len(b) // 3 * 4)
        order = (2, 1, 0) if big else (0, 1, 2)
        for i, j in enumerate(order):
            w[i + 1::4] = b[j::3]
        a = array.array("i", bytes(w))
        if sys.byteorder != "little":
            a.byteswap()
        return max(max(a), -min(a)) / 2 ** 31
    a = array.array("f" if is_float else ("h" if bits == 16 else "i"), b)
    if big != (sys.byteorder == "big"):
        a.byteswap()
    if is_float:
        return min(max(abs(max(a)), abs(min(a))), 1.0)  # The firmware limits float samples to full scale
    return max(max(a), -min(a)) / 2 ** (bits - 1)


def sample_peak(path, start=0, end=None):
    """(peak as a fraction of full scale, None) or (None, reason) for frames [start, end) of a WAV or AIFF file."""
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        head = fh.read(12)
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            fmt = data = None
            for cid, at, n in _chunks(fh, size, "<I"):
                if cid == b"fmt ":
                    fh.seek(at)
                    fmt = fh.read(min(n, 40))
                elif cid == b"data":
                    data = (at, n)
            if fmt is None or data is None or len(fmt) < 16:
                return None, "kein fmt oder data"
            tag, channels, _, _, align, bits = struct.unpack_from("<HHIIHH", fmt)
            if tag == 0xFFFE:
                return None, "WAVE_FORMAT_EXTENSIBLE, das liest der Deluge nicht"
            is_float, big = tag == 3, False
            if not ((tag == 1 and bits in (8, 16, 24, 32)) or (is_float and bits == 32)):
                return None, f"Format {tag} mit {bits} Bit, das liest der Deluge nicht"
        elif head[:4] == b"FORM" and head[8:12] == b"AIFF":
            comm = ssnd = None
            for cid, at, n in _chunks(fh, size, ">I"):
                if cid == b"COMM":
                    fh.seek(at)
                    comm = fh.read(min(n, 18))
                elif cid == b"SSND" and n >= 8:
                    fh.seek(at)
                    offset = struct.unpack(">I", fh.read(8)[:4])[0]
                    ssnd = (at + 8 + offset, max(0, n - 8 - offset))
            if comm is None or ssnd is None or len(comm) < 8:
                return None, "kein COMM oder SSND"
            channels, _, bits = struct.unpack_from(">hIh", comm)
            if bits not in (8, 16, 24, 32):
                return None, f"AIFF mit {bits} Bit"
            is_float, big, data = False, True, ssnd
            align = channels * bits // 8
        else:
            return None, "kein WAV oder AIFF, das liest der Deluge nicht"
        if channels not in (1, 2) or align != channels * bits // 8:
            return None, f"{channels} Kanäle, das liest der Deluge nicht"
        frames = data[1] // align
        start = min(max(start or 0, 0), frames)
        end = frames if end is None or end <= start else min(end, frames)
        fh.seek(data[0] + start * align)
        left, peak = (end - start) * align, 0.0
        block = max(align, (1 << 22) // align * align)
        while left > 0:
            b = fh.read(min(left, block))
            b = b[:len(b) // align * align]
            if not b:
                break
            peak = max(peak, _block_peak(b, bits, is_float, big))
            left -= len(b)
        return peak, None


class Card:
    """The card's files by path as FAT compares them (case ignored), read on first use."""

    def __init__(self, root):
        self.root = root
        self.index = None
        self.peaks = {}

    @staticmethod
    def key(rel):
        return unicodedata.normalize("NFC", rel.replace("\\", "/")).lower()

    def find(self, value):
        """The file an XML path names, or None. The path as UTF-8 first, else as CP437 (see readable())."""
        if self.index is None:
            self.index = {}
            for d, _, names in os.walk(self.root):
                for n in names:
                    full = os.path.join(d, n)
                    self.index[self.key(os.path.relpath(full, self.root))] = full
        raw = value.lstrip("/").encode("utf-8", "surrogateescape")
        for encoding in ("utf-8", "cp437"):
            try:
                found = self.index.get(self.key(raw.decode(encoding)))
            except UnicodeDecodeError:
                continue
            if found:
                return found
        return None

    def peak(self, value, start, end):
        """(peak, None) or (None, reason) of a sample the XML names, within [start, end)."""
        path = self.find(value)
        if path is None:
            return None, "Sample fehlt: " + readable(value)
        k = (path, start, end)
        if k not in self.peaks:
            try:
                self.peaks[k] = sample_peak(path, start, end)
            except (OSError, struct.error) as e:
                self.peaks[k] = (None, f"nicht lesbar ({getattr(e, 'strerror', None) or e})")
        return self.peaks[k]


# --------------------------------------------------------------------------------------------------------------------
# One song


class Track:
    def __init__(self, kind, name):
        self.kind, self.name = kind, name  # kind: "kit", "synth" or "audio"
        self.volume = None  # The loudest over its clips
        self.params = []  # kitParams / soundParams / params of every clip
        self.rows = {}  # drumIndex -> its soundParams in every clip


def track_key(node, prefix):
    """How a clip names its instrument (prefix "instrumentPreset") and an instrument itself ("preset"): the name, or
    the old numbered slot."""
    name = node.get(prefix + "Name")
    if name:
        return name
    slot = node.get(prefix + "Slot")
    return f"#{slot}.{node.get(prefix + 'SubSlot') or ''}" if slot is not None else "?"


def more(a, b):
    return b if a is None else a if b is None else max(a, b)


def check_song(root, card):
    """(settings, notes): what the song has, and its notes against the baseline, as report lines."""
    song = root.child("song")
    if song is None:
        raise ValueError("kein <song>")
    instruments = song.child("instruments")
    kits, synths, audio = {}, {}, {}
    for inst in (instruments.children if instruments is not None else []):
        if inst.name == "kit":
            kits[track_key(inst, "preset")] = inst
        elif inst.name == "sound":
            synths[track_key(inst, "preset")] = inst
        elif inst.name == "audioTrack":
            audio[inst.get("name") or "?"] = inst

    tracks = {}
    for clip in song.iter():
        if clip.name == "instrumentClip":
            key = track_key(clip, "instrumentPreset")
            if clip.child("kitParams") is not None:
                t = tracks.setdefault(("kit", key), Track("kit", key))
                p = clip.child("kitParams")
                rows = clip.child("noteRows")
                for nr in (rows.children if rows is not None else []):
                    sp = nr.child("soundParams")
                    index = nr.get("drumIndex")
                    if sp is None or index is None or not index.isdigit():
                        continue
                    t.rows.setdefault(int(index), []).append(sp)
            elif clip.child("soundParams") is not None:
                t = tracks.setdefault(("synth", key), Track("synth", key))
                p = clip.child("soundParams")
            else:
                continue  # MIDI and CV: no audio
        elif clip.name == "audioClip":
            key = clip.get("trackName") or "?"
            t = tracks.setdefault(("audio", key), Track("audio", key))
            p = clip.child("params")
        else:
            continue
        if p is not None:
            t.params.append(p)
            t.volume = more(t.volume, loudest(p, "volume"))

    notes, stages = [], []
    params = song.child("songParams")
    settings = {"song": loudest(params, "volume"), "compressor": loudest(params, "compressorThreshold")}
    if settings["song"] is not None and settings["song"] > SONG_KIT_LIMIT:
        k = knob(settings["song"])
        notes.append(f"Song-Lautstärke {round(k)}: {signed_db(db_between(knob(SONG_KIT_LIMIT), k))} über dem Standard 35")
    if settings["compressor"] is not None and settings["compressor"] > 0:
        notes.append(f"Master-Kompressor an (Threshold {round(unipolar_knob(settings['compressor']))})")
    stages += effect_stages(song, [params] if params is not None else [], "im Master", with_saturation=False)

    loudest_sound = loudest_group = None
    for (kind, key), t in sorted(tracks.items(), key=lambda kv: (["kit", "synth", "audio"].index(kv[0][0]), kv[0][1])):
        inst = {"kit": kits, "synth": synths, "audio": audio}[kind].get(key)
        label = {"kit": "Kit", "synth": "Synth", "audio": "Audio-Spur"}[kind] + " «" + readable(key) + "»"
        if kind in ("kit", "audio"):
            loudest_group = more(loudest_group, t.volume)
            if t.volume is not None and t.volume > SONG_KIT_LIMIT:
                k = knob(t.volume)
                notes.append(f"{label}: {round(k)}, {signed_db(db_between(knob(SONG_KIT_LIMIT), k))} über 35")
            where = "im Kit" if kind == "kit" else "in der Audio-Spur"
            stages += effect_stages(inst, t.params, f"{where} «{readable(key)}»")
        if kind == "synth":
            loudest_sound = more(loudest_sound, t.volume)
            notes += sound_notes(label, inst, t.params, card)
            if any((loudest(p, "compressorThreshold") or 0) > 0 for p in t.params):
                stages.append(f"Kompressor nach dem Regler von {label}")
        if kind == "kit":
            sources = inst.child("soundSources") if inst is not None else None
            drums = sources.children if sources is not None else []
            for index, rows in sorted(t.rows.items()):
                drum = drums[index] if index < len(drums) else None
                if drum is not None and drum.name != "sound":
                    continue  # MIDI and gate rows: no audio
                name = readable(drum.get("name")) if drum is not None else f"Reihe {index + 1}"
                for p in rows:
                    loudest_sound = more(loudest_sound, loudest(p, "volume"))
                notes += sound_notes(f"Reihe «{name}» in {label}", drum, rows, card)
                if any((loudest(p, "compressorThreshold") or 0) > 0 for p in rows):
                    stages.append(f"Kompressor nach dem Regler der Reihe «{name}» in {label}")
    settings["group"], settings["sound"] = loudest_group, loudest_sound
    if stages:
        notes.append("Stufen nach den Reglern, die mit dem Pegel stärker verzerren: " + "; ".join(stages))
    return settings, notes


def sound_notes(label, sound, params, card):
    """A synth's or row's volume above 40, unless all it plays is samples quiet enough for it."""
    volume = None
    for p in params:
        volume = more(volume, loudest(p, "volume"))
    if volume is None or volume <= SOUND_LIMIT:
        return []
    k = knob(volume)
    over = f"{label}: {round(k)}, {signed_db(db_between(40, k))} über 40"
    samples, other = sources(sound, params)
    if other or not samples:
        return [over]  # An oscillator, noise or FM sound: no sample to go by
    peaks, problems = [], []
    for path, start, end in samples:
        peak, error = card.peak(path, start, end)
        (problems if peak is None else peaks).append(error if peak is None else peak)
    if problems:
        return [over + " (" + "; ".join(sorted(set(problems))) + ")"]
    peak = max(peaks)
    equivalent = k * math.sqrt(peak)  # The gain goes with the knob squared: a peak p is the knob times sqrt(p)
    if equivalent <= 40 + EQUIVALENT_TOLERANCE:
        return []
    return [f"{label}: {round(k)}, Sample bis {num(20 * math.log10(peak))} dBFS, wirkt wie {num(equivalent)}"]


def sources(sound, params):
    """(the samples a sound plays as (path, start, end), whether anything else sounds: an oscillator, noise, FM).
    An oscillator counts if its level reaches above 0 in any clip, or isn't saved."""
    if sound is None or sound.name != "sound" or (sound.get("mode") or "subtractive") != "subtractive":
        return [], True
    samples, other = [], False
    for osc, level in (("osc1", "oscAVolume"), ("osc2", "oscBVolume")):
        node = sound.child(osc)
        levels = [loudest(p, level) for p in params]
        if node is None or not any(v is None or v > PARAM_MIN for v in levels or [None]):
            continue
        ranges = [r for g in node.children if g.name == "sampleRanges" for r in g.children]
        holders = [h for h in [node] + ranges if h.get("fileName")]
        if node.get("type") not in (None, "sample") or not holders:
            other = True
            continue
        for h in holders:
            zone = h.child("zone")
            pos = [param_values(zone.get(k)) if zone is not None else [] for k in ("startSamplePos", "endSamplePos")]
            samples.append((h.get("fileName"), pos[0][0] if pos[0] else 0, pos[1][0] if pos[1] else None))
    if any((loudest(p, "noiseVolume") or PARAM_MIN) > PARAM_MIN for p in params):
        other = True
    return samples, other


def effect_stages(owner, params, where, with_saturation=True):
    """The stages of a kit, an audio track or the song that distort the more, the louder it comes in."""
    found = []
    if owner is None:
        return found
    clipping = owner.get("clippingAmount")
    if with_saturation and clipping and clipping.strip().isdigit() and int(clipping) > 0:
        found.append(f"SATURATION {int(clipping)} {where}")
    if with_saturation and any((loudest(p, "compressorThreshold") or 0) > 0 for p in params):
        found.append(f"Kompressor {where}")
    delay = owner.child("delay")
    feedback = [loudest(p.child("delay"), "feedback") for p in params] + [loudest(p, "delayFeedback") for p in params]
    if delay is not None and delay.get("analog") == "1" and any(f is not None and f > PARAM_MIN for f in feedback):
        found.append(f"Analog-Delay mit Feedback {where}")
    for kind, name in (("lpf", "Tiefpass"), ("hpf", "Hochpass")):
        drive = kind == "lpf" and owner.get("lpfMode") == "24dBDrive"
        for p in params:
            f = p.child(kind)
            freqs = param_values(f.get("frequency")) if f is not None else []
            morph = loudest(p, kind + "Morph")
            if kind == "lpf":  # As getFilterModesForRender(): on unless fully open, always in drive mode
                active = drive or (freqs and min(freqs) < LPF_OPEN)
            else:
                active = freqs and max(freqs) > PARAM_MIN
            active = active or (morph is not None and morph > PARAM_MIN)
            resonance = loudest(f, "resonance") if f is not None else None
            if active and drive:
                found.append(f"{name} mit Drive {where}")
                break
            if active and resonance is not None and knob(resonance) >= RESONANCE_HINT:
                found.append(f"{name} mit Resonanz {round(knob(resonance))} {where}")
                break
    return found


# --------------------------------------------------------------------------------------------------------------------
# The card


def song_files(paths, card=None):
    """(song file, card root) for every PATH: a card root, a folder of songs or a song file. The card root is card if
    given, else the folder that holds SONGS, else the folder above the song's."""
    found = []
    for path in paths:
        path = os.path.abspath(path)
        if os.path.isfile(path):
            files = [path]
        else:
            songs = next((os.path.join(path, d) for d in os.listdir(path) if d.upper() == "SONGS" and
                          os.path.isdir(os.path.join(path, d))), path)
            files = sorted((os.path.join(d, n) for d, _, names in os.walk(songs) for n in names
                            if n.upper().endswith(".XML") and not n.startswith("._")), key=str.lower)
        for f in files:
            root, d = None, os.path.dirname(f)
            while True:  # The card's root: the folder that holds SONGS
                if os.path.basename(d).upper() == "SONGS":
                    root = os.path.dirname(d)
                    break
                if os.path.dirname(d) == d:
                    break
                d = os.path.dirname(d)
            found.append((f, os.path.abspath(card) if card else root or os.path.dirname(os.path.dirname(f))))
    return found


def report(files):
    cards, rows, sections, with_notes = {}, [], [], 0
    for path, root in files:
        name = os.path.splitext(os.path.basename(path))[0]
        card = cards.setdefault(root, Card(root))
        try:
            with open(path, "rb") as fh:
                settings, notes = check_song(parse_xml(fh.read().decode("utf-8", "surrogateescape")), card)
        except (OSError, ValueError) as e:
            rows.append(f"| {name} | - | - | - | - | nicht lesbar |")
            sections.append(f"## {name}\n\nNicht lesbar: {e}\n")
            with_notes += 1
            continue
        s, c, g, v = settings["song"], settings["compressor"], settings["group"], settings["sound"]
        rows.append(f"| {name} | {'-' if s is None else round(knob(s))} | "
                    f"{'an' if c is not None and c > 0 else 'aus'} | {'-' if g is None else round(knob(g))} | "
                    f"{'-' if v is None else round(knob(v))} | {len(notes) or 'ok'} |")
        if notes:
            with_notes += 1
            sections.append(f"## {name}\n\n" + "\n".join("- " + n for n in notes) + "\n")
    head = (f"# Baseline-Prüfung: {len(files)} Songs, {with_notes} mit Hinweisen\n\n"
            "Grenzen (Baseline Master, 27.09.2026): Song, Kit und Audio-Spur höchstens 35, Synth und Kit-Reihe höchstens "
            "40, lauter nur mit leisem Sample: «wirkt wie» ist der Regler, den ein voll ausgesteuertes Sample für "
            "denselben Pegel bräuchte (Regler mal 10^(Spitze/40)), höchstens 40. Master-Kompressor aus. Mit "
            "Automation zählt der lauteste Punkt. Ob ein Song clippt, zeigt nur das Messen: In DelugeRec bleibt das Pad "
            "-3 dunkel.\n\n"
            "| Song | Song-Lautstärke | Master-Kompressor | lautestes Kit, Audio | lautester Synth, Reihe | Hinweise |\n"
            "|---|---|---|---|---|---|\n")
    return head + "\n".join(rows) + "\n" + ("\n" + "\n".join(sections) if sections else "")


def main():
    ap = argparse.ArgumentParser(description="Prüft Deluge-Songs gegen die Baseline Master (nur lesend).")
    ap.add_argument("paths", nargs="+", metavar="PATH", help="Karte, Ordner mit Songs oder Song-Dateien")
    ap.add_argument("--card", help="wo die Samples liegen, wenn die Songs nicht auf der Karte sind: deren Wurzel")
    ap.add_argument("--out", help="den Bericht auch in diese Datei schreiben (UTF-8, Markdown)")
    args = ap.parse_args()
    files = song_files(args.paths, args.card)
    if not files:
        ap.error("keine Songs gefunden (SONGS/*.XML)")
    text = report(files)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(text, end="")
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


if __name__ == "__main__":
    main()
