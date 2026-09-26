#!/bin/sh
# CPU monitor (mastertune-v12-diag): host test of the firmware's collector, OLED line and SysEx encoder, then the
# page's decoder (tools/cpu_monitor.html) on the same messages.
# Usage: ./run.sh /path/to/DelugeFirmware   Needs: g++, gcc, node
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT
gcc -std=c11 -O2 -c -I "$FW/src" "$FW/src/lib/printf.c" -o "$OUT/printf.o"
g++ -std=c++20 -O2 -Wall -Wextra -Werror -I "$FW/src/deluge" -I "$FW/src" \
	"$HERE/cpu_stats_test.cpp" "$FW/src/deluge/processing/engines/cpu_stats_core.cpp" "$OUT/printf.o" -o "$OUT/test"
"$OUT/test" "$OUT/cases.json"
node "$HERE/decode_test.js" "$OUT/cases.json" "$HERE/../../tools/cpu_monitor.html"
