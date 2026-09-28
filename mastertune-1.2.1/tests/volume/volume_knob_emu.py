#!/usr/bin/env python3
"""The song's volume on the gold knob in Song view, on the real firmware in the emulator (mastertune v18): the song
with Affect Entire on and the knobs in the volume/pan mode (GlobalEffectable::getParameterFromKnob(): the upper knob
is UNPATCHED_VOLUME), the knob turned through SessionView::modEncoderAction(1, offset), the firmware's own path,
while the song plays. The drone and every reverb send are off, so the output is the song's mix through the song
volume alone (the master compressor at threshold 0 is a fixed gain at these levels).

Checks:
- the popups (OLED::displayPopup / SevenSegment::displayPopup, hooked): each detent 0.5 dB, shown in dB; from the
  song's -8.2 dB (its stored volume, knob 22: between the steps) the first detent down snaps to -8.5 dB; turning up
  ends at the top, +6.0 dB, and stays there;
- the output against the same song played without turning (the gain the song volume applies, output / reference,
  where the reference isn't near zero): each detent moves the level by 0.5 dB +-0.02 (the steps sound equal), and
  the gain moves smoothly: no step between two samples larger than an eighth of a detent (the ramp over a window,
  down to ~10 samples where the clock cuts one short; v17 jumped by the whole detent, 5.6 %, at a window's start).

Usage: volume_knob_emu.py <deluge.elf> [--tools PREFIX] [--build DIR]  (exit 1 on a failure)
"""
import argparse
import os
import re
import struct
import sys

import numpy as np
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R1

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402


def song(synths):
    files, lengths = make_sd.samples()
    xml = make_sd.song_xml(lengths, 1, synths)
    head_end = xml.index(">", xml.index("<song"))
    head = xml[:head_end].replace('affectEntire="0"', 'affectEntire="1"', 1).replace(
        'activeModFunction="1"', 'activeModFunction="0"', 1)
    xml = head + xml[head_end:]
    if "<drone " in xml:
        xml = xml[:xml.index("\t<drone ")] + xml[xml.index("</drone>\n") + len("</drone>\n"):]
    xml = re.sub(r'reverbAmount="[^"]*"', 'reverbAmount="0x80000000"', xml)
    files["SONGS/DEFAULT.XML"] = xml.encode()
    return files


def drain_pic(emu):
    item = emu.sym["uartItems"]
    w = struct.unpack("<H", emu.uc.mem_read(item, 2))[0]
    emu.uc.mem_write(item + 2, struct.pack("<HHBB", w, w, 1, 0))


def play(args, turns):
    """turns: {window index: detents}. Returns the output (stereo) and the popups [(window, text)]"""
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    sd = os.path.join(args.build, f"volume_knob{os.getpid()}.sd.img")
    fat32.build(sd, song(args.synths))
    emu = song_emu.Emulator(args.elf, sd, tools, args.build, lambda s: None)
    sym = emu.sym
    popups = []
    window = [0]

    def on_popup(uc, address, size, user):
        p = uc.reg_read(UC_ARM_REG_R1)
        text = bytes(uc.mem_read(p, 64)).split(b"\0")[0].decode(errors="replace")
        popups.append((window[0], text))

    for name, (addr, _) in sym.by_name.items():
        if name.startswith("_ZN") and "displayPopupEPKc" in name and ("OLED" in name or "SevenSegment" in name):
            a = addr & ~1
            emu.uc.hook_add(UC_HOOK_CODE, on_popup, begin=a, end=a)
    song_emu.setup_sd(emu)
    song_emu.init_sounds(emu)
    song_emu.boot(emu)
    emu.w32(sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    if emu.call(sym["_Z9getRootUIv"]) != sym["sessionView"]:
        raise SystemExit("the song doesn't open in Song view")
    drain_pic(emu)
    emu.call(sym["_ZN11SessionView13focusRegainedEv"], sym["sessionView"])
    drain_pic(emu)
    knob = sym["_ZN11SessionView16modEncoderActionEll"]
    player = song_emu.Player(emu)
    player.start()
    out = []
    starts = []  # the sample each window starts at (a window is cut short where the clock ticks)
    total = 0
    while total < args.seconds * 44100:
        starts.append(total)
        for _ in range(abs(turns.get(window[0], 0))):
            drain_pic(emu)
            emu.call(knob, sym["sessionView"], 1, 1 if turns[window[0]] > 0 else 0xFFFFFFFF)
        drain_pic(emu)
        w = player.window()
        total += w[1]
        out.append(w[4])
        window[0] += 1
    os.remove(sd)
    return np.concatenate(out), popups, np.array(starts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("--synths", type=int, default=2)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--tools")
    ap.add_argument("--build", default=os.environ.get("BLOCKCOUNT_DIR", os.path.join(HERE, "..", "song")))
    args = ap.parse_args()
    fails = 0
    # One detent down every 20 windows (0.06 s), 8 times, from window 100 on; then 40 detents up at once (to the top)
    turns = {100 + 20 * k: -1 for k in range(8)}
    turns[300] = 40
    # Each in a process of its own, forked before any emulation (two runs in one process don't play the same: the
    # instruction counter behind the emulated time carries on), both at once
    import multiprocessing
    with multiprocessing.get_context("fork").Pool(2) as pool:
        (x, popups, starts), (ref, _, _) = pool.starmap(play, [(args, turns), (args, {})])
    if os.environ.get("VOLUME_KNOB_DUMP"):
        np.savez(os.environ["VOLUME_KNOB_DUMP"], x=x, ref=ref)
    texts = [t for _, t in popups]
    print("popups:", " | ".join(texts))
    values = []
    for t in texts:
        m = re.search(r"(-inf|[+-]?\d+\.\d)", t)
        values.append(m.group(1) if m else t)
    want = ["-8.5", "-9.0", "-9.5", "-10.0", "-10.5", "-11.0", "-11.5", "-12.0"]
    if values[:8] != want:
        print(f"FAIL: the detents down show {values[:8]}, not {want}")
        fails += 1
    if not values or values[-1] not in ("+6.0", "6.0"):
        print(f"FAIL: the top shows {values[-1] if values else None}, not +6.0")
        fails += 1
    # The gain: output / reference where the reference is loud enough (above -34 dBFS), per sample (left); the two
    # runs are the same up to the first turn
    n = min(len(x), len(ref))
    xs, rs = x[:n, 0].astype(np.float64), ref[:n, 0].astype(np.float64)
    first = starts[100]
    if np.any(xs[:first] != rs[:first]):
        print("FAIL: the runs differ before the first turn")
        fails += 1
    ok = np.abs(rs) > 0.02
    g = np.full(n, np.nan)
    g[ok] = xs[ok] / rs[ok]
    # the level after each detent: the median gain over the second half of the stretch until the next turn
    levels = []
    for k in range(8):
        a, b = starts[100 + 20 * k + 10], starts[100 + 20 * (k + 1)]
        seg = g[a:b]
        levels.append(20 * np.log10(np.median(seg[~np.isnan(seg)])))
    steps = np.diff(levels)
    print("level after each detent against before the turns (dB):", " ".join(f"{v:+.2f}" for v in levels))
    print("steps (dB):", " ".join(f"{v:+.3f}" for v in steps))
    if abs(levels[0] - (-8.5 - (-8.24))) > 0.03 or np.any(np.abs(steps + 0.5) > 0.02):
        print("FAIL: a detent doesn't move the level by 0.5 dB (the first: from -8.24 to -8.5)")
        fails += 1
    # smoothness: the largest change of the gain between two neighbouring loud samples while the 8 detents down turn
    a, b = starts[100], starts[260]
    idx = np.nonzero(ok[a:b])[0] + a
    close = np.diff(idx) == 1
    d = np.abs(np.diff(g[idx]))[close] / g[idx][1:][close]
    worst = float(np.max(d))
    detent = 1 - 10 ** (-0.5 / 20)
    print(f"largest gain change between two samples while turning: {worst * 100:.4f} % (a detent: {detent * 100:.2f} %, "
          f"which v17 applied at once)")
    # (a window cut short at a clock tick, down to ~10 samples, takes its detent over those samples)
    if worst > detent / 8:
        print("FAIL: the gain steps")
        fails += 1
    print("FAIL" if fails else "ok")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
