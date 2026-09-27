#!/bin/sh
# Tests of tools/retune_library.py (the sample library converted once to the master tune, so that it plays natively):
#   1. pc_test.py: a card with every kind of file and reference converted to 432 Hz: pitch, lengths, chunks, positions,
#      paths, what is left alone, readers (libsndfile, scipy, wave, sox, ffmpeg), dry run, second run, back to 440 Hz;
#      peaks over full scale (32-bit float, or --no-float a little quieter; never clipped); a dry run over 300 MB of
#      songs keeps only their references and positions (peak memory).
#      paths_memory_test.py: paths in CP437 as the Deluge writes them (umlauts) and in UTF-8; a dry run over 300 MB of
#      WAV holds no audio in memory (only the headers are read while planning).
#   2. retune_emu.py: the firmware in the emulator (unicorn) plays a song from the original card and from the
#      converted one at master tune 432 Hz (and the original at 440 Hz as the reference, and both without the sample
#      cache): native voices, time stretching, instructions per block and per voice, pitch and markers in the output.
#
# Usage: ./run.sh <firmware tree | deluge.elf> [work dir]   (a tree: its build/Release/deluge.elf and toolchain)
# Needs: python3 with numpy, scipy, soxr, soundfile, pylibrb, unicorn 2; a C compiler; sox and ffmpeg optional
#   (pip install numpy scipy soxr soundfile pylibrb unicorn imageio-ffmpeg). About 2 minutes.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -d "$1" ]; then
	ELF="$(cd "$1" && pwd)/build/Release/deluge.elf"
	TOOLS="$(cd "$1" && pwd)/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-"
else
	ELF=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
	TOOLS="$(cd "$(dirname "$ELF")/../.." && pwd)/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-"
fi
[ -f "$ELF" ] || { echo "no $ELF"; exit 2; }
WORK=${2:-$(mktemp -d)}
mkdir -p "$WORK"
cd "$HERE"
status=0
echo "== 1. PC: tools/retune_library.py on a test card"
python3 pc_test.py "$WORK/pc" > "$WORK/pc.log" 2>&1 || status=1
sed -n '/^Summary:/,$p' "$WORK/pc.log" | grep -v "^report:"
echo
echo "== 1b. PC: paths in CP437 (umlauts, as the Deluge writes them), memory of a dry run over 300 MB"
python3 paths_memory_test.py "$WORK/paths_memory" > "$WORK/paths_memory.log" 2>&1 || status=1
grep -E "^(FAIL|umlauts|memory|paths_memory_test)" "$WORK/paths_memory.log"
echo
echo "== 2. emulator: $ELF, the original and the converted card"
python3 retune_emu.py "$ELF" "$WORK/emu" --tools "$TOOLS" || status=1
[ $status = 0 ] && echo "all tests passed" || echo "FAILED"
echo
echo "results in $WORK"
exit $status
