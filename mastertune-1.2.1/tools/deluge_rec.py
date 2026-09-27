#!/usr/bin/env python3
"""DELUGE USB REC: records the Deluge's USB audio output, in the Deluge's look.

The Deluge (mastertune v8 or later: Settings > Community features > USB audio on, then restart) appears on the
computer as an audio input called "Deluge": stereo, 24 bits, 44.1 kHz. This program records exactly that into WAV
files of 24 bits, without any conversion. It never sends anything to the Deluge.

Install:  pip install sounddevice numpy python-rtmidi   (tkinter comes with Python; rtmidi only for the song name)
Run:      python deluge_rec.py                 (--out FOLDER, --list, --demo without a Deluge, --help)

Controls (mouse or keys):
  REC     R, space       start or end a take
  ARM     A              the take starts by itself once the signal rises over the threshold (0.3 s pre-roll)
  STOP    S, Esc         end the take: the file is complete
  MON     M              monitor on or off: the Deluge's input on the computer's headphones or speakers
  OUT     O              choose the monitor's output: up/down, mouse wheel or the knob, then Enter (or OUT, or a
                         click on the knob); Esc leaves the list as it was
  FOLDER  F              open the recordings folder
  VOL     up/down, mouse wheel or drag on the fader: the level of the take, the pads and the monitor, 0 dB
          (bit-exact) down to -30 dB; a double-click on the fader: back to 0 dB
  THRESH  + -, mouse wheel or drag on the knob: the ARM threshold, -60 to -12 dBFS
The files are called by the song, the date and time and the firmware: "Rescue 3 2026-09-27 21-30-05 v17.WAV". The
Deluge tells its song and firmware over USB MIDI port 3 (a firmware that sends SysEx 0x12, see DelugeInfo; the
program only listens there); without that, the name is the date and time. Each file also carries them inside (RIFF
INFO: title, date, software, and a comment with the full firmware and VOL). Default folder: Music/Deluge USB in the
user's folder. The output, monitor on or off, the threshold and VOL are remembered.

Monitoring plays what arrives, about 50 ms later, on the chosen output (or the system's default output); a device
with "Deluge" in its name is never offered. The recording is not affected by it. The monitor fades in and out
(10 ms) where it starts, runs dry or skips ahead, so it does not click; its output is open while the Deluge's input
is. Its loudness is the computer's volume.

The Deluge's VOLUME knob is analog, after its converter: like resampling, the USB signal does not follow it. To keep
the pads out of the red, turn down the song's volume on the Deluge, or VOL here (below 0 dB the take is no longer
bit-exact). VOL cannot undo what the Deluge itself clipped: the last pad blinks red whenever the Deluge's own output
reaches full scale, whatever VOL is.

Top right shows the audio path: WASAPI EXCL (Windows, exclusive, bit-exact), WASAPI SHARED, CORE AUDIO, ALSA ...
The display shows the bit depth that actually arrives: 24B means bit-exact. 16B or 32B point to a conversion, on
Windows mostly an input level below 100 % or audio enhancements: set the level of the input "Deluge" to 100 % or run
without --shared. The pads show the level of the left and right channel in 3 dB steps from -45 dBFS, the last one
blinks red at full scale.

Versions (the number is in the window's title and on the display at start, --version prints it):
  1  the first build: REC, ARM with pre-roll, the pads, the bit depth that arrives
  2  reviewed (7 fixes): no take lost on STOP, quit, a disk error or at 4 GB; the pre-roll exact to the frame
  3  the monitor with a choice of output, everything in English, the version number, the Deluge's rain as icon
  4  VOL, a fader against the red; a box around each control; the monitor without clicks (fades in and out)
  5  the files named by song, date and time and firmware, which the Deluge tells on MIDI port 3; RIFF INFO inside
"""
import argparse
import collections
import json
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

VERSION = 5                     # One more with every change of the program, and a line under Versions above
RATE = 44100
CHANNELS = 2
FULL_SCALE = 2 ** 31            # The 24-bit samples arrive left-justified in int32
PREROLL = round(0.3 * RATE)     # ARM: the frames kept before the first sample over the threshold
MAX_DATA_BYTES = 4_000_000_000  # WAV sizes are 32-bit: after 4 GB (about 4 h 11 min) a take goes on in the next file
HEADER_EVERY = 2 * RATE         # The WAV header is brought up to date every 2 s: a crash leaves a readable file
THRESH_MIN, THRESH_MAX = -60, -12
VOL_MIN = -30                   # VOL: the fader goes from 0 dB (the take bit-exact) down to this
CLIP = FULL_SCALE * 10 ** (-0.1 / 20)  # From here the last pad blinks: full scale
MONITOR_TARGET = 2048           # Monitor: frames buffered before it plays (46 ms), and again after it ran dry
MONITOR_LIMIT = 6144            # More than this (139 ms, the two clocks drifting apart): back to MONITOR_TARGET
MONITOR_FADE = 441              # Monitor: 10 ms fades where it starts, runs dry or skips ahead, so it does not click
NOT_OUTPUTS = ("microsoft sound mapper", "primary sound driver")  # Windows' aliases of the default output
SONG_INFO = bytes([0xF0, 0x00, 0x21, 0x7B, 0x01, 0x12])  # The Deluge's SysEx with its song and firmware (port 3)

# --- the look: panel, OLED, pads (the Deluge's colours), lettering

PANEL, PLATE, EDGE, BEZEL, LABEL, SMALL = "#0e0e10", "#18181b", "#26262b", "#050506", "#d8d8de", "#8c8c96"
BOX, BOX_EDGE = "#1d1d21", "#35353d"  # The box around each control
OLED_ON, OLED_OFF = (226, 238, 255), (7, 9, 13)
PAD_COLOURS = ["#2fdc6e"] * 9 + ["#b8e636", "#f2d22e", "#f2d22e", "#ff9f1c", "#ff7a1c", "#ff5a1f", "#ff2d2d"]
# 64 x 64: the Deluge's rain of squares in the meter's colours and the REC dot (made by deluge_rec_icon.py)
ICON_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAADC0lEQVR42u2bTYvTUBSG8xMSS5m2aWdqGzr9mg9wJ3WjLkRdqogLra7F"
            "lQj+AWcnggizdOM/EFyKjIyIOxfWhQt3s5KK4MYRjufEXLmNLblJk8y9mVN4oUzTcN4n9749ydxrWQqvfn97hNpB7aEOUKCpDoIaqdaR"
            "tewLTzJGTTQ2HCWqfZzEuId6J5+s19sCz+vC2lobGo2TWopqoxqp1hAI8uJZMYb7VHy50xmC666C45TAtk8YIaqVaqbaJQjTyGkRmD8U"
            "XyKqJhmfB4I8SBAOF0IIhv1UDPdKxTXWeFjkRZoW07nTQZ7zRTIvQ5AzYV7a/xv2RTMvFJoOYxnARASeyXNeJROkYJzIwef/kZJTZwNl"
            "1KbtwDnUBdv2NcL3Hkr1HORRGgUjK+ia/JDQ+eoP0OR11NNaDfbbbfjU6fh62WzCw3IZLiKMquIokAJxxwpaR7+B0NX8Nhq/jya/drsA"
            "Gxtz9QpBXMPj6grnI68BgD1L9PbURcUpKlxA2scLtVB3SyX43u8vNC/0ptWCyzgSnIhzkldx72AlTf+8AFxCQ+9xyEeZF3pUqcAwIhPk"
            "XwOtAdSCq69qnvTB8/xwLASAHl7JJxh6cQD8HAzgah4A8gBCQ3nXdWMBIN0oCoB1BPC4Wo1l/huG5ZWiAFhB3XEc+DUcKgN4i4F5No8Q"
            "zCsTqOujZkfF/G/UA+wX1rMA8OPj5oyiDKZ1PDU2N3EUfMauLwrA83odziu0xUYBEFlwCyHsL+gHKPmf4a8F9QzlmHeFRgAgrQZNEbXE"
            "LxoNeI1dH7W/ZPw2wjmNkEoJbouNASDURJ1Cs2fsv6bp7nBliecCiUNQFyDLPhhhAAyAAaTTCGVZcJYPRxkAA2AA2fxDZOvLvRnF/XzZ"
            "4xkAA2AA2QJIs6C8gTAABsAA0g/BtA1kCYQBMAAGcPSNUNahygAYAAM4WgA6A2EADGAWQKJlciYDCC+T036hZNoKL5Q0Yqlsmgumw0tl"
            "jVksnYb+Wyx97JfL84YJ3jLDm6Z42xxvnOSts7x5+thun/8Dvi/Nm6uBEpQAAAAASUVORK5CYII=")

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


def unpack7(data):
    """SysEx data packed 7 bytes into 8 (the Deluge's pack_8bit_to_7bit): each group's first byte holds the high
    bits of the next seven."""
    out = bytearray()
    for i in range(0, len(data), 8):
        high = data[i]
        out += bytes(b | (0x80 if high >> j & 1 else 0) for j, b in enumerate(data[i + 1:i + 8]))
    return bytes(out)


def clean_name(text, limit=80):
    """A song's name as part of a file name on Windows, macOS and Linux: no <>:"/\\|?* or control characters, no
    dots or spaces at the ends."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)[:limit]
    return text.strip(" .")


def short_firmware(firmware):
    """The firmware in a file name: "1.2.1-mastertune-v17-4e3d2075" becomes "v17", "...-v17-l2d-b3385d83" "v17-l2d"."""
    if not firmware:
        return ""
    m = re.search(r"-(v\d+(?:-[a-z0-9]+)*?)(?:-[0-9a-f]{8})?$", firmware)
    return clean_name(m.group(1) if m else firmware, 24)


def info_chunk(tags):
    """RIFF LIST/INFO with (id, text) pairs, UTF-8, each ended by a zero byte and padded to an even length."""
    body = b"INFO"
    for key, text in tags:
        value = text.encode("utf-8") + b"\0"
        body += key + struct.pack("<I", len(value)) + value + (b"\0" if len(value) % 2 else b"")
    return b"LIST" + struct.pack("<I", len(body)) + body


class WavFile:
    """A WAV file, stereo, 24 bits, 44.1 kHz, with a RIFF INFO list (tags). Never overwrites a file. The header gets
    the current size every 2 s, so after a crash or a closed console the file holds everything up to shortly
    before."""

    def __init__(self, path, tags=()):
        self.f = open(path, "xb")
        self.info = info_chunk(tags) if tags else b""  # Between fmt and data: the header keeps its length
        self.frames, self.next_header = 0, HEADER_EVERY
        self.f.write(self.header())

    def header(self):
        data = self.frames * CHANNELS * 3
        fmt = struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE, RATE * CHANNELS * 3, CHANNELS * 3, 24)
        return (b"RIFF" + struct.pack("<I", 36 + len(self.info) + data) + b"WAVEfmt " + fmt + self.info + b"data"
                + struct.pack("<I", data))

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


class MonitorBuffer:
    """The frames on their way from the Deluge's input to the monitor's output: a ring between two clocks. It plays
    once MONITOR_TARGET frames are there, fading in; over MONITOR_LIMIT it skips ahead to the newest MONITOR_TARGET
    frames, crossfaded; run dry, the last frame played fades to silence, which lasts until MONITOR_TARGET frames are
    there again. Each fade is MONITOR_FADE frames long, however small the output's blocks: no clicks. Between the
    fades, 24-bit samples stay exact in float32."""

    def __init__(self, size=RATE):
        self.buf = np.zeros((size, CHANNELS), np.float32)
        self.size = size
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.written = self.read = 0  # Frames so far, both ends
            self.priming, self.faded_in = True, 0
            self.skip_from = self.skip_to = None  # A skip ahead: from where to where, crossfaded
            self.skipped = 0                      # ... and how much of the crossfade is done
            self.last = np.zeros(CHANNELS, np.float32)  # The last frame the output got
            self.decay_from, self.decay_pos = self.last, MONITOR_FADE  # Run dry: the last frame fading out
            self.underruns = self.drops = 0

    def push(self, block):
        x = block[-self.size:].astype(np.float32) / FULL_SCALE
        with self.lock:
            i = self.written % self.size
            first = min(len(x), self.size - i)
            self.buf[i:i + first] = x[:first]
            self.buf[:len(x) - first] = x[first:]
            self.written += len(x)
            behind = self.written - self.read
            oldest = self.read if self.skip_from is None else min(self.read, self.skip_from + self.skipped)
            if self.written - oldest > self.size - MONITOR_FADE:  # The output stalled: what it would play is gone
                self.read, self.priming = self.written - MONITOR_TARGET, True
                self.skip_from = self.skip_to = None
                self.drops += 1
            elif behind > MONITOR_LIMIT and self.skip_to is None:
                if self.priming:
                    self.read = self.written - MONITOR_TARGET
                else:
                    self.skip_to = self.written - MONITOR_TARGET  # pull() crossfades there
                self.drops += 1

    def frames(self, pos, n):
        i = pos % self.size
        return self.buf[i:i + n] if i + n <= self.size else np.concatenate((self.buf[i:], self.buf[:i + n - self.size]))

    def decay(self, out, start):
        """Adds the rest of the fade-out of the last frame (after running dry) to out, from start on."""
        m = min(len(out) - start, MONITOR_FADE - self.decay_pos)
        if m > 0:
            g = 1 - (self.decay_pos + np.arange(1, m + 1, dtype=np.float32)) / MONITOR_FADE
            out[start:start + m] += self.decay_from * g[:, None]
            self.decay_pos += m

    def pull(self, out):
        n = len(out)
        with self.lock:
            if self.priming and self.written - self.read < MONITOR_TARGET:
                out.fill(0)
                self.decay(out, 0)
                self.last = out[-1].copy()
                return
            if self.priming:
                self.priming, self.faded_in = False, 0
            done = 0
            if self.skip_to is not None:  # Too far behind: on to the newest frames, crossfaded over MONITOR_FADE
                if self.skip_from is None:
                    self.skip_from, self.skipped = self.read, 0
                done = min(MONITOR_FADE - self.skipped, n)
                r = ((self.skipped + np.arange(done, dtype=np.float32) + 0.5) / MONITOR_FADE)[:, None]
                out[:done] = (self.frames(self.skip_from + self.skipped, done) * (1 - r)
                              + self.frames(self.skip_to + self.skipped, done) * r)
                self.skipped += done
                self.read = self.skip_to + self.skipped
                if self.skipped >= MONITOR_FADE:
                    self.skip_from = self.skip_to = None
            k = min(n - done, self.written - self.read)
            out[done:done + k] = self.frames(self.read, k)
            self.read += k
            played = done + k
            if self.faded_in < MONITOR_FADE:  # Fading in after priming
                m = min(played, MONITOR_FADE - self.faded_in)
                gain = np.arange(self.faded_in + 1, self.faded_in + m + 1, dtype=np.float32) / MONITOR_FADE
                out[:m] *= gain[:, None]
                self.faded_in += m
            self.decay(out[:played], 0)  # What is left of an earlier fade-out, under the fade-in
            if played < n:  # Run dry: the last frame fades out, then silence until MONITOR_TARGET frames are there
                out[played:] = 0
                self.decay_from = (out[played - 1] if played else self.last).copy()
                self.decay_pos = 0
                self.decay(out, played)
                self.underruns += 1
                self.priming = True
                self.skip_from = self.skip_to = None
            self.last = out[-1].copy()


class Monitor:
    """Plays the Deluge's input on one of the computer's outputs (headphones, speakers). Never on the Deluge: a
    device with "Deluge" in its name is not an output here."""

    RANK = {"Windows WASAPI": 0, "Core Audio": 0, "ALSA": 0, "JACK Audio Connection Kit": 1,
            "Windows DirectSound": 2, "MME": 3, "Windows WDM-KS": 4}

    def __init__(self, output=None):
        self.output = output  # The chosen output's name; None: the system's default output
        self.on, self.stream, self.name, self.api, self.error = False, None, "", "", ""
        self.buffer = MonitorBuffer()

    @staticmethod
    def usable(d):
        return d["max_output_channels"] >= 1 and "deluge" not in d["name"].lower()

    @staticmethod
    def alias(d):
        return d["name"].lower().startswith(NOT_OUTPUTS)

    @staticmethod
    def same(a, b):
        """The same device under two host APIs: MME cuts names after 31 characters."""
        short, long = sorted((a.rstrip(), b.rstrip()), key=len)
        return short == long or (len(short) >= 30 and long.startswith(short))

    def outputs(self):
        """The outputs to choose from, the system's default first (None), then each device once (Windows lists one
        device per host API) under its longest name."""
        import sounddevice as sd
        names = []
        for d in sd.query_devices():
            if self.usable(d) and not self.alias(d):
                same = [k for k, n in enumerate(names) if self.same(n, d["name"])]
                if not same:
                    names.append(d["name"])
                elif len(d["name"]) > len(names[same[0]]):
                    names[same[0]] = d["name"]
        return [None] + names

    def candidates(self):
        """(rank, device index, host API) for the chosen output, the best way first. The system's default: each host
        API's default output, Windows' aliases of it too, but none at all if a host API names the Deluge as the
        default (an alias would lead there)."""
        import sounddevice as sd
        devices, apis = sd.query_devices(), sd.query_hostapis()
        found = []
        for i, d in enumerate(devices):
            api = apis[d["hostapi"]]
            if self.output is None:
                if i != api.get("default_output_device", -1):
                    continue
                if d["max_output_channels"] >= 1 and not self.alias(d) and "deluge" in d["name"].lower():
                    return []
            elif self.alias(d) or not self.same(d["name"], self.output):
                continue
            if self.usable(d):
                found.append((self.RANK.get(api["name"], 5), i, api["name"]))
        return sorted(found)

    def start(self):
        """Opens the chosen output; True once it plays."""
        import sounddevice as sd
        self.stop()
        self.buffer.reset()
        devices = sd.query_devices()
        for _, index, api in self.candidates():
            stream = None
            try:
                extra = sd.WasapiSettings(auto_convert=True) if api == "Windows WASAPI" else None
                stream = sd.OutputStream(device=index, channels=min(CHANNELS, devices[index]["max_output_channels"]),
                                         samplerate=RATE, dtype="float32", latency="low", callback=self.play,
                                         extra_settings=extra)
                stream.start()
            except Exception:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                continue
            self.stream, self.on, self.error = stream, True, ""
            self.name = devices[index]["name"]
            self.api = {"Windows WASAPI": "WASAPI", "Windows DirectSound": "DSOUND",
                        "Windows WDM-KS": "WDM-KS"}.get(api, api.upper())
            return True
        self.error = "NO OUTPUT DEVICE"
        return False

    def close(self):
        """Closes the output but leaves MON on: it opens again with the Deluge's input (Engine.open_monitor)."""
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def stop(self):
        self.close()
        self.on = False

    def feed(self, block):
        """From the input's callback."""
        if self.stream is not None:
            self.buffer.push(block)

    def play(self, outdata, frames, time_info, status):
        """The output's callback."""
        if outdata.shape[1] == CHANNELS:
            self.buffer.pull(outdata)
        else:  # A mono output: both channels
            both = np.empty((frames, CHANNELS), np.float32)
            self.buffer.pull(both)
            outdata[:, 0] = both.mean(axis=1)

    def lost(self):
        """True if the output stopped by itself (headphones unplugged): then it is off."""
        if self.on and self.stream is not None and not getattr(self.stream, "active", True):
            self.stop()
            self.error = "OUTPUT LOST"
            return True
        return False


class DelugeInfo:
    """What the Deluge tells about itself: its song's name and its firmware, in SysEx F0 00 21 7B 01 12 <JSON
    {"song": ..., "fw": ...}, UTF-8, packed 7 into 8> F7 on its USB MIDI port 3, while USB audio streams (a firmware
    that sends it). Only listens: it opens port 3 as an input and never sends anything. Port 1, the one a DAW uses,
    stays free."""

    def __init__(self):
        self.song = self.firmware = None  # None: not told (yet); "" for the song: a new song, not saved yet
        self.midi, self.port = None, ""

    @staticmethod
    def pick(names):
        """The Deluge's port 3 among the MIDI inputs, or None: never another of its ports."""
        deluge = [(i, n) for i, n in enumerate(names) if "deluge" in n.lower()]
        for i, n in deluge:
            if re.search(r"(midiin|port|midi) ?3\b", n.lower()):
                return i
        return deluge[2][0] if len(deluge) == 3 else None

    @staticmethod
    def parse(message):
        """(song, firmware) from one SysEx, or None if it is not the Deluge's song info."""
        message = bytes(message)
        if not message.startswith(SONG_INFO) or message[-1:] != b"\xf7":
            return None
        try:
            info = json.loads(unpack7(message[len(SONG_INFO):-1]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(info, dict):
            return None
        song, firmware = info.get("song"), info.get("fw")
        return (song, firmware) if isinstance(song, str) and isinstance(firmware, str) else None

    def received(self, event, data=None):
        """python-rtmidi's callback: (message, delta time)."""
        info = self.parse(event[0])
        if info:
            self.song, self.firmware = info

    def open(self):
        """Listens on the Deluge's port 3 if python-rtmidi is there and the port is free; True if it does."""
        if self.midi is not None:
            return True
        try:
            import rtmidi
            midi = rtmidi.MidiIn()
        except Exception:
            return False
        try:
            names = midi.get_ports()
            i = self.pick(names)
            if i is None:
                raise LookupError("no port 3 of a Deluge")
            midi.ignore_types(sysex=False)
            midi.set_callback(self.received)
            midi.open_port(i)
        except Exception:
            self.close_midi(midi)
            return False
        self.midi, self.port = midi, names[i]
        return True

    def close(self):
        midi, self.midi, self.song, self.firmware = self.midi, None, None, None
        if midi is not None:
            self.close_midi(midi)

    @staticmethod
    def close_midi(midi):
        try:
            midi.close_port()
            midi.delete()
        except Exception:
            pass


class Engine:
    """Opens the Deluge's input and records it. The audio callback only measures and queues the blocks; a writer
    thread keeps the pre-roll and writes the file."""

    def __init__(self, out_dir, device=None, shared=False, demo=False, threshold=-40):
        self.out_dir, self.device, self.shared, self.demo = Path(out_dir), device, shared, demo
        self.threshold_db = threshold
        self.volume_db = 0             # VOL; below 0 dB the take, the pads, ARM and the monitor get the lower level
        self.gain = 1.0                # The gain the last block ended with: a change ramps over the next block
        self.clipped = False           # The Deluge's own output reached full scale (VOL cannot undo that)
        self.stream, self.api, self.connected, self.last_cb = None, "", False, 0.0
        self.state = "idle"            # idle, armed, rec
        self.q, self.cmd = queue.SimpleQueue(), queue.SimpleQueue()
        self.peak = np.zeros(CHANNELS, np.int64)
        self.or_bits = 0
        self.overflows, self.gaps = 0, 0  # Overflows: for the display; gaps: all, for the take's line in the console
        self.take_gaps = 0
        self.wav, self.name, self.frames = None, "", 0
        self.base, self.part = "", 1   # The take's name without .WAV, and which of its files (a new one every 4 GB)
        self.label = ""                # The take on the display: the song, or the time it began
        self.last_take = None          # (name, seconds, bytes, label)
        self.info = DelugeInfo()       # The Deluge's song and firmware, for the file's name and its INFO list
        self.now = time.time
        self.events = collections.deque(maxlen=8)
        self.monitor = Monitor()
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
        """Tries to open the Deluge's input; True once it is open. The monitor's output opens with it (if MON is on)
        and closes with it: PortAudio restarts here, and no stream may be open then."""
        if self.connected:
            return True
        if self.demo:
            self.stream, self.api = DemoInput(self.callback), "DEMO"
            self.stream.start()
            self.connected, self.last_cb = True, time.monotonic()
            self.open_monitor()
            return True
        import sounddevice as sd
        self.monitor.close()
        self.info.open()  # Port 3 first: the Deluge tells its song and firmware once its audio streams
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
                self.open_monitor()
                return True
        return False

    def open_monitor(self):
        """MON on: its output opens once the Deluge's input is there."""
        m = self.monitor
        if not (m.on and self.connected) or m.stream is not None:
            return
        try:
            ok = m.start()
        except Exception:
            ok, m.on, m.error = False, False, "OUTPUT FAILED"
        self.events.append("MON " + m.name.upper() if ok else m.error or "OUTPUT FAILED")
        if ok:
            print(f"monitor: {m.name} ({m.api})", flush=True)

    def disconnect(self):
        """The Deluge went away, or the program ends: a take in progress is finished and saved. The monitor's output
        closes too (MON stays on)."""
        if self.state != "idle":
            self.command("stop")
        self.monitor.close()
        self.info.close()
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
        raw = np.array(indata, dtype=np.int32, copy=True)
        self.or_bits |= int(np.bitwise_or.reduce(raw, axis=None)) & 0xFFFFFFFF  # What arrives, before VOL
        peak = np.abs(raw.astype(np.int64)).max(axis=0)
        if peak.max() >= CLIP:
            self.clipped = True
        block = self.apply_volume(raw)
        if block is not raw:
            peak = np.abs(block.astype(np.int64)).max(axis=0)
        self.peak = np.maximum(self.peak, peak)
        self.last_cb = time.monotonic()
        self.monitor.feed(block)
        self.q.put(block)

    def apply_volume(self, raw):
        """VOL on a block: at 0 dB the block itself (bit-exact); below, the 24-bit samples scaled and rounded, a change
        ramped over the block so it does not click."""
        g = 10 ** (self.volume_db / 20) if self.volume_db < 0 else 1.0
        g0, self.gain = self.gain, g
        if g == 1.0 and g0 == 1.0:
            return raw
        n = len(raw)
        gains = np.full(n, g) if g == g0 else g0 + (g - g0) * np.arange(1, n + 1) / n
        y = np.rint((raw.astype(np.int64) >> 8) * gains[:, None])
        return (np.clip(y, -2 ** 23, 2 ** 23 - 1).astype(np.int64) << 8).astype(np.int32)

    # --- for the display

    def take_clipped(self):
        """Whether the Deluge's own output reached full scale since the last call."""
        clipped, self.clipped = self.clipped, False
        return clipped

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

    def take_name(self, when):
        """The song (if the Deluge told it), the date and time, the firmware in short: "Rescue 3 2026-09-27 21-30-05
        v17"."""
        stamp = time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(when))
        return " ".join(p for p in (clean_name(self.info.song or ""), stamp, short_firmware(self.info.firmware)) if p)

    def tags(self, when):
        song, firmware = self.info.song, self.info.firmware
        level = "0 dB (bit-exact)" if self.volume_db == 0 else f"{self.volume_db} dB"
        return [(b"INAM", song or "Deluge USB audio"), (b"ICRD", time.strftime("%Y-%m-%d", time.localtime(when))),
                (b"ISFT", f"DelugeRec v{VERSION}"),
                (b"ICMT", f"Recorded {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(when))} from the Deluge's USB "
                          f"audio. Song: {song if song else 'not told'}. Deluge firmware: {firmware or 'not told'}. "
                          f"VOL {level}.")]

    def open_file(self, part=1):
        """A new take (part 1), or its next file after 4 GB. A file of the same name is never overwritten: then
        " (2)", " (3)" ..."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        when = self.now()
        if part == 1:
            self.base = self.take_name(when)
            self.label = clean_name(self.info.song or "") or time.strftime("%H:%M:%S", time.localtime(when))
        stem, n = self.base + (f" part {part}" if part > 1 else ""), 1
        while True:
            name = stem + (f" ({n})" if n > 1 else "") + ".WAV"
            try:
                self.wav = WavFile(self.out_dir / name, self.tags(when))
                break
            except FileExistsError:
                n += 1
        self.name, self.part, self.frames, self.take_gaps, self.state = name, part, 0, self.gaps, "rec"

    def write(self, block):
        if self.wav is None or not len(block):
            return
        self.wav.write(block)
        self.frames += len(block)
        if self.frames * CHANNELS * 3 >= MAX_DATA_BYTES:  # The take goes on without a gap in the next file
            self.close_file()
            self.open_file(self.part + 1)
            self.events.append("4GB: NEXT FILE")

    def close_file(self):
        wav, self.wav = self.wav, None
        if wav is not None:
            wav.close()
            self.last_take = (self.name, self.seconds(), self.frames * CHANNELS * 3, self.label)
            self.events.append(("SAVED " + self.label)[:21])
            gaps = self.gaps - self.take_gaps
            print(f"saved: {self.out_dir / self.name} ({self.seconds():.1f} s"
                  + (f", {gaps} gaps: the computer was too slow)" if gaps else ")"), flush=True)
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
                print(f"error writing: {e}", flush=True)
        self.close_file()

    def shutdown(self):
        self.monitor.stop()
        self.disconnect()
        self.command("stop")
        deadline = time.monotonic() + 3
        while self.state != "idle" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.stop_writer = True
        self.writer.join(timeout=3)


# --- the window

class App:
    def __init__(self, root, engine, z, settings=None):
        import tkinter as tk
        self.root, self.engine, self.z, self.settings = root, engine, z, settings
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
        self.song_shown = None
        self.warned_import = False
        self.menu = None  # The output list on the OLED: {"items": [...], "sel": i}

        self.W, self.H = Z(572), Z(390)  # The Deluge's own proportions: 305 x 208 mm
        root.title(f"DELUGE USB REC v{VERSION}")
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
        ox, oy = Z(68), Z(52)
        c.create_rectangle(ox - Z(6), oy - Z(6), ox + Z(384) + Z(6), oy + Z(144) + Z(6), fill=BEZEL, outline=EDGE)
        self.img = tk.PhotoImage(width=self.oled.W * self.oled.scale, height=self.oled.H * self.oled.scale)
        c.create_image(ox, oy, image=self.img, anchor="nw")
        # Pads: the level of the left and right channel
        self.pads = []
        py = Z(222)
        for ch, name in enumerate("LR"):
            self.label(Z(50), py + ch * Z(26) + Z(3), name, Z(2))
            row = []
            for i in range(16):
                x, y = Z(70) + i * Z(24), py + ch * Z(26)
                row.append(c.create_rectangle(x, y, x + Z(20), y + Z(20), fill=self.dim(PAD_COLOURS[i]),
                                              outline="#0a0a0c", width=Z(1)))
            self.pads.append(row)
        # Round buttons with their LEDs, each in its box
        top, bottom = Z(284), Z(368)
        by = top + Z(28)
        self.buttons = {}
        x = Z(26)
        for key, colour, text, letter in (("rec", "#ff2d2d", "REC", "R"), ("arm", "#ffae1c", "ARM", "A"),
                                          ("stop", "#e8e8f0", "STOP", "S"), ("mon", "#2fdc6e", "MON", "M"),
                                          ("out", "#35d4e8", "OUT", "O"), ("folder", "#3d8bff", "FOLDER", "F")):
            w = max(self.label_width(text, Z(2)), Z(44)) + Z(14)
            self.box(x, top, x + w, bottom)
            cx, x = x + w // 2, x + w + Z(8)
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
        # On the right, one above the other: the VOL fader and the gold THRESH knob, each in its box
        cx = self.W - Z(58)
        self.vol_box = (cx - Z(41), Z(46), cx + Z(41), top - Z(8))
        self.box(*self.vol_box)
        self.box(cx - Z(41), top, cx + Z(41), bottom)
        self.label(cx - self.label_width("VOL", Z(2)) // 2, Z(56), "VOL", Z(2))
        self.fader_x, self.fader_top, self.fader_bottom = cx, Z(86), top - Z(46)
        fader_items = [c.create_rectangle(cx - Z(3), self.fader_top - Z(6), cx + Z(3), self.fader_bottom + Z(6),
                                          fill=BEZEL, outline=EDGE, width=Z(1))]
        for db in range(0, VOL_MIN - 1, -6):  # A tick every 6 dB
            y = self.fader_y(db)
            fader_items += [c.create_line(cx + d * Z(9), y, cx + d * Z(14), y, fill=SMALL, width=Z(1)) for d in (-1, 1)]
        self.fader_cap = c.create_rectangle(0, 0, 0, 0, fill="#c8c8d0", outline="#f0f0f4", width=Z(1))
        self.fader_mark = c.create_line(0, 0, 0, 0, fill="#18181b", width=Z(2))
        fader_items += [self.fader_cap, self.fader_mark]
        for item in fader_items:
            c.tag_bind(item, "<Button-1>", self.grab_fader)
            c.tag_bind(item, "<B1-Motion>", self.grab_fader)
            c.tag_bind(item, "<Double-Button-1>", lambda e: self.set_volume(0))
            c.tag_bind(item, "<Enter>", lambda e: c.configure(cursor="sb_v_double_arrow"))
            c.tag_bind(item, "<Leave>", lambda e: c.configure(cursor=""))
        self.update_fader()
        kx = cx
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

        for keys, action in ((("r", "R", "<space>"), "rec"), (("a", "A"), "arm"), (("s", "S"), "stop"),
                             (("m", "M"), "mon"), (("o", "O"), "out"), (("f", "F"), "folder")):
            for k in keys:
                root.bind(k, lambda e, a=action: self.press(a))
        root.bind("<Escape>", lambda e: self.escape())
        for k in ("<Return>", "<KP_Enter>"):
            root.bind(k, lambda e: self.choose() if self.menu else None)
        for k in ("<plus>", "<KP_Add>"):
            root.bind(k, lambda e: self.turn(1))
        for k in ("<minus>", "<KP_Subtract>"):
            root.bind(k, lambda e: self.turn(-1))
        for k, step in (("<Up>", 1), ("<Down>", -1)):  # VOL, or through the output list while it is open
            root.bind(k, lambda e, s=step: self.turn(s) if self.menu else self.set_volume(self.engine.volume_db + s))
        root.bind("<MouseWheel>", lambda e: self.wheel(e, 1 if e.delta > 0 else -1))
        root.bind("<Button-4>", lambda e: self.wheel(e, 1))
        root.bind("<Button-5>", lambda e: self.wheel(e, -1))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        print(f"recordings: {engine.out_dir}", flush=True)
        engine.open_monitor()  # MON on from the settings or --monitor: its output opens with the Deluge's input
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

    def box(self, x0, y0, x1, y1):
        """The box around one control on the panel."""
        self.c.create_rectangle(x0, y0, x1, y1, fill=BOX, outline=BOX_EDGE, width=self.Z(1))

    def fader_y(self, db):
        return self.fader_top + (self.fader_bottom - self.fader_top) * db / VOL_MIN

    def update_fader(self):
        x, y, Z = self.fader_x, self.fader_y(self.engine.volume_db), self.Z
        self.c.coords(self.fader_cap, x - Z(15), y - Z(6), x + Z(15), y + Z(6))
        self.c.coords(self.fader_mark, x - Z(11), y, x + Z(11), y)
        self.c.delete("vol")
        text = f"{self.engine.volume_db} DB"
        self.label(x - self.label_width(text, Z(1)) // 2, self.vol_box[3] - Z(18), text, Z(1), SMALL, "vol")

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
        if key == "mon":
            if e.monitor.on:
                e.monitor.stop()
                self.say("MONITOR OFF")
                self.save()
            else:
                self.start_monitor()
            return
        if key == "out":
            self.choose() if self.menu else self.open_menu()
            return
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
        if self.menu:  # The knob, the wheel and up/down move through the output list
            self.menu["sel"] = min(len(self.menu["items"]) - 1, max(0, self.menu["sel"] - step))
            return
        e.threshold_db = int(min(THRESH_MAX, max(THRESH_MIN, e.threshold_db + 2 * step)))
        self.update_knob()
        self.say(f"ARM LEVEL {e.threshold_db} DB")
        self.save()

    def set_volume(self, db):
        """VOL, 0 dB (bit-exact) down to VOL_MIN."""
        e = self.engine
        db = int(min(0, max(VOL_MIN, db)))
        if db == e.volume_db:
            return
        e.volume_db = db
        self.update_fader()
        self.say("VOL 0 DB: BIT-EXACT" if db == 0 else f"VOL {db} DB")
        self.save()

    def grab_fader(self, event):
        self.set_volume(round(VOL_MIN * (event.y - self.fader_top) / (self.fader_bottom - self.fader_top)))

    def escape(self):
        if self.menu:
            self.menu = None
        else:
            self.press("stop")

    def open_menu(self):
        m = self.engine.monitor
        try:
            items = m.outputs()
        except ImportError:
            self.say("PIP INSTALL SOUNDDEVICE", 2.2)
            return
        except Exception:
            self.say("AUDIO ERROR", 2.2)
            return
        sel = items.index(m.output) if m.output in items else 0
        self.menu = {"items": items, "sel": sel}

    def choose(self):
        """The output under the bar becomes the monitor's output, and the monitor plays on it."""
        item = self.menu["items"][self.menu["sel"]]
        self.menu = None
        self.engine.monitor.output = item
        self.start_monitor()

    def start_monitor(self):
        m = self.engine.monitor
        if not self.engine.connected:  # Its output opens with the Deluge's input
            m.on = True
            self.say("MONITOR ON", 2.2)
            self.save()
            return
        try:
            ok = m.start()
        except ImportError:
            self.say("PIP INSTALL SOUNDDEVICE", 2.2)
            return
        except Exception:
            ok, m.error = False, "OUTPUT FAILED"
        if ok:
            self.say("MON " + m.name.upper(), 2.2)
            print(f"monitor: {m.name} ({m.api})", flush=True)
        else:
            self.say(m.error or "OUTPUT FAILED", 2.2)
        self.save()

    def save(self):
        """The output, monitor on or off, the threshold and VOL, for the next start."""
        if self.settings is None:
            return
        m = self.engine.monitor
        try:
            self.settings.parent.mkdir(parents=True, exist_ok=True)
            self.settings.write_text(json.dumps({"output": m.output, "monitor": m.on,
                                                 "threshold": self.engine.threshold_db,
                                                 "volume": self.engine.volume_db}))
        except Exception:
            pass

    def wheel(self, event, step):
        kx, ky = self.knob
        if (event.x - kx) ** 2 + (event.y - ky) ** 2 <= self.Z(30) ** 2:
            self.turn(step)
        elif self.vol_box[0] <= event.x <= self.vol_box[2] and self.vol_box[1] <= event.y <= self.vol_box[3]:
            self.set_volume(self.engine.volume_db + step)

    def grab_knob(self, event):
        if self.menu:  # A click on the knob chooses, as its press does on the Deluge
            self.choose()
            self.drag_y = None
            return
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
        if e.info.song != self.song_shown:  # The Deluge told another song
            self.song_shown = e.info.song
            if self.song_shown:
                self.say(("SONG " + self.song_shown)[:21], 2.5)
        if e.overflows:
            e.overflows = 0
            self.say("OVERFLOW: PC TOO SLOW", 2)
        if e.monitor.lost():
            self.say("MONITOR OUTPUT LOST", 2.5)

        # Levels: fast attack, 24 dB/s release, the peak held for 1.5 s
        new = e.take_levels() if e.connected else [-math.inf] * CHANNELS
        if e.take_clipped():  # The Deluge's own output at full scale, whatever VOL is
            self.clip_until = now + 1.0
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
                "stop": e.connected and e.state == "idle", "mon": e.monitor.on, "out": self.menu is not None,
                "folder": False}
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
        if now < self.boot_until:  # At start the name and the version, as the Deluge shows its own
            o.text((o.W - o.width("DELUGE", 2)) // 2, 9, "DELUGE", 2)
            o.text((o.W - o.width(f"USB REC V{VERSION}")) // 2, 30, f"USB REC V{VERSION}")
            return
        if self.menu:
            self.draw_menu(o)
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
        if e.monitor.on:
            o.text(63, 0, "MON")
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
            o.text(0, 40, f"{e.label[:11]:11} {e.frames * CHANNELS * 3 / 1e6:6.1f}MB")
        elif e.state == "armed":
            o.text(0, 40, f"WAIT FOR > {e.threshold_db} DB")
        elif e.last_take:
            o.text(0, 40, f"{e.last_take[3][:11]:11} {e.last_take[2] / 1e6:6.1f}MB")
        elif e.info.song:
            o.text(0, 40, e.info.song[:21])
        else:
            o.text(0, 40, "R: REC   A: ARM")


    def draw_menu(self, o):
        """The output list as a Deluge menu: its title, four lines, the chosen one inverted, * the one in use."""
        o.text(0, 0, "MONITOR OUTPUT")
        o.rect(0, 9, o.W, 1)
        items, sel = self.menu["items"], self.menu["sel"]
        top = min(max(0, sel - 1), max(0, len(items) - 4))
        m = self.engine.monitor
        for row, item in enumerate(items[top:top + 4]):
            y = 12 + row * 9
            name = "SYSTEM DEFAULT" if item is None else item
            text = ("*" if item == m.output and m.on else " ") + name.upper()[:20]
            if top + row == sel:
                o.rect(0, y - 1, o.W, 9)
                o.text(0, y, text, invert=True)
            else:
                o.text(0, y, text)


def settings_path():
    base = os.environ.get("APPDATA") if sys.platform.startswith("win") else None
    return (Path(base) / "DelugeRec" if base else Path.home() / ".config" / "deluge_rec") / "settings.json"


def load_settings(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def output_dir(arg):
    if arg:
        return Path(arg).expanduser()
    music = Path.home() / "Music"
    return (music if music.is_dir() else Path.home()) / "Deluge USB"


def main():
    ap = argparse.ArgumentParser(description="Records the Deluge's USB audio output (WAV, 24 bits, 44.1 kHz).")
    ap.add_argument("--out", help="folder for the recordings (default: Music/Deluge USB)")
    ap.add_argument("--device", help="the input, if it is not called Deluge: its number or part of its name (--list)")
    ap.add_argument("--shared", action="store_true", help="Windows: do not open WASAPI exclusively")
    ap.add_argument("--threshold", type=int, help="ARM threshold in dBFS (default -40)")
    ap.add_argument("--monitor", action="store_true", help="start with the monitor on")
    ap.add_argument("--output", help="the monitor's output: part of its name (--list); default: the system's output")
    ap.add_argument("--list", action="store_true", help="list the audio inputs and outputs, then quit")
    ap.add_argument("--demo", action="store_true", help="try it without a Deluge: a test signal instead of the input")
    ap.add_argument("--version", action="version", version=f"DelugeRec v{VERSION}")
    ap.add_argument("--selftest", type=float, metavar="S", help=argparse.SUPPRESS)  # For the build: see selftest()
    args = ap.parse_args()
    if args.list:
        import sounddevice as sd
        apis = sd.query_hostapis()
        for title, key in (("inputs", "max_input_channels"), ("outputs", "max_output_channels")):
            print(title + ":")
            for i, d in enumerate(sd.query_devices()):
                if d[key] > 0:
                    print(f"{i:4d}  {d['name']}  ({apis[d['hostapi']]['name']}, {d[key]} channels)")
        try:
            import rtmidi
            names = rtmidi.MidiIn().get_ports()
            port3 = DelugeInfo.pick(names)
            print("midi inputs (only listens, on the Deluge's port 3: the song and the firmware):")
            for i, name in enumerate(names):
                print(f"{i:4d}  {name}" + ("  <- port 3 of the Deluge" if i == port3 else ""))
        except ImportError:
            print("midi: pip install python-rtmidi, for the song and the firmware in the file names")
        return
    if sys.platform.startswith("win"):
        try:  # Sharp pixels on scaled Windows displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    import tkinter as tk
    settings = None if args.selftest else settings_path()
    saved = load_settings(settings) if settings else {}
    threshold = args.threshold if args.threshold is not None else saved.get("threshold", -40)
    try:
        volume = int(min(0, max(VOL_MIN, saved.get("volume", 0))))
    except (TypeError, ValueError):
        volume = 0
    engine = Engine(output_dir(args.out), args.device, args.shared, args.demo or bool(args.selftest),
                    min(THRESH_MAX, max(THRESH_MIN, int(threshold))))
    engine.volume_db, engine.gain = volume, 10 ** (volume / 20) if volume < 0 else 1.0  # No ramp from 0 dB at start
    m = engine.monitor
    m.output = saved.get("output")
    if args.output:  # Part of a name: the first output that has it
        try:
            m.output = next((n for n in m.outputs()[1:] if args.output.lower() in n.lower()), None)
        except Exception:
            pass
    m.on = bool(args.monitor or saved.get("monitor"))  # App opens it once the window is there
    root = tk.Tk()
    app = App(root, engine, min(3.0, max(1.0, root.winfo_fpixels("1i") / 96)), settings)
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
    to the end, the monitor switched on (the build machine may have no output: no failure), PortAudio and rtmidi
    loaded. Writes
    selftest.txt into the output folder; exit status 1 if something failed."""
    def check():
        lines, ok = [f"version: v{VERSION}"], True
        try:
            import sounddevice as sd
            lines.append(f"portaudio: {sd.get_portaudio_version()[1]}, {len(sd.query_devices())} devices")
        except Exception as ex:
            ok = False
            lines.append(f"portaudio: FAILED {ex!r}")
        try:  # The build must have rtmidi; a machine without any MIDI system is no failure
            import rtmidi
            try:
                names = rtmidi.MidiIn().get_ports()
                port3 = DelugeInfo.pick(names)
                lines.append(f"midi: rtmidi {rtmidi.get_rtmidi_version()}, {len(names)} inputs, port 3 of a Deluge: "
                             + ("none" if port3 is None else names[port3]))
            except Exception as ex:
                lines.append(f"midi: rtmidi {rtmidi.get_rtmidi_version()}, no MIDI system here ({ex})")
        except Exception as ex:
            ok = False
            lines.append(f"midi: FAILED {ex!r}")
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
        m = engine.monitor
        lines.append(f"monitor: on, {m.name} ({m.api}), {m.buffer.written} frames in" if m.on
                     else f"monitor: off ({m.error or 'not started'})")
        lines.append("selftest: " + ("ok" if ok else "FAILED"))
        (engine.out_dir / "selftest.txt").write_text("\n".join(lines) + "\n")
        result[0] = 0 if ok else 1
        app.quit()
    root.after(1000, lambda: app.press("rec"))
    root.after(1500, lambda: app.press("mon"))
    root.after(int(seconds * 1000) - 600, lambda: app.press("stop"))
    root.after(int(seconds * 1000), check)


if __name__ == "__main__":
    main()
