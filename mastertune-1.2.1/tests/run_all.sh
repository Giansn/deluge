#!/bin/sh
# Runs all tests of this folder against a firmware tree: on the PC and on the Deluge's Cortex-A9 in the emulator
# (unicorn, see arm/). The Cortex-A9 runs are built with the firmware's own toolchain and flags, so they run the same
# machine code as the Deluge, and they report what the DSP costs per block of 128 samples.
#
# Usage: ./run_all.sh /path/to/DelugeFirmware [pc|arm|both]   (default both; the tree at the newest mastertune version)
# Needs: g++ with multilib (streaming test on the PC), python3 with unicorn (pip install unicorn), and for the WAV test
# soundfile and scipy.
FW=$(cd "$1" && pwd) || exit 2
WHAT=${2:-both}
HERE=$(cd "$(dirname "$0")" && pwd)
LOG=$(mktemp -d)
trap 'rm -rf "$LOG"' EXIT
PASSED=""
FAILED=""

run() {
	name=$1
	shift
	printf '%-34s' "$name"
	start=$(date +%s)
	if "$@" > "$LOG/out" 2>&1; then
		PASSED="$PASSED\n  $name"
		printf 'ok      (%ss)\n' $(($(date +%s) - start))
	else
		FAILED="$FAILED\n  $name"
		printf 'FAILED  (%ss)\n' $(($(date +%s) - start))
		tail -n 15 "$LOG/out" | sed 's/^/    /'
	fi
	grep '^\[arm\]' "$LOG/out" | sed 's/^\[arm\] /  /' >> "$LOG/costs"
}

master_tune_pc() {
	g++ -std=c++20 -O2 -I "$FW/src/deluge" "$HERE/master_tune_math_test.cpp" -o "$LOG/mt" && "$LOG/mt"
}
master_tune_arm() {
	python3 "$HERE/arm/arm_build.py" "$FW" -o "$LOG/mt.elf" -I "$FW/src/deluge" "$HERE/master_tune_math_test.cpp" &&
	    DELUGE_FIRMWARE="$FW" python3 "$HERE/arm/arm_run.py" "$LOG/mt.elf"
}
neon_shift_arm() {
	python3 "$HERE/arm/arm_build.py" "$FW" -o "$LOG/neon.elf" -I "$FW/src/deluge" "$HERE/neon_shift_test.cpp" &&
	    DELUGE_FIRMWARE="$FW" python3 "$HERE/arm/arm_run.py" "$LOG/neon.elf"
}
wav_chunk_pc() {
	python3 -c "import soundfile, scipy" 2>/dev/null || { echo "soundfile/scipy missing"; return 1; }
	python3 "$HERE/wav_mtun_chunk_test.py"
}

if [ "$WHAT" = pc ] || [ "$WHAT" = both ]; then
	echo "On the PC:"
	run "master tune arithmetic" master_tune_pc
	run "WAV tuning chunk (v2)" wav_chunk_pc
	run "SD access over USB (v7)" sh "$HERE/smsysex/run.sh" "$FW"
	run "USB audio (v8)" sh "$HERE/usbaudio/run.sh" "$FW"
	run "cluster loading queue (v9)" sh "$HERE/streaming/run.sh" "$FW"
	run "reverb (v10)" sh "$HERE/reverb/run.sh" "$FW"
	run "delay (v11)" sh "$HERE/delay/run.sh" "$FW"
	[ -f "$FW/src/deluge/dsp/drone/drone.cpp" ] && run "drone (v12)" sh "$HERE/drone/run.sh" "$FW"
fi
if [ "$WHAT" = arm ] || [ "$WHAT" = both ]; then
	echo "On the Deluge's Cortex-A9, in the emulator:"
	run "master tune arithmetic" master_tune_arm
	run "NEON buffer shift (v3)" neon_shift_arm
	run "SD access over USB (v7)" env ARM=1 sh "$HERE/smsysex/run.sh" "$FW"
	run "USB audio (v8)" env ARM=1 sh "$HERE/usbaudio/run.sh" "$FW"
	run "cluster loading queue (v9)" env ARM=1 sh "$HERE/streaming/run.sh" "$FW"
	run "reverb (v10)" env ARM=1 sh "$HERE/reverb/run.sh" "$FW"
	run "delay (v11)" env ARM=1 sh "$HERE/delay/run.sh" "$FW"
	[ -f "$FW/src/deluge/dsp/drone/drone.cpp" ] && run "drone (v12)" env ARM=1 sh "$HERE/drone/run.sh" "$FW"
fi

if [ -s "$LOG/costs" ]; then
	echo
	echo "Cost on the Cortex-A9 (instructions counted in the emulator; % at 1 instruction per cycle, 400 MHz):"
	cat "$LOG/costs"
fi
if [ -n "$FAILED" ]; then
	printf "\nFailed:$FAILED\n"
	exit 1
fi
echo "
all tests passed"
