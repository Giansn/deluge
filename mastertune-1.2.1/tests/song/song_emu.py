#!/usr/bin/env python3
"""Runs the real Deluge firmware (deluge.elf) in unicorn and measures what a whole song costs its Cortex-A9.

Usage: song_emu.py <deluge.elf> <sd.img> <out dir> [--warmup-bars N] [--bars N] [--culling] [--write-back song.xml]

Not a sum of unit benchmarks: the firmware's own code boots, loads the song from the SD card image and renders it
through AudioEngine::routine(), with everything the Deluge runs for it (Song, clips, Sounds, voices, kit, audio clip,
effects, reverb, sidechain, drone, the playback handler's ticks).

- Boot: the ELF's segments where the bootloader puts them, then resetprg() (the static constructors, main(),
  deluge_main(): memory, functionsInit, AudioEngine::init, settings, the SD card, the blank song, the task list). The
  hardware is plain memory, lazily mapped, except what the firmware waits on or counts with (models below): the OSTM
  and MTU2 timers count emulated time (instructions at 400 MHz), the DMA channels have finished (or, receiving, got
  nothing), SPI and the SPI flash (erased: default settings) answer at once. The SD card's sectors are an image file:
  FatFS runs as it is, over sd_read_sect()/disk_write(), and mount_volume() skips the card's initialisation (the SDHI
  driver). It stops where deluge_main() would start the task manager.
- The song: setupStartupSong() in the template mode loads SONGS/DEFAULT.XML, as at boot (the task manager's tasks
  run while it yields, so the cluster loading streams the samples in).
- Playback: PlaybackHandler::playButtonPressed() (the internal clock), then AudioEngine::routine() called here, once
  per window: the SSI's DMA position shows 127 free samples until the song renders, and none after, so each call
  renders exactly one window of 128 samples, cut short only where the song's clock ticks (as on the Deluge; one
  window in 44 here). Between windows it runs the other tasks that matter while playing: the playback handler's
  routine and the SD card's cluster loading. The task list is emptied first (currentID 0).
- Culling and cpuDireness: setDireness() (inlined in routine()) takes both from getLastRunTimeforCurrentTask() (also
  inlined): the audio task's durationStats.average in seconds, times 44100 = dspTime in samples; cpuDireness =
  dspTime - 49 (at most 14) from 50 samples on, soft culling from 80, hard culling from 112 (+20 per voice started in
  the last render, at most 4). Without --culling that average stays 0 (the emptied task list): no culling for CPU load
  and cpuDireness 0, i.e. the song's full demand. With --culling it is, before each routine() call, what the task
  manager would hold: the running average (average + runtime) / 2 over the previous routine() calls' emulated
  durations (instructions / 400 MHz), as TaskManager::runTask() updates it: the device's behaviour. Either way a Sound
  over its voice limit steals, as on the Deluge; cullVoice() calls are counted by caller and type.
- Per window it records the cpuDireness, the culls and the voices per Sound (AudioEngine::activeVoices, Sound* ->
  count); the Sounds are named by their String name (Output::name, SoundDrum::name), found near the Sound* pointer
  and matched against the names in the song's XML.
- Counting: per translated block, in C (blockcount.c): exact instruction counts of the firmware's machine code, per
  window and per function. CPU % = instructions per 128 samples / 1,161,000 (400 MHz at 1 instruction per cycle).
"""
import argparse
import bisect
import collections
import ctypes
import json
import math
import os
import re
import struct
import subprocess
import sys
import time

import numpy as np
import unicorn
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_MEM_UNMAPPED, UC_MODE_ARM, UC_PROT_ALL, Uc, UcError
from unicorn.arm_const import (UC_ARM_REG_C1_C0_2, UC_ARM_REG_CPSR, UC_ARM_REG_FPEXC, UC_ARM_REG_LR, UC_ARM_REG_PC,
                               UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP,
                               UC_CPU_ARM_CORTEX_A9)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fat32  # noqa: E402

INTERNAL_RAM, INTERNAL_RAM_SIZE = 0x20000000, 0x300000
SDRAM, SDRAM_SIZE = 0x0C000000, 0x04000000
UNCACHED_MIRROR_OFFSET = 0x40000000
STOP = 0x7FFF0000  # Return address of the calls made from here: nothing is executed there
PROGRAM_STACK_TOP = 0x20300000

CPU_HZ = 400e6
PERIPHERAL_HZ = 33.33e6
SAMPLE_RATE = 44100
CYCLES_PER_BLOCK = CPU_HZ * 128 / SAMPLE_RATE  # 1,161,000
BAR = SAMPLE_RATE * 2  # 4 beats at 120 BPM

OSTM0 = 0xFCFEC000
MTU2 = 0xFCFF0000
MTU_TCNT = {0x306: 64, 0x386: 1, 0x006: 64, 0x210: 64, 0x212: 1024}  # TCNT_0..4 offset: prescaler
DMAC = 0xE8200000
SSI_TX_DMA_CHANNEL = 6
RSPI0 = 0xE800C800
SPIBSC = 0x3FEFA000


def dmac_channel_base(n):
    return DMAC + n * 64 + (0x200 if n >= 8 else 0)


class Symbols:
    def __init__(self, elf, tool_prefix):
        def nm(*flags):
            return subprocess.run([tool_prefix + "nm", "-S", "-n", "--defined-only", *flags, elf], capture_output=True,
                                  text=True, check=True).stdout.splitlines()
        self.by_name = {}
        self.functions = []  # (start, end, demangled name)
        for line, dline in zip(nm(), nm("-C")):
            parts, dparts = line.split(maxsplit=3), dline.split(maxsplit=3)
            if len(parts) != 4 or len(dparts) != 4:
                continue
            address, size, kind = int(parts[0], 16), int(parts[1], 16), parts[2]
            self.by_name[parts[3]] = (address, size)
            self.by_name.setdefault(dparts[3], (address, size))
            if kind in "TtWw" and size:
                self.functions.append((address & ~1, (address & ~1) + size, dparts[3]))
        self.functions.sort()
        self.starts = [f[0] for f in self.functions]

    def __getitem__(self, name):
        return self.by_name[name][0]

    def find(self, prefix):
        """The one function or variable whose name starts with prefix (LTO adds suffixes like .constprop.0)."""
        found = sorted({v for k, v in self.by_name.items() if k.startswith(prefix)})
        if len(found) != 1:
            raise KeyError(f"{len(found)} symbols start with {prefix!r}")
        return found[0][0]

    def function_at(self, address):
        i = bisect.bisect_right(self.starts, address & ~1) - 1
        if i >= 0 and address < self.functions[i][1]:
            return self.functions[i]
        return None

    def name_at(self, address):
        f = self.function_at(address)
        return f"{f[2]}+{address - f[0]:#x}" if f else f"{address:#x}"


class Emulator:
    def __init__(self, elf, sd_image, tool_prefix, build_dir, log):
        self.elf = elf
        self.log = log
        self.tool_prefix = tool_prefix
        self.sym = Symbols(elf, tool_prefix)
        data = open(elf, "rb").read()
        _, phoff, _, _, _, phentsize, phnum = struct.unpack_from("<IIIIHHH", data, 24)
        self.segments = []  # (load address, content)
        for i in range(phnum):
            p_type, p_offset, _, p_paddr, p_filesz, _ = struct.unpack_from("<IIIIII", data, phoff + i * phentsize)
            if p_type == 1 and p_filesz:
                self.segments.append((p_paddr, data[p_offset:p_offset + p_filesz]))

        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)
        # Internal RAM and SDRAM, each also at its uncached mirror (the same host memory)
        self.iram = ctypes.create_string_buffer(INTERNAL_RAM_SIZE)
        self.sdram = ctypes.create_string_buffer(SDRAM_SIZE)
        for base, buf, size in ((INTERNAL_RAM, self.iram, INTERNAL_RAM_SIZE), (SDRAM, self.sdram, SDRAM_SIZE)):
            uc.mem_map_ptr(base, size, UC_PROT_ALL, ctypes.addressof(buf))
            uc.mem_map_ptr(base + UNCACHED_MIRROR_OFFSET, size, UC_PROT_ALL, ctypes.addressof(buf))
        uc.mem_map(STOP & ~0xFFF, 0x1000)
        # What's at address 0 on the Deluge reads but isn't written: the firmware does read through null pointers now
        # and then (Clip::~Clip() looks at currentSong while there's none)
        uc.mem_map(0, 0x100000, unicorn.UC_PROT_READ)
        # The peripherals the firmware waits on or counts with (models below) are MMIO pages; the rest is plain memory,
        # mapped when first touched
        self.mmio = {}
        self.readers = {}  # Address -> function(size) giving the value read
        self.writers = {}  # Address -> function(size, value), besides keeping the value
        self.setup_models()
        for page in sorted({a & ~0xFFF for a in list(self.readers) + list(self.writers)}):
            uc.mmio_map(page, 0x1000, self.mmio_read, page, self.mmio_write, page)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self.on_unmapped)
        uc.hook_add(UC_HOOK_INTR, self.on_interrupt)
        for paddr, content in self.segments:  # As the bootloader leaves it: every segment at its load address
            uc.mem_write(paddr, content)
        uc.reg_write(UC_ARM_REG_C1_C0_2, uc.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))  # CP10/CP11 (VFP/NEON)
        uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)

        lib = ctypes.CDLL(os.path.join(build_dir, "blockcount.so"))
        lib.bc_total.restype = ctypes.c_uint64
        lib.bc_dump.restype = ctypes.c_uint32
        lib.bc_install.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        if lib.bc_install(uc._uch, ctypes.addressof(self.iram), INTERNAL_RAM, INTERNAL_RAM_SIZE) != 0:
            raise SystemExit("could not install the block hook")
        self.bc = lib

        self.sd_path = sd_image
        self.sd_fd = os.open(sd_image, os.O_RDWR)
        self.sd_reads = self.sd_writes = 0
        self.dma_free = 0  # See ssi_position()
        self.stopped = False

    def seconds(self):
        """Emulated time: instructions at 400 MHz."""
        return self.bc.bc_total() / CPU_HZ

    # --- peripherals

    def setup_models(self):
        r, w = self.readers, self.writers
        # OSTM0, the task manager's clock, and the MTU2's 16-bit system timers count at the peripheral clock
        r[OSTM0 + 4] = lambda size: int(self.seconds() * PERIPHERAL_HZ) & 0xFFFFFFFF
        for offset, prescaler in MTU_TCNT.items():
            r[MTU2 + offset] = lambda size, p=prescaler: int(self.seconds() * PERIPHERAL_HZ / p) & 0xFFFF
        # DMA: each channel has finished (CHSTAT: END, TC) and stands where it started (CRSA/CRDA = N0SA/N0DA)
        for ch in range(16):
            base = dmac_channel_base(ch)
            r[base + 0x18] = lambda size, b=base: self.plain_read(b, 4)
            r[base + 0x1C] = lambda size, b=base: self.plain_read(b + 4, 4)
            r[base + 0x24] = lambda size: 0x60
        r[dmac_channel_base(SSI_TX_DMA_CHANNEL) + 0x18] = lambda size: self.ssi_position()
        # The UARTs' receive DMA (MIDI, PIC): nothing arrived, i.e. it writes where the reader is
        read_addresses = self.sym["rxBufferReadAddr"]
        for item in range(2):
            channel = self.elf_bytes_at(self.sym["rxDmaChannels"] + item, 1)[0]
            r[dmac_channel_base(channel) + 0x1C] = lambda size, a=read_addresses + 4 * item: self.u32(a)
        # RSPI0 (CV, OLED): transmit buffer always empty (SPTEF), receive buffer full (SPRF) from a write to a read
        self.spi_rx_full = False

        def spdr_read(size):
            self.spi_rx_full = False
            return 0

        def spdr_write(size, value):
            self.spi_rx_full = True
        r[RSPI0 + 3] = lambda size: 0x20 | (0x80 if self.spi_rx_full else 0)
        r[RSPI0 + 4] = spdr_read
        w[RSPI0 + 4] = spdr_write
        # SPIBSC, the SPI flash with the settings: transfers end at once (CMNSR: TEND); the flash is erased (0xFF: the
        # firmware's default settings) and its status register (command 0x05) never busy
        r[SPIBSC + 0x48] = lambda size: 0x1

        def flash_data(size):
            command = (self.plain_read(SPIBSC + 0x24, 4) >> 16) & 0xFF
            return 0 if command == 0x05 else (1 << (8 * size)) - 1
        r[SPIBSC + 0x38] = flash_data
        r[SPIBSC + 0x3C] = flash_data

    def elf_bytes_at(self, address, n):
        for paddr, content in self.segments:
            if paddr <= address < paddr + len(content):
                return content[address - paddr:address - paddr + n]
        raise KeyError(hex(address))

    def mmio_read(self, uc, offset, size, page):
        reader = self.readers.get(page + offset)
        return reader(size) if reader else self.plain_read(page + offset, size)

    def mmio_write(self, uc, offset, size, value, page):
        writer = self.writers.get(page + offset)
        if writer:
            writer(size, value)
        for i in range(size):
            self.mmio[page + offset + i] = (value >> (8 * i)) & 0xFF

    def plain_read(self, address, size):
        return sum(self.mmio.get(address + i, 0) << (8 * i) for i in range(size))

    def on_unmapped(self, uc, access, address, size, value, _):
        if address < 0x100000:
            self.log(f"write to {address:#x} (null pointer) at {self.sym.name_at(uc.reg_read(UC_ARM_REG_PC))}")
            return False
        try:
            uc.mem_map(address & ~0xFFFFF, 0x100000)
        except UcError:  # Next to an MMIO page
            uc.mem_map(address & ~0xFFF, 0x1000)
        return True

    def on_interrupt(self, uc, intno, _):
        raise SystemExit(f"exception {intno} at {self.sym.name_at(uc.reg_read(UC_ARM_REG_PC))}")

    def ssi_position(self):
        """Where the SSI's DMA reads (getTxBufferCurrentPlace()): dma_free samples ahead of where the renderer has
        written up to."""
        tx_pos = self.u32(self.sym["_ZN11AudioEngine14i2sTXBufferPosE"]) - UNCACHED_MIRROR_OFFSET
        start = self.sym["ssiTxBuffer"]
        return start + ((tx_pos - start + self.dma_free * 8) % (128 * 8))

    # --- memory

    def u32(self, address):
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def w32(self, address, value):
        self.uc.mem_write(address, struct.pack("<I", value & 0xFFFFFFFF))

    def u8(self, address):
        return self.uc.mem_read(address, 1)[0]

    def ram(self, address, n):
        """n bytes of internal RAM or SDRAM (or their uncached mirrors), or None where there's none."""
        for base, buf, size in ((INTERNAL_RAM, self.iram, INTERNAL_RAM_SIZE), (SDRAM, self.sdram, SDRAM_SIZE)):
            for b in (base, base + UNCACHED_MIRROR_OFFSET):
                if b <= address and address + n <= b + size:
                    return ctypes.string_at(ctypes.addressof(buf) + address - b, n)
        return None

    # --- hooks and calls

    def intercept(self, address, handler):
        """handler(emu) runs when the code reaches address; if it returns a value, the function returns it (r0)
        right there, else it goes on."""
        def hook(uc, addr, size, _):
            result = handler(self)
            if result is not None:
                uc.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        address &= ~1
        return self.uc.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    def skip_to(self, address, target, before=None):
        """Jumps from address to target (Thumb code in the same function)."""
        def hook(uc, addr, size, _):
            if before:
                before(self)
            uc.reg_write(UC_ARM_REG_PC, target | 1)
        self.uc.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    def call(self, address, *args, timeout_s=0):
        uc = self.uc
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            uc.reg_write(reg, value & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_SP, PROGRAM_STACK_TOP)
        uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.run(address | 1, STOP, timeout_s)
        return uc.reg_read(UC_ARM_REG_R0)

    def run(self, start, until, timeout_s=0):
        """Runs to until (or stop()). With a timeout, says every so often where the firmware is (a loop waiting for
        hardware that isn't modelled shows up so)."""
        uc = self.uc
        pc = start
        while True:
            try:
                uc.emu_start(pc, until, timeout=int(timeout_s * 1e6))
            except UcError as e:
                raise SystemExit(f"emulator error: {e} at {self.sym.name_at(uc.reg_read(UC_ARM_REG_PC))}, "
                                 f"lr {self.sym.name_at(uc.reg_read(UC_ARM_REG_LR))}")
            pc = uc.reg_read(UC_ARM_REG_PC)
            if pc == until or self.stopped or not timeout_s:
                self.stopped = False
                return
            self.log(f"  ... at {self.sym.name_at(pc)} (lr {self.sym.name_at(uc.reg_read(UC_ARM_REG_LR))}), "
                     f"{self.bc.bc_total() / 1e6:,.0f}M instructions")
            pc |= 1 if uc.reg_read(UC_ARM_REG_CPSR) & 0x20 else 0

    def stop(self):
        self.stopped = True
        self.uc.emu_stop()


def disassemble(emu, name):
    """(address, mnemonic, operands) of a function, from the toolchain's objdump."""
    start, size = emu.sym.by_name[name]
    start &= ~1
    out = subprocess.run([emu.tool_prefix + "objdump", "-d", "--no-show-raw-insn", f"--start-address={start:#x}",
                          f"--stop-address={start + size:#x}", emu.elf], capture_output=True, text=True).stdout
    lines = []
    for line in out.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]+:", parts[0]):
            lines.append((int(parts[0][:-1], 16), parts[1].strip(), parts[2].strip() if len(parts) > 2 else ""))
    return lines


def setup_sd(emu):
    """The SD card: FatFS's own code on the image file. Its sector reads (sd_read_sect(), under disk_read() and the
    cluster loading) and writes (disk_write()) go to the file; mount_volume() skips the card's initialisation."""
    uc = emu.uc

    def read_sectors(e):
        buf, sector, count = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        uc.mem_write(buf, os.pread(e.sd_fd, count * 512, sector * 512).ljust(count * 512, b"\0"))
        e.sd_reads += count
        return 0

    def write_sectors(e):
        buf, sector, count = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        os.pwrite(e.sd_fd, bytes(uc.mem_read(buf, count * 512)), sector * 512)
        e.sd_writes += count
        return 0

    emu.intercept(emu.sym.find("sd_read_sect"), read_sectors)
    emu.intercept(emu.sym.find("disk_write"), write_sectors)

    # In mount_volume(): from where it has cleared fs->fs_type (strb r3, [r5, #0]) and disk_initialize() (inlined,
    # the SDHI driver) begins, to where it calls check_fs(fs, 0) (after mov r0, r5 and movs r1, #0), with the card
    # ready (diskStatus 0)
    code = disassemble(emu, "mount_volume.lto_priv.0")
    begin = next(code[i + 1][0] for i, (a, m, o) in enumerate(code) if m == "strb" and o.startswith("r3, [r5, #0]"))
    call = next(i for i, (a, m, o) in enumerate(code) if m == "bl" and "<check_fs>" in o)
    if {code[call - 2][1], code[call - 1][1]} != {"movs", "mov"}:
        raise SystemExit(f"mount_volume() doesn't look as expected: {code[call - 3:call + 1]}")
    disk_status = emu.sym["diskStatus"]
    emu.skip_to(begin, code[call - 2][0], lambda e: e.uc.mem_write(disk_status, b"\0"))


def boot(emu):
    """resetprg() up to where deluge_main() would start the task manager, right after registerTasks()."""
    stop_hook = []

    def at_register_tasks(e):
        back = e.uc.reg_read(UC_ARM_REG_LR) & ~1

        def stop_here(uc, address, size, _):
            e.stop()
            uc.hook_del(stop_hook[0])
        stop_hook.append(e.uc.hook_add(UC_HOOK_CODE, stop_here, begin=back, end=back))

    emu.intercept(emu.sym["_Z13registerTasksv"], at_register_tasks)
    emu.uc.reg_write(UC_ARM_REG_SP, PROGRAM_STACK_TOP)
    t = time.time()
    emu.run(emu.sym["resetprg"] | 1, STOP, timeout_s=5)
    emu.log(f"boot: {emu.bc.bc_total() / 1e6:,.1f}M instructions ({time.time() - t:.1f} s)")


def load_startup_song(emu):
    """setupStartupSong() in the template mode: loads SONGS/DEFAULT.XML (writes the current song there first if
    there's none)."""
    emu.w32(emu.sym["_ZN12FlashStorage22defaultStartupSongModeE"], 1)  # StartupSongMode::TEMPLATE
    t = time.time()
    before = emu.bc.bc_total()
    emu.call(emu.sym["_Z16setupStartupSongv"], timeout_s=10)
    if not emu.u32(emu.sym["currentSong"]):
        raise SystemExit("no song after loading")
    emu.log(f"song loaded: {(emu.bc.bc_total() - before) / 1e6:,.1f}M instructions ({time.time() - t:.1f} s), SD card: "
            f"{emu.sd_reads} sectors read, {emu.sd_writes} written")


def write_back_song(emu, path_out):
    """The song as the firmware saves it (to check what it understood): unlink SONGS/DEFAULT.XML and have
    setupStartupSong() write the current song there (and load that again)."""
    emu.uc.mem_write(STOP + 0x100, b"SONGS/DEFAULT.XML\0")
    emu.call(emu.sym["f_unlink"], STOP + 0x100)
    load_startup_song(emu)
    open(path_out, "wb").write(fat32.read_file(emu.sd_path, "SONGS/DEFAULT.XML"))


CULL_TYPES = ["HARD", "FORCE", "SOFT_ALWAYS", "SOFT"]  # AudioEngine's enum CullType


def task_stats_address(emu):
    """Where routine() reads getLastRunTimeforCurrentTask() (inlined): taskManager.list[currentID].durationStats
    .average. From the code: movs r1, #sizeof(Task); ldrsb.w r2, [r3, #offsetof(currentID)]; mla r3, r1, r2, r3;
    vldr d16, [r3, #offsetof(average)]; vmul.f64 (by 44100)."""
    code = disassemble(emu, "_ZN11AudioEngine7routineEv")
    for i in range(5, len(code) - 1):
        if (code[i][1] == "vldr" and code[i + 1][1] == "vmul.f64" and code[i - 1][1] == "mla"
                and code[i - 2][1].startswith("ldrsb")):
            mla = [r.strip() for r in code[i - 1][2].split(",")]  # mla rd, rsize, rindex, rbase
            size_at = next((j for j in range(i - 3, i - 6, -1)
                            if code[j][1] == "movs" and code[j][2].startswith(mla[1] + ",")), None)
            if size_at is None:
                continue
            task_size, current_id, average = (int(re.search(r"#(\d+)", code[j][2]).group(1))
                                              for j in (size_at, i - 2, i))
            base = emu.sym["taskManager"]
            index = struct.unpack("<b", emu.uc.mem_read(base + current_id, 1))[0]
            return base + index * task_size + average
    raise SystemExit("routine() doesn't read the task's durationStats as expected")


class Player:
    """Plays the song one AudioEngine::routine() call (one window) at a time."""

    def __init__(self, emu, culling=False):
        self.emu = emu
        sym = emu.sym
        self.routine = sym["_ZN11AudioEngine7routineEv"]
        self.direness = sym["_ZN11AudioEngine11cpuDirenessE"]
        # The task manager's tasks are lambdas in registerTasks(), numbered in the order they're added: the 2nd is
        # playbackHandler.routine(), the 3rd audioFileManager.loadAnyEnqueuedClusters(128, false)
        self.playback_routine = sym.find("_ZZ13registerTasksvENUlvE0_4_FUNEv")
        self.load_clusters = sym.find("_ZZ13registerTasksvENUlvE1_4_FUNEv")
        self.sample_timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
        self.active_voices = sym["_ZN11AudioEngine12activeVoicesE"]
        self.rendering_buffer = sym["_ZN11AudioEngine15renderingBufferE"]
        self.master = sym["_ZN11AudioEngine23masterVolumeAdjustmentLE"], sym["_ZN11AudioEngine23masterVolumeAdjustmentRE"]
        self.culls = collections.Counter()  # AudioEngine::cullVoice() calls by caller and type
        self.window_culls = 0
        emu.intercept(sym.find("_ZN4Song11renderAudioEP12StereoSample"), self.on_render)
        emu.intercept(sym.find("_ZN11AudioEngine9cullVoiceE"), self.on_cull)
        # From here on the tasks are called from here, and an empty task list makes getLastRunTimeforCurrentTask()
        # read 0: no culling for CPU load, whatever the emulated time. With culling, the audio task's
        # durationStats.average is written before each routine() call, as the task manager would have it
        start, size = sym.by_name["taskManager"]
        emu.uc.mem_write(start, bytes(size))
        self.culling = culling
        self.task_average_address = task_stats_address(emu) if culling else None
        self.task_average = 0.0  # Seconds

    def on_render(self, emu):
        emu.dma_free = 0  # After this window, the DMA shows no more space: one window per routine() call

    def on_cull(self, emu):
        caller = emu.sym.function_at(emu.uc.reg_read(UC_ARM_REG_LR))
        kind = emu.uc.reg_read(UC_ARM_REG_R1)
        self.culls[f"{caller[2].split('(')[0] if caller else '?'} "
                   f"{CULL_TYPES[kind] if kind < len(CULL_TYPES) else kind}"] += 1
        self.window_culls += 1

    def voices_by_sound(self):
        """AudioEngine::activeVoices (a VoiceVector: elements {Sound*, Voice*}, in a ring buffer): Sound* -> voices."""
        es, _, _, memory, n, size, start = struct.unpack("<7I", self.emu.uc.mem_read(self.active_voices, 28))
        counts = collections.Counter()
        if n:
            block = bytes(self.emu.uc.mem_read(memory, size * es))
            for i in range(n):
                j = i + start
                j -= size if j >= size else 0
                counts[struct.unpack_from("<I", block, j * es)[0]] += 1
        return counts

    def start(self):
        self.emu.call(self.emu.sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
        if not self.emu.u8(self.emu.sym["playbackHandler"] + 16):
            raise SystemExit("playback didn't start")

    def voices(self):
        return self.emu.u32(self.active_voices + 16)  # activeVoices.numElements

    def window(self):
        """One AudioEngine::routine() call and the other tasks after it: (instructions, samples rendered, voices,
        other tasks' instructions, output samples scaled to the codec's full scale, cpuDireness, cullVoice() calls,
        voices per Sound*)."""
        emu = self.emu
        emu.dma_free = 127
        if self.culling:
            emu.uc.mem_write(self.task_average_address, struct.pack("<d", self.task_average))
        self.window_culls = 0
        timer = emu.u32(self.sample_timer)
        before = emu.bc.bc_total()
        emu.call(self.routine)
        instructions = emu.bc.bc_total() - before
        # TaskManager::runTask(): durationStats.update(runtime), i.e. average = (average + runtime) / 2
        self.task_average = (self.task_average + instructions / CPU_HZ) / 2
        samples = (emu.u32(self.sample_timer) - timer) & 0xFFFFFFFF
        voices = self.voices()
        direness = struct.unpack("<i", emu.uc.mem_read(self.direness, 4))[0]
        culls = self.window_culls
        by_sound = self.voices_by_sound()
        # What doSomeOutputting() sends to the codec: (sample * master volume) >> 32, << 8 with saturation
        x = np.frombuffer(bytes(emu.uc.mem_read(self.rendering_buffer, samples * 8)), "<i4").reshape(-1, 2)
        gain = np.array([struct.unpack("<i", emu.uc.mem_read(a, 4))[0] for a in self.master]) / 2 ** 55
        emu.dma_free = 0
        before = emu.bc.bc_total()
        emu.call(self.playback_routine)
        emu.call(self.load_clusters)
        return instructions, samples, voices, emu.bc.bc_total() - before, x * gain, direness, culls, by_sound

    def play(self, samples_wanted, record=None):
        total = 0
        while total < samples_wanted:
            w = self.window()
            total += w[1]
            if record is not None:
                record.append(w)


# The profile's areas, by function name (the first pattern that matches the name, without its parameters)
AREAS = [
    ("oscillators", r"Voice::renderOsc|Voice::renderBasicSource|WaveTable|getKernel|getWhichKernel|"
                    r"calculatePhaseIncrements|MasterTune::applyToPhase"),
    ("FM (DX7 engine)", r"neon_fm_kernel|FmOpKernel|FmCore|DxVoice|DxPatch|^Env::|PitchEnv|Freqlut"),
    ("filters", r"filter::|instantTan"),
    ("sample reading / interpolation / time-stretch",
     r"SampleLowLevelReader|VoiceSample|TimeStretch|^Sample::|SampleCluster|SampleCache|SamplePlaybackGuide|Cluster|"
     r"MultisampleRange|AudioFileManager"),
    ("per-track FX (mod FX, bitcrush, volume/pan/reverb send)",
     r"ModControllableAudio::process|processStutter|addAudio|shouldDoPanning"),
    ("reverb", r"reverb::|Reverb::|renderReverb"),
    ("delay", r"Delay"),
    ("drone", r"Drone"),
    ("sidechain / compressors", r"RMSFeedbackCompressor|SideChain"),
    ("song / master FX, output to the codec",
     r"GlobalEffectable|renderSongFX|doSomeOutputting|AbsValueFollower|Metronome|AudioEngine::routine$|renderOutput|"
     r"renderGlobalEffectableForClip"),
    ("voices: rendering loop, patcher, envelopes, LFOs",
     r"^Voice|Patcher|Envelope|^Sound::|LFO|PatchCable|getExp|interpolateTable|getFinalParameterValue|ModelStack|"
     r"VoiceVector|^Source::|lookup(Release|Attack)Rate|cableTo|VoiceUnison|SoundDrum::|SoundInstrument::"),
    ("playback / sequencing (clips, notes, arpeggiator, ticks)",
     r"Playback|Session|Clip|NoteRow|Arpeggiator|^Song::|^Kit::|tickSong|Midi|Arranger|^Note|^Output::"),
]
OTHER = "memory / other"


def area_of(name):
    name = name.split("(")[0]
    return next((area for area, pattern in AREAS if re.search(pattern, name)), OTHER)


def sound_names(emu, pointers, known):
    """Sound* -> name: a String (a char pointer) near each Sound (Output::name of its SoundInstrument, SoundDrum::name)
    pointing at one of the names in the song. The offset that names the most Sounds wins (the same field in every
    object of a class), so a neighbouring object's name isn't taken."""
    known = {n.encode() + b"\0" for n in known}
    longest = max(map(len, known))
    candidates = {}
    for p in pointers:
        found = {}
        span = 0x2000
        block = emu.ram(p - span, 2 * span) or emu.ram(p, span) or b""
        origin = p - span if len(block) == 2 * span else p
        for i in range(0, len(block) - 3, 4):
            target = struct.unpack_from("<I", block, i)[0]
            s = emu.ram(target, longest)
            if s:
                name = s[:s.find(b"\0") + 1] if b"\0" in s else None
                if name in known:
                    found[origin + i - p] = name[:-1].decode()
        candidates[p] = found
    votes = collections.Counter(d for found in candidates.values() for d in found)
    names = {}
    for p, found in candidates.items():
        best = max(found, key=lambda d: (votes[d], -abs(d)), default=None)
        names[p] = found[best] if best is not None else f"{p:#x}"
    return names


def profile_by_function(emu):
    n = 1 << 20
    addresses, instructions, counts = (ctypes.c_uint32 * n)(), (ctypes.c_uint32 * n)(), (ctypes.c_uint64 * n)()
    k = emu.bc.bc_dump(addresses, instructions, counts, n)
    by_function = collections.Counter()
    for i in range(k):
        f = emu.sym.function_at(addresses[i])
        by_function[f[2] if f else f"?{addresses[i]:#x}"] += instructions[i] * counts[i]
    return by_function


def measure(emu, player, warmup_bars, bars, out_dir, log, song_names=()):
    t = time.time()
    player.play(int(warmup_bars * BAR))
    log(f"warm-up: {warmup_bars:g} bar(s) ({time.time() - t:.1f} s)")
    emu.bc.bc_reset_counts()
    culls_before = collections.Counter(player.culls)
    windows = []
    t = time.time()
    player.play(int(bars * BAR), windows)
    elapsed = time.time() - t
    instr = np.array([w[0] for w in windows], dtype=np.float64)
    samples = np.array([w[1] for w in windows])
    voices = np.array([w[2] for w in windows])
    direness = np.array([w[5] for w in windows])
    culls = np.array([w[6] for w in windows])
    total_samples = int(samples.sum())
    seconds = total_samples / SAMPLE_RATE
    per_block = instr.sum() / total_samples * 128
    is_full = samples == 128
    full = instr[is_full]

    # Voices per Sound, named, in the song's order
    pointers = sorted({p for w in windows for p in w[7]})
    names = sound_names(emu, pointers, song_names) if song_names else {p: f"{p:#x}" for p in pointers}
    order = list(song_names)
    pointers.sort(key=lambda p: (order.index(names[p]) if names[p] in order else len(order), p))
    by_sound = np.array([[w[7].get(p, 0) for p in pointers] for w in windows]).reshape(len(windows), len(pointers))
    weights = samples / total_samples
    per_sound = {names[p]: dict(mean=float(weights @ by_sound[:, i]), max=int(by_sound[:, i].max()),
                                sounding=float(weights @ (by_sound[:, i] > 0)))
                 for i, p in enumerate(pointers)}
    all_sounding = (by_sound > 0).all(axis=1) if pointers else np.zeros(len(windows), bool)
    all_full = instr[all_sounding & is_full]
    num_sounding = (by_sound > 0).sum(axis=1)
    by_num_sounding = {int(k): dict(share=float(weights @ (num_sounding == k)),
                                    mean=float(instr[(num_sounding == k) & is_full].mean())
                                    if np.any((num_sounding == k) & is_full) else None)
                       for k in np.unique(num_sounding)}
    short = ~is_full
    log(f"measured: {bars:g} bars, {len(windows)} windows, {instr.sum() / 1e6:,.0f}M instructions ({elapsed:.1f} s, "
        f"{instr.sum() / elapsed / 1e6:.0f}M instructions/s)")

    out = np.concatenate([w[4] for w in windows])
    peak = float(np.max(np.abs(out)))
    rms = float(np.sqrt(np.mean(out ** 2)))
    pcm = (np.clip(out, -1, 1) * 32767).astype("<i2").tobytes()
    with open(os.path.join(out_dir, "measured.wav"), "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, 2, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 16) + b"data" +
                struct.pack("<I", len(pcm)) + pcm)

    functions = profile_by_function(emu)
    areas = collections.Counter()
    area_functions = collections.defaultdict(list)
    for name, n in functions.most_common():
        a = area_of(name)
        areas[a] += n / total_samples * 128
        area_functions[a].append((name, round(n / total_samples * 128)))

    def cpu(x):
        return x / CYCLES_PER_BLOCK * 100

    return dict(
        bars=bars, culling=player.culling, windows=len(windows), short_windows=int(np.sum(short)),
        samples=total_samples, instructions_per_128=per_block, cpu_percent=cpu(per_block),
        full_windows=dict(count=len(full), mean=float(full.mean()), max=float(full.max()), min=float(full.min()),
                          percentiles={p: float(np.percentile(full, p)) for p in (5, 25, 50, 75, 95, 99)}),
        max_cpu_percent=cpu(float(full.max())),
        # Windows shorter than 128 samples (here: cut at the song's clock ticks). What they cost per sample, against the
        # full windows: on the Deluge, whenever the main loop comes back sooner, routine() renders fewer samples
        short_window_cost=dict(
            count=int(short.sum()), mean_samples=float(samples[short].mean()) if short.any() else None,
            mean_instructions=float(instr[short].mean()) if short.any() else None,
            per_sample=float(instr[short].sum() / samples[short].sum()) if short.any() else None,
            full_per_sample=float(full.mean() / 128),
            extra_per_128=(float(instr.sum() / total_samples * 128 - full.mean())),
            # A straight line through the short and the full windows' means: a routine() call rendering n samples costs
            # about per_call + per_sample_slope * n (per_call is an upper bound: the short windows also do a tick's work)
            **(dict(per_call=float((instr[short].mean() * 128 - full.mean() * samples[short].mean())
                                   / (128 - samples[short].mean())),
                    per_sample_slope=float((full.mean() - instr[short].mean()) / (128 - samples[short].mean())))
               if short.any() else {})),
        all_sounds_sounding=dict(
            sounds=len(pointers), windows=int(all_sounding.sum()), full_windows=len(all_full),
            share=float(weights @ all_sounding),
            mean=float(all_full.mean()) if len(all_full) else None,
            mean_cpu_percent=cpu(float(all_full.mean())) if len(all_full) else None,
            max=float(all_full.max()) if len(all_full) else None,
            by_number_sounding=by_num_sounding),
        other_tasks_per_128=float(sum(w[3] for w in windows)) / total_samples * 128,
        voices=dict(mean=float(voices.mean()), max=int(voices.max()), min=int(voices.min())),
        voices_by_sound=per_sound,
        culls={k: v - culls_before[k] for k, v in player.culls.items() if v - culls_before[k]},
        culls_per_second=float(culls.sum() / seconds),
        culls_since_playback_started=dict(player.culls),
        cpu_direness=dict(mean=float(direness.mean()),
                          share={int(d): float(np.mean(direness == d)) for d in np.unique(direness)}),
        output=dict(peak_dbfs=20 * math.log10(peak) if peak else None, rms_dbfs=20 * math.log10(rms) if rms else None,
                    clipped_samples=int(np.sum(np.abs(out) >= 1.0)), nan=bool(np.isnan(out).any())),
        areas={a: dict(per_128=v, percent_of_total=v / per_block * 100, cpu_percent=cpu(v),
                       top=area_functions[a][:6]) for a, v in areas.most_common()},
        top_functions=[(name, round(n / total_samples * 128)) for name, n in functions.most_common(40)],
        window_log_fields=["instructions", "samples", "voices", "cpuDireness", "cullVoice calls"],
        window_log=[(int(w[0]), int(w[1]), int(w[2]), int(w[5]), int(w[6])) for w in windows],
        voices_by_sound_log=dict(sounds=[names[p] for p in pointers], windows=by_sound.tolist()),
    )


def report(r, log):
    fw = r["full_windows"]
    p = fw["percentiles"]
    log(f"\nper 128 samples: {r['instructions_per_128']:,.0f} instructions = {r['cpu_percent']:.1f}% CPU "
        f"({r['samples']} samples in {r['windows']} windows, {r['short_windows']} cut short by clock ticks)")
    log(f"full windows (128): mean {fw['mean']:,.0f} ({fw['mean'] / CYCLES_PER_BLOCK * 100:.1f}%), max "
        f"{fw['max']:,.0f} ({r['max_cpu_percent']:.1f}%), min {fw['min']:,.0f}; percentiles 5/25/50/75/95/99: "
        + " / ".join(f"{p[k] / CYCLES_PER_BLOCK * 100:.0f}%" for k in p))
    a = r["all_sounds_sounding"]
    if a["mean"] is not None:
        log(f"full windows with all {a['sounds']} Sounds sounding (voices in each): {a['full_windows']} "
            f"({a['share'] * 100:.0f}% of the time), mean {a['mean']:,.0f} ({a['mean_cpu_percent']:.1f}%), max "
            f"{a['max']:,.0f} ({a['max'] / CYCLES_PER_BLOCK * 100:.1f}%)")
    else:
        log(f"no window with all {a['sounds']} Sounds sounding")
    log("by the number of Sounds with voices (share of the time, mean of its full windows): " + ", ".join(
        f"{k}: {x['share'] * 100:.0f}% " + (f"{x['mean'] / CYCLES_PER_BLOCK * 100:.0f}%" if x['mean'] else "-")
        for k, x in a["by_number_sounding"].items()))
    s = r["short_window_cost"]
    if s["count"]:
        log(f"short windows (< 128 samples, cut at clock ticks): {s['count']}, mean {s['mean_samples']:.0f} samples, "
            f"{s['mean_instructions']:,.0f} instructions = {s['per_sample']:,.0f} per sample (full windows: "
            f"{s['full_per_sample']:,.0f}, {s['per_sample'] / s['full_per_sample']:.1f}x); they add "
            f"{s['extra_per_128']:,.0f} per 128 overall")
        a64 = (s['per_call'] * 2 + s['per_sample_slope'] * 128) / CYCLES_PER_BLOCK * 100
        log(f"  a routine() call costs about {s['per_call']:,.0f} + {s['per_sample_slope']:,.0f} per sample (the fixed "
            f"part at most: short windows also do a tick's work); called every 64 samples instead of 128 that would be "
            f"about {a64:.0f}% instead of {fw['mean'] / CYCLES_PER_BLOCK * 100:.0f}%")
    v = r["voices"]
    log(f"voices: mean {v['mean']:.1f}, max {v['max']}, min {v['min']}; cullVoice() calls: {r['culls'] or 'none'}, "
        f"{r['culls_per_second']:.1f}/s (since playback started: {r['culls_since_playback_started'] or 'none'})")
    d = r["cpu_direness"]
    log(f"cpuDireness: mean {d['mean']:.1f}; share of windows: "
        + ", ".join(f"{k}: {x * 100:.1f}%" for k, x in d["share"].items()))
    log("\nvoices per Sound (mean, max, share of the time with voices):")
    for name, x in r["voices_by_sound"].items():
        log(f"  {name:8s} {x['mean']:5.1f} {x['max']:3d} {x['sounding'] * 100:5.1f}%")
    o = r["output"]
    log(f"output: peak {o['peak_dbfs']:.1f} dBFS, RMS {o['rms_dbfs']:.1f} dBFS, {o['clipped_samples']} clipped, "
        f"NaN: {o['nan']}")
    log(f"other tasks (playback routine, cluster loading): {r['other_tasks_per_128']:,.0f} per 128")
    log("\nby area (per 128 samples, % of the total, % CPU):")
    for a, d in r["areas"].items():
        top = ", ".join(f"{n.split('(')[0]} {x:,}" for n, x in d["top"][:3])
        log(f"  {d['per_128']:9,.0f} {d['percent_of_total']:5.1f}% {d['cpu_percent']:5.1f}%  {a}: {top}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("sd")
    ap.add_argument("out")
    ap.add_argument("--tools", help="toolchain prefix (.../arm-none-eabi-); default: the firmware tree's")
    ap.add_argument("--build", default=HERE, help="directory with blockcount.so")
    ap.add_argument("--warmup-bars", type=float, default=1)
    ap.add_argument("--bars", type=float, default=2)
    ap.add_argument("--culling", action="store_true",
                    help="as on the Deluge: routine() culls voices and sets cpuDireness by its emulated duration")
    ap.add_argument("--write-back", help="also save the song as the firmware writes it after loading, to this file")
    args = ap.parse_args()
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(args.out, exist_ok=True)

    def log(s):
        print(s, flush=True)

    emu = Emulator(args.elf, args.sd, tools, args.build, log)
    setup_sd(emu)
    boot(emu)
    load_startup_song(emu)
    if args.write_back:
        write_back_song(emu, args.write_back)
    # The Sounds' names in the song (synths: presetName, kit rows: name), in its order
    xml = fat32.read_file(args.sd, "SONGS/DEFAULT.XML").decode(errors="replace")
    song_names = list(dict.fromkeys(re.findall(r'<sound\b[^>]*?\b(?:presetName|name)="([^"]+)"', xml)))
    player = Player(emu, culling=args.culling)
    player.start()
    result = measure(emu, player, args.warmup_bars, args.bars, args.out, log, song_names)
    json.dump(result, open(os.path.join(args.out, "result.json"), "w"), indent=1)
    report(result, log)


if __name__ == "__main__":
    main()
