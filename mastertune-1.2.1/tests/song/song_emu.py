#!/usr/bin/env python3
"""Runs the real Deluge firmware (deluge.elf) in unicorn and measures what a whole song costs its Cortex-A9.

Usage: song_emu.py <deluge.elf> <sd.img> <out dir> [--warmup-bars N] [--bars N] [--culling] [--write-back song.xml]
                   [--init-sounds] [--seed N] [--fill WORD] [--save-while-playing] [--midi-timing]
                   [--poke SYMBOL=VALUE]

--save-while-playing: instead of measuring the windows, the firmware saves the song while it plays, with the output DMA
in real time (see save_while_playing(), RealTimeDma and run.sh's SAVE=1).
--midi-timing: the MIDI/gate output timer and the DIN MIDI UART modelled with their interrupts (MidiTiming, Interrupts),
and when the MIDI goes out against the audio: in normal playback in real time (play_realtime()) or, with
--save-while-playing, while the song is saved again and again (run.sh's MIDI=1).

Bit-exact comparisons between builds (see run.sh): Sound::Sound() leaves the LFO phases and the skip-rendering
timestamps uninitialised (SOUND_UNINITIALISED), so what a song renders depends on what the RAM held before, e.g. stale
pointers whose values move with the code and data layout; --init-sounds sets them as a fix would. --seed pins the random
generator after boot (it is seeded from the emulated time). --fill tests for reads of RAM never written.

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
import hashlib
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
from unicorn.arm_const import (UC_ARM_REG_C1_C0_2, UC_ARM_REG_CPSR, UC_ARM_REG_D0, UC_ARM_REG_FPEXC, UC_ARM_REG_LR,
                               UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP,
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
L2C = 0x3FFFF000  # PL310, the L2 cache controller
L2C_REGISTERS = {0x100: "control", 0x104: "aux_control", 0x220: "int_clear", 0x730: "sync", 0x770: "inv_pa",
                 0x77C: "inv_way", 0x7B0: "clean_pa", 0x7BC: "clean_way", 0x7F0: "clean_inv_pa", 0x7FC: "clean_inv_way",
                 0x900: "d_lockdown", 0x904: "i_lockdown"}
OLED_SPI_DMA_CHANNEL = 4


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
    def __init__(self, elf, sd_image, tool_prefix, build_dir, log, fill=None):
        self.elf = elf
        self.fill = fill  # 32-bit word the RAM holds before boot (None: zeros), see --fill
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
        if fill is not None:  # What the RAM holds at power-on isn't zero: see --fill
            for buf, size in ((self.iram, INTERNAL_RAM_SIZE), (self.sdram, SDRAM_SIZE)):
                ctypes.memmove(buf, struct.pack("<I", fill) * (size // 4), size)
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
        # What reset_handler does before resetprg() (initsct: .bss cleared; resetprg() clears .frunk_bss itself)
        uc.mem_write(*self.section(data, ".bss"))
        uc.reg_write(UC_ARM_REG_C1_C0_2, uc.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))  # CP10/CP11 (VFP/NEON)
        uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)

        lib = ctypes.CDLL(os.path.join(build_dir, "blockcount.so"))
        lib.bc_total.restype = ctypes.c_uint64
        lib.bc_dump.restype = ctypes.c_uint32
        lib.bc_set_deadline.argtypes = [ctypes.c_uint64]
        lib.bc_deadline_hit.restype = ctypes.c_int
        lib.bc_install.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        if lib.bc_install(uc._uch, ctypes.addressof(self.iram), INTERNAL_RAM, INTERNAL_RAM_SIZE) != 0:
            raise SystemExit("could not install the block hook")
        self.bc = lib

        self.sd_path = sd_image
        self.sd_fd = os.open(sd_image, os.O_RDWR)
        self.sd_reads = self.sd_writes = 0  # Sectors
        self.sd_read_commands = self.sd_write_commands = 0  # sd_read_sect() / disk_write() calls
        self.sd_log = None  # A list: every read and write as (kind "r"/"w", sector, count, emu.now()) (tests/sdload)
        self.sd_model = None  # An SdModel: the card takes time (--sd-latency); None: the card is instant
        self.dma_free = 0  # See ssi_position()
        self.dma = None  # A RealTimeDma from when it takes over (--save-while-playing, --midi-timing)
        self.stopped = False
        self.idle = 0  # Instructions of emulated time that passed without any code running (play_realtime())
        self.interrupts = None  # Interrupts (--midi-timing)

    @staticmethod
    def section(data, wanted):
        """(address, bytes(size)) of the ELF section with that name."""
        shoff, = struct.unpack_from("<I", data, 32)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 46)
        strtab = struct.unpack_from("<IIIIII", data, shoff + shstrndx * shentsize)[4]
        for i in range(shnum):
            name, _, _, addr, _, size = struct.unpack_from("<IIIIII", data, shoff + i * shentsize)
            if data[strtab + name:data.index(b"\0", strtab + name)].decode() == wanted:
                return addr, bytes(size)
        raise KeyError(wanted)

    def now(self):
        """Emulated time in instructions (at 400 MHz): those executed, plus idle time (see play_realtime())."""
        return self.bc.bc_total() + self.idle

    def seconds(self):
        """Emulated time: instructions at 400 MHz."""
        return self.now() / CPU_HZ

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
        # The L2 cache controller (PL310; the L2 test versions switch it on): operations by way finish at once (the way
        # bits read 0), those by address too (bit 0 reads 0). Maintenance and the OLED's DMA starts go to l2_log for
        # tests/l2: (what, value, instruction count).
        self.l2_log = []
        for offset, name in L2C_REGISTERS.items():
            w[L2C + offset] = lambda size, value, n=name: self.l2_log.append((n, value, self.now()))
            if name.endswith(("_way", "_pa")):
                r[L2C + offset] = lambda size: 0
        oled_dma = dmac_channel_base(OLED_SPI_DMA_CHANNEL)
        r[oled_dma + 0x28] = lambda size: 0  # CHCTRL: command bits, read as 0
        w[oled_dma + 0x28] = lambda size, value: value & 1 and self.l2_log.append(
            ("oled_dma", (self.plain_read(oled_dma, 4), self.plain_read(oled_dma + 8, 4)), self.now()))

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
        written up to. With a RealTimeDma (--save-while-playing): where it reads now, by the emulated time."""
        if self.dma is not None:
            return self.sym["ssiTxBuffer"] + self.dma.position() % 128 * 8
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
            if self.bc.bc_deadline_hit() and self.interrupts:  # An interrupt is due (Interrupts): run it, go on
                self.interrupts.service()
                if pc != until:
                    pc |= 1 if uc.reg_read(UC_ARM_REG_CPSR) & 0x20 else 0
                    continue
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
    cluster loading) and writes (disk_write()) go to the file; mount_volume() skips the card's initialisation.
    Counted: sectors (emu.sd_reads, sd_writes) and commands (calls: sd_read_commands, sd_write_commands); with
    emu.sd_log a list, every access. The card is instant unless emu.sd_model is an SdModel (--sd-latency)."""
    uc = emu.uc

    def read_sectors(e):
        buf, sector, count = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        uc.mem_write(buf, os.pread(e.sd_fd, count * 512, sector * 512).ljust(count * 512, b"\0"))
        e.sd_reads += count
        e.sd_read_commands += 1
        if e.sd_log is not None:
            e.sd_log.append(("r", sector, count, e.now()))
        if e.sd_model:
            return e.sd_model.wait(count, False)  # Goes on in the wait loop (SdModel), which returns 0
        return 0

    def write_sectors(e):
        buf, sector, count = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        os.pwrite(e.sd_fd, bytes(uc.mem_read(buf, count * 512)), sector * 512)
        e.sd_writes += count
        e.sd_write_commands += 1
        if e.sd_log is not None:
            e.sd_log.append(("w", sector, count, e.now()))
        if e.sd_model:
            return e.sd_model.wait(count, True)
        return 0

    emu.intercept(emu.sym.find("sd_read_sect"), read_sectors)
    emu.intercept(emu.sym.find("disk_write"), write_sectors)

    # In mount_volume(): from where it has cleared fs->fs_type (strb r3, [r5, #0]) and disk_initialize() (inlined,
    # the SDHI driver) begins, to where it calls check_fs(fs, 0) (after mov r0, r5 and movs r1, #0), with the card
    # ready (diskStatus 0)
    code = disassemble(emu, "mount_volume.lto_priv.0")
    begin = next(code[i + 1][0] for i, (a, m, o) in enumerate(code) if m == "strb" and o.startswith("r3, [r5, #0]"))
    call = next(i for i, (a, m, o) in enumerate(code) if m == "bl" and ("<check_fs>" in o or "<check_fs." in o))
    if {code[call - 2][1], code[call - 1][1]} != {"movs", "mov"}:
        raise SystemExit(f"mount_volume() doesn't look as expected: {code[call - 3:call + 1]}")
    disk_status = emu.sym["diskStatus"]
    emu.skip_to(begin, code[call - 2][0], lambda e: e.uc.mem_write(disk_status, b"\0"))


SD_WAIT_TRAMPOLINE = STOP + 0x80  # SdModel's wait loop (Thumb code written there)
SD_WAIT_DONE = 0x7FFE0000  # An MMIO word it polls: 1 once the card is done


class SdModel:
    """--sd-latency: the card takes time. Every sd_read_sect() (and disk_write()) call is one command taking
    command_us + sectors * sector_us of emulated time. The data is there at once (read from the image), but the call
    returns only when that time has passed, and meanwhile the firmware does what it does on the Deluge while it waits
    for the card: routineForSD() once (sd_read.c) and then again and again until the transfer is done (the DMA wait
    loop in sd_dev_low.c: the audio routine, UI timers, OLED, PIC, encoders, buttons). That loop is Thumb code written
    at SD_WAIT_TRAMPOLINE, which the intercepted call jumps to with the caller's return address; it polls
    SD_WAIT_DONE between the routineForSD() calls. With poll_us, successive polls are at least that far apart (the
    time in between is idle, emu.idle): fewer calls to emulate, for speed; 0 (the default) calls it back to back as
    the Deluge does. Defaults: a typical SDHC card on the Deluge's 4-bit bus, ~1 ms per command, ~12 MB/s.
    Writes the same (write_command_us, write_sector_us: default as for reads). Off by default: the card is instant.
    self.waits: (start in instructions, duration in instructions, sectors, write, routineForSD() calls)."""

    def __init__(self, emu, command_us=1000.0, sector_us=512 / 12e6 * 1e6, poll_us=0.0, write_command_us=None,
                 write_sector_us=None):
        self.emu = emu
        self.command, self.sector = command_us * CPU_HZ / 1e6, sector_us * CPU_HZ / 1e6
        self.write_command = (command_us if write_command_us is None else write_command_us) * CPU_HZ / 1e6
        self.write_sector = (sector_us if write_sector_us is None else write_sector_us) * CPU_HZ / 1e6
        self.poll = poll_us * CPU_HZ / 1e6
        self.params = dict(command_us=command_us, sector_us=sector_us, poll_us=poll_us,
                           write_command_us=self.write_command * 1e6 / CPU_HZ,
                           write_sector_us=self.write_sector * 1e6 / CPU_HZ)
        self.stack = []  # Waits in progress (a read from inside routineForSD() would nest)
        self.waits = []
        code = struct.pack("<10H", 0xB510,  # push {r4, lr}
                           0x4C04,  # loop: ldr r4, =routineForSD
                           0x47A0,  # blx r4
                           0x4804,  # ldr r0, =SD_WAIT_DONE
                           0x6800,  # ldr r0, [r0]
                           0x2800,  # cmp r0, #0
                           0xD0F9,  # beq loop
                           0x2000,  # movs r0, #0 (SD_OK)
                           0xBD10,  # pop {r4, pc}
                           0xBF00)  # nop
        emu.uc.mem_write(SD_WAIT_TRAMPOLINE, code + struct.pack("<II", emu.sym["routineForSD"] | 1, SD_WAIT_DONE))
        emu.readers[SD_WAIT_DONE] = self.done
        emu.uc.mmio_map(SD_WAIT_DONE, 0x1000, emu.mmio_read, SD_WAIT_DONE, emu.mmio_write, SD_WAIT_DONE)
        emu.sd_model = self

    def wait(self, sectors, write):
        """From the intercepted call: go on in the wait loop (the caller's return address stays in lr)."""
        now = self.emu.now()
        duration = (self.write_command + sectors * self.write_sector) if write else (self.command + sectors * self.sector)
        self.stack.append([now, now + duration, sectors, write, 0, now])
        self.emu.uc.reg_write(UC_ARM_REG_PC, SD_WAIT_TRAMPOLINE | 1)
        return None

    def done(self, size):
        e = self.emu
        w = self.stack[-1]
        now = e.now()
        if now < w[1] and self.poll and w[5] + self.poll > now:
            e.idle += min(w[5] + self.poll, w[1]) - now
            now = e.now()
        w[5] = now
        w[4] += 1
        if now < w[1]:
            return 0
        self.stack.pop()
        self.waits.append((w[0], now - w[0], w[2], w[3], w[4]))
        return 1


# What Sound::Sound() (and ModControllableAudio's constructor) leave uninitialised although it is read (see --init-sounds):
# LFO() = default leaves phase and holdValue as they were, and the constructor sets skippingRendering = true without
# startSkippingRendering(), so the first stopSkippingRendering() ticks the LFOs and the arp by audioSampleTimer minus
# whatever these timestamps held
SOUND_UNINITIALISED = {"globalLFO": 0, "modFXLFO": 0, "timeStartedSkippingRenderingModFX": "timer",
                       "timeStartedSkippingRenderingLFO": "timer", "timeStartedSkippingRenderingArp": "timer"}


def init_sounds(emu):
    """--init-sounds: at the start of every Sound's constructor (every synth and kit row the song loads), the fields in
    SOUND_UNINITIALISED get what the proposed fix would give them: the LFOs (phase, holdValue) 0, the timestamps
    AudioEngine::audioSampleTimer. Their offsets come from the ELF's debug info (the toolchain's gdb)."""
    fields = list(SOUND_UNINITIALISED)
    out = subprocess.run([emu.tool_prefix + "gdb", "-batch", "-ex", "list SoundInstrument::SoundInstrument",
                          "-ex", "print sizeof(LFO)"]
                         + [x for f in fields for x in ("-ex", f"print (int)&((Sound*)0)->{f}")] + [emu.elf],
                         capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (\d+)$", out, re.M)]
    if len(values) != len(fields) + 1:
        raise SystemExit(f"--init-sounds: no offsets for {fields} from gdb:\n{out[-500:]}")
    lfo_size, offsets = values[0], dict(zip(fields, values[1:]))
    timer = emu.sym["_ZN11AudioEngine16audioSampleTimerE"]
    emu.sounds_initialised = 0

    def at_constructor(e):
        this = e.uc.reg_read(UC_ARM_REG_R0)
        now = e.u32(timer)
        for f, how in SOUND_UNINITIALISED.items():
            e.uc.mem_write(this + offsets[f], bytes(lfo_size) if how == 0 else struct.pack("<I", now))
        e.sounds_initialised += 1
    emu.intercept(emu.sym.find("_ZN5SoundC2Ev"), at_constructor)
    emu.log("--init-sounds: " + ", ".join(f"{f} (+{o})" for f, o in offsets.items()))


def boot(emu):
    """resetprg() up to where deluge_main() would start the task manager, right after registerTasks()."""
    stop_hook = []

    def at_register_tasks(e):
        back = e.uc.reg_read(UC_ARM_REG_LR) & ~1

        def stop_here(uc, address, size, _):
            e.stop()
            uc.hook_del(stop_hook[0])
        stop_hook.append(e.uc.hook_add(UC_HOOK_CODE, stop_here, begin=back, end=back))

    def at_start_clock(e):
        # registerTasks() inlined (LTO decides by the size of the whole program, deluge_main() may even end up in
        # resetprg()): stop where the task manager starts instead, at TaskManager::startClock() not called from
        # TaskManager::yield()
        if "TaskManager::yield" not in e.sym.name_at(e.uc.reg_read(UC_ARM_REG_LR)):
            e.stop()

    try:
        emu.intercept(emu.sym["_Z13registerTasksv"], at_register_tasks)
    except KeyError:
        emu.intercept(emu.sym.find("_ZN11TaskManager10startClockEv"), at_start_clock)
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
    # The measurement version (CPU monitor) wraps the v12 routine() as routineUnmeasured(), where the read then is
    code = disassemble(emu, "_ZN11AudioEngine7routineEv")
    for name in ("_ZN11AudioEngineL17routineUnmeasuredEv", "_ZN11AudioEngine17routineUnmeasuredEv"):
        if name in emu.sym.by_name:
            code = code + disassemble(emu, name)
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


# --- saving while the song plays (--save-while-playing)

class RealTimeDma:
    """The SSI's transmit DMA in real time: from here on it reads one sample every 1/44100 s of emulated time
    (instructions at 400 MHz), round the buffer of 128 samples, whether the firmware has refilled it or not. It starts
    with the buffer full (as the windows before leave it). Every sample the firmware writes into the buffer is checked
    against it: the gap is how many samples the DMA has read since the buffer was last full (the firmware refills up to
    where the DMA reads), i.e. how long the audio went without servicing. At 128 or more the DMA has played the buffer's
    old content again: an underrun, the buzz. What the firmware writes is kept for a WAV file."""

    def __init__(self, emu):
        self.emu = emu
        self.cached = emu.sym["ssiTxBuffer"]
        self.base = self.cached + UNCACHED_MIRROR_OFFSET
        slot = (emu.u32(emu.sym["_ZN11AudioEngine14i2sTXBufferPosE"]) - self.base) // 8
        self.start_instructions = emu.now()
        self.start_position = slot  # The DMA reads where the firmware writes next: the buffer is full
        self.written = slot + 128  # Absolute number of the next sample the firmware writes (the DMA's count + 128)
        self.max_gap = 0
        self.underruns = 0  # Samples written with a gap of 128 or more
        self.misplaced = 0  # Samples not written where expected (only after an underrun)
        self.pending = []  # Addresses written since the last collect()
        self.samples = []  # (left, right) as written, in order
        self.numbers = []  # The absolute number (see position()) of every sample written, in order (MidiTiming)
        for b in (self.base, self.cached):
            emu.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.on_write, begin=b, end=b + 128 * 8 - 1)
        emu.dma = self

    def position(self):
        """The number of the sample the DMA reads now (counted from where the firmware wrote first)."""
        return self.start_position + (self.emu.now() - self.start_instructions) * SAMPLE_RATE // int(CPU_HZ)

    def exact_position(self, instructions):
        """Where the DMA is at that emulated time, in samples with their fraction: sample n plays from n.0."""
        return self.start_position + (instructions - self.start_instructions) * SAMPLE_RATE / CPU_HZ

    def gap(self):
        return self.position() + 128 - self.written

    def on_write(self, uc, access, address, size, value, _):
        base = self.base if address >= self.base else self.cached
        offset = address - base
        for first in range(-(-offset // 8) * 8, offset + size, 8):  # Samples whose first word is written here
            slot = first // 8 % 128
            if slot != self.written % 128:
                self.misplaced += 1
                self.written += (slot - self.written) % 128
            gap = self.gap()
            self.max_gap = max(self.max_gap, gap)
            if gap >= 128:
                self.underruns += 1
            self.numbers.append(self.written)
            self.written += 1
            self.pending.append(base + slot * 8)

    def collect(self):
        """Reads the samples written since the last call (after the writes: the hook comes before them)."""
        for a in self.pending:
            self.samples.append(struct.unpack("<ii", self.emu.uc.mem_read(a, 8)))
        self.pending = []


class Regions:
    """The emulated time by what runs, exclusive: `outer` unless inside one of the functions given (the innermost
    counts), entered at their first instruction and left at their return address with the same stack pointer. Return
    hooks are added as return addresses show up (and the translated code there is dropped, so they take effect)."""

    def __init__(self, emu, functions, outer):
        self.emu = emu
        self.outer = outer
        self.stack = []  # (category, stack pointer, return address, instructions at entry, extra)
        self.time = collections.Counter()
        self.last = emu.bc.bc_total()
        self.return_hooks = set()
        self.on_enter = {}  # Category -> function(emu, return address) giving the entry's extra, for on_exit
        self.on_exit = {}  # Category -> function(emu, extra, instructions)
        self.missing = []
        for name, category in functions:
            try:
                address = emu.sym.find(name)
            except KeyError:
                self.missing.append(name)
                continue
            emu.intercept(address, lambda e, c=category: self.enter(c))
            emu.uc.ctl_remove_cache(address & ~1, (address & ~1) + 4)

    def charge(self):
        now = self.emu.bc.bc_total()
        self.time[self.stack[-1][0] if self.stack else self.outer] += now - self.last
        self.last = now

    def enter(self, category):
        uc = self.emu.uc
        self.charge()
        back = uc.reg_read(UC_ARM_REG_LR) & ~1
        if back not in self.return_hooks:
            self.return_hooks.add(back)
            uc.hook_add(UC_HOOK_CODE, self.at_return, begin=back, end=back)
            uc.ctl_remove_cache(back, back + 4)
        extra = self.on_enter[category](self.emu, back) if category in self.on_enter else None
        self.stack.append((category, uc.reg_read(UC_ARM_REG_SP), back, self.emu.bc.bc_total(), extra))

    def at_return(self, uc, address, size, _):
        if self.stack and self.stack[-1][2] == address and self.stack[-1][1] == uc.reg_read(UC_ARM_REG_SP):
            self.charge()
            category, _, _, entered, extra = self.stack.pop()
            if category in self.on_exit:
                self.on_exit[category](self.emu, extra, self.emu.bc.bc_total() - entered)


ISR_RETURN = STOP + 0x40  # Return address of the interrupts run here: a Thumb nop, so that return hooks there run


class Interrupts:
    """--midi-timing: interrupts at a given emulated time. blockcount.c stops the emulation at the first block from
    then on (bc_set_deadline()); Emulator.run() then runs the handler here, as the CPU would: on the stack below the
    interrupted code's, its registers (VFP/NEON too) saved and restored around it. Not while the code has interrupts
    masked (CPSR.I): then at the next check, 1 µs on. Between calls from the harness (play_realtime()'s idle time),
    idle_until() runs those due."""

    def __init__(self, emu):
        self.emu = emu
        self.pending = {}  # key -> [emulated time (instructions), handler address, before(emu) or None]
        self.inside = False
        self.masked_delays = 0  # Interrupts that had to wait for the code to unmask them
        self.after_return = None  # function(emu), called after each handler returns
        emu.uc.mem_write(ISR_RETURN, b"\x00\xbf")  # nop: a handler's tail call returns here (Regions' return hooks)
        emu.interrupts = self

    def schedule(self, key, at, address, before=None):
        self.pending[key] = [math.ceil(at), address, before]
        self.arm()

    def cancel(self, key):
        if self.pending.pop(key, None):
            self.arm()

    def arm(self):
        if self.inside:
            return  # Once the handler returns
        at = min((p[0] for p in self.pending.values()), default=None)
        self.emu.bc.bc_set_deadline(2 ** 64 - 1 if at is None else max(at - self.emu.idle, 0))

    def service(self):
        """At a deadline stop: runs what is due, unless the code has masked interrupts."""
        uc = self.emu.uc
        if uc.reg_read(UC_ARM_REG_CPSR) & 0x80:
            later = self.emu.now() + 400
            for p in self.pending.values():
                if p[0] < later:
                    p[0] = later
                    self.masked_delays += 1
            self.arm()
            return
        self.run_due(self.emu.now())

    def run_due(self, now):
        while True:
            due = [(p[0], k) for k, p in self.pending.items() if p[0] <= now]
            if not due:
                break
            _, key = min(due)
            _, address, before = self.pending.pop(key)
            if before:
                before(self.emu)
            self.fire(address)
            if self.after_return:
                self.after_return(self.emu)
        self.arm()

    def fire(self, address):
        uc = self.emu.uc
        context = uc.context_save()
        self.inside = True
        uc.reg_write(UC_ARM_REG_SP, (uc.reg_read(UC_ARM_REG_SP) - 256) & ~7)
        uc.reg_write(UC_ARM_REG_LR, ISR_RETURN | 1)
        uc.emu_start(address | 1, ISR_RETURN + 2)
        uc.context_restore(context)
        self.inside = False

    def idle_until(self, at):
        """The CPU idles (nothing runs) until then, but for the interrupts due meanwhile, each at its time."""
        emu = self.emu
        while True:
            first = min((p[0] for p in self.pending.values()), default=None)
            if first is None or first > at:
                break
            emu.idle = max(emu.idle, first - emu.bc.bc_total())
            self.run_due(emu.now())
        emu.idle = max(emu.idle, math.ceil(at) - emu.bc.bc_total())
        self.arm()


MIDI_BYTE_SECONDS = 10 / 31250  # On the DIN wire: start bit, 8 bits, stop bit at 31250 baud
UART_TX_FIFO = 16  # The SCIF's transmit FIFO, which the DMA fills
TIMER_MIDI_GATE_OUTPUT = 2
MTU2_TSTR, MTU2_TGRA_2, MTU2_TSTR_CST2 = MTU2 + 0x280, MTU2 + 0x008, 0x04
MIDI_TIMER_PRESCALER = 64  # setupTimerWithInterruptHandler(TIMER_MIDI_GATE_OUTPUT, 64, ...)


class MidiTiming:
    """--midi-timing: the MIDI and gate output timer and the DIN MIDI UART, modelled, and when what goes out.

    - The timer (MTU2 channel 2, P0 33.33 MHz / 64, cleared by TGRA): when the firmware starts it (TSTR.CST2, after
      writing TGRA in scheduleMidiGateOutISR(), inlined in routine()), its interrupt midiAndGateOutputTimerInterrupt()
      runs TGRA counts later (Interrupts); stopping it cancels that.
    - The MIDI UART's transmit DMA (channel txDmaChannels[UART_ITEM_MIDI]): when enabled (CHCTRL.SETEN) it sends N0TB
      bytes from N0SA (and N1TB from N1SA with CHCFG.REN, the wrap of the ring buffer) into the 16-byte FIFO, the wire
      sends one byte per 320 µs, and the transfer-end interrupt (MIDI_TX_INT_TrnEnd) comes when the last byte went into
      the FIFO. So txSending and uartGetTxBufferFullnessByItem() (MidiEngine::anythingInOutputBuffer()) behave as on the
      Deluge.
    - Every window: AudioEngine::tickSongFinalizeWindows(), its audioSampleTimer, length and
      timeWithinWindowAtWhichMIDIOrGateOccurs (t), and how many samples the RealTimeDma had been written then: the
      window's sample i is the one written as number that + i, if the rendering buffer was empty when the window was
      rendered (checked: each window starts where the one before ended).
    - Every timer run (the MIDI and gate output of one window, all of it at once): the emulated time it fires against
      the time the DMA reaches the window's sample at t (RealTimeDma: sample n plays from position n.0). That's the
      error of the scheduling; 0 = on time with the audio, -128 = a whole buffer (2.9 ms) early.
    - Every MIDI byte on the wire: when it starts, and what sent it (the timer's interrupt, the UART's transfer-end
      interrupt, or code outside interrupts: MidiEngine::flushMIDI() in PlaybackHandler::doMIDIClockOutTick() or
      AudioEngine's flushMIDIGateBuffers()). MIDI clocks (0xF8) against their own time: PlaybackHandler::
      timeNextMIDIClockOutTick when doMIDIClockOutTick() sends it, i.e. the window's sample there."""

    def __init__(self, emu):
        self.emu = emu
        sym = emu.sym
        self.ints = Interrupts(emu)
        # What sends a byte: back to the code outside interrupts once a handler returns
        self.ints.after_return = lambda e: setattr(self, "source", "code outside interrupts")
        self.timer_isr = sym["midiAndGateOutputTimerInterrupt"]
        self.tx_end_isr = sym.find("MIDI_TX_INT_TrnEnd")
        self.sample_timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
        self.dmac = dmac_channel_base(emu.elf_bytes_at(sym["txDmaChannels"] + 1, 1)[0])  # UART_ITEM_MIDI = 1
        self.tx_buffer, self.tx_size = sym.by_name["midiTxBuffer"]
        out = subprocess.run([emu.tool_prefix + "gdb", "-batch", "-ex",
                              "print (int)&((PlaybackHandler*)0)->timeNextMIDIClockOutTick", emu.elf],
                             capture_output=True, text=True).stdout
        m = re.search(r"^\$1 = (\d+)$", out, re.M)
        if not m:
            raise SystemExit(f"--midi-timing: no offset of PlaybackHandler::timeNextMIDIClockOutTick from gdb:\n{out}")
        self.clock_time_address = sym["playbackHandler"] + int(m.group(1))
        emu.writers[MTU2_TSTR] = self.on_tstr
        emu.writers[MTU2 + 0x006] = self.on_tcnt  # TCNT_2
        emu.writers[self.dmac + 0x28] = self.on_chctrl
        emu.intercept(sym.find("_ZN11AudioEngine23tickSongFinalizeWindows"), self.on_finalize)
        emu.intercept(sym.find("_ZN15PlaybackHandler18doMIDIClockOutTick"), self.on_clock_tick)
        for base in (self.tx_buffer, self.tx_buffer + UNCACHED_MIRROR_OFFSET):
            emu.uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self.on_buffer_write, begin=base, end=base + self.tx_size - 1)
        self.return_hooks = set()
        self.recording = False
        self.windows = []  # dict(start, at, number, n, t)
        self.finalizing = None  # (sp, return address, &numSamples, &t, window) while in tickSongFinalizeWindows()
        self.timers = []  # dict(set, tgra, due, window, fired, isr)
        self.clock_due = None  # timeNextMIDIClockOutTick while doMIDIClockOutTick() runs, until its 0xF8 is buffered
        self.buffered = {}  # Offset in the ring buffer -> (byte, clock time or None, window index)
        self.wire = []  # dict(start, byte, source, clock, window)
        self.wire_free = 0  # When the wire has sent all it was given
        self.fifo = collections.deque()  # When each byte in the FIFO or on the wire ends
        self.source = "code outside interrupts"
        self.model_breaks = 0  # Windows that didn't start where the last one ended in the output
        # Anything already started (before the models were installed) finishes now
        if emu.plain_read(MTU2_TSTR, 1) & MTU2_TSTR_CST2:
            self.ints.schedule("timer", emu.now(), self.timer_isr, self.before_timer)
        if emu.u8(sym["uartItems"] + 8 + 6):  # uartItems[UART_ITEM_MIDI].txSending
            self.ints.schedule("uart", emu.now(), self.tx_end_isr, self.before_tx_end)
        emu.uc.ctl_flush_tb()

    # --- the models

    def on_tstr(self, size, value):
        was, now = self.emu.plain_read(MTU2_TSTR, 1) & MTU2_TSTR_CST2, value & MTU2_TSTR_CST2
        per = MIDI_TIMER_PRESCALER / PERIPHERAL_HZ * CPU_HZ
        if not hasattr(self, "tcnt"):
            self.tcnt, self.match_at, self.start_at, self.start_tcnt, self.residuals = 0, None, None, 0, []
        if now and not was:
            tgra = self.emu.plain_read(MTU2_TGRA_2, 2)
            r = self.tcnt
            n = tgra - r if tgra > r else 65536 - r + tgra
            at = self.emu.now() + n * per
            self.match_at, self.start_at, self.start_tcnt = at, self.emu.now(), r
            if self.recording:
                self.residuals.append(r)
            self.ints.schedule("timer", at, self.timer_isr, self.before_timer)
            if self.recording:
                self.timers.append(dict(set=self.emu.now(), tgra=tgra, due=at, window=len(self.windows) - 1,
                                        fired=None))
        elif was and not now:
            self.ints.cancel("timer")
            t = self.emu.now()
            if self.match_at is not None and t >= self.match_at:
                self.tcnt = int((t - self.match_at) / per) % 65536
            elif self.start_at is not None:
                self.tcnt = (self.start_tcnt + int((t - self.start_at) / per)) % 65536

    def on_tcnt(self, size, value):
        # The firmware writing the counter (only while the timer is stopped, as scheduleMidiGateOutISR() does)
        if not hasattr(self, "tcnt"):
            self.tcnt, self.match_at, self.start_at, self.start_tcnt, self.residuals = 0, None, None, 0, []
        self.tcnt = value & 0xFFFF
        self.match_at = self.start_at = None

    def before_timer(self, emu):
        self.source = "timer interrupt"
        if self.recording and self.timers and self.timers[-1]["fired"] is None:
            self.timers[-1]["fired"] = emu.now()

    def before_tx_end(self, emu):
        self.source = "UART transfer-end interrupt"

    def on_chctrl(self, size, value):
        if not value & 1:  # SETEN
            return
        emu = self.emu
        read = emu.plain_read
        pieces = [(read(self.dmac, 4), read(self.dmac + 0x08, 4))]
        if read(self.dmac + 0x2C, 4) & 0x40000000:  # CHCFG.REN: then N1SA/N1TB
            pieces.append((read(self.dmac + 0x0C, 4), read(self.dmac + 0x14, 4)))
        now = emu.now()
        byte_time = MIDI_BYTE_SECONDS * CPU_HZ
        t = now
        for address, n in pieces:
            offset = (address - self.tx_buffer) % UNCACHED_MIRROR_OFFSET
            for i in range(n):
                o = (offset + i) % self.tx_size
                while self.fifo and self.fifo[0] <= t:
                    self.fifo.popleft()
                if len(self.fifo) >= UART_TX_FIFO:
                    t = self.fifo.popleft()  # Waits for room in the FIFO
                start = max(t, self.wire_free)
                self.wire_free = start + byte_time
                self.fifo.append(self.wire_free)
                if self.recording:
                    byte, clock, window = self.buffered.get(o, (None, None, None))
                    self.wire.append(dict(start=start, byte=byte, source=self.source, clock=clock, window=window))
        self.ints.schedule("uart", max(t, now + 400), self.tx_end_isr, self.before_tx_end)

    def on_buffer_write(self, uc, access, address, size, value, _):
        base = self.tx_buffer if address < UNCACHED_MIRROR_OFFSET else self.tx_buffer + UNCACHED_MIRROR_OFFSET
        for i in range(size):
            byte = (value >> (8 * i)) & 0xFF
            clock = None
            if byte == 0xF8 and self.clock_due is not None:
                clock, self.clock_due = self.clock_due, None
            window = len(self.windows) - (0 if self.finalizing else 1)
            self.buffered[(address - base + i) % self.tx_size] = (byte, clock, window)

    # --- the windows and MIDI clocks

    def on_finalize(self, emu):
        uc = emu.uc
        back = uc.reg_read(UC_ARM_REG_LR) & ~1
        if back not in self.return_hooks:
            self.return_hooks.add(back)
            uc.hook_add(UC_HOOK_CODE, self.at_finalized, begin=back, end=back)
            uc.ctl_remove_cache(back, back + 4)
        dma = emu.dma
        window = dict(start=emu.u32(self.sample_timer), at=emu.now(),
                      number=len(dma.numbers) if dma else None)
        self.finalizing = (uc.reg_read(UC_ARM_REG_SP), back, uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1),
                           window)

    def at_finalized(self, uc, address, size, _):
        if not self.finalizing or self.finalizing[1] != address or self.finalizing[0] != uc.reg_read(UC_ARM_REG_SP):
            return
        _, _, n_at, t_at, window = self.finalizing
        self.finalizing = None
        window.update(n=self.emu.u32(n_at), t=struct.unpack("<i", uc.mem_read(t_at, 4))[0])
        if self.recording:
            last = self.windows[-1] if self.windows else None
            if last and last["number"] is not None and window["number"] != last["number"] + last["n"]:
                self.model_breaks += 1
            self.windows.append(window)
        else:
            self.windows = [window]  # Only the last, for timers set before the recording starts

    def on_clock_tick(self, emu):
        self.clock_due = emu.u32(self.clock_time_address)

    # --- results

    def start(self):
        self.recording = True
        self.windows, self.timers, self.wire, self.model_breaks = [], [], [], 0

    def results(self):
        """Timing errors in samples (+ = late, - = early), against the DMA's position (emu.dma, still in place)."""
        dma = self.emu.dma
        emu = self.emu
        self.recording = False

        def played(window, offset):
            """The DMA's number of the window's sample at offset, None if not written (yet)."""
            if window is None or window < 0 or window >= len(self.windows) or self.windows[window]["number"] is None:
                return None
            i = self.windows[window]["number"] + offset
            return dma.numbers[i] if i < len(dma.numbers) else None

        timers = []
        for timer in self.timers:
            w = self.windows[timer["window"]] if 0 <= timer["window"] < len(self.windows) else None
            if w is None or timer["fired"] is None:
                continue
            t = max(w["t"], 0)
            number = played(timer["window"], t)
            if number is None:
                continue
            timers.append(dict(t=t, window=w["n"], error=dma.exact_position(timer["fired"]) - number,
                               scheduled=dma.exact_position(timer["due"]) - number,
                               needed=number - dma.exact_position(timer["set"]),
                               asked=timer["tgra"] * 65536 / 766245))
        clocks = []
        for byte in self.wire:
            if byte["byte"] == 0xF8 and byte["clock"] is not None and byte["window"] is not None:
                w = self.windows[byte["window"]] if byte["window"] < len(self.windows) else None
                # A clock due before the window (t < 0) is at the window's first sample at the earliest
                offset = max(struct.unpack("<i", struct.pack("<I", (byte["clock"] - w["start"]) & 0xFFFFFFFF))[0], 0) \
                    if w else 0
                number = played(byte["window"], offset) if w else None
                if number is not None:
                    clocks.append(dict(error=dma.exact_position(byte["start"]) - number, source=byte["source"]))

        notes = []
        for byte in self.wire:
            b = byte["byte"]
            if b is not None and b & 0xE0 == 0x80 and byte["window"] is not None and byte["window"] < len(self.windows):
                number = played(byte["window"], 0)
                if number is not None:
                    notes.append(dict(error=dma.exact_position(byte["start"]) - number, kind=hex(b), source=byte["source"],
                                      window=byte["window"]))
        self.notes_dump = notes

        def stats(errors):
            e = np.array(errors, dtype=np.float64)
            if not len(e):
                return dict(count=0)
            return dict(count=len(e), mean=float(e.mean()), min=float(e.min()), max=float(e.max()),
                        percentiles={p: float(np.percentile(e, p)) for p in (1, 5, 50, 95, 99)},
                        early_over_64=int(np.sum(e < -64)), within_4=int(np.sum(np.abs(e) <= 4)),
                        histogram={f"{lo}..{lo + 7}": int(np.sum((e >= lo) & (e < lo + 8)))
                                   for lo in range(-136, 136, 8) if np.sum((e >= lo) & (e < lo + 8))})
        sources = collections.Counter(b["source"] for b in self.wire)
        return dict(
            windows=len(self.windows), windows_not_starting_where_the_last_ended=self.model_breaks,
            underrun_samples=dma.underruns, interrupts_delayed_by_masking=self.ints.masked_delays,
            timer_runs=dict(scheduled=stats([x["scheduled"] for x in timers]),
                            scheduled_at_window_start=stats([x["scheduled"] for x in timers if x["t"] == 0]),
                            scheduled_later_in_window=stats([x["scheduled"] for x in timers if x["t"] > 0]),
                            all=stats([x["error"] for x in timers]),
                            at_window_start=stats([x["error"] for x in timers if x["t"] == 0]),
                            later_in_window=stats([x["error"] for x in timers if x["t"] > 0]),
                            needing_over_128=sum(1 for x in timers if x["needed"] > 128),
                            asked_minus_needed=stats([x["asked"] - x["needed"] for x in timers])),
            tcnt_residual_at_start=dict(count=len(getattr(self, "residuals", [])), max=max(getattr(self, "residuals", [0]) or [0]), over_40=sum(1 for x in getattr(self, "residuals", []) if x > 40)),
            notes_on_wire=dict(all=stats([n["error"] for n in self.notes_dump]), log=[(n["kind"], round(n["error"], 1), n["source"], n["window"]) for n in self.notes_dump]),
            midi_clocks_on_wire=dict(all=stats([c["error"] for c in clocks]),
                                     by_source={s: stats([c["error"] for c in clocks if c["source"] == s])
                                                for s in sorted({c["source"] for c in clocks})}),
            wire_bytes=dict(count=len(self.wire), by_source=dict(sources.most_common()),
                            by_kind=dict(collections.Counter(
                                "clock" if b["byte"] == 0xF8 else "note on" if b["byte"] is not None and
                                b["byte"] & 0xF0 == 0x90 else "note off" if b["byte"] is not None and
                                b["byte"] & 0xF0 == 0x80 else "other" for b in self.wire).most_common())),
            timer_log_fields=["t", "window length", "error", "error as scheduled",
                              "samples from setting the timer to the sample",
                              "samples the timer was set to (TGRA / 11.69)"],
            timer_log=[(x["t"], x["window"], round(x["error"], 2), round(x["scheduled"], 2), round(x["needed"], 2),
                        round(x["asked"], 2))
                       for x in timers])


def midi_report(r, log, what):
    def line(name, s):
        if not s["count"]:
            return f"  {name}: none"
        p = s["percentiles"]
        return (f"  {name}: {s['count']}, error mean {s['mean']:+.1f}, min {s['min']:+.1f}, max {s['max']:+.1f}, "
                f"1/50/99 %: {p[1]:+.1f} / {p[50]:+.1f} / {p[99]:+.1f}; within 4 samples {s['within_4']}, "
                f"more than 64 early {s['early_over_64']}")
    t = r["timer_runs"]
    log(f"\nMIDI/gate timing, {what} (samples, + = late, against the DMA reaching the window's sample; "
        f"{r['windows']} windows, {r['windows_not_starting_where_the_last_ended']} not starting where the last "
        f"ended, {r['underrun_samples']} underrun samples):")
    log(line("timer as scheduled (TGRA)", t["scheduled"]))
    log(line("  at the window's start (t = 0: notes, clocks on a tick)", t["scheduled_at_window_start"]))
    log(line("  later in the window (t > 0: MIDI clocks)", t["scheduled_later_in_window"]))
    log(line("timer interrupt as run (later while Song::renderAudio() masks interrupts)", t["all"]))
    log(line("  at the window's start", t["at_window_start"]))
    log(line("  later in the window", t["later_in_window"]))
    log(f"  timer runs whose sample was more than 128 samples ahead: {t['needing_over_128']}; the timer's delay "
        f"(TGRA) minus that time: mean {t['asked_minus_needed'].get('mean', 0):+.1f}")
    log(line("MIDI clocks on the wire", r["midi_clocks_on_wire"]["all"]))
    log(f"  TCNT residual when the timer was started: {r['tcnt_residual_at_start']}")
    log(line("NOTE status bytes on the wire (vs their window's sample 0)", r["notes_on_wire"]["all"]))
    for s, v in r["midi_clocks_on_wire"]["by_source"].items():
        log(line(f"  sent by {s}", v))
    log(f"  bytes on the wire: {r['wire_bytes']['count']}, " + ", ".join(
        f"{k} {v}" for k, v in r["wire_bytes"]["by_kind"].items()) + "; by " + ", ".join(
        f"{k} {v}" for k, v in r["wire_bytes"]["by_source"].items()))
    for name, key in (("as scheduled", "scheduled"), ("as run", "all")):
        hist = t[key].get("histogram")
        if hist:
            log(f"  timer errors {name}, by 8 samples: " + ", ".join(f"{k}: {v}" for k, v in hist.items()))
    log(f"  interrupts that waited for the code to unmask them (checked every µs): "
        f"{r['interrupts_delayed_by_masking']}")


AUDIO_TASK_INTERVAL = 16  # samples: the task manager's target time between AudioEngine::routine() calls


def play_realtime(emu, player, samples, midi, log):
    """--midi-timing without --save-while-playing: plays with the DMA in real time (RealTimeDma) as the task manager
    would (registerTasks()): AudioEngine::routine() every AUDIO_TASK_INTERVAL samples (its target time between calls),
    the playback handler's routine as often, cluster loading after each, and when nothing is due, idle (the emulated
    time goes on without code running; interrupts are run at their time)."""
    dma = RealTimeDma(emu)
    midi.start()
    per = AUDIO_TASK_INTERVAL * CPU_HZ / SAMPLE_RATE
    next_audio = next_playback = emu.now()
    start = dma.position()
    calls = 0
    t = time.time()
    while dma.position() - start < samples:
        ran = False
        if emu.now() >= next_audio:
            next_audio = emu.now() + per
            emu.call(player.routine)
            dma.collect()
            calls += 1
            ran = True
        if emu.now() >= next_playback:
            next_playback = emu.now() + per
            emu.call(player.playback_routine)
            ran = True
        emu.call(player.load_clusters)
        if not ran:
            emu.interrupts.idle_until(min(next_audio, next_playback))
    dma.collect()
    log(f"played {samples} samples in real time: {calls} routine() calls, idle "
        f"{emu.idle / CPU_HZ * 1e3:.0f} ms of {(emu.now() - dma.start_instructions) / CPU_HZ * 1e3:.0f} ms, max gap "
        f"{dma.max_gap} ({time.time() - t:.1f} s)")
    return dma


SAVE_PATH = "SONGS/SAVETEST.XML"
AUDIO, CLUSTERS, UI, FILES, XML = ("audio (AudioEngine::routine())", "cluster loading (loadAnyEnqueuedClusters)",
                                   "UI timers, OLED, PIC", "FatFS (f_write, f_close, ...; the card itself is instant)",
                                   "XML generation (the rest)")


def save_while_playing(emu, player, warmup_bars, out_dir, log, repeat_samples=0, midi=None):
    """--save-while-playing: after the warm-up (windows of 128 as in measure()), the DMA runs in real time
    (RealTimeDma) and the firmware saves the playing song to SAVE_PATH as SaveSongUI does: StorageManager::
    createXMLFile(), Song::writeToFile(), XMLSerializer::closeFileAfterWriting(). Nothing but the firmware itself services
    the audio meanwhile (as on the Deluge, where the save runs inside a task and only the SD card's waits yield; those are
    instant here). Reports the save's emulated duration, what it is spent on, the routine() calls and their windows, and
    the gaps (see RealTimeDma). With repeat_samples (--midi-timing), it saves again and again until the DMA has played
    that many samples, and midi (a MidiTiming) records the MIDI timing meanwhile (midi_timing.json)."""
    t = time.time()
    player.play(int(warmup_bars * BAR))
    log(f"warm-up: {warmup_bars:g} bar(s) ({time.time() - t:.1f} s)")
    sym = emu.sym
    timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
    calls = []  # Top-level routine() calls: dict
    renders = []  # audioSampleTimer where each window's rendering began (Song::renderAudio())
    regions = Regions(emu, [("_ZN11AudioEngine7routineEv", AUDIO),
                            ("_ZN16AudioFileManager23loadAnyEnqueuedClusters", CLUSTERS),
                            ("_ZN14UITimerManager7routineEv", UI), ("oledRoutine", UI), ("uartFlushIfNotSending", UI),
                            ("f_write", FILES), ("f_close", FILES), ("f_open", FILES), ("f_read", FILES),
                            ("f_lseek", FILES), ("f_sync", FILES)], XML)

    def routine_entered(e, back):
        if any(s[0] == AUDIO for s in regions.stack):
            return None  # From inside routine(): returns at once (audioRoutineLocked)
        return dict(gap=e.dma.gap(), timer=e.u32(timer), caller=e.sym.name_at(back).split("(")[0], renders=len(renders),
                    at=e.bc.bc_total())

    def routine_left(e, extra, instructions):
        if extra is None:
            return
        e.dma.collect()
        starts = renders[extra["renders"]:] + [e.u32(timer)]  # audioSampleTimer grows by each window after it
        extra.update(instructions=instructions, samples=(starts[-1] - extra["timer"]) & 0xFFFFFFFF,
                     windows=[(b - a) & 0xFFFFFFFF for a, b in zip(starts, starts[1:])], max_gap=e.dma.max_gap)
        calls.append(extra)
    regions.on_enter[AUDIO] = routine_entered
    regions.on_exit[AUDIO] = routine_left
    emu.intercept(sym.find("_ZN4Song11renderAudioEP12StereoSample"),
                  lambda e: renders.append(e.u32(timer)) and None)
    dma = RealTimeDma(emu)
    emu.uc.ctl_flush_tb()  # Code hooks added now take effect only where the code is translated again
    if midi:
        midi.start()

    strings = {}
    at = STOP + 0x100
    for name, text in (("path", SAVE_PATH), ("begin", '<?xml version="1.0" encoding="UTF-8"?>\n<song\n'),
                       ("end", "\n</song>\n")):
        emu.uc.mem_write(at, text.encode() + b"\0")
        strings[name] = at
        at += len(text) + 4 & ~3
    storage, serializer, song = sym["storageManager"], sym["smSerializer"], emu.u32(sym["currentSong"])
    t = time.time()
    before = emu.bc.bc_total()
    regions.last = before
    # createXMLFile() is a clone without the XMLSerializer (always smSerializer); Song::writeToFile() returns nothing
    steps = [("createXMLFile", sym.find("_ZN14StorageManager13createXMLFile"), (storage, strings["path"], 1, 0), True),
             ("Song::writeToFile", sym["_ZN4Song11writeToFileER14StorageManager"], (song, storage), False),
             ("closeFileAfterWriting", sym.find("_ZN13XMLSerializer21closeFileAfterWriting"),
              (serializer, strings["path"], strings["begin"], strings["end"]), True)]
    saves = 0
    while True:
        for name, address, arguments, returns_error in steps:
            error = emu.call(address, *arguments, timeout_s=30)
            if returns_error and error:
                raise SystemExit(f"{name}: error {error}")
        saves += 1
        if dma.position() - dma.start_position >= repeat_samples:
            break
    regions.charge()
    dma.collect()
    total = emu.bc.bc_total() - before
    end_gap = dma.gap()
    log(f"saved {SAVE_PATH}" + (f" {saves} times" if saves > 1 else "") +
        f": {total / 1e6:,.1f}M instructions ({time.time() - t:.1f} s)")
    xml = fat32.read_file(emu.sd_path, SAVE_PATH)

    ms = lambda n: n / CPU_HZ * 1e3  # noqa: E731
    windows = np.array([w for c in calls for w in c["windows"]])
    rendering = [c for c in calls if c["windows"]]
    gaps_in = np.array([c["gap"] for c in calls])
    per_caller = collections.Counter(c["caller"] for c in calls)
    samples = np.array(dma.samples, dtype=np.int64).reshape(-1, 2)
    pcm = (samples >> 16).astype("<i2").tobytes()
    with open(os.path.join(out_dir, "save.wav"), "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, 2, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 16) + b"data" +
                struct.pack("<I", len(pcm)) + pcm)
    result = dict(
        saves=saves, file=dict(path=SAVE_PATH, bytes=len(xml), sha256=hashlib.sha256(xml).hexdigest()),
        duration_ms=ms(total), instructions=total,
        by_what={k: dict(ms=ms(v), share=v / total) for k, v in regions.time.most_common()},
        not_in_elf=regions.missing,
        routine_calls=dict(
            count=len(calls), rendering=len(rendering), output_only=len(calls) - len(rendering),
            by_caller=dict(per_caller.most_common()),
            mean_instructions=float(np.mean([c["instructions"] for c in calls])) if calls else None,
            mean_instructions_rendering=float(np.mean([c["instructions"] for c in rendering])) if rendering else None,
            mean_interval_samples=(ms(calls[-1]["at"] - calls[0]["at"]) * SAMPLE_RATE / 1e3 / (len(calls) - 1))
            if len(calls) > 1 else None),
        windows=dict(count=len(windows), samples=int(windows.sum()) if len(windows) else 0,
                     mean=float(windows.mean()) if len(windows) else None,
                     percentiles={p: float(np.percentile(windows, p)) for p in (5, 25, 50, 75, 95)}
                     if len(windows) else None,
                     histogram={f"{lo}-{lo + 15}": int(np.sum((windows >= lo) & (windows < lo + 16)))
                                for lo in range(0, 129, 16)} if len(windows) else None),
        gaps=dict(max=int(max(dma.max_gap, end_gap)), at_end=int(end_gap), underrun_samples=dma.underruns,
                  misplaced=dma.misplaced,
                  at_routine_entry=dict(mean=float(gaps_in.mean()), max=int(gaps_in.max()),
                                        percentiles={p: float(np.percentile(gaps_in, p)) for p in (50, 95, 99)})
                  if len(calls) else None),
        samples_written=len(samples), samples_played=int(dma.position() - dma.start_position),
        call_log_fields=["instructions since the save began", "gap at entry", "instructions", "samples rendered",
                         "windows"],
        call_log=[(int(c["at"] - before), int(c["gap"]), int(c["instructions"]), int(c["samples"]), c["windows"])
                  for c in calls])
    json.dump(result, open(os.path.join(out_dir, "save_result.json"), "w"), indent=1)
    midi_result = midi.results() if midi else None
    emu.dma = None

    log(f"\nsave while playing: {result['duration_ms']:.1f} ms emulated ({total / 1e6:,.1f}M instructions), "
        f"{len(xml):,} bytes of XML")
    for k, v in result["by_what"].items():
        log(f"  {v['ms']:8.1f} ms {v['share'] * 100:5.1f}%  {k}")
    r = result["routine_calls"]
    log(f"routine() calls: {r['count']} ({r['rendering']} rendering, {r['output_only']} only outputting), one every "
        f"{r['mean_interval_samples'] or 0:.1f} samples; by caller: "
        + ", ".join(f"{k} {v}" for k, v in r["by_caller"].items()))
    w = result["windows"]
    if w["count"]:
        log(f"windows rendered: {w['count']}, {w['samples']} samples, mean {w['mean']:.1f}, percentiles 5/25/50/75/95: "
            + " / ".join(f"{x:.0f}" for x in w["percentiles"].values()) + "; by size: "
            + ", ".join(f"{k}: {v}" for k, v in w["histogram"].items() if v))
    g = result["gaps"]
    log(f"gaps (samples the DMA read since the buffer was last full; 128 = underrun): max {g['max']}, at the end "
        f"{g['at_end']}, samples written with a gap of 128 or more: {g['underrun_samples']}, misplaced: {g['misplaced']}"
        + (f"; at routine() entry: mean {g['at_routine_entry']['mean']:.1f}, max {g['at_routine_entry']['max']}"
           if g["at_routine_entry"] else ""))
    log(f"samples: {result['samples_played']} played by the DMA during the save, {result['samples_written']} written "
        f"(save.wav); file sha256 {result['file']['sha256'][:16]}")
    if midi_result:
        json.dump(midi_result, open(os.path.join(out_dir, "midi_timing.json"), "w"), indent=1)
        midi_report(midi_result, log, f"while saving ({saves} saves back to back)")
    return result


NEVER = STOP + 0xA0  # movs r0, #0; bx lr: a yield condition that never holds (run_task_manager())


def gdb_values(emu, expressions):
    """Integers the toolchain's gdb prints for these expressions on the ELF (struct offsets, sizes)."""
    out = subprocess.run([emu.tool_prefix + "gdb", "-batch"] + [x for e in expressions for x in ("-ex", f"print {e}")]
                         + [emu.elf], capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (-?\d+)$", out, re.M)]
    if len(values) != len(expressions):
        raise SystemExit(f"gdb: {expressions}:\n{out[-500:]}")
    return values


def drain_uarts(emu):
    """No transfer-end interrupt here: whatever the firmware put in the PIC's and the MIDI UART's rings counts as sent
    (as tests/songchange does for the PIC), so they never fill up."""
    items = emu.sym["uartItems"]
    for item in range(2):
        w = struct.unpack("<H", emu.uc.mem_read(items + 8 * item, 2))[0]
        emu.uc.mem_write(items + 8 * item + 2, struct.pack("<HHBB", w, w, 1, 0))


def run_task_manager(emu, seconds):
    """Runs the firmware's own task manager for `seconds` of emulated time, as deluge_main()'s loop does:
    TaskManager::yield() with a condition that never holds and that timeout. registerTasks()'s tasks run at their own
    intervals, as chooseBestTask() picks them by the emulated time (the audio routine every ~16 samples at most, the
    cluster loading, the kit RAM saver every second, the CPU monitor every 50 ms, UI, OLED, PIC, ...); the time the
    scheduler itself spins counts too. Needs the task list as boot() left it (Player empties it) and, for the audio,
    a RealTimeDma. The yield counts as a task's run: the current task ID is set to an unused slot first, so no real
    task's duration statistics (which the audio routine's culling reads) get it. The UARTs' rings are drained at
    every flush (drain_uarts())."""
    sym = emu.sym
    if not getattr(emu, "task_manager_setup", None):
        task_size, current_id, handle = gdb_values(emu, ["sizeof(Task)", "(int)&((TaskManager*)0)->currentID",
                                                         "(int)&((Task*)0)->handle"])
        base, size = sym.by_name["taskManager"]
        free = [i for i in range(size // task_size) if not emu.u32(base + i * task_size + handle)]
        if not free:
            raise SystemExit("no free slot in the task list")
        emu.uc.mem_write(NEVER, b"\x00\x20\x70\x47")
        emu.intercept(sym.find("uartFlushIfNotSending"), lambda e: drain_uarts(e))
        emu.uc.ctl_flush_tb()
        emu.task_manager_setup = (base + current_id, free[-1], sym.find("_ZN11TaskManager5yieldEPFbvEd"))
    at, slot, yield_ = emu.task_manager_setup
    emu.uc.mem_write(at, struct.pack("<b", slot))
    drain_uarts(emu)
    emu.uc.reg_write(UC_ARM_REG_D0, struct.unpack("<Q", struct.pack("<d", seconds))[0])
    emu.call(yield_, NEVER)


CPU_STATS_FIELDS = ["ticks", "busyTicks", "samples", "peakTicks", "peakSamples", "voicesNow", "voicesMax", "direMax",
                    "direSamples", "culled", "sdLoads", "sdTicks", "sdCardTicks", "sdMaxTicks", "sdCardMaxTicks",
                    "maxGapTicks"]
CPU_STATS_WINDOW = "<I4xQ9I4xQQ3I4x"  # cpu_stats::Window (88 bytes)
CPU_STATS_SUMMARY = ["windowMs", "dspAvgPermille", "dspPeakPermille", "voicesNow", "voicesMax", "direMax",
                     "direSharePermille", "culled", "sdLoads", "sdAvgUs", "sdMaxUs", "maxGapUs", "samples",
                     "sdCardAvgUs", "sdCardMaxUs"]


class CpuStats:
    """Settings > CPU monitor (cpu_stats) switched on, and its half-second windows as the firmware publishes them
    (Collector's half_ slot), collected each time its task (cpu_stats::routine()) runs. summaries() has the firmware's
    own cpu_stats::summarize() compute what the display shows (CPU average and peak in 0.1 %, SD read times, ...)."""

    def __init__(self, emu, mode=1):
        self.emu = emu
        sym = emu.sym
        emu.uc.mem_write(sym["_ZN9cpu_stats4modeE"], bytes([mode]))
        emu.uc.mem_write(sym["_ZN9cpu_stats7enabledE"], b"\x01")
        half, window = gdb_values(emu, ["(int)&cpu_stats::collector.half_ - (int)&cpu_stats::collector",
                                        "(int)&cpu_stats::collector.half_.window - (int)&cpu_stats::collector.half_"])
        self.slot = sym["_ZN9cpu_stats9collectorE"] + half
        self.window_at = self.slot + window
        self.seq = emu.u32(self.slot)
        self.windows = []  # (emulated seconds when collected, dict)
        emu.intercept(sym.find("_ZN9cpu_stats7routineEv"), self.collect)
        emu.uc.ctl_flush_tb()

    def collect(self, emu=None):
        seq = self.emu.u32(self.slot)
        if seq != self.seq and not seq & 1:
            self.seq = seq
            raw = bytes(self.emu.uc.mem_read(self.window_at, struct.calcsize(CPU_STATS_WINDOW)))
            self.windows.append((self.emu.seconds(), dict(zip(CPU_STATS_FIELDS, struct.unpack(CPU_STATS_WINDOW, raw)))))

    def summaries(self):
        """The firmware's cpu_stats::summarize() of each window collected (called here, between runs)."""
        out = []
        f = self.emu.sym.find("_ZN9cpu_stats9summarizeE")
        for t, w in self.windows:
            self.emu.uc.mem_write(STOP + 0x400, struct.pack(CPU_STATS_WINDOW, *(w[k] for k in CPU_STATS_FIELDS)))
            self.emu.call(f, STOP + 0x600, STOP + 0x400)
            values = struct.unpack(f"<{len(CPU_STATS_SUMMARY)}I", self.emu.uc.mem_read(STOP + 0x600, 4 * len(CPU_STATS_SUMMARY)))
            out.append(dict(at_s=t, **dict(zip(CPU_STATS_SUMMARY, values))))
        return out


def ram_usage(emu):
    """The GeneralMemoryAllocator's regions (internal RAM, SDRAM, the stealable SDRAM with the sample clusters): size,
    bytes free (its empty-space records) and allocations."""
    offsets = gdb_values(emu, ["sizeof(MemoryRegion)", "(int)&((MemoryRegion*)0)->emptySpaces",
                               "(int)&((ResizeableArray*)0)->memory", "(int)&((ResizeableArray*)0)->numElements",
                               "(int)&((ResizeableArray*)0)->memorySize", "(int)&((ResizeableArray*)0)->memoryStart",
                               "(int)&((ResizeableArray*)0)->elementSize"])
    region_size, empty, memory, count, msize, mstart, esize = offsets
    base = emu.sym["_ZZN22GeneralMemoryAllocator3getEvE22generalMemoryAllocator"]
    regions = []
    for r in range(3):
        a = base + r * region_size
        start, end, allocations = struct.unpack("<3I", emu.uc.mem_read(a, 12))
        arr = a + empty
        mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
        free = 0
        for i in range(n):
            free += emu.u32(mem + ((first + i) % max(size, 1)) * es)
        regions.append(dict(start=start, end=end, size=end - start, free=free, used=end - start - free,
                            allocations=allocations, empty_spaces=n))
    return regions


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
    ap.add_argument("--save-while-playing", action="store_true",
                    help="instead of the measurement: after the warm-up bars, save the playing song (to "
                         f"{SAVE_PATH}) with the DMA in real time; reports the save's duration by what runs, the "
                         "routine() calls and windows, and the gaps in the audio (save_result.json, save.wav)")
    ap.add_argument("--midi-timing", action="store_true",
                    help="models the MIDI/gate output timer and the DIN MIDI UART (MidiTiming) and reports when the "
                         "MIDI and gate output goes out against the audio (midi_timing.json): after the warm-up, plays "
                         "--bars bars in real time as the task manager would (play_realtime()), or with "
                         "--save-while-playing saves again and again for --bars bars")
    ap.add_argument("--fill", type=lambda x: int(x, 0),
                    help="fill the internal RAM and the SDRAM (not the peripherals) with this 32-bit word before boot "
                         "(default: zeros); two different words give the same measured.wav unless the firmware reads "
                         "memory it never wrote")
    ap.add_argument("--init-sounds", action="store_true",
                    help="initialise what Sound::Sound() leaves uninitialised but reads (SOUND_UNINITIALISED: the LFO "
                         "and mod FX LFO phases, the skip-rendering timestamps), as the proposed fix would; without it "
                         "the output depends on what the RAM held before (stale pointers: the code and data layout)")
    ap.add_argument("--seed", type=lambda x: int(x, 0),
                    help="set the random generator (jcong) to this after boot, before the song loads: Song::"
                         "setupDefault() seeds it from the MTU2's fast timer (TCNT_0), i.e. from the emulated time, "
                         "which depends on the build's code and data layout. With it, measured.wav is bit-exact "
                         "across builds that render the same")
    ap.add_argument("--sd-latency", metavar="CMD_US,SECTOR_US[,POLL_US]",
                    help="the SD card takes time (SdModel), from after boot: each read or write command CMD_US plus "
                         "SECTOR_US per sector of emulated time, during which the firmware calls routineForSD() as "
                         "it waits (POLL_US apart at least; default 0, back to back). E.g. 1000,42.7 for a typical "
                         "SDHC card (~1 ms per command, ~12 MB/s). Default: off, the card is instant")
    ap.add_argument("--poke", action="append", default=[], metavar="SYMBOL=VALUE",
                    help="write this 32-bit value to the firmware's variable after boot (e.g. to try a setting of a "
                         "build that keeps it in a variable)")
    args = ap.parse_args()
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(args.out, exist_ok=True)

    def log(s):
        print(s, flush=True)

    emu = Emulator(args.elf, args.sd, tools, args.build, log, fill=args.fill)
    setup_sd(emu)
    if args.init_sounds:
        init_sounds(emu)
    boot(emu)
    jcong = emu.sym["jcong"]
    log(f"random seed after boot (jcong, from TCNT_0 in Song::setupDefault()): {emu.u32(jcong):#010x}"
        + (f", set to {args.seed:#010x} (--seed)" if args.seed is not None else ""))
    if args.seed is not None:
        emu.w32(jcong, args.seed)
    if args.sd_latency:
        SdModel(emu, *(float(x) for x in args.sd_latency.split(",")))
        log(f"--sd-latency: {emu.sd_model.params}")
    for poke in args.poke:
        name, value = poke.split("=")
        emu.w32(emu.sym[name], int(value, 0))
        log(f"--poke: {name} = {int(value, 0)}")
    load_startup_song(emu)
    if args.write_back:
        write_back_song(emu, args.write_back)
    # The Sounds' names in the song (synths: presetName, kit rows: name), in its order
    xml = fat32.read_file(args.sd, "SONGS/DEFAULT.XML").decode(errors="replace")
    song_names = list(dict.fromkeys(re.findall(r'<sound\b[^>]*?\b(?:presetName|name)="([^"]+)"', xml)))
    if args.init_sounds:
        log(f"--init-sounds: {emu.sounds_initialised} Sounds constructed")
    player = Player(emu, culling=args.culling)
    midi = MidiTiming(emu) if args.midi_timing else None
    player.start()
    if args.save_while_playing:
        save_while_playing(emu, player, args.warmup_bars, args.out, log,
                           repeat_samples=int(args.bars * BAR) if midi else 0, midi=midi)
        return
    if midi:
        t = time.time()
        player.play(int(args.warmup_bars * BAR))
        log(f"warm-up: {args.warmup_bars:g} bar(s) ({time.time() - t:.1f} s)")
        play_realtime(emu, player, int(args.bars * BAR), midi, log)
        r = midi.results()
        json.dump(r, open(os.path.join(args.out, "midi_timing.json"), "w"), indent=1)
        midi_report(r, log, f"normal playback ({args.bars:g} bars in real time)")
        return
    result = measure(emu, player, args.warmup_bars, args.bars, args.out, log, song_names)
    json.dump(result, open(os.path.join(args.out, "result.json"), "w"), indent=1)
    report(result, log)


if __name__ == "__main__":
    main()
