#!/usr/bin/env python3
"""The song's master filters (GlobalEffectable: LPF and HPF in renderSongFX()) on the real firmware in the emulator,
the LPF turned with the upper gold knob in Song view (SessionView::modEncoderAction(1, offset), the firmware's own
param path), while the song plays.

Usage: master_filter_emu.py <deluge.elf> <out.npz> --lpf 24dB --hpf HPLadder --route H2L --hpf-freq K --hpf-res K
                            --lpf-res K [--turn slow|fast|none] [--synths N] [--tools PREFIX] [--build DIR]
  K: knob positions 0-50 as the display shows them. The LPF starts fully open (50, off) and is turned down to 0 and
  back up: slow, 1 click every 6 windows (about 2.2 s each way); fast, 4 clicks per window (about 0.1 s each way,
  then held). Writes the output (what the codec gets, stereo, full scale 1) and the LPF knob position per window.
"""
import argparse
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402


def song(lpf, hpf, route, hpf_freq, hpf_res, lpf_res, synths):
    files, lengths = make_sd.samples()
    xml = make_sd.song_xml(lengths, 1, synths)
    head_end = xml.index(">", xml.index("<song"))
    head = xml[:head_end].replace('lpfMode="24dB"', f'lpfMode="{lpf}"', 1).replace(
        'hpfMode="HPLadder"', f'hpfMode="{hpf}"', 1).replace('filterRoute="H2L"', f'filterRoute="{route}"', 1)
    # Affect Entire on: in Song view the gold knobs are the song's own params (Song::getActiveModControllable())
    head = head.replace('affectEntire="0"', 'affectEntire="1"', 1)
    xml = head + xml[head_end:]
    start = xml.index("<songParams")
    end = xml.index("</songParams>")
    block = xml[start:end]
    block = block.replace(f'<lpf frequency="{make_sd.knob(50)}" resonance="{make_sd.knob(0)}" />',
                          f'<lpf frequency="{make_sd.knob(50)}" resonance="{make_sd.knob(lpf_res)}" />', 1)
    block = block.replace(f'<hpf frequency="{make_sd.knob(0)}" resonance="{make_sd.knob(0)}" />',
                          f'<hpf frequency="{make_sd.knob(hpf_freq)}" resonance="{make_sd.knob(hpf_res)}" />', 1)
    xml = xml[:start] + block + xml[end:]
    # Without the drone (mastertune-v12 on), so every version from 1.2.1 on plays the same song
    if "<drone " in xml:
        xml = xml[:xml.index("\t<drone ")] + xml[xml.index("</drone>\n") + len("</drone>\n"):]
    files["SONGS/DEFAULT.XML"] = xml.encode()
    return files


def drain_pic(emu):
    """The PIC's UART ring counts as sent (the display updates of a knob turn would otherwise fill it)"""
    item = emu.sym["uartItems"]
    w = struct.unpack("<H", emu.uc.mem_read(item, 2))[0]
    emu.uc.mem_write(item + 2, struct.pack("<HHBB", w, w, 1, 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("out")
    ap.add_argument("--lpf", default="24dB")
    ap.add_argument("--hpf", default="HPLadder")
    ap.add_argument("--route", default="H2L")
    ap.add_argument("--hpf-freq", type=float, default=20)
    ap.add_argument("--hpf-res", type=float, default=40)
    ap.add_argument("--lpf-res", type=float, default=0)
    ap.add_argument("--turn", default="slow", choices=("slow", "fast", "none"))
    ap.add_argument("--synths", type=int, default=2)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--tools")
    ap.add_argument("--build", default=os.path.join(HERE, "..", "song"))
    args = ap.parse_args()
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    sd = args.out + ".sd.img"
    fat32.build(sd, song(args.lpf, args.hpf, args.route, args.hpf_freq, args.hpf_res, args.lpf_res, args.synths))
    emu = song_emu.Emulator(args.elf, sd, tools, args.build, lambda s: None)
    sym = emu.sym
    song_emu.setup_sd(emu)
    song_emu.init_sounds(emu)
    song_emu.boot(emu)
    emu.w32(sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    root = emu.call(sym["_Z9getRootUIv"])
    if root != sym["sessionView"]:
        raise SystemExit(f"the song doesn't open in Song view (root UI {root:#x})")
    if emu.call(sym["_Z12getCurrentUIv"]) != sym["sessionView"]:
        raise SystemExit("Song view isn't the current UI")
    # As when Song view comes up: the song's params become the gold knobs' (view.activeModControllableModelStack)
    drain_pic(emu)
    emu.call(sym["_ZN11SessionView13focusRegainedEv"], sym["sessionView"])
    drain_pic(emu)
    knob = sym.find("_ZN11SessionView16modEncoderActionEll")
    player = song_emu.Player(emu)
    player.start()
    out, pos_per_window = [], []
    total, window, pos, direction = 0, 0, 64, -1  # the knob's position -64..64 (64: fully open, the LPF off)
    warmup = song_emu.BAR // 2
    while total < args.seconds * 44100:
        if total >= warmup and args.turn != "none":
            clicks = 4 if args.turn == "fast" else (1 if window % 6 == 0 else 0)
            for _ in range(clicks):
                if pos == -64 and direction < 0:
                    direction = 1
                elif pos == 64 and direction > 0:
                    direction = 0
                if direction:
                    drain_pic(emu)
                    emu.call(knob, sym["sessionView"], 1, direction)
                    pos += direction
            drain_pic(emu)
        w = player.window()
        total += w[1]
        window += 1
        out.append(w[4])
        pos_per_window.append(np.full(w[1], pos))
    os.remove(sd)
    np.savez(args.out, x=np.concatenate(out).astype(np.float32), knob=np.concatenate(pos_per_window).astype(np.int8))


if __name__ == "__main__":
    main()
