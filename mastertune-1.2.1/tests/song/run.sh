#!/bin/sh
# The whole firmware's audio load with a song where everything plays at once, on the Deluge's Cortex-A9 code in the
# emulator (unicorn): the real deluge.elf boots, loads the song from an SD card image and renders it through
# AudioEngine::routine(). Reports instructions per block of 128 samples (CPU % at 400 MHz, 1 instruction per cycle),
# their spread over the windows, voices, the output level and a profile by area and function.
#
# Usage: ./run.sh <firmware tree | deluge.elf> [out dir] [bars to measure, default 4]
#   With a tree, it takes build/Release/deluge.elf (built with symbols) and the tree's toolchain (nm, objdump).
#   Results: <out>/result.json (all numbers, per window too), <out>/measured.wav (what the codec gets), and the same
#   for the Digital reverb model in <out>/digital/.
# Needs: python3 with unicorn 2 and numpy, a C compiler (for blockcount.c). About a minute.
#
# Files: make_sd.py (the song and its samples, generated; its docstring describes the song), fat32.py (the card
# image), song_emu.py (the emulator harness; its docstring says what is real and what is modelled), blockcount.c
# (instruction counting per translated block).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -d "$1" ]; then
	FW=$(cd "$1" && pwd)
	ELF="$FW/build/Release/deluge.elf"
else
	ELF=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
	FW=$(cd "$(dirname "$ELF")/../.." && pwd)
fi
OUT=${2:-$(mktemp -d)}
BARS=${3:-4}
TOOLS="$FW/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-"
[ -f "$ELF" ] || { echo "no $ELF"; exit 2; }
mkdir -p "$OUT/digital"

UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
cc -O2 -shared -fPIC -I"$UC/include" "$HERE/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" -l:libunicorn.so.2 \
	-Wl,-rpath,"$UC/lib"

echo "== the song, reverb: Mutable (the default model)"
python3 "$HERE/make_sd.py" "$OUT/sd.img" --xml-out "$OUT/song.xml"
python3 "$HERE/song_emu.py" "$ELF" "$OUT/sd.img" "$OUT" --tools "$TOOLS" --build "$OUT" --bars "$BARS"
rm -f "$OUT/sd.img"

echo
echo "== the same song, reverb: Digital"
python3 "$HERE/make_sd.py" "$OUT/digital/sd.img" --reverb-model 2 > /dev/null
python3 "$HERE/song_emu.py" "$ELF" "$OUT/digital/sd.img" "$OUT/digital" --tools "$TOOLS" --build "$OUT" --bars 2 |
	grep -E "^per 128|^  .*reverb"
rm -f "$OUT/digital/sd.img"
echo
echo "results in $OUT"
