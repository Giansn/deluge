#!/bin/sh
# Sampling profiler (mastertune-v15-prof): host test of the firmware's ring and SysEx encoders, then the computer's
# decoders on the same messages: tools/deluge_profiler.py and (if node is there) tools/profiler.html.
# Usage: ./run.sh /path/to/DelugeFirmware   Needs: g++, python3 (node for the page)
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT
g++ -std=c++20 -O2 -Wall -Wextra -Werror -I "$FW/src/deluge" -I "$FW/src" \
	"$HERE/profiler_test.cpp" "$FW/src/deluge/processing/engines/profiler_core.cpp" -o "$OUT/test"
"$OUT/test" "$OUT/cases.json"
python3 "$HERE/decode_test.py" "$OUT/cases.json"
if command -v node >/dev/null && [ -f "$HERE/../../tools/profiler.html" ]; then
	node "$HERE/decode_test.js" "$OUT/cases.json" "$HERE/../../tools/profiler.html"
fi
