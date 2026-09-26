#!/bin/sh
# Host test for the reverb (v10): the firmware's reverb code on the PC, with UndefinedBehaviorSanitizer.
# Usage: ./run.sh /path/to/DelugeFirmware (checked out at mastertune-v10) [calibrate]
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -w -I "$HERE/stubs" -I "$D/deluge" \
    -I "$D" -include host_shim.h -o "$B/reverb_test" "$HERE/reverb_test.cpp" "$D/deluge/dsp/reverb/freeverb/freeverb.cpp"
"$B/reverb_test" $2
