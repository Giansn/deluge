#!/bin/sh
# Every Voice::renderOsc() call of the song test's demand run (tests/song), by value, into a trace file: compare two
# builds with cmp (see song_trace.py for what is recorded and why not measured.wav).
# Usage: ./song_trace.sh <firmware tree | deluge.elf> <trace file> [bars to measure, default 4]   (about 3 minutes)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SONG=$(cd "$HERE/../../song" && pwd)
if [ -d "$1" ]; then
	FW=$(cd "$1" && pwd)
	ELF="$FW/build/Release/deluge.elf"
else
	ELF=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
	FW=$(cd "$(dirname "$ELF")/../.." && pwd)
fi
TRACE=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
BARS=${3:-4}
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
cc -O2 -shared -fPIC -I"$UC/include" "$SONG/blockcount.c" -o "$B/blockcount.so" -L"$UC/lib" -l:libunicorn.so.2 \
	-Wl,-rpath,"$UC/lib"
python3 "$SONG/make_sd.py" "$B/sd.img" > /dev/null
python3 "$HERE/song_trace.py" "$ELF" "$B/sd.img" "$B" --tools "$FW/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-" \
	--build "$B" --bars "$BARS" --trace "$TRACE" | grep -E "^per 128|^measured"
echo "$(wc -l < "$TRACE") renderOsc calls in $TRACE"
