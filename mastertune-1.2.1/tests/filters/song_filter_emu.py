#!/usr/bin/env python3
"""Song view: the song's own (master) LPF and HPF on the whole mix, in the real firmware in the emulator.

Usage: song_filter_emu.py <deluge.elf> <out dir> [--tools PREFIX] [--build DIR] [--synths N] [--cases LIST]

The song of tests/song/make_sd.py plays; its song-level HPF is on with resonance, and the song's LPF is turned with
the upper gold knob in song view (SessionView::modEncoderAction(1, +-1), the firmware's own knob path, mod knob mode
LPF), from fully open down to fully closed and back up, one click at a time, slow (2 s each way) and fast (0.25 s).
Each case is compared against the same turn with the HPF's resonance at 0.
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

SR = 44100


def song(lpf_mode, hpf_mode, route, hpf_knob, hpf_res, lpf_res, synths):
    files, lengths = make_sd.samples()
    xml = make_sd.song_xml(lengths, 1, synths)
    # Affect Entire on: song view's gold knobs work on the song's own params (Song::getActiveModControllable())
    xml = xml.replace('affectEntire="0"', 'affectEntire="1"', 1)
    xml = xml.replace('lpfMode="24dB"', f'lpfMode="{lpf_mode}"', 1)
    xml = xml.replace('hpfMode="HPLadder"', f'hpfMode="{hpf_mode}"', 1)
    xml = xml.replace('filterRoute="H2L"', f'filterRoute="{route}"', 1)
    xml = xml.replace(f'<lpf frequency="{make_sd.knob(50)}" resonance="{make_sd.knob(0)}" />',
                      f'<lpf frequency="{make_sd.knob(50)}" resonance="{make_sd.knob(lpf_res)}" />', 1)
    xml = xml.replace(f'<hpf frequency="{make_sd.knob(0)}" resonance="{make_sd.knob(0)}" />',
                      f'<hpf frequency="{make_sd.knob(hpf_knob)}" resonance="{make_sd.knob(hpf_res)}" />', 1)
    files["SONGS/DEFAULT.XML"] = xml.encode()
    return files


def drain_pic(emu):
    item = emu.sym["uartItems"]
    w = struct.unpack("<H", emu.uc.mem_read(item, 2))[0]
    emu.uc.mem_write(item + 2, struct.pack("<HHBB", w, w, 1, 0))


def play_turning(elf, out, tools, build, files, sweep_s, warmup_s=1.0, tail_s=0.5):
    sd = os.path.join(out, "sd.img")
    fat32.build(sd, files)
    emu = song_emu.Emulator(elf, sd, tools, build, lambda s: None)
    sym = emu.sym
    song_emu.setup_sd(emu)
    song_emu.init_sounds(emu)
    song_emu.boot(emu)
    emu.w32(sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    os.remove(sd)
    session = sym["sessionView"]
    # As when song view opens (SessionView::focusRegained()): the song is the view's mod controllable, so the gold
    # knobs work on its own params (setupStartupSong() doesn't open the view)
    drain_pic(emu)
    emu.call(sym.find("_ZN11SessionView13focusRegainedEv"), session)
    drain_pic(emu)
    knob = sym.find("_ZN11SessionView16modEncoderActionEll")
    player = song_emu.Player(emu)
    player.start()
    clicks = 128
    total_s = warmup_s + 2 * sweep_s + tail_s
    out_x, knob_at = [], []
    done, n, pos = 0, 0, 64
    while n < total_s * SR:
        t = n / SR - warmup_s
        # clicks due by now: down 128 in sweep_s, then up 128 in sweep_s
        due = 0 if t < 0 else min(2 * clicks, int(t / sweep_s * clicks))
        while done < due:
            drain_pic(emu)
            emu.call(knob, session, 1, -1 if done < clicks else 1)
            drain_pic(emu)
            done += 1
            pos += -1 if done <= clicks else 1
        w = player.window()
        n += w[1]
        out_x.append(w[4])
        knob_at.append((n, pos))
    return np.concatenate(out_x), knob_at


def hf_profile(x, n=2048, cut=8000):
    w = np.hanning(n)
    f = np.fft.rfftfreq(n, 1 / SR)
    rows = []
    for i in range(0, len(x) - n, n // 2):
        p = np.abs(np.fft.rfft(x[i:i + n] * w)) ** 2 / (w ** 2).sum() * 2 / n
        k = np.argmax(p * (f > cut))
        rows.append((i / SR, 10 * np.log10(p.sum() + 1e-20), 10 * np.log10(p[f > cut].sum() + 1e-20), f[k]))
    return np.array(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("out")
    ap.add_argument("--tools")
    ap.add_argument("--build", default=os.environ.get("BLOCKCOUNT_DIR", HERE))
    ap.add_argument("--synths", type=int, default=8)
    ap.add_argument("--case", action="append", default=[],
                    help="lpfMode,hpfMode,route,hpfKnob,hpfRes,lpfRes,sweep_s e.g. 24dB,HPLadder,H2L,20,50,0,2")
    args = ap.parse_args()
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(args.out, exist_ok=True)
    for case in args.case or ["24dB,HPLadder,H2L,20,50,0,2"]:
        lm, hm, route, hk, hr, lr, sw = case.split(",")
        res = {}
        for label, r in (("res", int(hr)), ("ref", 0)):
            x, knob_at = play_turning(args.elf, args.out, tools, args.build,
                                      song(lm, hm, route, int(hk), r, int(lr), args.synths), float(sw))
            np.save(os.path.join(args.out, f"{case.replace(',', '_')}_{label}.npy"), x)
            res[label] = hf_profile(x[:, 0])
        a, b = res["res"], res["ref"]
        m = min(len(a), len(b))
        e = a[:m, 2] - b[:m, 2]
        i = int(np.argmax(e))
        print(f"{case}: HF>8k worst excess {e[i]:+.1f} dB at t={a[i, 0]:.2f} s ({a[i, 2]:.1f} dBFS, total "
              f"{a[i, 1]:.1f} dBFS, peak {a[i, 3]:.0f} Hz); ref HF {b[i, 2]:.1f} dBFS; max HF {a[:, 2].max():.1f} "
              f"dBFS (ref {b[:, 2].max():.1f})", flush=True)


if __name__ == "__main__":
    main()
