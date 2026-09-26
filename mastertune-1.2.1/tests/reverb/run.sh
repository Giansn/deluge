#!/bin/sh
# Host test for the reverb (v10): the firmware's reverb code on the PC, with UndefinedBehaviorSanitizer.
# Usage: ./run.sh /path/to/DelugeFirmware (checked out at mastertune-v10) [calibrate]
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../arm/select.sh"
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
CXX="g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -w"
RUN=""
[ -n "$ARM" ] && CXX="$ARM_CXX" && RUN="$ARM_RUN"
$CXX -I "$HERE/stubs" -I "$HERE/../arm" -I "$D/deluge" -I "$D" -include host_shim.h -o "$B/reverb_test" \
    "$HERE/reverb_test.cpp" "$D/deluge/dsp/reverb/freeverb/freeverb.cpp"
$RUN "$B/reverb_test" $2
