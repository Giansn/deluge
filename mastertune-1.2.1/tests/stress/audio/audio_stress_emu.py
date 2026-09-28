#!/usr/bin/env python3
"""Audio stress test: the real firmware (deluge.elf) plays a song in real time in the emulator for many bars, as the
Deluge would, and what it sends to the codec, what it costs and what goes wrong are measured (run.sh runs the
scenarios on two builds).

Usage: audio_stress_emu.py <deluge.elf> <sd.img> <out dir> [--bars N] [--warmup-bars N] [--mode-storm]
                           [--song-storm] [--seed N] [--tools PREFIX] [--build DIR]

The harness is ../../song/song_emu.py (its docstring says what is real and what is modelled): boot, the song
SONGS/DEFAULT.XML from the card image (stress_sd.py), CommunityFeatures.XML read at boot as on the Deluge, --init-sounds
and a fixed random seed (both builds get the same input). Then playback, in real time (as song_emu.py --midi-timing's
play_realtime(), without the MIDI models):
- The SSI's output DMA reads one sample every 1/44100 s of emulated time (instructions at 400 MHz), round its buffer of
  128 samples (Dma, as song_emu.RealTimeDma): whenever the firmware is about to write to it (doSomeOutputting()), the
  gap (samples the DMA has read since the buffer was last full) is taken; 128 or more is an underrun (the DMA plays
  the old buffer again: a buzz), and the DMA goes on round the buffer as on the Deluge.
- AudioEngine::routine() every 16 samples (the task manager's target, AUDIO_TASK_INTERVAL) or at once when it is due
  again after a long call, the playback handler's routine as often, the cluster loading between; idle time passes
  without code running. So the firmware renders what is due (its minimum window, doubling, 128 at most), and culls
  voices and sets cpuDireness by its own measure of the routine's time (AudioEngine::routineTimeAverage, v17 on): the
  device's behaviour, nothing written from here.
- --mode-storm: every 256 to 768 samples (random, seeded; 2 to 6 windows of 128), every ModControllableAudio of the song
  gets a random LPF mode (12dB, 24dB, drive, SVF band, SVF notch, off), HPF mode (HP ladder, SVF band, SVF notch, off)
  and filter route (HPF to LPF, LPF to HPF, parallel): every synth, kit row, the kit, the audio track and the song,
  their fields (ModControllableAudio::lpfMode, hpfMode, filterRoute) written between routine() calls, as the menu does
  (offsets from the ELF's debug info, the toolchain's gdb; the objects found by walking Song::firstOutput, Output::next,
  Kit::firstDrum, Drum::next). --storm-objects N: only N of them, at random, each time (1: as a user would).
- --song-storm: every 16th note (5512.5 samples at 120 BPM) the song's own params (its UnpatchedParamSet: volume, pan,
  LPF and HPF frequency, resonance and morph, EQ bass and treble) jump to the extremes or random values (a third each,
  seeded), written between routine() calls: the song-level automation of stress_sd.py's automation song, which the
  firmware plays only in arrangement mode.

Measured over the bars after the warm-up (--warmup-bars, also played in real time):
- crash (an exception the emulator raises: undefined instruction, abort, a write through a null pointer), a memory
  access outside the RAM and the peripherals (lazily mapped by the harness: listed with the code that did it),
  freezeWithError() (its message), a hang (a routine() or task call over HANG_INSTRUCTIONS, 0.5 s of emulated time);
  the run stops there and reports what it has.
- DMA: underrun samples, the largest gap.
- CPU: routine()'s instructions per 128 samples rendered (consecutive calls grouped until they rendered 128 or more),
  mean, 99th percentile and max, in % of 400 MHz (1,161,000 per 128 samples); every call counts, also those that
  render nothing. cpuDireness (share of the samples at 14, where the drive ladder's oversampling is off, and the
  changes to and from 14), voices, AudioEngine::cullVoice() calls by type (soft: SOFT, SOFT_ALWAYS; force: FORCE,
  HARD) and caller.
- Output (what the firmware wrote to the codec, full scale 2^31): peak, clipped samples (at the rail, |x| >=
  0x7FFFFF00), clicks: samples where the 2nd difference of either channel is over 8 times its RMS over the previous
  10 ms (441 samples) and over -40 dBFS; also as events (clicks less than 10 ms apart are one), and for --mode-storm how
  many events start within 10 ms after a switch. stress.wav (16 bits).
- Heap: the GeneralMemoryAllocator's regions (song_emu.ram_usage(): internal RAM, SDRAM, the stealable SDRAM with the
  sample clusters) free and allocations after the warm-up and at the end, at the same place in the song's loop when the
  bars are a multiple of 4 (a leak shows as less free at the end), and after loading, before playback.
- Profile: the top 30 functions of the measured bars, instructions per 128 samples rendered (the block counts).
Results: <out>/result.json, stress.wav, calls.npz (per routine() call: emulated time and instructions, samples
rendered, gap at entry, voices, cpuDireness, culls); a line per result on stdout. Exit status 1 on a crash, a hang or a freeze.
"""
import argparse
import array
import collections
import json
import math
import os
import re
import struct
import sys
import time
import wave

import numpy as np
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "song"))
import fat32  # noqa: E402
import song_emu as se  # noqa: E402

AUDIO_TASK_INTERVAL = 16  # samples
HANG_INSTRUCTIONS = 200_000_000  # 0.5 s of emulated time for one call
SIXTEENTH = se.SAMPLE_RATE * 0.125  # 120 BPM
LPF_MODES = [0, 1, 2, 3, 4, 6]  # FilterMode: TRANSISTOR_12DB, _24DB, _24DB_DRIVE, SVF_BAND, SVF_NOTCH, OFF
HPF_MODES = [5, 3, 4, 6]  # HPLADDER, SVF_BAND, SVF_NOTCH, OFF
ROUTES = [0, 1, 2]  # HIGH_TO_LOW, LOW_TO_HIGH, PARALLEL
# UnpatchedGlobal / UnpatchedShared (modulation/params/param.h, the same in v17 and v18)
SONG_PARAMS = {"volume": 39, "pan": 31, "lpfFrequency": 32, "lpfResonance": 33, "lpfMorph": 34, "hpfFrequency": 35,
               "hpfResonance": 36, "hpfMorph": 37, "bass": 1, "treble": 2}
CLICK_RATIO, CLICK_FLOOR, CLICK_WINDOW = 8.0, 10 ** (-40 / 20), 441
SOFT, FORCE = ("SOFT", "SOFT_ALWAYS"), ("FORCE", "HARD")
PERIPHERALS = [(0xE8000000, 0xFD000000), (0x3FE00000, 0x40000000), (0x18000000, 0x20000000)]


class Stop(Exception):
    """The run ends here: a hang or freezeWithError()."""


def gdb_ints(emu, context, expressions):
    """Integers gdb prints for these expressions, with `context` listed first (gdb sees a class's type only in the
    compilation units it has expanded, as init_sounds() does)."""
    import subprocess
    cmd = [emu.tool_prefix + "gdb", "-batch", "-ex", f"list {context}"]
    for e in expressions:
        cmd += ["-ex", f"print {e}"]
    out = subprocess.run(cmd + [emu.elf], capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (-?\d+)$", out, re.M)]
    if len(values) != len(expressions):
        raise SystemExit(f"gdb ({context}): {expressions}:\n{out[-800:]}")
    return values


class Dma:
    """The SSI's output DMA in real time, as song_emu.RealTimeDma (emu.dma: getTxBufferCurrentPlace() reads where it
    is by the emulated time, one sample every 1/44100 s round the 128-sample buffer), but without a memory write hook
    (which slows every store the emulated code makes): only doSomeOutputting() writes the buffer, so at its entry
    (a code hook) the samples written since (AudioEngine::i2sTXBufferPos moved on, at most 127 at once: the firmware
    never fills the whole ring) are read into self.out, and the gap is taken there: how many samples the DMA has read
    since the buffer was last full. 128 or more is an underrun, the DMA playing the old buffer again. Unlike
    RealTimeDma it goes on after one as the Deluge does: the firmware sees the free space only modulo 128, so the sample
    it writes next plays when the DMA comes round to its slot again: `written` moves on by the whole buffers the DMA
    gained. Counted per lap: an underrun event and its stale samples (gap - 127)."""

    def __init__(self, emu):
        self.emu = emu
        self.cached = emu.sym["ssiTxBuffer"]
        self.pos_var = emu.sym["_ZN11AudioEngine14i2sTXBufferPosE"]
        self.last = self.slot()
        self.start_instructions = emu.now()
        self.start_position = self.last  # The buffer counts as full (of silence) at the start
        self.written = self.last + 128  # Absolute number of the next sample written (the DMA's count + 128)
        self.max_gap = 0
        self.over_64 = 0
        self.underruns = 0  # Stale samples
        self.underrun_events = 0
        self.underrun_at = []  # Output sample index of each, the first 200
        self.misplaced = 0
        self.out = array.array("i")
        emu.dma = self
        emu.intercept(emu.sym["_ZN11AudioEngine16doSomeOutputtingEv"], self.at_output)

    def slot(self):
        return ((self.emu.u32(self.pos_var) - self.cached) % se.UNCACHED_MIRROR_OFFSET) // 8 % 128

    def position(self):
        return self.start_position + (self.emu.now() - self.start_instructions) * se.SAMPLE_RATE // int(se.CPU_HZ)

    def gap(self):
        return self.position() + 128 - self.written

    def collect(self):
        pos = self.slot()
        n = (pos - self.last) % 128
        if n:
            buf = bytes(self.emu.uc.mem_read(self.cached, 128 * 8))
            first = self.last
            if first + n <= 128:
                self.out.frombytes(buf[first * 8:(first + n) * 8])
            else:
                self.out.frombytes(buf[first * 8:] + buf[:(first + n - 128) * 8])
            self.written += n
            self.last = pos

    def at_output(self, emu):
        self.collect()
        gap = self.gap()
        if gap > self.max_gap:
            self.max_gap = gap
        if gap > 64:
            self.over_64 += 1
        if gap >= 128:
            self.underrun_events += 1
            self.underruns += gap - 127
            if len(self.underrun_at) < 200:
                self.underrun_at.append(len(self.out) // 2)
            self.written += 128 * (gap // 128)
        return None


class Heap:
    """song_emu.ram_usage() with the offsets from gdb once."""

    def __init__(self, emu):
        self.emu = emu
        self.offsets = se.gdb_values(emu, ["sizeof(MemoryRegion)", "(int)&((MemoryRegion*)0)->emptySpaces",
                                           "(int)&((ResizeableArray*)0)->memory",
                                           "(int)&((ResizeableArray*)0)->numElements",
                                           "(int)&((ResizeableArray*)0)->memorySize",
                                           "(int)&((ResizeableArray*)0)->memoryStart",
                                           "(int)&((ResizeableArray*)0)->elementSize"])
        self.base = emu.sym["_ZZN22GeneralMemoryAllocator3getEvE22generalMemoryAllocator"]

    def __call__(self):
        region_size, empty, memory, count, msize, mstart, esize = self.offsets
        u32 = self.emu.u32
        out = {}
        for r, name in enumerate(("stealable", "internal", "external")):  # MEMORY_REGION_*
            a = self.base + r * region_size
            start, end, allocations = struct.unpack("<3I", self.emu.uc.mem_read(a, 12))
            arr = a + empty
            mem, n, size, first, es = (u32(arr + o) for o in (memory, count, msize, mstart, esize))
            free = sum(u32(mem + ((first + i) % max(size, 1)) * es) for i in range(n))
            out[name] = dict(size=end - start, free=free, empty_spaces=n)
        return out


class Objects:
    """Every ModControllableAudio of the song: (what, address), and the offsets of lpfMode, hpfMode, filterRoute."""

    def __init__(self, emu):
        (first_output, global_effectable, mca_in_ge, output_next, output_type, lpf, hpf, route, kit_output, kit_mca,
         first_drum, drum_next, drum_type, sd_drum, sd_mca, ao_output, ao_mca, song_pm) = gdb_ints(emu, "Song::Song", [
             "(int)&((Song*)0)->firstOutput", "(int)&((Song*)0)->globalEffectable",
             "(int)(ModControllableAudio*)(GlobalEffectable*)4096 - 4096", "(int)&((Output*)0)->next",
             "(int)&((Output*)0)->type", "(int)&((ModControllableAudio*)0)->lpfMode",
             "(int)&((ModControllableAudio*)0)->hpfMode", "(int)&((ModControllableAudio*)0)->filterRoute",
             "(int)(Output*)(Kit*)4096 - 4096", "(int)(ModControllableAudio*)(Kit*)4096 - 4096",
             "(int)&((Kit*)0)->firstDrum", "(int)&((Drum*)0)->next", "(int)&((Drum*)0)->type",
             "(int)(Drum*)(SoundDrum*)4096 - 4096", "(int)(ModControllableAudio*)(SoundDrum*)4096 - 4096",
             "(int)(Output*)(AudioOutput*)4096 - 4096", "(int)(ModControllableAudio*)(AudioOutput*)4096 - 4096",
             "(int)&((Song*)0)->paramManager"])
        si_output, si_mca = gdb_ints(emu, "SoundInstrument::SoundInstrument", [
            "(int)(Output*)(SoundInstrument*)4096 - 4096", "(int)(ModControllableAudio*)(SoundInstrument*)4096 - 4096"])
        (self.summaries, self.collection, self.params, self.autoparam_size, self.current_value) = gdb_ints(
            emu, "Song::Song", ["(int)&((ParamManager*)0)->summaries",
                                "(int)&((ParamCollectionSummary*)0)->paramCollection",
                                "(int)&((ParamSet*)0)->params", "sizeof(AutoParam)",
                                "(int)&((AutoParam*)0)->currentValue"])
        self.emu = emu
        self.fields = (lpf, hpf, route)
        song = emu.u32(emu.sym["currentSong"])
        self.song = song
        self.song_pm = song + song_pm
        self.list = [("song", song + global_effectable + mca_in_ge)]
        out = emu.u32(song + first_output)
        while out:
            kind = emu.u8(out + output_type)
            if kind == 0:  # SYNTH
                self.list.append(("synth", out - si_output + si_mca))
            elif kind == 1:  # KIT
                kit = out - kit_output
                self.list.append(("kit", kit + kit_mca))
                d = emu.u32(kit + first_drum)
                while d:
                    if emu.u8(d + drum_type) == 0:  # SOUND
                        self.list.append(("kit row", d - sd_drum + sd_mca))
                    d = emu.u32(d + drum_next)
            elif kind == 4:  # AUDIO
                self.list.append(("audio track", out - ao_output + ao_mca))
            out = emu.u32(out + output_next)

    def modes(self):
        return [tuple(self.emu.u32(a + f) for f in self.fields) for _, a in self.list]

    def set(self, address, lpf, hpf, route):
        for f, v in zip(self.fields, (lpf, hpf, route)):
            self.emu.w32(address + f, v)

    def song_param_address(self, p):
        """The song's unpatched param p: its AutoParam's currentValue."""
        collection = self.emu.u32(self.song_pm + self.summaries + self.collection)
        return self.emu.u32(collection + self.params) + p * self.autoparam_size + self.current_value


class Storms:
    """--mode-storm and --song-storm (see the docstring): due by the samples rendered since playback started."""

    def __init__(self, emu, objects, mode_storm, song_storm, seed, timer0, storm_objects=0):
        self.emu = emu
        self.storm_objects = storm_objects
        self.objects = objects
        self.rng = np.random.default_rng(seed)
        self.timer_address = emu.sym["_ZN11AudioEngine16audioSampleTimerE"]
        self.timer0 = timer0
        self.mode_storm = mode_storm
        self.song_storm = song_storm
        self.next_mode = 512 if mode_storm else None
        self.next_song_k = 1 if song_storm else None
        self.mode_events = []  # Sample index (rendered since playback started) of each switch
        self.song_events = 0
        self.song_addresses = {n: objects.song_param_address(p) for n, p in SONG_PARAMS.items()} if song_storm else {}

    def value(self):
        k = self.rng.integers(3)
        return -2 ** 31 if k == 0 else 2 ** 31 - 1 if k == 1 else int(self.rng.integers(-2 ** 31, 2 ** 31 - 1))

    def after_call(self):
        now = (self.emu.u32(self.timer_address) - self.timer0) & 0xFFFFFFFF
        if self.next_mode is not None and now >= self.next_mode:
            targets = self.objects.list
            if self.storm_objects:  # --storm-objects N: only N of them, at random
                targets = [targets[i] for i in self.rng.choice(len(targets), self.storm_objects, replace=False)]
            for _, address in targets:
                self.objects.set(address, int(self.rng.choice(LPF_MODES)), int(self.rng.choice(HPF_MODES)),
                                 int(self.rng.choice(ROUTES)))
            self.mode_events.append(now)
            self.next_mode = now + int(self.rng.integers(256, 769))
        if self.next_song_k is not None and now >= round(self.next_song_k * SIXTEENTH):
            for name, address in self.song_addresses.items():
                self.emu.w32(address, self.value())
            self.song_events += 1
            self.next_song_k += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("sd")
    ap.add_argument("out")
    ap.add_argument("--tools")
    ap.add_argument("--build", default=os.environ.get("BLOCKCOUNT_DIR", HERE), help="directory with blockcount.so")
    ap.add_argument("--bars", type=float, default=4)
    ap.add_argument("--warmup-bars", type=float, default=1)
    ap.add_argument("--mode-storm", action="store_true")
    ap.add_argument("--song-storm", action="store_true")
    ap.add_argument("--storm-objects", type=int, default=0,
                    help="--mode-storm switches only this many objects (at random) each time, e.g. 1 as a user would "
                         "(default: all of them at once)")
    ap.add_argument("--seed", type=int, default=1, help="the storms' random values (and the firmware's jcong)")
    a = ap.parse_args()
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(a.out, exist_ok=True)
    log = lambda s: print(s, flush=True)  # noqa: E731
    result = dict(elf=os.path.abspath(a.elf), sd=os.path.basename(a.sd), bars=a.bars, warmup_bars=a.warmup_bars,
                  mode_storm=a.mode_storm, song_storm=a.song_storm, status="ok")

    # Memory the harness maps lazily (song_emu.Emulator.on_unmapped): after playback starts, anything outside the
    # peripherals is an invalid access
    invalid = []
    playing = [False]
    original_on_unmapped = se.Emulator.on_unmapped

    def on_unmapped(self, uc, access, address, size, value, user):
        if playing[0] and not any(lo <= address < hi for lo, hi in PERIPHERALS) and len(invalid) < 50:
            invalid.append(dict(address=hex(address), access=access, pc=self.sym.name_at(uc.reg_read(UC_ARM_REG_PC))))
        return original_on_unmapped(self, uc, access, address, size, value, user)
    se.Emulator.on_unmapped = on_unmapped

    emu = se.Emulator(a.elf, a.sd, tools, a.build, log)
    se.setup_sd(emu)
    se.init_sounds(emu)
    se.boot(emu)
    emu.w32(emu.sym["jcong"], a.seed)
    freezes = []

    def on_freeze(e):
        text = bytes(e.uc.mem_read(e.uc.reg_read(UC_ARM_REG_R0), 32)).split(b"\0")[0].decode("ascii", "replace")
        freezes.append(dict(message=text, at=e.sym.name_at(e.uc.reg_read(UC_ARM_REG_PC))))
        e.stop()
        return None
    for address in sorted({v[0] & ~1 for n, v in emu.sym.by_name.items() if "freezeWithError" in n}):
        emu.intercept(address, on_freeze)
    se.load_startup_song(emu)
    xml = fat32.read_file(a.sd, "SONGS/DEFAULT.XML").decode(errors="replace")
    try:
        cf = fat32.read_file(a.sd, "CommunityFeatures.XML").decode(errors="replace")
    except FileNotFoundError:
        cf = ""
    result["community_features"] = dict(re.findall(r'name="(\w+)"\s+value="(\d+)"', cf))
    heap = Heap(emu)
    result["heap_loaded"] = heap()
    objects = Objects(emu)
    result["objects"] = collections.Counter(w for w, _ in objects.list)
    modes = objects.modes()
    log(f"objects: {dict(result['objects'])}; their modes (lpf, hpf, route): {collections.Counter(modes)}")
    if len({m for m in modes if m[0] not in LPF_MODES or m[1] not in HPF_MODES or m[2] not in ROUTES}):
        raise SystemExit(f"the ModControllableAudio offsets look wrong: {modes}")
    if a.song_storm:
        m = re.search(r'<songParams\b[^>]*?\svolume="0x([0-9A-Fa-f]{8})', xml, re.S)
        got = emu.u32(objects.song_param_address(SONG_PARAMS["volume"]))
        if not m or got != int(m.group(1), 16):
            raise SystemExit(f"the song's volume param reads {got:#x}, the XML says {m and m.group(1)}: offsets wrong?")

    player = se.Player(emu, culling=True)
    sym = emu.sym
    timer_address = sym["_ZN11AudioEngine16audioSampleTimerE"]
    player.start()
    timer0 = emu.u32(timer_address)
    storms = Storms(emu, objects, a.mode_storm, a.song_storm, a.seed, timer0, a.storm_objects)
    limiter_running = sym.by_name.get("_ZN11AudioEngine20outputLimiterRunningE")
    guard = sym.by_name.get("_ZN6deluge3dsp6filter9FilterSet13crossingGuardE")
    dma = Dma(emu)
    emu.uc.ctl_flush_tb()
    playing[0] = True
    per = AUDIO_TASK_INTERVAL * se.CPU_HZ / se.SAMPLE_RATE
    stop = se.STOP

    def guarded(address):
        emu.bc.bc_set_deadline(emu.bc.bc_total() + HANG_INSTRUCTIONS)
        emu.call(address)
        emu.bc.bc_set_deadline(2 ** 64 - 1)
        pc = emu.uc.reg_read(UC_ARM_REG_PC) & ~1
        if freezes:
            raise Stop(f"freezeWithError: {freezes[-1]}")
        if pc != stop:
            raise Stop(f"hang: over {HANG_INSTRUCTIONS / 1e6:.0f}M instructions in {emu.sym.name_at(address)}, at "
                       f"{emu.sym.name_at(pc)}")

    calls = []  # (emulated time, instructions, samples rendered, gap at entry, voices, cpuDireness, culls)
    direness_address = player.direness

    def play(samples):
        next_audio = next_playback = emu.now()
        start = dma.position()
        while dma.position() - start < samples:
            ran = False
            if emu.now() >= next_audio:
                next_audio = emu.now() + per
                player.window_culls = 0
                t0, before, timer, gap = emu.now(), emu.bc.bc_total(), emu.u32(timer_address), dma.gap()
                guarded(player.routine)
                dma.collect()
                calls.append((t0, emu.bc.bc_total() - before, (emu.u32(timer_address) - timer) & 0xFFFFFFFF, gap,
                              player.voices(), struct.unpack("<i", emu.uc.mem_read(direness_address, 4))[0],
                              player.window_culls))
                storms.after_call()
                ran = True
            if emu.now() >= next_playback:
                next_playback = emu.now() + per
                guarded(player.playback_routine)
                ran = True
            guarded(player.load_clusters)
            if not ran:
                target = min(next_audio, next_playback)
                if target > emu.now():
                    emu.idle += int(math.ceil(target - emu.now()))

    t = time.time()
    marks = {}
    try:
        play(int(a.warmup_bars * se.BAR))
        dma.collect()
        marks = dict(calls=len(calls), out=len(dma.out) // 2, underruns=dma.underruns, events_dma=dma.underrun_events,
                     culls=collections.Counter(
            player.culls), timer=(emu.u32(timer_address) - timer0) & 0xFFFFFFFF, events=len(storms.mode_events),
            max_gap_warmup=dma.max_gap)
        result["heap_start"] = heap()
        emu.bc.bc_reset_counts()  # The profile: the measured bars only
        dma.max_gap = 0
        log(f"warm-up: {a.warmup_bars:g} bar(s) ({time.time() - t:.0f} s), max gap {marks['max_gap_warmup']}, "
            f"underruns {dma.underrun_events}")
        t = time.time()
        play(int(a.bars * se.BAR))
        result["heap_end"] = heap()
    except (Stop, SystemExit) as e:
        result["status"] = "crash" if isinstance(e, SystemExit) else ("freeze" if freezes else "hang")
        result["stopped"] = str(e)
        result["stopped_at_sample"] = (emu.u32(timer_address) - timer0) & 0xFFFFFFFF
        log(f"STOPPED: {e}")
        dma.collect()
    elapsed = time.time() - t
    playing[0] = False
    if not marks:
        marks = dict(calls=0, out=0, underruns=0, events_dma=0, culls=collections.Counter(), timer=0, events=0,
                     max_gap_warmup=0)
    result["seconds"] = elapsed
    result["invalid_accesses"] = invalid
    result["freezes"] = freezes
    result["limiter_running"] = bool(emu.u8(limiter_running[0])) if limiter_running else None
    result["crossing_guard"] = bool(emu.u8(guard[0])) if guard else None

    # --- the numbers
    c = np.array(calls[marks["calls"]:], dtype=np.float64).reshape(-1, 7)
    result["dma"] = dict(underrun_events=dma.underrun_events - marks["events_dma"],
                         underrun_samples=dma.underruns - marks["underruns"], max_gap=dma.max_gap,
                         warmup_underrun_events=marks["events_dma"], warmup_underrun_samples=marks["underruns"],
                         warmup_max_gap=marks["max_gap_warmup"], misplaced=dma.misplaced, over_64=dma.over_64,
                         underrun_at_s=[round((i - marks["out"]) / se.SAMPLE_RATE, 4) for i in dma.underrun_at
                                        if i >= marks["out"]][:40])
    if len(c):
        instr, samples = c[:, 1], c[:, 2]
        groups, acc_i, acc_s = [], 0.0, 0.0
        for i, s in zip(instr, samples):
            acc_i += i
            acc_s += s
            if acc_s >= 128:
                groups.append(acc_i / acc_s * 128)
                acc_i = acc_s = 0.0
        groups = np.array(groups) / se.CYCLES_PER_BLOCK * 100
        rendering = samples > 0
        direness = c[:, 5]
        w = samples / max(samples.sum(), 1)
        at14 = direness >= 14
        result["cpu"] = dict(mean=float(instr.sum() / samples.sum() * 128 / se.CYCLES_PER_BLOCK * 100),
                             p99=float(np.percentile(groups, 99)), max=float(groups.max()),
                             p50=float(np.percentile(groups, 50)),
                             calls=len(c), rendering_calls=int(rendering.sum()),
                             mean_window=float(samples[rendering].mean()) if rendering.any() else 0,
                             max_call_instructions=int(instr.max()))
        result["cpu_direness"] = dict(mean=float(w @ direness), share_at_14=float(w @ at14),
                                      changes_to_14=int(np.sum(at14[1:] & ~at14[:-1])),
                                      changes_from_14=int(np.sum(~at14[1:] & at14[:-1])))
        result["voices"] = dict(mean=float(w @ c[:, 4]), max=int(c[:, 4].max()))
    culls = collections.Counter({k: v - marks["culls"].get(k, 0) for k, v in player.culls.items()
                                 if v - marks["culls"].get(k, 0)})
    by_type = collections.Counter()
    for k, v in culls.items():
        by_type[k.split()[-1]] += v
    result["culls"] = dict(soft=sum(by_type[t] for t in SOFT), force=sum(by_type[t] for t in FORCE),
                           by_type=dict(by_type), by_caller=dict(culls),
                           warmup=sum(marks["culls"].values()))

    # The profile of the measured bars (song_emu.profile_by_function(): the block counts): the top functions in
    # instructions per 128 samples rendered, and the per-call log (calls.npz: emulated time in instructions,
    # instructions, samples rendered, gap at entry, voices, cpuDireness, culls)
    rendered = float(c[:, 2].sum()) if len(c) else 0.0
    if rendered:
        functions = se.profile_by_function(emu)
        result["top_functions"] = [(n, round(v / rendered * 128)) for n, v in functions.most_common(30)]
    np.savez_compressed(os.path.join(a.out, "calls.npz"), calls=np.array(calls, dtype=np.int64).reshape(-1, 7),
                        measured_from=marks["calls"])
    x = np.frombuffer(dma.out, dtype="<i4").reshape(-1, 2)
    wav_path = os.path.join(a.out, "stress.wav")
    with wave.open(wav_path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(se.SAMPLE_RATE)
        f.writeframes((x >> 16).astype("<i2").tobytes())
    m = x[marks["out"]:].astype(np.float64) / 2 ** 31
    if len(m) > CLICK_WINDOW + 2:
        peak = float(np.abs(m).max())
        clipped = int(np.sum((np.abs(x[marks["out"]:].astype(np.int64)) >= 0x7FFFFF00).any(axis=1)))
        flagged = np.zeros(len(m), bool)
        for ch in range(2):
            d2 = np.zeros(len(m))
            d2[2:] = m[2:, ch] - 2 * m[1:-1, ch] + m[:-2, ch]
            cs = np.concatenate([[0.0], np.cumsum(d2 ** 2)])
            rms = np.full(len(m), np.inf)
            n = np.arange(CLICK_WINDOW + 2, len(m))
            rms[n] = np.sqrt((cs[n] - cs[n - CLICK_WINDOW]) / CLICK_WINDOW)
            flagged |= (np.abs(d2) > CLICK_RATIO * rms) & (np.abs(d2) > CLICK_FLOOR)
        idx = np.flatnonzero(flagged)
        events = idx[np.concatenate([[True], np.diff(idx) > CLICK_WINDOW])] if len(idx) else idx
        out = dict(peak_dbfs=20 * math.log10(peak) if peak else None, clipped_samples=clipped,
                   rms_dbfs=float(10 * np.log10(np.mean(m ** 2) + 1e-30)),
                   click_samples=int(len(idx)), click_events=int(len(events)),
                   click_event_times_s=[round(float(i) / se.SAMPLE_RATE, 4) for i in events[:40]])
        # Events within 10 ms after cpuDireness went to or from 14 (the drive ladder's oversampling switched off or on)
        allc = np.array(calls, dtype=np.float64).reshape(-1, 7)
        if len(allc) > 1:
            at14_all = allc[:, 5] >= 14
            first_sample = np.cumsum(allc[:, 2]) - allc[:, 2]  # Rendered before each call, since playback started
            ch = np.flatnonzero(at14_all[1:] != at14_all[:-1]) + 1
            tr = first_sample[ch].astype(np.int64) - marks["out"]
            tr = tr[tr >= 0]
            near14 = 0
            if len(tr) and len(events):
                j = np.searchsorted(tr, events, side="right") - 1
                near14 = int(np.sum((j >= 0) & (events - tr[np.maximum(j, 0)] <= CLICK_WINDOW)))
            out["direness_14_changes"] = int(len(tr))
            out["click_events_within_10ms_after_direness_14_change"] = near14
        if a.mode_storm:
            # Events within 10 ms after a switch (the output's sample index = samples rendered since playback started,
            # less those before the measurement)
            sw = np.array(storms.mode_events[marks["events"]:], dtype=np.int64) - marks["out"]
            near = 0
            if len(sw) and len(events):
                j = np.searchsorted(sw, events, side="right") - 1
                near = int(np.sum((j >= 0) & (events - sw[np.maximum(j, 0)] <= CLICK_WINDOW)))
            out["switches"] = int(len(sw))
            out["click_events_within_10ms_after_switch"] = near
        result["output"] = out
    result["mode_switches"] = len(storms.mode_events)
    result["song_param_jumps"] = storms.song_events
    json.dump(result, open(os.path.join(a.out, "result.json"), "w"), indent=1, default=str)

    # --- the report
    def hfree(h):
        return f"{h['internal']['free']:,} / {h['external']['free']:,}" if h else "-"
    log(f"status: {result['status']}" + (f" ({result.get('stopped')})" if result["status"] != "ok" else "")
        + f"; invalid accesses: {len(invalid)}; limiter running: {result['limiter_running']}, crossing guard: "
          f"{result['crossing_guard']}; {elapsed:.0f} s")
    if "cpu" in result:
        cpu, d = result["cpu"], result["cpu_direness"]
        log(f"CPU per 128 samples: mean {cpu['mean']:.1f} %, p50 {cpu['p50']:.1f} %, p99 {cpu['p99']:.1f} %, max "
            f"{cpu['max']:.1f} %; {cpu['calls']} routine() calls ({cpu['rendering_calls']} rendering, mean window "
            f"{cpu['mean_window']:.1f}); cpuDireness mean {d['mean']:.1f}, at 14 {d['share_at_14'] * 100:.1f} % "
            f"({d['changes_to_14']} changes to 14); voices mean {result['voices']['mean']:.1f}, max "
            f"{result['voices']['max']}")
    cu = result["culls"]
    log(f"culls: soft {cu['soft']}, force {cu['force']} ({cu['by_caller'] or 'none'}; warm-up {cu['warmup']})")
    dm = result["dma"]
    log(f"DMA: underruns {dm['underrun_events']} ({dm['underrun_samples']} stale samples), max gap {dm['max_gap']} "
        f"(warm-up: {dm['warmup_underrun_events']}, {dm['warmup_underrun_samples']}, {dm['warmup_max_gap']})")
    if "output" in result:
        o = result["output"]
        log(f"output: peak {o['peak_dbfs']:.2f} dBFS, RMS {o['rms_dbfs']:.1f} dBFS, clipped {o['clipped_samples']}, "
            f"clicks {o['click_samples']} samples / {o['click_events']} events"
            + (f", {o['click_events_within_10ms_after_switch']} within 10 ms after one of {o['switches']} switches"
               if a.mode_storm else "")
            + (f", {o.get('click_events_within_10ms_after_direness_14_change', 0)} within 10 ms after one of "
               f"{o.get('direness_14_changes', 0)} changes of cpuDireness to or from 14"))
    log(f"heap free internal / external: loaded {hfree(result['heap_loaded'])}, after warm-up "
        f"{hfree(result.get('heap_start'))}, end {hfree(result.get('heap_end'))}")
    if a.song_storm:
        log(f"song params jumped {storms.song_events} times")
    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
