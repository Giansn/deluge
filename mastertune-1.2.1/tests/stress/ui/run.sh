#!/bin/sh
# Stress test of the user's actions while a heavy song plays, on the real firmware in the emulator, two builds with the
# same input (stress_ui_emu.py; its docstring says what runs, what's measured and what's modelled): the firmware's own
# task manager, the SSI's DMA in real time, the SD card with the typical card's times.
#   songchange  30 song changes while playing between three heavy songs (make_sd.py's song with 5 synths, its kit,
#               audio track and drone; the same all 24dBDrive; drone tracks plus 5 synths), at 4x tempo (the launch
#               after each change comes 4 times sooner: a 4-bar loop in 2 s): each completes, the load and the wait,
#               the DMA's worst gap and underruns per change and after it, the heap after each change
#   bigcard     ~1,000 songs in groups (v17 and v18 group them the same) and the heavy song playing: 3 rounds of the
#               song browser opened, 40 fast turns, every 3rd group folded out and in, back, closed: gaps, underruns,
#               folder reads, per action instructions, the heap per round
#   save        the heavy song saved 10 times while it plays, loaded back (a song change) and saved again: the same XML?
#   drone       the drone view opened and closed 20 times while the heavy song plays (its knob turned in it)
#   settings    Community features' output limiter and filter crossing guard switched on in the new build, saved,
#               the restart; the old build on that card (ignores them? keeps them when it saves the file?), the new
#               build again
# Crashes (emulator errors, invalid memory accesses, freezeWithError(), fault handlers), hangs (the watchdog: an action
# not back within its limit of emulated time) and error popups are reported for every run.
#
# Usage: ./run.sh <new deluge.elf> <old deluge.elf> [out dir] [scenario ...]   (default: all five)
#   Each ELF with its toolchain two levels up (build/Release/deluge.elf in a firmware tree). BLOCKCOUNT_DIR: where
#   blockcount.so is (else it's built into the out dir). Results: <out>/new/<scenario>.json, <out>/old/..., the table
#   by report.py. One emulator at a time; about 45 minutes for all (the songchange runs 12-15 minutes each).
# Needs: python3 with unicorn 2 and numpy, a C compiler (blockcount.c), the toolchain's gdb and nm.
HERE=$(cd "$(dirname "$0")" && pwd)
NEW=$(realpath "$1")
OLD=$(realpath "$2")
OUT=$(realpath -m "${3:-$(mktemp -d)}")
shift 3 2>/dev/null || shift $#
SCEN=${*:-songchange bigcard save drone settings}
[ -f "$NEW" ] && [ -f "$OLD" ] || { echo "usage: run.sh <new deluge.elf> <old deluge.elf> [out dir] [scenario ...]"; exit 2; }
mkdir -p "$OUT/new" "$OUT/old"
if [ -z "$BLOCKCOUNT_DIR" ] || [ ! -f "$BLOCKCOUNT_DIR/blockcount.so" ]; then
	UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
	cc -O2 -shared -fPIC -I"$UC/include" "$HERE/../../song/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" \
		-l:libunicorn.so.2 -Wl,-rpath,"$UC/lib" || exit 2
	BLOCKCOUNT_DIR=$OUT
fi
export BLOCKCOUNT_DIR
status=0
for s in $SCEN; do
	case $s in
	songchange) opts="--changes ${CHANGES:-30} --settle 0.3 --tempo-scale 4" ;;
	save) opts="--saves ${SAVES:-10} --tempo-scale 4" ;;
	bigcard) opts="--rounds ${ROUNDS:-3} --steps ${STEPS:-40}" ;;
	drone) opts="--cycles ${CYCLES:-20}" ;;
	settings) opts="--old-elf $OLD" ;;
	*) echo "unknown scenario $s"; exit 2 ;;
	esac
	for v in new old; do
		[ "$s" = settings ] && [ $v = old ] && continue  # One run: the new build's card read by the old build
		elf=$NEW
		[ $v = old ] && elf=$OLD
		echo "== $s, $v build ($elf)"
		# shellcheck disable=SC2086
		python3 "$HERE/stress_ui_emu.py" "$elf" "$s" --out "$OUT/$v" $opts 2>&1 | tee "$OUT/$v/$s.log"
		grep -q "PROBLEM\|INVALID ACCESS\|Traceback" "$OUT/$v/$s.log" && status=1
	done
done
python3 "$HERE/report.py" "$OUT"
exit $status
