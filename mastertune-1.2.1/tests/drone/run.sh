#!/bin/sh
# Host test for the drone (v12): the firmware's drone DSP on the PC, with UndefinedBehaviorSanitizer.
# Usage: ./run.sh /path/to/DelugeFirmware   (ARM=1 ./run.sh ...: on the Deluge's Cortex-A9 in the emulator)
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
$CXX -I "$HERE/../delay/stubs" -I "$HERE/../arm" -I "$D/deluge" -I "$D" -include host_shim.h \
    -o "$B/drone_test" "$HERE/drone_test.cpp" "$D/deluge/dsp/drone/drone.cpp"
$RUN "$B/drone_test"
