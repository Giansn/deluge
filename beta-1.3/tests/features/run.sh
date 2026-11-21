#!/bin/sh
# The tests of the features ported from mastertune 1.2.1 (beta-1.3/features), each on the real firmware in the
# emulator with the v1.3 rig. Each fails on the build without the features (the fixes alone) and passes with them.
#   arp_extras_emu.py   0001: latch, ratchet notes, bounce, bounce length and velocity, roll and ball, latched
#                       notes dropped when the arp goes off (12 cases)
#   cpu_monitor_emu.py  0002: the CPU monitor: LEARN + tempo encoder, the OLED line, the SysEx, kept across a restart
#   master_tune_emu.py  0003: master tune: every part's pitch in the output at 432 Hz and after the menu retunes it
#                       while playing, MIDI RPN 1, CV, the "mtun" chunk of an export, kept, reset
#   scan_emu.py         0004: Scan: the note and cents at 440 and 432 Hz, Hz, BPM, a C major chord's key, the
#                       analysis freed on leaving
#   dj_emu.py           0005: the DJ filter's positions, its params and output levels against the filters' own knobs,
#                       sync to 128 BPM from Scan, the nudge and its return
#   usb_audio_emu.py    0006: USB audio's setting saved at once and read before USB starts, the frames into the ring as
#                       sent to the codec, the song info SysEx
#   arp_freeze_emu.py   0007: arp freeze: the frozen clip plays what the arp played (plain, reversed, mastertune's
#                       ratchets), undo brings the arp back
#   arp_note_order_emu.py 0008: the five note orders over a four-note chord and two octaves
#   arp_note_order_test.cpp (host, 0008): the orders' cycles for 1 to 7 notes
#   (and mastertune's host tests on the ELF's tree: the CPU monitor's core, mastertune-1.2.1/tests/cpu_stats; the
#   master tune arithmetic, master_tune_math_test.cpp; the recorder's WAV header with the "mtun" chunk; Scan's
#   analysis, tests/scan; the DJ knob, tests/dj; USB audio's descriptors and ring, tests/usbaudio)
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
MT="$HERE/../../../mastertune-1.2.1/tests"
echo "== mastertune-1.2.1/tests: master_tune_math_test.cpp and wav_mtun_chunk_test.py (host)"
if [ -f "$TREE/src/deluge/model/tuning/master_tune_math.h" ]; then
	if g++ -std=c++20 -O2 -I "$TREE/src/deluge" "$MT/master_tune_math_test.cpp" -o "$OUT/master_tune_math" \
		&& "$OUT/master_tune_math" > "$OUT/master_tune_host.log" 2>&1 \
		&& python3 "$MT/wav_mtun_chunk_test.py" >> "$OUT/master_tune_host.log" 2>&1
	then tail -1 "$OUT/master_tune_host.log"
	else tail -3 "$OUT/master_tune_host.log"; status=1
	fi
else
	echo "skipped: no model/tuning/master_tune_math.h in $TREE"
fi
for h in scan:dsp/scan/scan.cpp dj:dsp/dj/dj_filter.h usbaudio:io/usb/usb_audio_stream.h; do
	d=${h%%:*}
	echo "== mastertune-1.2.1/tests/$d (host)"
	if [ -f "$TREE/src/deluge/${h#*:}" ]; then
		if sh "$MT/$d/run.sh" "$TREE" > "$OUT/${d}_host.log" 2>&1
		then tail -1 "$OUT/${d}_host.log"
		else tail -3 "$OUT/${d}_host.log"; status=1
		fi
	else
		echo "skipped: no ${h#*:} in $TREE"
	fi
done
echo "== arp_note_order_test.cpp (host)"
if [ -f "$TREE/src/deluge/modulation/arp_note_order.h" ]; then
	if g++ -std=c++20 -O1 -I "$TREE/src" -I "$TREE/src/deluge" "$HERE/arp_note_order_test.cpp" -o "$OUT/arp_note_order" \
		&& "$OUT/arp_note_order" > "$OUT/arp_note_order_host.log" 2>&1
	then tail -1 "$OUT/arp_note_order_host.log"
	else tail -3 "$OUT/arp_note_order_host.log"; status=1
	fi
else
	echo "skipped: no modulation/arp_note_order.h in $TREE"
fi
for t in arp_extras_emu.py cpu_monitor_emu.py master_tune_emu.py scan_emu.py dj_emu.py usb_audio_emu.py \
	arp_freeze_emu.py arp_note_order_emu.py; do
	echo "== $t"
	if python3 "$HERE/$t" "$ELF" --tools "$TOOLS" --build "$BUILD" --out "$OUT/${t%.py}" > "$OUT/${t%.py}.log" 2>&1
	then tail -1 "$OUT/${t%.py}.log"
	else tail -1 "$OUT/${t%.py}.log"; status=1
	fi
done
exit $status
