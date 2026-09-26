#!/usr/bin/env python3
"""Builds a test program for the Deluge's Cortex-A9, with the firmware's toolchain and its code generation flags.

Usage: arm_build.py <firmware checkout> -o <program.elf> [compiler arguments: sources, -I, -D, -include ...]

The flags are the firmware's (scripts/cmake/CMakeToolchainDeluge.cmake and CMakeLists.txt, release build): Thumb-2 for
the Cortex-A9 with NEON and hard float, -O2, -funsafe-math-optimizations (NEON for float, flushing denormals to zero
as on the Deluge), no inlining beyond what's marked inline, no exceptions or RTTI. So the DSP code under test becomes
the same machine code as in the firmware (without the firmware's link-time optimisation across files), and the
instruction counts of emu_count.h are what the Deluge runs. On top: newlib with semihosting (rdimon.specs), for printf,
the heap, files and the exit code. With ARM_UBSAN=1 in the environment, also UndefinedBehaviorSanitizer in trap mode
(arm_run.py reports where it caught something); the tests on the PC always have it.
"""
import os
import subprocess
import sys

FIRMWARE_FLAGS = [
    "-mcpu=cortex-a9", "-mfpu=neon", "-mfloat-abi=hard", "-mthumb", "-mthumb-interwork", "-mlittle-endian",
    "-funsafe-math-optimizations", "-O2", "-fno-inline-functions", "-ffunction-sections", "-fdata-sections",
    "-fsigned-char", "-D_GNU_SOURCE", "-DNDEBUG",
]
CXX_FLAGS = ["-std=gnu++23", "-fno-rtti", "-fno-exceptions", "-fno-use-cxa-atexit", "-fno-threadsafe-statics"]
C_FLAGS = ["-std=gnu2x"]
CHECK_FLAGS = ["-g", "-w", "-Wno-psabi"]
if os.environ.get("ARM_UBSAN"):
    CHECK_FLAGS += ["-fsanitize=undefined", "-fsanitize-undefined-trap-on-error"]
LINK_FLAGS = ["--specs=rdimon.specs", "-Wl,--gc-sections", "-lm"]


def main():
    if len(sys.argv) < 4 or "-o" not in sys.argv:
        raise SystemExit(__doc__)
    fw = os.path.abspath(sys.argv[1])
    args = sys.argv[2:]
    out = args[args.index("-o") + 1]
    del args[args.index("-o"):args.index("-o") + 2]
    tc = os.path.join(fw, "toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    sources = [a for a in args if a.endswith((".c", ".cpp", ".cc"))]
    options = [a for a in args if a not in sources]
    objects = []
    build = out + ".build"
    os.makedirs(build, exist_ok=True)
    for i, src in enumerate(sources):
        obj = os.path.join(build, f"{i}_{os.path.basename(src)}.o")
        c = src.endswith(".c")
        cmd = [tc + ("gcc" if c else "g++")] + FIRMWARE_FLAGS + (C_FLAGS if c else CXX_FLAGS) + CHECK_FLAGS + \
            options + ["-c", src, "-o", obj]
        subprocess.run(cmd, check=True)
        objects.append(obj)
    subprocess.run([tc + "g++"] + FIRMWARE_FLAGS + CHECK_FLAGS + objects + LINK_FLAGS + ["-o", out], check=True)


if __name__ == "__main__":
    main()
