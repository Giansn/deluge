#!/usr/bin/env python3
"""Renders each preset's synth (gen_presets.PRESETS) in the real firmware (emulated) on a held A major chord (A3 C#4 E4,
like the Pettra passage at 0:55), 4 bars at 120 BPM, to <out dir>/<name>.wav."""
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "../song"))  # the song harness: song_emu.py, make_sd.py, fat32.py
BUILD = os.environ.get("BLOCKCOUNT_DIR", HERE)  # where blockcount.so is (run.sh builds it)
import gen_presets  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402

A_MAJOR = [57, 61, 64]


def held_chord(octave, velocity=100):
    return [("y", n + 12 * octave, [(0, 4 * make_sd.BAR, velocity)], None) for n in A_MAJOR]


def main():
    elf, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    make_sd.chord_rows = held_chord
    for name, arp in gen_presets.PRESETS.items():
        sd = os.path.join(out_dir, "demo.img")
        gen_presets.build_sd(sd, name, arp)
        tools = os.path.join(os.path.dirname(os.path.abspath(elf)),
                             "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
        emu = song_emu.Emulator(elf, sd, tools, BUILD, lambda s: None)
        song_emu.setup_sd(emu)
        song_emu.boot(emu)
        emu.w32(emu.sym["jcong"], 1)
        song_emu.load_startup_song(emu)
        player = song_emu.Player(emu)
        player.start()
        record = []
        player.play(4 * 88200, record)
        x = np.concatenate([w[4] for w in record])
        peak = np.abs(x).max()
        x = x / max(peak, 1e-9) * 0.7  # normalised for listening
        path = os.path.join(out_dir, f"{name}.wav")
        with wave.open(path, "wb") as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(44100)
            f.writeframes((x * 32767).astype("<i2").tobytes())
        print(path, f"peak {20 * np.log10(max(peak, 1e-9)):.1f} dBFS")


if __name__ == "__main__":
    main()
