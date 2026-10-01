#!/usr/bin/env python3
"""Stress test of what the user does while a heavy song plays, on the real firmware in the emulator (tests/song's
harness): the firmware's own task manager runs (song_emu.run_task_manager(): the registered tasks as its scheduler picks
them), the SSI's DMA in real time (song_emu.RealTimeDma: 128 samples or more without the buffer refilled = an underrun),
the SD card with the typical card's times (song_emu.SdModel, 1 ms per command and 12 MB/s; its waits yield to the task
manager as the firmware's do) unless --sd-latency instant. The user's actions are the firmware's own entry points
(Buttons::buttonAction(), LoadSongUI::selectEncoderAction(), openUI(), the menu items), called between slices of the
task manager as its input task would, the current task an unused slot (so a yield inside counts there). Same input for
every build: run it for two ELFs and compare (run.sh, report.py).

Usage: stress_ui_emu.py <deluge.elf> <scenario> --out DIR [--tools PREFIX] [--build DIR] [--sd-latency CMD_US,SECTOR_US
                        | instant] [--synths N] [--changes N] [--rounds N] [--steps N] [--saves N] [--cycles N]
                        [--old-elf ELF] [--old-tools PREFIX]
Scenarios:
  songchange  a card with three heavy songs: DEFAULT (make_sd.py's song: --synths synths, the kit, the audio track, the
              drone), DRIVE (the same with every LPF 24dBDrive, make_sd.py --lpf-mode) and DRONE (make_sd.py
              --drone-track's drone tracks plus --synths synths). DEFAULT loads at boot and plays; then --changes song
              changes, DRIVE, DRONE, DEFAULT, DRIVE, ...: the song browser opened (openUI(&loadSongUI)), the select
              encoder turned to the song, the encoder pressed (Buttons::buttonAction(SELECT_ENC): LoadSongUI::
              performLoad(): the song loads while the old one plays, arms, and waits in TaskManager::yield() until the
              swap at the launch event), released, then --settle seconds of the new song. Per change: the load (press
              to Session::armForSongSwap()), the wait (to PlaybackHandler::doSongSwap()), the whole press, the DMA's
              worst gap and underruns during the change and during the settle after it, the heap after it.
  bigcard     a big card (like tests/sdload's s5, make_bigsd.py's song_file(): each song as the firmware writes it,
              4 KB real, the rest sparse): DEFAULT (the heavy song, playing) and ~1,000 songs in groups that both
              v17's rule (the first word) and v18's (the name without its number) group the same: 150 groups "G001 mix",
              "G001 mix 2", .. (1, 2, 3, 5, 8 or 12 versions), "LIVE set" .. "LIVE set 120" (larger than the browser's
              window of 100 file items), 100 songs of their own "S001X solo" ... --rounds rounds: the browser opened
              (on DEFAULT, "LIVE set 60", "G075 mix 5", "S050X solo" in turn: the current song's name set first),
              --steps turns of +1 (-1 in every 2nd round; 20 ms of the task manager after each: a fast turn), on every
              3rd folded group row (LoadSongUI::selectionShownFolded()) the encoder pressed (folds out), three more
              turns through its versions, BACK (folds in); then 15 turns back; then BACK until the browser is closed. Per round: the worst gap, underruns,
              folder reads (Browser::readFileItemsFromFolderAndMemory()), per action instructions and emulated time;
              the heap after it.
  save        the heavy song (DEFAULT) playing, saved --saves times to SONGS/SAVETEST.XML as SaveSongUI does
              (StorageManager::createXMLFile(), Song::writeToFile(), XMLSerializer::closeFileAfterWriting(); only the
              firmware itself services the audio meanwhile, as in song_emu.py --save-while-playing), 0.5 s of the task
              manager between them; then SAVETEST loaded back (a song change while playing, as songchange) and saved
              again, as SONGS/SAVETST2.XML: the XML must be the same as the last save (and the saves all the same).
  settings    Community features' output limiter and filter crossing guard (v18): a card without
              CommunityFeatures.XML; both switched on in their menu items and the menu left (SoundEditor::
              exitCompletely() saves the file), a reboot on the same card: both on, reaching the audio
              (AudioEngine::outputLimiterRunning, FilterSet::crossingGuard); then --old-elf (v17) booted on that card:
              no fault, no error, its own settings as on a card without the file, the new entries kept when it saves
              the file; then the new build again: both still on. For an ELF without the two settings the steps with the
              new build are left out.
  drone       the heavy song (its drone on) playing; --cycles times: SCALE in Song view (the drone view), 0.25 s of
              the task manager (the UI timer: the graphics routine), the upper gold knob +1 and -1, 0.1 s, BACK (Song
              view), 0.25 s. Per cycle: gaps, underruns, the heap, the actions' cost.
Per run: crash (an emulator error, e.g. a write through a null pointer or an exception), an invalid memory access (an
access outside RAM, SDRAM and the peripheral space: the emulator maps it and goes on, it's recorded), freezeWithError()
and the fault handlers, error popups (OLED::displayError(), displayPopup() with an error text), a hang (an action or a
slice of the task manager not back within its limit of emulated time: blockcount.c's deadline stops it, the functions
that ran meanwhile are reported). Results: <out>/<scenario>.json and a summary on stdout; exit status 1 on a crash,
fault or hang.
Needs python3 with unicorn 2 and numpy, blockcount.so (tests/song's blockcount.c) in --build or $BLOCKCOUNT_DIR.
"""
import argparse
import collections
import difflib
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(TESTS, "song"))
sys.path.insert(0, os.path.join(TESTS, "sdload"))
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402
from song_emu import STOP  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1  # noqa: E402


def button_xy(x, y):  # hid/button.h fromXY(): 9 * (y + kDisplayHeight * 2) + x
    return 9 * (y + 16) + x


SELECT_ENC, LOAD, BACK, SCALE, SONG_VIEW = button_xy(4, 3), button_xy(6, 1), button_xy(7, 1), button_xy(6, 0), \
    button_xy(3, 1)
UI_MODE_NONE, UI_MODE_HORIZONTAL_SCROLL = 0, 1 << 29
UI_MODE_ARMED = 36  # UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_ARMED
# Where the RZ/A1L has something besides the RAM the emulator maps: the SPI flash's window (and its mirror), the SPI
# multi-I/O bus and L2 cache controller registers, the I/O areas. An access elsewhere is a wild pointer
PERIPHERAL = [(0x18000000, 0x20000000), (0x3FE00000, 0x40000000), (0x58000000, 0x60000000), (0xE8000000, 1 << 32)]
ERROR_WORDS = re.compile(r"error|fail|corrupt|card|memory|full|invalid|unsupport|freeze", re.I)
MENU_VALUE_OFFSET = 12  # runtime_feature::Setting (a Selection): value_ (as tests/settings)
MENU = "_ZN6deluge3gui9menu_item15runtime_feature{}E"
LIMITER_MENU, GUARD_MENU = MENU.format("17menuOutputLimiter"), MENU.format("23menuFilterCrossingGuard")
MENU_WRITE = "_ZN6deluge3gui9menu_item15runtime_feature7Setting17writeCurrentValueEv"
MENU_READ = "_ZN6deluge3gui9menu_item15runtime_feature7Setting16readCurrentValueEv"
GUARD_FLAG = "_ZN6deluge3dsp6filter9FilterSet13crossingGuardE"
LIMITER_FLAG = "_ZN11AudioEngineL20outputLimiterRunningE"


def log(s):
    print(s, flush=True)


def gdb_ints(emu, commands):
    """The integers gdb prints for these commands (some set the context: "list Song::Song" for the Song in it)."""
    out = subprocess.run([emu.tool_prefix + "gdb", "-batch"] + [x for c in commands for x in ("-ex", c)] + [emu.elf],
                         capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (-?\d+)$", out, re.M)]
    if len(values) != sum(c.startswith("print") for c in commands):
        raise SystemExit(f"gdb: {commands}:\n{out[-500:]}")
    return values


class Stop(Exception):
    """The run can't go on: a crash, a fault or a hang (in rig.problems)."""


class LightDma(se.RealTimeDma):
    """The SSI's DMA in real time (song_emu.RealTimeDma's hooks and clock) without keeping the samples (runs of
    minutes), with the gap judged per slot of the ring of 128, as the DMA sees it: a sample written into slot s at DMA
    position p comes (p - s) mod 128 samples after the DMA last read that slot, the gap (how many samples the DMA read
    since the buffer was last full there); if the DMA has read the slot more than once since it was last written, it
    played the old content again (an underrun: n - 1 stale samples for n reads) and the gap is 128 more per extra read.
    Unlike RealTimeDma's count of samples written, this recovers as the Deluge does after an underrun: the firmware
    renders up to where the DMA reads now, modulo the ring (AudioEngine::routine() can't see a lap it missed), so its
    next samples are on time again; RealTimeDma keeps a lag of a lap for ever and counts every later sample as an
    underrun (an artifact in long runs). Per phase (take()): the worst gap, underrun events (runs of late samples),
    stale samples, times over 64."""

    def __init__(self, emu):
        super().__init__(emu)
        self.slot_pos = [self.start_position - 1] * 128  # The DMA's position when each slot was last written
        self.last_pos, self.last_gap, self.late = self.start_position, 0, False
        self.phase = dict(max_gap=0, underruns=0, underrun_samples=0, over_64=0)
        self.underrun_events = 0

    def on_write(self, uc, access, address, size, value, _):
        base = self.base if address >= self.base else self.cached
        offset = address - base
        pos = self.position()
        p = self.phase
        for first in range(-(-offset // 8) * 8, offset + size, 8):
            s = first // 8 % 128
            reads = (pos - s) // 128 - (self.slot_pos[s] - s) // 128  # DMA reads of slot s since its last write
            self.slot_pos[s] = pos
            gap = (pos - s) % 128 + 128 * max(reads - 1, 0)
            if gap > p["max_gap"]:
                p["max_gap"] = gap
            if gap > 64 and not self.over_64_now:
                p["over_64"] += 1
            self.over_64_now = gap > 64
            late = reads >= 2
            if late:
                p["underrun_samples"] += reads - 1
                if not self.late:
                    p["underruns"] += 1
            self.late = late
            self.last_pos, self.last_gap = pos, gap

    def take(self):
        """The phase's numbers (a gap standing now counts too: nothing written for a while), and a new phase."""
        p = self.phase
        p["max_gap"] = max(p["max_gap"], self.last_gap + self.position() - self.last_pos)
        self.max_gap = max(self.max_gap, p["max_gap"])
        self.underruns += p["underrun_samples"]
        self.underrun_events += p["underruns"]
        self.over_64 += p["over_64"]
        self.phase = dict(max_gap=0, underruns=0, underrun_samples=0, over_64=0)
        return p


class Rig:
    """The emulated Deluge: booted (OLED), the startup song loaded, the task manager's list as boot() left it, the DMA
    in real time, the card with --sd-latency. Records problems (crash, fault, invalid access, hang, error popups)."""

    _patched = False

    def __init__(self, elf, tools, build, image, sd_latency, label=""):
        self.problems = []  # dict(kind, what, detail)
        self.popups = []
        self.invalid = collections.Counter()  # (address page, function) -> accesses outside RAM and peripherals
        self.label = label
        self.action_name = "boot"
        if not Rig._patched:  # Every unmapped access (the emulator maps it): the wild ones recorded
            orig = se.Emulator.on_unmapped

            def on_unmapped(emu, uc, access, address, size, value, _):
                if address >= 0x100000 and not any(lo <= address < hi for lo, hi in PERIPHERAL):
                    rig = getattr(emu, "rig", None)
                    if rig is not None:
                        rig.invalid[(address & ~0xFFF, emu.sym.name_at(uc.reg_read(UC_ARM_REG_PC)),
                                     rig.action_name)] += 1
                return orig(emu, uc, access, address, size, value, _)
            se.Emulator.on_unmapped = on_unmapped
            Rig._patched = True
        self.emu = emu = se.Emulator(elf, image, tools, build, lambda s: None)
        emu.rig = self
        sym = emu.sym
        self.hook_faults()
        se.setup_sd(emu)
        self.guard("boot", lambda: se.boot(emu), 20)
        emu.call(sym["_ZN6deluge3hid7display15swapDisplayTypeEv"])  # The OLED
        emu.uc.ctl_flush_tb()
        self.guard("startup song", lambda: se.load_startup_song(emu), 30)
        self.song_name_off, = gdb_ints(emu, ["list Song::Song", "print (int)&((Song*)0)->name"])
        (self.string_memory, region_size, empty, memory, count, msize, mstart,
         esize) = se.gdb_values(emu, ["(int)&((String*)0)->stringMemory", "sizeof(MemoryRegion)", "(int)&((MemoryRegion*)0)->emptySpaces",
                                      "(int)&((ResizeableArray*)0)->memory",
                                      "(int)&((ResizeableArray*)0)->numElements",
                                      "(int)&((ResizeableArray*)0)->memorySize",
                                      "(int)&((ResizeableArray*)0)->memoryStart",
                                      "(int)&((ResizeableArray*)0)->elementSize"])
        self.heap_offsets = (region_size, empty, memory, count, msize, mstart, esize)
        if sd_latency and sd_latency != "instant":
            se.SdModel(emu, *(float(x) for x in sd_latency.split(",")), wait="yield")
        self.dma = LightDma(emu)
        # Settings > CPU monitor on: its half-second windows as the firmware computes them (cpu_take())
        self.cpu = se.CpuStats(emu) if "_ZN9cpu_stats9collectorE" in sym.by_name else None
        self.cpu_seen = 0
        se.run_task_manager(emu, 0.001)  # Its setup (the free slot, the UART drain hook) done once
        self.task_at, self.task_slot, _ = emu.task_manager_setup
        self.a = {n: sym.find(n) for n in (
            "_Z6openUIP2UI", "_ZN7Buttons12buttonActionEhbb", "_ZN10LoadSongUI19selectEncoderActionEa",
            "_Z12getCurrentUIv", "_Z9getRootUIv", "_ZN15PlaybackHandler17playButtonPressedEl")}
        self.v = {n: sym[n] for n in ("currentUIMode", "currentSong", "loadSongUI", "sessionView", "droneView",
                                      "_ZN8QwertyUI11enteredTextE", "playbackHandler")}
        self.ui_names = {sym[n]: n for n in ("sessionView", "droneView", "instrumentClipView", "arrangerView",
                                             "loadSongUI", "soundEditor", "audioClipView", "keyboardScreen",
                                             "automationView", "saveSongUI") if n in sym.by_name}
        self.folder_reads = 0
        self.swaps = []
        self.arms = []

        def on_read(e):
            self.folder_reads += 1
        emu.intercept(sym.find("_ZN7Browser32readFileItemsFromFolderAndMemory"), on_read)
        emu.intercept(sym.find("_ZN15PlaybackHandler10doSongSwapEb"), lambda e: self.swaps.append(e.now()) and None)
        emu.intercept(sym.find("_ZN7Session14armForSongSwapEv"), lambda e: self.arms.append(e.now()) and None)
        emu.uc.ctl_flush_tb()

    # --- problems
    def hook_faults(self):
        emu, sym = self.emu, self.emu.sym

        def text(p, n=80):
            try:
                return bytes(emu.uc.mem_read(p, n)).split(b"\0")[0].decode(errors="replace")
            except Exception:  # noqa: BLE001
                return f"<{p:#x}>"

        def fault(kind, detail_fn):
            def hook(e):
                detail = detail_fn(e)
                lr = e.sym.name_at(e.uc.reg_read(UC_ARM_REG_LR))
                self.problems.append(dict(kind=kind, what=self.action_name, detail=f"{detail} (from {lr})",
                                          at_s=round(e.seconds(), 4)))
                e.stop()
            return hook
        emu.intercept(sym["freezeWithError"],
                      fault("freezeWithError", lambda e: text(e.uc.reg_read(UC_ARM_REG_R0))))
        for name in ("abort", "handle_cpu_fault", "undefined_handler", "swi_handler", "undef_handler"):
            if name in sym.by_name:
                emu.intercept(sym[name], fault(f"fault handler {name}", lambda e: ""))

        def popup(kind, reg):
            def hook(e):
                s = text(e.uc.reg_read(reg))
                self.popups.append((round(e.seconds(), 3), self.action_name, kind, s))
            return hook
        # OLED::displayPopup() is a member (the text in r1), OLED::popupText() static (in r0)
        for prefix, kind, reg in (("_ZN6deluge3hid7display4OLED12displayPopupEPKc", "popup", UC_ARM_REG_R1),
                                  ("_ZN6deluge3hid7display4OLED9popupTextEPKc", "popupText", UC_ARM_REG_R0)):
            try:
                emu.intercept(sym.find(prefix), popup(kind, reg))
            except KeyError:
                pass

        def on_error(e):
            self.popups.append((round(e.seconds(), 3), self.action_name, "displayError",
                                f"Error {e.uc.reg_read(UC_ARM_REG_R1)}"))
        emu.intercept(sym.find("_ZN6deluge3hid7display4OLED12displayErrorE5Error"), on_error)

    def error_popups(self):
        return [p for p in self.popups if p[2] == "displayError" or ERROR_WORDS.search(p[3])]

    def guard(self, what, fn, limit_s):
        """fn() with the watchdog: a hang if it isn't back within limit_s of emulated time (blockcount's deadline
        stops the emulation there); a crash if the emulator stops with an error (song_emu raises SystemExit)."""
        emu = self.emu
        self.action_name = what
        emu.bc.bc_reset_counts()
        deadline = emu.bc.bc_total() + int(limit_s * se.CPU_HZ)
        emu.bc.bc_set_deadline(deadline)
        n = len(self.problems)
        try:
            result = fn()
        except SystemExit as ex:
            self.problems.append(dict(kind="crash", what=what, detail=str(ex), at_s=round(emu.seconds(), 4)))
            raise Stop(what) from None
        finally:
            emu.bc.bc_set_deadline((1 << 64) - 1)
        if len(self.problems) > n:
            raise Stop(what)
        if emu.bc.bc_total() >= deadline and (emu.uc.reg_read(UC_ARM_REG_PC) & ~1) != STOP:
            top = se.profile_by_function(emu).most_common(6)
            pc = emu.sym.name_at(emu.uc.reg_read(UC_ARM_REG_PC))
            lr = emu.sym.name_at(emu.uc.reg_read(UC_ARM_REG_LR))
            try:
                state = dict(mode=self.mode(), song=self.song_name())
            except Exception:  # noqa: BLE001 (a hang before the rig is set up)
                state = {}
            self.problems.append(dict(kind="hang", what=what, detail=f"not back after {limit_s:g} s: at {pc} (lr {lr});"
                                      f" ran most: " + ", ".join(f"{f.split('(')[0]} {n / 1e6:.0f}M" for f, n in top),
                                      at_s=round(emu.seconds(), 4), **state))
            raise Stop(what)
        return result

    # --- state
    def string(self, address):
        p = self.emu.u32(address + self.string_memory)
        if not p:
            return ""
        return bytes(self.emu.uc.mem_read(p, 128)).split(b"\0")[0].decode(errors="replace")

    def song(self):
        return self.emu.u32(self.v["currentSong"])

    def song_name(self):
        return self.string(self.song() + self.song_name_off)

    def browser_name(self):
        return self.string(self.v["_ZN8QwertyUI11enteredTextE"])

    def mode(self):
        return self.emu.u32(self.v["currentUIMode"])

    def playing(self):
        return self.emu.u8(self.v["playbackHandler"] + 16)

    def ui_name(self, root=False):
        a = self.emu.call(self.a["_Z9getRootUIv" if root else "_Z12getCurrentUIv"])
        return self.ui_names.get(a, hex(a))

    def heap(self):
        """The allocator's regions (internal RAM, SDRAM, stealable SDRAM): bytes free, allocations (song_emu.
        ram_usage() with the offsets looked up once)."""
        region_size, empty, memory, count, msize, mstart, esize = self.heap_offsets
        emu = self.emu
        base = emu.sym["_ZZN22GeneralMemoryAllocator3getEvE22generalMemoryAllocator"]
        out = []
        for r in range(3):
            a = base + r * region_size
            arr = a + empty
            mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
            free = sum(emu.u32(mem + ((first + i) % max(size, 1)) * es) for i in range(n))
            out.append(dict(free=free, empty_spaces=n))
        # MEMORY_REGION_STEALABLE 0, _INTERNAL 1, _EXTERNAL 2 (numAllocations is counted only in test builds)
        return dict(internal_free=out[1]["free"], sdram_free=out[2]["free"], stealable_free=out[0]["free"],
                    internal_empty_spaces=out[1]["empty_spaces"], sdram_empty_spaces=out[2]["empty_spaces"])

    def cpu_take(self):
        """The CPU monitor's windows since the last call, as the firmware summarizes them: CPU average and peak (%),
        voices max, direness max, voices culled, its own worst gap (us)."""
        if not self.cpu:
            return None
        windows, self.cpu.windows = self.cpu.windows, self.cpu.windows[self.cpu_seen:]
        try:
            w = self.cpu.summaries()
        finally:
            self.cpu.windows = windows
            self.cpu_seen = len(windows)
        if not w:
            return None
        return dict(windows=len(w), cpu_avg=round(sum(x["dspAvgPermille"] for x in w) / len(w) / 10, 1),
                    cpu_peak=max(x["dspPeakPermille"] for x in w) / 10, voices_max=max(x["voicesMax"] for x in w),
                    direness_max=max(x["direMax"] for x in w), culled=sum(x["culled"] for x in w),
                    max_gap_us=max(x["maxGapUs"] for x in w))

    # --- running
    def tm(self, seconds, what="task manager"):
        """The task manager for seconds of emulated time (watchdog: 3x that + 2 s)."""
        self.guard(what, lambda: se.run_task_manager(self.emu, seconds), seconds * 3 + 2)

    def action(self, what, address, *args, limit_s=10):
        """A user action: the firmware function called as the input task would (the current task the free slot, the
        UARTs drained). Its instructions and emulated time (with what runs inside its yields)."""
        emu = self.emu
        se.drain_uarts(emu)
        emu.uc.mem_write(self.task_at, struct.pack("<b", self.task_slot))
        i0 = emu.now()
        r = self.guard(what, lambda: emu.call(address, *args), limit_s)
        return r, emu.now() - i0

    def button(self, b, on, limit_s=10):
        return self.action(f"button {b} {'on' if on else 'off'}", self.a["_ZN7Buttons12buttonActionEhbb"], b,
                           1 if on else 0, 0, limit_s=limit_s)

    def play(self):
        self.action("PLAY", self.a["_ZN15PlaybackHandler17playButtonPressedEl"], 0)
        if not self.playing():
            raise SystemExit("playback didn't start")

    def wait_mode_none(self, limit_s=3, slice_s=0.02):
        waited = 0.0
        while self.mode() != UI_MODE_NONE and waited < limit_s:
            self.tm(slice_s)
            waited += slice_s
        return waited

    def open_browser(self, start=None):
        """openUI(&loadSongUI) (it opens on the current song; with start, the current song's name set to that first,
        as tests/browser does), then the task manager until its scroll-in is over."""
        if start:
            self.emu.uc.mem_write(STOP + 0x200, start.encode() + b"\0")
            self.emu.call(self.emu.sym["_ZN6String3setEPKcl"], self.song() + self.song_name_off, STOP + 0x200,
                          0xFFFFFFFF)
        r0 = self.folder_reads
        ok, n = self.action("open the song browser", self.a["_Z6openUIP2UI"], self.v["loadSongUI"], limit_s=20)
        waited = self.wait_mode_none()
        return dict(ok=bool(ok & 0xFF), instructions=n, scroll_in_s=round(waited, 3), folder_reads=self.folder_reads - r0)

    def turn(self, offset, settle_s=0.02):
        """The select encoder in the song browser (waits up to 0.3 s for an animation to end first, as a turn during
        it is ignored; then forces the mode off)."""
        forced = False
        if self.mode() not in (UI_MODE_NONE, UI_MODE_HORIZONTAL_SCROLL):
            self.wait_mode_none(0.3)
            if self.mode() not in (UI_MODE_NONE, UI_MODE_HORIZONTAL_SCROLL):
                self.emu.w32(self.v["currentUIMode"], 0)
                forced = True
        r0 = self.folder_reads
        _, n = self.action(f"select encoder {offset:+d}", self.a["_ZN10LoadSongUI19selectEncoderActionEa"],
                           self.v["loadSongUI"], offset)
        if settle_s:
            self.tm(settle_s)
        return dict(instructions=n, folder_reads=self.folder_reads - r0, forced=forced)

    def browse_to(self, name, limit=12):
        """Turns the select encoder towards name (known to be up to limit steps away)."""
        steps = []
        for direction in (1, -1):
            for _ in range(limit):
                if self.browser_name().upper() == name.upper():
                    return steps
                steps.append(self.turn(direction))
            if self.browser_name().upper() == name.upper():
                return steps
            for _ in range(limit):  # Back to where it began, then the other way
                steps.append(self.turn(-direction))
                if self.browser_name().upper() == name.upper():
                    return steps
        raise SystemExit(f"{name} not found in the song browser (at {self.browser_name()!r})")

    def change_song(self, name, settle_s, limit_s=40):
        """Song browser, to name, the encoder pressed: loads while playing, armed, the swap at the launch event
        (all inside the press: performLoad() yields until then), released; then settle_s of the new song."""
        emu = self.emu
        self.dma.take()
        heap0 = None
        t0 = emu.now()
        opened = self.open_browser()
        nav = self.browse_to(name)
        self.wait_mode_none()  # The press is ignored while the name or the preview scrolls (the user waits)
        forced = self.mode() != UI_MODE_NONE
        if forced:
            self.emu.w32(self.v["currentUIMode"], 0)
        arms0, swaps0 = len(self.arms), len(self.swaps)
        press_at = emu.now()
        _, press_n = self.button(SELECT_ENC, True, limit_s=limit_s)
        done_at = emu.now()
        self.button(SELECT_ENC, False)
        change = self.dma.take()
        change["cpu"] = self.cpu_take()
        self.tm(settle_s, "playing after the change")
        after = self.dma.take()
        after["cpu"] = self.cpu_take()
        arm = self.arms[arms0] if len(self.arms) > arms0 else None
        swap = self.swaps[swaps0] if len(self.swaps) > swaps0 else None
        ms = lambda n: round(n / se.CPU_HZ * 1e3, 2)  # noqa: E731
        r = dict(song=name, loaded=self.song_name(), ok=self.song_name().upper() == name.upper() and swap is not None,
                 ui=self.ui_name(root=True), mode=self.mode(), playing=bool(self.playing()),
                 open_instructions=opened["instructions"], open_folder_reads=opened["folder_reads"],
                 scroll_in_s=opened["scroll_in_s"], steps=len(nav), forced_mode=forced,
                 step_instructions_max=max((s["instructions"] for s in nav), default=0),
                 load_ms=ms(arm - press_at) if arm else None, wait_ms=ms(swap - arm) if arm and swap else None,
                 press_ms=ms(done_at - press_at), press_instructions=press_n, whole_ms=ms(emu.now() - t0),
                 gap_change=change, gap_after=after, heap=self.heap())
        del heap0
        return r


# --- the cards

def scale_tempo(xml, scale):
    """The song's tempo times scale (timePerTimerTick and its fraction: samples per tick at 96 ticks per quarter)."""
    if scale == 1:
        return xml
    t = 44100 * 60 / (120 * scale) / 96
    whole = int(t)
    frac = round((t - whole) * 2 ** 32)
    frac -= 2 ** 32 if frac >= 2 ** 31 else 0
    xml, n = re.subn(r'timePerTimerTick="\d+"', f'timePerTimerTick="{whole}"', xml, count=1)
    xml, m = re.subn(r'timerTickFraction="-?\d+"', f'timerTickFraction="{frac}"', xml, count=1)
    assert n == m == 1, "no tempo in the song"
    return xml


def heavy_songs(synths, tempo_scale=1):
    """(files with the samples, {name: xml}): make_sd.py's song with `synths` synths (the kit, the audio track, the
    drone), the same with every LPF 24dBDrive, and the drone tracks plus `synths` synths; at 120 BPM times
    tempo_scale."""
    files, lengths = make_sd.samples()
    heavy = make_sd.song_xml(lengths, 1, synths)
    drive = heavy.replace('lpfMode="24dB"', 'lpfMode="24dBDrive"')
    orig = make_sd.drone_tracks
    make_sd.drone_tracks = lambda: orig() + make_sd.synths()[:synths]
    try:
        drone = make_sd.song_xml(lengths, 1, drone_track=True)
    finally:
        make_sd.drone_tracks = orig
    songs = dict(DEFAULT=heavy, DRIVE=drive, DRONE=drone)
    return files, {k: scale_tempo(v, tempo_scale) for k, v in songs.items()}


def build_songchange_card(path, synths, start="DEFAULT", tempo_scale=1):
    """SONGS/DEFAULT.XML is the song loaded at boot (start); the others under their names."""
    files, songs = heavy_songs(synths, tempo_scale)
    for name, xml in songs.items():
        files[f"SONGS/{name}.XML"] = xml.encode()
    if start != "DEFAULT":
        files["SONGS/DEFAULT.XML"], files[f"SONGS/{start}.XML"] = files[f"SONGS/{start}.XML"], files["SONGS/DEFAULT.XML"]
    fat32.build(path, files)
    return {n: len(x) for n, x in songs.items()}


GROUP_SIZES = (1, 2, 3, 5, 8, 12)


def bigcard_names():
    names = []
    for g in range(1, 151):
        k = GROUP_SIZES[g % len(GROUP_SIZES)]
        names += [f"G{g:03d} mix"] + [f"G{g:03d} mix {v}" for v in range(2, k + 1)]
    names += ["LIVE set"] + [f"LIVE set {v}" for v in range(2, 121)]
    names += [f"S{n:03d}X solo" for n in range(1, 101)]
    return names


def build_bigcard(path, synths):
    import make_bigsd  # tests/sdload
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, synths).encode()
    small = make_sd.song_xml(lengths, 1, 1)
    names = bigcard_names()
    for i, n in enumerate(names):
        data = make_bigsd.song_file(small, i).encode()
        files[f"SONGS/{n}.XML"] = (len(data), [(0, data[:4096])])
    fat32.build(path, files)
    return len(names)


# --- the scenarios

def summarize_gaps(rows, key):
    g = [r[key] for r in rows if r.get(key)]
    return dict(max_gap=max((x["max_gap"] for x in g), default=None), underruns=sum(x["underruns"] for x in g),
                underrun_samples=sum(x["underrun_samples"] for x in g), over_64=sum(x["over_64"] for x in g),
                phases_with_underruns=sum(1 for x in g if x["underruns"]))


def heap_trend(rows):
    """First and last of each region's free bytes, the least, and a straight line's slope (bytes per cycle)."""
    import numpy as np
    out = {}
    for k in ("internal_free", "sdram_free", "internal_empty_spaces", "sdram_empty_spaces"):
        y = [r["heap"][k] for r in rows if r.get("heap")]
        if len(y) >= 2:
            slope = float(np.polyfit(range(len(y)), y, 1)[0])
            out[k] = dict(first=y[0], last=y[-1], min=min(y), max=max(y), slope_per_cycle=round(slope, 1))
    return out


def run_songchange(a, rig, res):
    names = a.order.split(",")
    rig.play()
    rig.tm(1.0, "warm-up")
    res["warmup"] = rig.dma.take()
    res["warmup"]["cpu"] = rig.cpu_take()
    log(f"  warm-up ({rig.song_name() or 'DEFAULT'}): gap {res['warmup']['max_gap']}, underruns "
        f"{res['warmup']['underrun_samples']}, CPU monitor {res['warmup']['cpu']}")
    res["heap_start"] = rig.heap()
    rows = res["changes"] = []
    for i in range(a.changes):
        name = names[i % 3]
        r = rig.change_song(name, a.settle)
        r["index"] = i + 1
        rows.append(r)
        log(f"  {i + 1:2d} -> {name:7s} {'ok ' if r['ok'] else 'NOT'} load {r['load_ms']} ms, wait {r['wait_ms']} ms,"
            f" press {r['press_ms']} ms; gap {r['gap_change']['max_gap']} (underruns {r['gap_change']['underruns']}/"
            f"{r['gap_change']['underrun_samples']} smp), after {r['gap_after']['max_gap']} ({r['gap_after']['underruns']}/"
            f"{r['gap_after']['underrun_samples']}), CPU "
            f"{(r['gap_after']['cpu'] or {}).get('cpu_avg')} % culled {(r['gap_after']['cpu'] or {}).get('culled')}"
            f"; heap free internal "
            f"{r['heap']['internal_free']:,} SDRAM {r['heap']['sdram_free']:,}"
            f"; {r['ui']}")
        if not r["ok"]:
            rig.problems.append(dict(kind="song change failed", what=name, detail=f"loaded {r['loaded']!r}, "
                                     f"mode {r['mode']}, ui {r['ui']}", at_s=round(rig.emu.seconds(), 3)))
    by_song = {}
    for n in names:
        rs = [r for r in rows if r["song"] == n]
        by_song[n] = dict(changes=len(rs), gap_change=summarize_gaps(rs, "gap_change"),
                          gap_after=summarize_gaps(rs, "gap_after"),
                          load_ms_max=max((r["load_ms"] or 0 for r in rs), default=None),
                          heap=heap_trend(rs))
    res["by_song"] = by_song
    res["summary"] = dict(changes=len(rows), ok=sum(r["ok"] for r in rows), gap_change=summarize_gaps(rows, "gap_change"),
                          gap_after=summarize_gaps(rows, "gap_after"), heap=heap_trend(rows),
                          load_ms_max=max((r["load_ms"] or 0 for r in rows), default=None),
                          press_ms_max=max((r["press_ms"] for r in rows), default=None))


def run_bigcard(a, rig, res):
    emu = rig.emu
    folded = rig.emu.sym.find("_ZN10LoadSongUI20selectionShownFoldedEv")
    rig.play()
    rig.tm(1.0, "warm-up")
    res["warmup"] = rig.dma.take()
    res["heap_start"] = rig.heap()
    rounds = res["rounds"] = []
    actions = collections.defaultdict(list)  # kind -> [instructions]
    headers_seen = 0
    starts = ["DEFAULT", "LIVE set 60", "G075 mix 5", "S050X solo"]
    for rnd in range(a.rounds):
        r0, t0 = rig.folder_reads, time.time()
        direction = 1 if rnd % 2 == 0 else -1
        opened = rig.open_browser(starts[rnd % len(starts)])
        actions["open"].append(opened["instructions"])
        start = rig.browser_name()
        forced = folds = 0
        for _ in range(a.steps):
            s = rig.turn(direction)
            actions["turn"].append(s["instructions"])
            forced += s["forced"]
            if emu.call(folded, rig.v["loadSongUI"]) & 0xFF:
                headers_seen += 1
                if headers_seen % 3 == 0:
                    rig.wait_mode_none()
                    _, n = rig.button(SELECT_ENC, True)
                    rig.button(SELECT_ENC, False)
                    actions["fold out"].append(n)
                    rig.tm(0.05)
                    folds += 1
                    for _ in range(3):
                        s = rig.turn(1)
                        actions["turn"].append(s["instructions"])
                    rig.wait_mode_none()
                    _, n = rig.button(BACK, True)
                    rig.button(BACK, False)
                    actions["back (fold in)"].append(n)
                    rig.tm(0.05)
                    if rig.ui_name() != "loadSongUI":  # BACK closed it (the selection wasn't in the group)
                        rig.open_browser()
        for _ in range(15):
            s = rig.turn(-direction)
            actions["turn"].append(s["instructions"])
        end = rig.browser_name()
        closes = 0
        while rig.ui_name() == "loadSongUI" and closes < 4:
            rig.wait_mode_none()
            _, n = rig.button(BACK, True)
            rig.button(BACK, False)
            actions["back (close)"].append(n)
            rig.wait_mode_none(1.0)
            closes += 1
        rig.tm(0.3, "playing after closing the browser")
        g = rig.dma.take()
        r = dict(round=rnd + 1, start=start, end=end, folds=folds, forced_modes=forced,
                 folder_reads=rig.folder_reads - r0, scroll_in_s=opened["scroll_in_s"],
                 open_instructions=opened["instructions"], closed=rig.ui_name() != "loadSongUI", gap=g,
                 heap=rig.heap(), host_s=round(time.time() - t0, 1))
        rounds.append(r)
        log(f"  round {rnd + 1}: {start!r} .. {end!r}, {folds} groups folded out, {r['folder_reads']} folder reads, "
            f"open {opened['instructions'] / 1e6:.1f}M instr, scroll-in {opened['scroll_in_s']} s; gap {g['max_gap']} "
            f"(underruns {g['underrun_samples']}, over 64 {g['over_64']}); heap free internal "
            f"{r['heap']['internal_free']:,} SDRAM {r['heap']['sdram_free']:,}; {r['host_s']} s host")
    import numpy as np
    res["actions"] = {k: dict(count=len(v), median=float(np.median(v)), max=int(max(v)), mean=float(np.mean(v)))
                      for k, v in actions.items() if v}
    res["summary"] = dict(rounds=len(rounds), gap=summarize_gaps(rounds, "gap"), heap=heap_trend(rounds),
                          folder_reads=sum(r["folder_reads"] for r in rounds), actions=res["actions"])
    for k, v in res["actions"].items():
        log(f"  {k}: {v['count']} x, instructions median {v['median'] / 1e3:,.0f}k, max {v['max'] / 1e6:,.2f}M "
            f"({v['max'] / se.CPU_HZ * 1e3:.1f} ms at 400 MHz)")


def save_song(rig, path, what):
    """SaveSongUI's steps (as song_emu.save_while_playing()): createXMLFile(), Song::writeToFile(),
    closeFileAfterWriting(); (sha256, bytes, instructions, the DMA's numbers)."""
    emu, sym = rig.emu, rig.emu.sym
    at = STOP + 0x100
    strings = {}
    for name, text in (("path", path), ("begin", '<?xml version="1.0" encoding="UTF-8"?>\n<song\n'),
                       ("end", "\n</song>\n")):
        emu.uc.mem_write(at, text.encode() + b"\0")
        strings[name] = at
        at += len(text) + 4 & ~3
    storage, serializer, song = sym["storageManager"], sym["smSerializer"], rig.song()
    steps = [("createXMLFile", sym.find("_ZN14StorageManager13createXMLFile"), (storage, strings["path"], 1, 0), True),
             ("Song::writeToFile", sym["_ZN4Song11writeToFileER14StorageManager"], (song, storage), False),
             ("closeFileAfterWriting", sym.find("_ZN13XMLSerializer21closeFileAfterWriting"),
              (serializer, strings["path"], strings["begin"], strings["end"]), True)]
    rig.dma.take()
    total = 0
    for name, address, arguments, returns_error in steps:
        error, n = rig.action(f"{what}: {name}", address, *arguments, limit_s=20)
        total += n
        if returns_error and error:
            rig.problems.append(dict(kind="save error", what=what, detail=f"{name}: error {error}",
                                     at_s=round(emu.seconds(), 3)))
            raise Stop(what)
    xml = fat32.read_file(emu.sd_path, path)
    return xml, dict(sha256=hashlib.sha256(xml).hexdigest(), bytes=len(xml), instructions=total,
                     ms=round(total / se.CPU_HZ * 1e3, 2), gap=rig.dma.take())


def xml_diff(a, b, n=30):
    lines = list(difflib.unified_diff(a.decode(errors="replace").splitlines(), b.decode(errors="replace").splitlines(),
                                      "saved while playing", "loaded back, saved", n=0, lineterm=""))
    return len([x for x in lines if x[:1] in "+-" and x[:3] not in ("+++", "---")]), lines[:n]


def run_save(a, rig, res, out):
    rig.play()
    rig.tm(1.0, "warm-up")
    res["warmup"] = rig.dma.take()
    rows = res["saves"] = []
    xmls = []
    for i in range(a.saves):
        xml, r = save_song(rig, "SONGS/SAVETEST.XML", f"save {i + 1}")
        rig.tm(0.5, "playing between saves")
        r["gap_after"] = rig.dma.take()
        r["heap"] = rig.heap()
        rows.append(r)
        xmls.append(xml)
        log(f"  save {i + 1}: {r['bytes']:,} bytes, sha256 {r['sha256'][:16]}, {r['ms']} ms emulated; gap "
            f"{r['gap']['max_gap']} (underruns {r['gap']['underrun_samples']}), after {r['gap_after']['max_gap']}")
    open(os.path.join(out, "save_while_playing.xml"), "wb").write(xmls[-1])
    distinct = sorted({r["sha256"] for r in rows})
    back = rig.change_song("SAVETEST", 0.5)
    log(f"  loaded back: {'ok' if back['ok'] else 'NOT'} ({back['loaded']!r}), load {back['load_ms']} ms, gap "
        f"{back['gap_change']['max_gap']} (underruns {back['gap_change']['underrun_samples']})")
    xml2, r2 = save_song(rig, "SONGS/SAVETST2.XML", "save of the song loaded back")
    open(os.path.join(out, "loaded_back_saved.xml"), "wb").write(xml2)
    changed, diff = xml_diff(xmls[-1], xml2)
    res["round_trip"] = dict(load=back, save=r2, same=xml2 == xmls[-1], changed_lines=changed, diff=diff)
    res["summary"] = dict(saves=len(rows), distinct_files=len(distinct), gap=summarize_gaps(rows, "gap"),
                          gap_after=summarize_gaps(rows, "gap_after"), heap=heap_trend(rows),
                          save_ms_max=max(r["ms"] for r in rows), round_trip_same=xml2 == xmls[-1],
                          round_trip_changed_lines=changed, loaded_back=back["ok"])
    log(f"  {len(rows)} saves: {len(distinct)} distinct file(s); round trip: "
        + ("the same XML" if xml2 == xmls[-1] else f"{changed} lines differ:\n    " + "\n    ".join(diff[:16])))
    if len(distinct) > 1:
        c, d = xml_diff(xmls[0], xmls[-1])
        res["saves_diff"] = dict(changed_lines=c, diff=d)
        log("  first and last save differ:\n    " + "\n    ".join(d[:12]))


def menu_value(rig, menu):
    emu = rig.emu
    emu.w32(menu + MENU_VALUE_OFFSET, 0xFF)
    rig.action("menu read", emu.sym.find(MENU_READ), menu)
    return emu.u32(menu + MENU_VALUE_OFFSET)


def set_menus(rig, menus, value):
    """Settings opened, each item set to value (its writeCurrentValue(), as the select button does), the menu left
    (SoundEditor::exitCompletely(), which saves CommunityFeatures.XML)."""
    emu, sym = rig.emu, rig.emu.sym
    editor = sym["soundEditor"]
    rig.action("settings setup", sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], editor, 0, sym["settingsRootMenu"], 0)
    rig.action("settings open", sym["_Z6openUIP2UI"], editor)
    for menu in menus:
        emu.w32(menu + MENU_VALUE_OFFSET, value)
        rig.action("menu write", sym.find(MENU_WRITE), menu)
    rig.action("settings exit (save)", sym["_ZN11SoundEditor14exitCompletelyEv"], editor, limit_s=20)


def community_file(sd):
    try:
        xml = fat32.read_file(sd, "CommunityFeatures.XML").decode("ascii", "replace")
    except FileNotFoundError:
        return None, {}
    return xml, dict(re.findall(r'name="([^"]+)"\s+value="(-?\d+)"', xml))


def feature_values(rig):
    """runtimeFeatureSettings' values as the firmware holds them: each RuntimeFeatureSetting's xmlName and value."""
    emu = rig.emu
    try:
        size, count, name_off, value_off = se.gdb_values(emu, [
            "sizeof(RuntimeFeatureSetting)", "sizeof(runtimeFeatureSettings.settings) / sizeof(RuntimeFeatureSetting)",
            "(int)&((RuntimeFeatureSetting*)0)->xmlName._M_str", "(int)&((RuntimeFeatureSetting*)0)->value"])
        base, = se.gdb_values(emu, ["(int)&runtimeFeatureSettings.settings"])
    except SystemExit:
        return None
    out = {}
    for i in range(count):
        p = emu.u32(base + i * size + name_off)
        name = bytes(emu.uc.mem_read(p, 64)).split(b"\0")[0].decode(errors="replace") if p else f"#{i}"
        out[name or f"#{i}"] = emu.u32(base + i * size + value_off)
    return out


def run_settings(a, out, res, tools, build):
    sd = os.path.join(out, "settings.img")
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 2).encode()
    fat32.build(sd, files)
    steps = res["steps"] = []

    def boot(elf, t, label):
        rig = Rig(elf, t, build, sd, "instant", label)
        return rig

    def audio(rig, seconds=0.5):
        emu = rig.emu
        if GUARD_FLAG in emu.sym.by_name:
            emu.uc.mem_write(emu.sym[GUARD_FLAG], bytes([0xAA]))  # Neither 0 nor 1: the routine must write it
        rig.play()
        rig.tm(seconds, "playing")
        g = rig.dma.take()
        flags = {}
        if GUARD_FLAG in emu.sym.by_name:
            flags["FilterSet::crossingGuard"] = emu.u8(emu.sym[GUARD_FLAG])
        lim = emu.sym.by_name.get(LIMITER_FLAG) or next((v for k, v in emu.sym.by_name.items()
                                                        if "outputLimiterRunning" in k), None)
        if lim:
            flags["outputLimiterRunning"] = emu.u8(lim[0])
        rig.action("PLAY (stop)", rig.a["_ZN15PlaybackHandler17playButtonPressedEl"], 0)
        return g, flags

    def record(label, rig, **kw):
        xml, values = community_file(sd)
        s = dict(step=label, file=values, file_text=xml, problems=list(rig.problems), error_popups=rig.error_popups(),
                 invalid=len(rig.invalid), **kw)
        steps.append(s)
        log(f"  {label}: " + ", ".join(f"{k} {v}" for k, v in kw.items()) + f"; file {values or 'none'}"
            + (f"; PROBLEMS {rig.problems}" if rig.problems else "") + (f"; popups {s['error_popups']}"
                                                                         if s["error_popups"] else ""))
        return s

    new, old = a.elf, a.old_elf
    has_new = False
    rig = boot(new, tools, "new")
    sym = rig.emu.sym
    has_new = LIMITER_MENU in sym.by_name and GUARD_MENU in sym.by_name
    if has_new:
        menus = [sym[LIMITER_MENU], sym[GUARD_MENU]]
        before = [menu_value(rig, m) for m in menus]
        set_menus(rig, menus, 1)
        g, flags = audio(rig)
        record("new build: both switched on, the menu left", rig, menus_before=before, flags=flags, gap=g)
        rig = boot(new, tools, "new, restarted")
        menus = [rig.emu.sym[LIMITER_MENU], rig.emu.sym[GUARD_MENU]]
        after = [menu_value(rig, m) for m in menus]
        g, flags = audio(rig, 1.0)
        record("new build restarted on the card", rig, menus=after, flags=flags, gap=g,
               ok=after == [1, 1] and flags.get("FilterSet::crossingGuard") == 1
               and flags.get("outputLimiterRunning") == 1)
    else:
        record("new build: no output limiter / crossing guard menu items (skipped)", rig)
    if old:
        old_tools = a.old_tools or os.path.join(os.path.dirname(os.path.abspath(old)),
                                                "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
        file_before, values_before = community_file(sd)
        rig = boot(old, old_tools, "old")
        held = feature_values(rig)
        g, flags = audio(rig, 1.0)
        # Its own save of the settings (the menu left), with nothing changed
        set_menus(rig, [], 0)
        _, values_after = community_file(sd)
        kept = {k: values_after.get(k) for k in ("outputLimiter", "filterCrossingGuard") if k in values_before}
        record("old build on the new build's card", rig, held=held, gap=g,
               kept_when_it_saves=kept, dropped=[k for k in values_before if k not in values_after],
               changed={k: (values_before[k], values_after[k]) for k in values_before
                        if k in values_after and values_before[k] != values_after[k]})
        # Reference: the old build on a card without the file (its defaults)
        sd2 = os.path.join(out, "settings_ref.img")
        fat32.build(sd2, files)
        ref = Rig(old, old_tools, build, sd2, "instant", "old, no file")
        ref_held = feature_values(ref)
        os.remove(sd2)
        steps[-1]["same_as_without_the_file"] = held == ref_held
        steps[-1]["held_without_the_file"] = ref_held
        log(f"  old build: its settings the same as on a card without the file: {held == ref_held}")
        if has_new:
            rig = boot(new, tools, "new, after the old build")
            menus = [rig.emu.sym[LIMITER_MENU], rig.emu.sym[GUARD_MENU]]
            again = [menu_value(rig, m) for m in menus]
            record("new build after the old build saved the file", rig, menus=again, ok=again == [1, 1])
    os.remove(sd)
    res["summary"] = dict(steps=len(steps), problems=sum(len(s["problems"]) for s in steps),
                          error_popups=sum(len(s["error_popups"]) for s in steps),
                          ok=all(s.get("ok", True) for s in steps) and all(s.get("same_as_without_the_file", True)
                                                                            for s in steps))


def run_drone(a, rig, res):
    knob = rig.emu.sym.find("_ZN9DroneView16modEncoderActionEll")
    rig.play()
    rig.tm(1.0, "warm-up")
    root = rig.ui_name(root=True)
    if root != "sessionView":  # To Song view (the SONG button, as the user would)
        rig.button(SONG_VIEW, True)
        rig.button(SONG_VIEW, False)
        rig.tm(0.3)
    res["root_at_start"] = root
    res["warmup"] = rig.dma.take()
    res["heap_start"] = rig.heap()
    rows = res["cycles"] = []
    for i in range(a.cycles):
        _, n_open = rig.button(SCALE, True)
        rig.button(SCALE, False)
        opened = rig.ui_name(root=True)
        rig.tm(0.25, "drone view open")
        rig.action("drone knob +1", knob, rig.v["droneView"], 1, 1)
        rig.tm(0.05)
        rig.action("drone knob -1", knob, rig.v["droneView"], 1, -1)
        rig.tm(0.05)
        _, n_close = rig.button(BACK, True)
        rig.button(BACK, False)
        closed = rig.ui_name(root=True)
        rig.tm(0.25, "Song view")
        g = rig.dma.take()
        r = dict(cycle=i + 1, opened=opened, closed=closed, ok=opened == "droneView" and closed == "sessionView",
                 open_instructions=n_open, close_instructions=n_close, gap=g, heap=rig.heap())
        rows.append(r)
        log(f"  {i + 1:2d}: {opened} -> {closed}; open {n_open / 1e3:,.0f}k, close {n_close / 1e3:,.0f}k instr; gap "
            f"{g['max_gap']} (underruns {g['underrun_samples']}); heap free internal {r['heap']['internal_free']:,} "
            f"SDRAM {r['heap']['sdram_free']:,}")
    res["summary"] = dict(cycles=len(rows), ok=sum(r["ok"] for r in rows), gap=summarize_gaps(rows, "gap"),
                          heap=heap_trend(rows), open_instructions_max=max(r["open_instructions"] for r in rows),
                          close_instructions_max=max(r["close_instructions"] for r in rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("scenario", choices=["songchange", "bigcard", "save", "settings", "drone"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--tools")
    ap.add_argument("--build", default=os.environ.get("BLOCKCOUNT_DIR", HERE))
    ap.add_argument("--sd-latency", default="1000,42.67")
    ap.add_argument("--synths", type=int, default=5, help="synths in the heavy songs (make_sd.py --synths)")
    ap.add_argument("--changes", type=int, default=30)
    ap.add_argument("--order", default="DRIVE,DRONE,DEFAULT", help="songchange: the songs, in turn")
    ap.add_argument("--start", default="DEFAULT", help="songchange: the song loaded at boot (DEFAULT, DRIVE, DRONE)")
    ap.add_argument("--tempo-scale", type=float, default=1, help="songchange, save, drone: the songs' tempo times this "
                    "(120 BPM x 4: a 4-bar loop in 2 s, so the launch after a song change comes 4 times sooner)")
    ap.add_argument("--settle", type=float, default=1.0, help="seconds of the new song after each change")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--saves", type=int, default=10)
    ap.add_argument("--cycles", type=int, default=20)
    ap.add_argument("--old-elf", help="settings: the older build booted on the new build's card")
    ap.add_argument("--old-tools")
    ap.add_argument("--keep-image", action="store_true")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    res = dict(elf=a.elf, scenario=a.scenario, sd_latency=a.sd_latency, synths=a.synths, tempo_scale=a.tempo_scale,
               problems=[])
    t0 = time.time()
    rig = None
    image = os.path.join(out, f"{a.scenario}.img")
    status = 0
    try:
        if a.scenario == "settings":
            run_settings(a, out, res, tools, a.build)
            problems = [p for s in res["steps"] for p in s["problems"]]
            res["problems"] = problems
        else:
            if a.scenario == "bigcard":
                res["songs_on_card"] = build_bigcard(image, a.synths)
            else:
                res["songs"] = build_songchange_card(image, a.synths, a.start, a.tempo_scale)
            rig = Rig(a.elf, tools, a.build, image, a.sd_latency)
            res["boot"] = dict(song=rig.song_name(), root=rig.ui_name(root=True), heap=rig.heap())
            log(f"{a.scenario} on {a.elf}: booted, {rig.song_name()!r} in {res['boot']['root']}, the card "
                f"{a.sd_latency}")
            try:
                if a.scenario == "songchange":
                    run_songchange(a, rig, res)
                elif a.scenario == "bigcard":
                    run_bigcard(a, rig, res)
                elif a.scenario == "save":
                    run_save(a, rig, res, out)
                elif a.scenario == "drone":
                    run_drone(a, rig, res)
            except Stop as ex:
                log(f"  STOPPED at {ex}: {rig.problems[-1] if rig.problems else ''}")
            res["problems"] = rig.problems
    finally:
        if rig is not None:
            res["error_popups"] = rig.error_popups()
            res["popups"] = collections.Counter(p[3] for p in rig.popups).most_common(20)
            res["invalid_accesses"] = [dict(page=hex(k[0]), function=k[1], action=k[2], count=v)
                                       for k, v in rig.invalid.most_common(20)]
            res["emulated_s"] = round(rig.emu.seconds(), 2)
            res["dma_total"] = dict(max_gap=max(rig.dma.max_gap, rig.dma.phase["max_gap"]),
                                    underrun_samples=rig.dma.underruns + rig.dma.phase["underrun_samples"])
        res["host_s"] = round(time.time() - t0, 1)
        if os.path.exists(image) and not a.keep_image:
            os.remove(image)
        for suffix in (".json",):
            if os.path.exists(image + suffix):
                os.remove(image + suffix)
        json.dump(res, open(os.path.join(out, f"{a.scenario}.json"), "w"), indent=1, default=str)
    if res["problems"] or res.get("invalid_accesses"):
        status = 1
        for p in res["problems"]:
            log(f"  PROBLEM {p}")
        for p in res.get("invalid_accesses", []):
            log(f"  INVALID ACCESS {p}")
    if res.get("error_popups"):
        log(f"  error popups: {res['error_popups'][:10]}")
    log(f"{a.scenario}: {json.dumps(res.get('summary'), default=str)}")
    log(f"-> {os.path.join(out, a.scenario + '.json')} ({res['host_s']} s host)")
    sys.exit(status)


if __name__ == "__main__":
    main()
