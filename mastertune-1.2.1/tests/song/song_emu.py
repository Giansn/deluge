#!/usr/bin/env python3
"""Runs the real Deluge firmware (deluge.elf) in unicorn and measures what a whole song costs the Cortex-A9.

Usage: song_emu.py <deluge.elf> <sd.img> <out dir> [--bars-warmup N] [--bars N] [--reverb-model M]

What it does:
- Loads the ELF, runs its own reset code (resetprg: the static constructors, main, deluge_main: memory allocator,
  functionsInit, AudioEngine::init, settings, the blank song) with the hardware replaced by plain memory, except a few
  things that must behave: the timers (OSTM, MTU2) count emulated time (instructions at 400 MHz), the DMA channels stand
  still, the SD card's sector reads and writes go to an image file (FatFS's own code on top), and the hardware parts
  that only wait for devices (SD card init, USB, SPI flash, OLED) are skipped. It stops where deluge_main would start the
  task manager.
- Loads the startup song from the SD image (the firmware's own setupStartupSong(), as at boot), starts playback with
  the internal clock (PlaybackHandler::playButtonPressed) and then calls AudioEngine::routine() itself, one call per
  128 samples: the SSI's DMA position is made to show exactly 128 samples free before each call and no more space
  after the render, so every call renders one window of up to 128 samples (shorter only where the song's clock ticks,
  as on the Deluge). Between calls, it runs what the task manager would: the playback handler's routine and the SD
  card's cluster loading.
- Counts instructions per translated block in C (blockcount.c), so a window's cost and the profile by function are
  exact instruction counts of the firmware's machine code (not cycles: 1 instruction per cycle at 400 MHz is the scale).
"""
import argparse
import bisect
import collections
import ctypes
import json
import math

import numpy as np
import os
import struct
import subprocess
import sys
import time

import unicorn
from unicorn import (UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_MEM_UNMAPPED, UC_MODE_ARM, UC_PROT_ALL, Uc,
                     UcError)
from unicorn.arm_const import (UC_ARM_REG_C1_C0_2, UC_ARM_REG_CPSR, UC_ARM_REG_D0, UC_ARM_REG_FPEXC, UC_ARM_REG_LR,
                               UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                               UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_SP,
                               UC_CPU_ARM_CORTEX_A9)

HERE = os.path.dirname(os.path.abspath(__file__))

INTERNAL_RAM, INTERNAL_RAM_SIZE = 0x20000000, 0x300000
SDRAM, SDRAM_SIZE = 0x0C000000, 0x04000000
UNCACHED_MIRROR_OFFSET = 0x40000000
STOP = 0x7FFF0000  # Return address of the calls we make: nothing is executed there
PROGRAM_STACK_TOP = 0x20300000

CPU_HZ = 400e6
PERIPHERAL_HZ = 33.33e6
SAMPLE_RATE = 44100
CYCLES_PER_BLOCK = CPU_HZ * 128 / SAMPLE_RATE  # 1,161,000

OSTM0 = 0xFCFEC000
MTU2 = 0xFCFF0000
MTU_TCNT = {0x306: (0, 64), 0x386: (1, 1), 0x006: (2, 64), 0x210: (3, 64), 0x212: (4, 1024)}  # offset: (timer, prescaler)
DMAC = 0xE8200000
SSI_TX_DMA_CHANNEL = 6

# Status registers that the firmware waits on, preset once to "ready" (the peripherals are plain memory)
SEEDS = {
    0xE800C803: b"\xA0",  # RSPI0 SPSR: transmit buffer empty (SPTEF), receive buffer full (SPRF)
}


def dmac_channel_base(n):
    return n * 64 + (0x200 if n >= 8 else 0)


class Symbols:
    def __init__(self, elf, tool_prefix):
        out = subprocess.run([tool_prefix + "nm", "-S", "-n", "--defined-only", elf], capture_output=True, text=True,
                             check=True).stdout
        demangled = subprocess.run([tool_prefix + "nm", "-S", "-n", "-C", "--defined-only", elf], capture_output=True,
                                   text=True, check=True).stdout
        self.by_name = {}
        self.functions = []  # (start, end, demangled name)
        for line, dline in zip(out.splitlines(), demangled.splitlines()):
            parts = line.split(maxsplit=3)
            dparts = dline.split(maxsplit=3)
            if len(parts) != 4:
                continue
            address, size, kind, name = int(parts[0], 16), int(parts[1], 16), parts[2], parts[3]
            self.by_name[name] = (address, size)
            if len(dparts) == 4:
                self.by_name.setdefault(dparts[3], (address, size))
            if kind in "TtWw" and size:
                self.functions.append((address & ~1, (address & ~1) + size, dparts[3] if len(dparts) == 4 else name))
        self.functions.sort()
        self.starts = [f[0] for f in self.functions]

    def __getitem__(self, name):
        return self.by_name[name][0]

    def find(self, prefix):
        """The one symbol whose (mangled or demangled) name starts with prefix."""
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
    def __init__(self, elf, sd_image, tool_prefix, log):
        self.elf = elf
        self.log = log
        self.tool_prefix = tool_prefix
        self.sym = Symbols(elf, tool_prefix)
        data = open(elf, "rb").read()
        _, phoff, _, _, _, phentsize, phnum = struct.unpack_from("<IIIIHHH", data, 24)
        self.segments = []
        for i in range(phnum):
            p_type, p_offset, p_vaddr, p_paddr, p_filesz, _ = struct.unpack_from("<IIIIII", data, phoff + i * phentsize)
            if p_type == 1 and p_filesz:
                self.segments.append((p_vaddr, p_paddr, data[p_offset:p_offset + p_filesz]))
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)

        # Internal RAM and SDRAM, each also at its uncached mirror (the same host memory)
        self.iram = ctypes.create_string_buffer(INTERNAL_RAM_SIZE)
        self.sdram = ctypes.create_string_buffer(SDRAM_SIZE)
        for base, buf, size in ((INTERNAL_RAM, self.iram, INTERNAL_RAM_SIZE), (SDRAM, self.sdram, SDRAM_SIZE)):
            uc.mem_map_ptr(base, size, UC_PROT_ALL, ctypes.addressof(buf))
            uc.mem_map_ptr(base + UNCACHED_MIRROR_OFFSET, size, UC_PROT_ALL, ctypes.addressof(buf))
        uc.mem_map(STOP & ~0xFFF, 0x1000)
        # What's at address 0 on the Deluge (the boot ROM area) reads, but isn't written: the firmware does read
        # through null pointers now and then (e.g. Clip::~Clip() looks at currentSong while there's none)
        uc.mem_map(0, 0x100000, unicorn.UC_PROT_READ)

        # Timers, DMA, SPI registers behave (models below); the rest of the peripherals is plain memory, mapped when
        # first touched
        self.mmio = {}
        self.readers = {}  # Absolute address -> function(size) returning the value
        self.writers = {}  # Absolute address -> function(size, value)
        self.setup_models()
        for page in sorted({a & ~0xFFF for a in list(self.readers) + list(self.writers)}):
            uc.mmio_map(page, 0x1000, self.mmio_read, page, self.mmio_write, page)
        self.mapped_lazily = []
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, self.on_unmapped)
        uc.hook_add(UC_HOOK_INTR, self.on_interrupt)

        # The image, as the bootloader leaves it: every segment at its load address
        for vaddr, paddr, content in self.segments:
            uc.mem_write(paddr, content)

        uc.reg_write(UC_ARM_REG_C1_C0_2, uc.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))  # CP10/CP11 (VFP/NEON)
        uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)

        # Instruction counting
        lib = ctypes.CDLL(os.path.join(os.environ.get("SONG_BUILD", HERE), "blockcount.so"))
        lib.bc_total.restype = ctypes.c_uint64
        lib.bc_dump.restype = ctypes.c_uint32
        lib.bc_install.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        self.bc = lib
        if lib.bc_install(uc._uch, ctypes.addressof(self.iram), INTERNAL_RAM, INTERNAL_RAM_SIZE) != 0:
            raise SystemExit("could not install the block hook")

        # SD card: an image file (read and written in place)
        self.sd_path = sd_image
        self.sd_fd = os.open(sd_image, os.O_RDWR)
        self.sd_reads = self.sd_writes = 0

        self.intercepts = {}
        self.dma_free = None  # Set while measuring: see ssi_position()
        self.time_offset = 0.0

    # --- time: instructions at 400 MHz

    def seconds(self):
        return self.bc.bc_total() / CPU_HZ + self.time_offset

    def setup_models(self):
        r, w = self.readers, self.writers
        # OSTM0: free-running counter (the task manager's clock)
        r[OSTM0 + 4] = lambda size: int(self.seconds() * PERIPHERAL_HZ) & 0xFFFFFFFF
        # MTU2: the 16-bit system timers, counting at the peripheral clock / their prescaler
        for offset, (_, prescaler) in MTU_TCNT.items():
            r[MTU2 + offset] = lambda size, p=prescaler: int(self.seconds() * PERIPHERAL_HZ / p) & 0xFFFF
        # DMA channels stand still: current address = where they start; the SSI's is ssi_position()
        for ch in range(16):
            base = DMAC + dmac_channel_base(ch)
            r[base + 0x18] = lambda size, b=base: self.plain_read(b, 4)
            r[base + 0x1C] = lambda size, b=base: self.plain_read(b + 4, 4)
            r[base + 0x24] = lambda size: 0x60  # CHSTAT: transfer ended (END, TC), not enabled
        r[DMAC + dmac_channel_base(SSI_TX_DMA_CHANNEL) + 0x18] = lambda size: self.ssi_position()
        # The UARTs' receive DMA (PIC, MIDI): nothing new arrived, i.e. it writes where the reader is
        rx_channels = self.sym["rxDmaChannels"]
        rx_read_addresses = self.sym["rxBufferReadAddr"]
        for item in range(2):
            channel = struct.unpack_from("<B", self.elf_bytes_at(rx_channels + item, 1))[0]
            r[DMAC + dmac_channel_base(channel) + 0x1C] = lambda size, a=rx_read_addresses + 4 * item: self.u32(a)
        # RSPI0 (CV, OLED): transmit buffer always empty, the receive buffer full after each write until read
        spi = 0xE800C800
        self.spi_rx_full = False

        def spsr(size):
            return 0x20 | (0x80 if self.spi_rx_full else 0)

        def spdr_read(size):
            self.spi_rx_full = False
            return 0

        def spdr_write(size, value):
            self.spi_rx_full = True

        # SPIBSC (the SPI flash holding the settings): every transfer has ended at once (CMNSR: TEND, SSL negated),
        # the flash reads as zeros (no settings saved: the firmware's defaults)
        r[0x3FEFA000 + 0x48] = lambda size: 0x1
        # SMRDR0/1, the data read: erased flash (0xFF), except for the status register (RDSR, 0x05): never busy
        def flash_data(size):
            command = (self.plain_read(0x3FEFA000 + 0x24, 4) >> 16) & 0xFF
            return 0 if command == 0x05 else (1 << (8 * size)) - 1
        r[0x3FEFA000 + 0x38] = flash_data
        r[0x3FEFA000 + 0x3C] = flash_data
        r[spi + 3] = spsr
        r[spi + 4] = spdr_read
        w[spi + 4] = spdr_write

    def elf_bytes_at(self, address, n):
        for vaddr, paddr, content in self.segments:
            if paddr <= address < paddr + len(content):
                return content[address - paddr:address - paddr + n]
        raise KeyError(hex(address))

    def mmio_read(self, uc, offset, size, page):
        address = page + offset
        reader = self.readers.get(address)
        if reader:
            return reader(size)
        return self.plain_read(address, size)

    def mmio_write(self, uc, offset, size, value, page):
        address = page + offset
        writer = self.writers.get(address)
        if writer:
            writer(size, value)
        for i in range(size):
            self.mmio[address + i] = (value >> (8 * i)) & 0xFF

    def plain_read(self, address, size):
        value = 0
        for i in range(size):
            value |= self.mmio.get(address + i, 0) << (8 * i)
        return value

    def on_unmapped(self, uc, access, address, size, value, _):
        region = address & ~0xFFFFF
        if address < 0x100000:
            self.log(f"access to {address:#x} (null pointer?) at {self.sym.name_at(uc.reg_read(UC_ARM_REG_PC))}")
            return False
        try:
            uc.mem_map(region, 0x100000)
        except UcError:
            # Next to an MMIO page: map just this page
            region = address & ~0xFFF
            uc.mem_map(region, 0x1000)
        self.mapped_lazily.append(region)
        for seed_address, seed in SEEDS.items():
            if region <= seed_address < region + (0x100000 if (region & 0xFFFFF) == 0 else 0x1000):
                uc.mem_write(seed_address, seed)
        return True

    def on_interrupt(self, uc, intno, _):
        pc = uc.reg_read(UC_ARM_REG_PC)
        raise SystemExit(f"exception {intno} at {self.sym.name_at(pc)}")

    # --- the SSI's DMA position (what getTxBufferCurrentPlace() reads)

    def ssi_position(self):
        """While measuring: 127 samples free (the DMA just behind where the renderer has written up to) until the
        window is rendered, then none, so each AudioEngine::routine() renders exactly one window. Before that: the
        buffer always full (nothing rendered)."""
        tx_pos = self.u32(self.sym["_ZN11AudioEngine14i2sTXBufferPosE"]) - UNCACHED_MIRROR_OFFSET
        start = self.sym["ssiTxBuffer"]
        free = self.dma_free if self.dma_free is not None else 0
        pos = start + ((tx_pos - start + free * 8) % (128 * 8))
        return pos

    # --- memory helpers

    def u32(self, address):
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def w32(self, address, value):
        self.uc.mem_write(address, struct.pack("<I", value & 0xFFFFFFFF))

    def u8(self, address):
        return self.uc.mem_read(address, 1)[0]

    def w8(self, address, value):
        self.uc.mem_write(address, bytes([value & 0xFF]))

    def cstring(self, address, limit=256):
        out = bytes(self.uc.mem_read(address, limit))
        return out.split(b"\0")[0].decode("latin-1")

    # --- intercepting functions

    def intercept(self, address, handler):
        """handler(emu) runs when the code reaches address. It returns None to go on, or a value (int) to return from
        the function right there with that value in r0 ("skip")."""
        address &= ~1

        def hook(uc, addr, size, _):
            result = handler(self)
            if result is not None:
                uc.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

        self.intercepts[address] = self.uc.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    def skip_to(self, address, target, before=None):
        """Jumps from address to target (both Thumb code in the same function)."""
        def hook(uc, addr, size, _):
            if before:
                before(self)
            uc.reg_write(UC_ARM_REG_PC, target | 1)
        self.uc.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    # --- calling firmware functions

    def call(self, address, *args, timeout_s=0):
        uc = self.uc
        regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
        for reg, value in zip(regs, args):
            uc.reg_write(reg, value & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_SP, PROGRAM_STACK_TOP)
        uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.run(address | 1, STOP, timeout_s)
        return uc.reg_read(UC_ARM_REG_R0)

    def run(self, start, until, timeout_s=0):
        uc = self.uc
        pc = start
        while True:
            try:
                uc.emu_start(pc, until, timeout=int(timeout_s * 1e6) if timeout_s else 0)
            except UcError as e:
                pc = uc.reg_read(UC_ARM_REG_PC)
                raise SystemExit(f"emulator error: {e} at {self.sym.name_at(pc)}, lr {self.sym.name_at(uc.reg_read(UC_ARM_REG_LR))}")
            pc = uc.reg_read(UC_ARM_REG_PC)
            if pc == until or getattr(self, "stopped", False):
                self.stopped = False
                return
            if not timeout_s:
                return
            # Timed out: say where we are, and go on (so a firmware loop waiting for hardware shows up)
            self.log(f"  ... still running at {self.sym.name_at(pc)} (lr {self.sym.name_at(uc.reg_read(UC_ARM_REG_LR))}), "
                     f"{self.bc.bc_total() / 1e6:,.0f}M instructions")
            thumb = uc.reg_read(UC_ARM_REG_CPSR) & 0x20
            pc |= 1 if thumb else 0

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
        if len(parts) >= 2 and parts[0].endswith(":"):
            try:
                address = int(parts[0][:-1], 16)
            except ValueError:
                continue
            lines.append((address, parts[1].strip(), parts[2].strip() if len(parts) > 2 else ""))
    return lines


def setup_sd(emu):
    """The SD card: FatFS's own code on top of the image. Its sector reads and writes go to the image file, and its
    mount skips the card's initialisation (the SDHI driver, inlined into mount_volume) as if the card said ready."""
    sym = emu.sym
    uc = emu.uc

    def read_sectors(e):
        buf, sector, count = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        data = os.pread(e.sd_fd, count * 512, sector * 512)
        uc.mem_write(buf, data.ljust(count * 512, b"\0"))
        e.sd_reads += count
        return 0

    def write_sectors(e):
        buf, sector, count = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        os.pwrite(e.sd_fd, bytes(uc.mem_read(buf, count * 512)), sector * 512)
        e.sd_writes += count
        return 0

    emu.intercept(sym.find("sd_read_sect"), read_sectors)
    emu.intercept(sym.find("disk_write"), write_sectors)

    # In mount_volume: from where it has cleared fs->fs_type (strb r3, [r5]) and starts disk_initialize(), to where
    # it calls check_fs(fs, 0) for sector 0 (mov r0, r5 and movs r1, #0 before it)
    code = disassemble(emu, "mount_volume.lto_priv.0")
    begin = next(code[i + 1][0] for i, (a, m, o) in enumerate(code) if m == "strb" and o.startswith("r3, [r5, #0]"))
    call = next(i for i, (a, m, o) in enumerate(code) if m == "bl" and "<check_fs>" in o)
    target = code[call - 2][0]
    assert {code[call - 2][1], code[call - 1][1]} == {"movs", "mov"}, code[call - 3:call + 1]
    disk_status = sym["diskStatus"]

    def card_ready(e):
        e.w8(disk_status, 0)

    emu.skip_to(begin, target, card_ready)


def load_startup_song(emu):
    """What the task manager would run once the card is ready: setupStartupSong(), in the template mode, which loads
    SONGS/DEFAULT.XML (or writes the blank song there first if there's none)."""
    sym = emu.sym
    emu.w32(sym["_ZN12FlashStorage22defaultStartupSongModeE"], 1)  # StartupSongMode::TEMPLATE
    t = time.time()
    before = emu.bc.bc_total()
    emu.call(sym["_Z16setupStartupSongv"], timeout_s=10)
    emu.log(f"song loaded: {(emu.bc.bc_total() - before) / 1e6:,.1f}M instructions, {time.time() - t:.1f} s, "
            f"{emu.sd_reads} sectors read, {emu.sd_writes} written")


class Measurement:
    """Drives AudioEngine::routine() one window at a time and records what each costs."""

    def __init__(self, emu):
        self.emu = emu
        sym = emu.sym
        self.routine = sym["_ZN11AudioEngine7routineEv"]
        # The task manager's tasks (lambdas in registerTasks(), in the order they're added): #2 is
        # playbackHandler.routine(), #3 audioFileManager.loadAnyEnqueuedClusters(128, false)
        self.playback_routine = sym.find("_ZZ13registerTasksvENUlvE0_4_FUNEv")
        self.load_clusters = sym.find("_ZZ13registerTasksvENUlvE1_4_FUNEv")
        self.sample_timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
        self.active_voices = sym["_ZN11AudioEngine12activeVoicesE"]
        self.rendering_buffer = sym["_ZN11AudioEngine15renderingBufferE"]
        self.master_l = sym["_ZN11AudioEngine23masterVolumeAdjustmentLE"]
        self.master_r = sym["_ZN11AudioEngine23masterVolumeAdjustmentRE"]
        self.culls = {}  # Calls of AudioEngine::cullVoice() by caller: solicitVoice (a Sound over its maxVoices, or out
        # of memory for voices) or the audio routine's CPU-load culling
        self.rendered = False
        emu.intercept(sym.find("_ZN4Song11renderAudioEP12StereoSample"), self.on_render)
        emu.intercept(sym.find("_ZN11AudioEngine9cullVoiceE"), self.on_cull)

    def on_render(self, emu):
        # From here on, the DMA shows no more free space: this routine() call renders just this window
        emu.dma_free = 0
        self.rendered = True

    def on_cull(self, emu):
        caller = emu.sym.function_at(emu.uc.reg_read(UC_ARM_REG_LR))
        name = caller[2].split("(")[0] if caller else "?"
        self.culls[name] = self.culls.get(name, 0) + 1

    def voices(self):
        return self.emu.u32(self.active_voices + 16)

    def window(self):
        """One AudioEngine::routine() call: returns (instructions, samples rendered, stereo int32 samples)."""
        emu = self.emu
        emu.dma_free = 127
        self.rendered = False
        timer = emu.u32(self.sample_timer)
        before = emu.bc.bc_total()
        emu.call(self.routine)
        instructions = emu.bc.bc_total() - before
        samples = (emu.u32(self.sample_timer) - timer) & 0xFFFFFFFF
        audio = None
        if samples:
            raw = bytes(emu.uc.mem_read(self.rendering_buffer, samples * 8))
            ml = struct.unpack("<i", emu.uc.mem_read(self.master_l, 4))[0]
            mr = struct.unpack("<i", emu.uc.mem_read(self.master_r, 4))[0]
            audio = (raw, ml, mr)
        return instructions, samples, audio

    def other_tasks(self):
        """What the task manager runs besides the audio routine, between windows: the playback handler's routine and
        the SD card's cluster loading. Returns their instructions."""
        emu = self.emu
        emu.dma_free = 0
        before = emu.bc.bc_total()
        emu.call(self.playback_routine)
        emu.call(self.load_clusters)
        return emu.bc.bc_total() - before


def write_back_song(emu, path_out):
    """Has the firmware save the song it loaded (as setupStartupSong() writes the template: unlink DEFAULT.XML, and it
    writes the current song there and loads it again), then copies that file out of the image."""
    sys.path.insert(0, HERE)
    import fat32
    name = b"SONGS/DEFAULT.XML\0"
    emu.uc.mem_write(STOP + 0x100, name)
    emu.call(emu.sym["f_unlink"], STOP + 0x100)
    load_startup_song(emu)
    open(path_out, "wb").write(fat32.read_file(emu.sd_path, "SONGS/DEFAULT.XML"))


def boot(emu):
    """resetprg up to where deluge_main starts the task manager, right after registerTasks(): the tasks are there (the
    song's loading yields to them, the cluster loading among them), but the scheduler's loop doesn't start."""
    sym = emu.sym
    stop_hook = []

    def at_register_tasks(e):
        back = e.uc.reg_read(UC_ARM_REG_LR) & ~1

        def stop_here(uc, address, size, _):
            e.stop()
            uc.hook_del(stop_hook[0])
        stop_hook.append(e.uc.hook_add(UC_HOOK_CODE, stop_here, begin=back, end=back))

    emu.intercept(sym["_Z13registerTasksv"], at_register_tasks)
    emu.uc.reg_write(UC_ARM_REG_SP, PROGRAM_STACK_TOP)
    t = time.time()
    emu.run(sym["resetprg"] | 1, STOP, timeout_s=3)
    emu.log(f"boot: {emu.bc.bc_total() / 1e6:,.1f}M instructions, {time.time() - t:.1f} s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("sd")
    ap.add_argument("out")
    ap.add_argument("--tools", default=None)
    ap.add_argument("--warmup-bars", type=float, default=1)
    ap.add_argument("--bars", type=float, default=2)
    ap.add_argument("--write-back", help="file to save the song as the firmware writes it after loading")
    args = ap.parse_args()
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)), "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(args.out, exist_ok=True)

    def log(s):
        print(s, flush=True)

    emu = Emulator(args.elf, args.sd, tools, log)
    setup_sd(emu)
    boot(emu)
    load_startup_song(emu)
    if args.write_back:
        write_back_song(emu, args.write_back)
    # From here on we call the tasks ourselves. An empty task list also means getLastRunTimeforCurrentTask(), which
    # the audio routine's culling goes by, reads 0: no culling for CPU load, whatever the emulated time
    start, size = emu.sym.by_name["taskManager"]
    emu.uc.mem_write(start, bytes(size))
    m = Measurement(emu)
    emu.call(emu.sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
    if not emu.u8(emu.sym["playbackHandler"] + 16):
        raise SystemExit("playback didn't start")
    result = measure(emu, m, args, log)
    json.dump(result, open(os.path.join(args.out, "result.json"), "w"), indent=1)


def run_windows(m, samples_wanted, record):
    total = 0
    while total < samples_wanted:
        n, samples, audio = m.window()
        other = m.other_tasks()
        total += samples
        if record is not None:
            record.append((n, samples, m.voices(), other, audio))
    return total


def measure(emu, m, args, log):
    bar = SAMPLE_RATE * 2  # 4 beats at 120 BPM
    t = time.time()
    run_windows(m, int(args.warmup_bars * bar), None)
    log(f"warm-up: {args.warmup_bars} bar(s), {time.time() - t:.1f} s")
    emu.bc.bc_reset_counts()
    culls_before = dict(m.culls)
    windows = []
    t = time.time()
    run_windows(m, int(args.bars * bar), windows)
    elapsed = time.time() - t
    total_instr = sum(w[0] for w in windows)
    total_samples = sum(w[1] for w in windows)
    log(f"measured: {args.bars} bars, {len(windows)} windows, {total_samples} samples, {total_instr / 1e6:,.0f}M "
        f"instructions, {elapsed:.1f} s ({total_instr / elapsed / 1e6:.0f}M instructions/s emulated)")
    per_block = total_instr / total_samples * 128
    full = [w[0] for w in windows if w[1] == 128]
    other = sum(w[3] for w in windows) / total_samples * 128
    voices = [w[2] for w in windows]
    culls = {k: v - culls_before.get(k, 0) for k, v in m.culls.items() if v - culls_before.get(k, 0)}

    # Output level: what doSomeOutputting() sends to the codec (full scale = 2^31), from the rendering buffer
    l_all, r_all = [], []
    for w in windows:
        raw, ml, mr = w[4]
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64).reshape(-1, 2)
        l_all.append(x[:, 0] * ml / 2 ** 32 * 256 / 2 ** 31)
        r_all.append(x[:, 1] * mr / 2 ** 32 * 256 / 2 ** 31)
    out = np.stack([np.concatenate(l_all), np.concatenate(r_all)], axis=1)
    peak = float(np.max(np.abs(out)))
    rms = float(np.sqrt(np.mean(out ** 2)))
    clipped = int(np.sum(np.abs(out) >= 1.0))
    wav_path = os.path.join(args.out, "measured.wav")
    pcm = (np.clip(out, -1, 1) * 32767).astype("<i2").tobytes()
    with open(wav_path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, 2, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 16) + b"data" +
                struct.pack("<I", len(pcm)) + pcm)

    profile = profile_by_function(emu, total_samples)
    areas = collections.Counter()
    area_functions = collections.defaultdict(list)
    for name, v in profile.items():
        a = area_of(name)
        areas[a] += v
        area_functions[a].append((name, v))
    result = dict(
        instructions_per_128=per_block, cpu_percent=per_block / CYCLES_PER_BLOCK * 100,
        full_windows=len(full), full_window_mean=float(np.mean(full)), full_window_max=int(np.max(full)),
        full_window_percentiles={p: float(np.percentile(full, p)) for p in (5, 25, 50, 75, 95, 99)},
        max_cpu_percent=float(np.max(full)) / CYCLES_PER_BLOCK * 100,
        windows=len(windows), samples=total_samples,
        short_windows=sum(1 for w in windows if w[1] != 128),
        other_tasks_per_128=other,
        voices_mean=float(np.mean(voices)), voices_max=int(np.max(voices)), voices_min=int(np.min(voices)),
        culls=culls, peak_dbfs=20 * math.log10(peak) if peak else None,
        rms_dbfs=20 * math.log10(rms) if rms else None, clipped_samples=clipped,
        areas={a: dict(per_128=v, percent_of_total=v / per_block * 100, cpu_percent=v / CYCLES_PER_BLOCK * 100,
                       top=[(n, round(x)) for n, x in area_functions[a][:6]])
               for a, v in areas.most_common()},
        profile=profile, window_log=[(w[0], w[1], w[2]) for w in windows],
    )
    return result


# Areas of the profile, by function name (the first rule that matches)
AREAS = [
    ("oscillators", r"Voice::renderOsc|Voice::renderBasicSource|WaveTable|renderWave|Oscillator|render(Sine|Saw|Square|"
                    r"Triangle|Pulse)|getKernel|getWhichKernel|calculatePhaseIncrements|MasterTune::applyToPhase"),
    ("FM (DX7 engine)", r"neon_fm_kernel|FmOpKernel|FmCore|DxVoice|DxPatch|Env::|PitchEnv|Freqlut|Exp2|Sin::|Tanh"),
    ("filters", r"filter::|instantTan|Filter"),
    ("sample reading / interpolation / time-stretch",
     r"SampleLowLevelReader|VoiceSample|TimeStretch|Sample::|SampleCluster|SampleCache|SamplePlaybackGuide|Cluster|"
     r"AudioClip::render|interpolat(e|ion)Buffer|MultisampleRange|getAveragesForCrossfade|AudioFileManager"),
    ("reverb", r"reverb::|Reverb::|Freeverb|Mutable|renderReverb"),
    ("delay", r"Delay"),
    ("drone", r"Drone"),
    ("sidechain / compressors", r"RMSFeedbackCompressor|SideChain|Compressor"),
    ("per-track FX (mod FX, bitcrush, volume/pan/reverb send)",
     r"ModControllableAudio::process|ModFX|Chorus|Phaser|Flanger|processStutter|addAudio|shouldDoPanning"),
    ("song / master (song FX, output to the codec)",
     r"GlobalEffectable|renderSongFX|doSomeOutputting|calcApproxRMS|AbsValueFollower|Metronome|AudioEngine::routine\b|"
     r"renderGlobalEffectableForClip|renderOutput"),
    ("voices: patcher, envelopes, LFOs, voice mixing",
     r"^Voice|Patcher|Envelope|^Sound::|LFO|PatchCable|getExp|interpolateTable|getFinalParameterValue|ModelStack|"
     r"VoiceVector|^Source::|lookup(Release|Attack)Rate|cableTo|VoiceUnison|SoundDrum::|SoundInstrument::"),
    ("playback / sequencing (clips, notes, arpeggiator, ticks)",
     r"Playback|Session|Clip|NoteRow|Arpeggiator|^Song::|Kit::|tickSong|Midi|Arranger|Note::|Output::"),
    ("memory / other", r""),
]


def area_of(name):
    import re
    name = name.split("(")[0]  # The function's name, without its parameter types
    for area, pattern in AREAS:
        if re.search(pattern, name):
            return area
    return "memory / other"


def profile_by_function(emu, total_samples):
    n = 1 << 20
    addresses = (ctypes.c_uint32 * n)()
    instructions = (ctypes.c_uint32 * n)()
    counts = (ctypes.c_uint64 * n)()
    k = emu.bc.bc_dump(addresses, instructions, counts, n)
    by_function = collections.Counter()
    for i in range(k):
        f = emu.sym.function_at(addresses[i])
        by_function[f[2] if f else f"?{addresses[i]:#x}"] += instructions[i] * counts[i]
    return {name: v / total_samples * 128 for name, v in by_function.most_common()}

if __name__ == "__main__":
    main()
