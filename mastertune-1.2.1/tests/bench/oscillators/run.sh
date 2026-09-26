#!/bin/sh
# Benchmark of the oscillators: the firmware's Voice::renderOsc (with renderWave, renderPulseWave, the crude saw and
# the osc sync path), cut out of model/voice/voice.cpp as it is, per voice and block of 128 samples.
# Usage: ./run.sh /path/to/DelugeFirmware   (ARM=1 ./run.sh ...: on the Deluge's Cortex-A9 in the emulator, with
# instruction counts per block; ARM_PROFILE=1 in addition shows the top functions)
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")/.." && pwd) # select.sh finds the emulator at $HERE/../arm
. "$HERE/../arm/select.sh"
HERE=$HERE/oscillators
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
# The oscillator part of voice.cpp: from renderWave's instance up to the end of Voice::renderOsc, unchanged.
{
	echo '#include "osc_shim.h"'
	echo "#line $(grep -n '^CREATE_WAVE_RENDER_FUNCTION_INSTANCE(renderWave,' "$D/deluge/model/voice/voice.cpp" | cut -d: -f1) \"$D/deluge/model/voice/voice.cpp\""
	awk '/^CREATE_WAVE_RENDER_FUNCTION_INSTANCE\(renderWave,/ {p = 1} /^bool Voice::doFastRelease/ {p = 0} p' \
	    "$D/deluge/model/voice/voice.cpp"
} > "$B/voice_osc.cpp"
grep -q 'Voice::renderOsc' "$B/voice_osc.cpp" || { echo "renderOsc not found in voice.cpp"; exit 1; }
T="$D/deluge/util/lookuptables"
CXX="g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -w -I $HERE/stubs_pc"
RUN=""
[ -n "$ARM" ] && CXX="$ARM_CXX" && RUN="$ARM_RUN"
$CXX -I "$HERE/stubs" -I "$HERE/../../delay/stubs" -I "$HERE/../../arm" -I "$D/deluge" -I "$D" -include host_shim.h \
    -o "$B/osc_bench" "$HERE/bench.cpp" "$B/voice_osc.cpp" "$T/lookuptables.cpp" "$T/saw.cpp" "$T/square.cpp" \
    "$T/analog_square.cpp" "$T/mystery_synth_a_saw.cpp" "$T/mystery_synth_b_saw.cpp"
$RUN "$B/osc_bench"
