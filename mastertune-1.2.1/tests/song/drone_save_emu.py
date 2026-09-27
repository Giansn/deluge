#!/usr/bin/env python3
"""A drone track saved while it plays (mastertune-v16), on the real firmware in the emulator. The audio runs while the
song's XML is written (XMLSerializer::write() services it every 256 characters), and Kit::writeDataToFile() takes the
rows it has written out of the kit's list of drums until the kit is done; drone rows are rendered from that list
(Kit::renderGlobalEffectableForClip()), so they must not drop out meanwhile.

Usage: drone_save_emu.py <deluge.elf> <out dir> [--tools PREFIX] [--build DIR]   (run.sh's DRONE=1 runs it)

The song: one drone track (make_sd.py's drone kit, "DRONESAVE"), a 1-bar clip with 16 drone rows, 300 to 3300 Hz in
steps of 200 Hz, each a note as long as the clip (held across the loop point), level 25; the drone's own tone off.
After a 1-bar warm-up the firmware saves the song again and again for half a bar, with the output DMA in real time
(song_emu.save_while_playing(), as run.sh's SAVE=1 does). Checked from what the firmware wrote to the codec meanwhile
(save.wav): each row's level in 2048-sample windows (46 ms, 7-term Blackman-Harris) hopping by 512 stays within
0.2 dB of its median (a row left out while its kit is written drops out for as long as the rest of the kit takes,
and comes back out of phase), and no sample was written late (no underrun).
Results: <out>/save.wav, <out>/save_result.json.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drone_check  # noqa: E402
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402

SR = 44100
BAR = song_emu.BAR
ROWS_HZ = [300 + 200 * k for k in range(16)]
N = 2048


def song():
    make_sd.DRONE_TRACKS = [("DRONESAVE", 1, [(hz * 100, [(0, make_sd.BAR)], []) for hz in ROWS_HZ])]
    make_sd.DRONE_LEVEL = 25  # 16 rows together well below full scale
    xml = make_sd.song_xml({}, 1, drone_track=True)
    return xml.replace('<tone index="0" active="1"', '<tone index="0" active="0"', 1)  # The drone's reference off


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("out")
    ap.add_argument("--tools")
    ap.add_argument("--build", default=HERE)
    args = ap.parse_args()
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(args.out, exist_ok=True)
    sd = os.path.join(args.out, "sd.img")
    fat32.build(sd, {"SONGS/DEFAULT.XML": song().encode()})

    log = (lambda s: print(s, flush=True)) if os.environ.get("EMU_DEBUG") else (lambda s: None)
    emu = song_emu.Emulator(args.elf, sd, tools, args.build, log)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    emu.w32(emu.sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    print("== a drone track of 16 rows saved again and again while it plays (DMA in real time)", flush=True)
    player = song_emu.Player(emu)
    player.start()
    result = song_emu.save_while_playing(emu, player, 1, args.out, log, repeat_samples=int(0.5 * BAR))
    os.remove(sd)

    failures = 0

    def check(what, ok, detail=""):
        nonlocal failures
        failures += not ok
        print(f"  [{'ok' if ok else 'FAIL'}] {what}" + (f": {detail}" if detail else ""), flush=True)

    x = drone_check.read_wav(os.path.join(args.out, "save.wav"))[:, 0]
    starts = range(0, len(x) - N + 1, N // 4)
    levels = np.array([[drone_check.amplitude(x[i:i + N], hz) for hz in ROWS_HZ] for i in starts])
    silent = int(np.sum(levels.max(axis=0) < 1e-4))
    db = 20 * np.log10(levels / np.median(levels, axis=0) + 1e-12)
    worst = np.abs(db).max(axis=0)
    row = int(np.argmax(worst))
    check(f"{result['saves']} saves ({result['file']['bytes']:,} bytes of XML each, {result['duration_ms']:.0f} ms "
          f"emulated in all): each of the 16 rows steady through them, {len(starts)} windows",
          silent == 0 and worst.max() < 0.2,
          f"worst {worst.max():.2f} dB ({ROWS_HZ[row]} Hz row, min {db[:, row].min():+.2f} dB)"
          + (f", {silent} row(s) silent" if silent else ""))
    gaps = result["gaps"]
    check("no underrun while saving", gaps["underrun_samples"] == 0 and gaps["max"] < 128,
          f"worst gap {gaps['max']} samples, {gaps['underrun_samples']} written late")
    json.dump(dict(rows_hz=ROWS_HZ, worst_db=worst.tolist()), open(os.path.join(args.out, "rows.json"), "w"))
    print(f"a drone track saved while it plays: {'all checks passed' if not failures else f'{failures} failed'}",
          flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
