#!/usr/bin/env python3
"""Builds neon_shift_test.cpp for the Deluge's Cortex-A9 and runs it in an emulator (unicorn).

Usage: python3 run_neon_shift_test.py <firmware checkout>   (needs: pip install unicorn)
Checks that deluge::dsp::shiftInterpolationBuffer() leaves exactly the buffer the old element-by-element loop left.
"""
import os
import subprocess
import sys
import tempfile

from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_C1_C0_2, UC_ARM_REG_FPEXC, UC_ARM_REG_R0, UC_ARM_REG_SP, UC_CPU_ARM_CORTEX_A9

fw = os.path.abspath(sys.argv[1])
tc = os.path.join(fw, "toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neon_shift_test.cpp")
with tempfile.TemporaryDirectory() as tmp:
    elf, binary = os.path.join(tmp, "t.elf"), os.path.join(tmp, "t.bin")
    subprocess.run([tc + "g++", "-std=c++20", "-mcpu=cortex-a9", "-mfpu=neon", "-mfloat-abi=hard", "-mthumb", "-O2",
                    "-funsafe-math-optimizations", "-ffreestanding", "-nostdlib", "-fno-exceptions", "-fno-rtti",
                    "-I" + os.path.join(fw, "src/deluge"), "-Wl,-Ttext=0x10000", "-Wl,-e,_start", src, "-o", elf],
                   check=True)
    subprocess.run([tc + "objcopy", "-O", "binary", elf, binary], check=True)
    nm = subprocess.run([tc + "nm", elf], capture_output=True, text=True, check=True).stdout
    start = next(int(line.split()[0], 16) for line in nm.splitlines() if line.endswith(" _start"))
    code = open(binary, "rb").read()

mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
mu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)
mu.mem_map(0x10000, 0x100000)
mu.mem_write(0x10000, code)
mu.reg_write(UC_ARM_REG_SP, 0x100000)
mu.reg_write(UC_ARM_REG_C1_C0_2, mu.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))  # CP10/CP11 access
mu.reg_write(UC_ARM_REG_FPEXC, 0x40000000)  # FPEXC.EN
mu.emu_start(start | 1, start + 4, count=50_000_000)
mismatches = mu.reg_read(UC_ARM_REG_R0)
print(f"{mismatches} mismatches" if mismatches else "all checks passed (2000 rounds, shift 0-17)")
sys.exit(1 if mismatches else 0)
