#!/usr/bin/env python3
"""Records every Voice::renderOsc() call of the song test (tests/song, demand run) by value, so that two firmware
builds can be compared oscillator call by oscillator call: what each call gets (its arguments, the phase, cpuDireness
and the buffer's samples before it) and what it gives (the buffer's samples and the phase after it). Pointers are left
out (the heap moves with the code size), and so are the osc sync arguments where renderOsc doesn't use them (they are
uninitialised then), and the samples past numSamples (the vector loops write some there, over what an earlier window
left, which is not comparable).

Why not compare the song's measured.wav: it changes with the binary's layout alone. The v12 tree unchanged but with a
longer RELEASE_TYPE, or with never executed padding in renderOsc, gives a different measured.wav, from the first
window on (see the perf notes). The renderOsc calls stay identical there.

Usage: song_trace.sh <firmware tree | deluge.elf> <trace file> [bars] (it runs this with song_emu.py's arguments plus
--trace <file>). Then: cmp trace_a trace_b.
"""
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../../song"))
import song_emu  # noqa: E402
from song_emu import UC_ARM_REG_LR, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, UC_HOOK_CODE  # noqa

RENDER_OSC = "_ZN5Voice9renderOscEl7OscTypelPlS1_lmmPmblbmmml"


def install(emu, trace):
    uc = emu.uc
    state = {"pending": None, "returns": set(), "n": 0}
    direness = emu.sym["_ZN11AudioEngine11cpuDirenessE"]

    def read(address, n):
        return bytes(uc.mem_read(address, n))

    def u32(address):
        return struct.unpack("<I", read(address, 4))[0]

    def on_entry(_uc, _address, _size, _):
        sp = uc.reg_read(UC_ARM_REG_SP)
        # this, s, type, amplitude in r0-r3; bufferStart, bufferEnd, numSamples, phaseIncrement, pulseWidth,
        # startPhase, applyAmplitude, amplitudeIncrement, doOscSync, resetterPhase, resetterPhaseIncrement,
        # retriggerPhase, waveIndexIncrement on the stack
        a = struct.unpack("<13I", read(sp, 52))
        buffer, samples, phase_pointer = a[0], a[2], a[5]
        sync_used = (a[8] & 0xFF) or a[4]
        values = [state["n"], uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2) & 0xFF,
                  uc.reg_read(UC_ARM_REG_R3), samples, a[3], a[4], u32(phase_pointer), a[6] & 0xFF, a[7], a[8] & 0xFF,
                  a[9] if sync_used else "-", a[10] if sync_used else "-", a[11], a[12],
                  struct.unpack("<i", read(direness, 4))[0], f"in={zlib.crc32(read(buffer, 4 * samples)):08x}"]
        state["n"] += 1
        state["pending"] = (values, sp, buffer, samples, phase_pointer)
        back = uc.reg_read(UC_ARM_REG_LR) & ~1
        if back not in state["returns"]:
            state["returns"].add(back)
            uc.hook_add(UC_HOOK_CODE, on_return, begin=back, end=back)

    def on_return(_uc, _address, _size, _):
        p = state["pending"]
        if p is None or uc.reg_read(UC_ARM_REG_SP) != p[1]:
            return
        state["pending"] = None
        values, _, buffer, samples, phase_pointer = p
        trace.write(" ".join(str(v) for v in values)
                    + f" out={zlib.crc32(read(buffer, 4 * samples)):08x} phase={u32(phase_pointer)}\n")

    uc.hook_add(UC_HOOK_CODE, on_entry, begin=emu.sym[RENDER_OSC] & ~1, end=emu.sym[RENDER_OSC] & ~1)


def main():
    i = sys.argv.index("--trace")
    trace = open(sys.argv[i + 1], "w")
    del sys.argv[i:i + 2]
    boot = song_emu.boot

    def boot_and_trace(emu):
        boot(emu)
        install(emu, trace)

    song_emu.boot = boot_and_trace
    song_emu.main()
    trace.close()


if __name__ == "__main__":
    main()
