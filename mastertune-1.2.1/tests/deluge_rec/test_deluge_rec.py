#!/usr/bin/env python3
"""Tests for tools/deluge_rec.py without audio hardware and without a display: sounddevice and tkinter are stubs.

Covers the WAV writer (byte for byte, header, header during the take, 4 GB split, stop, quit, disk error), ARM with
pre-roll (to the frame), file numbering, the meter (dBFS, pads, bit depth), the choice of the input (Windows WASAPI
exclusive first and the fallbacks, macOS, Linux) and --demo. Needs only numpy.
"""
import importlib.util
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


def parse_wav(path):
    raw = Path(path).read_bytes()
    assert raw[:4] == b"RIFF" and raw[8:16] == b"WAVEfmt ", raw[:16]
    riff, = struct.unpack("<I", raw[4:8])
    fmt = struct.unpack("<IHHIIHH", raw[16:36])
    assert raw[36:40] == b"data"
    size, = struct.unpack("<I", raw[40:44])
    return riff, fmt, size, raw[44:]


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
        self.assertEqual(path.name, "USB00001.WAV")
        riff, fmt, size, data = parse_wav(path)
        frames = sum(map(len, blocks))
        self.assertEqual(fmt, (16, 1, 2, 44100, 44100 * 6, 6, 24))
        self.assertEqual((size, riff, len(data)), (frames * 6, 36 + frames * 6, frames * 6))
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
        self.assertEqual(parse_wav(self.dir / "USB00001.WAV")[3], expected_bytes(blocks))

    def test_4gb_goes_on_in_the_next_file(self):
        old = dr.MAX_DATA_BYTES
        dr.MAX_DATA_BYTES = 1000 * 6
        try:
            e = self.engine()
            blocks = [samples24(300) for _ in range(9)]
            self.take(e, blocks)
        finally:
            dr.MAX_DATA_BYTES = old
        files = sorted(self.dir.iterdir())
        self.assertEqual([p.name for p in files], ["USB00001.WAV", "USB00002.WAV", "USB00003.WAV"])
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


class Numbering(EngineCase):
    def test_next_number_and_no_overwrite(self):
        for name in ("USB00007.WAV", "usb00003.wav", "USB12.WAV", "REC00050.WAV", "USB00009.WAV.tmp"):
            (self.dir / name).write_bytes(b"keep " + name.encode())
        e = self.engine()
        self.assertEqual(self.take(e, [samples24(10)]).name, "USB00008.WAV")
        self.assertEqual(self.take(e, [samples24(10)]).name, "USB00009.WAV")
        for name in ("USB00007.WAV", "usb00003.wav", "USB12.WAV", "REC00050.WAV", "USB00009.WAV.tmp"):
            self.assertEqual((self.dir / name).read_bytes(), b"keep " + name.encode())

    def test_existing_name_is_skipped(self):
        e = self.engine()
        (self.dir / "USB00001.WAV").write_bytes(b"keep")
        e.next_number = lambda: 1  # Another program took the number in between
        self.assertEqual(self.take(e, [samples24(10)]).name, "USB00002.WAV")
        self.assertEqual((self.dir / "USB00001.WAV").read_bytes(), b"keep")

    def test_past_99999(self):
        (self.dir / "USB99999.WAV").write_bytes(b"keep")
        e = self.engine()
        self.assertEqual(self.take(e, [samples24(10)]).name, "USB100000.WAV")
        self.assertEqual(self.take(e, [samples24(10)]).name, "USB100001.WAV")


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
        items = {}

        class Canvas:
            def __init__(self, *a, **kw):
                self.items = items

            def _new(self, *a, **kw):
                items[len(items) + 1] = dict(kw)
                return len(items)
            create_rectangle = create_oval = create_line = create_image = _new

            def itemconfigure(self, item, **kw):
                items[item].update(kw)

            def pack(self, *a, **kw): pass
            def tag_bind(self, *a, **kw): pass
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
            def title(self, *a): pass
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
        """The keyboard: R (and the space bar) REC, A ARM, S (and Esc) STOP, F (and O) FOLDER, upper and lower case."""
        e = self.engine()
        root = self.tk.Tk()
        app = dr.App(root, e, 1.0)
        pressed = []
        app.press = pressed.append
        want = {"r": "rec", "R": "rec", "<space>": "rec", "a": "arm", "A": "arm", "s": "stop", "S": "stop",
                "<Escape>": "stop", "f": "folder", "F": "folder", "o": "folder", "O": "folder"}
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


class FakeSd(types.ModuleType):
    """sounddevice with made-up devices: (name, host API, input channels)."""

    def __init__(self, devices, fail=()):
        super().__init__("sounddevice")
        self.apis = sorted({d[1] for d in devices})
        self.devices = [{"name": n, "hostapi": self.apis.index(a), "max_input_channels": c} for n, a, c in devices]
        self.fail, self.opened = set(fail), []
        self.WasapiSettings = lambda exclusive: types.SimpleNamespace(exclusive=exclusive)
        self.InputStream = lambda **kw: FakeStream(self, **kw)

    def query_devices(self): return self.devices
    def query_hostapis(self): return [{"name": a} for a in self.apis]
    def _terminate(self): pass
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

    def test_sends_nothing(self):
        code = SRC.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"OutputStream|RawStream|sd\.Stream\b|\.play\(|rtmidi|mido|sysex", code, re.I))


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
        riff, fmt, size, data = parse_wav(self.dir / "USB00001.WAV")
        self.assertEqual((size, riff), (len(data), 36 + len(data)))
        self.assertGreater(size // 6, 44100 * 0.4)
        e.disconnect()
        self.assertFalse(e.connected)


if __name__ == "__main__":
    unittest.main(verbosity=1)
