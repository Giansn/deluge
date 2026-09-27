#!/usr/bin/env python3
"""A big SD card and a big project on the real firmware in the emulator (tests/song's harness): what grows with the card,
the number of files, long names, big samples and the card's own speed. One scenario per call (run.sh runs them all,
one emulator at a time); images from make_bigsd.py (their layout in <image>.json tells the areas apart).

Usage: sdload_emu.py <deluge.elf> <image> <out dir> <scenario> [--seconds S] [--sd-latency CMD_US,SECTOR_US]
                     [--lines] [--tools PREFIX] [--build DIR]
Scenarios:
  load   boot (the card mounted), SONGS/DEFAULT.XML loaded as at startup (setupStartupSong()), then optionally
         --save (SONGS/SAVETEST.XML written as SaveSongUI does: the first cluster of a new file is where FatFS
         searches the FAT for free space, create_chain()) and/or --idle S (below). Per phase: instructions, SD
         commands (sd_read_sect()/disk_write() calls) and sectors by area (FAT, directories, data), estimated real
         time with the card profiles (CPU at 400 MHz, 1 instruction per cycle, plus command and sector times). Per
         sample opened (AudioFileManager::getAudioFileFromFilename()): directory, FAT and data sectors. RAM (the
         allocator's regions) after loading.
  --idle S  after loading, the song stopped, S seconds of emulated time with the firmware's own task manager
         (song_emu.run_task_manager(): every registered task at its own interval, the SSI's DMA in real time) and
         the CPU monitor on (cpu_stats, as Settings > CPU monitor: its half-second windows and what it shows,
         computed by the firmware's cpu_stats::summarize()). Per task: calls and instructions; the audio routine's
         calls, the samples each renders and its instructions (CPU % at 1 instruction per cycle). Cluster loads while
         idle. --lines: then, for 0.02 s more, the distinct 32-byte lines (the Cortex-A9's L1 line) of SDRAM and
         internal RAM each audio routine call touches (data only), against the L1 data cache's 1,024 lines.
  play   loads, then plays (PlaybackHandler::playButtonPressed()) for --seconds with the task manager as for --idle,
         the card instant or, with --sd-latency, taking time (song_emu.SdModel: the firmware waits for each command in
         its routineForSD() loop, which runs the audio routine meanwhile). Cluster loads and their duration, audio
         routine calls from the task manager and from routineForSD(), gaps (song_emu.RealTimeDma: 128 samples or more
         = an underrun), culled voices by type, cpu_stats as shown (QL: direness above 0, VC: voices culled).
  browse loads, opens the song browser (openUI(&loadSongUI)) and turns the select encoder one step at a time
         (LoadSongUI::selectEncoderAction(+1)) through the whole SONGS folder: per step instructions, commands and
         sectors, and how often the browser reads the whole folder again (Browser::readFileItemsFromFolderAndMemory()).
Results: <out>/<scenario>.json and a summary on stdout. The display is the OLED (as on the user's Deluge).
"""
import argparse
import bisect
import collections
import json
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../song"))
import song_emu as se  # noqa: E402
from song_emu import CPU_HZ, SAMPLE_RATE, STOP  # noqa: E402
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1  # noqa: E402

# Card profiles for the estimates: (µs per command, µs per 512-byte sector)
PROFILES = {"typical (1 ms/command, 12 MB/s)": (1000.0, 512 / 12e6 * 1e6),
            "fast (0.25 ms/command, 25 MB/s)": (250.0, 512 / 25e6 * 1e6)}
INSTR_PER_SAMPLE = CPU_HZ / SAMPLE_RATE  # 9,070: 100 % CPU at 1 instruction per cycle


def log(s):
    print(s, flush=True)


class Card:
    """The image's layout (make_bigsd.py's .json): which area a sector is in."""

    def __init__(self, image):
        self.layout = json.load(open(image + ".json"))
        lay = self.layout
        self.fat_start, self.data_start, self.csize = lay["fat_start"], lay["data_start"], lay["csize"]
        runs = sorted((first, first + n) for chain in lay["dirs"].values() for first, n in chain)
        self.dir_starts = [a for a, _ in runs]
        self.dir_ends = [b for _, b in runs]

    def area(self, sector):
        if sector < self.fat_start:
            return "boot/fsinfo"
        if sector < self.data_start:
            return "fat"
        c = (sector - self.data_start) // self.csize + 2
        i = bisect.bisect_right(self.dir_starts, c) - 1
        return "dir" if i >= 0 and c < self.dir_ends[i] else "data"

    def summarize(self, entries):
        """Commands and sectors by kind and area of a slice of emu.sd_log."""
        out = collections.Counter()
        for kind, sector, count, _ in entries:
            a = self.area(sector)
            out[f"{kind}_commands"] += 1
            out[f"{kind}_sectors"] += count
            out[f"{kind}_{a}_commands"] += 1
            out[f"{kind}_{a}_sectors"] += count
        return dict(out)


def estimate(instructions, sd):
    """Seconds on the Deluge: the CPU work at 400 MHz plus the card's time for these commands and sectors."""
    commands = sd.get("r_commands", 0) + sd.get("w_commands", 0)
    sectors = sd.get("r_sectors", 0) + sd.get("w_sectors", 0)
    return {name: round(instructions / CPU_HZ + (commands * c + sectors * s) / 1e6, 3)
            for name, (c, s) in PROFILES.items()}


class Run:
    def __init__(self, args):
        self.args = args
        tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                           "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
        self.card = Card(args.image)
        self.emu = emu = se.Emulator(args.elf, args.image, tools, args.build, log)
        emu.sd_log = []
        se.setup_sd(emu)
        self.result = dict(scenario=args.scenario, image=os.path.basename(args.image), card=self.card.layout["notes"],
                           card_clusters=self.card.layout["num_clusters"], used_clusters=self.card.layout["used_clusters"],
                           phases={}, profiles=PROFILES)
        self.phase("boot (mount)", lambda: se.boot(emu))
        emu.call(emu.sym["_ZN6deluge3hid7display15swapDisplayTypeEv"])  # The OLED
        self.opens = []
        regions = se.Regions(emu, [("_ZN16AudioFileManager24getAudioFileFromFilename", "open")], "other")
        regions.on_enter["open"] = lambda e, back: len(e.sd_log)
        regions.on_exit["open"] = lambda e, at, n: self.opens.append((n, self.card.summarize(e.sd_log[at:])))
        emu.uc.ctl_flush_tb()
        t = time.time()
        self.phase("song load (setupStartupSong())", lambda: se.load_startup_song(emu))
        self.result["load_host_s"] = time.time() - t
        self.result["opens"] = self.open_stats()
        self.result["ram_after_load"] = se.ram_usage(emu)

    def phase(self, name, fn):
        emu = self.emu
        i0, n0 = emu.now(), len(emu.sd_log)
        fn()
        instructions = emu.now() - i0
        sd = self.card.summarize(emu.sd_log[n0:])
        p = dict(instructions=instructions, cpu_s=instructions / CPU_HZ, sd=sd, estimated_s=estimate(instructions, sd))
        self.result["phases"][name] = p
        log(f"{name}: {instructions / 1e6:,.1f}M instructions ({instructions / CPU_HZ:.3f} s CPU), reads "
            f"{sd.get('r_commands', 0):,} commands / {sd.get('r_sectors', 0):,} sectors (FAT "
            f"{sd.get('r_fat_sectors', 0):,}, directories {sd.get('r_dir_sectors', 0):,}, data "
            f"{sd.get('r_data_sectors', 0):,}), writes {sd.get('w_commands', 0):,} / {sd.get('w_sectors', 0):,}; "
            f"estimated " + ", ".join(f"{v:.2f} s {k.split()[0]}" for k, v in p["estimated_s"].items()))
        return p

    def open_stats(self):
        if not self.opens:
            return None
        keys = ["r_dir_sectors", "r_fat_sectors", "r_data_sectors", "r_commands"]
        arr = {k: np.array([o[1].get(k, 0) for o in self.opens]) for k in keys}
        ins = np.array([o[0] for o in self.opens])
        tot = collections.Counter()
        for o in self.opens:
            tot.update(o[1])
        out = dict(count=len(self.opens), instructions_mean=float(ins.mean()),
                   per_open={k: dict(mean=float(v.mean()), max=int(v.max()), total=int(v.sum())) for k, v in arr.items()},
                   estimated_total_s=estimate(int(ins.sum()), dict(tot)))
        log(f"samples opened: {len(self.opens)}, per open: directory sectors mean {arr['r_dir_sectors'].mean():.0f} "
            f"(max {arr['r_dir_sectors'].max()}), FAT sectors mean {arr['r_fat_sectors'].mean():.1f} (max "
            f"{arr['r_fat_sectors'].max()}), commands mean {arr['r_commands'].mean():.0f}, instructions mean "
            f"{ins.mean() / 1e3:,.0f}k; all opens estimated " + ", ".join(
                f"{v:.2f} s {k.split()[0]}" for k, v in out["estimated_total_s"].items()))
        return out

    # --- saving

    def save(self):
        emu, sym = self.emu, self.emu.sym
        strings, at = {}, STOP + 0x100
        for name, text in (("path", se.SAVE_PATH), ("begin", '<?xml version="1.0" encoding="UTF-8"?>\n<song\n'),
                           ("end", "\n</song>\n")):
            emu.uc.mem_write(at, text.encode() + b"\0")
            strings[name] = at
            at += len(text) + 4 & ~3
        storage, serializer, song = sym["storageManager"], sym["smSerializer"], emu.u32(sym["currentSong"])

        def run():
            for name, address, arguments, check in (
                    ("createXMLFile", sym.find("_ZN14StorageManager13createXMLFile"),
                     (storage, strings["path"], 1, 0), True),
                    ("Song::writeToFile", sym["_ZN4Song11writeToFileER14StorageManager"], (song, storage), False),
                    ("closeFileAfterWriting", sym.find("_ZN13XMLSerializer21closeFileAfterWriting"),
                     (serializer, strings["path"], strings["begin"], strings["end"]), True)):
                error = emu.call(address, *arguments, timeout_s=60)
                if check and error:
                    raise SystemExit(f"{name}: error {error}")
        p = self.phase("save SONGS/SAVETEST.XML", run)
        p["xml_bytes"] = len(se.fat32.read_file(emu.sd_path, se.SAVE_PATH))

    # --- the task manager (idle, play)

    def task_accounting(self):
        """Per task (TaskManager::runTask()): calls and instructions (inclusive); audio routine calls by caller."""
        emu, sym = self.emu, self.emu.sym
        name_off, = se.gdb_values(emu, ["(int)&((Task*)0)->name"])
        task_size, = se.gdb_values(emu, ["sizeof(Task)"])
        base = sym["taskManager"]
        self.tasks = collections.defaultdict(lambda: [0, 0])
        self.routine_calls = []  # (caller, instructions, samples)
        timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
        rfs = sym["routineForSD"] & ~1
        rfs_end = rfs + sym.by_name["routineForSD"][1]

        def task_name(i):
            p = emu.u32(base + i * task_size + name_off)
            s = emu.ram(p, 40) if p else None
            return s.split(b"\0")[0].decode(errors="replace") if s else f"task {i}"

        self.task_name = task_name
        regions = se.Regions(emu, [("_ZN11TaskManager7runTaskEa", "task"), ("_ZN11AudioEngine7routineEv", "audio")],
                             "scheduler, idle loop")

        def task_enter(e, back):
            return struct.unpack("<b", bytes([e.uc.reg_read(UC_ARM_REG_R0) & 0xFF]))[0]

        def task_exit(e, tid, n):
            self.tasks[tid][0] += 1
            self.tasks[tid][1] += n

        def audio_enter(e, back):
            caller = "routineForSD" if rfs <= back < rfs_end else "task" if any(
                s[0] == "task" for s in regions.stack) else "other"
            return caller, e.u32(timer)

        def audio_exit(e, extra, n):
            self.routine_calls.append((extra[0], n, (e.u32(timer) - extra[1]) & 0xFFFFFFFF))

        regions.on_enter.update(task=task_enter, audio=audio_enter)
        regions.on_exit.update(task=task_exit, audio=audio_exit)
        self.regions = regions
        self.culls = collections.Counter()

        def on_cull(e):
            kind = e.uc.reg_read(UC_ARM_REG_R1)
            self.culls[se.CULL_TYPES[kind] if kind < len(se.CULL_TYPES) else str(kind)] += 1
        emu.intercept(sym.find("_ZN11AudioEngine9cullVoiceE"), on_cull)
        self.cluster_loads = 0

        def on_load(e):
            self.cluster_loads += 1
        emu.intercept(sym.find("_ZN16AudioFileManager11loadClusterE"), on_load)
        emu.uc.ctl_flush_tb()

    def run_tasks(self, seconds, label):
        emu = self.emu
        cpu = se.CpuStats(emu)
        dma = se.RealTimeDma(emu)
        self.task_accounting()
        i0, n0, t = emu.now(), len(emu.sd_log), time.time()
        regions = self.regions
        regions.last = emu.bc.bc_total()
        regions.time.clear()
        se.run_task_manager(emu, seconds)
        dma.collect()
        regions.charge()
        total = emu.now() - i0
        sd = self.card.summarize(emu.sd_log[n0:])
        calls = self.routine_calls
        rendering = [c for c in calls if c[2]]
        audio_instr = sum(c[1] for c in calls)
        samples = sum(c[2] for c in calls)
        by_caller = collections.Counter(c[0] for c in rendering)
        tasks = {self.task_name(tid): dict(calls=v[0], instructions=v[1], share=v[1] / total)
                 for tid, v in sorted(self.tasks.items(), key=lambda kv: -kv[1][1])}
        windows = cpu.summaries()
        r = dict(label=label, seconds=total / CPU_HZ, host_s=time.time() - t, instructions=total, sd=sd,
                 audio=dict(calls=len(calls), rendering_calls=len(rendering), samples=samples,
                            samples_per_rendering_call=samples / max(len(rendering), 1),
                            instructions=audio_instr, instructions_per_call=audio_instr / max(len(calls), 1),
                            instructions_per_rendering_call=sum(c[1] for c in rendering) / max(len(rendering), 1),
                            empty_call_instructions=sum(c[1] for c in calls if not c[2]),
                            cpu_percent_1ipc=audio_instr / max(samples, 1) / INSTR_PER_SAMPLE * 100,
                            rendering_calls_by_caller=dict(by_caller),
                            window_sizes=dict(collections.Counter(c[2] for c in rendering).most_common(8))),
                 tasks=tasks, exclusive=dict(regions.time), culls=dict(self.culls), cluster_loads=self.cluster_loads,
                 dma=dict(max_gap=dma.max_gap, underrun_samples=dma.underruns, samples_played=int(dma.position() - dma.start_position)),
                 cpu_stats=windows)
        if emu.sd_model:
            waits = [w for w in emu.sd_model.waits if w[0] >= i0]
            d = np.array([w[1] for w in waits]) / CPU_HZ * 1e3 if waits else np.zeros(1)
            r["sd_waits"] = dict(count=len(waits), ms_mean=float(d.mean()), ms_max=float(d.max()),
                                 ms_total=float(d.sum()), routine_for_sd_calls=sum(w[4] for w in waits),
                                 model=emu.sd_model.params)
        emu.dma = None
        a = r["audio"]
        log(f"{label}: {r['seconds']:.2f} s emulated ({r['host_s']:.0f} s host), {total / 1e6:,.0f}M instructions")
        log(f"  audio routine: {a['calls']:,} calls, {a['rendering_calls']:,} rendering ({a['samples_per_rendering_call']:.1f}"
            f" samples each; by caller {a['rendering_calls_by_caller']}), {a['instructions_per_rendering_call']:,.0f} "
            f"instructions per rendering call, {a['empty_call_instructions'] / max(a['calls'] - a['rendering_calls'], 1):,.0f}"
            f" per empty call; CPU {a['cpu_percent_1ipc']:.1f} % at 1 instruction per cycle (time in routine() / audio"
            f" rendered, as cpu_stats measures)")
        log("  tasks (inclusive): " + ", ".join(f"{k} {v['share'] * 100:.1f}% ({v['calls']:,})" for k, v in
                                                list(tasks.items())[:9]))
        log(f"  cluster loads {self.cluster_loads}, SD reads {sd.get('r_commands', 0)} commands / {sd.get('r_sectors', 0)} "
            f"sectors, culls {dict(self.culls)}, DMA max gap {dma.max_gap} samples, underrun samples {dma.underruns}")
        if "sd_waits" in r:
            w = r["sd_waits"]
            log(f"  card waits: {w['count']} commands, mean {w['ms_mean']:.2f} ms, max {w['ms_max']:.2f} ms, total "
                f"{w['ms_total']:.0f} ms, routineForSD() calls meanwhile {w['routine_for_sd_calls']:,}")
        for w in windows:
            log(f"  cpu_stats {w['at_s']:6.2f} s: CPU {w['dspAvgPermille'] / 10:.1f}% avg / {w['dspPeakPermille'] / 10:.1f}% "
                f"peak, voices {w['voicesNow']}/{w['voicesMax']}, direness max {w['direMax']} ({w['direSharePermille'] / 10:.1f}%"
                f" of the audio), culled {w['culled']}, SD loads {w['sdLoads']} avg {w['sdAvgUs']} us (card "
                f"{w['sdCardAvgUs']} us) max {w['sdMaxUs']} us, max gap {w['maxGapUs']} us")
        return r

    def lines(self, seconds=0.02):
        """Distinct 32-byte lines of SDRAM and internal RAM touched by each audio routine call (data accesses)."""
        emu = self.emu
        dma = se.RealTimeDma(emu)
        cur = [None]
        per_call = []
        union = [set(), set()]

        def on_mem(uc, access, address, size, value, _):
            c = cur[0]
            if c is not None:
                c[0 if address < 0x20000000 or address >= 0x40000000 else 1].add(address >> 5)

        hooks = [emu.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, on_mem, begin=b, end=b + size - 1)
                 for b, size in ((se.SDRAM, se.SDRAM_SIZE), (se.SDRAM + se.UNCACHED_MIRROR_OFFSET, se.SDRAM_SIZE),
                                 (se.INTERNAL_RAM, se.INTERNAL_RAM_SIZE))]
        timer = emu.sym["_ZN11AudioEngine16audioSampleTimerE"]
        routine = emu.sym["_ZN11AudioEngine7routineEv"]
        depth = [0, 0]
        regions = se.Regions(emu, [("_ZN11AudioEngine7routineEv", "lines")], "other")

        def enter(e, back):
            depth[0] += 1
            if depth[0] == 1:
                cur[0] = (set(), set())
                depth[1] = e.u32(timer)
            return None

        def leave(e, extra, n):
            depth[0] -= 1
            if depth[0] == 0 and cur[0] is not None:
                samples = (e.u32(timer) - depth[1]) & 0xFFFFFFFF
                if samples:
                    per_call.append((len(cur[0][0]), len(cur[0][1]), samples, n))
                    union[0].update(cur[0][0])
                    union[1].update(cur[0][1])
                cur[0] = None
        regions.on_enter["lines"] = enter
        regions.on_exit["lines"] = leave
        emu.uc.ctl_flush_tb()
        se.run_task_manager(emu, seconds)
        for h in hooks:
            emu.uc.hook_del(h)
        emu.dma = None
        if not per_call:
            return None
        a = np.array(per_call, dtype=float)
        r = dict(calls=len(per_call), sdram_lines_mean=float(a[:, 0].mean()), sdram_lines_max=int(a[:, 0].max()),
                 internal_lines_mean=float(a[:, 1].mean()), samples_mean=float(a[:, 2].mean()),
                 instructions_mean=float(a[:, 3].mean()), sdram_lines_all_calls=len(union[0]),
                 internal_lines_all_calls=len(union[1]), l1_data_lines=1024)
        log(f"lines touched per rendering audio routine call ({r['calls']} calls, {r['samples_mean']:.1f} samples, "
            f"{r['instructions_mean']:,.0f} instructions each): SDRAM {r['sdram_lines_mean']:,.0f} (max "
            f"{r['sdram_lines_max']:,}), internal RAM {r['internal_lines_mean']:,.0f}; all calls together SDRAM "
            f"{r['sdram_lines_all_calls']:,}, internal {r['internal_lines_all_calls']:,} (L1 data cache: 1,024 lines "
            f"of 32 bytes)")
        return r

    # --- the song browser

    def browse(self, steps):
        emu, sym = self.emu, self.emu.sym
        reads = [0]

        def on_read(e):
            reads[0] += 1
        emu.intercept(sym.find("_ZN7Browser32readFileItemsFromFolderAndMemory"), on_read)
        emu.uc.ctl_flush_tb()
        ui = sym["loadSongUI"]
        opened = self.phase("song browser opened (openUI(&loadSongUI))",
                            lambda: emu.call(sym["_Z6openUIP2UI"], ui, timeout_s=60))
        opened["folder_reads"] = reads[0]
        mode = emu.u32(sym["currentUIMode"])
        opened["ui_mode_after_open"] = mode
        if mode:  # What the startup song's loading left (the UI's own flow isn't run here): the browser needs NONE
            log(f"currentUIMode {mode:#x} after opening, set to UI_MODE_NONE for the select encoder")
            emu.w32(sym["currentUIMode"], 0)
        per_step = []
        select = sym["_ZN10LoadSongUI19selectEncoderActionEa"]
        selected = sym["_ZN7Browser17fileIndexSelectedE"]
        deleted = sym["_ZN7Browser26numFileItemsDeletedAtStartE"]
        mode_resets = 0
        t = time.time()
        for i in range(steps):
            se.drain_uarts(emu)
            if emu.u32(sym["currentUIMode"]):
                emu.w32(sym["currentUIMode"], 0)
                mode_resets += 1
            r0, i0, n0 = reads[0], emu.now(), len(emu.sd_log)
            emu.call(select, ui, 1, timeout_s=60)
            sd = self.card.summarize(emu.sd_log[n0:])
            per_step.append((emu.now() - i0, sd.get("r_commands", 0), sd.get("r_sectors", 0), reads[0] - r0,
                             struct.unpack("<i", emu.uc.mem_read(selected, 4))[0] + emu.u32(deleted)))
        a = np.array([p[:4] for p in per_step], dtype=float)
        rereads = a[:, 3] > 0
        est = [estimate(int(p[0]), {"r_commands": p[1], "r_sectors": p[2]}) for p in per_step]
        key = list(PROFILES)[0]
        e = np.array([x[key] for x in est])
        r = dict(steps=steps, host_s=time.time() - t, folder_reads=int(a[:, 3].sum()),
                 steps_with_reread=int(rereads.sum()),
                 per_step_instructions=dict(median=float(np.median(a[:, 0])), max=float(a[:, 0].max())),
                 per_reread=dict(instructions=float(a[rereads, 0].mean()) if rereads.any() else 0,
                                 commands=float(a[rereads, 1].mean()) if rereads.any() else 0,
                                 sectors=float(a[rereads, 2].mean()) if rereads.any() else 0),
                 total=dict(instructions=float(a[:, 0].sum()), commands=int(a[:, 1].sum()), sectors=int(a[:, 2].sum())),
                 estimated_total_s=estimate(int(a[:, 0].sum()), {"r_commands": int(a[:, 1].sum()),
                                                                  "r_sectors": int(a[:, 2].sum())}),
                 estimated_step_ms=dict(median=float(np.median(e)) * 1e3, max=float(e.max()) * 1e3,
                                        reread_mean=float(e[rereads].mean()) * 1e3 if rereads.any() else 0),
                 selected_index_trace=[p[4] for p in per_step[::50]], ui_mode_resets=mode_resets)
        log(f"browsing: {steps} steps ({r['host_s']:.0f} s host): the folder read again {r['folder_reads']} times "
            f"({r['steps_with_reread']} steps); a step without: {r['per_step_instructions']['median'] / 1e3:,.0f}k "
            f"instructions; with: {r['per_reread']['instructions'] / 1e6:,.1f}M instructions, "
            f"{r['per_reread']['commands']:,.0f} commands, {r['per_reread']['sectors']:,.0f} sectors, estimated "
            f"{r['estimated_step_ms']['reread_mean']:.0f} ms ({key.split()[0]}); whole scroll "
            + ", ".join(f"{v:.1f} s {k.split()[0]}" for k, v in r["estimated_total_s"].items()))
        return r


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("scenario", choices=["load", "play", "browse"])
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--idle", type=float, default=0)
    ap.add_argument("--lines", action="store_true")
    ap.add_argument("--seconds", type=float, default=2)
    ap.add_argument("--steps", type=int, default=1250)
    ap.add_argument("--sd-latency", help="CMD_US,SECTOR_US[,POLL_US] (song_emu.SdModel), for play")
    ap.add_argument("--name", help="result file name (default: the scenario)")
    ap.add_argument("--tools")
    ap.add_argument("--build", default=HERE)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    run = Run(args)
    res = run.result
    if args.save:
        run.save()
    if args.idle:
        res["idle"] = run.run_tasks(args.idle, f"idle, song stopped, {args.idle:g} s with the task manager")
        if args.lines:
            res["lines"] = run.lines()
    if args.scenario == "play":
        if args.sd_latency:
            se.SdModel(run.emu, *(float(x) for x in args.sd_latency.split(",")))
        run.emu.call(run.emu.sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
        res["play"] = run.run_tasks(args.seconds, f"playing {args.seconds:g} s, card "
                                    + (f"model {args.sd_latency}" if args.sd_latency else "instant"))
    if args.scenario == "browse":
        res["browse"] = run.browse(args.steps)
    path = os.path.join(args.out, (args.name or args.scenario) + ".json")
    json.dump(res, open(path, "w"), indent=1, default=str)
    log(f"-> {path}")


if __name__ == "__main__":
    main()
