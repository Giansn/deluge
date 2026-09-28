#!/usr/bin/env python3
"""Draws DelugeRec's icon: the Deluge's rain of squares in the colours of a level meter (green, yellow, red from the
bottom up) and a red recording dot. Writes deluge_rec.ico (16 to 256 pixels) and prints ICON_PNG for deluge_rec.py.
With --baseline DelugeBaseline's: the meter without red, its top two rows grey above a gold line (the baseline), no
dot; deluge_baseline.ico and ICON_PNG for deluge_baseline.py. With --tuner DelugeTuner's: DelugeRec's meter with a
wave in the empty corner at the top right instead of its dot; deluge_tuner.ico and ICON_PNG for deluge_tuner.py.

Usage:  python3 deluge_rec_icon.py [FOLDER] [--baseline | --tuner]   (default: next to this file; --preview writes
        preview.png)
Needs only numpy.
"""
import base64
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

# The Deluge's logo: 25 squares on a grid of 10 rows and 11 columns, seven streaks falling to the right
SQUARES = [(0, 1), (0, 4), (1, 2), (1, 5), (2, 0), (2, 3), (2, 6), (3, 1), (3, 4), (3, 7), (4, 2), (5, 3), (5, 5),
           (5, 8), (6, 1), (6, 6), (6, 9), (7, 2), (7, 5), (7, 7), (7, 10), (8, 3), (8, 6), (9, 4), (9, 7)]
ROWS, COLS = 10, 11
GREEN, YELLOW, RED = (47, 220, 110), (242, 210, 46), (255, 45, 45)  # The pads' colours in deluge_rec.py
GREY, GOLD = (70, 70, 78), (217, 179, 90)  # DelugeBaseline: above the line, and the line (the gold knobs' colour)
WAVE = (53, 212, 232)  # DelugeTuner's wave: the colour of its UMSTIMMEN button (CYAN in deluge_tuner.py)
RING, INSIDE, DOT_RING = (42, 42, 48), (16, 16, 18), (90, 16, 16)
# Per size: the grid's pitch and the side of a square, in pixels (whole pixels keep the small sizes sharp)
LAYOUT = {16: (1, 1), 24: (2, 2), 32: (2, 2), 48: (3, 3), 64: (4, 4), 128: (9, 8), 256: (18, 17)}


def colour(row, baseline=False):
    """A level meter from the bottom up: the lower half green, then yellow, the top two rows red (grey above the
    baseline)."""
    return (GREY if baseline else RED) if row < 2 else YELLOW if row < 5 else GREEN


def draw(size, ss=8, baseline=False, tuner=False):
    """The icon as RGBA, size x size. Shapes are drawn ss times larger and averaged down (smooth edges)."""
    n = size * ss
    y, x = (np.mgrid[0:n, 0:n] + 0.5) / ss  # Pixel centres, in pixels of the icon
    rgb, alpha = np.zeros((n, n, 3)), np.zeros((n, n))

    def fill(mask, c, a=1.0):
        rgb[mask], alpha[mask] = c, a

    def rounded(inset, radius):
        cx, cy = np.clip(x, inset + radius, size - inset - radius), np.clip(y, inset + radius, size - inset - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2

    ring = max(1.0, size / 26)  # The grey rim, 10 pixels at 256
    fill(rounded(0, size * 0.22), RING)
    fill(rounded(ring, size * 0.22 - ring), INSIDE)
    pitch, side = LAYOUT[size]
    x0, y0 = (size - COLS * pitch) // 2, (size - ROWS * pitch) // 2
    off = (pitch - side) // 2  # Whole pixels: sharp edges
    for r, c in SQUARES:
        sx, sy = x0 + c * pitch + off, y0 + r * pitch + off
        fill((x >= sx) & (x < sx + side) & (y >= sy) & (y < sy + side), colour(r, baseline))
    if tuner:  # A wave in the empty corner at the top right, where DelugeRec has its dot: one period of a sine, "~"
        wx0, wx1, cy, amp = x0 + 7.5 * pitch, x0 + 10.8 * pitch, y0 + 1.1 * pitch, 0.85 * pitch
        half = max(0.5, pitch * 0.36)  # Half the line's width: as strong as DelugeRec's dot
        box = (x > wx0 - half - 1) & (x < wx1 + half + 1) & (abs(y - cy) < amp + half + 1)
        bx, by, d2 = x[box], y[box], np.inf
        for u in np.linspace(0, 1, 240):  # The line: the points within half its width of the curve
            d2 = np.minimum(d2, (bx - wx0 - (wx1 - wx0) * u) ** 2 + (by - cy + amp * np.sin(2 * np.pi * u)) ** 2)
        box[box] = d2 <= half ** 2
        fill(box, WAVE)
    elif baseline:  # The line right above the third row (the grey ones pass behind it), over the width of the rain
        bottom, th = y0 + 2 * pitch + off, max(1.0, round(pitch * 0.45))
        fill((x >= x0 - pitch * 0.3) & (x < x0 + (COLS + 0.3) * pitch) & (y >= bottom - th) & (y < bottom), GOLD)
    else:  # The recording dot in the empty corner at the top right, as on a Deluge with REC lit
        cx, cy, rad = x0 + 9.2 * pitch, y0 + 1.1 * pitch, max(1.6, 1.55 * pitch)
        d2 = (x - cx) ** 2 + (y - cy) ** 2
        if size >= 48:
            fill(d2 <= (rad + size / 64) ** 2, DOT_RING)
        fill(d2 <= rad ** 2, RED)
    a = alpha.reshape(size, ss, size, ss).mean(axis=(1, 3))
    premult = (rgb * alpha[..., None]).reshape(size, ss, size, ss, 3).mean(axis=(1, 3))
    out = np.zeros((size, size, 4), np.uint8)
    out[..., :3] = np.round(premult / np.maximum(a, 1e-9)[..., None]).clip(0, 255)
    out[..., 3] = np.round(a * 255)
    return out


def png(img):
    h, w, ch = img.shape
    raw = b"".join(b"\0" + img[y].tobytes() for y in range(h))

    def chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if ch == 4 else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images):
    """An .ico with one PNG per size (Windows Vista and later)."""
    head, body, offset = struct.pack("<HHH", 0, 1, len(images)), b"", 6 + 16 * len(images)
    for size, data in images:
        head += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    return head + body


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    baseline, tuner = "--baseline" in sys.argv, "--tuner" in sys.argv
    folder = Path(args[0]) if args else Path(__file__).resolve().parent
    images = {size: draw(size, baseline=baseline, tuner=tuner) for size in LAYOUT}
    (folder / ("deluge_tuner.ico" if tuner else "deluge_baseline.ico" if baseline else "deluge_rec.ico")).write_bytes(
        ico([(s, png(im)) for s, im in images.items()]))
    b64 = base64.b64encode(png(images[64])).decode()  # For deluge_rec.py, deluge_baseline.py or deluge_tuner.py
    print("ICON_PNG = (" + "\n            ".join(f'"{b64[i:i + 104]}"' for i in range(0, len(b64), 104)) + ")")
    if "--preview" in sys.argv:  # Every size four times as large, on the app's panel colour
        sheet = np.zeros((256 * 4 + 8, sum(s * 4 + 8 for s in LAYOUT) + 8, 3), np.uint8)
        sheet[:] = (14, 14, 16)
        x = 8
        for s, im in images.items():
            big = im.repeat(4, 0).repeat(4, 1).astype(float)
            a = big[..., 3:] / 255
            area = sheet[8:8 + s * 4, x:x + s * 4]
            area[:] = np.round(big[..., :3] * a + area * (1 - a))
            x += s * 4 + 8
        (folder / "preview.png").write_bytes(png(sheet))


if __name__ == "__main__":
    main()
