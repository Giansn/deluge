#!/bin/sh
# Song change on the real firmware in the emulator (songchange_emu.py; its docstring says what runs and what's
# checked): the countdown while a loaded song is armed to start (loops, then bars, then beats), on the OLED and the
# 7-segment display, with swing, following an external MIDI clock, and stopped while armed; the gold knobs and mod
# buttons on the playing song's master FX until the song changes, and nothing of the old song left at the swap.
# Since v19.0.2 also: a countdown has priority (popups below it on the OLED, none on the 7-segment display), and
# scenario launch: Song view's launch countdown on the OLED's title line. Since v19.0.3 scenarios countin and countin7:
# the record count-in as a countdown (REC, then PLAY with a 1-bar count-in, a gold knob turned during it).
#
# Usage: ./run.sh <firmware tree | deluge.elf> [out dir] [scenario ...]
#   With a tree, it takes build/Release/deluge.elf and the tree's toolchain. Scenarios: oled 7seg swing extclock stop
#   launch countin countin7 (default: all). Results: <out>/<scenario>/result.json (checks, per window while armed the swung tick, repeats,
#   launch event and what's shown, every display event), oled_*.png/.txt (the OLED at checkpoints).
#   BASE=<1.2.1 or mastertune-v13 tree | deluge.elf> ./run.sh ... also runs that build (--baseline: reported, not
#   checked), for comparison: its popups instead of the countdown, its knobs on the selected clip.
# Needs: python3 with unicorn 2 and numpy, a C compiler (blockcount.c). About 3 minutes per scenario (countin: 1).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
elf_of() {
	if [ -d "$1" ]; then echo "$(cd "$1" && pwd)/build/Release/deluge.elf"; else echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; fi
}
tools_of() {
	if [ -d "$1" ]; then echo "$(cd "$1" && pwd)/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-"
	else echo "$(cd "$(dirname "$1")/../.." && pwd)/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-"; fi
}
ELF=$(elf_of "$1")
TOOLS=$(tools_of "$1")
OUT=${2:-$(mktemp -d)}
shift $(( $# < 2 ? $# : 2 ))
[ -f "$ELF" ] || { echo "no $ELF"; exit 2; }
mkdir -p "$OUT"
SCEN=""
for s in "$@"; do SCEN="$SCEN --scenario $s"; done

python3 "$HERE/songchange_emu.py" "$ELF" --tools "$TOOLS" --out "$OUT" $SCEN
if [ -n "$BASE" ]; then
	echo
	echo "== for comparison, $BASE (not checked)"
	python3 "$HERE/songchange_emu.py" "$(elf_of "$BASE")" --tools "$(tools_of "$BASE")" --out "$OUT/base" --build "$OUT" \
		--baseline --scenario oled --scenario 7seg
fi
echo "results in $OUT"
