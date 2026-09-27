#!/usr/bin/env python3
"""DELUGE USB REC: nimmt den USB-Audio-Ausgang des Deluge auf, im Look des Deluge.

Der Deluge (mastertune ab v8: Settings > Community features > USB audio an, neu starten) erscheint am Computer als
Audio-Eingang «Deluge», Stereo, 24 Bit, 44,1 kHz. Dieses Programm nimmt genau das auf und schreibt WAV-Dateien mit
24 Bit, ohne Umrechnung. Es öffnet nur diesen Eingang und sendet nichts an den Deluge.

Installieren:  pip install sounddevice numpy        (tkinter gehört zu Python)
Starten:       python deluge_rec.py                 (--out ORDNER, --list, --demo ohne Deluge, --help)

Bedienung (Maus oder Tasten):
  REC     R, Leertaste   Aufnahme starten oder beenden
  ARM     A              Aufnahme startet von selbst, sobald das Signal über die Schwelle steigt (0,3 s Vorlauf)
  STOP    S, Esc         Aufnahme beenden, die Datei ist dann fertig
  FOLDER  F, O           Aufnahme-Ordner öffnen
  Knopf   Mausrad, ziehen, Pfeil auf/ab, + -   Schwelle für ARM, -60 bis -12 dBFS
Die Dateien heissen USB00001.WAV, USB00002.WAV ... wie die Aufnahmen am Deluge (REC00001.WAV). Standardordner:
Musik/Deluge USB im Benutzerordner.

Oben rechts steht der Weg: WASAPI EXCL (Windows, exklusiv, bitgenau), WASAPI SHARED, CORE AUDIO, ALSA ... Das Display
zeigt die Bittiefe, die wirklich ankommt: 24B heisst bitgenau. 16B oder 32B deuten auf eine Umrechnung, unter Windows
meist ein Eingangspegel unter 100 % oder Audio-Verbesserungen: den Pegel des Eingangs «Deluge» auf 100 % stellen oder
ohne --shared starten. Die Pads zeigen den Pegel links und rechts in 3-dB-Schritten ab -45 dBFS, das letzte blinkt rot
bei Vollaussteuerung.
"""
import argparse
import collections
import math
import os
import queue
import re
import signal
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

RATE = 44100
CHANNELS = 2
FULL_SCALE = 2 ** 31            # The 24-bit samples arrive left-justified in int32
PREROLL = round(0.3 * RATE)     # ARM: the frames kept before the first sample over the threshold
MAX_DATA_BYTES = 4_000_000_000  # WAV sizes are 32-bit: after 4 GB (about 4 h 11 min) a take goes on in the next file
HEADER_EVERY = 2 * RATE         # The WAV header is brought up to date every 2 s: a crash leaves a readable file
THRESH_MIN, THRESH_MAX = -60, -12

# --- the look: panel, OLED, pads (the Deluge's colours), lettering

PANEL, PLATE, EDGE, BEZEL, LABEL, SMALL = "#0e0e10", "#18181b", "#26262b", "#050506", "#d8d8de", "#8c8c96"
OLED_ON, OLED_OFF = (226, 238, 255), (7, 9, 13)
PAD_COLOURS = ["#2fdc6e"] * 9 + ["#b8e636", "#f2d22e", "#f2d22e", "#ff9f1c", "#ff7a1c", "#ff5a1f", "#ff2d2d"]
ICON_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAB8klEQVR4nO2bsVLCQBCG/2SsGbVEwZ7KwtoH4BXw8fQVeADH0oIq"
            "vUTT6vAAaOEsXC6Xu4QElr3sV5HcDbP7sXNzy8wmaMBsdv/bZN+5kWWrJLTHu0Fq4jY+Ec4FO/GiWPcd00kYj6elZ5eIygszeamJ"
            "25gibAmp+RBj8kA5F7u6U9dCTMkTdRIufBslMMe28m5ZLuwdRbGunAsJsDciKXlK/GUyqawt8hxAvQiSkGWrpFIBEphj60ycoLVF"
            "ntdKIBJpv34oeZs6CVQFfj1nRtvkgf9qcJ0ThBgBhyRP+CQEz4DR6NK7vtn8lJ6/Jzfe/Vf5V6fv7xsxFXAsVAB3ANyoAO4AmrJE"
            "urvhtcV3IRIjADhMQug2KEoAALyNrhtLiPIqTHRphoD9VVhkMwTsk3NVQ+hXNxErgGiTrAtxZ0Df9N4LrF9vvfunj5+d9vfdOwy+"
            "AlQAdwDcqADuALhRAdwBcCO2F+iKyL/Fj4EK4A6Am2AvcPf+5F3/eHg+6X7tBXpGBXAHwI0K4A6AGxXAHQA32gswx8GOCuAOgBtx"
            "vUDb/SEGXwEqgDsAblQAdwDcqACaobEHCWLGnBcQOzDRBVNASh/MhZgxkwccZ0DMEly57QSY83QxSqibHSxVQKwSfIOTOjrr+4LB"
            "Dk/bSBXRZHz+D/TV1SQk6UmAAAAAAElFTkSuQmCC")  # 64 x 64, the pads and the REC LED

# A 5 x 7 pixel font: 7 rows of 5 bits per glyph
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
}
GLYPHS = {c: np.array([[b == "1" for b in row] for row in rows.split()], bool) for c, rows in FONT.items()}

# 7-segment digits for the big timer: segments a b c d e f g
SEGMENTS = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgedc",
            "7": "abc", "8": "abcdefg", "9": "abfgcd", "-": "g", " ": ""}


class Oled:
    """The Deluge's OLED: 128 x 48 pixels, drawn with a visible pixel grid and scanlines."""
    W, H = 128, 48

    def __init__(self, scale):
        self.scale = s = scale
        self.fb = np.zeros((self.H, self.W), bool)
        row, col = np.ones(self.H * s), np.ones(self.W * s)
        if s >= 3:
            row[s - 1::s] = 0.45  # Scanlines
            col[s - 1::s] = 0.8   # The pixel grid
        self.shade = (row[:, None] * col[None, :])[..., None]
        self.header = b"P6 %d %d 255\n" % (self.W * s, self.H * s)

    def clear(self):
        self.fb[:] = False

    @staticmethod
    def width(s, size=1):
        return len(s) * 6 * size - size

    def text(self, x, y, s, size=1, invert=False):
        for ch in s.upper():
            g = GLYPHS.get(ch, GLYPHS["?"])
            if size > 1:
                g = np.repeat(np.repeat(g, size, 0), size, 1)
            h, w = g.shape
            x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, self.W), min(y + h, self.H)
            if x1 > x0 and y1 > y0:
                part = g[y0 - y:y1 - y, x0 - x:x1 - x]
                if invert:
                    self.fb[y0:y1, x0:x1] = ~part
                else:
                    self.fb[y0:y1, x0:x1] |= part
            x += w + size
        return x

    def rect(self, x, y, w, h, on=True):
        self.fb[max(y, 0):max(y + h, 0), max(x, 0):max(x + w, 0)] = on

    def seven_seg(self, x, y, s, w=11, h=21, t=3):
        """Big 7-segment digits, ':' and '.' for the timer."""
        m = (h - t) // 2
        for ch in s:
            if ch == ":":
                self.rect(x + 1, y + h // 3 - 1, t, t)
                self.rect(x + 1, y + 2 * h // 3 - 1, t, t)
                x += t + 4
            elif ch == ".":
                self.rect(x, y + h - t, t, t)
                x += t + 3
            else:
                parts = {"a": (x + 1, y, w - 2, t), "d": (x + 1, y + h - t, w - 2, t), "g": (x + 1, y + m, w - 2, t),
                         "f": (x, y + 1, t, m), "b": (x + w - t, y + 1, t, m),
                         "e": (x, y + m + 1, t, h - m - 2), "c": (x + w - t, y + m + 1, t, h - m - 2)}
                for k in SEGMENTS.get(ch, ""):
                    self.rect(*parts[k])
                x += w + 4
        return x

    def ppm(self):
        s = self.scale
        big = np.repeat(np.repeat(self.fb, s, 0), s, 1)[..., None]
        return self.header + (np.where(big, OLED_ON, OLED_OFF) * self.shade).astype(np.uint8).tobytes()


# --- audio

def to_db(peak):
    return 20 * math.log10(peak / FULL_SCALE) if peak > 0 else -math.inf


def pack24(block):
    """int32 samples (24 bits left-justified) as little-endian 3-byte samples. Rounded, not cut off: a sample that
    arrives exactly stays exactly the same, and one that went through float on the way (WASAPI shared, Core Audio)
    comes back to its 24-bit value instead of one step below."""
    v = (block.astype(np.int64) + 128) >> 8
    np.clip(v, -2 ** 23, 2 ** 23 - 1, out=v)
    return v.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()


class WavFile:
    """A WAV file, stereo, 24 bits, 44.1 kHz. Never overwrites a file. The header gets the current size every 2 s, so
    after a crash or a closed console the file holds everything up to shortly before."""

    def __init__(self, path):
        self.f = open(path, "xb")
        self.frames, self.next_header = 0, HEADER_EVERY
        self.f.write(self.header())

    def header(self):
        data = self.frames * CHANNELS * 3
        return b"RIFF%sWAVEfmt %sdata%s" % (struct.pack("<I", 36 + data), struct.pack(
            "<IHHIIHH", 16, 1, CHANNELS, RATE, RATE * CHANNELS * 3, CHANNELS * 3, 24), struct.pack("<I", data))

    def write(self, block):
        self.f.write(pack24(block))
        self.frames += len(block)
        if self.frames >= self.next_header:
            self.next_header = self.frames + HEADER_EVERY
            self.update_header()

    def update_header(self):
        end = self.f.tell()
        self.f.seek(0)
        self.f.write(self.header())
        self.f.seek(end)
        self.f.flush()

    def close(self):
        try:
            self.update_header()
        finally:
            self.f.close()


class DemoStatus:
    input_overflow = False


class DemoInput:
    """A stand-in for the Deluge to try the program: a plucked phrase with hats, 2.5 s playing, 1.5 s silence."""

    def __init__(self, callback):
        self.callback, self.running, self.pos = callback, False, 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.running = True
        self.thread.start()

    def close(self):
        self.running = False

    def block(self, n):
        t = (self.pos + np.arange(n)) / RATE
        self.pos += n
        cycle = t % 4.0
        since = (cycle * 2) % 1.0 / 2                   # Seconds since the last eighth note (120 BPM)
        note = (146.83, 220.0, 196.0, 293.66)[int(cycle[0] * 2) % 4]
        pluck = sum(np.sin(2 * np.pi * note * k * t) / k for k in (1, 2, 3, 5)) * np.exp(-since * 7)
        hat = np.random.default_rng(self.pos).standard_normal(n) * np.exp(-since * 60) * 0.2
        on = cycle < 2.5
        left = np.where(on, 0.2 * pluck + hat, 0.0)
        right = np.where(on, 0.18 * pluck - 0.6 * hat, 0.0)
        s = np.clip(np.stack([left, right], 1), -1, 1) * (2 ** 23 - 1)
        return np.round(s).astype(np.int32) << 8

    def run(self):
        start, sent = time.monotonic(), 0
        while self.running:
            self.callback(self.block(441), 441, None, DemoStatus())
            sent += 441
            wait = start + sent / RATE - time.monotonic()
            if wait > 0:
                time.sleep(wait)


class Engine:
    """Opens the Deluge's input and records it. The audio callback only measures and queues the blocks; a writer
    thread keeps the pre-roll and writes the file."""

    def __init__(self, out_dir, device=None, shared=False, demo=False, threshold=-40):
        self.out_dir, self.device, self.shared, self.demo = Path(out_dir), device, shared, demo
        self.threshold_db = threshold
        self.stream, self.api, self.connected, self.last_cb = None, "", False, 0.0
        self.state = "idle"            # idle, armed, rec
        self.q, self.cmd = queue.SimpleQueue(), queue.SimpleQueue()
        self.peak = np.zeros(CHANNELS, np.int64)
        self.or_bits = 0
        self.overflows, self.gaps = 0, 0  # Overflows: for the display; gaps: all, for the take's line in the console
        self.take_gaps = 0
        self.wav, self.name, self.frames = None, "", 0
        self.last_take = None          # (name, seconds, bytes)
        self.events = collections.deque(maxlen=8)
        self.stop_writer = False
        self.writer = threading.Thread(target=self.write_loop, daemon=True)
        self.writer.start()

    # --- the connection

    def candidates(self):
        import sounddevice as sd
        devices, apis = sd.query_devices(), sd.query_hostapis()
        rank = {"Windows WASAPI": 0, "Core Audio": 0, "ALSA": 0, "JACK Audio Connection Kit": 1,
                "Windows WDM-KS": 2, "Windows DirectSound": 3, "MME": 4}
        want = self.device if self.device is not None else "deluge"
        found = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] >= CHANNELS and (
                    (want.isdigit() and i == int(want)) or (not want.isdigit() and want.lower() in d["name"].lower())):
                api = apis[d["hostapi"]]["name"]
                found.append((rank.get(api, 5), i, api))
        return sorted(found)

    def connect(self):
        """Tries to open the Deluge's input; True once it is open."""
        if self.connected:
            return True
        if self.demo:
            self.stream, self.api = DemoInput(self.callback), "DEMO"
            self.stream.start()
            self.connected, self.last_cb = True, time.monotonic()
            return True
        import sounddevice as sd
        try:
            sd._terminate()  # PortAudio lists the devices only when it starts: a Deluge plugged in later shows up so
            sd._initialize()
        except Exception:
            pass
        for _, index, api in self.candidates():
            modes = ([] if self.shared else [True]) + [False] if api == "Windows WASAPI" else [None]
            for exclusive in modes:
                stream = None
                try:
                    extra = sd.WasapiSettings(exclusive=exclusive) if exclusive is not None else None
                    stream = sd.InputStream(device=index, channels=CHANNELS, samplerate=RATE, dtype="int32",
                                            blocksize=1024, latency="high", callback=self.callback,
                                            extra_settings=extra, dither_off=True)
                    stream.start()
                except Exception:
                    if stream is not None:  # Opened but did not start: let go, or it blocks the next way
                        try:
                            stream.close()
                        except Exception:
                            pass
                    continue
                self.stream = stream
                self.api = {"Windows WASAPI": "WASAPI " + ("EXCL" if exclusive else "SHARED"),
                            "Windows DirectSound": "DSOUND", "Windows WDM-KS": "WDM-KS"}.get(api, api.upper())
                self.connected, self.last_cb = True, time.monotonic()
                return True
        return False

    def disconnect(self):
        """The Deluge went away, or the program ends: a take in progress is finished and saved."""
        if self.state != "idle":
            self.command("stop")
        stream, self.stream, self.connected = self.stream, None, False
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    # --- the audio thread

    def callback(self, indata, frames, time_info, status):
        if status is not None and status.input_overflow:  # PortAudio lost samples before this block
            self.overflows += 1
            self.gaps += 1
        block = np.array(indata, dtype=np.int32, copy=True)
        self.peak = np.maximum(self.peak, np.abs(block.astype(np.int64)).max(axis=0))
        self.or_bits |= int(np.bitwise_or.reduce(block, axis=None)) & 0xFFFFFFFF
        self.last_cb = time.monotonic()
        self.q.put(block)

    # --- for the display

    def take_levels(self):
        peak, self.peak = self.peak, np.zeros(CHANNELS, np.int64)
        return [to_db(int(p)) for p in peak]

    def take_bits(self):
        """The bit depth that arrived since the last call (from the lowest bit set in any sample); None in silence."""
        bits, self.or_bits = self.or_bits, 0
        return 32 - ((bits & -bits).bit_length() - 1) if bits else None

    def command(self, c):
        self.cmd.put(c)

    def seconds(self):
        return self.frames / RATE

    # --- the writer thread

    def next_number(self):
        used = [int(m.group(1)) for p in self.out_dir.iterdir()
                if (m := re.fullmatch(r"USB(\d{5,})\.WAV", p.name, re.I))]
        return max(used, default=0) + 1

    def open_file(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        n = self.next_number()
        while True:  # A file of the same name (another program, another case) is never overwritten
            name = f"USB{n:05d}.WAV"
            try:
                self.wav = WavFile(self.out_dir / name)
                break
            except FileExistsError:
                n += 1
        self.name, self.frames, self.take_gaps, self.state = name, 0, self.gaps, "rec"

    def write(self, block):
        if self.wav is None or not len(block):
            return
        self.wav.write(block)
        self.frames += len(block)
        if self.frames * CHANNELS * 3 >= MAX_DATA_BYTES:  # The take goes on without a gap in the next file
            self.close_file()
            self.open_file()
            self.events.append("4GB: NEXT FILE")

    def close_file(self):
        wav, self.wav = self.wav, None
        if wav is not None:
            wav.close()
            self.last_take = (self.name, self.seconds(), self.frames * CHANNELS * 3)
            self.events.append("SAVED " + self.name[:-4])
            gaps = self.gaps - self.take_gaps
            print(f"gespeichert: {self.out_dir / self.name} ({self.seconds():.1f} s"
                  + (f", {gaps} Lücken: der Computer war zu langsam)" if gaps else ")"), flush=True)
        self.state = "idle"

    def drain(self):
        """Writes the blocks that are still queued: they were recorded before the take was stopped."""
        while True:
            try:
                self.write(self.q.get_nowait())
            except queue.Empty:
                return

    def trigger(self, preroll, block):
        """ARM: the first sample over the threshold starts the take, with exactly PREROLL frames before it (fewer only
        if ARM was pressed less than 0.3 s before). True once the take runs."""
        over = FULL_SCALE * 10 ** (self.threshold_db / 20)
        loud = np.flatnonzero(np.abs(block.astype(np.int64)).max(axis=1) >= over)
        if not len(loud):
            return False
        before = np.concatenate([*preroll, block[:loud[0]]])
        self.open_file()
        self.write(before[len(before) - PREROLL:] if len(before) > PREROLL else before)
        self.write(block[loud[0]:])
        return True

    def write_loop(self):
        preroll, preroll_frames = collections.deque(), 0
        while not self.stop_writer:
            try:
                try:
                    while True:
                        c = self.cmd.get_nowait()
                        if c == "rec" and self.state != "rec":
                            self.open_file()
                        elif c == "arm" and self.state == "idle":
                            preroll.clear()
                            preroll_frames = 0
                            self.state = "armed"
                        elif c == "stop":
                            if self.state == "rec":
                                self.drain()
                            self.close_file()
                except queue.Empty:
                    pass
                try:
                    block = self.q.get(timeout=0.05)
                except queue.Empty:
                    continue
                if self.state == "rec":
                    self.write(block)
                elif self.state == "armed":
                    if self.trigger(preroll, block):
                        preroll.clear()
                        preroll_frames = 0
                    else:  # Keep at least PREROLL frames, without the oldest block if it is not needed
                        preroll.append(block)
                        preroll_frames += len(block)
                        while preroll_frames - len(preroll[0]) >= PREROLL:
                            preroll_frames -= len(preroll.popleft())
            except Exception as e:  # A full or removed disk: the take ends as far as it got, the program goes on
                try:
                    self.close_file()
                except Exception:
                    self.wav, self.state = None, "idle"
                self.events.append("DISK ERROR")
                print(f"Fehler beim Schreiben: {e}", flush=True)
        self.close_file()

    def shutdown(self):
        self.disconnect()
        self.command("stop")
        deadline = time.monotonic() + 3
        while self.state != "idle" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.stop_writer = True
        self.writer.join(timeout=3)


# --- the window

class App:
    def __init__(self, root, engine, z):
        import tkinter as tk
        self.root, self.engine, self.z = root, engine, z
        self.oled = Oled(max(3, round(3 * z)))
        s = self.oled.scale / 3  # Everything follows the OLED's size
        Z = self.Z = lambda v: int(round(v * s))  # noqa: E731
        self.boot_until = time.monotonic() + 1.4
        self.levels, self.holds = [-math.inf] * CHANNELS, [(-math.inf, 0.0)] * CHANNELS
        self.clip_until = 0.0
        self.bits, self.bits_at, self.bits_next = None, 0.0, 0.0
        self.message, self.message_until = "", 0.0
        self.next_connect = 0.0
        self.pad_fill, self.api_text, self.drag_y = {}, None, None
        self.warned_import = False

        self.W, self.H = Z(440), Z(366)
        root.title("DELUGE USB REC")
        self.icon = tk.PhotoImage(data=ICON_PNG)
        root.iconphoto(True, self.icon)
        root.configure(bg=PANEL)
        root.resizable(False, False)
        c = self.c = tk.Canvas(root, width=self.W, height=self.H, bg=PANEL, highlightthickness=0)
        c.pack()
        c.create_rectangle(Z(8), Z(8), self.W - Z(8), self.H - Z(8), fill=PLATE, outline=EDGE, width=Z(1))
        self.label(Z(26), Z(16), "DELUGE", Z(3))
        self.label(Z(26) + self.label_width("DELUGE", Z(3)) + Z(12), Z(23), "USB REC", Z(2))
        # The OLED in its bezel
        ox, oy = Z(28), Z(52)
        c.create_rectangle(ox - Z(6), oy - Z(6), ox + Z(384) + Z(6), oy + Z(144) + Z(6), fill=BEZEL, outline=EDGE)
        self.img = tk.PhotoImage(width=self.oled.W * self.oled.scale, height=self.oled.H * self.oled.scale)
        c.create_image(ox, oy, image=self.img, anchor="nw")
        # Pads: the level of the left and right channel
        self.pads = []
        py = Z(214)
        for ch, name in enumerate("LR"):
            self.label(Z(12), py + ch * Z(26) + Z(3), name, Z(2))
            row = []
            for i in range(16):
                x, y = Z(30) + i * Z(24), py + ch * Z(26)
                row.append(c.create_rectangle(x, y, x + Z(20), y + Z(20), fill=self.dim(PAD_COLOURS[i]),
                                              outline="#0a0a0c", width=Z(1)))
            self.pads.append(row)
        # Round buttons with their LEDs, and the gold knob
        by = Z(304)
        self.buttons = {}
        for key, x, colour, text, letter in (("rec", 56, "#ff2d2d", "REC", "R"), ("arm", 124, "#ffae1c", "ARM", "A"),
                                             ("stop", 192, "#e8e8f0", "STOP", "S"),
                                             ("folder", 266, "#3d8bff", "FOLDER", "F")):
            cx = Z(x)
            ring = c.create_oval(cx - Z(17), by - Z(17), cx + Z(17), by + Z(17), fill="#26262a", outline="#3c3c42",
                                 width=Z(2))
            led = c.create_oval(cx - Z(6), by - Z(6), cx + Z(6), by + Z(6), fill=self.dim(colour, 0.22), outline="")
            self.label(cx - self.label_width(text, Z(2)) // 2, by + Z(26), text, Z(2))
            # Its key on the computer's keyboard, small underneath
            self.label(cx - self.label_width(letter, Z(1)) // 2, by + Z(44), letter, Z(1), fill=SMALL)
            for item in (ring, led):
                c.tag_bind(item, "<Button-1>", lambda e, k=key: self.press(k))
                c.tag_bind(item, "<Enter>", lambda e: c.configure(cursor="hand2"))
                c.tag_bind(item, "<Leave>", lambda e: c.configure(cursor=""))
            self.buttons[key] = (led, colour)
        kx = Z(374)
        self.knob = (kx, by)
        knob_items = [c.create_oval(kx - Z(22), by - Z(22), kx + Z(22), by + Z(22), fill="#8a6a28", outline="#4e3b14",
                                    width=Z(2)),
                      c.create_oval(kx - Z(17), by - Z(17), kx + Z(17), by + Z(17), fill="#d9b35a", outline="#f0d58c",
                                    width=Z(1))]
        self.knob_line = c.create_line(kx, by, kx, by - Z(15), fill="#2a1e08", width=Z(3), capstyle="round")
        knob_items.append(self.knob_line)
        self.label(kx - self.label_width("THRESH", Z(2)) // 2, by + Z(31), "THRESH", Z(2))
        for item in knob_items:
            c.tag_bind(item, "<Button-1>", self.grab_knob)
            c.tag_bind(item, "<B1-Motion>", self.drag_knob)
            c.tag_bind(item, "<Enter>", lambda e: c.configure(cursor="sb_v_double_arrow"))
            c.tag_bind(item, "<Leave>", lambda e: c.configure(cursor=""))
        self.update_knob()

        for keys, action in ((("r", "R", "<space>"), "rec"), (("a", "A"), "arm"), (("s", "S", "<Escape>"), "stop"),
                             (("f", "F", "o", "O"), "folder")):
            for k in keys:
                root.bind(k, lambda e, a=action: self.press(a))
        for k in ("<plus>", "<KP_Add>", "<Up>"):
            root.bind(k, lambda e: self.turn(1))
        for k in ("<minus>", "<KP_Subtract>", "<Down>"):
            root.bind(k, lambda e: self.turn(-1))
        root.bind("<MouseWheel>", lambda e: self.wheel(e, 1 if e.delta > 0 else -1))
        root.bind("<Button-4>", lambda e: self.wheel(e, 1))
        root.bind("<Button-5>", lambda e: self.wheel(e, -1))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        print(f"Aufnahmen: {engine.out_dir}", flush=True)
        self.tick()

    # --- drawing helpers

    def label(self, x, y, s, p, fill=LABEL, tag=None):
        """Lettering in the same pixel font, p screen pixels per font pixel."""
        for ch in s.upper():
            g = GLYPHS.get(ch, GLYPHS["?"])
            for r, col in zip(*np.nonzero(g)):
                self.c.create_rectangle(x + col * p, y + r * p, x + (col + 1) * p, y + (r + 1) * p, fill=fill,
                                        width=0, tags=tag)
            x += 6 * p

    @staticmethod
    def label_width(s, p):
        return len(s) * 6 * p - p

    @staticmethod
    def dim(colour, f=0.14):
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % (int(24 + (r - 24) * f), int(24 + (g - 24) * f), int(26 + (b - 26) * f))

    def update_knob(self):
        kx, ky = self.knob
        a = math.radians(-135 + 270 * (self.engine.threshold_db - THRESH_MIN) / (THRESH_MAX - THRESH_MIN))
        r = self.Z(15)
        self.c.coords(self.knob_line, kx, ky, kx + r * math.sin(a), ky - r * math.cos(a))

    def say(self, text, seconds=1.6):
        self.message, self.message_until = text, time.monotonic() + seconds

    # --- actions

    def press(self, key):
        e = self.engine
        if key == "folder":
            e.out_dir.mkdir(parents=True, exist_ok=True)
            try:
                if sys.platform.startswith("win"):
                    os.startfile(e.out_dir)
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(e.out_dir)])
            except Exception:
                self.say(str(e.out_dir)[-21:], 3)
            return
        if not e.connected:
            self.say("WAITING FOR DELUGE")
            return
        if key == "rec":
            e.command("stop" if e.state == "rec" else "rec")
        elif key == "arm":
            e.command("stop" if e.state == "armed" else "arm")
        elif key == "stop":
            e.command("stop")

    def turn(self, step):
        e = self.engine
        e.threshold_db = int(min(THRESH_MAX, max(THRESH_MIN, e.threshold_db + 2 * step)))
        self.update_knob()
        self.say(f"ARM LEVEL {e.threshold_db} DB")

    def wheel(self, event, step):
        kx, ky = self.knob
        if (event.x - kx) ** 2 + (event.y - ky) ** 2 <= self.Z(30) ** 2:
            self.turn(step)

    def grab_knob(self, event):
        self.drag_y = event.y

    def drag_knob(self, event):
        if self.drag_y is not None and abs(event.y - self.drag_y) >= self.Z(6):
            self.turn(1 if event.y < self.drag_y else -1)
            self.drag_y = event.y

    def quit(self):
        self.engine.shutdown()
        self.root.destroy()

    # --- the loop: levels, pads, LEDs, OLED, 25 times a second

    def tick(self):
        now = time.monotonic()
        e = self.engine
        if not e.connected and now >= self.next_connect:
            self.next_connect = now + 2.0
            try:
                e.connect()
            except ImportError:
                self.say("PIP INSTALL SOUNDDEVICE", 2.2)
                if not self.warned_import:
                    print("Es fehlt sounddevice: pip install sounddevice numpy", flush=True)
                    self.warned_import = True
            except Exception:
                self.say("AUDIO ERROR", 2.2)
        if e.connected and now - e.last_cb > 2.0:
            e.disconnect()
            self.say("DELUGE LOST", 3)
        while e.events:
            self.say(e.events.popleft(), 2.5)
        if e.overflows:
            e.overflows = 0
            self.say("OVERFLOW: PC TOO SLOW", 2)

        # Levels: fast attack, 24 dB/s release, the peak held for 1.5 s
        new = e.take_levels() if e.connected else [-math.inf] * CHANNELS
        for ch in range(CHANNELS):
            self.levels[ch] = max(new[ch], self.levels[ch] - 24 * 0.04)
            hold, at = self.holds[ch]
            if new[ch] >= hold or now - at > 1.5:
                self.holds[ch] = (new[ch], now)
            if new[ch] >= -0.1:
                self.clip_until = now + 1.0
        if now >= self.bits_next:  # The bit depth over the last second
            self.bits_next = now + 1.0
            bits = e.take_bits() if e.connected else None
            if bits is not None:
                self.bits, self.bits_at = bits, now
            elif now - self.bits_at > 3:
                self.bits = None

        # Pads: pad i lights from -48 + 3 (i + 1) dBFS, the last one blinks at full scale
        for ch in range(CHANNELS):
            lvl, hold = self.levels[ch], self.holds[ch][0]
            for i, item in enumerate(self.pads[ch]):
                if i == 15:
                    lit = now < self.clip_until and int(now * 8) % 2 == 0
                else:
                    low = -48 + 3 * (i + 1)
                    lit = lvl >= low or low <= hold < low + 3
                fill = PAD_COLOURS[i] if lit else self.dim(PAD_COLOURS[i])
                if self.pad_fill.get((ch, i)) != fill:
                    self.c.itemconfigure(item, fill=fill)
                    self.pad_fill[(ch, i)] = fill

        blink = int(now * 2.5) % 2 == 0
        leds = {"rec": e.state == "rec", "arm": e.state == "armed" and blink,
                "stop": e.connected and e.state == "idle", "folder": False}
        for key, (led, colour) in self.buttons.items():
            self.c.itemconfigure(led, fill=colour if leds[key] else self.dim(colour, 0.22))
        api = e.api if e.connected else "SEARCHING"
        if api != self.api_text:  # The audio path, top right, in the panel's lettering
            self.api_text = api
            self.c.delete("api")
            p = self.Z(2)
            self.label(self.W - self.Z(26) - self.label_width(api, p), self.Z(23), api, p, SMALL, "api")

        self.draw_oled(now, blink)
        self.img.configure(data=self.oled.ppm())
        self.root.after(40, self.tick)

    def draw_oled(self, now, blink):
        o, e = self.oled, self.engine
        o.clear()
        message = self.message[:21] if self.message and now < self.message_until else ""
        if now < self.boot_until:  # At start the name, as the Deluge shows its version
            o.text((o.W - o.width("DELUGE", 2)) // 2, 9, "DELUGE", 2)
            o.text((o.W - o.width("USB REC")) // 2, 30, "USB REC")
            return
        if not e.connected:
            o.text((o.W - o.width("NO DELUGE", 2)) // 2, 3, "NO DELUGE", 2)
            o.text(1, 22, "COMMUNITY FEATURES >")
            o.text(1, 31, "USB AUDIO ON, REBOOT")
            if message:
                o.rect(0, 39, o.W, 9)
                o.text(1, 40, message, invert=True)
            return
        # Top line: the rate and the bit depth that arrives, the state on the right
        o.text(0, 0, "44.1K " + ("--B" if self.bits is None else f"{self.bits}B"))
        state = {"rec": "REC", "armed": "ARM", "idle": "READY"}[e.state]
        if e.state != "armed" or blink:
            x = o.W - o.width(state)
            o.text(x, 0, state)
            if e.state == "rec" and blink:
                o.rect(x - 7, 1, 5, 5)
        o.rect(0, 9, o.W, 1)
        # The big timer: minutes, seconds and tenths of the take
        secs = e.seconds() if e.state == "rec" else (e.last_take[1] if e.last_take else 0.0)
        m, sec = divmod(int(secs * 10), 600)
        o.seven_seg((o.W - 88) // 2, 14, f"{m % 100:02d}:{sec // 10:02d}.{sec % 10}")
        # Bottom line: a message, the file, what ARM waits for
        if message:
            o.rect(0, 39, o.W, 9)
            o.text(1, 40, message, invert=True)
        elif e.state == "rec":
            o.text(0, 40, f"{e.name[:8]} {e.frames * CHANNELS * 3 / 1e6:7.1f}MB")
        elif e.state == "armed":
            o.text(0, 40, f"WAIT FOR > {e.threshold_db} DB")
        elif e.last_take:
            o.text(0, 40, f"{e.last_take[0][:8]} {e.last_take[2] / 1e6:7.1f}MB")
        else:
            o.text(0, 40, "R: REC   A: ARM")


def output_dir(arg):
    if arg:
        return Path(arg).expanduser()
    music = Path.home() / "Music"
    return (music if music.is_dir() else Path.home()) / "Deluge USB"


def main():
    ap = argparse.ArgumentParser(description="Nimmt den USB-Audio-Ausgang des Deluge auf (WAV, 24 Bit, 44,1 kHz).")
    ap.add_argument("--out", help="Ordner für die Aufnahmen (Standard: Musik/Deluge USB)")
    ap.add_argument("--device", help="Eingang, falls er nicht «Deluge» heisst: Nummer oder Teil des Namens (--list)")
    ap.add_argument("--shared", action="store_true", help="Windows: WASAPI nicht exklusiv öffnen")
    ap.add_argument("--threshold", type=int, default=-40, help="Schwelle für ARM in dBFS (Standard -40)")
    ap.add_argument("--list", action="store_true", help="Audio-Eingänge auflisten und beenden")
    ap.add_argument("--demo", action="store_true", help="ohne Deluge ausprobieren: ein Testsignal statt des Eingangs")
    ap.add_argument("--selftest", type=float, metavar="S", help=argparse.SUPPRESS)  # For the build: see selftest()
    args = ap.parse_args()
    if args.list:
        import sounddevice as sd
        apis = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"{i:3d}  {d['name']}  ({apis[d['hostapi']]['name']}, {d['max_input_channels']} Kanäle)")
        return
    if sys.platform.startswith("win"):
        try:  # Sharp pixels on scaled Windows displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    import tkinter as tk
    engine = Engine(output_dir(args.out), args.device, args.shared, args.demo or bool(args.selftest),
                    min(THRESH_MAX, max(THRESH_MIN, args.threshold)))
    root = tk.Tk()
    app = App(root, engine, min(3.0, max(1.0, root.winfo_fpixels("1i") / 96)))
    for sig in (signal.SIGINT, signal.SIGTERM):  # Ctrl+C in the console: the take is saved, the window closes
        signal.signal(sig, lambda *a: root.after(0, app.quit))
    result = [0]
    if args.selftest:
        selftest(root, app, engine, args.selftest, result)
    try:
        root.mainloop()
    finally:
        engine.shutdown()
    sys.exit(result[0])


def selftest(root, app, engine, seconds, result):
    """The build's check of the finished program (also the .exe): the window with the demo signal, a take from 1 s
    to the end, PortAudio loaded. Writes selftest.txt into the output folder; exit status 1 if something failed."""
    def check():
        lines, ok = [], True
        try:
            import sounddevice as sd
            lines.append(f"portaudio: {sd.get_portaudio_version()[1]}, {len(sd.query_devices())} devices")
        except Exception as ex:
            ok = False
            lines.append(f"portaudio: FAILED {ex!r}")
        take = engine.last_take
        try:
            with wave.open(str(engine.out_dir / take[0]), "rb") as w:
                fmt = (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes())
            good = fmt[:3] == (CHANNELS, 3, RATE) and fmt[3] >= (seconds - 2) * RATE
            lines.append(f"take: {take[0]} channels {fmt[0]}, bytes {fmt[1]}, {fmt[2]} Hz, {fmt[3]} frames: "
                         + ("ok" if good else "FAILED"))
            ok &= good
        except Exception as ex:
            ok = False
            lines.append(f"take: FAILED {ex!r}")
        lines.append("selftest: " + ("ok" if ok else "FAILED"))
        (engine.out_dir / "selftest.txt").write_text("\n".join(lines) + "\n")
        result[0] = 0 if ok else 1
        app.quit()
    root.after(1000, lambda: app.press("rec"))
    root.after(int(seconds * 1000) - 600, lambda: app.press("stop"))
    root.after(int(seconds * 1000), check)


if __name__ == "__main__":
    main()
