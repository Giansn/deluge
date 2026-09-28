#!/usr/bin/env python3
"""Share of emulated time in the audio task, with the firmware's own task manager (DMA in real time), identical for
stock 1.2.1 and mastertune builds. Based on device/analysis/sitar_emu.py measure_tasks() and tests/sdload Run.run_tasks().

Differences to sitar_emu.py --tasks, so that stock 1.2.1 runs the same way:
- no CPU monitor (cpu_stats does not exist in 1.2.1; it stays off in the mastertune build too), no profiler per track,
- master tune: CommunityFeatures.XML says 440.0 Hz (4400), so the mastertune build plays at the same pitch as stock.

Usage: tasks_emu.py <elf> <out dir> (--sitar [--all-kits] | --image <sd.img from make_sd.py>) [--ipc X]
                    [--sd-latency CMD_US,SECTOR_US] [--warmup-s S] [--seconds S]
"""
import argparse
import collections
import json
import os
import re
import sys

MT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SONG = os.path.join(MT, "tests/song")
sys.path.insert(0, SONG)
sys.path.insert(0, os.path.join(MT, "tests/sdload"))
sys.path.insert(0, os.path.join(MT, "device/analysis"))
import fat32  # noqa: E402
import sdload_emu as SL  # noqa: E402
import sitar_emu  # noqa: E402
import song_emu as E  # noqa: E402


class NoCpuStats:
    def __init__(self, emu, mode=1):
        pass

    def close(self):
        pass

    def summaries(self):
        return []


E.CpuStats = NoCpuStats


class Card:  # No layout of areas: commands and sectors only
    def summarize(self, entries):
        out = collections.Counter()
        for kind, _, count, _ in entries:
            out[f"{kind}_commands"] += 1
            out[f"{kind}_sectors"] += count
        return dict(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("out")
    ap.add_argument("--sitar", action="store_true")
    ap.add_argument("--all-kits", action="store_true")
    ap.add_argument("--image")
    ap.add_argument("--ipc", type=float, default=1.0)
    ap.add_argument("--sd-latency", default="1000,42.67")
    ap.add_argument("--warmup-s", type=float, default=sitar_emu.SONG_BAR / 44100)
    ap.add_argument("--seconds", type=float, default=4 * sitar_emu.SONG_BAR / 44100)
    ap.add_argument("--build", required=True)
    ap.add_argument("--fixed", action="store_true", help="fixed 128-sample windows (song_emu.measure(), no culling), 1 bar warm-up, 4 bars")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    tools = os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                         "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    log = SL.log
    if args.ipc != 1:
        E.set_instructions_per_cycle(args.ipc)
    info = dict(elf=args.elf, ipc=args.ipc, sd_latency=args.sd_latency)
    if args.sitar:
        files, replaced, _, playing = sitar_emu.card_files(os.path.join(MT, "device/card"), 4400, args.all_kits)
        info.update(song="New Sitar Grii 10", all_kits=args.all_kits, clips_playing=playing)
        image = os.path.join(args.out, "sd.img")
        fat32.build(image, files)
        log(f"card: {len(files)} files, replaced {replaced}; clips playing: {', '.join(playing)}")
    else:
        image = args.image
        info.update(song=os.path.basename(image))
    emu = E.Emulator(args.elf, image, tools, args.build, log)
    # As sitar_emu.py: a time-stretched voice that is also resampled writes to address 0 (a firmware bug); page 0 takes
    # the write, which is counted and undone after each VoiceSample::render()
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
    mt = [v for k, v in emu.sym.by_name.items() if k.startswith("_ZN10MasterTune12_GLOBAL__N_18tenthsHzE")]
    info["master_tune_hz"] = emu.u32(mt[0][0]) / 10 if mt else None
    log(f"master tune after boot: {info['master_tune_hz']}")
    E.load_startup_song(emu)
    emu.intercept(emu.sym.find("_ZN11VoiceSample6renderE"), at_render)

    if args.fixed:
        xml = fat32.read_file(image, "SONGS/DEFAULT.XML").decode(errors="replace")
        song_names = list(dict.fromkeys(re.findall(r'<sound\b[^>]*?\b(?:presetName|name)="([^"]+)"', xml)))
        player = E.Player(emu, culling=False)
        player.start()
        bar = sitar_emu.SONG_BAR if args.sitar else E.BAR
        result = E.measure(emu, player, bar / E.BAR, 4 * bar / E.BAR, args.out, log, song_names)
        result.update(info, null_writes=null_writes[0])
        json.dump(result, open(os.path.join(args.out, "result.json"), "w"), indent=1, default=str)
        E.report(result, log)
        if args.sitar:
            os.remove(image)
        return

    emu.sd_log = []
    run = SL.Run.__new__(SL.Run)
    run.args, run.emu, run.card, run.regions, run.ui_renders, run.result = args, emu, Card(), None, None, {}
    if args.sd_latency:
        E.SdModel(emu, *(float(x) for x in args.sd_latency.split(",")), wait="yield")
    emu.call(emu.sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
    run.run_tasks(args.warmup_s, f"warm-up {args.warmup_s:g} s")
    r = run.run_tasks(args.seconds, f"playing {args.seconds:g} s")
    a, at, dma = r["audio"], r["audio_task"], r["dma"]
    total = r["instructions"]
    tasks = r["tasks"]
    audio_task = tasks.get(SL.AUDIO_TASK, {})
    renders = r["renders"]
    summary = dict(info, seconds=r["seconds"], host_s=r["host_s"], null_writes=null_writes[0],
                   routine_share=a["share_of_time"], render_share=sum(x[5] for x in renders) / total,
                   empty_call_share=a["empty_call_instructions"] / total,
                   audio_task_inclusive=audio_task.get("inclusive"), audio_task_exclusive=audio_task.get("exclusive"),
                   instructions_per_sample=a["instructions"] / max(a["samples"], 1),
                   render_instructions_per_sample=sum(x[5] for x in renders) / max(a["samples"], 1),
                   samples=a["samples"], samples_per_render=a["samples_per_rendering_call"],
                   samples_most_often=a["samples_per_call_most_often"], calls_per_s=a["calls"] / r["seconds"],
                   rendering_calls_per_s=a["rendering_calls"] / r["seconds"],
                   direness_max=max((x[3] for x in renders), default=0),
                   direness_calls=sum(1 for x in renders if x[3] > 0),
                   culls=r["culls"], culls_total=sum(r["culls"].values()), cull_context=r["cull_context"],
                   max_gap=dma["max_gap"], over_64=dma["over_64"], underrun_samples=dma["underrun_samples"],
                   samples_played=dma["samples_played"], other_tasks_share=at["other_tasks_share"],
                   scheduler_share=at["scheduler_share"], tasks=tasks, sd=r["sd"],
                   worst_gap_events=r.get("worst_gap_events", [])[-14:])
    json.dump(summary, open(os.path.join(args.out, "tasks.json"), "w"), indent=1, default=str)
    json.dump(renders, open(os.path.join(args.out, "renders.json"), "w"))
    s = summary
    log(f"\nsummary: routine {s['routine_share'] * 100:.1f} % of the time (rendering calls "
        f"{s['render_share'] * 100:.1f} %, empty calls {s['empty_call_share'] * 100:.1f} %), audio task inclusive "
        f"{(s['audio_task_inclusive'] or 0) * 100:.1f} %; {s['instructions_per_sample']:.0f} instructions per sample "
        f"({s['instructions_per_sample'] * 128:,.0f} per 128); {s['samples_per_render']:.1f} samples per render, "
        f"{s['calls_per_s']:,.0f} calls/s ({s['rendering_calls_per_s']:,.0f} rendering); culls {s['culls_total']}, "
        f"direness max {s['direness_max']} in {s['direness_calls']} renders; max gap {s['max_gap']}, underrun samples "
        f"{s['underrun_samples']}; null writes {s['null_writes']}")
    if args.sitar:
        os.remove(image)


if __name__ == "__main__":
    main()
