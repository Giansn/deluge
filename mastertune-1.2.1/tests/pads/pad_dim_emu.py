#!/usr/bin/env python3
"""Pad brightness on the real firmware in the emulator (tests/song's Emulator): what goes to the PIC.

Usage: pad_dim_emu.py <deluge.elf> [--base <1.2.1 deluge.elf>] [--tools <arm-none-eabi- prefix>] [--work <dir>]

Boots each ELF (as song_emu.py does, up to the task manager), then for every dimmer interval 0 (100 %) .. 25 (0 %)
calls PadLEDs::setDimmerInterval() (what the menu's setBrightnessLevel() and SHIFT + LEARN + vertical encoder call) and
reads the bytes the firmware put in the PIC's UART ring (picTxBuffer, uartItems[UART_ITEM_PIC]): the refresh time (19,
t), the dimmer interval (243, n) and whatever else it sends. With the pad-dim ELF, both ways of the community feature
"Flicker-free dimming" (runtimeFeatureSettings, offsets from the ELF's debug info), On then Off, as the menu does
(Setting::writeCurrentValue(), then PadLEDs::reapplyBrightness() = setDimmerInterval(dimmerInterval)).

At 100 %, 48 %, 44 %, 40 %, 20 %, 4 % and 0 % it also fills PadLEDs::image with test colours and sends the main pads
(sendOutMainPadColours()) and the sidebar (sendOutSidebarColours()), scrolls horizontally by one column
(horizontal::setupScroll(): the scroll row messages) and sets the gold knob indicators, and checks every colour and
indicator value the PIC gets against the expected scaling, computed here independently of the firmware's code:
round(v * light 1.2.1 / light PIC), at least 1 for v > 0, where light = refresh / (refresh + dimmer).

Checks: Off sends exactly what the 1.2.1 ELF sends (--base) and the colours unchanged; On keeps the PIC's scan period
(refresh + dimmer) at 23 and its refresh time at 10 or more, scales colours and indicators as expected, and resends the
pads and indicators when the scale changes. Prints the table of both. Needs python3 with unicorn 2 and numpy, cc.
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import song_emu  # noqa: E402
from song_emu import STOP, Emulator, setup_sd  # noqa: E402

UART_ITEM_PIC = 0
PIC_TX_BUFFER_SIZE = 1024
ROWS, COLS = 8, 18
FULL = 1 << 16


# --- the expected behaviour, written independently of hid/led/pad_dimming.h

def legacy_timing(interval):
    """1.2.1's setDimmerInterval() (pad_leds.cpp at a8e8af32)."""
    refresh = 23 - interval
    while refresh < 8:
        refresh += 1
        interval = int(interval * 1.2)
    return refresh, interval


def light(timing):
    refresh, dimmer = timing
    return refresh / (refresh + dimmer)


def expected_scale(interval):
    """16.16: the light 1.2.1 gives over what the PIC gives with the flicker-free timing (refresh >= 10, period 23)."""
    pic_dimmer = min(interval, 13)
    ratio = light(legacy_timing(interval)) / light((23 - pic_dimmer, pic_dimmer))
    return int(ratio * FULL + 0.5)


def scaled(v, scale):
    s = (v * scale + FULL // 2) >> 16
    return 1 if (s == 0 and v) else s


def test_colours():
    """160 colours: the palette's and derived ones (tails, grey), very dark ones, then pseudo-random ones."""
    base = [(255, 0, 0), (255, 128, 0), (255, 255, 0), (0, 255, 6), (0, 0, 255), (128, 0, 255), (7, 7, 7),
            (60, 15, 15), (60, 15, 60), (30, 30, 10), (54, 29, 3), (46, 16, 2), (37, 15, 37), (221, 72, 13),
            (130, 120, 130), (255, 255, 255), (127, 127, 127), (1, 1, 1), (2, 1, 0), (3, 3, 3), (4, 2, 1), (5, 5, 50),
            (10, 5, 2), (0, 0, 0), (255, 48, 0), (255, 44, 50), (60, 60, 60), (0, 128, 128)]
    x = 12345
    while len(base) < ROWS * COLS:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        base.append(((x >> 8) & 0xFF, (x >> 16) & 0xFF, (x >> 3) & 0x3F))
    return base


# --- the firmware

class Pic:
    """What the firmware writes into the PIC's UART ring between begin() and end()."""

    def __init__(self, emu):
        self.emu = emu
        self.item = emu.sym["uartItems"] + 8 * UART_ITEM_PIC
        self.buffer = emu.sym["picTxBuffer"]

    def begin(self):
        w = struct.unpack("<H", self.emu.uc.mem_read(self.item, 2))[0]
        # All sent (read positions at the write position, so the space is the whole buffer), and txSending: the
        # firmware's flushes leave the ring alone (no transfer-end interrupt in the emulator)
        self.emu.uc.mem_write(self.item + 2, struct.pack("<HHBB", w, w, 1, 0))
        self.start = w

    def end(self):
        w = struct.unpack("<H", self.emu.uc.mem_read(self.item, 2))[0]
        ring = bytes(self.emu.uc.mem_read(self.buffer, PIC_TX_BUFFER_SIZE))
        n = (w - self.start) % PIC_TX_BUFFER_SIZE
        return bytes(ring[(self.start + i) % PIC_TX_BUFFER_SIZE] for i in range(n))


def parse(data):
    """The PIC messages (pic.h's Message) in data: (name, payload)."""
    out, i = [], 0
    while i < len(data):
        m = data[i]
        if 1 <= m <= 9:  # SET_COLOUR_FOR_TWO_COLUMNS + column pair: 16 colours
            payload, i = data[i + 1:i + 49], i + 49
            out.append(("cols", (m - 1, [tuple(payload[j:j + 3]) for j in range(0, 48, 3)])))
        elif m == 19:
            out.append(("refresh", data[i + 1]))
            i += 2
        elif m == 243:
            out.append(("dimmer", data[i + 1]))
            i += 2
        elif m in (20, 21):
            out.append(("knob", (m - 20, list(data[i + 1:i + 5]))))
            i += 5
        elif 228 <= m < 236:  # SET_SCROLL_ROW + row: 1 colour
            out.append(("scrollrow", (m - 228, tuple(data[i + 1:i + 4]))))
            i += 4
        elif 236 <= m < 240:
            out.append(("scrollsetup", m - 236))
            i += 1
        elif m == 240:
            out.append(("done", None))
            i += 1
        else:
            out.append(("?", m))
            i += 1
    return out


def boot(emu):
    """resetprg() up to deluge_main()'s setupBlankSong(), right after it set the pad brightness from the settings
    (FlashStorage, the community features from the SD card): everything the pads and the PIC need is set up there.
    (song_emu.py's boot() stops at registerTasks(), which this build has inlined.)"""
    hooks = []

    def at_blank_song(e):
        e.stop()
        e.uc.hook_del(hooks[0])
    hooks.append(emu.intercept(emu.sym["_Z14setupBlankSongv"], at_blank_song))
    emu.uc.reg_write(song_emu.UC_ARM_REG_SP, song_emu.PROGRAM_STACK_TOP)
    emu.run(emu.sym["resetprg"] | 1, STOP, timeout_s=5)
    if not emu.sym.name_at(emu.uc.reg_read(song_emu.UC_ARM_REG_PC)).startswith("setupBlankSong()+0x0"):
        raise SystemExit(f"boot stopped at {emu.sym.name_at(emu.uc.reg_read(song_emu.UC_ARM_REG_PC))}")


def fn(emu, demangled):
    address, _ = emu.sym.by_name[demangled]
    return address


def gdb_values(emu, expressions):
    out = subprocess.run([emu.tool_prefix + "gdb", "-batch"] + [x for e in expressions for x in ("-ex", f"print {e}")]
                         + [emu.elf], capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (-?\d+)$", out, re.M)]
    if len(values) != len(expressions):
        raise SystemExit(f"gdb: {expressions}:\n{out[-500:]}")
    return values


class Firmware:
    def __init__(self, elf, tools, work, log):
        self.log = log
        sd = os.path.join(work, "sd.img")
        if not os.path.exists(sd):
            subprocess.run([sys.executable, os.path.join(HERE, "..", "song", "make_sd.py"), sd, "--synths", "1"],
                           check=True, capture_output=True)
        emu = self.emu = Emulator(elf, sd, tools, work, lambda s: None)
        setup_sd(emu)
        boot(emu)
        self.pic = Pic(emu)
        self.has_feature = "PIC::colourScale" in emu.sym.by_name
        if self.has_feature:
            index, size, settings, value = gdb_values(emu, [
                "(int)FlickerFreeDimming", "sizeof(RuntimeFeatureSetting)",
                "(int)&((RuntimeFeatureSettings*)0)->settings", "(int)&((RuntimeFeatureSetting*)0)->value"])
            self.feature = emu.sym["runtimeFeatureSettings"] + settings + index * size + value
            self.colour_scale = emu.sym["PIC::colourScale"]
        self.set_dimmer = fn(emu, "PadLEDs::setDimmerInterval(long)")
        self.image = emu.sym["PadLEDs::image"]
        self.image_store = emu.sym["PadLEDs::imageStore"]

    def call(self, name_or_address, *args):
        address = name_or_address if isinstance(name_or_address, int) else fn(self.emu, name_or_address)
        self.emu.pic_calls = getattr(self.emu, "pic_calls", 0) + 1
        return self.emu.call(address, *args, timeout_s=5)

    def capture(self, *calls):
        self.pic.begin()
        for c in calls:
            self.call(*c)
        return parse(self.pic.end())

    def set_feature(self, on):
        self.emu.w32(self.feature, 1 if on else 0)

    def scale(self):
        return self.emu.u32(self.colour_scale) if self.has_feature else FULL

    def dimmer_interval(self, interval):
        """The messages setDimmerInterval() sends, and whether it asked for the pads to be sent again (the flags
        sendOut{MainPad,Sidebar}ColoursSoon() set for the matrix driver's timer)."""
        flags = [self.emu.sym["PadLEDs::needToSendOutMainPadColours"], self.emu.sym["PadLEDs::needToSendOutSidebarColours"]]
        for a in flags:
            self.emu.uc.mem_write(a, b"\0")
        msgs = self.capture((self.set_dimmer, interval))
        return msgs, all(self.emu.u8(a) for a in flags)

    def reapply(self):
        """As the menu does after the setting changed: PadLEDs::reapplyBrightness() (inlined there)."""
        return self.dimmer_interval(self.emu.u32(self.emu.sym["PadLEDs::dimmerInterval"]))

    def pads(self, colours):
        emu = self.emu
        flat = b"".join(bytes(c) for c in colours)
        emu.uc.mem_write(self.image, flat)
        emu.uc.mem_write(emu.sym["PadLEDs::slowFlashSquares"], b"\xff" * ROWS)  # No slow-flash cursor on a pad
        return self.capture(("PadLEDs::sendOutMainPadColours()",), ("PadLEDs::sendOutSidebarColours()",))

    def scroll(self, store_colours):
        """horizontal::setupScroll(1, 18, false, 16): sends the setup and, per row, the column coming in (imageStore
        column 2) as the new rightmost one."""
        emu = self.emu
        emu.uc.mem_write(self.image_store, b"".join(bytes(c) for c in store_colours))
        emu.uc.mem_write(emu.sym["PadLEDs::transitionTakingPlaceOnRow"], b"\x01" * ROWS)
        return self.capture(("PadLEDs::horizontal::setupScroll(signed char, unsigned char, bool, long)", 1, 18, 0, 16))

    def knobs(self, levels):
        # No level before (as resendKnobIndicatorLevels() does), so each call sends its level
        self.emu.uc.mem_write(self.emu.sym["indicator_leds::knobIndicatorLevels"], b"\xff" * len(levels))
        knob = "indicator_leds::setKnobIndicatorLevel(unsigned char, unsigned char, bool)"
        return [m for m in self.capture(*[(knob, k, level, 0) for k, level in enumerate(levels)]) if m[0] == "knob"]


DETAIL = [0, 13, 14, 15, 20, 24, 25]


def check_pads(fw, interval, mode, log, failures):
    colours = test_colours()
    scale = fw.scale() if mode == "on" else FULL
    msgs = fw.pads(colours)
    got = {}
    for name, payload in msgs:
        if name == "cols":
            pair, cs = payload
            for i, c in enumerate(cs):
                got[(2 * pair + i // 8, i % 8)] = c  # (x, y)
    bad = 0
    worst_hue = 0.0
    for y in range(ROWS):
        for x in range(COLS):
            src = colours[y * COLS + x]
            want = tuple(scaled(v, scale) for v in src)
            if got.get((x, y)) != want:
                bad += 1
                if bad <= 3:
                    log(f"    pad {x},{y}: {src} -> {got.get((x, y))}, expected {want}")
    if bad or len(got) != ROWS * COLS:
        failures.append(f"{mode} interval {interval}: {bad} pad colours wrong, {len(got)} sent")
    # Knobs: full (level 128: 4 x 255) and partial (level 80: 255, 255, 64, 0)
    knobs = fw.knobs([128, 80])
    want_knobs = [[scaled(v, scale) for v in (255, 255, 255, 255)], [scaled(v, scale) for v in (255, 255, 64, 0)]]
    if [k[1][1] for k in knobs] != want_knobs:
        failures.append(f"{mode} interval {interval}: knob indicators {knobs}, expected {want_knobs}")
    # Horizontal scroll: rows get imageStore[row][2] scaled
    store = [colours[(i * 7) % len(colours)] for i in range(ROWS * 2 * COLS)]
    msgs = fw.scroll(store)
    rows = {p[0]: p[1] for n, p in msgs if n == "scrollrow"}
    want_rows = {r: tuple(scaled(v, scale) for v in store[r * COLS + 2]) for r in range(ROWS)}
    if rows != want_rows:
        failures.append(f"{mode} interval {interval}: scroll rows {rows}, expected {want_rows}")
    return got, knobs


def run(elf, tools, work, log, modes):
    fw = Firmware(elf, tools, work, log)
    results = {}
    failures = []
    for mode in modes:
        if mode in ("on", "off"):
            fw.set_feature(mode == "on")
            fw.reapply()
        rows = []
        for interval in range(26):
            msgs, pads_resent = fw.dimmer_interval(interval)
            timing = [p for n, p in msgs if n in ("refresh", "dimmer")]
            others = [n for n, p in msgs if n not in ("refresh", "dimmer")]
            rows.append({"interval": interval, "refresh": timing[0], "dimmer": timing[1], "scale": fw.scale(),
                         "others": others, "pads_resent": pads_resent,
                         "raw": [m for m in msgs if m[0] in ("refresh", "dimmer")]})
            if interval in DETAIL:
                check_pads(fw, interval, mode, log, failures)
        results[mode] = rows
    return results, failures, fw


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--base", help="the 1.2.1 (mastertune-v13) ELF to compare the Off mode with")
    ap.add_argument("--tools", help="toolchain prefix (.../arm-none-eabi-); default: the ELF's tree's")
    ap.add_argument("--work", help="directory for blockcount.so and the SD image (default: a temporary one)")
    args = ap.parse_args()
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    work = args.work or tempfile.mkdtemp()
    os.makedirs(work, exist_ok=True)
    uc_dir = subprocess.run([sys.executable, "-c", "import os, unicorn; print(os.path.dirname(unicorn.__file__))"],
                            capture_output=True, text=True, check=True).stdout.strip()
    so = os.path.join(work, "blockcount.so")
    if not os.path.exists(so):
        subprocess.run(["cc", "-O2", "-shared", "-fPIC", f"-I{uc_dir}/include",
                        os.path.join(HERE, "..", "song", "blockcount.c"), "-o", so, f"-L{uc_dir}/lib",
                        "-l:libunicorn.so.2", f"-Wl,-rpath,{uc_dir}/lib"], check=True)

    def log(s):
        print(s, flush=True)

    failures = []
    new, f, fw = run(args.elf, tools, work, log, ["on", "off"])
    failures += f
    if not fw.has_feature:
        raise SystemExit("this ELF has no flicker-free dimming (PIC::colourScale)")
    base = None
    if args.base:
        base, f, _ = run(args.base, tools, work, log, ["legacy"])
        failures += f

    log("interval  UI  | 1.2.1: refresh dimmer period light | Off = 1.2.1? | On: refresh dimmer period  scale  "
        "light (PIC x scale, 255)  / 1.2.1")
    for i in range(26):
        on, off = new["on"][i], new["off"][i]
        lt = legacy_timing(i)
        l_old = light(lt)
        same = (base is None or base["legacy"][i]["raw"] == off["raw"]) and (off["refresh"], off["dimmer"]) == lt
        if not same:
            failures.append(f"interval {i}: Off sends {off['raw']}, 1.2.1 {base['legacy'][i]['raw'] if base else lt}")
        if off["scale"] != FULL:
            failures.append(f"interval {i}: Off has colour scale {off['scale']}")
        period_on = on["refresh"] + on["dimmer"]
        if period_on != 23 or on["refresh"] < 10:
            failures.append(f"interval {i}: On sends refresh {on['refresh']}, dimmer {on['dimmer']}")
        if on["scale"] != expected_scale(i):
            failures.append(f"interval {i}: On colour scale {on['scale']}, expected {expected_scale(i)}")
        l_on = light((on["refresh"], on["dimmer"])) * scaled(255, on["scale"]) / 255
        log(f"{i:8d} {(25 - i) * 4:3d}% | {lt[0]:6d} {lt[1]:6d} {sum(lt):3d} ({sum(lt) / 23:.1f}x) {l_old:.4f} | "
            f"{'yes' if same else 'NO':>12} | {on['refresh']:6d} {on['dimmer']:6d} {period_on:6d}  "
            f"{on['scale'] / FULL:.3f}  {l_on:.4f}  {l_on / l_old:.3f}")
    # The pads (flagged for the matrix driver's timer) and knob indicators (at once) are sent again when the scale
    # changes, and only then
    for mode in ("on", "off"):
        rows = new[mode]
        changed = [i for i in range(26) if rows[i]["scale"] != (rows[i - 1]["scale"] if i else FULL)]
        pads = [i for i in range(26) if rows[i]["pads_resent"]]
        knobs = [i for i in range(26) if rows[i]["others"].count("knob") == 2]
        extra = sorted({n for r in rows for n in r["others"]} - {"knob"})
        log(f"{mode}: scale changed at intervals {changed}; pads sent again at {pads}, knob indicators at {knobs}; "
            f"other messages: {extra or 'none'}")
        if not (changed == pads == knobs) or extra:
            failures.append(f"{mode}: resends don't match the scale changes")
    log(f"checked pad, sidebar, scroll and knob values at intervals {DETAIL}, On and Off"
        + (" and on the 1.2.1 ELF" if base else ""))
    if failures:
        log(f"FAILED ({len(failures)}):")
        for f in failures[:30]:
            log("  " + f)
        sys.exit(1)
    log("all checks passed")


if __name__ == "__main__":
    main()
