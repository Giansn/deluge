#!/usr/bin/env python3
"""Tests for tools/deluge_baseline.py (DelugeBaseline): levels, normalizing with compensation, restore, writing.

- Params: a knob scaled, automation's values alike and its positions kept, rounded down (never louder).
- Levels on the demo card: song, compressor, kit, the row with a loud sample; the quiet row and the multisample synth
  stay; only those values change, byte for byte the rest; a backup of the old file; into a folder the card stays;
  automation keeps its shape; a second run finds nothing; the check has no notes left.
- Normalizing: every format the firmware reads (WAV 8/16/24/32 bit and float, AIFF 8/16/24 bit) raised to the target
  exactly, every chunk and the header kept, nothing over full scale; target 0 dBFS; samples at the target stay; a
  multisample one gain; wavetables and audio clips stay; compensation keeps every sound's level (what the check calls
  «wirkt wie») the same, in songs and in KITS/ and SYNTHS/; without a saved oscillator level, with a patch cable to
  it, in FM, or with an unreadable XML on the card the samples stay; without compensation all are raised and no XML
  changes.
- Restore gives back the files byte for byte. The command line, the window's Markdown, the self-test (with tkinter).
- The card copy in geraet/karte: normalizing keeps every row's level, levels leave no notes but the missing sample.

Usage: python3 test_deluge_baseline.py   Needs numpy (and tkinter for the self-test, else it is skipped)
"""
import contextlib
import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import baseline_check as bc  # noqa: E402
import deluge_baseline as db  # noqa: E402

KARTE = Path(HERE, "..", "..", "geraet", "karte").resolve()
try:
    import tkinter  # noqa: F401
    HAS_TK = bool(os.environ.get("DISPLAY")) or sys.platform.startswith("win")
except ImportError:
    HAS_TK = False


def attrs_of(tree):
    """Every attribute of every node, by (path of indexes, name)."""
    out = {}

    def walk(node, path):
        for k, v in node.attrs.items():
            out[(path, node.name, k)] = v
        for i, c in enumerate(node.children):
            walk(c, path + (i,))
    walk(tree, ())
    return out


def changed_attrs(before, after):
    """(node, attribute, old, new) of every attribute that differs, in document order; no other difference."""
    a, b = attrs_of(bc.parse_xml(before)), attrs_of(bc.parse_xml(after))
    assert a.keys() == b.keys()
    return [(k[1], k[2], a[k], b[k]) for k in a if a[k] != b[k]]


def levels_of(root, rel):
    """«wirkt wie» of every sound of a song or preset: the knob a full-scale sample would need, per oscillator."""
    tree = bc.parse_xml(Path(root, rel).read_bytes().decode("utf-8", "surrogateescape"))
    card = bc.Card(str(root))
    out = {}
    for r in db.xml_refs(rel, tree)[0]:
        samples, _ = bc.sources(r.sound, r.params)
        level, problems = bc.sample_level([s for s in samples if s[3] is not None], card)
        out[r.label + r.osc] = level[0]
    return out


def chunk(cid, body):
    return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def wav(path, x, bits=16, tag=1, extra=b""):
    """A mono WAV of the integers (or floats) x, with extra chunks before the audio."""
    x = np.asarray(x)
    if tag == 3:
        raw = x.astype("<f4").tobytes()
    elif bits == 8:
        raw = (x + 128).astype(np.uint8).tobytes()
    elif bits == 24:
        v = (x.astype(np.int64) & 0xFFFFFF).astype(np.uint32)
        raw = np.stack([v & 0xFF, (v >> 8) & 0xFF, v >> 16], axis=1).astype(np.uint8).tobytes()
    else:
        raw = x.astype(f"<i{bits // 8}").tobytes()
    align = bits // 8
    fmt = struct.pack("<HHIIHH", tag, 1, 44100, 44100 * align, align, bits)
    body = b"WAVE" + chunk(b"fmt ", fmt) + extra + chunk(b"data", raw) + chunk(b"LIST", b"INFOISFT\x04\0\0\0test")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def aiff(path, x, bits=16):
    x = np.asarray(x).astype(np.int64)
    if bits == 8:
        raw = x.astype(np.int8).tobytes()
    elif bits == 24:
        v = (x & 0xFFFFFF).astype(np.uint32)
        raw = np.stack([v >> 16, (v >> 8) & 0xFF, v & 0xFF], axis=1).astype(np.uint8).tobytes()
    else:
        raw = x.astype(">i2").tobytes()
    comm = struct.pack(">hIh", 1, len(x), bits) + bytes.fromhex("400EAC44000000000000")
    ssnd = struct.pack(">II", 0, 0) + raw
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    body += b"NAME" + struct.pack(">I", 4) + b"test"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)


def signal(db_peak, bits, n=3000, float_=False):
    """A sine with its positive peak at db_peak and its negative peak 3 dB lower (asymmetric, as real samples)."""
    t = np.arange(n)
    s = np.sin(t * 0.031) + 0.2 * np.sin(t * 0.17)
    s = np.where(s > 0, s / s.max(), s / -s.min() * 10 ** (-3 / 20)) * 10 ** (db_peak / 20)
    return s.astype(np.float32) if float_ else np.round(s * (2 ** (bits - 1) - 1)).astype(np.int64)


def sound(name, path=None, mode="subtractive", osc1_type="sample", ranges=None, cable=None, defaults=None):
    osc = f'<osc1 type="{osc1_type}"' + (f' fileName="{path}"' if path else "") + ">"
    if ranges:
        osc += "<sampleRanges>" + "".join(f'<sampleRange fileName="{r}" />' for r in ranges) + "</sampleRanges>"
    osc += "</osc1>"
    extra = f'<patchCables><patchCable source="velocity" destination="{cable}" amount="0x20000000" /></patchCables>' \
        if cable else ""
    d = f"<defaultParams {defaults} />" if defaults else ""
    return f'<sound name="{name}" presetName="{name}" mode="{mode}">{osc}<osc2 type="square" />{extra}{d}</sound>'


def params(volume="0x4CCCCCA8", osc_a='oscAVolume="0x7FFFFFFF"', extra=""):
    return f'volume="{volume}" {osc_a} oscBVolume="0x80000000" noiseVolume="0x80000000" {extra}'


def song_text(kits="", synths="", clips="", song_params='volume="0x3504F334" compressorThreshold="0x00000000"'):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<song firmwareVersion="c1.2.1">\n<instruments>{kits}{synths}'
            f'</instruments>\n<songParams {song_params} />\n<sessionClips>{clips}</sessionClips>\n</song>\n')


def kit(name, drums):
    return f'<kit presetName="{name}"><soundSources>{"".join(drums)}</soundSources></kit>'


def kit_clip(name, rows, volume="0x3504F334"):
    return (f'<instrumentClip instrumentPresetName="{name}"><kitParams volume="{volume}" /><noteRows>'
            + "".join(f'<noteRow drumIndex="{i}" noteDataWithLift="0x0000000000000060400000"><soundParams {p} /></noteRow>' for i, p in rows)
            + "</noteRows></instrumentClip>")


def synth_clip(name, p):
    return f'<instrumentClip instrumentPresetName="{name}"><soundParams {p} /></instrumentClip>'


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "card"
        (self.root / "SONGS").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def song(self, name, text):
        p = self.root / "SONGS" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8", "surrogateescape"))
        return p


class Params(unittest.TestCase):
    def test_scale(self):
        self.assertEqual(db.param_of_knob(40), 0x4CCCCCA8)  # The Deluge's own 40
        self.assertEqual(db.param_of_knob(50), 2147483602)  # 0x7FFFFFFF is 50.0000005
        self.assertEqual(db.param_of_knob(0), -2 ** 31)
        self.assertEqual(db.param_of_knob(40.0000001), 0x4CCCCCA8 + 8)  # Rounded down: never louder
        # The automation's positions stay, its values scale as knobs
        auto = "0x4CCCCCA8" + "7FFFFFFF" + "80000060" + "00000000" + "000000C0"
        out = db.scale_text(auto, 0.8)
        self.assertEqual((out[:2], out[18:26], out[34:42]), ("0x", "80000060", "000000C0"))
        self.assertEqual([round(bc.knob(v), 6) for v in bc.param_values(out)], [32.0, 40.0, 20.0])
        self.assertEqual(db.scale_text(" -21474836 ", 1.0), " -21474836 ")

    def test_edit(self):
        text = '<a x="1" y="2"><b>0x10</b></a>'
        tree = bc.parse_xml(text)
        e = db.XmlEdit("t", text)
        e.replace(tree.child("a").span("y"), "22")
        e.replace(tree.child("a").span("b"), "0x20")
        self.assertEqual(e.result(), b'<a x="1" y="22"><b>0x20</b></a>')
        e.replace(tree.child("a").span("y"), "22")  # The same again is fine
        with self.assertRaises(ValueError):
            e.replace(tree.child("a").span("y"), "23")


class Levels(Case):
    def test_demo_card(self):
        card = db.demo_card(Path(self.tmp.name) / "demo")
        before = (card / "SONGS" / "Demo.XML").read_text(encoding="utf-8")
        plan = db.plan_levels(card)
        self.assertEqual(list(plan.changes), ["SONGS/Demo.XML"])
        lines = plan.changes["SONGS/Demo.XML"].lines
        self.assertEqual(lines, [
            "Song-Lautstärke: 40,0 auf 35,4, -2,1 dB",
            "Master-Kompressor aus (Threshold war 25)",
            "Kit «Drums» (Clip 1): 40,0 auf 35,4, -2,1 dB",
            "Reihe «Kick» in Kit «Drums» (Clip 1): 50,0 auf 40,0, -3,9 dB"])
        after = plan.changes["SONGS/Demo.XML"].data.decode("utf-8")
        self.assertEqual(changed_attrs(before, after), [
            ("songParams", "volume", "0x4CCCCCA8", "0x3504F334"),
            ("songParams", "compressorThreshold", "0x40000000", "0x00000000"),
            ("kitParams", "volume", "0x4CCCCCA8", "0x3504F334"),
            ("soundParams", "volume", "0x7FFFFFFF", "0x4CCCCCA8")])  # Kick only: Tick's sample is quiet
        self.assertEqual(len(after), len(before))  # Same lengths: nothing else moved
        # Into a folder: the card stays; onto the card: a backup of the old file
        out = Path(self.tmp.name) / "out"
        db.write_plan(plan, to=out)
        self.assertEqual((card / "SONGS" / "Demo.XML").read_text(encoding="utf-8"), before)
        self.assertEqual((out / "SONGS" / "Demo.XML").read_text(encoding="utf-8"), after)
        self.assertEqual(sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()),
                         ["BASELINE.txt", "SONGS/Demo.XML"])
        db.write_plan(plan)
        backups = list((card / db.BACKUP).iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "SONGS" / "Demo.XML").read_text(encoding="utf-8"), before)
        self.assertTrue((backups[0] / "BASELINE.txt").exists())
        self.assertEqual(db.plan_levels(card).changes, {})
        self.assertIn("| Demo | 35 | aus | 35 | 50 | ok |", bc.report(bc.song_files([str(card)])))

    def test_automation_and_clips(self):
        wav(self.root / "SAMPLES" / "k.wav", signal(0, 16))
        auto_kit = "0x3504F334" + "7FFFFFFF" + "80000060"  # 35, a node at 50
        auto_row = "0x4CCCCCA8" + "7FFFFFFF" + "00000030"  # 40, a node at 50
        text = song_text(kit("K", [sound("k", "SAMPLES/k.wav")]),
                         clips=kit_clip("K", [(0, params(auto_row))], volume=auto_kit)
                         + kit_clip("K", [(0, params("0x66666662"))]))
        self.song("A.XML", text)
        plan = db.plan_levels(self.root)
        new = plan.changes["SONGS/A.XML"].data.decode("utf-8")
        tree = bc.parse_xml(new)
        clips = [c for c in tree.iter() if c.name == "instrumentClip"]
        kit_values = bc.param_values(clips[0].child("kitParams").get("volume"))
        self.assertLessEqual(max(kit_values), bc.SONG_KIT_LIMIT)
        self.assertAlmostEqual(bc.knob(kit_values[0]) / bc.knob(kit_values[1]), 35.355 / 50, places=4)
        self.assertIn("80000060", clips[0].child("kitParams").get("volume"))  # The position stays
        rows = [c.child("noteRows").children[0].child("soundParams") for c in clips]
        self.assertAlmostEqual(bc.knob(max(bc.param_values(rows[0].get("volume")))), 40, places=5)
        self.assertAlmostEqual(bc.knob(bc.param_values(rows[0].get("volume"))[0]), 32, places=5)  # 40 * 40/50
        self.assertEqual(rows[1].get("volume"), "0x4CCCCCA8")  # 45 down to 40
        self.assertEqual(plan.changes["SONGS/A.XML"].lines[-1], "Reihe «k» in Kit «K» (Clip 2): 45,0 auf 40,0, -2,0 dB")

    def test_without_notes(self):
        text = song_text(kit("K", [sound("saw", osc1_type="saw")]), clips=kit_clip("K", [(0, params("0x7FFFFFFF"))])
                         .replace(' noteDataWithLift="0x0000000000000060400000"', ""))
        self.song("C.XML", text)
        self.assertEqual(db.plan_levels(self.root).changes["SONGS/C.XML"].lines,
                         ["Reihe «saw» in Kit «K» (Clip 1): 50,0 auf 40,0, -3,9 dB, ohne Noten"])

    def test_same_name_other_folder(self):
        # Two kits «K», in KITS/A and KITS/B: each clip goes by its own kit, as in the firmware (name and folder)
        wav(self.root / "SAMPLES" / "quiet.wav", signal(-12, 16), 16)
        wav(self.root / "SAMPLES" / "loud.wav", signal(0, 16), 16)
        kits = kit("K", [sound("q", "SAMPLES/quiet.wav")]).replace('<kit presetName="K"', '<kit presetName="K" '
                                                                     'presetFolder="KITS/A"') + \
            kit("K", [sound("q", "SAMPLES/loud.wav")]).replace('<kit presetName="K"', '<kit presetName="K" '
                                                                 'presetFolder="KITS/B"')
        clips = kit_clip("K", [(0, params("0x7FFFFFFF"))]).replace(
            'instrumentPresetName="K"', 'instrumentPresetName="K" instrumentPresetFolder="KITS/A"')
        self.song("F.XML", song_text(kits, clips=clips))
        self.assertEqual(db.plan_levels(self.root).changes, {})  # 50 with a sample at -12 dBFS: as loud as 25
        self.assertEqual(bc.check_all(bc.song_files([str(self.root)]))[0]["notes"], [])
        self.song("F.XML", song_text(kits, clips=clips.replace("KITS/A", "kits/b")))  # Case doesn't count
        self.assertEqual(db.plan_levels(self.root).changes["SONGS/F.XML"].lines,
                         ["Reihe «q» in Kit «K» (Clip 1): 50,0 auf 40,0, -3,9 dB"])

    def test_what_stays(self):
        # Samples that can't be read: nothing changes, and the report says so; a row with an oscillator: 40 exactly
        text = song_text(kit("K", [sound("gone", "SAMPLES/gone.wav"), sound("saw", osc1_type="saw")]),
                         clips=kit_clip("K", [(0, params("0x7FFFFFFF")), (1, params("0x7FFFFFFF"))]))
        self.song("B.XML", text)
        plan = db.plan_levels(self.root)
        self.assertEqual(plan.changes["SONGS/B.XML"].lines, [
            "Reihe «gone» in Kit «K»: nicht geändert (Sample fehlt: SAMPLES/gone.wav)",
            "Reihe «saw» in Kit «K» (Clip 1): 50,0 auf 40,0, -3,9 dB"])


class Normalize(Case):
    def test_formats(self):
        s = self.root / "SAMPLES"
        smpl = chunk(b"smpl", bytes(36) + struct.pack("<I", 0))
        files = {
            "w16.wav": lambda p: wav(p, signal(-12, 16), 16, extra=smpl),
            "w24.wav": lambda p: wav(p, signal(-7.5, 24), 24),
            "w32.wav": lambda p: wav(p, signal(-20, 32), 32),
            "w8.wav": lambda p: wav(p, signal(-9, 8), 8),
            "wf.wav": lambda p: wav(p, signal(-6, 32, float_=True), 32, tag=3),
            "a16.aif": lambda p: aiff(p, signal(-10, 16), 16),
            "a24.aiff": lambda p: aiff(p, signal(-3, 24), 24),
            "a8.aif": lambda p: aiff(p, signal(-5, 8), 8),
            "near.wav": lambda p: wav(p, signal(-1.05, 16), 16),
            "hot.wav": lambda p: wav(p, signal(-0.2, 24), 24),
        }
        for name, make in files.items():
            make(s / name)
        before = {n: (s / n).read_bytes() for n in files}
        plan = db.plan_normalize(self.root, -1.0, True)
        self.assertEqual(sorted(Path(r).name for r in plan.changes), sorted(set(files) - {"near.wav", "hot.wav"}))
        db.write_plan(plan)
        for name in set(files) - {"near.wav", "hot.wav"}:
            peak, _ = bc.sample_peak(str(s / name))
            self.assertAlmostEqual(20 * math.log10(peak), -1.0, delta=0.08 if "8" in name else 0.002, msg=name)
            with open(s / name, "rb") as fh:
                f = bc.audio_format(fh)
            old, new = before[name], (s / name).read_bytes()
            self.assertEqual(len(old), len(new), name)
            self.assertEqual(old[:f["pos"]], new[:f["pos"]], name)  # Header and chunks before the audio
            self.assertEqual(old[f["pos"] + f["size"]:], new[f["pos"] + f["size"]:], name)  # And after it
            x, y = db.decode(old[f["pos"]:f["pos"] + f["size"]], f), db.decode(new[f["pos"]:f["pos"] + f["size"]], f)
            g = 10 ** (-1 / 20) / bc.sample_peak(str(self.tmp.name) and None or str(s / name))[0] if False else None
            ratio = float(np.max(np.abs(y))) / float(np.max(np.abs(x)))
            if not f["float"]:
                self.assertTrue(np.all(np.abs(y - np.rint(x * ratio)) <= 1), name)  # One gain, rounded
                self.assertLessEqual(int(y.max()), 2 ** (f["bits"] - 1) - 1, name)
        for name in ("near.wav", "hot.wav"):
            self.assertEqual((s / name).read_bytes(), before[name], name)

    def test_target_zero_and_multisample(self):
        s = self.root / "SAMPLES"
        wav(s / "lo.wav", signal(-9, 16), 16)
        wav(s / "hi.wav", signal(-4, 16), 16)
        wav(s / "one.wav", signal(-6, 24), 24)
        self.song("K.XML", song_text(synths=sound("Keys", ranges=["SAMPLES/lo.wav", "SAMPLES/hi.wav"]),
                                     clips=synth_clip("Keys", params("0x66666662"))))
        plan = db.plan_normalize(self.root, 0.0, True)
        db.write_plan(plan)
        lo, hi = bc.sample_peak(str(s / "lo.wav"))[0], bc.sample_peak(str(s / "hi.wav"))[0]
        self.assertAlmostEqual(20 * math.log10(hi), 0, delta=0.001)  # The loudest of the group reaches the target
        self.assertAlmostEqual(20 * math.log10(hi / lo), 5.0, delta=0.01)  # The balance stays
        self.assertEqual(int(np.max(db.decode((s / "one.wav").read_bytes()[44:], {"bits": 24, "float": False,
                                                                                      "big": False}))), 2 ** 23 - 1)

    def test_compensation_keeps_the_mix(self):
        s = self.root / "SAMPLES"
        wav(s / "quiet.wav", signal(-14, 24), 24)
        wav(s / "mid.wav", signal(-6, 16), 16)
        wav(s / "free.wav", signal(-10, 16), 16)  # On no song: raised, nothing to compensate
        text = song_text(kit("K", [sound("q", "SAMPLES/quiet.wav"), sound("m", "SAMPLES/mid.wav")]),
                         clips=kit_clip("K", [(0, params("0x7FFFFFFF", 'oscAVolume="0x66666662"')),
                                              (1, params("0x4CCCCCA8"))])
                         + kit_clip("K", [(0, params("0x4CCCCCA8", 'oscAVolume="0x7FFFFFFF7FFFFFFF00000060"'))]))
        self.song("sub/Mix.XML", text)
        (self.root / "KITS").mkdir()
        (self.root / "KITS" / "K.XML").write_text(
            '<?xml version="1.0"?>\n<kit><soundSources>'
            + sound("q", "SAMPLES/quiet.wav", defaults='volume="0x4CCCCCA8" oscAVolume="0x7FFFFFFF"')
            + "</soundSources></kit>\n", encoding="utf-8")
        (self.root / "SYNTHS").mkdir()
        (self.root / "SYNTHS" / "M.XML").write_text(
            '<?xml version="1.0"?>\n' + sound("M", "SAMPLES/mid.wav", defaults='oscAVolume="0x7FFFFFFF"') + "\n",
            encoding="utf-8")
        rels = ["SONGS/sub/Mix.XML", "KITS/K.XML", "SYNTHS/M.XML"]
        before = {rel: levels_of(self.root, rel) for rel in rels}
        plan = db.plan_normalize(self.root, -1.0, True)
        self.assertEqual(sorted(plan.changes), sorted(rels + ["SAMPLES/quiet.wav", "SAMPLES/mid.wav",
                                                              "SAMPLES/free.wav"]))
        db.write_plan(plan)
        for rel in rels:
            after = levels_of(self.root, rel)
            self.assertEqual(before[rel].keys(), after.keys())
            for k in before[rel]:
                self.assertAlmostEqual(before[rel][k], after[k], delta=2e-5, msg=f"{rel} {k}")
        # Only the oscillator levels changed in the XML, the automation's position stays
        new = (self.root / "SONGS" / "sub" / "Mix.XML").read_text(encoding="utf-8")
        self.assertEqual({k[1] for k in changed_attrs(text, new)}, {"oscAVolume"})
        self.assertIn("00000060", new)
        self.assertAlmostEqual(20 * math.log10(bc.sample_peak(str(s / "free.wav"))[0]), -1, delta=0.002)

    def test_what_stays(self):
        s = self.root / "SAMPLES"
        for name in ("nolevel", "cable", "fm", "table", "take", "ok", "lonely"):
            wav(s / f"{name}.wav", signal(-12, 16), 16)
        wav(s / "ser.wav", signal(-12, 16), 16, extra=chunk(b"clm ", b"<!>2048 00000000 wavetable (www.x.com)"))
        text = song_text(
            kit("K", [sound("nolevel", "SAMPLES/nolevel.wav"), sound("cable", "SAMPLES/cable.wav", cable="oscAVolume"),
                      sound("fm", "SAMPLES/fm.wav", mode="fm"), sound("ok", "SAMPLES/ok.wav")]),
            sound("Pad", "SAMPLES/table.wav", osc1_type="wavetable"),
            kit_clip("K", [(0, 'volume="0x4CCCCCA8"'), (1, params()), (2, params()), (3, params())])
            + '<audioClip trackName="T" filePath="SAMPLES/take.wav"><params volume="0xE0000000" /></audioClip>')
        self.song("S.XML", text)
        self.song("Other.XML", song_text(kit("L", [sound("lonely", "SAMPLES/lonely.wav")])))  # No clip: level unsaved
        plan = db.plan_normalize(self.root, -1.0, True)
        stays = dict(plan.sections)["Bleiben wie sie sind"]
        self.assertEqual(stays, [
            "SAMPLES/cable.wav: Kabel auf den Osc-Pegel (Reihe «cable» in Kit «K»)",
            "SAMPLES/fm.wav: FM oder Ringmod",
            "SAMPLES/lonely.wav: Osc-Pegel nirgends gespeichert (Reihe «lonely» in Kit «L»)",
            "SAMPLES/nolevel.wav: Osc-Pegel nicht gespeichert (Reihe «nolevel» in Kit «K»)",
            "SAMPLES/ser.wav: Wavetable",
            "SAMPLES/table.wav: Wavetable",
            "SAMPLES/take.wav: Audio-Clip"])
        self.assertEqual(sorted(plan.changes), ["SAMPLES/ok.wav", "SONGS/S.XML"])
        # Without compensation: all but wavetables and audio clips, and no XML
        plan = db.plan_normalize(self.root, -1.0, False)
        self.assertEqual(sorted(plan.changes), ["SAMPLES/cable.wav", "SAMPLES/fm.wav", "SAMPLES/lonely.wav",
                                                "SAMPLES/nolevel.wav", "SAMPLES/ok.wav"])
        # An XML that can't be read: its uses are unknown, so with compensation nothing changes
        self.song("Broken.XML", "<song><instruments></song>")
        plan = db.plan_normalize(self.root, -1.0, True)
        self.assertEqual(plan.changes, {})
        self.assertEqual(dict(plan.sections)["Bleiben wie sie sind"][-1], "SAMPLES/take.wav: Audio-Clip")

    def test_what_the_firmware_finds(self):
        """Every use of a sample, as the firmware finds it: old formats, instruments of the same name in other
        folders, copies in a song's own folder, wavetable ranges."""
        s = self.root / "SAMPLES"
        for name in ("old", "tag", "drum", "a", "copied", "range", "take"):
            wav(s / f"{name}.wav", signal(-12, 16), 16)
        synths = self.root / "SYNTHS"
        synths.mkdir()
        (synths / "Old.XML").write_text('<synth><osc1 type="sample" fileName="SAMPLES/old.wav" /></synth>')
        (synths / "Tag.XML").write_text('<sound><osc1 type="sample"><fileName>SAMPLES/tag.wav</fileName></osc1>'
                                        '<defaultParams oscAVolume="0x7FFFFFFF" /></sound>')
        (self.root / "KITS").mkdir()
        (self.root / "KITS" / "Old.XML").write_text(
            '<kit><soundSources><sample><fileName>SAMPLES/drum.wav</fileName></sample></soundSources></kit>')
        bass_a = sound("Bass", "SAMPLES/a.wav").replace('presetName="Bass"', 'presetName="Bass" '
                                                                               'presetFolder="SYNTHS/A"')
        bass_b = sound("Bass", osc1_type="saw").replace('presetName="Bass"', 'presetName="Bass" '
                                                                              'presetFolder="SYNTHS/B"')
        clip = '<instrumentClip instrumentPresetName="Bass" instrumentPresetFolder="SYNTHS/{}"><soundParams {} />' \
               '</instrumentClip>'
        self.song("Two.XML", song_text(synths=bass_a + bass_b, clips=clip.format("A", params()) + clip.format(
            "B", params(osc_a='oscAVolume="0x7FFFFFFF"'))))
        self.song("Collected.XML", song_text(sound("C", "SAMPLES/copied.wav"), clips=synth_clip("C", params())))
        wav(self.root / "SONGS" / "Collected" / "copied.wav", signal(-12, 16), 16)  # «Collect media»
        pad = '<sound presetName="Pad" mode="subtractive"><osc1 type="wavetable"><wavetableRanges><wavetableRange ' \
              'fileName="SAMPLES/range.wav" /></wavetableRanges></osc1><defaultParams oscAVolume="0x7FFFFFFF" />' \
              '</sound>'
        self.song("Pad.XML", song_text(synths=pad, clips=synth_clip("Pad", params())
                                       + '<audioClip trackName="T" filePath="SAMPLES/take.wav" />'))
        (self.root / "KITS" / "Fm.XML").write_text(f'<kit><soundSources>{sound("fm", "SAMPLES/take.wav", mode="fm")}'
                                                   '</soundSources></kit>')
        before = (self.root / "SONGS" / "Two.XML").read_text()
        plan = db.plan_normalize(self.root, -1.0, True)
        self.assertEqual(dict(plan.sections)["Bleiben wie sie sind"], [
            "SAMPLES/copied.wav: spielt vielleicht die Kopie SONGS/Collected/copied.wav (Synth «C»)",
            "SAMPLES/drum.wav: anderes Format, zum Beispiel von 2016",
            "SAMPLES/old.wav: Osc-Pegel nirgends gespeichert (Synth)",
            "SAMPLES/range.wav: Wavetable",
            "SAMPLES/take.wav: Audio-Clip"])
        self.assertEqual(sorted(plan.changes), ["SAMPLES/a.wav", "SAMPLES/tag.wav", "SONGS/Two.XML", "SYNTHS/Tag.XML"])
        a = plan.changes["SAMPLES/a.wav"]
        self.assertTrue(callable(a.make))  # Raised when it writes: not every sample in memory at once
        self.assertEqual(a.size, (s / "a.wav").stat().st_size)
        after = plan.changes["SONGS/Two.XML"].data.decode()
        self.assertEqual([(n, a, old) for n, a, old, _ in changed_attrs(before, after)],
                         [("soundParams", "oscAVolume", "0x7FFFFFFF")])  # Bass of SYNTHS/A only
        self.assertIn('instrumentPresetFolder="SYNTHS/B"><soundParams volume="0x4CCCCCA8" oscAVolume="0x7FFFFFFF"',
                      after)
        # Without compensation: never wavetables or audio clips, whatever else plays them
        plan = db.plan_normalize(self.root, -1.0, False)
        self.assertEqual(sorted(k for k in plan.changes if k.startswith("SAMPLES/")), [
            "SAMPLES/a.wav", "SAMPLES/copied.wav", "SAMPLES/drum.wav", "SAMPLES/old.wav", "SAMPLES/tag.wav"])

    def test_restore(self):
        card = db.demo_card(Path(self.tmp.name) / "demo")
        files = {p: p.read_bytes() for p in card.rglob("*") if p.is_file()}
        db.write_plan(db.plan_levels(card))
        db.write_plan(db.plan_normalize(card, -1.0, True), now=__import__("datetime").datetime(2030, 1, 1))
        backups = sorted((card / db.BACKUP).iterdir())
        self.assertEqual(len(backups), 2)
        # Back to before normalizing, then to before levels
        for backup in sorted(backups, key=lambda b: "Pegel" in b.name):
            plan = db.plan_restore(backup)
            db.write_plan(plan)
        for p, data in files.items():
            self.assertEqual(p.read_bytes(), data, p)
        with self.assertRaises(ValueError):
            db.plan_restore(card / "SONGS")


class CommandLine(Case):
    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(db.main(list(args)), 0)
        return out.getvalue()

    def test_commands(self):
        card = db.demo_card(Path(self.tmp.name) / "demo")
        before = (card / "SONGS" / "Demo.XML").read_bytes()
        self.assertIn("Master-Kompressor an", self.run_main("check", str(card / "SONGS")))
        self.assertIn("Nur gezeigt, nichts geschrieben", self.run_main("levels", str(card)))
        self.assertEqual((card / "SONGS" / "Demo.XML").read_bytes(), before)
        out = Path(self.tmp.name) / "out"
        self.assertIn("Dateien geschrieben in", self.run_main("normalize", str(card), "--to", str(out), "--yes"))
        self.assertTrue((out / "SAMPLES" / "quiet.wav").exists())
        self.assertIn("Sicherung:", self.run_main("levels", str(card), "--yes"))
        backup = next((card / db.BACKUP).iterdir())
        self.assertIn("1 Datei auf der Karte", self.run_main("restore", str(backup), "--yes"))
        self.assertEqual((card / "SONGS" / "Demo.XML").read_bytes(), before)
        with self.assertRaises(ValueError):
            db.card_root(str(card / "SAMPLES"))



class Oled(unittest.TestCase):
    """The window's OLED and lists, without a window."""

    def test_text(self):
        self.assertEqual(db.oled_text("Größe «x» é€"), "GRÖSSE «X» E?")
        o = db.Oled(3)
        o.text(1, 2, "A")
        self.assertEqual([tuple(o.fb[2 + r][1:6]) for r in range(7)], list(db.glyph("A")))
        o.rect(0, 20, 7, 7)
        o.text(1, 20, "I", invert=True)  # Dark on light: the bar stays lit around it
        self.assertEqual(tuple(o.fb[20][:7]), (1, 1, 0, 0, 0, 1, 1))
        self.assertEqual(o.text(-3, 40, "WW", size=2), 21)  # Cut at the edge, not wrapped
        self.assertEqual(len(o.ppm()), len(o.header) + 384 * 144 * 3)
        o.clear()
        self.assertFalse(any(o.frame()))
        self.assertEqual(o.ppm()[len(o.header):len(o.header) + 3], bytes(db.OLED_OFF))

    def test_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = db.demo_card(Path(tmp) / "card")
            results = bc.check_all(bc.song_files([str(card)]))
            items = db.check_items(results)
            self.assertEqual(items[0], ("Demo: 4 HINWEISE", True, 0))
            self.assertEqual(len(items), 5)
            items = db.plan_items(db.plan_levels(card), {db.rel_key("SONGS/Demo.XML"): 0})
            self.assertEqual(items[0], ("1 Song gelesen, 1 zu ändern", False, None))
            self.assertIn(("Demo", True, 0), items)
            self.assertIn(("Song-Lautstärke: 40,0 auf 35,4, -2,1 dB", False, 0), items)
            items = db.plan_items(db.plan_normalize(card, -1.0, True), {db.rel_key("SONGS/Demo.XML"): 0})
            self.assertIn(("Angehoben", True, None), items)
            self.assertIn(("Ausgeglichen: Demo", True, 0), items)
        self.assertEqual(db.backup_name("2026-09-28 07-18-35 Pegel 2"), "28.09.26 07:18 Pegel 2")
        self.assertEqual(db.song_name("SONGS/Live/Set 1.XML"), "Live/Set 1")


@unittest.skipUnless(HAS_TK, "no tkinter or display: the window can't open")
class Window(unittest.TestCase):
    """The window's buttons pressed, as by the mouse or the keys."""

    def setUp(self):
        import tkinter as tk
        self.tmp = tempfile.TemporaryDirectory()
        self.card = db.demo_card(Path(self.tmp.name) / "card")
        self.settings = Path(self.tmp.name) / "settings.json"
        self.root = tk.Tk()
        self.app = db.App(self.root, str(self.settings), card=str(self.card))

    def tearDown(self):
        self.root.destroy()
        self.tmp.cleanup()

    def press(self, key):
        self.app.press(key)
        db.settle(self.root, self.app)

    def files(self):
        return {p.relative_to(self.card).as_posix(): p.read_bytes() for p in self.card.rglob("*") if p.is_file()}

    def test_shown_first(self):
        before = self.files()
        self.press("check")
        self.assertEqual(self.app.list["title"], "1 SONG, 1 MIT HINWEISEN")
        self.assertEqual([s["status"] for s in self.app.songs], ["notes"])
        self.press("normalize")
        self.assertEqual(self.app.pending[0], "normalize")
        self.assertIn(("Ausgeglichen: Demo", True, 0), self.app.list["items"])
        self.app.turn_gold(1)  # Another target: shown anew before it writes
        self.assertIsNone(self.app.pending)
        self.press("levels")
        self.app.escape()
        self.assertIsNone(self.app.pending)
        self.press("levels")
        self.press("normalize")  # Another button: the levels aren't written
        self.assertEqual(self.app.pending[0], "normalize")
        self.app.toggle("compensate")
        self.assertIsNone(self.app.pending)
        self.assertEqual(self.files(), before)
        self.assertEqual(json.loads(self.settings.read_text()),
                         {"card": str(self.card), "mode": "card", "out": "", "target": -0.3, "compensate": False})

    def test_into_a_folder(self):
        before = self.files()
        self.app.out = str(Path(self.tmp.name) / "out")
        self.app.toggle("folder")
        self.press("normalize")
        self.press("normalize")
        self.assertEqual(self.app.list["title"], "GESCHRIEBEN")
        self.assertTrue((Path(self.app.out) / "SAMPLES" / "quiet.wav").exists())
        self.assertEqual(self.files(), before)

    def test_onto_the_card_and_back(self):
        before = (self.card / "SONGS" / "Demo.XML").read_bytes()
        self.press("levels")
        self.app.enter()  # SELECT: yes
        db.settle(self.root, self.app)
        self.assertEqual(self.app.list["title"], "GESCHRIEBEN")
        self.assertEqual([s["status"] for s in self.app.songs], ["ok"])
        self.assertNotEqual((self.card / "SONGS" / "Demo.XML").read_bytes(), before)
        self.press("restore")
        self.assertEqual(self.app.view, "backups")
        self.app.enter()
        db.settle(self.root, self.app)
        self.assertEqual(self.app.pending[0], "restore")
        self.app.busy = "SCHREIBE"
        self.app.quit()  # Not while it writes
        self.assertTrue(self.root.winfo_exists())
        self.app.busy = None
        self.press("restore")
        self.assertEqual((self.card / "SONGS" / "Demo.XML").read_bytes(), before)

    def test_without_a_card(self):
        self.app.card = ""
        self.press("check")
        self.assertIsNone(self.app.list)
        self.assertEqual(self.app.message, "KEINE KARTE: K ODER MENU")
        self.app.card = str(Path(self.tmp.name) / "gone")
        self.press("levels")
        self.assertTrue(self.app.message.startswith("KARTE NICHT DA"))


@unittest.skipUnless(HAS_TK, "no tkinter or display: the self-test's window can't open")
class SelfTest(unittest.TestCase):
    def test_selftest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(db.selftest(tmp), Path(tmp, "selftest.txt").read_text())
            self.assertEqual(Path(tmp, "selftest.txt").read_text().splitlines(), [
                f"version: v{db.VERSION}", "check: ok", "levels: ok", "normalize: ok", "restore: ok", "window: ok"])


@unittest.skipUnless((KARTE / "SONGS").is_dir(), "no card copy in geraet/karte")
class CardCopy(unittest.TestCase):
    def test_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "karte"
            shutil.copytree(KARTE, card, ignore=shutil.ignore_patterns("*.csv"))
            rel = "SONGS/New Sitar Grii 10.XML"
            before = levels_of(card, rel)
            plan = db.plan_normalize(card, -1.0, True)
            raised = [r for r in plan.changes if r.startswith("SAMPLES/")]
            self.assertGreater(len(raised), 10)
            db.write_plan(plan, to=Path(tmp) / "unused")  # Into a folder first: the card stays
            self.assertEqual(levels_of(card, rel), before)
            db.write_plan(plan)
            after = levels_of(card, rel)
            for k in before:
                self.assertAlmostEqual(before[k], after[k], delta=2e-5, msg=k)
            for r in raised:
                self.assertLessEqual(20 * math.log10(bc.sample_peak(str(card / r))[0]), -0.99, r)
            db.write_plan(db.plan_levels(card))
            section = bc.report(bc.song_files([str(card)])).split("## New Sitar Grii 10")[1]
            self.assertEqual([line for line in section.splitlines() if line.startswith("- ")], [
                "- Reihe «hihatlong» in Kit «Hihat»: 50, +3,9 dB über 40 (Sample fehlt: SAMPLES/PsyPack/hihatlong.wav), "
                "ohne Noten: klingt nur live gespielt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
