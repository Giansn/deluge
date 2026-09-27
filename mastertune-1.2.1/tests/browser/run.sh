#!/bin/sh
# Versions of a song grouped in the song browser, on the real firmware in the emulator (song_groups_emu.py; its
# docstring says what runs and what's checked): files whose names begin with the same word ("TRACK", "TRACK 2",
# "TRACK 3") are one row, pressing it folds the group out, the select encoder goes through the versions, a version
# loads (stopped and while playing), BACK and leaving the group fold it in, songs on their own as before, a group
# larger than the browser's window of file items, the save browser unchanged, the 7-segment display.
#
# Usage: ./run.sh <firmware tree | deluge.elf> [out dir]
#   With a tree, it takes build/Release/deluge.elf and the tree's toolchain.
#   BASE=<tree | deluge.elf without the grouping> ./run.sh ... also runs that build (--baseline: reported, not
#   checked), for comparison: every file a row of its own.
# Needs: python3 with unicorn 2 and numpy, a C compiler (blockcount.c). About 1 minute per build.
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
OUT=${2:-$(mktemp -d)}
[ -f "$ELF" ] || { echo "no $ELF"; exit 2; }
mkdir -p "$OUT"

python3 "$HERE/song_groups_emu.py" "$ELF" --tools "$(tools_of "$1")" --out "$OUT"
if [ -n "$BASE" ]; then
	echo
	echo "== for comparison, $BASE (not checked)"
	python3 "$HERE/song_groups_emu.py" "$(elf_of "$BASE")" --tools "$(tools_of "$BASE")" --out "$OUT/base" --build "$OUT" \
		--baseline
fi
echo "results in $OUT"
