#!/bin/bash
# Gate and MIDI clock outputs under an external MIDI clock (upstream 7d9beeef), in the emulator: see extclock_out.py.
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
# The outputs following an external MIDI clock (checked), then on the internal clock (for comparing builds)
python3 "$HERE/extclock_out.py" "$ELF" --build "$OUT" --out "$OUT/external"
python3 "$HERE/extclock_out.py" "$ELF" --build "$OUT" --out "$OUT/internal" --internal
