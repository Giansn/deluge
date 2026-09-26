#!/bin/sh
# The whole firmware's audio load with a song where everything plays at once, on the Deluge's Cortex-A9 code in the
# emulator (unicorn): the real deluge.elf boots, loads the song from an SD card image and renders it through
# AudioEngine::routine(). Reports instructions per block of 128 samples (CPU % at 400 MHz, 1 instruction per cycle),
# their spread over the windows, the windows shorter than 128 samples and their cost, voices (in all and per Sound),
# cpuDireness, culls, the output level and a profile by area and function.
#
# Three runs over the same bars:
#   1. demand: no culling for CPU load, cpuDireness 0 (what the song asks for; the task stats read 0)
#   2. device: as on the Deluge, routine() culls voices and sets cpuDireness by its own emulated duration (the task
#      manager's running average of the previous routine() calls, see song_emu.py)
#   3. the Digital reverb instead of Mutable, as 1., compared by total and by the reverb area
#
# Usage: ./run.sh <firmware tree | deluge.elf> [out dir] [bars to measure, default 4]
#   With a tree, it takes build/Release/deluge.elf (built with symbols) and the tree's toolchain (nm, objdump).
#   Results: <out>/result.json (all numbers, per window and per Sound too), <out>/measured.wav (what the codec gets),
#   and the same in <out>/device/ and <out>/digital/.
# Needs: python3 with unicorn 2 and numpy, a C compiler (for blockcount.c). About 2 minutes.
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
mkdir -p "$OUT/device" "$OUT/digital"

UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
cc -O2 -shared -fPIC -I"$UC/include" "$HERE/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" -l:libunicorn.so.2 \
	-Wl,-rpath,"$UC/lib"

run() { # out dir, song options, emulator options
	python3 "$HERE/make_sd.py" "$1/sd.img" $2 > /dev/null
	python3 "$HERE/song_emu.py" "$ELF" "$1/sd.img" "$1" --tools "$TOOLS" --build "$OUT" --bars "$BARS" $3
	rm -f "$1/sd.img"
}

echo "== 1. demand: the song (reverb: Mutable, the default model), no culling for CPU load, cpuDireness 0"
run "$OUT" "--xml-out $OUT/song.xml" ""

echo
echo "== 2. device: the same, with culling and cpuDireness by the emulated duration, as on the Deluge"
run "$OUT/device" "" "--culling" | grep -Ev "^  \.\.\.|^boot|^song loaded"

echo
echo "== 3. the same song with the Digital reverb, no culling ($BARS bars, as 1.)"
run "$OUT/digital" "--reverb-model 2" "" | grep -E "^per 128|^full windows \(128\)"
python3 - "$OUT" <<'EOF'
import json, sys
m, d = (json.load(open(f"{sys.argv[1]}/{x}result.json")) for x in ("", "digital/"))
assert m["samples"] == d["samples"], "not the same bars"
rv = lambda r: r["areas"]["reverb"]["per_128"]
print(f"same {m['samples']} samples: total per 128 Mutable {m['instructions_per_128']:,.0f}, Digital "
      f"{d['instructions_per_128']:,.0f} ({d['instructions_per_128'] - m['instructions_per_128']:+,.0f}); reverb area "
      f"Mutable {rv(m):,.0f} ({rv(m) / 1161000 * 100:.1f}% CPU), Digital {rv(d):,.0f} ({rv(d) / 1161000 * 100:.1f}% CPU), "
      f"{rv(d) - rv(m):+,.0f}")
EOF
echo
echo "results in $OUT"
