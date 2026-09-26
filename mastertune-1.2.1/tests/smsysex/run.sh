#!/bin/sh
# Host test for the SysEx file access (v7): builds storage/smsysex*.cpp from a firmware tree for x86 with
# AddressSanitizer, runs it on a FAT32 RAM disk and drives it the way DEx does (client_test.py).
# Usage: ./run.sh /path/to/DelugeFirmware (checked out at mastertune-v7)
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
mkdir -p "$B/fatfs"
cp "$FW"/src/fatfs/ff.c "$FW"/src/fatfs/ff.h "$FW"/src/fatfs/ffconf.h "$FW"/src/fatfs/ffunicode.c "$FW"/src/fatfs/diskio.h "$B/fatfs/"
sed -i 's/#define FF_USE_MKFS[[:space:]]*0/#define FF_USE_MKFS 1/' "$B/fatfs/ffconf.h" # to format the RAM disk
# d_string.cpp stores a counter in an intptr_t-sized slot on ARM (4 bytes); on x86-64 that would be 8
sed 's/intptr_t\*/int32_t*/g' "$FW/src/deluge/util/d_string.cpp" > "$B/d_string_host.cpp"
CXX="g++ -std=c++20 -O1 -g -fsanitize=address,undefined -w -I $HERE/stubs -I $B -I $FW/src/deluge -I $FW/src"
CC="gcc -O1 -g -fsanitize=address,undefined -w"
cd "$B"
$CXX -c "$HERE/harness.cpp" "$FW/src/deluge/storage/smsysex.cpp" "$FW/src/deluge/storage/smsysex_json.cpp" d_string_host.cpp
# ff.c reads unaligned words on purpose (fine on the Deluge's ARM), so no UBSan for FatFS itself
gcc -O1 -g -fsanitize=address -w -I fatfs -c fatfs/ff.c fatfs/ffunicode.c "$HERE/diskio_ram.c"
$CC -I "$FW/src/deluge" -c "$FW/src/deluge/util/pack.c"
g++ -fsanitize=address,undefined -o harness *.o
python3 "$HERE/client_test.py"
