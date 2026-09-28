#!/bin/sh
# The volume stage (mastertune v18, dsp/gain_ramp.h): ModControllableAudio::processReverbSendAndVolume() as the firmware
# has it (cut out of mod_controllable_audio.cpp), the sound's pan (panInPlace / panMonoToStereo) and the ramps, on the
# Deluge's Cortex-A9 in the emulator (NEON: ARM only). See volume_test.cpp for the cases and what fails.
# limiter_test.cpp: the output limiter (dsp/output_limiter.cpp); detents_test.cpp (on the PC): the volume knobs' 0.5 dB
# steps and their display (modulation/params/volume_steps.cpp).
# Usage: ./run.sh /path/to/DelugeFirmware   (TEST=<case> for one: ramp neutral precision zipper bigstep pan cpu
#   limiter-below limiter-boost limiter-cpu detents)
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(cd "$HERE/.." && pwd)
H=$HERE; HERE=$T/delay; . "$T/arm/select.sh"; HERE=$H
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
M="$D/deluge/model/mod_controllable"
[ -f "$D/deluge/dsp/gain_ramp.h" ] || { echo "no dsp/gain_ramp.h in $FW (not a v18 tree)"; exit 2; }
{
	echo '#include "volume_shim.h"'
	echo '#include "util/functions.h"'
	awk -v f="$M/mod_controllable_audio.cpp" '
	    /^void ModControllableAudio::(processReverbSendAndVolume|restartVolumeRamps)\(/ { p = 1; printf "#line %d \"%s\"\n", NR, f }
	    p; p && /^}/ {p = 0}' "$M/mod_controllable_audio.cpp"
	sed -n '/^bool shouldDoPanning(/,/^}/p;/^int32_t getFinalParameterValueVolume(/,/^}/p' "$D/deluge/util/functions.cpp"
} > "$B/volume_extract.cpp"
[ "$(grep -c '^void ModControllableAudio::' "$B/volume_extract.cpp")" -eq 2 ] || { echo "volume stage not found"; exit 1; }
# v17's NEON loop for the instruction counts (V17=<commit>, default mastertune-v17's 2cb5e31b), where the tree has it
V17_DEF=""
if git -C "$FW" show "${V17:-2cb5e31b}:src/deluge/model/mod_controllable/track_fx_kernels.h" > "$B/v17_kernels.h" 2> /dev/null; then
	V17_DEF="-DV17_KERNELS"
fi
$ARM_CXX $V17_DEF -I "$B" -I "$HERE/stubs" -I "$T/delay/stubs" -I "$T/arm" -I "$D/deluge" -I "$D" -include host_shim.h \
    -include definitions_cxx.hpp -o "$B/volume_test" "$HERE/volume_test.cpp" "$B/volume_extract.cpp"
$ARM_CXX -I "$T/arm" -I "$D/deluge" -I "$D" -include host_shim.h -I "$T/delay/stubs" -include definitions_cxx.hpp \
    -o "$B/limiter_test" "$HERE/limiter_test.cpp" "$D/deluge/dsp/output_limiter.cpp"
status=0
for t in ${TEST:-ramp neutral precision zipper bigstep pan cpu limiter-below limiter-boost limiter-cpu}; do
	echo "== $t"
	case $t in
	limiter-*) $ARM_RUN "$B/limiter_test" "${t#limiter-}" || status=1 ;;
	*) $ARM_RUN "$B/volume_test" "$t" || status=1 ;;
	esac
done
# The detents and their display (modulation/params/volume_steps.cpp), on the PC
if [ -z "$TEST" ] || [ "$TEST" = detents ]; then
	echo "== detents"
	gcc -O2 -w -c -I "$D/deluge" -I "$D" -o "$B/cf.o" "$D/deluge/util/cfunctions.c"
	g++ -std=gnu++23 -O2 -w -I "$D/deluge" -I "$D" -include definitions_cxx.hpp -I "$T/delay/stubs" -o "$B/detents" \
	    "$HERE/detents_test.cpp" "$D/deluge/modulation/params/volume_steps.cpp" "$B/cf.o"
	"$B/detents" || status=1
fi
exit $status
