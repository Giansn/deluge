#!/usr/bin/env python3
"""A big SD card and a big project on the real firmware in the emulator (tests/song's harness): what grows with the card,
the number of files, long names, big samples and the card's own speed. One scenario per call (run.sh runs them all,
one emulator at a time); images from make_bigsd.py (their layout in <image>.json tells the areas apart).

Usage: sdload_emu.py <deluge.elf> <image> <out dir> <scenario> [--save] [--idle S] [--idle-fixed S] [--lines]
                     [--seconds S] [--sd-latency CMD_US,SECTOR_US] [--sd-wait yield|loop] [--ipc X] [--steps N]
                     [--settle-ms MS] [--tools PREFIX] [--build DIR]
Scenarios:
  load   boot (the card mounted), SONGS/DEFAULT.XML loaded as at startup (setupStartupSong()), then optionally
         --save (SONGS/SAVETEST.XML written as SaveSongUI does: the first cluster of a new file is where FatFS
         searches the FAT for free space, create_chain()) and/or --idle S (below). Per phase: instructions, SD
         commands (sd_read_sect()/disk_write() calls) and sectors by area (FAT: also how far into it, directories,
         data), estimated real time with the card profiles: the CPU's instructions plus command and sector times. Not in
         the estimates: the driver's own work per command (routineForSD() and, while the card works, the yield to the
         task manager, which returns only after the task it runs), since sd_read_sect() is intercepted: a lower bound.
         Per sample lookup (AudioFileManager::getAudioFileFromFilename(); the song looks up every reference twice,
         Song::loadAllSamples(false), then (true)): its file and the sectors it read; per file read from the card (its
         first lookup; the later ones find it in memory): directory, FAT and data sectors, by folder. RAM (the
         allocator's regions) after loading.
  --idle S  after loading, the song stopped, S seconds of emulated time with the firmware's own task manager
         (song_emu.run_task_manager(): the registered tasks as its scheduler picks them, the SSI's DMA in real time)
         and the CPU monitor on (cpu_stats, as Settings > CPU monitor: its half-second windows and what it shows,
         computed by the firmware's cpu_stats::summarize()). Per task (name and priority: two are "playback routine"):
         calls, instructions inclusive and exclusive (without the tasks run inside it while it yields). The audio
         routine: calls, samples each, instructions, CPU % as cpu_stats measures it (time in routine() / audio
         rendered), and per run of its task the time until the next and what fills it (other tasks, the scheduler).
         --idle-fixed S: then S seconds more with the playback routine's (priority 2) target interval set to 16/44100. s
         in RAM, what deluge.cpp:584 means: its integer 16 / 44100 is 0, which keeps that task always due, and while
         it is backed off the scheduler's fallback runs whatever is past its own back-off, the audio routine (10 us)
         first. --lines: then, for 0.02 s more, the distinct 32-byte lines (the Cortex-A9's L1 line) of SDRAM and
         internal RAM each audio routine call touches (data only), against the L1 data cache's 1,024 lines; the calls
         that render nothing apart (--lines also after play: the song still playing, the card instant from then on, SDRAM
         only).
  play   loads, then plays (PlaybackHandler::playButtonPressed()) for --seconds with the task manager as for --idle,
         the card instant or, with --sd-latency, taking time (song_emu.SdModel; --sd-wait yield, the default, as the
         firmware built with USE_TASK_MANAGER waits: routineForSD() once, then the driver's waits yield to the task
         manager until the card is done; loop: routineForSD() again and again, a build without USE_TASK_MANAGER).
         Cluster loads, the card waits (their time against the card's own), audio routine calls by where they come
         from (the task manager; a task run inside another task's yield, i.e. while it waits for the card;
         routineForSD()), gaps (song_emu.RealTimeDma: 128 samples or more = an underrun), culled voices by type,
         cpu_stats as shown (QL: direness above 0, VC: voices culled).
  browse loads, opens the song browser (openUI(&loadSongUI)), lets the task manager run until its scroll-in has
         ended, then turns the select encoder one step at a time (LoadSongUI::selectEncoderAction(+1)) through the
         whole SONGS folder with --settle-ms of the task manager after each step (UI timers: the scrolling; a fast
         turn): per step instructions, commands and sectors (the next song's preview drawn from its file), and how
         often the browser reads the whole folder again (Browser::readFileItemsFromFolderAndMemory()); the UI modes
         found before the steps (reset only if the select encoder would be ignored).
--ipc X: the CPU runs X instructions per cycle at 400 MHz (song_emu.set_instructions_per_cycle(); default 1, the
emulator's usual assumption; the real Cortex-A9 with cache misses runs fewer).
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
from song_emu import SAMPLE_RATE, STOP  # noqa: E402
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2  # noqa: E402

# Card profiles for the estimates: (µs per command, µs per 512-byte sector)
PROFILES = {"typical (1 ms/command, 12 MB/s)": (1000.0, 512 / 12e6 * 1e6),
            "fast (0.25 ms/command, 25 MB/s)": (250.0, 512 / 25e6 * 1e6)}
UI_MODE_HORIZONTAL_SCROLL = 1 << 29  # ui.h (UI_MODE_NONE 0, UI_MODE_VERTICAL_SCROLL 1)
AUDIO_TASK = "audio routine (p0)"
PLAYBACK_TASK = "playback routine (p2)"


def hz():
    """Emulated instructions per second: 400 MHz times --ipc (song_emu.CPU_HZ)."""
    return se.CPU_HZ


def log(s):
    print(s, flush=True)


class Card:
    """The image's layout (make_bigsd.py's .json): which area a sector is in."""

    def __init__(self, image):
        self.layout = json.load(open(image + ".json"))
        lay = self.layout
        self.fat_start, self.data_start, self.csize = lay["fat_start"], lay["data_start"], lay["csize"]
        self.fat_sectors = lay["fat_sectors"]
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
        """Commands and sectors by kind and area of a slice of emu.sd_log; <kind>_fat_extent: how far into the FAT
        (sectors of one copy from its start) the reads or writes went."""
        out = collections.Counter()
        for kind, sector, count, _ in entries:
            a = self.area(sector)
            out[f"{kind}_commands"] += 1
            out[f"{kind}_sectors"] += count
            out[f"{kind}_{a}_commands"] += 1
            out[f"{kind}_{a}_sectors"] += count
            if a == "fat":
                end = (sector - self.fat_start) % self.fat_sectors + count
                out[f"{kind}_fat_extent"] = max(out[f"{kind}_fat_extent"], end)
        return dict(out)


def estimate(instructions, sd):
    """Seconds on the Deluge: the CPU work plus the card's time for these commands and sectors (not the driver's own
    work per command: a lower bound)."""
    commands = sd.get("r_commands", 0) + sd.get("w_commands", 0)
    sectors = sd.get("r_sectors", 0) + sd.get("w_sectors", 0)
    return {name: round(instructions / hz() + (commands * c + sectors * s) / 1e6, 3)
            for name, (c, s) in PROFILES.items()}


def short(profile_values):
    return ", ".join(f"{v:.2f} s {k.split()[0]}" for k, v in profile_values.items())


class Run:
    def __init__(self, args):
        self.args = args
        tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                           "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
        self.card = Card(args.image)
        self.emu = emu = se.Emulator(args.elf, args.image, tools, args.build, log)
        emu.sd_log = []
        se.setup_sd(emu)
        self.regions = None
        self.ui_renders = None  # --ui-load: [redraws so far]
        self.result = dict(scenario=args.scenario, image=os.path.basename(args.image), card=self.card.layout["notes"],
                           card_clusters=self.card.layout["num_clusters"], used_clusters=self.card.layout["used_clusters"],
                           fat_sectors=self.card.fat_sectors,
                           first_free_cluster=self.card.layout.get("first_free_cluster"),
                           instructions_per_cycle=hz() / 400e6, phases={}, profiles=PROFILES)
        self.phase("boot (mount)", lambda: se.boot(emu))
        emu.call(emu.sym["_ZN6deluge3hid7display15swapDisplayTypeEv"])  # The OLED
        self.lookups = []
        self.watch_lookups()
        emu.uc.ctl_flush_tb()
        t = time.time()
        self.phase("song load (setupStartupSong())", lambda: se.load_startup_song(emu))
        self.result["load_host_s"] = time.time() - t
        self.result["lookups"] = self.lookup_stats()
        self.result["ram_after_load"] = se.ram_usage(emu)

    def phase(self, name, fn):
        emu = self.emu
        i0, n0 = emu.now(), len(emu.sd_log)
        fn()
        instructions = emu.now() - i0
        sd = self.card.summarize(emu.sd_log[n0:])
        p = dict(instructions=instructions, cpu_s=instructions / hz(), sd=sd, estimated_s=estimate(instructions, sd))
        self.result["phases"][name] = p
        log(f"{name}: {instructions / 1e6:,.1f}M instructions ({instructions / hz():.3f} s CPU), reads "
            f"{sd.get('r_commands', 0):,} commands / {sd.get('r_sectors', 0):,} sectors (FAT "
            f"{sd.get('r_fat_sectors', 0):,}, directories {sd.get('r_dir_sectors', 0):,}, data "
            f"{sd.get('r_data_sectors', 0):,}), writes {sd.get('w_commands', 0):,} / {sd.get('w_sectors', 0):,}; "
            f"estimated {short(p['estimated_s'])} (card and CPU, without the driver's work per command)")
        if sd.get("r_fat_sectors", 0) > 64:  # A search of the FAT
            ff = self.card.layout.get("first_free_cluster")
            log(f"  FAT: {sd['r_fat_sectors']:,} sectors read, up to its sector {sd['r_fat_extent']:,} of "
                f"{self.card.fat_sectors:,} (one copy)"
                + (f"; the first free cluster, {ff:,}, is in its sector {ff * 4 // 512 + 1:,}" if ff else ""))
        return p

    # --- the sample lookups while loading

    def watch_lookups(self):
        emu, sym = self.emu, self.emu.sym
        prefix = "_ZN16AudioFileManager24getAudioFileFromFilename"
        # LTO's .constprop clone drops `this` (the one audioFileManager): the path is then the first argument
        clone = any("constprop" in k for k in sym.by_name if k.startswith(prefix))
        path_reg, may_reg = (UC_ARM_REG_R0, UC_ARM_REG_R1) if clone else (UC_ARM_REG_R1, UC_ARM_REG_R2)
        string_memory, = se.gdb_values(emu, ["(int)&((String*)0)->stringMemory"])
        regions = se.Regions(emu, [(prefix, "lookup")], "other")

        def enter(e, back):
            p = e.u32(e.uc.reg_read(path_reg) + string_memory)
            raw = (e.ram(p, 256) or e.ram(p, 32) or b"") if p else b""
            return raw.split(b"\0")[0].decode(errors="replace"), e.uc.reg_read(may_reg) & 0xFF, len(e.sd_log)

        def leave(e, extra, n):
            self.lookups.append((extra[0], extra[1], n, self.card.summarize(e.sd_log[extra[2]:])))
        regions.on_enter["lookup"] = enter
        regions.on_exit["lookup"] = leave

    def lookup_stats(self):
        L = self.lookups
        if not L:
            return None
        first, again = {}, 0
        for x in L:
            if x[3].get("r_commands", 0):
                if x[0] in first:
                    again += 1
                else:
                    first[x[0]] = x
        tot = collections.Counter()
        for x in L:
            tot.update(x[3])
        instructions = sum(x[2] for x in L)

        def group(xs):
            d = np.array([x[3].get("r_dir_sectors", 0) for x in xs])
            mean = lambda key: float(np.mean([x[3].get(key, 0) for x in xs]))  # noqa: E731
            return dict(files=len(xs), dir_sectors_mean=float(d.mean()), dir_sectors_min=int(d.min()),
                        dir_sectors_max=int(d.max()), dir_sectors_total=int(d.sum()),
                        fat_sectors_mean=mean("r_fat_sectors"), data_sectors_mean=mean("r_data_sectors"),
                        commands_mean=mean("r_commands"), instructions_mean=float(np.mean([x[2] for x in xs])))
        by_folder = collections.defaultdict(list)
        for p, x in first.items():
            by_folder[p.rsplit("/", 1)[0] if "/" in p else ""].append(x)
        folders = self.card.layout["notes"].get("folders", {})
        out = dict(lookups=len(L), files=len(dict.fromkeys(x[0] for x in L)),
                   by_may_read_card={str(k): v for k, v in sorted(collections.Counter(x[1] for x in L).items())},
                   files_read=len(first), lookups_reading_again=again, lookups_in_memory=len(L) - len(first) - again,
                   per_file_read=group(list(first.values())) if first else None,
                   by_folder={f: dict(group(xs), folder=folders.get(f)) for f, xs in by_folder.items()},
                   total=dict(instructions=instructions, commands=tot["r_commands"], sectors=tot["r_sectors"],
                              dir_sectors=tot["r_dir_sectors"], fat_sectors=tot["r_fat_sectors"],
                              data_sectors=tot["r_data_sectors"]),
                   estimated_total_s=estimate(instructions, dict(tot)))
        log(f"sample lookups (getAudioFileFromFilename()): {len(L)} for {out['files']} files (mayReadCard "
            f"{out['by_may_read_card']}); {len(first)} read the card, each file's first; {out['lookups_in_memory']} "
            f"found the file in memory" + (f", {again} read the card again" if again else ""))
        if first:
            g = out["per_file_read"]
            log(f"  per file read: directory sectors mean {g['dir_sectors_mean']:.0f} (min {g['dir_sectors_min']}, max "
                f"{g['dir_sectors_max']}), FAT {g['fat_sectors_mean']:.1f}, data {g['data_sectors_mean']:.0f}, commands "
                f"{g['commands_mean']:.0f}, {g['instructions_mean'] / 1e3:,.0f}k instructions")
        for f, v in sorted(out["by_folder"].items(), key=lambda kv: -kv[1]["dir_sectors_total"])[:3]:
            fo = v["folder"]
            log(f"  {f or '(root)'}" + (f" ({fo['files']:,} files, {fo['entries']:,} entries, {fo['sectors']:,} sectors)"
                                        if fo else "")
                + f": {v['files']} files read, directory sectors mean {v['dir_sectors_mean']:.0f} (min "
                  f"{v['dir_sectors_min']}, max {v['dir_sectors_max']}), total {v['dir_sectors_total']:,}")
        log(f"  all lookups: {tot['r_commands']:,} commands, {tot['r_sectors']:,} sectors (directories "
            f"{tot['r_dir_sectors']:,}); estimated {short(out['estimated_total_s'])}")
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

    def setup_tasks(self):
        """Once: per task (TaskManager::runTask()) calls and instructions, inclusive and exclusive (without the tasks
        run inside it, as while it waits for the card); the audio routine's calls by caller; culls, cluster loads."""
        if self.regions:
            return
        emu, sym = self.emu, self.emu.sym
        (self.task_size, self.name_off, self.prio_off, self.target_off, self.handle_off, list_size,
         current_off, average_off) = se.gdb_values(emu, ["sizeof(Task)", "(int)&((Task*)0)->name",
                                                         "(int)&((Task*)0)->schedule.priority",
                                                         "(int)&((Task*)0)->schedule.targetInterval",
                                                         "(int)&((Task*)0)->handle", "sizeof(((TaskManager*)0)->list)",
                                                         "(int)&((TaskManager*)0)->currentID",
                                                         "(int)&((Task*)0)->durationStats.average"])
        self.num_slots = list_size // self.task_size
        self.tm = sym["taskManager"]
        timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
        rfs = sym["routineForSD"] & ~1
        rfs_end = rfs + sym.by_name["routineForSD"][1]
        regions = se.Regions(emu, [("_ZN11TaskManager7runTaskEa", "task"), ("_ZN11AudioEngine7routineEv", "audio")],
                             "scheduler, idle loop")
        self.task_stack = []  # [task ID, instructions of the tasks run inside it]

        def task_enter(e, back):
            tid = struct.unpack("<b", bytes([e.uc.reg_read(UC_ARM_REG_R0) & 0xFF]))[0]
            self.task_stack.append([tid, 0, 0])  # ID, tasks run inside it, audio routine calls inside it
            return tid

        def task_exit(e, tid, n):
            _, inner, audio = self.task_stack.pop() if self.task_stack else (tid, 0, 0)
            t = self.tasks[tid]
            t[0] += 1
            t[1] += n
            t[2] += n - inner
            t[3] = max(t[3], n - inner - audio)  # How long the audio routine waited for it
            if self.task_stack:
                self.task_stack[-1][1] += n

        def audio_enter(e, back):
            if rfs <= back < rfs_end:
                caller = "routineForSD()"
            else:
                depth = sum(1 for s in regions.stack if s[0] == "task")
                caller = ("task manager" if depth == 1 else "task manager, inside another task's yield"
                          if depth > 1 else "other")
            return caller, e.u32(timer), e.now()

        def audio_exit(e, extra, n):
            if extra[0] in ("routineForSD()", "other") and self.task_stack:
                self.task_stack[-1][2] += n  # Called from inside a task: the audio didn't wait for that part
            self.routine_calls.append((extra[0], n, (e.u32(timer) - extra[1]) & 0xFFFFFFFF, extra[2], e.now()))

        regions.on_enter.update(task=task_enter, audio=audio_enter)
        regions.on_exit.update(task=task_exit, audio=audio_exit)
        self.regions = regions

        # mastertune-v17: setDireness() goes by the audio routine's own time (AudioEngine::routineTimeAverage)
        own = sym.by_name.get("_ZN11AudioEngine18routineTimeAverageE")
        self.cull_judged_by = ("the audio routine's own average time" if own else
                               "the current task's average duration")

        def on_cull(e):
            """cullVoice(saveVoice, type, ...): setDireness() judged by the current task's average duration
            (getLastRunTimeforCurrentTask(), in samples: dspTime), from v17 by the audio routine's own
            (routineTimeAverage); against it the DMA's real lag."""
            kind = e.uc.reg_read(UC_ARM_REG_R1)
            self.culls[se.CULL_TYPES[kind] if kind < len(se.CULL_TYPES) else str(kind)] += 1
            audio = [x for x in regions.stack if x[0] == "audio"]
            current = struct.unpack("<b", bytes(e.uc.mem_read(self.tm + current_off, 1)))[0]
            self.cull_context[f"routine() from {audio[-1][4][0] if audio else '?'}, current task "
                              f"{self.task_key(current)}"] += 1
            if own:
                average = struct.unpack("<d", bytes(e.uc.mem_read(own[0], 8)))[0]
            else:
                average = struct.unpack("<d", bytes(e.uc.mem_read(self.tm + current * self.task_size + average_off,
                                                                  8)))[0] if 0 <= current < self.num_slots else 0
            self.cull_samples.append((round(average * SAMPLE_RATE), e.dma.gap() if e.dma else -1))
        emu.intercept(sym.find("_ZN11AudioEngine9cullVoiceE"), on_cull)

        def on_load(e):
            self.cluster_loads += 1
        emu.intercept(sym.find("_ZN16AudioFileManager11loadClusterE"), on_load)
        emu.uc.ctl_flush_tb()

    def reset_counters(self):
        self.tasks = collections.defaultdict(lambda: [0, 0, 0, 0])  # calls, inclusive, exclusive, longest run (excl.)
        self.routine_calls = []  # (caller, instructions, samples, start, end)
        self.culls = collections.Counter()
        self.cull_context = collections.Counter()
        self.cull_samples = []  # (numSamples setDireness() judged by, the DMA's gap then)
        self.cluster_loads = 0
        self.regions.time.clear()
        self.regions.last = self.emu.bc.bc_total()

    def task_key(self, tid):
        """The task's name and priority (the list as it is now)."""
        if not 0 <= tid < self.num_slots:
            return f"task {tid}"
        t = self.tm + tid * self.task_size
        p = self.emu.u32(t + self.name_off)
        s = self.emu.ram(p, 40) if p else None
        name = " ".join(s.split(b"\0")[0].decode(errors="replace").split()) if s else f"task {tid}"
        return f"{name} (p{self.emu.u8(t + self.prio_off)})"

    def run_tasks(self, seconds, label, setup=None):
        """setup(dma): called with the RealTimeDma before the task manager runs (clockin's MIDI input)."""
        emu = self.emu
        self.setup_tasks()
        self.reset_counters()
        cpu = se.CpuStats(emu)
        dma = se.RealTimeDma(emu)
        if setup:
            setup(dma)
        waits0 = len(emu.sd_model.waits) if emu.sd_model else 0
        i0, n0, t = emu.now(), len(emu.sd_log), time.time()
        se.run_task_manager(emu, seconds)
        dma.close()
        cpu.close()
        self.regions.charge()
        total = emu.now() - i0
        us = lambda n: n / hz() * 1e6  # noqa: E731
        sd = self.card.summarize(emu.sd_log[n0:])
        calls = self.routine_calls
        rendering = [c for c in calls if c[2]]
        audio_instr = sum(c[1] for c in calls)
        samples = sum(c[2] for c in calls)
        keys = {tid: self.task_key(tid) for tid in self.tasks}
        tasks = {keys[tid]: dict(calls=v[0], inclusive=v[1] / total, exclusive=v[2] / total, longest_us=us(v[3]))
                 for tid, v in sorted(self.tasks.items(), key=lambda kv: -kv[1][2])}
        audio_task = next((v for tid, v in self.tasks.items() if keys[tid] == AUDIO_TASK), [0, 0, 0])
        runs = max(audio_task[0], 1)
        others = sum(v[2] for tid, v in self.tasks.items() if keys[tid] != AUDIO_TASK)
        scheduler = self.regions.time.get("scheduler, idle loop", 0)
        windows = cpu.summaries()
        r = dict(label=label, seconds=total / hz(), host_s=time.time() - t, instructions=total, sd=sd,
                 instructions_per_cycle=hz() / 400e6,
                 audio=dict(calls=len(calls), rendering_calls=len(rendering), samples=samples,
                            samples_per_rendering_call=samples / max(len(rendering), 1),
                            samples_per_call_most_often=dict(collections.Counter(c[2] for c in rendering).most_common(8)),
                            instructions=audio_instr, instructions_per_call=audio_instr / max(len(calls), 1),
                            instructions_per_rendering_call=sum(c[1] for c in rendering) / max(len(rendering), 1),
                            empty_call_instructions=sum(c[1] for c in calls if not c[2]),
                            cpu_percent=audio_instr / max(samples, 1) / (hz() / SAMPLE_RATE) * 100,
                            share_of_time=audio_instr / total,
                            rendering_calls_by_caller=dict(collections.Counter(c[0] for c in rendering))),
                 audio_task=dict(runs=audio_task[0], every_us=us(total / runs), in_task_us=us(audio_task[2] / runs),
                                 gap_us=us((total - audio_task[2]) / runs), gap_other_tasks_us=us(others / runs),
                                 gap_scheduler_us=us(scheduler / runs), other_tasks_share=others / total,
                                 scheduler_share=scheduler / total),
                 tasks=tasks, exclusive=dict(self.regions.time), culls=dict(self.culls),
                 cull_context=dict(self.cull_context),
                 cull_judged_samples_max=max((c[0] for c in self.cull_samples), default=None),
                 cull_dma_gap_max=max((c[1] for c in self.cull_samples), default=None),
                 cluster_loads=self.cluster_loads,
                 dma=dict(max_gap=dma.max_gap, underrun_samples=dma.underruns,
                          samples_played=int(dma.position() - dma.start_position)),
                 cpu_stats=windows)
        if emu.sd_model:
            waits = emu.sd_model.waits[waits0:]
            d = np.array([w[1] for w in waits] or [0]) / hz() * 1e3
            card = np.array([w[5] for w in waits] or [0]) / hz() * 1e3
            r["sd_waits"] = dict(count=len(waits), ms_mean=float(d.mean()), ms_max=float(d.max()),
                                 ms_total=float(d.sum()), card_ms_mean=float(card.mean()),
                                 extra_ms_mean=float((d - card).mean()), extra_ms_max=float((d - card).max()),
                                 polls=int(sum(w[4] for w in waits)), model=emu.sd_model.params)
        a, at = r["audio"], r["audio_task"]
        log(f"{label}: {r['seconds']:.2f} s emulated ({r['host_s']:.0f} s host), {total / 1e6:,.0f}M instructions"
            + (f", the CPU at {r['instructions_per_cycle']:g} instructions per cycle"
               if r["instructions_per_cycle"] != 1 else ""))
        log(f"  audio routine: {a['calls']:,} calls ({a['calls'] / r['seconds']:,.0f}/s), {a['rendering_calls']:,} "
            f"rendering, {a['samples_per_rendering_call']:.1f} samples each (most often "
            f"{a['samples_per_call_most_often']}), {a['instructions_per_rendering_call']:,.0f} instructions each; by "
            f"caller {a['rendering_calls_by_caller']}; CPU {a['cpu_percent']:.1f} % as cpu_stats measures (time in "
            f"routine() / audio rendered)")
        log(f"  its task: every {at['every_us']:.1f} us ({at['runs']:,} runs): {at['in_task_us']:.1f} us in it, then "
            f"{at['gap_us']:.1f} us until the next (other tasks {at['gap_other_tasks_us']:.1f} us, the scheduler "
            f"{at['gap_scheduler_us']:.1f} us); in all, other tasks {at['other_tasks_share'] * 100:.1f} % of the time, "
            f"the scheduler {at['scheduler_share'] * 100:.1f} %")
        log("  tasks (exclusive / inclusive, runs): " + ", ".join(
            f"{k} {v['exclusive'] * 100:.1f}/{v['inclusive'] * 100:.1f}% ({v['calls']:,})"
            for k, v in list(tasks.items())[:10]))
        longest = sorted(((k, v["longest_us"]) for k, v in tasks.items() if k != AUDIO_TASK), key=lambda kv: -kv[1])
        log("  longest runs (exclusive, the audio routine waits meanwhile): " + ", ".join(
            f"{k} {u:.0f} us ({u * SAMPLE_RATE / 1e6:.0f} samples)" for k, u in longest[:4]))
        if self.ui_renders is not None:
            r["ui_renders"] = self.ui_renders[0]
            log(f"  UI load: {self.ui_renders[0]:,} redraws of the pads, sidebar and OLED ({self.ui_renders[0] / r['seconds']:.0f}/s)")
            self.ui_renders[0] = 0
        log(f"  cluster loads {self.cluster_loads}, SD reads {sd.get('r_commands', 0)} commands / {sd.get('r_sectors', 0)} "
            f"sectors, culls {dict(self.culls)}, DMA max gap {dma.max_gap} samples, underrun samples {dma.underruns}")
        if self.cull_samples:
            log(f"  culls judged by up to {r['cull_judged_samples_max']} samples (setDireness(): {self.cull_judged_by}"
                f") while the DMA was at most {r['cull_dma_gap_max']} samples behind: {dict(self.cull_context)}")
        if "sd_waits" in r:
            w = r["sd_waits"]
            log(f"  card waits ({w['model']['wait']}): {w['count']} commands, mean {w['ms_mean']:.2f} ms (the card's own "
                f"{w['card_ms_mean']:.2f} ms, +{w['extra_ms_mean']:.2f} ms until the firmware noticed, max +"
                f"{w['extra_ms_max']:.2f}), max {w['ms_max']:.2f} ms, total {w['ms_total']:.0f} ms, polls {w['polls']:,}")
        if windows:
            avg = [x["dspAvgPermille"] / 10 for x in windows]
            steady = avg[1:] or avg  # The first window began before this phase
            r["cpu_stats_avg_percent"] = float(np.mean(steady))
            log(f"  cpu_stats ({len(windows)} windows of 0.5 s): CPU avg {np.mean(steady):.1f} % (after the first "
                f"window; {min(avg):.1f}-{max(avg):.1f}), peak max {max(x['dspPeakPermille'] for x in windows) / 10:.1f} %, voices max "
                f"{max(x['voicesMax'] for x in windows)}, direness max {max(x['direMax'] for x in windows)} (QL in "
                f"{sum(x['direMax'] > 0 for x in windows)} windows), culled {sum(x['culled'] for x in windows)} (VC), "
                f"SD loads {sum(x['sdLoads'] for x in windows)} (avg up to {max(x['sdAvgUs'] for x in windows)} us), "
                f"max gap {max(x['maxGapUs'] for x in windows)} us")
        return r

    def ui_load(self):
        """--ui-load: every 10 ms the next doAnyPendingUIRendering() (the pending-UI task's, at most every 10 ms, or
        another caller's) redraws all main pads, the sidebar and the OLED with the firmware's own functions (the open UI's renderMainPads(), renderSidebar(),
        renderOLED(); the pads' colours to the PIC's UART, the image to the OLED's DMA), as while a knob is turned or the
        view scrolls: the UI's real work at its real cost, which the audio routine waits for."""
        emu, sym = self.emu, self.emu.sym
        rows, side, oled = (sym["whichMainRowsNeedRendering"], sym["whichSideRowsNeedRendering"],
                            sym["doesOLEDNeedRendering"])
        self.ui_renders = [0]
        last = [None]

        def before(e):
            if last[0] is not None and e.now() - last[0] < 0.01 * se.CPU_HZ:
                return
            last[0] = e.now()
            e.uc.mem_write(rows, struct.pack("<I", 0xFF))
            e.uc.mem_write(side, struct.pack("<I", 0xFF))
            e.uc.mem_write(oled, b"\x01")
            self.ui_renders[0] += 1
        emu.intercept(sym.find("_Z23doAnyPendingUIRenderingv"), before)
        emu.uc.ctl_flush_tb()

    def clock_in(self, seconds, bpm):
        """clockin: the song follows an external MIDI clock that comes in through the firmware's own DIN MIDI input:
        a MIDI start, then a clock (0xF8) every 1/24 beat at `bpm`, each byte put into the UART's receive ring at its
        arrival (the receive DMA's write address, CRDA, read by uartGetCharWithTiming(), moves on then) and with its
        timing capture as the RZ/A1's DMA takes it (the SSI transmit DMA's source address, CRSA, at the arrival). Per
        tick the firmware processed (PlaybackHandler::inputTick()): where it placed it (timeLastInputTicks[0]) against
        when the byte arrived, i.e. when the tick's sample plays minus the arrival (the firmware aims at 168 samples:
        40 plus the output buffer's 128), how long the byte waited, and the samples rendered ahead then."""
        emu, sym = self.emu, self.emu.sym
        ph = sym["playbackHandler"]
        off_ticks, off_enabled, off_ignoring = se.gdb_values(emu, [
            "(int)&((PlaybackHandler*)0)->timeLastInputTicks", "(int)&((PlaybackHandler*)0)->midiInClockEnabled",
            "(int)&((PlaybackHandler*)0)->ignoringMidiClockInput"])
        emu.uc.mem_write(ph + off_enabled, b"\x01")
        emu.uc.mem_write(ph + off_ignoring, b"\x00")
        rx, rx_size = sym.by_name["midiRxBuffer"]
        timing, timing_size = sym.by_name["midiRxTimingBuffer"]
        timing_size //= 4
        read_addr = sym["rxBufferReadAddr"] + 4  # UART_ITEM_MIDI
        crda = se.dmac_channel_base(emu.elf_bytes_at(sym["rxDmaChannels"] + 1, 1)[0]) + 0x1C
        timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
        out_pos, out_end = sym["_ZN11AudioEngine24renderingBufferOutputPosE"], sym["_ZN11AudioEngine24renderingBufferOutputEndE"]
        ssi = sym["ssiTxBuffer"]
        period = se.CPU_HZ * 60 / (bpm * 24)  # Instructions per clock
        state = dict(events=[], next=0, written=0, arrivals={}, dma=None)
        ticks = []  # (arrival position, heard position, waited samples, rendered ahead, tick number)

        def setup(dma):
            state["dma"] = dma
            t0 = emu.now() + int(0.02 * se.CPU_HZ)
            state["events"] = [(t0, 0xFA)] + [(int(t0 + (k + 0.5) * period), 0xF8)
                                             for k in range(int((seconds - 0.03) * se.CPU_HZ / period))]

        def write_address(size):
            dma = state["dma"]
            if dma is not None:
                now = emu.now()
                ev = state["events"]
                while state["next"] < len(ev) and ev[state["next"]][0] <= now:
                    t, byte = ev[state["next"]]
                    k = state["written"]
                    emu.uc.mem_write(rx + k % rx_size, bytes([byte]))
                    pos = dma.exact_position(t)
                    whole = int(pos // 1)
                    crsa = ssi + whole % 128 * 8 + (4 if pos - whole >= 0.5 else 0)
                    emu.uc.mem_write(timing + k % timing_size * 4, struct.pack("<I", crsa))
                    state["arrivals"][k % rx_size] = pos
                    state["written"] += 1
                    state["next"] += 1
            return rx + state["written"] % rx_size
        emu.readers[crda] = write_address

        regions = se.Regions(emu, [("_ZN15PlaybackHandler9inputTickEbm", "tick")], "other")

        def enter(e, back):
            k = (e.u32(read_addr) - 1 - rx) % rx_size
            return state["arrivals"].get(k), e.u32(ph + off_ticks)

        def leave(e, extra, n):
            arrival, before = extra
            dma = state["dma"]
            tick = e.u32(ph + off_ticks)
            if arrival is None or dma is None or tick == before:
                return  # Not one of ours, or skipped (numInputTicksToSkip)
            ahead = (e.u32(out_end) - e.u32(out_pos)) >> 3
            next_sample = (e.u32(timer) - ahead) & 0xFFFFFFFF  # The sample dma.written stands for
            rel = struct.unpack("<i", struct.pack("<I", (tick - next_sample) & 0xFFFFFFFF))[0]
            now = dma.exact_position(e.now())
            ticks.append((arrival, dma.written + rel, now - arrival, ahead, tick))
        regions.on_enter["tick"] = enter
        regions.on_exit["tick"] = leave
        emu.uc.ctl_flush_tb()
        r = self.run_tasks(seconds, f"following an external MIDI clock at {bpm:g} BPM through the DIN MIDI input, "
                                    f"{seconds:g} s", setup=setup)
        emu.readers[crda] = lambda size: emu.u32(read_addr)
        lat = np.array([h - a for a, h, _, _, _ in ticks] or [0.0])
        waited = np.array([w for _, _, w, _, _ in ticks] or [0.0])
        ahead = np.array([x for _, _, _, x, _ in ticks] or [0])
        err = lat - 168
        r["clock_in"] = dict(bpm=bpm, clocks_sent=len(state["events"]) - 1, ticks=len(ticks),
                             latency_mean=float(lat.mean()), latency_min=float(lat.min()),
                             latency_max=float(lat.max()), latency_std=float(lat.std()),
                             error_abs_mean=float(np.abs(err).mean()), error_abs_max=float(np.abs(err).max()),
                             late_over_64=int((err > 64).sum()), waited_mean=float(waited.mean()),
                             waited_max=float(waited.max()), ahead_mean=float(ahead.mean()), ahead_max=int(ahead.max()),
                             ticks_list=[(round(a, 2), round(h - a, 2), round(w, 2), int(x)) for a, h, w, x, _ in ticks])
        c = r["clock_in"]
        log(f"  MIDI clock in: {c['ticks']} of {c['clocks_sent']} clocks placed; the tick plays {c['latency_mean']:.1f} "
            f"samples after the byte arrived on average (min {c['latency_min']:.1f}, max {c['latency_max']:.1f}, std "
            f"{c['latency_std']:.1f}; aimed at 168): error |latency - 168| mean {c['error_abs_mean']:.1f}, max "
            f"{c['error_abs_max']:.1f} samples, {c['late_over_64']} more than 64 late; the byte waited {c['waited_mean']:.1f} "
            f"samples on average (max {c['waited_max']:.1f}), rendered ahead then {c['ahead_mean']:.1f} (max {c['ahead_max']})")
        return r

    def idle_fixed(self, seconds):
        """--idle-fixed: the playback routine's target interval 16/44100. s (deluge.cpp:584 as meant) for this phase."""
        self.setup_tasks()
        at = next((self.tm + i * self.task_size + self.target_off for i in range(self.num_slots)
                   if self.emu.u32(self.tm + i * self.task_size + self.handle_off)
                   and self.task_key(i) == PLAYBACK_TASK), None)
        if at is None:
            raise SystemExit(f"no task {PLAYBACK_TASK}")
        old, = struct.unpack("<d", bytes(self.emu.uc.mem_read(at, 8)))
        self.emu.uc.mem_write(at, struct.pack("<d", 16 / 44100.))
        try:
            r = self.run_tasks(seconds, f"idle, {seconds:g} s more with the playback routine's target interval "
                                        f"{16 / 44100. * 1e6:.0f} us instead of {old * 1e6:g} us (deluge.cpp:584 as meant)")
        finally:
            self.emu.uc.mem_write(at, struct.pack("<d", old))
        r["playback_target_s"] = dict(original=old, set=16 / 44100.)
        return r

    def lines(self, seconds=0.02, internal=True):
        """Distinct 32-byte lines of SDRAM and (internal) internal RAM touched by each audio routine call (data
        accesses); the calls that render nothing apart."""
        emu = self.emu
        dma = se.RealTimeDma(emu)
        cur = [None]
        per_call = []
        empty = []
        union = [set(), set()]

        def on_mem(uc, access, address, size, value, _):
            c = cur[0]
            if c is not None:
                c[0 if address < 0x20000000 or address >= 0x40000000 else 1].add(address >> 5)

        # While playing only SDRAM: hooks on the internal RAM's data (the stack, ...) then crashed the emulation (unicorn
        # 2.1.4: a Thumb instruction after an IT block taken as invalid, a jump into data), deterministically
        ranges = ((se.SDRAM, se.SDRAM_SIZE), (se.SDRAM + se.UNCACHED_MIRROR_OFFSET, se.SDRAM_SIZE))
        if internal:
            ranges += ((se.INTERNAL_RAM, se.INTERNAL_RAM_SIZE),)
        hooks = [emu.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, on_mem, begin=b, end=b + size - 1)
                 for b, size in ranges]
        timer = emu.sym["_ZN11AudioEngine16audioSampleTimerE"]
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
                else:
                    empty.append((len(cur[0][0]), len(cur[0][1]), n))
                cur[0] = None
        regions.on_enter["lines"] = enter
        regions.on_exit["lines"] = leave
        emu.uc.ctl_flush_tb()
        se.run_task_manager(emu, seconds)
        for h in hooks:
            emu.uc.hook_del(h)
        dma.close()
        if not per_call:
            return None
        a = np.array(per_call, dtype=float)
        r = dict(calls=len(per_call), sdram_lines_mean=float(a[:, 0].mean()), sdram_lines_max=int(a[:, 0].max()),
                 internal_lines_mean=float(a[:, 1].mean()), samples_mean=float(a[:, 2].mean()),
                 instructions_mean=float(a[:, 3].mean()), sdram_lines_all_calls=len(union[0]),
                 internal_lines_all_calls=len(union[1]), l1_data_lines=1024, empty_calls=len(empty),
                 empty_sdram_lines_mean=float(np.mean([e[0] for e in empty])) if empty else 0.0,
                 empty_instructions_mean=float(np.mean([e[2] for e in empty])) if empty else 0.0, seconds=seconds)
        log(f"lines touched per rendering audio routine call ({r['calls']} calls, {r['samples_mean']:.1f} samples, "
            f"{r['instructions_mean']:,.0f} instructions each): SDRAM {r['sdram_lines_mean']:,.0f} (max "
            f"{r['sdram_lines_max']:,}), internal RAM {r['internal_lines_mean']:,.0f}; all calls together SDRAM "
            f"{r['sdram_lines_all_calls']:,}, internal {r['internal_lines_all_calls']:,} (L1 data cache: 1,024 lines "
            f"of 32 bytes); {r['empty_calls']} calls rendering nothing, SDRAM {r['empty_sdram_lines_mean']:,.0f} lines "
            f"and {r['empty_instructions_mean']:,.0f} instructions each")
        return r

    # --- the song browser

    def browse(self, steps, settle_s):
        emu, sym = self.emu, self.emu.sym
        reads = [0]

        def on_read(e):
            reads[0] += 1
        emu.intercept(sym.find("_ZN7Browser32readFileItemsFromFolderAndMemory"), on_read)
        emu.uc.ctl_flush_tb()
        ui = sym["loadSongUI"]
        mode_at = sym["currentUIMode"]
        opened = self.phase("song browser opened (openUI(&loadSongUI))",
                            lambda: emu.call(sym["_Z6openUIP2UI"], ui, timeout_s=60))
        opened["folder_reads"] = reads[0]
        opened["ui_mode_after_open"] = emu.u32(mode_at)
        dma = se.RealTimeDma(emu)
        waited = 0.0
        while emu.u32(mode_at) and waited < 2:  # The scroll-in (UI_MODE_VERTICAL_SCROLL), by the UI timer
            se.run_task_manager(emu, 0.05)
            waited += 0.05
        opened["scroll_in_s"] = waited
        opened["ui_mode_after_scroll_in"] = emu.u32(mode_at)
        log(f"currentUIMode {opened['ui_mode_after_open']:#x} after opening, {opened['ui_mode_after_scroll_in']:#x} after "
            f"{waited:.2f} s of the task manager")
        per_step = []
        select = sym["_ZN10LoadSongUI19selectEncoderActionEa"]
        selected = sym["_ZN7Browser17fileIndexSelectedE"]
        deleted = sym["_ZN7Browser26numFileItemsDeletedAtStartE"]
        modes = collections.Counter()
        mode_resets = 0
        settle_reads = [0, 0, 0]  # commands, sectors, folder reads while settling
        t = time.time()
        for i in range(steps):
            se.drain_uarts(emu)
            mode = emu.u32(mode_at)
            modes[mode] += 1
            if mode not in (0, UI_MODE_HORIZONTAL_SCROLL):  # The select encoder would be ignored
                emu.w32(mode_at, 0)
                mode_resets += 1
            r0, i0, n0 = reads[0], emu.now(), len(emu.sd_log)
            emu.call(select, ui, 1, timeout_s=60)
            sd = self.card.summarize(emu.sd_log[n0:])
            per_step.append((emu.now() - i0, sd.get("r_commands", 0), sd.get("r_sectors", 0), reads[0] - r0,
                             struct.unpack("<i", emu.uc.mem_read(selected, 4))[0] + emu.u32(deleted)))
            if settle_s:
                r1, n1 = reads[0], len(emu.sd_log)
                se.run_task_manager(emu, settle_s)
                sd = self.card.summarize(emu.sd_log[n1:])
                settle_reads[0] += sd.get("r_commands", 0)
                settle_reads[1] += sd.get("r_sectors", 0)
                settle_reads[2] += reads[0] - r1
        dma.close()
        a = np.array([p[:4] for p in per_step], dtype=float)
        rereads = a[:, 3] > 0
        est = [estimate(int(p[0]), {"r_commands": p[1], "r_sectors": p[2]}) for p in per_step]
        key = list(PROFILES)[0]
        e = np.array([x[key] for x in est])
        plain = ~rereads
        r = dict(steps=steps, settle_s=settle_s, host_s=time.time() - t, folder_reads=int(a[:, 3].sum()),
                 steps_with_reread=int(rereads.sum()),
                 per_step_instructions=dict(median=float(np.median(a[:, 0])), max=float(a[:, 0].max())),
                 per_plain_step=dict(instructions=float(a[plain, 0].mean()) if plain.any() else 0,
                                     commands=float(a[plain, 1].mean()) if plain.any() else 0,
                                     sectors=float(a[plain, 2].mean()) if plain.any() else 0),
                 per_reread=dict(instructions=float(a[rereads, 0].mean()) if rereads.any() else 0,
                                 commands=float(a[rereads, 1].mean()) if rereads.any() else 0,
                                 sectors=float(a[rereads, 2].mean()) if rereads.any() else 0),
                 total=dict(instructions=float(a[:, 0].sum()), commands=int(a[:, 1].sum()), sectors=int(a[:, 2].sum())),
                 estimated_total_s=estimate(int(a[:, 0].sum()), {"r_commands": int(a[:, 1].sum()),
                                                                  "r_sectors": int(a[:, 2].sum())}),
                 estimated_step_ms=dict(median=float(np.median(e)) * 1e3, max=float(e.max()) * 1e3,
                                        plain_mean=float(e[plain].mean()) * 1e3 if plain.any() else 0,
                                        reread_mean=float(e[rereads].mean()) * 1e3 if rereads.any() else 0),
                 while_settling=dict(commands=settle_reads[0], sectors=settle_reads[1], folder_reads=settle_reads[2]),
                 ui_modes_before_steps={f"{k:#x}": v for k, v in modes.items()}, ui_mode_resets=mode_resets,
                 selected_index_trace=[p[4] for p in per_step[::50]])
        log(f"browsing: {steps} steps, {settle_s * 1e3:g} ms of the task manager after each ({r['host_s']:.0f} s host); UI "
            f"modes before the steps {r['ui_modes_before_steps']}, reset {mode_resets}; the folder read again "
            f"{r['folder_reads']} times ({r['steps_with_reread']} steps); a step without: "
            f"{r['per_plain_step']['instructions'] / 1e3:,.0f}k instructions, {r['per_plain_step']['commands']:.1f} "
            f"commands, estimated {r['estimated_step_ms']['plain_mean']:.1f} ms; with: "
            f"{r['per_reread']['instructions'] / 1e6:,.1f}M instructions, {r['per_reread']['commands']:,.0f} commands, "
            f"{r['per_reread']['sectors']:,.0f} sectors, estimated {r['estimated_step_ms']['reread_mean']:.0f} ms "
            f"({key.split()[0]}); whole scroll {short(r['estimated_total_s'])}; while settling "
            f"{settle_reads[0]} commands, {settle_reads[2]} folder reads")
        return r


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("scenario", choices=["load", "play", "browse", "clockin"])
    ap.add_argument("--ui-load", action="store_true")
    ap.add_argument("--bpm", type=float, default=120)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--idle", type=float, default=0)
    ap.add_argument("--idle-fixed", type=float, default=0)
    ap.add_argument("--lines", action="store_true")
    ap.add_argument("--seconds", type=float, default=2)
    ap.add_argument("--steps", type=int, default=1250)
    ap.add_argument("--settle-ms", type=float, default=20)
    ap.add_argument("--sd-latency", help="CMD_US,SECTOR_US[,POLL_US] (song_emu.SdModel), for play")
    ap.add_argument("--sd-wait", default="yield", choices=["yield", "loop"])
    ap.add_argument("--ipc", type=float, default=1.0)
    ap.add_argument("--name", help="result file name (default: the scenario)")
    ap.add_argument("--tools")
    ap.add_argument("--build", default=HERE)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if args.ipc != 1:
        se.set_instructions_per_cycle(args.ipc)
    run = Run(args)
    res = run.result
    if args.ui_load:
        run.ui_load()
    if args.save:
        run.save()
    if args.idle:
        res["idle"] = run.run_tasks(args.idle, f"idle, song stopped, {args.idle:g} s with the task manager")
        if args.idle_fixed:
            res["idle_fixed"] = run.idle_fixed(args.idle_fixed)
        if args.lines:
            res["lines"] = run.lines()
    if args.scenario == "play":
        if args.sd_latency:
            se.SdModel(run.emu, *(float(x) for x in args.sd_latency.split(",")), wait=args.sd_wait)
        run.emu.call(run.emu.sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
        res["play"] = run.run_tasks(args.seconds, f"playing {args.seconds:g} s, card "
                                    + (f"model {args.sd_latency}, wait {args.sd_wait}" if args.sd_latency
                                       else "instant"))
        if args.lines:  # The card instant from here on (a wait in progress ends as modelled)
            run.emu.sd_model = None
            res["lines"] = run.lines(internal=False)
    if args.scenario == "clockin":
        if args.sd_latency:
            se.SdModel(run.emu, *(float(x) for x in args.sd_latency.split(",")), wait=args.sd_wait)
        res["clockin"] = run.clock_in(args.seconds, args.bpm)
    if args.scenario == "browse":
        res["browse"] = run.browse(args.steps, args.settle_ms / 1e3)
    path = os.path.join(args.out, (args.name or args.scenario) + ".json")
    json.dump(res, open(path, "w"), indent=1, default=str)
    log(f"-> {path}")


if __name__ == "__main__":
    main()
