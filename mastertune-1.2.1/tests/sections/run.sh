#!/bin/bash
# Section launch commands learned to a CC or an MPE zone, after loading a song (upstream 95b7acab): see section_cc_test.py.
# Usage: ./run.sh <deluge.elf> [out dir]   (the ELF in a firmware tree's build/Release)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ELF=$(realpath "$1")
OUT=$(realpath -m "${2:-$HERE/out}")
mkdir -p "$OUT"
UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
cc -O2 -shared -fPIC -I"$UC/include" "$HERE/../song/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" \
	-l:libunicorn.so.2 -Wl,-rpath,"$UC/lib"
cd "$OUT"
BLOCKCOUNT_DIR="$OUT" python3 "$HERE/section_cc_test.py" "$ELF"
