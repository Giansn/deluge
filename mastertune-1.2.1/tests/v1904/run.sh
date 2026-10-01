#!/bin/sh
# v19.0.4's fixes (patch 0123) on the real firmware in the emulator. Each test passes on v19.0.4 and fails on a build
# before it (e.g. v19.0.1-v19.0.3):
#   export_abort_emu.py  (tests/export) stem export with the recorder's RAM failing: M123 before, all stems after
#   synth_kit_emu.py     a synth loaded into a kit row with the browser open: the view on the freed drum before
#   save_check_emu.py    a saved song: six duplicate attributes in <audioClip> before; after, none, and it loads back
# Usage: ./run.sh <deluge.elf> <toolchain prefix> <blockcount dir> [out dir]
# About 10 minutes. Exit status: the number of failed tests.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ELF=$1 TOOLS=$2 BUILD=$3 OUT=${4:-/tmp/v1904}
mkdir -p "$OUT"
cd /tmp || exit 1
fails=0
python3 "$HERE/../export/export_abort_emu.py" "$ELF" --tools "$TOOLS" --build "$BUILD" --out "$OUT/export" \
  > "$OUT/export.log" 2>&1 || fails=$((fails + 1))
python3 "$HERE/synth_kit_emu.py" "$ELF" --tools "$TOOLS" --build "$BUILD" --out "$OUT/synth_kit" \
  > "$OUT/synth_kit.log" 2>&1 || fails=$((fails + 1))
python3 "$HERE/save_check_emu.py" "$ELF" --tools "$TOOLS" --build "$BUILD" --out "$OUT/save_check" \
  > "$OUT/save_check.log" 2>&1 || fails=$((fails + 1))
for t in export synth_kit save_check; do echo "== $t: $(tail -1 "$OUT/$t.log")"; done
exit $fails
