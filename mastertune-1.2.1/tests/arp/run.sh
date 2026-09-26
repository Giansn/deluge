#!/bin/bash
# Ratchet roll and bounce length in the real firmware (emulated), with the song harness of ../song.
# Usage: ./run.sh <deluge.elf> [out dir]
#   arp_roll_test.py: every note-on after the arp against the formula, 7 cases (roll accelerating/decelerating/even,
#     fixed counts over 1 and 2 steps, 16ths, unsynced); and the next arp note after each ratchet
#   edge_test.py: 11 edge cases with note-ons and note-offs (latch, sequence length, note probability, swing, release
#     and press again; TICKS=1 also logs the arp's clock ticks)
#   gen_presets.py: the presets in ../../presets/SYNTHS, saved by the firmware itself
#   render_demo.py: the presets on a held A major chord, 4 bars at 120 BPM (../../presets/demo)
# Needs: python3 with unicorn 2 and numpy, a C compiler (for blockcount.c).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ELF=$(realpath "$1")
OUT=$(realpath -m "${2:-$HERE/out}")
mkdir -p "$OUT"
UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
cc -O2 -shared -fPIC -I"$UC/include" "$HERE/../song/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" \
	-l:libunicorn.so.2 -Wl,-rpath,"$UC/lib"
cd "$OUT"
BLOCKCOUNT_DIR="$OUT" python3 "$HERE/arp_roll_test.py" "$ELF"
BLOCKCOUNT_DIR="$OUT" python3 "$HERE/edge_test.py" "$ELF"
BLOCKCOUNT_DIR="$OUT" python3 "$HERE/gen_presets.py" "$ELF" "$OUT/presets"
BLOCKCOUNT_DIR="$OUT" python3 "$HERE/render_demo.py" "$ELF" "$OUT/demo"
