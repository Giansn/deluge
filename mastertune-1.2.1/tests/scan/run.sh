#!/bin/sh
# The Scan view's analysis (mastertune-v18.4, dsp/scan/scan.cpp): the pitch and the tempo of the audio input, on the PC
# with UndefinedBehaviorSanitizer, or with ARM=1 on the Deluge's Cortex-A9 in the emulator (with the instructions per
# call). Usage: run.sh <firmware checkout with the patches>
set -e
FW=$(cd "$1" && pwd)
shift
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../arm/select.sh"
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
CXX="g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -w"
RUN=""
[ -n "$ARM" ] && CXX="$ARM_CXX" && RUN="$ARM_RUN"
$CXX -I "$HERE/../arm" -I "$D/deluge" -I "$D" -o "$B/scan_test" "$HERE/scan_test.cpp" "$D/deluge/dsp/scan/scan.cpp"
$RUN "$B/scan_test" "$HERE/../../device/card/SAMPLES" "$@"
