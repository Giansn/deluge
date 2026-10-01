#!/bin/bash
# The L2 configurations in the emulator (l2d: the release build since v18.2): boot sequence of the L2 controller, cache maintenance before the OLED's DMA,
# invalidate_range_all_caches() on unaligned ranges, the chainloader's L2 shutdown (see l2_emu.py).
# Usage: ./run.sh <deluge.elf> <v13 | v14 | v15 | v16 | v17 | l2i | l2d> [out dir]
#   TOOLS=<prefix arm-none-eabi-> if the ELF is not in a firmware tree's build/Release.
# Needs: python3 with unicorn 2 and numpy, a C compiler (for blockcount.c).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ELF=$(realpath "$1")
OUT=$(realpath -m "${3:-$HERE/out}")
mkdir -p "$OUT"
UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
cc -O2 -shared -fPIC -I"$UC/include" "$HERE/../song/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" \
	-l:libunicorn.so.2 -Wl,-rpath,"$UC/lib"
cd "$OUT"
BLOCKCOUNT_DIR="$OUT" python3 "$HERE/l2_emu.py" "$ELF" "$2"
