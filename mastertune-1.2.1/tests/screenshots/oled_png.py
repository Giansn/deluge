#!/usr/bin/env python3
"""The OLED dumps of the emulator rigs ('#' lit, '.' dark, one line per pixel row, as save_oled() writes them) as
pictures for the README: each pixel a lit square with a thin gap, like the Deluge's OLED up close, on a dark rounded
screen.

Usage: oled_png.py <dump.txt> <out.png> [--scale 4]
"""
import argparse
import sys

from PIL import Image, ImageDraw

LIT = (236, 244, 255)
DARK = (20, 22, 26)  # An unlit pixel, just visible: the grid of the screen
SCREEN = (6, 7, 9)
BORDER = (58, 60, 66)


def render(rows, scale=4, pad=14):
    h, w = len(rows), max(len(r) for r in rows)
    size = (w * scale + 2 * pad, h * scale + 2 * pad)
    im = Image.new("RGBA", size, (0, 0, 0, 0))  # Transparent outside the rounded screen
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=pad, fill=SCREEN + (255,), outline=BORDER + (255,),
                        width=2)
    dot = max(1, scale - 1)
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            x0, y0 = pad + x * scale, pad + y * scale
            d.rectangle([x0, y0, x0 + dot - 1, y0 + dot - 1], fill=(LIT if c == "#" else DARK) + (255,))
    return im


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dump")
    ap.add_argument("out")
    ap.add_argument("--scale", type=int, default=4)
    a = ap.parse_args()
    rows = [line.rstrip("\n") for line in open(a.dump) if line.strip()]
    if not rows or any(set(r) - {"#", "."} for r in rows):
        print(f"{a.dump}: not an OLED dump", file=sys.stderr)
        return 1
    render(rows, a.scale).save(a.out, optimize=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
