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
    ap.add_argument("--no-play", action="store_true")
    ap.add_argument("--trace", action="store_true", help="per step: the file, folder reads, the window")
    ap.add_argument("--where", action="store_true", help="what ran in the longest stretches without the audio routine")
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
    entries = []
    if a.where:
        for f in ("_ZN7Browser13sortFileItemsEv", "_ZN7Browser29deleteFolderAndDuplicateItems",
                  "_ZN7Browser22readFileItemsForFolder", "_ZN7Browser14emptyFileItemsEv", "_ZN7Browser19selectEncoderAction",
                  "_ZN10LoadSongUI10readWindow", "_ZN10LoadSongUI15drawSongPreview", "_ZN7Browser19deleteSomeFileItems",
                  "_ZN10LoadSongUI17fillWindowForRows", "_ZN10LoadSongUI10renderOLED", "_ZN7Browser14getNewFileItem",
                  "_ZN7Browser32readFileItemsFromFolderAndMemory", "sd_read_sect", "_ZN12CStringArray19quickSortForStrings",
                  "f_readdir_get_filepointer", "_ZN10LoadSongUI19selectEncoderAction"):
            try:
                addr = sym.find(f)
            except KeyError:
                log(f"no {f}")
                continue
            emu.intercept(addr, lambda e, f=f: entries.append((e.now(), f)) and None)
    select = sym["_ZN10LoadSongUI19selectEncoderActionEa"]

    if not a.no_play:
        emu.call(sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
        se.run_task_manager(emu, 0.5)
    window = [sym[n] for n in ("_ZN7Browser26numFileItemsDeletedAtStartE", "_ZN7Browser24numFileItemsDeletedAtEndE",
                               "_ZN7Browser17fileIndexSelectedE", "_ZN7Browser17scrollPosVerticalE",
                               "_ZN7Browser9fileItemsE")]
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
                rs = reads[0]
                emu.call(select, ui, offset, timeout_s=120)
                steps += 1
                se.run_task_manager(emu, a.settle_ms / 1e3)
                if a.trace:
                    v = [emu.u32(x) for x in window]
                    log(f"  {offset:+d} {name()}: {reads[0] - rs} reads; before {v[0]}, after {v[1]}, selected {v[2]}, "
                        f"top {v[3]}, items {emu.u32(window[4] + 16)}")
                if stop and stop(name()):
                    break
            path.append(name())
        dma.close()
        calls = sorted(run.routine_calls, key=lambda c: c[3])
        longest = max((b[3] - c[4] for c, b in zip(calls, calls[1:])), default=0)
        if a.where:
            import bisect
            import collections
            times = [x[0] for x in entries]
            for gap, c, b in sorted(((b[3] - c[4], c, b) for c, b in zip(calls, calls[1:])), key=lambda g: -g[0])[:4]:
                lo, hi = bisect.bisect_left(times, c[4]), bisect.bisect_right(times, b[3])
                last = entries[lo - 1][1] if lo else None
                inside = collections.Counter(f for _, f in entries[lo:hi])
                log(f"    stretch {gap / sdl.hz() * 1e6:,.0f} us: last entered before it {last}; inside {dict(inside)}")
            entries.clear()
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
