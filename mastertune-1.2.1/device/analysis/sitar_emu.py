#!/usr/bin/env python3
"""The song "New Sitar Grii 10" in the emulator: the song from the card (device/card) at a given tuning, played and measured.

Uses the emulator environment of tests/song (song_emu.py, fat32.py) from the development branch:
- Card image: SONGS/DEFAULT.XML = the song, all samples from card/SAMPLES, CommunityFeatures.XML with masterTune.
  The three missing samples of the kit Hihat (SAMPLES/PsyPack/hihat*.wav, without details in the CSV) are replaced:
  length from endSamplePos in the song, format as the other 11 samples of the kit (stereo, 24 bit, 44.1 kHz),
  content decaying noise.
- --all-kits: the clips of 3L3Ctr0, 014 CR-78 and KIT1 play too (isPlaying="1"); as saved, only
  Hihat, Guiro, 170 Sitar 2 and Oboe are active.
- Measurement as run.sh (--init-sounds, --seed 1): 1 bar of lead-in, then --bars bars (140 BPM), measure() with
  profile_by_function(). Per track also the render time the firmware measures itself (profiler::timingOutputs on,
  profiler::outputTicks[], OS timer 0 = emulated time), like the profiler on the device.

--tasks S: instead of fixed windows the firmware's task manager runs (song_emu.run_task_manager(), the DMA in
  real time), as in tests/sdload (scenario play, Run.run_tasks()): the firmware chooses its block sizes itself,
  direness and culling as on the device, the CPU monitor (cpu_stats) on. First --warmup-s of lead-in, then S seconds
  measured, then --lines-s seconds of SDRAM rows per call (Run.lines()) for the device estimate: instructions at
  400 MHz plus 60 ns (24 instructions) per SDRAM row. --sd-latency CMD_US,SECTOR_US: the card takes time
  (song_emu.SdModel, the waits yield to the task manager). --ipc X: the CPU at X instructions per cycle
  (song_emu.set_instructions_per_cycle(), like tests/sdload s4ipc), then without row counting.

Usage: sitar_emu.py <deluge.elf> <card> <out> --song-dir <tests/song> --build <dir with blockcount.so>
                    [--tenths 4320] [--all-kits] [--culling] [--bars 4] [--window 128]
                    [--tasks S [--warmup-s S] [--lines-s S] [--sd-latency CMD_US,SECTOR_US] [--ipc X]]
"""
import argparse
import collections
import json
import os
import re
import struct
import subprocess
import sys

import numpy as np

SONG_BPM = 140
SONG_BAR = 44100 * 60 * 4 // SONG_BPM  # 75'600 Samples
EXTRA_KITS = ("3L3Ctr0", "014 CR-78", "KIT1")
TYPES = {0: "S", 1: "K", 2: "M", 3: "C", 4: "A"}


def wav24_stereo(frames):
    pcm = (np.clip(frames, -1, 1) * 8388607).astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 44100 * 6, 6, 24) + b"data" + struct.pack("<I", len(pcm)) + pcm)


def card_files(card, tenths, all_kits):
    files = {}
    for dirpath, _, names in os.walk(os.path.join(card, "SAMPLES")):
        for name in names:
            full = os.path.join(dirpath, name)
            files[os.path.relpath(full, card).replace(os.sep, "/")] = open(full, "rb").read()
    xml = open(os.path.join(card, "SONGS", "New Sitar Grii 10.XML"), encoding="utf-8").read()
    rng = np.random.default_rng(1)
    replaced = {}
    for path in re.findall(r'fileName="(SAMPLES/PsyPack/hihat[a-z]*\.wav)"', xml):
        if path in files:
            continue
        zone = xml[xml.find(path):xml.find("</sound>", xml.find(path))]
        length = int(re.search(r'endSamplePos="(\d+)"', zone).group(1))
        t = np.arange(length) / 44100
        frames = rng.standard_normal((length, 2)) * 0.3 * np.exp(-t / (length / 44100 / 4))[:, None]
        files[path] = wav24_stereo(frames)
        replaced[path] = length
    song_names = list(dict.fromkeys(re.findall(r'<sound\b[^>]*?\b(?:presetName|name)="([^"]+)"', xml)))
    if all_kits:
        def activate(m):
            tag = m.group(0)
            name = re.search(r'instrumentPresetName="([^"]*)"', tag)
            if name and name.group(1).strip() in EXTRA_KITS:
                tag = tag.replace('isPlaying="0"', 'isPlaying="1"')
            return tag
        s, e = xml.find("<sessionClips>"), xml.find("</sessionClips>")
        xml = xml[:s] + re.sub(r"<instrumentClip\b[^>]*>", activate, xml[s:e]) + xml[e:]
    files["SONGS/DEFAULT.XML"] = xml.encode()
    files["CommunityFeatures.XML"] = (f'<?xml version="1.0" encoding="UTF-8"?>\n<runtimeFeatureSettings>\n'
                                      f'\t<setting name="masterTune" value="{tenths}" />\n'
                                      f'</runtimeFeatureSettings>\n').encode()
    playing = [re.search(r'instrumentPresetName="([^"]*)"', t).group(1).strip()
               for t in re.findall(r"<instrumentClip\b[^>]*>", xml[xml.find("<sessionClips>"):])
               if 'isPlaying="1"' in t and "instrumentPresetName" in t]
    return files, replaced, song_names, playing


def gdb_offsets(tools, elf, expressions):
    out = subprocess.run([tools + "gdb", "-batch"] + [x for e in expressions for x in ("-ex", f"print {e}")] + [elf],
                         capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (\d+)$", out, re.M)]
    if len(values) != len(expressions):
        raise SystemExit(f"gdb: {expressions}: {out[-400:]}")
    return values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("card")
    ap.add_argument("out")
    ap.add_argument("--song-dir", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--tools")
    ap.add_argument("--tenths", type=int, default=4320)
    ap.add_argument("--all-kits", action="store_true")
    ap.add_argument("--culling", action="store_true")
    ap.add_argument("--bars", type=float, default=4)
    ap.add_argument("--window", type=int, default=128,
                    help="samples per AudioEngine::routine() call (the DMA shows window - 1 free samples); v16 renders "
                         "4-8 a call when idle (patch 0053, found in the emulator)")
    ap.add_argument("--tasks", type=float, default=0, help="seconds with the firmware's task manager")
    ap.add_argument("--warmup-s", type=float, default=SONG_BAR / 44100)
    ap.add_argument("--lines-s", type=float, default=0.1)
    ap.add_argument("--sd-latency", help="CMD_US,SECTOR_US (song_emu.SdModel, the waits yield)")
    ap.add_argument("--ipc", type=float, default=1.0, help="instructions per cycle at 400 MHz (song_emu, tests/sdload)")
    args = ap.parse_args()
    sys.path.insert(0, args.song_dir)
    import fat32  # noqa: E402
    import song_emu as E  # noqa: E402
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(args.out, exist_ok=True)

    def log(s):
        print(s, flush=True)

    if args.ipc != 1:
        E.set_instructions_per_cycle(args.ipc)
    files, replaced, song_names, playing = card_files(args.card, args.tenths, args.all_kits)
    log(f"card: {len(files)} files, replaced {replaced}; clips playing: {', '.join(playing)}")
    image = os.path.join(args.out, "sd.img")
    fat32.build(image, files)
    emu = E.Emulator(args.elf, image, tools, args.build, log)
    # As in tests/retune/retune_emu.py: a time-stretched voice that is also resampled writes to address 0 (a firmware
    # bug); page 0 takes the write here, which is counted and undone after each VoiceSample::render()
    emu.uc.mem_protect(0, 0x1000, E.UC_PROT_ALL)
    null_writes = [0]
    returns = set()

    def at_return(e):
        if e.u32(0):
            null_writes[0] += 1
            e.w32(0, 0)

    def at_render(e):
        lr = e.uc.reg_read(E.UC_ARM_REG_LR) & ~1
        if lr not in returns:
            returns.add(lr)
            e.intercept(lr, at_return)
    E.setup_sd(emu)
    E.init_sounds(emu)
    E.boot(emu)
    emu.w32(emu.sym["jcong"], 1)
    tuned = emu.u32(emu.sym.find("_ZN10MasterTune12_GLOBAL__N_18tenthsHzE"))
    log(f"master tune after boot (CommunityFeatures.XML): {tuned / 10:g} Hz")
    if tuned != args.tenths:
        raise SystemExit("the master tune from CommunityFeatures.XML didn't arrive")
    E.load_startup_song(emu)
    emu.intercept(emu.sym.find("_ZN11VoiceSample6renderE"), at_render)

    # The song's outputs in the order Song::renderAudio() numbers them (profiler::currentOutput)
    first_output, o_next, o_name, o_type, s_mem = gdb_offsets(tools, args.elf, [
        "(int)&((struct Song*)0)->firstOutput", "(int)&((struct Output*)0)->next",
        "(int)&((struct Output*)0)->name", "(int)&((struct Output*)0)->type", "(int)&((struct String*)0)->stringMemory"])
    outputs = []
    o = emu.u32(emu.u32(emu.sym["currentSong"]) + first_output)
    while o:
        p = emu.u32(o + o_name + s_mem)
        name = bytes(emu.uc.mem_read(p, 64)).split(b"\0")[0].decode(errors="replace") if p else ""
        kind = emu.uc.mem_read(o + o_type, 1)[0]
        label = f"{TYPES.get(kind, '?')} {name.strip()}"
        outputs.append(label if label not in outputs else f"{label} ({outputs.count(label) + 1})")
        o = emu.u32(o + o_next)
    emu.uc.mem_write(emu.sym["_ZN8profiler13timingOutputsE"], b"\x01")
    ticks_at, _ = emu.sym.by_name["_ZN8profiler11outputTicksE"]

    def ticks():
        return np.frombuffer(bytes(emu.uc.mem_read(ticks_at, 4 * len(outputs))), "<u4").astype(np.float64)

    if args.tasks:
        measure_tasks(emu, E, args, outputs, ticks, tuned, playing, replaced, null_writes, log)
        os.remove(image)
        return
    player = E.Player(emu, culling=args.culling)
    snaps = []
    play = player.play

    def play_and_snap(samples_wanted, record=None):
        if record is not None:
            snaps.append(ticks())
        play(samples_wanted, record)
        if record is not None:
            snaps.append(ticks())
    player.play = play_and_snap
    if args.window != 128:  # Player.window() sets 127 free samples, then calls routine(): fewer free samples here
        call = emu.call

        def call_with_window(address, *a, **k):
            if address == player.routine and emu.dma_free:
                emu.dma_free = args.window - 1
            return call(address, *a, **k)
        emu.call = call_with_window
    player.start()
    if args.window == 128:
        result = E.measure(emu, player, SONG_BAR / E.BAR, args.bars * SONG_BAR / E.BAR, args.out, log, song_names)
    else:  # measure() takes full windows as 128 samples: here only the totals and the profile
        player.play(SONG_BAR)
        emu.bc.bc_reset_counts()
        windows = []
        player.play(int(args.bars * SONG_BAR), windows)
        samples = sum(w[1] for w in windows)
        per_block = sum(w[0] for w in windows) / samples * 128
        areas = collections.Counter()
        for name, n in E.profile_by_function(emu).items():
            areas[E.area_of(name)] += n / samples * 128
        result = dict(samples=samples, windows=len(windows), mean_window=samples / len(windows),
                      instructions_per_128=per_block, cpu_percent=per_block / E.CYCLES_PER_BLOCK * 100,
                      culls=dict(player.culls), voices=dict(max=max(w[2] for w in windows)),
                      areas={a: dict(per_128=v, cpu_percent=v / E.CYCLES_PER_BLOCK * 100)
                             for a, v in areas.most_common()})
        log(f"measured: {len(windows)} windows of {samples / len(windows):.1f} samples on average, "
            f"{per_block:,.0f} instructions per 128 samples = {result['cpu_percent']:.1f}% CPU")
    instr_per_tick = E.CPU_HZ / E.PERIPHERAL_HZ
    per_128 = (snaps[1] - snaps[0]) * instr_per_tick / result["samples"] * 128
    result["outputs"] = {name: dict(per_128=float(v), cpu_percent=float(v / E.CYCLES_PER_BLOCK * 100),
                                    percent_of_routine=float(v / result["instructions_per_128"] * 100))
                         for name, v in zip(outputs, per_128)}
    result["master_tune_hz"] = tuned / 10
    result["window"] = args.window
    result["clips_playing"] = playing
    result["replaced_samples"] = replaced
    result["null_writes"] = null_writes[0]
    json.dump(result, open(os.path.join(args.out, "result.json"), "w"), indent=1)
    if args.window == 128:
        E.report(result, log)
    log("\nper output (render time as the firmware measures it, OS timer 0; per 128 samples, % CPU, % of routine):")
    for name, v in sorted(result["outputs"].items(), key=lambda kv: -kv[1]["per_128"]):
        log(f"  {name:<16} {v['per_128']:>11,.0f}  {v['cpu_percent']:6.1f}%  {v['percent_of_routine']:5.1f}%")
    five = [n for n in result["outputs"] if n[2:] in ("3L3Ctr0", "Hihat", "Guiro", "014 CR-78", "KIT1")]
    log(f"  the five kits ({len(five)}): {sum(result['outputs'][n]['cpu_percent'] for n in five):.1f}% CPU, "
        f"{sum(result['outputs'][n]['percent_of_routine'] for n in five):.1f}% of routine; null writes {null_writes[0]}")
    os.remove(image)


def measure_tasks(emu, E, args, outputs, ticks, tuned, playing, replaced, null_writes, log):
    """The song played with the firmware's own task manager, measured as tests/sdload's play scenario."""
    sys.path.insert(0, os.path.join(args.song_dir, "..", "sdload"))
    import sdload_emu as SL  # noqa: E402

    class Card:  # No layout of areas here: commands and sectors only
        def summarize(self, entries):
            out = collections.Counter()
            for kind, _, count, _ in entries:
                out[f"{kind}_commands"] += 1
                out[f"{kind}_sectors"] += count
            return dict(out)
    emu.sd_log = []
    run = SL.Run.__new__(SL.Run)
    run.args, run.emu, run.card, run.regions, run.ui_renders, run.result = args, emu, Card(), None, None, {}
    if args.sd_latency:
        E.SdModel(emu, *(float(x) for x in args.sd_latency.split(",")), wait="yield")
    emu.call(emu.sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
    run.run_tasks(args.warmup_s, f"warm-up {args.warmup_s:g} s")
    before = ticks()
    r = run.run_tasks(args.tasks, f"playing {args.tasks:g} s")
    after = ticks()
    a, dma, win = r["audio"], r["dma"], r["cpu_stats"][1:] or r["cpu_stats"]

    def wmax(key, scale=1):
        return max(w[key] for w in win) / scale if win else None
    total = r["instructions"]
    per_track = (after - before) / (r["seconds"] * E.PERIPHERAL_HZ) * 100
    summary = dict(
        master_tune_hz=tuned / 10, clips_playing=playing, replaced_samples=replaced, null_writes=null_writes[0],
        sd_latency=args.sd_latency, ipc=args.ipc, seconds=r["seconds"],
        monitor_percent=r.get("cpu_stats_avg_percent"),
        monitor_peak_percent=wmax("dspPeakPermille", 10),
        render_percent=sum(x[5] for x in r["renders"]) / total * 100,
        routine_percent=a["share_of_time"] * 100,
        device_estimate_percent=None, sdram_lines_per_render=None, sdram_lines_per_empty_call=None,
        samples_per_render=a["samples_per_rendering_call"],
        samples_per_render_most_often=a["samples_per_call_most_often"],
        calls_per_s=a["calls"] / r["seconds"], rendering_calls_per_s=a["rendering_calls"] / r["seconds"],
        voices_max=wmax("voicesMax"), culls=r["culls"], culls_total=sum(r["culls"].values()),
        cull_context=r["cull_context"],
        ql_windows=sum(w["direMax"] > 0 for w in win), windows=len(win), direness_max=wmax("direMax"),
        ql_share_percent=float(np.mean([w["direSharePermille"] for w in win])) / 10 if win else None,
        max_gap_samples=dma["max_gap"], max_gap_ms=dma["max_gap"] / 44.1, reserve_samples=128 - dma["max_gap"],
        monitor_max_gap_ms=wmax("maxGapUs", 1000), over_64=dma["over_64"],
        underrun_samples=dma["underrun_samples"], cluster_loads=r["cluster_loads"], sd=r["sd"],
        tracks_percent_of_time={name: float(v) for name, v in sorted(zip(outputs, per_track), key=lambda kv: -kv[1])},
        worst_gap_events=r.get("worst_gap_events", [])[-14:], cpu_stats=r["cpu_stats"], lines=None)
    json.dump(summary, open(os.path.join(args.out, "tasks.json"), "w"), indent=1, default=str)
    # The lines of SDRAM per call (Run.lines(): memory hooks, which unicorn 2.1.4 sometimes chokes on; saved above)
    if args.lines_s and args.ipc == 1:
        try:
            lines = run.lines(seconds=args.lines_s, internal=False)
        except BaseException as e:  # noqa: B036 (SystemExit from the emulator's error)
            lines = None
            summary["lines_error"] = str(e)
            log(f"lines: {e}")
        if lines:
            empty = a["calls"] - a["rendering_calls"]
            sdram = lines["sdram_lines_mean"] * a["rendering_calls"] + lines["empty_sdram_lines_mean"] * empty
            summary.update(device_estimate_percent=(a["instructions"] + 24 * sdram) / total * 100,
                           sdram_lines_per_render=lines["sdram_lines_mean"],
                           sdram_lines_per_empty_call=lines["empty_sdram_lines_mean"], lines=lines)
        json.dump(summary, open(os.path.join(args.out, "tasks.json"), "w"), indent=1, default=str)
    s = {k: (v if v is not None else float("nan")) for k, v in summary.items()}
    log(f"\nsummary: monitor {s['monitor_percent']:.1f} % (peak {s['monitor_peak_percent']:.1f}), rendering "
        f"{s['render_percent']:.1f} %, routine {s['routine_percent']:.1f} %, device estimate "
        f"{s['device_estimate_percent']:.1f} % ({s['sdram_lines_per_render']:,.0f} SDRAM lines per render); "
        f"{s['samples_per_render']:.1f} samples per render, {s['calls_per_s']:,.0f} calls/s "
        f"({s['rendering_calls_per_s']:,.0f} rendering); voices max {s['voices_max']}, culls {s['culls_total']}, "
        f"QL in {s['ql_windows']}/{s['windows']} windows ({s['ql_share_percent']:.0f} % of the time, direness max "
        f"{s['direness_max']}); max gap {s['max_gap_samples']} samples = {s['max_gap_ms']:.2f} ms (monitor "
        f"{s['monitor_max_gap_ms']:.2f} ms), reserve {s['reserve_samples']}, underrun samples {s['underrun_samples']}")
    log("tracks (% of the time): " + ", ".join(f"{k} {v:.1f}" for k, v in s["tracks_percent_of_time"].items()
                                               if v >= 0.05))


if __name__ == "__main__":
    main()
