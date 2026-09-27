#!/usr/bin/env python3
"""Tests for tools/deluge_rec.py without audio hardware and without a display: sounddevice and tkinter are stubs.

Covers the WAV writer (byte for byte, header, header during the take, 4 GB split, stop, quit, disk error), ARM with
pre-roll (to the frame), file numbering, the meter (dBFS, pads, bit depth), the choice of the input (Windows WASAPI
exclusive first and the fallbacks, macOS, Linux), the monitor (its buffer, the outputs offered, never the Deluge,
the choice of the output and its menu, no clicks), VOL (the samples, the pads, the fader, the keys), the boxes on
the panel, --demo and the version. Needs only numpy.
"""
import contextlib
import importlib.util
import io
import json
import math
import os
import re
import struct
import sys
import tempfile
import time
import types
import unittest
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC = Path(os.environ.get("DELUGE_REC", HERE.parents[1] / "tools" / "deluge_rec.py"))
spec = importlib.util.spec_from_file_location("deluge_rec", SRC)
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)
dr.print = lambda *a, **kw: None  # The program's lines for the console

RNG = np.random.default_rng(17)


def samples24(n):
    """Random 24-bit stereo samples, left-justified in int32, with the extremes."""
    v = RNG.integers(-2 ** 23, 2 ** 23, size=(n, 2), dtype=np.int64)
    v[:4] = [[-2 ** 23, 2 ** 23 - 1], [0, -1], [1, -2 ** 23 + 1], [2 ** 22, -2 ** 22]][:n]
    return (v << 8).astype(np.int32)


def expected_bytes(blocks):
    return b"".join(int(s).to_bytes(3, "little", signed=True) for b in blocks for s in (b.reshape(-1) >> 8))


def chunks(raw):
    """The RIFF chunks of a WAV: {id: (offset of the body, size)}; data last, it runs to the end."""
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE", raw[:16]
    found, pos = {}, 12
    while pos + 8 <= len(raw):
        key, size = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        found[key] = (pos + 8, size)
        if key == b"data":
            break
        pos += 8 + size + (size & 1)
    return found


def parse_wav(path):
    raw = Path(path).read_bytes()
    riff, = struct.unpack("<I", raw[4:8])
    c = chunks(raw)
    fmt = struct.unpack("<IHHIIHH", raw[c[b"fmt "][0] - 4:c[b"fmt "][0] + 16])
    start, size = c[b"data"]
    return riff, fmt, size, raw[start:]


def wav_info(path):
    """The RIFF INFO list of a WAV: {b"INAM": "...", ...}."""
    raw = Path(path).read_bytes()
    start, size = chunks(raw)[b"LIST"]
    assert raw[start:start + 4] == b"INFO"
    info, pos = {}, start + 4
    while pos < start + size:
        key, n = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        value = raw[pos + 8:pos + 8 + n]
        assert value.endswith(b"\0")
        info[key] = value[:-1].decode("utf-8")
        pos += 8 + n + (n & 1)
    return info


def pack7(data):
    """The Deluge's pack_8bit_to_7bit (util/pack.c), for SysEx from a made-up Deluge."""
    out = bytearray()
    for i in range(0, len(data), 7):
        group = data[i:i + 7]
        out.append(sum(1 << j for j, b in enumerate(group) if b & 0x80))
        out += bytes(b & 0x7F for b in group)
    return bytes(out)


def song_info(song, firmware):
    return dr.SONG_INFO + pack7(json.dumps({"song": song, "fw": firmware}, ensure_ascii=False).encode()) + b"\xf7"


def wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("timeout")
        time.sleep(0.002)


def settle(e):
    """Until the writer has taken every block and every command (it waits at most 0.05 s per round)."""
    wait(lambda: e.q.empty() and e.cmd.empty())
    time.sleep(0.12)


def feed(e, blocks):
    for b in blocks:
        e.callback(b, len(b), None, dr.DemoStatus())


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.engines = []

    def tearDown(self):
        for e in self.engines:
            e.shutdown()
        self.tmp.cleanup()

    def engine(self, **kw):
        e = dr.Engine(self.dir, **kw)
        e.connected = True
        self.engines.append(e)
        return e

    def take(self, e, blocks):
        e.command("rec")
        wait(lambda: e.state == "rec")
        feed(e, blocks)
        settle(e)
        e.command("stop")
        wait(lambda: e.state == "idle")
        return e.out_dir / e.last_take[0]


class Writer(EngineCase):
    def test_pack24_exact(self):
        b = samples24(5000)
        self.assertEqual(dr.pack24(b), expected_bytes([b]))

    def test_pack24_rounds_back_a_float_path(self):
        x = RNG.integers(-2 ** 23, 2 ** 23, size=(4000, 2))
        f = (x / 2 ** 23).astype(np.float32).astype(np.float64)
        for conv in (np.trunc(f * 0x7FFFFFFF), np.floor(f * 0x7FFFFFFF - 0.5), np.round(f * 2 ** 31)):
            got = np.frombuffer(dr.pack24(np.clip(conv, -2 ** 31, 2 ** 31 - 1).astype(np.int32)), np.uint8).reshape(-1, 3)
            back = got[:, 0].astype(np.int64) | got[:, 1].astype(np.int64) << 8 | got[:, 2].astype(np.int64) << 16
            back = np.where(back >= 2 ** 23, back - 2 ** 24, back)
            self.assertTrue(np.array_equal(back, x.reshape(-1)))
        self.assertEqual(dr.pack24(np.array([[2 ** 31 - 1, -2 ** 31]], np.int32)), b"\xff\xff\x7f\x00\x00\x80")

    def test_file_byte_for_byte(self):
        e = self.engine()
        blocks = [samples24(n) for n in (1024, 1, 441, 1024, 7, 1024, 3000)]
        path = self.take(e, blocks)
        riff, fmt, size, data = parse_wav(path)
        frames = sum(map(len, blocks))
        self.assertEqual(fmt, (16, 1, 2, 44100, 44100 * 6, 6, 24))
        self.assertEqual((size, riff, len(data)), (frames * 6, path.stat().st_size - 8, frames * 6))
        self.assertEqual(data, expected_bytes(blocks))
        with wave.open(str(path)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()),
                             (2, 3, 44100, frames))
        self.assertEqual(e.last_take[2], frames * 6)

    def test_header_up_to_date_during_the_take(self):
        path = self.dir / "t.wav"
        w = dr.WavFile(path)
        for _ in range(dr.HEADER_EVERY // 1000 + 1):
            w.write(samples24(1000))
        riff, fmt, size, data = parse_wav(path)  # As a crash would leave it
        self.assertEqual(size, len(data))
        self.assertGreaterEqual(size, dr.HEADER_EVERY * 6)
        w.close()
        self.assertEqual(parse_wav(path)[2], w.frames * 6)
        with self.assertRaises(FileExistsError):
            dr.WavFile(path)

    def test_stop_keeps_the_queued_blocks(self):
        e = self.engine()
        e.command("rec")
        wait(lambda: e.state == "rec")
        blocks = [samples24(1024) for _ in range(300)]
        feed(e, blocks)
        e.command("stop")
        wait(lambda: e.state == "idle")
        self.assertEqual(parse_wav(self.dir / e.last_take[0])[3], expected_bytes(blocks))

    def test_quit_while_recording_saves(self):
        e = self.engine()
        e.command("rec")
        wait(lambda: e.state == "rec")
        blocks = [samples24(1024) for _ in range(20)]
        feed(e, blocks)
        e.shutdown()
        self.assertFalse(e.writer.is_alive())
        self.assertEqual(parse_wav(self.dir / e.last_take[0])[3], expected_bytes(blocks))

    def test_4gb_goes_on_in_the_next_file(self):
        old = dr.MAX_DATA_BYTES
        dr.MAX_DATA_BYTES = 1000 * 6
        try:
            e = self.engine()
            blocks = [samples24(300) for _ in range(9)]
            self.take(e, blocks)
        finally:
            dr.MAX_DATA_BYTES = old
        files = sorted(self.dir.iterdir(), key=lambda p: p.stat().st_mtime_ns)
        base = files[0].name[:-4]
        self.assertEqual([p.name for p in files], [base + ".WAV", base + " part 2.WAV", base + " part 3.WAV"])
        self.assertEqual([parse_wav(p)[2] // 6 for p in files], [1200, 1200, 300])
        self.assertEqual(b"".join(parse_wav(p)[3] for p in files), expected_bytes(blocks))

    def test_disk_error_ends_the_take_and_goes_on(self):
        e = self.engine()
        e.out_dir = self.dir / "file"
        e.out_dir.write_bytes(b"x")  # Not a folder
        e.command("rec")
        wait(lambda: "DISK ERROR" in e.events)
        self.assertEqual(e.state, "idle")
        self.assertTrue(e.writer.is_alive())
        e.out_dir = self.dir / "ok"
        self.assertEqual(self.take(e, [samples24(10)]).parent, self.dir / "ok")


class Volume(EngineCase):
    def test_samples(self):
        """VOL: at 0 dB the block itself (bit-exact); below, 24-bit samples scaled and rounded; a change ramps over one
        block; back at 0 dB bit-exact again."""
        e, x = self.engine(), samples24(1000)
        self.assertIs(e.apply_volume(x), x)
        e.volume_db, g = -6, 10 ** (-6 / 20)

        def scaled(gains):
            y = np.rint((x.astype(np.int64) >> 8) * np.asarray(gains)[:, None] if np.ndim(gains) else
                        (x.astype(np.int64) >> 8) * gains)
            return (np.clip(y, -2 ** 23, 2 ** 23 - 1).astype(np.int64) << 8).astype(np.int32)
        ramp = 1 + (g - 1) * np.arange(1, 1001) / 1000
        self.assertTrue(np.array_equal(e.apply_volume(x), scaled(ramp)))  # From 1 to g over the block
        y = e.apply_volume(x)
        self.assertTrue(np.array_equal(y, scaled(g)))
        self.assertEqual((y.dtype, int(np.bitwise_or.reduce(y & 0xFF, axis=None))), (np.int32, 0))  # Still 24 bits
        e.volume_db = 0
        self.assertTrue(np.array_equal(e.apply_volume(x), scaled(g + (1 - g) * np.arange(1, 1001) / 1000)))
        self.assertIs(e.apply_volume(x), x)

    def test_take_levels_and_clip(self):
        """The take and the pads get the level after VOL; the bit depth and the clip are what the Deluge sends."""
        e = self.engine()
        e.volume_db, e.gain = -20, 0.1  # Already there: no ramp
        full = np.full((1024, 2), -2 ** 31, np.int32)
        full[:, 1] = -2 ** 30  # Half of full scale on the right
        full[5, 1] += 1 << 8  # The lowest of 24 bits set once: 24 bits arrive
        path = self.take(e, [full])
        _, _, size, data = parse_wav(path)
        got = np.frombuffer(data, np.uint8).reshape(-1, 6)
        left = int.from_bytes(bytes(got[0, :3]), "little", signed=True)
        self.assertEqual(left, round(-2 ** 23 * 0.1))
        levels = e.take_levels()
        self.assertAlmostEqual(levels[0], -20, places=3)
        self.assertAlmostEqual(levels[1], -26.02, places=1)
        self.assertTrue(e.take_clipped())  # The Deluge's output was at full scale
        self.assertFalse(e.take_clipped())
        self.assertEqual(e.take_bits(), 24)


class Naming(EngineCase):
    WHEN = time.mktime((2026, 9, 27, 21, 30, 5, 0, 0, -1))

    def engine_at(self, song=None, firmware=None):
        e = self.engine()
        e.now = lambda: self.WHEN
        e.info.song, e.info.firmware = song, firmware
        return e

    def test_song_date_firmware(self):
        """The name: the song as the Deluge told it, the date and time, the firmware in short; inside, the INFO list
        with the full firmware."""
        e = self.engine_at("Rescue 3", "1.2.1-mastertune-v17-4e3d2075")
        path = self.take(e, [samples24(10)])
        self.assertEqual(path.name, "Rescue 3 2026-09-27 21-30-05 v17.WAV")
        info = wav_info(path)
        self.assertEqual((info[b"INAM"], info[b"ICRD"], info[b"ISFT"]),
                         ("Rescue 3", "2026-09-27", f"DelugeRec v{dr.VERSION}"))
        self.assertIn("Deluge firmware: 1.2.1-mastertune-v17-4e3d2075", info[b"ICMT"])
        self.assertIn("2026-09-27 21:30:05", info[b"ICMT"])
        self.assertIn("VOL 0 dB (bit-exact)", info[b"ICMT"])
        self.assertEqual(e.last_take[3], "Rescue 3")
        with wave.open(str(path)) as w:  # Other programs read past the INFO list
            self.assertEqual(w.getnframes(), 10)

    def test_without_the_deluge_telling(self):
        """An older firmware tells nothing: the date and time alone; a new song not saved yet: no song."""
        e = self.engine_at()
        self.assertEqual(self.take(e, [samples24(10)]).name, "2026-09-27 21-30-05.WAV")
        self.assertEqual(wav_info(self.dir / e.last_take[0])[b"INAM"], "Deluge USB audio")
        e.info.song, e.info.firmware = "", "1.2.1-mastertune-v18"
        self.assertEqual(self.take(e, [samples24(10)]).name, "2026-09-27 21-30-05 v18.WAV")

    def test_no_overwrite(self):
        e = self.engine_at("Rescue 3", "1.2.1-mastertune-v17-l2d-b3385d83")
        (self.dir / "Rescue 3 2026-09-27 21-30-05 v17-l2d.WAV").write_bytes(b"keep")
        self.assertEqual(self.take(e, [samples24(10)]).name, "Rescue 3 2026-09-27 21-30-05 v17-l2d (2).WAV")
        self.assertEqual(self.take(e, [samples24(10)]).name, "Rescue 3 2026-09-27 21-30-05 v17-l2d (3).WAV")
        self.assertEqual((self.dir / "Rescue 3 2026-09-27 21-30-05 v17-l2d.WAV").read_bytes(), b"keep")

    def test_names_safe_on_every_system(self):
        self.assertEqual(dr.clean_name('a<b>c:d"e/f\\g|h?i*j\x01. '), "a_b_c_d_e_f_g_h_i_j_")
        self.assertEqual(dr.clean_name("x" * 200), "x" * 80)
        self.assertEqual(dr.short_firmware("1.2.1-mastertune-v16-l2d-dronefix-ba499a93"), "v16-l2d-dronefix")
        self.assertEqual(dr.short_firmware(None), "")
        e = self.engine_at("Grüezi/../Welt", "c1.2.1")
        self.assertEqual(self.take(e, [samples24(10)]).name, "Grüezi_.._Welt 2026-09-27 21-30-05 c1.2.1.WAV")


class SongInfo(unittest.TestCase):
    def test_parse(self):
        """The SysEx from the Deluge: JSON packed 7 into 8, UTF-8; anything else is not it."""
        self.assertEqual(dr.DelugeInfo.parse(song_info("Grüezi Mitenand 2", "1.2.1-mastertune-v18")),
                         ("Grüezi Mitenand 2", "1.2.1-mastertune-v18"))
        for n in range(0, 40):  # Every length of the packing's last group
            self.assertEqual(dr.DelugeInfo.parse(song_info("x" * n, "fw"))[0], "x" * n)
        good = song_info("Rescue", "v17")
        for bad in (good[:-1], b"\xf0\x00\x21\x7b\x01\x10" + good[6:], good[:6] + b"\x00\x7f" + b"\xf7",
                    dr.SONG_INFO + pack7(b'{"song": 3, "fw": "x"}') + b"\xf7", dr.SONG_INFO + pack7(b"[]") + b"\xf7"):
            self.assertIsNone(dr.DelugeInfo.parse(bad), bad)

    def test_port_3_only(self):
        """The Deluge's port 3 as Windows, macOS and Linux name it; never port 1, which a DAW uses."""
        pick = dr.DelugeInfo.pick
        self.assertEqual(pick(["Deluge 0", "MIDIIN2 (Deluge) 1", "MIDIIN3 (Deluge) 2"]), 2)
        self.assertEqual(pick(["IAC Bus 1", "Deluge Port 1", "Deluge Port 2", "Deluge Port 3"]), 3)
        self.assertEqual(pick(["Midi Through:Midi Through Port-0 14:0", "Deluge:Deluge MIDI 1 20:0",
                               "Deluge:Deluge MIDI 2 20:1", "Deluge:Deluge MIDI 3 20:2"]), 3)
        self.assertEqual(pick(["Deluge", "Deluge 2", "Deluge 3"]), 2)
        self.assertIsNone(pick(["Deluge 0"]))
        self.assertIsNone(pick(["Launchpad", "loopMIDI Port 3"]))

    def test_other_heads_and_cp437(self):
        """After SysEx with the developer ID 0x7D the Deluge sends F0 7D 12 ...; a name in CP437 (the card's code page)
        is read too."""
        good = song_info("Rescue", "v17")
        self.assertEqual(dr.DelugeInfo.parse(dr.SONG_INFO_7D + good[len(dr.SONG_INFO):]), ("Rescue", "v17"))
        cp437 = dr.SONG_INFO + pack7('{"song":"Grüezi","fw":"v17"}'.encode("cp437")) + b"\xf7"
        self.assertEqual(dr.DelugeInfo.parse(cp437), ("Grüezi", "v17"))

    def test_listens_only(self):
        """With python-rtmidi: port 3 opened as an input, SysEx let through, read by poll() (no second thread), the
        song and firmware taken from it; closing forgets them. A taken port: busy. No output port is ever made."""
        fake, made = fake_rtmidi(["Deluge 0", "MIDIIN2 (Deluge) 1", "MIDIIN3 (Deluge) 2"])
        with installed(fake):
            info = dr.DelugeInfo()
            self.assertTrue(info.open())
            self.assertEqual((made[0].opened, made[0].sysex, info.port), (2, True, "MIDIIN3 (Deluge) 2"))
            made[0].queue += [song_info("Rescue 3", "1.2.1-mastertune-v17-4e3d2075"), bytes([0x90, 60, 100])]
            self.assertTrue(info.poll())
            self.assertEqual((info.song, info.firmware), ("Rescue 3", "1.2.1-mastertune-v17-4e3d2075"))
            made[0].queue.append(song_info("Rescue 3", "1.2.1-mastertune-v17-4e3d2075"))
            self.assertFalse(info.poll())  # The same again: no change
            info.close()
            self.assertEqual((made[0].opened, info.song, info.firmware), (None, None, None))
            fake.ports = ["Deluge 0"]
            self.assertFalse(info.open())  # No port 3: nothing opened
            self.assertIsNone(made[-1].opened)
            fake.ports, fake.taken = ["Deluge 0", "MIDIIN2 (Deluge) 1", "MIDIIN3 (Deluge) 2"], True
            self.assertFalse(info.open())
            self.assertTrue(info.busy)
        self.assertNotIn("MidiOut", SRC.read_text(encoding="utf-8"))


def fake_rtmidi(ports):
    """python-rtmidi with made-up MIDI inputs: MidiIn only."""
    made = []
    fake = types.ModuleType("rtmidi")
    fake.ports, fake.taken, fake.broken = list(ports), False, False

    class MidiIn:
        def __init__(self):
            if fake.broken:
                raise RuntimeError("MidiInAlsa::initialize: error creating ALSA sequencer client object.")
            made.append(self)
            self.opened, self.sysex, self.queue = None, None, []
        def get_ports(self): return list(fake.ports)
        def ignore_types(self, sysex=True, **kw): self.sysex = not sysex
        def open_port(self, i):
            if fake.taken:
                raise RuntimeError("MidiInWinMM::openPort: error creating Windows MM MIDI input port.")
            self.opened = i
        def get_message(self): return (list(self.queue.pop(0)), 0.0) if self.queue else None
        def close_port(self): self.opened = None
        def delete(self): pass
    fake.MidiIn = MidiIn
    fake.get_rtmidi_version = lambda: "6.0.0"
    return fake, made


class installed:
    """A made-up module in sys.modules for the time of a with block."""

    def __init__(self, module):
        self.module = module

    def __enter__(self):
        self.old = sys.modules.get(self.module.__name__)
        sys.modules[self.module.__name__] = self.module

    def __exit__(self, *a):
        if self.old is None:
            sys.modules.pop(self.module.__name__, None)
        else:
            sys.modules[self.module.__name__] = self.old


class PreRoll(EngineCase):
    """Quiet frames carry their index (index * 256, far below -40 dBFS), so the file shows every lost or doubled
    frame."""

    def counted(self, start, n, loud_at=None):
        idx = np.arange(start, start + n, dtype=np.int64)
        b = np.stack([idx << 8, -(idx << 8)], 1)
        if loud_at is not None:
            b[loud_at:] = [2 ** 30, -2 ** 30]
        return b.astype(np.int32)

    def armed_take(self, sizes, onset_block, onset_at, threshold=-40):
        e = self.engine(threshold=threshold)
        e.command("arm")
        wait(lambda: e.state == "armed")
        pos, blocks = 0, []
        for n in sizes:
            blocks.append(self.counted(pos, n))
            pos += n
        blocks.append(self.counted(pos, onset_block, onset_at))
        feed(e, blocks[:-1])
        settle(e)
        self.assertEqual(e.state, "armed")
        tail = [np.full((500, 2), 2 ** 29, np.int32) for _ in range(3)]
        feed(e, [blocks[-1]] + tail)
        settle(e)
        e.command("stop")
        wait(lambda: e.state == "idle")
        data = np.frombuffer(parse_wav(self.dir / e.last_take[0])[3], np.uint8).reshape(-1, 3)
        left = (data[0::2, 0].astype(np.int64) | data[0::2, 1].astype(np.int64) << 8
                | data[0::2, 2].astype(np.int64) << 16)
        return left, pos + onset_at, len(data) // 2

    def test_exactly_0_3_s_before_the_first_loud_frame(self):
        for sizes, onset_block, onset_at in (([1024] * 40, 1024, 700), ([441] * 90, 441, 0), ([1024] * 30, 1024, 1023),
                                             ([333, 1024, 5000, 1, 17] * 5, 64, 13)):
            with self.subTest(sizes=len(sizes), at=onset_at):
                left, onset, frames = self.armed_take(sizes, onset_block, onset_at)
                self.assertEqual(frames, dr.PREROLL + (onset_block - onset_at) + 1500)
                self.assertTrue(np.array_equal(left[:dr.PREROLL], np.arange(onset - dr.PREROLL, onset)))
                self.assertEqual(left[dr.PREROLL], 2 ** 30 >> 8)
                self.tearDown()
                self.setUp()

    def test_armed_shortly_before(self):
        left, onset, frames = self.armed_take([1024, 1024], 1024, 500)
        self.assertEqual(frames, 2048 + 1024 + 1500)  # Everything since ARM
        self.assertTrue(np.array_equal(left[:onset], np.arange(onset)))

    def test_threshold_in_dbfs(self):
        e = self.engine(threshold=-20)
        over = dr.FULL_SCALE * 10 ** (-20 / 20)
        below = np.full((100, 2), math.floor(over) - 1, np.int32)
        at = np.full((100, 2), math.ceil(over), np.int32)
        self.assertFalse(e.trigger([], below))
        self.assertEqual(e.state, "idle")
        self.assertTrue(e.trigger([], at))
        e.close_file()


class Meter(EngineCase):
    def test_db(self):
        self.assertEqual(dr.to_db(2 ** 31), 0.0)
        self.assertAlmostEqual(dr.to_db(2 ** 30), -6.0206, places=4)
        self.assertAlmostEqual(dr.to_db((2 ** 23 - 1) << 8), 0.0, places=5)
        self.assertEqual(dr.to_db(0), -math.inf)

    def test_levels_and_bits(self):
        e = self.engine()
        feed(e, [np.array([[-2 ** 31, 2 ** 20], [5, -2 ** 29]], np.int32)])
        left, right = e.take_levels()
        self.assertEqual(left, 0.0)
        self.assertAlmostEqual(right, -12.0412, places=4)
        self.assertEqual(e.take_levels(), [-math.inf, -math.inf])
        for block, bits in ((samples24(100), 24), (samples24(100) & ~0xFFFF, 16), (samples24(100) | 1, 32),
                            (np.zeros((100, 2), np.int32), None)):
            e.take_bits()
            feed(e, [block])
            self.assertEqual(e.take_bits(), bits)


class FakeTk(types.ModuleType):
    """Just enough tkinter for App: the canvas keeps its items' options."""

    def __init__(self):
        super().__init__("tkinter")
        items, binds = {}, {}

        class Canvas:
            def __init__(self, *a, **kw):
                self.items, self.binds = items, binds

            def _new(self, *a, **kw):
                items[len(items) + 1] = dict(kw, xy=a)
                return len(items)
            create_rectangle = create_oval = create_line = create_image = _new

            def itemconfigure(self, item, **kw):
                items[item].update(kw)

            def pack(self, *a, **kw): pass
            def tag_bind(self, item, event, f): binds[(item, event)] = f
            def configure(self, *a, **kw): pass
            def coords(self, *a, **kw): pass
            def delete(self, *a, **kw): pass

        class PhotoImage:
            def __init__(self, **kw): self.data = None
            def configure(self, data): self.data = data

        class Tk:
            def __init__(self): self.after_calls = []
            def after(self, ms, f): self.after_calls.append(f)
            def winfo_fpixels(self, s): return 96.0
            def title(self, t): self.titled = t
            def configure(self, **kw): pass
            def resizable(self, *a): pass
            def bind(self, key, f): self.bindings = getattr(self, "bindings", {}); self.bindings[key] = f
            def iconphoto(self, *a): pass
            def protocol(self, *a): pass
            def destroy(self): self.destroyed = True
        self.Canvas, self.PhotoImage, self.Tk = Canvas, PhotoImage, Tk


class Gui(EngineCase):
    def setUp(self):
        super().setUp()
        self.old_tk = sys.modules.get("tkinter")
        sys.modules["tkinter"] = self.tk = FakeTk()
        sys.modules["sounddevice"] = FakeSd([])

    def tearDown(self):
        super().tearDown()
        sys.modules.pop("sounddevice", None)
        if self.old_tk is not None:
            sys.modules["tkinter"] = self.old_tk
        else:
            sys.modules.pop("tkinter", None)

    def test_keys(self):
        """The keyboard: R (and the space bar) REC, A ARM, S (and Esc) STOP, M MON, O OUT, F FOLDER, upper and lower
        case."""
        e = self.engine()
        root = self.tk.Tk()
        app = dr.App(root, e, 1.0)
        pressed = []
        app.press = pressed.append
        want = {"r": "rec", "R": "rec", "<space>": "rec", "a": "arm", "A": "arm", "s": "stop", "S": "stop",
                "<Escape>": "stop", "m": "mon", "M": "mon", "o": "out", "O": "out", "f": "folder", "F": "folder"}
        for key, action in want.items():
            self.assertIn(key, root.bindings, key)
            pressed.clear()
            root.bindings[key](None)
            self.assertEqual(pressed, [action], key)

    def test_pads_and_oled(self):
        e = self.engine()
        root = self.tk.Tk()
        app = dr.App(root, e, 1.0)
        app.boot_until = 0
        canvas = app.c

        def lit(ch):
            return [i for i, item in enumerate(app.pads[ch]) if canvas.items[item]["fill"] == dr.PAD_COLOURS[i]]

        e.connected, e.last_cb = True, time.monotonic()
        e.peak = np.array([round(dr.FULL_SCALE * 10 ** (-10 / 20)), 0], np.int64)
        app.tick()
        self.assertEqual(lit(0), list(range(12)))  # Pad i from -45 + 3 i dBFS: -12 lights, -9 does not
        self.assertEqual(lit(1), [])
        e.peak = np.array([2 ** 31, 2 ** 31 - 256], np.int64)
        app.tick()
        self.assertGreater(app.clip_until, time.monotonic())
        self.assertEqual(lit(1)[:15], list(range(15)))
        for state in ("idle", "armed", "rec"):
            e.state, e.name, e.frames = state, "USB00001.WAV", 44100
            app.tick()
            self.assertTrue(app.oled.fb.any())
            self.assertTrue(app.img.data.startswith(b"P6 384 144 255\n"))
            self.assertEqual(len(app.img.data), len(b"P6 384 144 255\n") + 384 * 144 * 3)
        e.state = "idle"
        app.quit()
        self.assertTrue(root.destroyed)

    def test_vol_fader_and_keys(self):
        """VOL: up/down 1 dB (THRESH on + -), the fader by click or drag (0 dB at the top, VOL_MIN at the bottom), the
        wheel over it, a double-click back to 0 dB; the pads show the level after VOL, the last pad blinks when the
        Deluge's own output is at full scale."""
        e = self.engine()
        root = self.tk.Tk()
        app = dr.App(root, e, 1.0)
        app.boot_until = 0
        root.bindings["<Down>"](None)
        root.bindings["<Down>"](None)
        root.bindings["<Up>"](None)
        self.assertEqual((e.volume_db, e.threshold_db), (-1, -40))
        root.bindings["<plus>"](None)
        self.assertEqual((e.volume_db, e.threshold_db), (-1, -38))
        app.grab_fader(types.SimpleNamespace(y=app.fader_bottom + 50))
        self.assertEqual(e.volume_db, dr.VOL_MIN)
        app.grab_fader(types.SimpleNamespace(y=(app.fader_top + app.fader_bottom) / 2))
        self.assertEqual(e.volume_db, dr.VOL_MIN // 2)
        app.wheel(types.SimpleNamespace(x=app.fader_x, y=app.fader_top), 1)
        self.assertEqual(e.volume_db, dr.VOL_MIN // 2 + 1)
        cap = app.c.items[app.fader_cap]["xy"]
        app.c.binds[(app.fader_cap, "<Double-Button-1>")](None)
        self.assertEqual(e.volume_db, 0)
        for _ in range(3):
            root.bindings["<Up>"](None)
        self.assertEqual(e.volume_db, 0)  # Not above 0 dB
        e.volume_db, e.gain = -20, 0.1

        def lit(ch):
            return [i for i, item in enumerate(app.pads[ch]) if app.c.items[item]["fill"] == dr.PAD_COLOURS[i]]
        e.connected, e.last_cb = True, time.monotonic()
        block = np.zeros((1024, 2), np.int32)
        block[0] = [round(dr.FULL_SCALE * 10 ** (-9 / 20)), -2 ** 31]
        feed(e, [block])
        app.tick()
        self.assertEqual([i for i in lit(0) if i < 15], list(range(6)))  # -9 - 20 dB = -29 dBFS: to the pad for -30
        self.assertGreater(app.clip_until, time.monotonic())  # The right channel arrived at full scale
        app.quit()

    def test_port_3_again_and_busy(self):
        """Port 3 taken by another program: a notice, and a new try every 5 s; once free, the song arrives and shows."""
        fake, made = fake_rtmidi(["Deluge 0", "MIDIIN2 (Deluge) 1", "MIDIIN3 (Deluge) 2"])
        fake.taken = True
        with installed(fake):
            e = self.engine()
            e.last_cb = time.monotonic()
            root = self.tk.Tk()
            app = dr.App(root, e, 1.0)  # Its first tick tries port 3
            app.boot_until = 0
            self.assertEqual((e.info.midi, app.message), (None, "PORT 3 BUSY: NO SONG"))
            fake.taken = False
            e.last_cb = time.monotonic()
            app.tick()
            self.assertIsNone(e.info.midi)  # Not before 5 s
            app.next_info = 0
            app.tick()
            self.assertIsNotNone(e.info.midi)
            made[-1].queue.append(song_info("Rescue 3", "1.2.1-mastertune-v18"))
            e.last_cb = time.monotonic()
            app.tick()
            self.assertEqual((e.info.song, app.message), ("Rescue 3", "SONG Rescue 3"))
            app.quit()
            self.assertIsNone(made[-1].opened)  # Let go at the end

    def test_boxes(self):
        """A box around each control: the six buttons, VOL and THRESH, none touching another, all on the plate, each
        button's name inside its box (OUT and FOLDER no longer run together)."""
        e = self.engine()
        root = self.tk.Tk()
        app = dr.App(root, e, 1.0)
        boxes = [v["xy"] for v in app.c.items.values() if v.get("fill") == dr.BOX]
        self.assertEqual(len(boxes), 8)
        for i, a in enumerate(boxes):
            self.assertTrue(8 < a[0] < a[2] < app.W - 8 and 8 < a[1] < a[3] < app.H - 8, a)
            for b in boxes[i + 1:]:
                apart = a[2] + 4 <= b[0] or b[2] + 4 <= a[0] or a[3] + 4 <= b[1] or b[3] + 4 <= a[1]
                self.assertTrue(apart, (a, b))
        oled_right = 68 + 384 + 6
        self.assertTrue(all(b[0] > oled_right or b[1] > 52 + 144 + 6 for b in boxes))  # Clear of the OLED
        pads_bottom = max(v["xy"][3] for k, v in app.c.items.items() if k in sum(app.pads, []))
        self.assertTrue(all(b[0] > oled_right or b[1] > pads_bottom for b in boxes))  # Clear of the pads
        self.assertAlmostEqual(app.W / app.H, 305 / 208, delta=0.01)  # The Deluge's proportions
        app.quit()

    def test_version(self):
        """The version: listed under Versions (1 to VERSION, one line each), in the title, on the display at start and
        with --version."""
        self.assertIsInstance(dr.VERSION, int)
        listed = [int(n) for n in re.findall(r"^  (\d+)  \S", dr.__doc__, re.M)]
        self.assertEqual(listed, list(range(1, dr.VERSION + 1)))
        root = self.tk.Tk()
        app = dr.App(root, self.engine(), 1.0)
        self.assertEqual(root.titled, f"DELUGE USB REC v{dr.VERSION}")
        drawn, text = [], app.oled.text
        app.oled.text = lambda x, y, s, *a, **kw: (drawn.append(s), text(x, y, s, *a, **kw))
        app.tick()
        self.assertIn(f"USB REC V{dr.VERSION}", drawn)
        app.quit()
        argv, out = sys.argv, io.StringIO()
        sys.argv = ["deluge_rec.py", "--version"]
        try:
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ex:
                dr.main()
        finally:
            sys.argv = argv
        self.assertEqual(ex.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"DelugeRec v{dr.VERSION}")


class FakeStream:
    def __init__(self, sd, **kw):
        self.sd, self.kw, self.closed = sd, kw, False
        sd.opened.append(self)

    def start(self):
        extra = self.kw["extra_settings"]
        mode = None if extra is None else extra.exclusive
        if (self.kw["device"], mode) in self.sd.fail:
            raise RuntimeError("PortAudio: invalid device")

    def close(self):
        self.closed = True


class FakeOutStream:
    def __init__(self, sd, **kw):
        self.sd, self.kw, self.closed, self.active = sd, kw, False, True
        sd.outputs_opened.append(self)

    def start(self):
        if self.kw["device"] in self.sd.fail_out:
            raise RuntimeError("PortAudio: invalid sample rate")

    def close(self):
        self.closed = True


class FakeSd(types.ModuleType):
    """sounddevice with made-up devices: (name, host API, input channels[, output channels]); each host API's default
    output is its first device with outputs."""

    def __init__(self, devices, fail=(), fail_out=()):
        super().__init__("sounddevice")
        self.apis = sorted({d[1] for d in devices})
        self.devices = [{"name": d[0], "hostapi": self.apis.index(d[1]), "max_input_channels": d[2],
                         "max_output_channels": d[3] if len(d) > 3 else 0} for d in devices]
        self.fail, self.fail_out, self.opened, self.outputs_opened = set(fail), set(fail_out), [], []
        self.WasapiSettings = lambda exclusive=False, auto_convert=False: types.SimpleNamespace(
            exclusive=exclusive, auto_convert=auto_convert)
        self.InputStream = lambda **kw: FakeStream(self, **kw)
        self.OutputStream = lambda **kw: FakeOutStream(self, **kw)

    def query_devices(self): return self.devices

    def query_hostapis(self):
        return [{"name": a, "default_output_device": next(
            (i for i, d in enumerate(self.devices) if d["hostapi"] == k and d["max_output_channels"]), -1)}
            for k, a in enumerate(self.apis)]
    def _terminate(self): self.open_at_terminate = [o for o in self.outputs_opened if not o.closed]
    def _initialize(self): pass


WINDOWS = [("Microsoft Sound Mapper - Input", "MME", 2), ("Line (Deluge)", "MME", 2),
           ("Line (Deluge)", "Windows DirectSound", 2), ("Line (Deluge)", "Windows WASAPI", 2),
           ("Line (Deluge)", "Windows WDM-KS", 2), ("Speakers (Deluge)", "Windows WASAPI", 0),
           ("Microphone (Realtek)", "Windows WASAPI", 2)]


class Devices(EngineCase):
    def connect(self, devices, fail=(), **kw):
        sys.modules["sounddevice"] = sd = FakeSd(devices, fail)
        try:
            e = dr.Engine(self.dir, **kw)
            self.engines.append(e)
            ok = e.connect()
            return ok, e, sd
        finally:
            sys.modules.pop("sounddevice", None)

    def test_windows_order(self):
        ok, e, sd = self.connect(WINDOWS)
        self.assertEqual((ok, e.api), (True, "WASAPI EXCL"))
        kw = sd.opened[-1].kw
        self.assertEqual((kw["device"], kw["samplerate"], kw["channels"], kw["dtype"], kw["dither_off"]),
                         (3, 44100, 2, "int32", True))

    def test_windows_candidates(self):
        sys.modules["sounddevice"] = FakeSd(WINDOWS)
        try:
            e = dr.Engine(self.dir)
            self.engines.append(e)
            self.assertEqual([c[1:] for c in e.candidates()], [(3, "Windows WASAPI"), (4, "Windows WDM-KS"),
                                                               (2, "Windows DirectSound"), (1, "MME")])
            e.device = "6"
            self.assertEqual([c[1] for c in e.candidates()], [6])
            e.device = "realtek"
            self.assertEqual([c[1] for c in e.candidates()], [6])
        finally:
            sys.modules.pop("sounddevice", None)

    def test_windows_fallbacks(self):
        ok, e, sd = self.connect(WINDOWS, fail={(3, True)})
        self.assertEqual(e.api, "WASAPI SHARED")
        self.assertTrue(sd.opened[0].closed)  # The exclusive try let go of the device
        ok, e, sd = self.connect(WINDOWS, shared=True)
        self.assertEqual((e.api, len(sd.opened)), ("WASAPI SHARED", 1))
        ok, e, sd = self.connect(WINDOWS, fail={(3, True), (3, False)})
        self.assertEqual(e.api, "WDM-KS")
        ok, e, sd = self.connect(WINDOWS, fail={(3, True), (3, False), (4, None), (2, None)})
        self.assertEqual(e.api, "MME")

    def test_mac_and_linux(self):
        ok, e, sd = self.connect([("MacBook Pro Microphone", "Core Audio", 1), ("Deluge", "Core Audio", 2)])
        self.assertEqual((ok, e.api, sd.opened[0].kw["device"]), (True, "CORE AUDIO", 1))
        ok, e, sd = self.connect([("HDA Intel PCH: ALC257 Analog (hw:0,0)", "ALSA", 2),
                                  ("Deluge: USB Audio (hw:2,0)", "ALSA", 2), ("pulse", "ALSA", 32),
                                  ("default", "ALSA", 32)])
        self.assertEqual((e.api, sd.opened[0].kw["device"]), ("ALSA", 1))

    def test_no_deluge(self):
        ok, e, sd = self.connect(WINDOWS[:1] + WINDOWS[5:])
        self.assertEqual((ok, e.connected, sd.opened), (False, False, []))

    def test_port_3_let_go_without_audio(self):
        """No audio from the Deluge (its input missing or taken): port 3 is let go at once, for other programs; and
        --list gets by without any MIDI system."""
        fake, made = fake_rtmidi(["Deluge 0", "MIDIIN2 (Deluge) 1", "MIDIIN3 (Deluge) 2"])
        with installed(fake), installed(FakeSd([("Microphone (Realtek)", "Windows WASAPI", 2)])):
            e = dr.Engine(self.dir)
            self.engines.append(e)
            self.assertFalse(e.connect())
            self.assertIsNone(e.info.midi)
            self.assertTrue(made and all(m.opened is None for m in made))
            fake.broken = True
            argv = sys.argv
            sys.argv = ["deluge_rec.py", "--list"]
            try:
                dr.main()  # No exception
            finally:
                sys.argv = argv

    def test_never_to_the_deluge(self):
        """Nothing goes to the Deluge: MIDI only as an input (its port 3), no stream that plays and records at once,
        and the monitor's outputs leave out every device with Deluge in its name (also a Deluge that could play what
        the computer sends, as a later USB audio could)."""
        code = SRC.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"RawStream|sd\.Stream\b|\.play\(|MidiOut|send_message|mido|open_virtual", code))
        self.assertLessEqual(set(re.findall(r"rtmidi\.\w+", code)), {"rtmidi.MidiIn", "rtmidi.get_rtmidi_version"})
        self.assertEqual(len(re.findall(r"OutputStream\(", code)), 1)  # Only the monitor's
        sys.modules["sounddevice"] = FakeSd(WINDOWS_OUT)
        try:
            m = dr.Monitor()
            self.assertFalse([o for o in m.outputs() if o and "deluge" in o.lower()])
            m.output = "Speakers (Deluge)"
            self.assertEqual(m.candidates(), [])
            self.assertFalse(m.start())
        finally:
            sys.modules.pop("sounddevice", None)


WINDOWS_OUT = [("Microsoft Sound Mapper - Output", "MME", 0, 2), ("Speakers (Realtek(R) Audio)", "MME", 0, 2),
               ("Headphones (USB Headset With a", "MME", 0, 2), ("Primary Sound Driver", "Windows DirectSound", 0, 2),
               ("Speakers (Realtek(R) Audio)", "Windows DirectSound", 0, 2),
               ("Speakers (Realtek(R) Audio)", "Windows WASAPI", 0, 2),
               ("Headphones (USB Headset With a Very Long Name)", "Windows WASAPI", 0, 2),
               ("Speakers (Deluge)", "Windows WASAPI", 0, 2), ("Line (Deluge)", "Windows WASAPI", 2, 0),
               ("Speakers (Realtek(R) Audio)", "Windows WDM-KS", 0, 2)]


class Monitoring(EngineCase):
    def setUp(self):
        super().setUp()
        sys.modules["sounddevice"] = self.sd = FakeSd(WINDOWS_OUT)

    def tearDown(self):
        super().tearDown()
        sys.modules.pop("sounddevice", None)

    def test_buffer_exact_and_bounded(self):
        """What arrives plays after MONITOR_TARGET frames: a fade-in, then exactly (24 bits in float32); run dry, the last
        frame fades out, then silence until MONITOR_TARGET frames are there again; at most MONITOR_LIMIT behind."""
        b, fade = dr.MonitorBuffer(), dr.MONITOR_FADE
        x = samples24(dr.MONITOR_TARGET + 1000)
        out = np.ones((500, 2), np.float32)
        b.push(x[:1000])
        b.pull(out)
        self.assertFalse(out.any())  # Not yet
        b.push(x[1000:])
        got = []
        for n in (512, 441, 1024, 3, 1100):  # 32 frames more than there are
            o = np.empty((n, 2), np.float32)
            b.pull(o)
            got.append(o)
        got, want = np.concatenate(got).astype(np.float64), x.astype(np.float64) / 2 ** 31
        mid = slice(fade, len(x))
        self.assertTrue(np.array_equal(got[mid], want[mid]))
        self.assertTrue(np.array_equal((got[mid] * 2 ** 31).astype(np.int64), x[mid]))
        ramp = (np.arange(1, fade + 1) / fade)[:, None]
        self.assertTrue(np.allclose(got[:fade], want[:fade] * ramp, rtol=1e-6, atol=0))  # Fading in
        self.assertEqual(b.underruns, 1)
        b.push(x[:100])  # Priming again: nothing plays until MONITOR_TARGET frames are there ...
        o = np.ones((600, 2), np.float32)
        b.pull(o)
        tail = np.concatenate([got[len(x):], o])  # ... but the last frame fades out over MONITOR_FADE frames
        self.assertTrue(np.allclose(tail[:fade], want[-1] * (1 - ramp), rtol=0, atol=1e-7))
        self.assertFalse(tail[fade:].any())
        b.reset()
        for _ in range(20):
            b.push(samples24(1024))
        self.assertLessEqual(b.written - b.read, dr.MONITOR_LIMIT)
        self.assertGreater(b.drops, 0)

    def test_outputs_offered(self):
        """The system's default first, then each device once (MME's names cut after 31 characters are the same
        device), never Windows' aliases of the default and never the Deluge."""
        self.assertEqual(dr.Monitor().outputs(), [None, "Speakers (Realtek(R) Audio)",
                                                  "Headphones (USB Headset With a Very Long Name)"])

    def test_output_ways(self):
        """WASAPI shared with Windows converting to the device's rate first, then DirectSound, MME, WDM-KS."""
        m = dr.Monitor("Speakers (Realtek(R) Audio)")
        self.assertEqual([c[1:] for c in m.candidates()], [(5, "Windows WASAPI"), (4, "Windows DirectSound"),
                                                           (1, "MME"), (9, "Windows WDM-KS")])
        self.assertTrue(m.start())
        kw = self.sd.outputs_opened[-1].kw
        self.assertEqual((kw["device"], kw["samplerate"], kw["channels"], kw["dtype"], kw["extra_settings"].auto_convert),
                         (5, 44100, 2, "float32", True))
        self.assertEqual((m.on, m.api, m.name), (True, "WASAPI", "Speakers (Realtek(R) Audio)"))
        m.stop()
        self.assertTrue(self.sd.outputs_opened[-1].closed)
        self.sd.fail_out = {5, 4}
        self.assertTrue(m.start())
        self.assertEqual((self.sd.outputs_opened[-1].kw["device"], m.api), (1, "MME"))
        self.assertTrue(all(o.closed for o in self.sd.outputs_opened[:-1]))  # The failed tries let go
        m.output = "Headphones (USB Headset With a"  # As MME names it: the same device
        self.sd.fail_out = set()
        self.assertTrue(m.start())
        self.assertEqual(self.sd.outputs_opened[-1].kw["device"], 6)

    def test_system_default(self):
        """Each host API's default output, WASAPI's first, Windows' aliases as the fallbacks; none at all if Windows
        has the Deluge as its default output (the aliases would lead there)."""
        m = dr.Monitor()
        self.assertEqual([c[1] for c in m.candidates()], [5, 3, 0, 9])
        self.assertTrue(m.start())
        self.assertEqual(self.sd.outputs_opened[-1].kw["device"], 5)
        sys.modules["sounddevice"] = FakeSd([("Microsoft Sound Mapper - Output", "MME", 0, 2),
                                             ("Speakers (Deluge)", "Windows WASAPI", 0, 2),
                                             ("Speakers (Realtek(R) Audio)", "Windows WASAPI", 0, 2)])
        self.assertEqual(m.candidates(), [])
        self.assertFalse(m.start())

    def test_input_feeds_the_monitor_only_when_on(self):
        e = self.engine()
        feed(e, [samples24(1024)])
        self.assertEqual(e.monitor.buffer.written, 0)
        self.assertTrue(e.monitor.start())
        blocks = [samples24(1024) for _ in range(2)]
        feed(e, blocks)
        self.assertEqual(e.monitor.buffer.written, 2048)
        out = np.empty((2048, 2), np.float32)
        e.monitor.play(out, 2048, None, None)
        want, fade = np.concatenate(blocks).astype(np.float64) / 2 ** 31, dr.MONITOR_FADE
        self.assertTrue(np.array_equal(out[fade:], want[fade:]))  # Exact after the fade-in
        self.assertTrue(np.allclose(out[:fade], want[:fade] * (np.arange(1, fade + 1) / fade)[:, None], rtol=1e-6,
                                    atol=0))
        self.sd.outputs_opened[-1].active = False  # Headphones unplugged
        self.assertTrue(e.monitor.lost())
        self.assertFalse(e.monitor.on)
        settle(e)

    def test_no_clicks(self):
        """A sine through the monitor: it fades in, skips ahead crossfaded when too far behind, fades out when run dry
        and fades in again. Nowhere a step larger than the sine's own (a click)."""
        b, pos, got = dr.MonitorBuffer(), 0, []
        sine = np.round(np.sin(2 * np.pi * 440 * np.arange(dr.RATE) / dr.RATE) * 2 ** 22).astype(np.int64) << 8
        x = np.repeat(sine[:, None], 2, axis=1).astype(np.int32)  # Half of full scale

        def push(n):
            nonlocal pos
            b.push(x[pos:pos + n])
            pos += n

        def pull(n):
            o = np.empty((n, 2), np.float32)
            b.pull(o)
            got.append(o[:, 0])

        push(dr.MONITOR_TARGET)
        for _ in range(4):
            pull(441)
            push(441)
        push(dr.MONITOR_LIMIT)  # A burst: too far behind
        self.assertIsNotNone(b.skip_to)
        for _ in range(4):
            pull(441)
            push(441)
        pull(3 * dr.MONITOR_LIMIT)  # Run dry
        push(dr.MONITOR_TARGET)
        for _ in range(4):
            pull(441)
        y = np.concatenate(got).astype(np.float64)
        self.assertEqual((b.drops, b.underruns), (1, 1))
        self.assertGreater(np.abs(y).max(), 0.49)
        self.assertLess(np.abs(np.diff(y)).max(), 1.1 * np.pi * 440 / dr.RATE)  # The sine's largest step: 0.031

    def test_no_clicks_with_small_blocks(self):
        """Blocks of 1024 in, 512 or 64 out: the buffer runs dry exactly at a block's end, and a skip ahead spans many
        blocks. The last frame still fades out, and the crossfade still takes MONITOR_FADE frames."""
        sine = np.round(np.sin(2 * np.pi * 440 * np.arange(dr.RATE) / dr.RATE) * 2 ** 22).astype(np.int64) << 8
        x = np.repeat(sine[:, None], 2, axis=1).astype(np.int32)
        for n in (512, 64):
            b, got = dr.MonitorBuffer(), []

            def pull(count):
                o = np.empty((count, 2), np.float32)
                b.pull(o)
                got.append(o[:, 0])
            for i in range(4):
                b.push(x[i * 1024:(i + 1) * 1024])
            for _ in range(4 * 1024 // n):
                pull(n)
            for i in range(4, 12):  # Now too far behind: a skip ahead, then running dry at a block's end
                b.push(x[i * 1024:(i + 1) * 1024])
            for _ in range(8 * 1024 // n + 8):
                pull(n)
            y = np.concatenate(got).astype(np.float64)
            self.assertEqual((b.drops, b.underruns), (1, 1), n)
            self.assertLess(np.abs(np.diff(y)).max(), 1.1 * np.pi * 440 / dr.RATE, n)
            self.assertEqual(y[-1], 0.0)

    def test_the_monitor_opens_and_closes_with_the_input(self):
        """PortAudio restarts when the input opens (so a Deluge plugged in later shows up): the monitor's output is
        closed then and opens again after it. When the Deluge goes, the output closes and MON stays on."""
        e = self.engine()
        e.connected, m = False, e.monitor
        self.assertTrue(m.start())
        self.assertTrue(e.connect())
        self.assertEqual(self.sd.open_at_terminate, [])  # Nothing open while PortAudio restarted
        self.assertEqual(len(self.sd.outputs_opened), 2)  # Closed before, opened again after
        self.assertTrue(m.on and m.stream is self.sd.outputs_opened[-1] and not m.stream.closed)
        self.assertIn("MON SPEAKERS (REALTEK(R) AUDIO)", e.events)
        e.disconnect()
        self.assertTrue(m.on)
        self.assertIsNone(m.stream)
        self.assertTrue(all(o.closed for o in self.sd.outputs_opened))
        feed(e, [samples24(1024)])
        self.assertEqual(m.buffer.written, 0)  # Nothing piles up without an output
        self.assertTrue(e.connect())
        self.assertFalse(m.stream.closed)
        settle(e)

    def test_menu_and_keys(self):
        """OUT opens the list on the OLED, up/down or the knob move, Enter (or OUT, or a click on the knob) chooses
        and switches the monitor on; Esc leaves the list; MON switches it off and on; the settings keep the choice."""
        old_tk = sys.modules.get("tkinter")
        sys.modules["tkinter"] = tk = FakeTk()
        try:
            e = self.engine()
            root = tk.Tk()
            app = dr.App(root, e, 1.0, settings=self.dir / "settings.json")
            app.boot_until = 0
            app.press("out")
            self.assertEqual(app.menu["items"][1:], ["Speakers (Realtek(R) Audio)",
                                                     "Headphones (USB Headset With a Very Long Name)"])
            app.tick()
            self.assertTrue(app.oled.fb.any())
            root.bindings["<Down>"](None)
            root.bindings["<Down>"](None)
            root.bindings["<Down>"](None)  # Stops at the last
            root.bindings["<Escape>"](None)
            self.assertIsNone(app.menu)
            self.assertFalse(e.monitor.on)
            app.press("out")
            app.turn(-1)
            root.bindings["<Return>"](None)
            self.assertEqual((e.monitor.output, e.monitor.on), ("Speakers (Realtek(R) Audio)", True))
            self.assertEqual(json.loads((self.dir / "settings.json").read_text()),
                             {"output": "Speakers (Realtek(R) Audio)", "monitor": True, "threshold": -40,
                              "volume": 0})
            app.tick()
            app.press("mon")
            self.assertFalse(e.monitor.on)
            app.press("mon")
            self.assertTrue(e.monitor.on)
            app.press("out")
            app.grab_knob(types.SimpleNamespace(y=0))  # A click on the knob chooses
            self.assertIsNone(app.menu)
            e.disconnect()
            app.press("mon")
            app.press("mon")  # On without the Deluge: MON waits for it
            self.assertTrue(e.monitor.on)
            self.assertIsNone(e.monitor.stream)
            self.assertTrue(e.connect())  # The Deluge is there: the output opens
            self.assertIsNotNone(e.monitor.stream)
            app.quit()
            self.assertFalse(e.monitor.on)
        finally:
            if old_tk is not None:
                sys.modules["tkinter"] = old_tk
            else:
                sys.modules.pop("tkinter", None)


class Demo(EngineCase):
    def test_demo_take(self):
        e = dr.Engine(self.dir, demo=True)
        self.engines.append(e)
        self.assertTrue(e.connect())
        self.assertEqual(e.api, "DEMO")
        e.command("rec")
        time.sleep(0.6)
        self.assertEqual(e.take_bits(), 24)
        self.assertGreater(max(e.take_levels()), -30)
        e.command("stop")
        wait(lambda: e.state == "idle")
        path = self.dir / e.last_take[0]
        riff, fmt, size, data = parse_wav(path)
        self.assertEqual((size, riff), (len(data), path.stat().st_size - 8))
        self.assertGreater(size // 6, 44100 * 0.4)
        e.disconnect()
        self.assertFalse(e.connected)


if __name__ == "__main__":
    unittest.main(verbosity=1)
