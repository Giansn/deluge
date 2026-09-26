#!/bin/sh
# Benchmark of the effects, per stereo block of 128 samples: mod FX (chorus, stereo chorus, flanger, phaser) and EQ
# (ModControllableAudio::processFX), bitcrush and sample rate reduction (processSRRAndBitcrushing), both cut out of
# model/mod_controllable/mod_controllable_audio.cpp as they are; the compressor (dsp/compressor/rms_feedback.cpp) and
# the analog delay's impulse response (dsp/convolution/impulse_response_processor.h) as whole files.
# Usage: ./run.sh /path/to/DelugeFirmware   (ARM=1 ./run.sh ...: instruction counts on the Deluge's Cortex-A9 in the
# emulator; ARM_PROFILE=1 in addition: by function). On the PC it prints an output hash per case (bit-exactness check).
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(cd "$HERE/../.." && pwd)
H=$HERE; HERE=$T/delay; . "$T/arm/select.sh"; HERE=$H # select.sh finds tests/arm as $HERE/../arm
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
M="$D/deluge/model/mod_controllable"
awk '/^struct Grain \{/ {p = 1} p; p && /^\};/ {p = 0}' "$M/mod_controllable_audio.h" > "$B/grain.h"
# The effect functions of mod_controllable_audio.cpp, unchanged (the file itself drags in the whole firmware)
{
	echo '#include "fx_shim.h"'
	awk -v f="$M/mod_controllable_audio.cpp" '
	    /^(inline )?(void|bool) ModControllableAudio::(hasBassAdjusted|hasTrebleAdjusted|processFX|isBitcrushingEnabled|isSRREnabled|processSRRAndBitcrushing|doEQ)\(/ {
	        p = 1; printf "#line %d \"%s\"\n", NR, f }
	    p; p && /^}/ {p = 0}' "$M/mod_controllable_audio.cpp"
} > "$B/fx_extract.cpp"
[ "$(grep -c '^[a-z ]*ModControllableAudio::' "$B/fx_extract.cpp")" = 7 ] || { echo "effect functions not found"; exit 1; }
# getExp and interpolateTable (EQ, SRR), quickLog, random and shouldDoPanning (grain) straight out of functions.cpp
{ echo '#include "util/functions.h"'; sed -n '/^int32_t interpolateTable(/,/^}/p;/^int32_t getExp(/,/^}/p;/^int32_t quickLog(/,/^}/p;/^int32_t random(/,/^}/p;/^bool shouldDoPanning(/,/^}/p' \
    "$D/deluge/util/functions.cpp"; } > "$B/fw_functions.cpp"
CXX="g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -fno-sanitize=signed-integer-overflow -w"
RUN=""
[ -n "$ARM" ] && CXX="$ARM_CXX" && RUN="$ARM_RUN"
$CXX -I "$HERE/stubs" -I "$B" -I "$T/delay/stubs" -I "$T/arm" -I "$D/deluge" -I "$D" -include host_shim.h \
    -include definitions_cxx.hpp -include util/functions.h \
    -o "$B/fx_bench" "$HERE/bench.cpp" "$B/fx_extract.cpp" "$D/deluge/dsp/compressor/rms_feedback.cpp" \
    "$D/deluge/util/waves.cpp" "$D/deluge/util/lookuptables/lookuptables.cpp" "$B/fw_functions.cpp"
$RUN "$B/fx_bench"
