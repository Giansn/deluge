#!/bin/sh
# The bass / treble EQ (mastertune v18, dsp/eq_shelves.h: TPT one-pole shelves) against v17's and a float64 model.
# See eq_test.cpp for the cases and what fails.
# On the Deluge's Cortex-A9 in the emulator (the firmware's toolchain and flags), with the instruction counts.
# Usage: ./run.sh /path/to/DelugeFirmware   (TEST=<case> for one: neutral response corner precision clicks cpu restart)
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(cd "$HERE/.." && pwd)
H=$HERE; HERE=$T/delay; . "$T/arm/select.sh"; HERE=$H
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
[ -f "$D/deluge/dsp/eq_shelves.h" ] || { echo "no dsp/eq_shelves.h in $FW (not a v18 tree)"; exit 2; }
# (ARM only: dsp/gain_ramp.h is NEON code)
DEFS=""
grep -q restartAfterSilence "$D/deluge/dsp/eq_shelves.h" && DEFS="-DEQ_RESTART_AFTER_SILENCE"
$ARM_CXX $DEFS -I "$T/arm" -I "$D/deluge" -I "$D" -include host_shim.h -I "$T/delay/stubs" -include definitions_cxx.hpp \
    -o "$B/eq_test" "$HERE/eq_test.cpp"
status=0
for t in ${TEST:-neutral response corner precision clicks cpu restart}; do
	echo "== $t"
	$ARM_RUN "$B/eq_test" "$t" || status=1
done
exit $status
