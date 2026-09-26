#!/usr/bin/env python3
"""Records every call of the per-track effect functions in the song test (tests/song, demand run) by value, so that two
firmware builds can be compared call by call: ModControllableAudio::processReverbSendAndVolume (volume, pan, reverb
send), processFX (mod FX, EQ and the delay behind them) and processSRRAndBitcrushing. One line per call, in call order,
with what the call gets (its arguments, the object's state it uses, CRCs of the buffers it reads) and what it gives
(CRCs of the buffers it writes, the state after). Pointers are left out (they move with the code size), and so are
buffer contents a call neither reads nor writes (the reverb buffer without a send, the samples past numSamples).

Why not compare the song's measured.wav: it changes with the binary's layout alone (see oscillators/song_trace.py).

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
from song_emu import (UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,  # noqa: E402
                      UC_ARM_REG_SP, UC_HOOK_CODE)

# ModControllableAudio's members (offsets from `this`, the same in both builds: the class layout is unchanged)
PHASER_TO_EQ = (8, 88)  # phaserMemory, allpassMemory[6], bassFreq, trebleFreq, withoutTrebleL/bassOnlyL/R
SRR_ON_LAST_TIME = 488
MOD_FX_BUFFER = 508  # StereoSample*, kModFXBufferSize = 512 samples
MOD_FX_WRITE_INDEX = 512  # uint16_t
MOD_FX_LFO = 516  # LFO: phase, holdValue
GRAIN_WRAPS = 644  # wrapsToShutdown (then modFXGrainBuffer, a pointer, left out)
GRAIN_WRITE_INDEX = 652
SRR_STATE = (972, 1004)  # lowSampleRatePos, highSampleRatePos, lastSample, grabbedSample, lastGrabbedSample
POST_REVERB_VOLUME_LAST_TIME = 1080

FUNCTIONS = {  # prefix of the symbol (the base build has processSRRAndBitcrushing as an .isra.0 clone) -> kind
    "_ZN20ModControllableAudio26processReverbSendAndVolumeEP12StereoSamplelPlllllbS1_": "V",
    "_ZN20ModControllableAudio9processFXEP12StereoSamplel9ModFXTypellRKN5Delay5StateEPlP12ParamManager": "F",
    "_ZN20ModControllableAudio24processSRRAndBitcrushingEP12StereoSamplelPlP12ParamManager": "S",
}


def install(emu, trace):
    uc = emu.uc
    counts = {k: 0 for k in FUNCTIONS.values()}
    pending = {}
    returns = set()
    render_in_stereo = emu.sym["_ZN11AudioEngine14renderInStereoE"]
    last_reverb = emu.sym["_ZN11AudioEngine26timeThereWasLastSomeReverbE"]

    def read(address, n):
        return bytes(uc.mem_read(address, n))

    def u32(address):
        return struct.unpack("<I", read(address, 4))[0]

    def crc(address, n):
        return f"{zlib.crc32(read(address, n)):08x}"

    def stack(n):
        return struct.unpack(f"<{n}I", read(uc.reg_read(UC_ARM_REG_SP), 4 * n))

    def fx_state(this):
        lfo = struct.unpack("<Ii", read(this + MOD_FX_LFO, 8))
        buf = u32(this + MOD_FX_BUFFER)
        return [crc(this + PHASER_TO_EQ[0], PHASER_TO_EQ[1] - PHASER_TO_EQ[0]),
                struct.unpack("<H", read(this + MOD_FX_WRITE_INDEX, 2))[0], lfo[0], lfo[1],
                crc(buf, 512 * 8) if buf else "-", u32(this + GRAIN_WRAPS), u32(this + GRAIN_WRITE_INDEX)]

    def srr_state(this):
        return [read(this + SRR_ON_LAST_TIME, 1)[0], crc(this + SRR_STATE[0], SRR_STATE[1] - SRR_STATE[0])]

    def on_entry(kind):
        def hook(_uc, _address, _size, _):
            this, buf, n = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            if kind == "V":
                reverb = uc.reg_read(UC_ARM_REG_R3)
                post_fx, post_reverb, send, pan, ramp, add_to = stack(6)
                ramp &= 0xFF
                values = [n, post_fx, post_reverb, send, pan, ramp, int(add_to != 0), read(render_in_stereo, 1)[0],
                          u32(this + POST_REVERB_VOLUME_LAST_TIME), "in", crc(buf, 8 * n),
                          crc(add_to, 8 * n) if add_to else "-", crc(reverb, 4 * n) if send else "-"]
                keep = (buf, n, reverb, send, add_to)
            elif kind == "F":
                mod_fx_type = uc.reg_read(UC_ARM_REG_R3)
                rate, depth, _delay_state, post_fx_pointer, _param_manager = stack(5)
                values = [n, mod_fx_type, rate, depth, u32(post_fx_pointer), "in", crc(buf, 8 * n)] + fx_state(this)
                keep = (buf, n, post_fx_pointer)
            else:
                post_fx_pointer = uc.reg_read(UC_ARM_REG_R3)
                values = [n, u32(post_fx_pointer), "in", crc(buf, 8 * n)] + srr_state(this)
                keep = (buf, n, post_fx_pointer)
            values = [kind, counts[kind]] + values
            counts[kind] += 1
            pending[kind] = (values, uc.reg_read(UC_ARM_REG_SP), this, keep)
            back = uc.reg_read(UC_ARM_REG_LR) & ~1
            if back not in returns:
                returns.add(back)
                uc.hook_add(UC_HOOK_CODE, on_return, begin=back, end=back)
        return hook

    def on_return(_uc, address, _size, _):
        sp = uc.reg_read(UC_ARM_REG_SP)
        for kind, p in list(pending.items()):
            if p[1] != sp:
                continue
            del pending[kind]
            values, _, this, keep = p
            out = ["out"]
            if kind == "V":
                buf, n, reverb, send, add_to = keep
                out += [crc(add_to, 8 * n) if add_to else crc(buf, 8 * n), crc(reverb, 4 * n) if send else "-",
                        u32(this + POST_REVERB_VOLUME_LAST_TIME), u32(last_reverb)]
            elif kind == "F":
                buf, n, post_fx_pointer = keep
                out += [crc(buf, 8 * n), u32(post_fx_pointer)] + fx_state(this)
            else:
                buf, n, post_fx_pointer = keep
                out += [crc(buf, 8 * n), u32(post_fx_pointer)] + srr_state(this)
            trace.write(" ".join(str(v) for v in values + out) + "\n")
            return

    for prefix, kind in FUNCTIONS.items():
        uc.hook_add(UC_HOOK_CODE, on_entry(kind), begin=emu.sym.find(prefix) & ~1, end=emu.sym.find(prefix) & ~1)


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
