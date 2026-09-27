#!/usr/bin/env python3
"""The audio while the song browser is scrolled during playback, on the real firmware in the emulator (tests/sdload's
harness: its Run, the firmware's own task manager, song_emu.RealTimeDma for the SSI's DMA in real time).

The card: tests/sdload's s5 image (make_bigsd.py s5): SONGS/DEFAULT.XML (tests/song's song, with its samples) and 1,200
songs; "SONG 010 final mix", "... A", "... B" .. "SONG 400 final mix B" are 120 files beginning with the word SONG, one
group of versions in the song browser (mastertune), sorted right after DEFAULT; then SONG001, SONG001A, SONG001B, ...
DEFAULT is loaded and played; the song browser is opened on it (while playing); then the select encoder is turned one
step at a time (LoadSongUI::selectEncoderAction()) with --settle-ms of the task manager after each step (a fast turn):
  group    across the group: +1 until SONG001, then -1 until DEFAULT (with the grouping 2 steps each way, without 121)
  files    across hundreds of files: from SONG001 (reached unmeasured) +1 --files times, then -1 as many
Per phase: steps, folder reads (Browser::readFileItemsFromFolderAndMemory()), the longest time without the audio
routine (from the end of one AudioEngine::routine() call to the start of the next), the DMA's largest gap (how many
samples it played since the buffer was last full) and underrun samples (a gap of 128 or more: old samples again).
The card: --sd-latency CMD_US,SECTOR_US (default 1000,42.67: typical, its waits yield to the task manager as the
firmware's) or "instant".

Usage: browse_audio_emu.py <deluge.elf> <s5 image> <out dir> [--sd-latency ...] [--settle-ms MS] [--files N]
                           [--tools PREFIX] [--build DIR] [--name NAME]
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../song"))
sys.path.insert(0, os.path.join(HERE, "../sdload"))
import sdload_emu as sdl  # noqa: E402
import song_emu as se  # noqa: E402
from song_emu import SAMPLE_RATE  # noqa: E402

UI_MODE_HORIZONTAL_SCROLL = 1 << 29


def log(s):
    print(s, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("--sd-latency", default="1000,42.67")
    ap.add_argument("--settle-ms", type=float, default=20)
    ap.add_argument("--files", type=int, default=300)
    ap.add_argument("--tools")
    ap.add_argument("--build", default=HERE)
    ap.add_argument("--name", default="browse_audio")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    a.scenario = "browse"
    run = sdl.Run(a)
    emu, sym = run.emu, run.emu.sym
    run.setup_tasks()
    run.reset_counters()
    if a.sd_latency != "instant":
        se.SdModel(emu, *(float(x) for x in a.sd_latency.split(",")), wait="yield")
    string_memory, = se.gdb_values(emu, ["(int)&((String*)0)->stringMemory"])
    entered = sym["_ZN8QwertyUI11enteredTextE"]

    def name():
        p = emu.u32(entered + string_memory)
        return emu.ram(p, 128).split(b"\0")[0].decode(errors="replace") if p else ""
    reads = [0]

    def on_read(e):
        reads[0] += 1
    emu.intercept(sym.find("_ZN7Browser32readFileItemsFromFolderAndMemory"), on_read)
    emu.uc.ctl_flush_tb()
    ui, mode_at = sym["loadSongUI"], sym["currentUIMode"]
    select = sym["_ZN10LoadSongUI19selectEncoderActionEa"]

    emu.call(sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
    se.run_task_manager(emu, 0.5)
    emu.call(sym["_Z6openUIP2UI"], ui, timeout_s=120)
    waited = 0.0
    while emu.u32(mode_at) and waited < 2:
        se.run_task_manager(emu, 0.05)
        waited += 0.05
    log(f"playing; the browser opened on {name()!r} (scroll-in {waited:.2f} s)")

    result = dict(elf=a.elf, sd_latency=a.sd_latency, settle_ms=a.settle_ms, phases={})

    def phase(label, moves):
        """moves: [(offset, stop(name) or None, max steps)]"""
        run.reset_counters()
        dma = se.RealTimeDma(emu)
        r0, t, i0 = reads[0], time.time(), emu.now()
        steps, path = 0, [name()]
        for offset, stop, limit in moves:
            for _ in range(limit):
                se.drain_uarts(emu)
                if emu.u32(mode_at) not in (0, UI_MODE_HORIZONTAL_SCROLL):
                    emu.w32(mode_at, 0)
                emu.call(select, ui, offset, timeout_s=120)
                steps += 1
                se.run_task_manager(emu, a.settle_ms / 1e3)
                if stop and stop(name()):
                    break
            path.append(name())
        dma.close()
        calls = sorted(run.routine_calls, key=lambda c: c[3])
        longest = max((b[3] - c[4] for c, b in zip(calls, calls[1:])), default=0)
        us = lambda n: n / sdl.hz() * 1e6  # noqa: E731
        p = dict(steps=steps, path=path, folder_reads=reads[0] - r0, emulated_s=(emu.now() - i0) / sdl.hz(),
                 longest_without_audio_routine_us=us(longest), dma_max_gap_samples=dma.max_gap,
                 dma_max_gap_ms=dma.max_gap / SAMPLE_RATE * 1e3, dma_over_64=dma.over_64,
                 underrun_samples=dma.underruns, host_s=time.time() - t)
        result["phases"][label] = p
        log(f"{label}: {steps} steps ({' -> '.join(path)}), {p['folder_reads']} folder reads, {p['emulated_s']:.2f} s; "
            f"longest without the audio routine {p['longest_without_audio_routine_us']:,.0f} us; DMA max gap "
            f"{dma.max_gap} samples ({p['dma_max_gap_ms']:.2f} ms), over 64 {dma.over_64} times, underrun samples "
            f"{dma.underruns} ({p['host_s']:.0f} s host)")

    phase("group", [(1, lambda n: n == "SONG001", 200), (-1, lambda n: n == "DEFAULT", 200)])
    for _ in range(200):  # To SONG001, not measured
        emu.call(select, ui, 1, timeout_s=120)
        se.run_task_manager(emu, a.settle_ms / 1e3)
        if name() == "SONG001":
            break
    phase("files", [(1, None, a.files), (-1, None, a.files)])
    path = os.path.join(a.out, a.name + ".json")
    json.dump(result, open(path, "w"), indent=1)
    log(f"-> {path}")


if __name__ == "__main__":
    main()
