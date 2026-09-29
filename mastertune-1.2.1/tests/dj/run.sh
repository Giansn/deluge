#!/bin/sh
# The DJ filter's knob (mastertune-v19.0, dsp/dj/dj_filter.h), on the PC with UndefinedBehaviorSanitizer.
# Usage: run.sh <firmware checkout with the patches>
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -Wall -I "$FW/src/deluge" \
    -o "$B/dj_filter_test" "$HERE/dj_filter_test.cpp"
"$B/dj_filter_test"
