#!/bin/sh
# Flicker-free pad dimming (pad-dim branch): the host unit test of hid/led/pad_dimming.h, then the real firmware in the
# emulator (tests/song's Emulator): what goes to the PIC at every pad brightness, both ways of the community feature
# "Flicker-free dimming" and, with a second ELF, against 1.2.1 (mastertune-v13). See pad_dim_emu.py's docstring.
#
# Usage: ./run.sh <pad-dim firmware tree> [1.2.1 deluge.elf to compare with] [work dir]
#   The tree's build/Release/deluge.elf (built with symbols) and its toolchain (nm, gdb). About 15 seconds.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
FW=$(cd "$1" && pwd)
WORK=${3:-$(mktemp -d)}
mkdir -p "$WORK"

echo "== host: hid/led/pad_dimming.h"
g++ -std=c++23 -O1 -Wall -Wextra -Werror -I"$FW/src/deluge" "$HERE/pad_dimming_test.cpp" -o "$WORK/pad_dimming_test"
"$WORK/pad_dimming_test"

echo
echo "== emulator: what the firmware sends to the PIC"
python3 "$HERE/pad_dim_emu.py" "$FW/build/Release/deluge.elf" ${2:+--base "$2"} \
	--tools "$FW/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-" --work "$WORK"
