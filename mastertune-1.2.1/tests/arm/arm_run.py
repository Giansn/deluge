#!/usr/bin/env python3
"""Runs a program built for the Deluge's Cortex-A9 in an emulator (unicorn), like a program on the PC.

Usage: arm_run.py <program.elf> [arguments...]

The program is built with the firmware's toolchain and flags (see arm_build.py) and newlib's semihosting
(rdimon.specs), so it has printf, a heap, stdin and stdout, files and an exit code. This runs it:
- Semihosting calls (svc 0xab in Thumb, svc 0x123456 in ARM) are handled here: the program's output goes to stdout,
  its input comes from stdin, and its exit code is ours.
- An undefined instruction (what -fsanitize-undefined-trap-on-error puts where UndefinedBehaviorSanitizer found
  something) stops it with the place in the source.
- Between EMU_COUNT_BEGIN() and EMU_COUNT_END() (emu_count.h), every instruction executed is counted, so a test can
  report what its DSP costs on the real instruction set.
"""
import os
import struct
import subprocess
import sys
import time

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_INTR, UC_MODE_ARM, Uc, UcError
from unicorn.arm_const import (UC_ARM_REG_C1_C0_2, UC_ARM_REG_CPSR, UC_ARM_REG_FPEXC, UC_ARM_REG_PC, UC_ARM_REG_R0,
                               UC_ARM_REG_R1, UC_ARM_REG_SP, UC_CPU_ARM_CORTEX_A9)

RAM_SIZE = 0x20000000  # 512 MB from address 0, mapped lazily by the host
STACK_TOP = RAM_SIZE
STACK_SIZE = 0x01000000

# Exception numbers unicorn passes to the interrupt hook (QEMU's EXCP_*)
EXCP_UDEF, EXCP_SWI, EXCP_PREFETCH_ABORT, EXCP_DATA_ABORT, EXCP_BKPT = 1, 2, 3, 4, 7

# Our own calls, next to semihosting
SVC_SEMIHOSTING_THUMB, SVC_SEMIHOSTING_ARM = 0xAB, 0x123456
SVC_COUNT_BEGIN, SVC_COUNT_END = 0x41, 0x42


def toolchain_prefix():
    fw = os.environ.get("DELUGE_FIRMWARE")
    if not fw:
        return None
    return os.path.join(fw, "toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")


class Program:
    def __init__(self, path, argv):
        self.path = path
        self.argv = argv
        self.exit_code = None
        self.files = {}  # Semihosting handle -> file object
        self.next_handle = 3
        self.counts = {}  # Label -> [instructions, calls]
        self.counting = None  # [label, count, hook]
        self.start_time = time.time()
        data = open(path, "rb").read()
        if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
            raise SystemExit(f"{path}: not a 32-bit little-endian ELF file")
        self.entry, phoff, _, _, _, phentsize, phnum = struct.unpack_from("<IIIIHHH", data, 24)
        self.segments = []
        for i in range(phnum):
            p_type, p_offset, p_vaddr, _, p_filesz, p_memsz = struct.unpack_from("<IIIIII", data, phoff + i * phentsize)
            if p_type == 1:  # PT_LOAD
                self.segments.append((p_vaddr, data[p_offset:p_offset + p_filesz], p_memsz))
        self.heap_base = max(v + m for v, _, m in self.segments)
        self.heap_base = (self.heap_base + 0xFFF) & ~0xFFF
        self.text_range = (min(v for v, _, _ in self.segments), self.heap_base)

    # --- memory helpers

    def word(self, address):
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def words(self, address, n):
        return struct.unpack(f"<{n}I", self.uc.mem_read(address, 4 * n))

    def cstring(self, address, limit=4096):
        out = bytearray()
        while len(out) < limit:
            chunk = self.uc.mem_read(address + len(out), 64)
            end = chunk.find(b"\0")
            if end >= 0:
                out += chunk[:end]
                break
            out += chunk
        return bytes(out)

    # --- semihosting

    def semihosting(self, op, param):
        uc = self.uc
        if op == 0x01:  # SYS_OPEN
            name_ptr, mode, length = self.words(param, 3)
            name = bytes(uc.mem_read(name_ptr, length)).decode("latin-1")
            if name == ":tt":
                return 0 if mode < 4 else (1 if mode < 8 else 2)
            if name == ":semihosting-features":
                # Magic and feature byte: SYS_EXIT_EXTENDED, so the exit code gets through, and separate stdout and
                # stderr
                handle = self.next_handle
                self.next_handle += 1
                self.files[handle] = FeatureFile(b"SHFB\x03")
                return handle
            modes = ["r", "rb", "r+", "r+b", "w", "wb", "w+", "w+b", "a", "ab", "a+", "a+b"]
            try:
                f = open(name, modes[mode] if "b" in modes[mode] else modes[mode] + "b")
            except OSError:
                return 0xFFFFFFFF
            handle = self.next_handle
            self.next_handle += 1
            self.files[handle] = f
            return handle
        if op == 0x02:  # SYS_CLOSE
            (handle,) = self.words(param, 1)
            f = self.files.pop(handle, None)
            if f:
                f.close()
            return 0
        if op == 0x03:  # SYS_WRITEC
            sys.stdout.buffer.write(bytes(uc.mem_read(param, 1)))
            return 0
        if op == 0x04:  # SYS_WRITE0
            sys.stdout.buffer.write(self.cstring(param, 1 << 20))
            return 0
        if op == 0x05:  # SYS_WRITE: returns the number of bytes not written
            handle, buf, length = self.words(param, 3)
            data = bytes(uc.mem_read(buf, length))
            if handle in (0, 1, 2):
                out = sys.stderr if handle == 2 else sys.stdout
                try:
                    out.buffer.write(data)
                    out.flush()
                except BrokenPipeError:  # Whoever reads our output has stopped (e.g. head): stop too, quietly
                    self.exit_code = 141
                    uc.emu_stop()
                    sys.stdout = open(os.devnull, "w")
            elif handle in self.files:
                self.files[handle].write(data)
            else:
                return length
            return 0
        if op == 0x06:  # SYS_READ: returns the number of bytes not read
            handle, buf, length = self.words(param, 3)
            if handle == 0:
                sys.stdout.flush()
                data = sys.stdin.buffer.readline(length) if length else b""
            elif handle in self.files:
                data = self.files[handle].read(length)
            else:
                return length
            uc.mem_write(buf, data)
            return length - len(data)
        if op == 0x07:  # SYS_READC
            c = sys.stdin.buffer.read(1)
            return c[0] if c else 0xFFFFFFFF
        if op == 0x08:  # SYS_ISERROR
            (status,) = self.words(param, 1)
            return 1 if status >= 0x80000000 else 0
        if op == 0x09:  # SYS_ISTTY
            (handle,) = self.words(param, 1)
            return 1 if handle in (0, 1, 2) else 0
        if op == 0x0A:  # SYS_SEEK
            handle, position = self.words(param, 2)
            if handle in self.files:
                self.files[handle].seek(position)
                return 0
            return 0xFFFFFFFF
        if op == 0x0C:  # SYS_FLEN
            (handle,) = self.words(param, 1)
            f = self.files.get(handle)
            if not f or isinstance(f, FeatureFile):
                return len(f.data) if f else 0xFFFFFFFF
            here = f.tell()
            f.seek(0, 2)
            n = f.tell()
            f.seek(here)
            return n
        if op == 0x0E:  # SYS_REMOVE
            name_ptr, length = self.words(param, 2)
            try:
                os.remove(bytes(uc.mem_read(name_ptr, length)).decode("latin-1"))
                return 0
            except OSError:
                return 0xFFFFFFFF
        if op == 0x10:  # SYS_CLOCK: centiseconds
            return int((time.time() - self.start_time) * 100) & 0xFFFFFFFF
        if op == 0x11:  # SYS_TIME
            return int(time.time()) & 0xFFFFFFFF
        if op == 0x13:  # SYS_ERRNO
            return 0
        if op == 0x15:  # SYS_GET_CMDLINE
            buf, length = self.words(param, 2)
            cmdline = " ".join([os.path.basename(self.path)] + self.argv).encode()[:length - 1] + b"\0"
            uc.mem_write(buf, cmdline)
            uc.mem_write(param + 4, struct.pack("<I", len(cmdline) - 1))
            return 0
        if op == 0x16:  # SYS_HEAPINFO
            (block,) = self.words(param, 1)
            uc.mem_write(block, struct.pack("<IIII", self.heap_base, STACK_TOP - STACK_SIZE, STACK_TOP,
                                            STACK_TOP - STACK_SIZE))
            return 0
        if op == 0x18:  # SYS_EXIT: the reason is in r1 itself
            self.exit_code = 0 if param == 0x20026 else 1
            uc.emu_stop()
            return 0
        if op == 0x20:  # SYS_EXIT_EXTENDED
            reason, code = self.words(param, 2)
            self.exit_code = code if reason == 0x20026 else 1
            uc.emu_stop()
            return 0
        if op == 0x30:  # SYS_ELAPSED
            ticks = int((time.time() - self.start_time) * 1e6)
            uc.mem_write(param, struct.pack("<Q", ticks))
            return 0
        if op == 0x31:  # SYS_TICKFREQ
            return 1000000
        sys.stderr.write(f"arm_run: unknown semihosting call {op:#x}\n")
        return 0xFFFFFFFF

    # --- hooks

    def where(self, pc):
        prefix = toolchain_prefix()
        if prefix and os.path.exists(prefix + "addr2line"):
            out = subprocess.run([prefix + "addr2line", "-f", "-C", "-i", "-e", self.path, hex(pc)],
                                 capture_output=True, text=True).stdout.strip().splitlines()
            return " / ".join(out)
        return hex(pc)

    def on_interrupt(self, uc, intno, _):
        pc = uc.reg_read(UC_ARM_REG_PC)
        thumb = uc.reg_read(UC_ARM_REG_CPSR) & 0x20
        if intno == EXCP_SWI:
            if thumb:
                number = struct.unpack("<H", uc.mem_read(pc - 2, 2))[0] & 0xFF
            else:
                number = struct.unpack("<I", uc.mem_read(pc - 4, 4))[0] & 0xFFFFFF
            if number in (SVC_SEMIHOSTING_THUMB, SVC_SEMIHOSTING_ARM):
                result = self.semihosting(uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1))
                uc.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
                return
            if number == SVC_COUNT_BEGIN:
                label = self.cstring(uc.reg_read(UC_ARM_REG_R0), 200).decode("latin-1")
                state = [label, 0, None]

                def count(_uc, _address, _size, s):
                    s[1] += 1

                state[2] = uc.hook_add(UC_HOOK_CODE, count, user_data=state, begin=self.text_range[0],
                                       end=self.text_range[1])
                uc.ctl_flush_tb()  # Code translated before has no hook in it
                self.counting = state
                return
            if number == SVC_COUNT_END:
                if self.counting:
                    label, n, hook = self.counting
                    uc.hook_del(hook)
                    uc.ctl_flush_tb()  # Back to full speed
                    entry = self.counts.setdefault(label, [0, 0])
                    entry[0] += n - 1  # Without this svc
                    entry[1] += 1
                    self.counting = None
                return
            self.fail(f"unknown svc #{number:#x}", pc)
            return
        if intno == EXCP_UDEF:
            self.fail("undefined instruction (UndefinedBehaviorSanitizer trap or a bad jump)", pc - (2 if thumb else 4))
            return
        if intno in (EXCP_PREFETCH_ABORT, EXCP_DATA_ABORT):
            self.fail("memory access fault", pc)
            return
        if intno == EXCP_BKPT:
            self.fail("breakpoint", pc)
            return
        self.fail(f"exception {intno}", pc)

    def fail(self, what, pc):
        sys.stdout.flush()
        sys.stderr.write(f"arm_run: {what} at {self.where(pc)}\n")
        self.exit_code = 134
        self.uc.emu_stop()

    # --- running

    def run(self):
        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)
        uc.mem_map(0, RAM_SIZE)
        for address, content, _ in self.segments:
            uc.mem_write(address, content)  # The rest of each segment (.bss) is already zero
        uc.reg_write(UC_ARM_REG_SP, STACK_TOP)
        uc.reg_write(UC_ARM_REG_C1_C0_2, uc.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))  # CP10/CP11 access (VFP/NEON)
        uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)  # FPEXC.EN
        uc.hook_add(UC_HOOK_INTR, self.on_interrupt)
        try:
            uc.emu_start(self.entry, 0xFFFFFFFE)
        except UcError as e:
            self.fail(f"emulator error: {e}", uc.reg_read(UC_ARM_REG_PC))
        sys.stdout.flush()
        for label, (n, calls) in self.counts.items():
            per = n / calls
            # 128 samples at 44.1 kHz on the 400 MHz Cortex-A9: 1.16 million cycles
            print(f"[arm] {label}: {per:,.0f} instructions per call ({calls} calls), "
                  f"about {per / 1161000 * 100:.2f}% of the CPU per block of 128 at 1 instruction per cycle")
        if self.exit_code is None:
            self.exit_code = 1
        return self.exit_code


class FeatureFile:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, n):
        out = self.data[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def write(self, _):
        pass

    def seek(self, pos, whence=0):
        self.pos = pos

    def tell(self):
        return self.pos

    def close(self):
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(Program(sys.argv[1], sys.argv[2:]).run())
