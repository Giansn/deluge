#!/bin/sh
# Host test for the delay (v11): the firmware's delay code on the PC, with UndefinedBehaviorSanitizer.
# Usage: ./run.sh /path/to/DelugeFirmware
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -w -I "$HERE/stubs" -I "$D/deluge" -I "$D" -include host_shim.h \
    -o "$B/delay_test" "$HERE/delay_test.cpp" "$D/deluge/dsp/delay/delay.cpp" "$D/deluge/dsp/delay/delay_buffer.cpp" \
    "$D/deluge/util/lookuptables/lookuptables.cpp"
"$B/delay_test"
