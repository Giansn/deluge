#!/bin/sh
# The tests of the features ported from mastertune 1.2.1 (beta-1.3/features), each on the real firmware in the
# emulator with the v1.3 rig. Each fails on the build without the features (the fixes alone) and passes with them.
#   arp_extras_emu.py   0001: latch, ratchet notes, bounce, bounce length and velocity, roll and ball, latched
#                       notes dropped when the arp goes off (12 cases)
#   cpu_monitor_emu.py  0002: the CPU monitor: LEARN + tempo encoder, the OLED line, the SysEx, kept across a restart
#   (and mastertune's host test of the CPU monitor's core, mastertune-1.2.1/tests/cpu_stats, on the ELF's tree)
# Usage: beta-1.3/tests/features/run.sh <deluge.elf> <tools prefix> <blockcount dir> [out dir]
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ELF=$1
TOOLS=$2
BUILD=$3
OUT=${4:-$HERE/out}
mkdir -p "$OUT"
status=0
echo "== mastertune-1.2.1/tests/cpu_stats (host test of the core, on the tree of the ELF)"
TREE=$(cd "$(dirname "$ELF")/../.." && pwd)
if [ -f "$TREE/src/deluge/processing/engines/cpu_stats_core.cpp" ]; then
	if sh "$HERE/../../../mastertune-1.2.1/tests/cpu_stats/run.sh" "$TREE" > "$OUT/cpu_stats_host.log" 2>&1
	then tail -3 "$OUT/cpu_stats_host.log"
	else tail -3 "$OUT/cpu_stats_host.log"; status=1
	fi
else
	echo "skipped: no cpu_stats_core.cpp in $TREE"
fi
for t in arp_extras_emu.py cpu_monitor_emu.py; do
	echo "== $t"
	if python3 "$HERE/$t" "$ELF" --tools "$TOOLS" --build "$BUILD" --out "$OUT/${t%.py}" > "$OUT/${t%.py}.log" 2>&1
	then tail -1 "$OUT/${t%.py}.log"
	else tail -1 "$OUT/${t%.py}.log"; status=1
	fi
done
exit $status
