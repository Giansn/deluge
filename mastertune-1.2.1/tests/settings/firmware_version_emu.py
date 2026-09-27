#!/usr/bin/env python3
"""Settings > Firmware version on the OLED, readable in full (mastertune-v18), on the real firmware in the emulator
(the harness of ../song).

v17 drew kFirmwareVersionString ("1.2.1-mastertune-v17-l2d-b3385d83", 33 characters) on one line with
Canvas::drawStringCentredShrinkIfNecessary(): 128 / 33 = 3 pixels per character of the 6 the small font needs, so
the letters overlapped. Now it's broken after a '-' onto up to three lines in the normal small font (6 x 7 pixels,
9 pixels from line to line), as few lines as it takes and those as even as they can be, each centred, the block
centred below the title.

What's drawn is taken from the canvas calls (Canvas::drawChar(): character, position, spacing, height) and from the
OLED's image (OLED::main): for the version string of this ELF (the menu item's own renderOLED(): title, then the
text) and, through drawLinesBrokenAtDashes() with a string of the test's, for the real v17 string (33), the v16 L2
string (42), one of 48 and a short one:
- the characters drawn are the string, in order; each with the small font's spacing (6) and height (7)
- lines: each (but the last) ends with '-', at most 21 characters (128 pixels); within a line the characters 6
  pixels apart; centred (left and right margins within a pixel); lines 9 pixels apart; the block centred between
  the title's line and the bottom (within a pixel)
- the lines expected (fewest lines, then the most even), e.g. "1.2.1-mastertune-" / "v17-l2d-b3385d83"
- the image below the title is exactly the lines drawn alone with Canvas::drawString() in the small font at the
  test's own positions: no pixel squeezed, overlapped or missing
Usage: firmware_version_emu.py <deluge.elf> [--tools PREFIX] [--out DIR]   (BLOCKCOUNT_DIR: where blockcount.so is)
Exit status 0 when every check passes (an ELF without drawLinesBrokenAtDashes(), e.g. v17: the menu's own string
only)."""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP  # noqa: E402

WIDTH, HEIGHT = 128, 48  # OLED_MAIN_WIDTH_PIXELS, OLED_MAIN_HEIGHT_PIXELS
TOP = 5 + 1 + 12  # Below the title's line (OLED_MAIN_TOPMOST_PIXEL 5, Canvas::drawScreenTitle())
SPACING_X, SIZE_Y, SPACING_Y = 6, 7, 9  # kTextSpacingX, kTextSizeYUpdated, kTextSpacingY
TEXT = se.STOP + 0x100  # Scratch memory for the strings

STRINGS = [
    ("the real v17 string", "1.2.1-mastertune-v17-l2d-b3385d83", ["1.2.1-mastertune-", "v17-l2d-b3385d83"]),
    ("the v16 L2 string (42)", "1.2.1-mastertune-v16-l2d-dronefix-ba499a93",
     ["1.2.1-mastertune-v16-", "l2d-dronefix-ba499a93"]),
    ("48 characters", "1.2.1-mastertune-v18-l2d-hotfix2-0123abcd-dirty1",
     ["1.2.1-mastertune-", "v18-l2d-hotfix2-", "0123abcd-dirty1"]),
    ("a short one", "1.2.1", ["1.2.1"]),
]

failures = 0
checks = 0


def check(what, ok, detail=""):
    global failures, checks
    checks += 1
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {what}" + (f": {detail}" if detail else ""), flush=True)


class Oled:
    def __init__(self, emu):
        self.emu = emu
        sym = emu.sym
        self.canvas = sym["_ZN6deluge3hid7display4OLED4mainE"]
        if sym.by_name["_ZN6deluge3hid7display4OLED4mainE"][1] != WIDTH * HEIGHT // 8:
            raise SystemExit("OLED::main isn't just its image")
        self.image_offset = 0  # Canvas: image_ its only member
        self.draw_string = sym.find("_ZN6deluge3hid7display11oled_canvas6Canvas10drawStringESt17basic_string_view")
        self.chars = None
        emu.intercept(sym.find("_ZN6deluge3hid7display11oled_canvas6Canvas8drawCharEh"), self.on_char)
        emu.uc.ctl_flush_tb()

    def on_char(self, emu):
        if self.chars is not None:
            uc = emu.uc
            sp = uc.reg_read(UC_ARM_REG_SP)
            spacing, height = struct.unpack("<ii", uc.mem_read(sp, 8))
            to_signed = lambda v: v - (1 << 32) if v & 0x80000000 else v  # noqa: E731
            self.chars.append((chr(uc.reg_read(UC_ARM_REG_R1) & 0xFF), to_signed(uc.reg_read(UC_ARM_REG_R2)),
                               to_signed(uc.reg_read(UC_ARM_REG_R3)), spacing, height))

    def clear(self):
        self.emu.uc.mem_write(self.canvas + self.image_offset, bytes(WIDTH * HEIGHT // 8))

    def image(self):
        """pixels[y][x] (0/1) of OLED::main"""
        raw = bytes(self.emu.uc.mem_read(self.canvas + self.image_offset, WIDTH * HEIGHT // 8))
        return [[(raw[(y >> 3) * WIDTH + x] >> (y & 7)) & 1 for x in range(WIDTH)] for y in range(HEIGHT)]

    def record(self, fn, *args):
        """fn(*args) on a clear canvas: the characters drawn, the image"""
        self.clear()
        self.chars = []
        self.emu.call(fn, *args)
        chars, self.chars = self.chars, None
        return chars, self.image()

    def reference(self, lines):
        """The lines drawn alone in the small font where the test expects them: the image"""
        self.clear()
        y = TOP + (HEIGHT - TOP - ((len(lines) - 1) * SPACING_Y + SIZE_Y)) // 2
        for line in lines:
            self.emu.uc.mem_write(TEXT, line.encode() + b"\0")
            x = (WIDTH - len(line) * SPACING_X) // 2
            # drawString(string_view{len, ptr}, x, y, textWidth, textHeight, scrollPos, endX, useTextWidth)
            uc = self.emu.uc
            args = struct.pack("<iiiiii", y, SPACING_X, SIZE_Y, 0, WIDTH, 0)
            sp = se.PROGRAM_STACK_TOP - 0x40
            uc.mem_write(sp, args)
            self.call_with_stack(self.draw_string, sp, self.canvas, len(line), TEXT, x)
            y += SPACING_Y
        return self.image()

    def call_with_stack(self, address, sp, *args):
        """emu.call() with the stack arguments already at sp"""
        emu, uc = self.emu, self.emu.uc
        for reg, value in zip((se.UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            uc.reg_write(reg, value & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(se.UC_ARM_REG_LR, se.STOP | 1)
        emu.run(address | 1, se.STOP)


def expected_lines(text):
    """The test's own layout: broken after a '-' (a part longer than a line cut where it's full only if there's no
    other way), at most 21 characters a line and three lines; the fewest lines, then the shortest longest line"""
    def fill(limit, hard):
        starts, pos = [], 0
        while pos < len(text):
            if len(starts) == 3:
                return None
            starts.append(pos)
            if len(text) - pos <= limit:
                break
            ends = [i + 1 for i in range(pos, pos + limit) if text[i] == "-"]
            if not ends and not hard:
                return None
            pos = ends[-1] if ends else pos + limit
        return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    for hard in (False, True):
        widest = fill(WIDTH // SPACING_X, hard)
        if widest is not None:
            return next(f for f in (fill(limit, hard) for limit in range(1, 22)) if f and len(f) <= len(widest))
    return None


def lines_of(chars):
    """The characters drawn, grouped into lines by their y"""
    lines = []
    for c in chars:
        if not lines or lines[-1][0][2] != c[2]:
            lines.append([])
        lines[-1].append(c)
    return lines


def check_text(oled, what, text, chars, image, expected=None):
    print(f"== {what}: {text!r} ({len(text)} characters)", flush=True)
    drawn = "".join(c[0] for c in chars)
    check("the characters drawn are the string, in order", drawn == text, repr(drawn))
    sizes = sorted({(c[3], c[4]) for c in chars})
    check("each in the small font: spacing 6, height 7", sizes == [(SPACING_X, SIZE_Y)], f"{sizes}")
    lines = lines_of(chars)
    texts = ["".join(c[0] for c in line) for line in lines]
    ours = expected_lines(text)
    if expected is not None and ours != expected:
        raise SystemExit(f"the test's own layout {ours} isn't the one written down {expected}")
    check(f"the lines {ours}", texts == ours, f"{texts}")
    check("at most three lines, each (but the last) ending with '-'", len(lines) <= 3
          and all(t.endswith("-") for t in texts[:-1]), f"{texts}")
    steps = {b[1] - a[1] for line in lines for a, b in zip(line, line[1:])}
    check("within a line 6 pixels from character to character", steps <= {SPACING_X}, f"{sorted(steps)}")
    margins = [(line[0][1], WIDTH - (line[-1][1] + SPACING_X)) for line in lines]
    check("each line inside the 128 pixels and centred (margins within a pixel)",
          all(left >= 0 and right >= 0 and abs(left - right) <= 1 for left, right in margins), f"{margins}")
    ys = [line[0][2] for line in lines]
    gaps = {b - a for a, b in zip(ys, ys[1:])}
    top, bottom = ys[0] - TOP, HEIGHT - (ys[-1] + SIZE_Y)
    check("lines 9 pixels apart, the block centred below the title (within a pixel)",
          gaps <= {SPACING_Y} and top >= 0 and bottom >= 0 and abs(top - bottom) <= 1,
          f"y {ys}, {top} above, {bottom} below")
    reference = oled.reference(ours)
    below = lambda im: [row for row in im[TOP:]]  # noqa: E731
    differ = sum(a != b for ra, rb in zip(below(image), below(reference)) for a, b in zip(ra, rb))
    lit = sum(map(sum, below(image)))
    check("the image below the title: exactly those lines drawn alone in the small font", differ == 0 and lit > 0,
          f"{differ} of {WIDTH * (HEIGHT - TOP)} pixels differ, {lit} lit")
    return image


def show(image):
    for row in image[TOP - 12:]:
        print("   " + "".join("#" if p else "." for p in row))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools")
    ap.add_argument("--out", default=os.getcwd())
    ap.add_argument("--show", action="store_true", help="print the menu's image")
    a = ap.parse_args()
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(a.out, exist_ok=True)
    sd = os.path.join(a.out, "version.img")
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 2).encode()
    make_sd.fat32.build(sd, files)

    emu = se.Emulator(a.elf, sd, tools, os.environ.get("BLOCKCOUNT_DIR", HERE), lambda s: None)
    se.setup_sd(emu)
    se.boot(emu)
    sym = emu.sym
    emu.call(sym["_ZN6deluge3hid7display15swapDisplayTypeEv"])  # The OLED in place of the 7-segment display
    oled = Oled(emu)

    # The menu item as the sound editor draws it: title, then the version (MenuItem::renderOLED())
    menu = sym["firmwareVersionMenu"]
    render = sym.find("_ZN8MenuItem10renderOLEDEv")
    chars, image = oled.record(render, menu)
    title_rows = sum(map(sum, image[:TOP]))
    body = [c for c in chars if c[2] >= TOP]
    text = "".join(c[0] for c in body)
    print(f"the ELF's version string, as drawn: {text!r}")
    check("the menu's title drawn above", title_rows > 0, f"{title_rows} pixels lit above y {TOP}")
    check_text(oled, "Settings > Firmware version (this ELF's kFirmwareVersionString)", text, body, image)
    if a.show:
        show(image)

    names = [n for n in sym.by_name if "drawLinesBrokenAtDashes" in n]
    if not names:
        print("no drawLinesBrokenAtDashes() in this ELF: the menu's own string only")
    else:
        draw = sym[names[0]]
        for what, text, expected in STRINGS:
            emu.uc.mem_write(TEXT, text.encode() + b"\0")
            chars, image = oled.record(draw, oled.canvas, TEXT)
            check_text(oled, what, text, chars, image, expected)
            if a.show:
                show(image)

    print(f"Firmware version on the OLED ({os.path.basename(a.elf)}): {checks - failures} of {checks} ok", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
