#!/usr/bin/env python3
"""Checks every song on a Deluge card against the baseline master (device/analysis/2026-09-27-baseline-master.md).

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
From mastertune v18 on the Deluge shows these volumes in dB and steps them by 0.5 dB (modulation/params/
volume_steps.cpp, patch 0101): 40 log10((p + 2^31) / 2^31), a kit's, an audio track's and the song's 6.02 dB lower
(20 log10 2, their >> 1). What it stores stays the same, but a value is no longer on the 0-50 steps: the report shows
every volume as its value to 0.1 and in these dB (Song 35.4 = 0.00 dB, Synth 40 = +8.16 dB), and judges it as it is.

Usage (Windows: py instead of python3):
  python3 baseline_check.py PATH... [--card ROOT] [--out REPORT.md] [--lang de|en]
  PATH: the card's root (with SONGS in it), a folder of songs or song files. Samples are looked up in the card's root:
  the folder that holds SONGS, or ROOT. The report goes to the console and, with --out, into a file (UTF-8), in the
  computer's language (German if it is German, else English; system_language()) or as --lang says.
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
HALF_DB = 20 * math.log10(2)  # A kit's, an audio track's and the song's volume in dB: 6.02 lower (mastertune v18)
PARAM_MIN = -2 ** 31  # A filter's resonance or morph and a delay's feedback at 0
LPF_OPEN = 2147483602  # From here up the low-pass filter is off (GlobalEffectable::getFilterModesForRender())
RESONANCE_HINT = 25  # Resonance knob value from which an active filter is reported
# How far above a limit still counts as at it: less than the report shows (0.01 dB), so it never says "+0,00 dB
# above". Nothing is rounded to a step: a value between them counts as it is.
TOLERANCE_DB = 0.005
LANG = "de"  # The language of every text it makes: "de" or "en" (the command line and DelugeBaseline set it)


def t(de, en):
    """A text in the language set: German or English."""
    return en if LANG == "en" else de


def system_language():
    """de if the computer speaks German, else en: the language to start with, on the command line and in
    DelugeBaseline's window until one is chosen there. Windows: the language of its user interface; macOS and Linux:
    the locale (LC_ALL, LC_MESSAGES, LANG, the first one set, else Python's locale). English where it can't be read."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            return "de" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x07 else "en"  # LANG_GERMAN
        code = next((os.environ[k] for k in ("LC_ALL", "LC_MESSAGES", "LANG") if os.environ.get(k)), None)
        if code is None:
            import locale
            code = locale.getlocale()[0] or ""
        return "de" if code.lower().startswith("de") else "en"
    except Exception:
        return "en"


# --------------------------------------------------------------------------------------------------------------------
# The XML: the same tolerant reading as retune_library.py. The Deluge writes attributes twice at times, and the paths
# in CP437 bytes (its FAT has code page 437 and no Unicode API): the text is read as UTF-8 with surrogateescape.


class Node:
    __slots__ = ("name", "attrs", "spans", "children", "text", "text_span", "parent", "name_end")

    def __init__(self, name, parent, name_end=0):
        self.name, self.parent, self.name_end = name, parent, name_end  # name_end: where attributes can be added
        self.attrs = {}
        self.spans = {}  # Attribute -> (start, end) of its value in the text
        self.children = []
        self.text = None
        self.text_span = None

    def get(self, name):
        """A value as the firmware's readTagOrAttributeValue() finds it: attribute or child tag."""
        if name in self.attrs:
            return self.attrs[name]
        c = self.child(name)
        return None if c is None else (c.text or "")

    def span(self, name):
        """(start, end) of the value get() finds, in the text, or None (a child tag without text has none)."""
        if name in self.spans:
            return self.spans[name]
        c = self.child(name)
        return c.text_span if c is not None else None

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
            raw = text[pos:m.start()]
            cur.text = raw.strip()
            lead = pos + len(raw) - len(raw.lstrip())
            cur.text_span = (lead, lead + len(cur.text))
        pos = m.end()
        if m.group(2) is None:
            continue
        closing, name, rest = m.group(1), m.group(2), m.group(3)
        if closing:
            if cur.name != name:
                raise ValueError(t(f"</{name}> schliesst <{cur.name}>", f"</{name}> closes <{cur.name}>"))
            cur = cur.parent
            continue
        node = Node(name, cur, m.end(2))
        base = m.start(3)
        for a in ATTR.finditer(rest):
            g = 2 if a.group(2) is not None else 3
            node.attrs[a.group(1)] = a.group(g)
            node.spans[a.group(1)] = (base + a.start(g), base + a.end(g))  # The last of a twice written one counts
        cur.children.append(node)
        if not rest.rstrip().endswith("/"):
            cur = node
    if cur is not root:
        raise ValueError(t(f"<{cur.name}> nicht geschlossen", f"<{cur.name}> not closed"))
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
    s = f"{round(x, digits) + 0.0:.{digits}f}"  # + 0.0: no "-0,0"
    return s if LANG == "en" else s.replace(".", ",")


def signed_db(x, digits=1):
    return ("+" if x >= 0 else "") + num(x, digits) + " dB"


def volume_db(param, song_kit=False):
    """The dB mastertune v18 shows for a stored volume (volume::storedToDb() in modulation/params/volume_steps.cpp):
    40 log10((p + 2^31) / 2^31), a kit's, an audio track's and the song's (song_kit) 6.02 dB lower. -inf for off."""
    x = (param + 2 ** 31) / 2 ** 31
    return 40 * math.log10(x) - (HALF_DB if song_kit else 0.0) if x > 0 else -math.inf


def knob_db(k, song_kit=False):
    """The same for a knob's value k (0-50, maybe between the steps): 40 log10(k / 25), song_kit 6.02 dB lower."""
    return volume_db(k * 2 ** 31 / 25 - 2 ** 31, song_kit)


def db_text(db):
    """dB to 0.01 (the Deluge shows 0.1): +8,16 dB, 0,00 dB, -0,18 dB; -inf dB at -100 dB and below, as off."""
    if db <= -99.995:
        return "-inf dB"
    return ("+" if round(db, 2) > 0 else "") + num(db, 2) + " dB"


def above(param, limit, song_kit=False):
    """How many dB a volume is above its limit (as the Deluge shows dB from mastertune v18 on), or None if not."""
    over = volume_db(param, song_kit) - volume_db(limit, song_kit)
    return over if over > TOLERANCE_DB else None


def level(param, song_kit=False):
    """A volume as it is: its value to 0.1, never rounded to a whole step (mastertune v18 steps by 0.5 dB, between
    them), and in dB as the Deluge shows it from v18 on. 40,0 (+8,16 dB)"""
    return level_of_knob(knob(param), song_kit)


def level_of_knob(k, song_kit=False):
    return f"{num(k)} ({db_text(knob_db(k, song_kit))})"


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


def audio_format(fh):
    """Where the audio of an open WAV or AIFF file is and how the firmware reads it: {"pos", "size", "channels",
    "bits", "float", "big"}, or the reason (text) why the Deluge can't read it."""
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    fh.seek(0)
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
            return t("kein fmt oder data", "no fmt or data")
        tag, channels, _, _, align, bits = struct.unpack_from("<HHIIHH", fmt)
        if tag == 0xFFFE:
            return "WAVE_FORMAT_EXTENSIBLE, " + t("das liest der Deluge nicht", "the Deluge can't read it")
        if not ((tag == 1 and bits in (8, 16, 24, 32)) or (tag == 3 and bits == 32)):
            return t(f"Format {tag} mit {bits} Bit, das liest der Deluge nicht",
                     f"format {tag} with {bits} bits, the Deluge can't read it")
        is_float, big = tag == 3, False
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
            return t("kein COMM oder SSND", "no COMM or SSND")
        channels, _, bits = struct.unpack_from(">hIh", comm)
        if bits not in (8, 16, 24, 32):
            return t(f"AIFF mit {bits} Bit", f"AIFF with {bits} bits")
        is_float, big, data = False, True, ssnd
        align = channels * bits // 8
    else:
        return t("kein WAV oder AIFF, das liest der Deluge nicht", "no WAV or AIFF, the Deluge can't read it")
    if channels not in (1, 2) or align != channels * bits // 8:
        return t(f"{channels} Kanäle, das liest der Deluge nicht", f"{channels} channels, the Deluge can't read it")
    return {"pos": data[0], "size": data[1] // align * align, "channels": channels, "bits": bits, "float": is_float,
            "big": big}


def sample_peak(path, start=0, end=None):
    """(peak as a fraction of full scale, None) or (None, reason) for frames [start, end) of a WAV or AIFF file."""
    with open(path, "rb") as fh:
        f = audio_format(fh)
        if isinstance(f, str):
            return None, f
        align = f["channels"] * f["bits"] // 8
        frames = f["size"] // align
        start = min(max(start or 0, 0), frames)
        end = frames if end is None or end <= start else min(end, frames)
        fh.seek(f["pos"] + start * align)
        left, peak = (end - start) * align, 0.0
        block = max(align, (1 << 22) // align * align)
        while left > 0:
            b = fh.read(min(left, block))
            b = b[:len(b) // align * align]
            if not b:
                break
            peak = max(peak, _block_peak(b, f["bits"], f["float"], f["big"]))
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
            return None, t("Sample fehlt: ", "sample missing: ") + readable(value)
        k = (path, start, end)
        if k not in self.peaks:
            try:
                self.peaks[k] = sample_peak(path, start, end)
            except (OSError, struct.error) as e:
                self.peaks[k] = (None, t("nicht lesbar", "unreadable") + f" ({getattr(e, 'strerror', None) or e})")
        return self.peaks[k]


# --------------------------------------------------------------------------------------------------------------------
# One song


class Track:
    def __init__(self, kind, name):
        self.kind, self.name = kind, name  # kind: "kit", "synth" or "audio"
        self.volume = None  # The loudest over its clips
        self.params = []  # kitParams / soundParams / params of every clip
        self.rows = {}  # drumIndex -> its soundParams in every clip
        self.played = set()  # drumIndexes with notes in some clip


def track_key(node, prefix):
    """How a clip names its instrument (prefix "instrumentPreset") and an instrument itself ("preset"): the name, or
    the old numbered slot."""
    name = node.get(prefix + "Name")
    if name:
        return name
    slot = node.get(prefix + "Slot")
    return f"#{slot}.{node.get(prefix + 'SubSlot') or ''}" if slot is not None else "?"


def instrument_key(node, prefix, kind):
    """How the firmware joins a clip to its instrument (Song::getInstrumentFromPresetSlot()): kind, name and folder,
    case ignored. Songs from before V4.0.0 name no folder: SYNTHS or KITS."""
    folder = node.get(prefix + "Folder")
    if folder is None:
        folder = "KITS" if kind == "kit" else "SYNTHS"
    return kind, track_key(node, prefix).lower(), folder.lower()


def has_notes(row):
    """Whether a note row has notes (noteData, noteDataWithLift, ... or a notes tag of old files)."""
    return any(k.startswith("noteData") and v for k, v in row.attrs.items()) or any(
        c.name in ("notes", "noteData") for c in row.children)


def more(a, b):
    return b if a is None else a if b is None else max(a, b)


def check_song(root, card):
    """(settings, notes): what the song has, and its notes against the baseline, as report lines."""
    song = root.child("song")
    if song is None:
        raise ValueError(t("kein <song>", "no <song>"))
    instruments = song.child("instruments")
    kits, synths, audio = {}, {}, {}
    for inst in (instruments.children if instruments is not None else []):
        if inst.name == "kit":
            kits[instrument_key(inst, "preset", "kit")] = inst
        elif inst.name == "sound":
            synths[instrument_key(inst, "preset", "synth")] = inst
        elif inst.name == "audioTrack":
            audio[("audio", inst.get("name") or "?", "")] = inst

    tracks = {}  # instrument_key() -> Track
    for clip in song.iter():
        if clip.name == "instrumentClip":
            name = track_key(clip, "instrumentPreset")
            if clip.child("kitParams") is not None:
                tr = tracks.setdefault(instrument_key(clip, "instrumentPreset", "kit"), Track("kit", name))
                p = clip.child("kitParams")
                rows = clip.child("noteRows")
                for nr in (rows.children if rows is not None else []):
                    sp = nr.child("soundParams")
                    index = nr.get("drumIndex")
                    if sp is None or index is None or not index.isdigit():
                        continue
                    tr.rows.setdefault(int(index), []).append(sp)
                    if has_notes(nr):
                        tr.played.add(int(index))
            elif clip.child("soundParams") is not None:
                tr = tracks.setdefault(instrument_key(clip, "instrumentPreset", "synth"), Track("synth", name))
                p = clip.child("soundParams")
            else:
                continue  # MIDI and CV: no audio
        elif clip.name == "audioClip":
            name = clip.get("trackName") or "?"
            tr = tracks.setdefault(("audio", name, ""), Track("audio", name))
            p = clip.child("params")
        else:
            continue
        if p is not None:
            tr.params.append(p)
            tr.volume = more(tr.volume, loudest(p, "volume"))

    notes, stages = [], []
    params = song.child("songParams")
    settings = {"song": loudest(params, "volume"), "compressor": loudest(params, "compressorThreshold")}
    if settings["song"] is not None and above(settings["song"], SONG_KIT_LIMIT, True):
        now, top = level(settings["song"], True), level(SONG_KIT_LIMIT, True)
        over = signed_db(above(settings["song"], SONG_KIT_LIMIT, True), 2)
        notes.append(t(f"Song-Lautstärke {now}: {over} über dem Standard {top}",
                       f"Song volume {now}: {over} above the default {top}"))
    if settings["compressor"] is not None and settings["compressor"] > 0:
        threshold = round(unipolar_knob(settings["compressor"]))
        notes.append(t(f"Master-Kompressor an (Threshold {threshold})",
                       f"Master compressor on (threshold {threshold})"))
    stages += effect_stages(song, [params] if params is not None else [], t("im Master", "in the master"),
                            with_saturation=False)

    loudest_sound = loudest_group = None
    names = [(k[0], k[1]) for k in tracks]
    order = ["kit", "synth", "audio"]
    for key, tr in sorted(tracks.items(), key=lambda kv: (order.index(kv[0][0]), kv[1].name, kv[0])):
        kind = tr.kind
        inst = {"kit": kits, "synth": synths, "audio": audio}[kind].get(key)
        where = f" ({readable(key[2]).upper()})" if names.count(key[:2]) > 1 else ""  # Two alike but for the folder
        label = {"kit": "Kit", "synth": "Synth", "audio": t("Audio-Spur", "Audio track")}[kind] + " «" + \
            readable(tr.name) + "»" + where
        if kind in ("kit", "audio"):
            loudest_group = more(loudest_group, tr.volume)
            if tr.volume is not None and above(tr.volume, SONG_KIT_LIMIT, True):
                now, top = level(tr.volume, True), level(SONG_KIT_LIMIT, True)
                over = signed_db(above(tr.volume, SONG_KIT_LIMIT, True), 2)
                notes.append(t(f"{label}: {now}, {over} über {top}", f"{label}: {now}, {over} above {top}"))
            stages += effect_stages(inst, tr.params, t("im " if kind == "kit" else "in der ", "in ") + label)
        if kind == "synth":
            loudest_sound = more(loudest_sound, tr.volume)
            notes += sound_notes(label, inst, tr.params, card)
            if any((loudest(p, "compressorThreshold") or 0) > 0 for p in tr.params):
                stages.append(t(f"Kompressor nach dem Regler von {label}", f"compressor after the knob of {label}"))
        if kind == "kit":
            sources = inst.child("soundSources") if inst is not None else None
            drums = sources.children if sources is not None else []
            for index, rows in sorted(tr.rows.items()):
                drum = drums[index] if index < len(drums) else None
                if drum is not None and drum.name != "sound":
                    continue  # MIDI and gate rows: no audio
                name = readable(drum.get("name")) if drum is not None else t("Reihe", "row") + f" {index + 1}"
                for p in rows:
                    loudest_sound = more(loudest_sound, loudest(p, "volume"))
                notes += sound_notes(t(f"Reihe «{name}» in {label}", f"Row «{name}» in {label}"), drum, rows, card,
                                     "" if index in tr.played else t(", ohne Noten: klingt nur live gespielt",
                                                                     ", no notes: sounds only when played live"))
                if any((loudest(p, "compressorThreshold") or 0) > 0 for p in rows):
                    stages.append(t(f"Kompressor nach dem Regler der Reihe «{name}» in {label}",
                                    f"compressor after the knob of row «{name}» in {label}"))
    settings["group"], settings["sound"] = loudest_group, loudest_sound
    if stages:
        notes.append(t("Stufen nach den Reglern, die mit dem Pegel stärker verzerren: ",
                       "Stages after the knobs that distort the more, the louder it comes in: ") + "; ".join(stages))
    return settings, notes


def sound_notes(label, sound, params, card, suffix=""):
    """A synth's or row's volume above 40, unless all it plays is samples quiet enough for it."""
    volume = None
    for p in params:
        volume = more(volume, loudest(p, "volume"))
    if volume is None or not above(volume, SOUND_LIMIT):
        return []
    k = knob(volume)
    over = f"{label}: {level(volume)}, {signed_db(above(volume, SOUND_LIMIT), 2)} " + t("über ", "above ") + \
        level(SOUND_LIMIT)
    samples, other = sources(sound, params)
    if other or not samples:
        return [over + suffix]  # An oscillator, noise or FM sound: no sample to go by
    found, problems = sample_level(samples, card)  # (level() is the text of a volume)
    if problems:
        return [over + " (" + "; ".join(problems) + ")" + suffix]
    equivalent = k * found[0]
    if knob_db(equivalent) - volume_db(SOUND_LIMIT) <= TOLERANCE_DB:
        return []
    osc = f", Osc {num(found[2])}" if found[2] < 49.95 else ""
    peak = num(20 * math.log10(found[1]))
    over = signed_db(knob_db(equivalent) - volume_db(SOUND_LIMIT), 2)
    return [t(f"{label}: {level(volume)}, Sample bis {peak} dBFS{osc}, wirkt wie {level_of_knob(equivalent)}, "
              f"{over} über {level(SOUND_LIMIT)}",
              f"{label}: {level(volume)}, sample up to {peak} dBFS{osc}, as loud as {level_of_knob(equivalent)}, "
              f"{over} above {level(SOUND_LIMIT)}") + suffix]


def sample_level(samples, card):
    """((factor, peak, osc knob), problems) of the loudest sample: the factor makes a knob into the knob a full-scale
    sample needs for the same level. The gain goes with the knob squared, in the volume and in the oscillator's level:
    a peak p at osc level o is the knob times sqrt(p) * o / 50. ((0, 0, 50), problems) if a sample can't be read."""
    best, problems = (0.0, 0.0, 50.0), set()
    for path, start, end, osc in samples:
        peak, error = card.peak(path, start, end)
        if peak is None:
            problems.add(error)
        elif peak > 0 and math.sqrt(peak) * osc / 50 > best[0]:
            best = (math.sqrt(peak) * osc / 50, peak, osc)
    return best, sorted(problems)


def sources(sound, params):
    """(the samples a sound plays as (path, start, end, its oscillator's loudest level as a knob), whether anything
    else sounds: an oscillator, noise, FM). An oscillator counts if its level reaches above 0 in any clip, or isn't
    saved (then at the default, 50)."""
    if sound is None or sound.name != "sound" or (sound.get("mode") or "subtractive") != "subtractive":
        return [], True
    samples, other = [], False
    for osc, level in (("osc1", "oscAVolume"), ("osc2", "oscBVolume")):
        node = sound.child(osc)
        levels = [loudest(p, level) for p in params]
        if node is None or not any(v is None or v > PARAM_MIN for v in levels or [None]):
            continue
        osc_knob = 50.0 if not levels or None in levels else knob(max(levels))
        ranges = [r for g in node.children if g.name == "sampleRanges" for r in g.children]
        holders = [h for h in [node] + ranges if h.get("fileName")]
        if node.get("type") not in (None, "sample") or not holders:
            other = True
            continue
        for h in holders:
            zone = h.child("zone")
            pos = [param_values(zone.get(k)) if zone is not None else [] for k in ("startSamplePos", "endSamplePos")]
            samples.append((h.get("fileName"), pos[0][0] if pos[0] else 0, pos[1][0] if pos[1] else None, osc_knob))
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
        found.append(t("Kompressor", "compressor") + f" {where}")
    delay = owner.child("delay")
    feedback = [loudest(p.child("delay"), "feedback") for p in params] + [loudest(p, "delayFeedback") for p in params]
    if delay is not None and delay.get("analog") == "1" and any(f is not None and f > PARAM_MIN for f in feedback):
        found.append(t("Analog-Delay mit Feedback", "analog delay with feedback") + f" {where}")
    for kind, name in (("lpf", t("Tiefpass", "low-pass")), ("hpf", t("Hochpass", "high-pass"))):
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
                found.append(name + t(" mit Drive ", " with drive ") + where)
                break
            if active and resonance is not None and knob(resonance) >= RESONANCE_HINT:
                found.append(name + t(" mit Resonanz ", " with resonance ") + f"{round(knob(resonance))} {where}")
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


def check_all(files, progress=None):
    """Every song checked: dicts with name, path, settings (None if unreadable), notes, error."""
    cards, results = {}, []
    for i, (path, root) in enumerate(files):
        if progress:
            progress(i, len(files))
        name = os.path.splitext(os.path.basename(path))[0]
        card = cards.setdefault(root, Card(root))
        try:
            with open(path, "rb") as fh:
                settings, notes = check_song(parse_xml(fh.read().decode("utf-8", "surrogateescape")), card)
            results.append({"name": name, "path": path, "settings": settings, "notes": notes, "error": None})
        except (OSError, ValueError) as e:
            results.append({"name": name, "path": path, "settings": None, "notes": [], "error": str(e)})
    return results


def report(files, results=None):
    results = check_all(files) if results is None else results
    rows, sections, with_notes = [], [], 0
    for r in results:
        name = r["name"]
        if r["error"] is not None:
            rows.append(f"| {name} | - | - | - | - | " + t("nicht lesbar", "unreadable") + " |")
            sections.append(f"## {name}\n\n" + t("Nicht lesbar: ", "Unreadable: ") + f"{r['error']}\n")
            with_notes += 1
            continue
        settings, notes = r["settings"], r["notes"]
        s, c, g, v = settings["song"], settings["compressor"], settings["group"], settings["sound"]
        rows.append(f"| {name} | {'-' if s is None else level(s, True)} | "
                    f"{t('an', 'on') if c is not None and c > 0 else t('aus', 'off')} | "
                    f"{'-' if g is None else level(g, True)} | "
                    f"{'-' if v is None else level(v)} | {len(notes) or 'ok'} |")
        if notes:
            with_notes += 1
            sections.append(f"## {name}\n\n" + "\n".join("- " + n for n in notes) + "\n")
    n = len(results)
    head = t(f"# Baseline-Prüfung: {n} {'Song' if n == 1 else 'Songs'}, {with_notes} mit Hinweisen\n\n"
             "Grenzen (Baseline Master, 27.09.2026): Song, Kit und Audio-Spur höchstens 35,4 (0,00 dB), Synth und "
             "Kit-Reihe höchstens 40,0 (+8,16 dB), lauter nur mit leisem Sample: «wirkt wie» ist der Regler, den ein "
             "voll ausgesteuertes "
             "Sample für denselben Pegel bräuchte (Regler mal 10^(Spitze/40)), höchstens 40. Master-Kompressor aus. "
             "Mit Automation zählt der lauteste Punkt. Jeder Pegel steht als Reglerwert 0-50 und in dB wie am Deluge "
             "ab mastertune v18 (0,5 dB pro Raste, Werte dazwischen wie gespeichert). Ob ein Song clippt, zeigt nur "
             "das Messen: In DelugeRec bleibt das Pad -3 dunkel.\n\n"
             "| Song | Song-Lautstärke | Master-Kompressor | lautestes Kit, Audio | lautester Synth, Reihe | "
             "Hinweise |\n|---|---|---|---|---|---|\n",
             f"# Baseline check: {n} {'song' if n == 1 else 'songs'}, {with_notes} with notes\n\n"
             "Limits (baseline master, 2026-09-27): song, kit and audio track at most 35.4 (0.00 dB), synth and kit "
             "row at most 40.0 (+8.16 dB), louder only with a quiet sample: «as loud as» is the knob a full-scale "
             "sample would need for the same level (knob times 10^(peak/40)), at most 40. Master compressor off. With "
             "automation the loudest point counts. Every level as its knob value 0-50 and in dB as the Deluge shows "
             "it from mastertune v18 on (0.5 dB per detent, values between as stored). Whether a song clips only "
             "measuring shows: in DelugeRec the pad -3 stays dark.\n\n"
             "| Song | Song volume | Master compressor | loudest kit, audio | loudest synth, row | Notes |\n"
             "|---|---|---|---|---|---|\n")
    return head + "\n".join(rows) + "\n" + ("\n" + "\n".join(sections) if sections else "")


def main():
    global LANG
    ap = argparse.ArgumentParser(description="Checks Deluge songs against the baseline master (only reads).")
    ap.add_argument("paths", nargs="+", metavar="PATH", help="the card, a folder of songs or song files")
    ap.add_argument("--card", help="where the samples are when the songs aren't on the card: its root")
    ap.add_argument("--out", help="the report into this file too (UTF-8, Markdown)")
    ap.add_argument("--lang", choices=("de", "en"),
                    help="the report's language: German or English (default: the computer's language)")
    args = ap.parse_args()
    LANG = args.lang or system_language()
    files = song_files(args.paths, args.card)
    if not files:
        ap.error(t("keine Songs gefunden (SONGS/*.XML)", "no songs found (SONGS/*.XML)"))
    text = report(files)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print(text, end="")
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


if __name__ == "__main__":
    main()
