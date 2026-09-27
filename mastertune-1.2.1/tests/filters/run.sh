#!/bin/sh
# The resonant HPF's whistle (mastertune-v16 fix): the firmware's filters (dsp/filter, FilterSet) with the HPF on and
# resonance medium to full while the LPF is turned, in the song's, a kit's, a synth's and a kit row's filters, every
# HPF mode, route and LPF mode (see filter_tone_test.cpp). Fails where the HPF whistles more than 10 dB above the music
# (v16: the HP ladder, up to ~24 dB on the song's filters).
# Usage: ./run.sh /path/to/DelugeFirmware   (ARM=1 ./run.sh ...: on the Deluge's Cortex-A9 in the emulator, the
# firmware's own arithmetic; VERBOSE=1: every case)
# hpf_whistle_test.cpp: the song's filters after the music stops: the HPF's own tone against the music (fails more
# than 10 dB above it; v16: up to +24 dB), the drive LPF's own tone (from 50 % LPF resonance, as 1.2.1) counted apart.
# lpf_precision_test.cpp: the 12 / 24 dB LP ladders' arithmetic noise at low cutoff against a float64 model of the same
# ladder (the rustle report on v17): fails where the A-weighted error at cutoff 5 / 15 is above -180 dBFS or the output
# doesn't die away after the music at resonance 0 / 25 % (v17: -161 to -178 dBFS, tails up to -114 dBFS; lpf-fix: -183
# to -200 dBFS, silent). Built with lpladder.h's private members public and, on the PC, rounding as the Deluge rounds.
# TEST=filter_tone, TEST=hpf_whistle, TEST=lpf_whistle or TEST=lpf_precision: only that one.
# song_filter_emu.py, master_filter_emu.py --check 10: the same in the whole firmware (song view, the gold knob).
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(cd "$HERE/.." && pwd)
H=$HERE; HERE=$T/delay; . "$T/arm/select.sh"; HERE=$H # select.sh finds tests/arm as $HERE/../arm
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
# getExp (the knobs' params) and quickLog / instantTan (setConfig) straight out of functions.cpp
{ echo '#include "util/functions.h"'; sed -n '/^int32_t quickLog(/,/^}/p;/^int32_t instantTan(/,/^}/p;/^int32_t getExp(/,/^}/p;/^int32_t interpolateTable(/,/^}/p;/^int32_t getFinalParameterValueLinear(/,/^}/p;/^int32_t getFinalParameterValueExp(/,/^}/p;/^int32_t cableToLinearParamShortcut(/,/^}/p;/^int32_t cableToExpParamShortcut(/,/^}/p' \
    "$D/deluge/util/functions.cpp"; } > "$B/fw_functions.cpp"
# The fixed firmware takes the HP ladder's saturation from the caller (song/kit/track or voice)
DEFS=""
grep -q kSaturationGlobal "$D/deluge/dsp/filter/hpladder.h" && DEFS="-DHPF_SATURATION_PER_CONTEXT"
# ... and the 12 / 24 dB LP ladders' (mastertune-v17 lpf-fix)
grep -q kSaturationGlobal "$D/deluge/dsp/filter/lpladder.h" && DEFS="$DEFS -DLPF_SATURATION_PER_CONTEXT"
CXX="g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -fno-sanitize=signed-integer-overflow -w"
RUN=""
[ -n "$ARM" ] && CXX="$ARM_CXX" && RUN="$ARM_RUN"
F="$D/deluge/dsp/filter"
# For lpf_precision: lpladder.h with its private members public (the coefficients), and fixedpoint.h's rounded
# multiplies rounding on the PC as the Deluge's smmulr / smmlar do (the PC's fallback truncates)
mkdir -p "$B/prec/dsp/filter" "$B/prec/util"
sed 's/^private:/public:/' "$F/lpladder.h" > "$B/prec/dsp/filter/lpladder.h"
awk '/_rounded\(q31_t.*\) \{$/ {r = 1} r && /int64_t\)b\) >> 32\)/ {sub(/\* \(int64_t\)b\) >> 32\)/, "* (int64_t)b + 0x80000000LL) >> 32)"); r = 0} /^}/ {r = 0} {print}' \
    "$D/deluge/util/fixedpoint.h" > "$B/prec/util/fixedpoint.h"
grep -q '0x80000000LL) >> 32)' "$B/prec/util/fixedpoint.h" || { echo "run.sh: fixedpoint.h's rounded multiplies not found"; exit 1; }
status=0
for t in ${TEST:-filter_tone hpf_whistle lpf_whistle lpf_precision}; do
  INC=""
  [ "$t" = lpf_precision ] && INC="-I $B/prec"
  $CXX $DEFS $INC -I "$T/bench/filters/stubs" -I "$T/delay/stubs" -I "$T/arm" -I "$D/deluge" -I "$D" -include host_shim.h \
      -include definitions_cxx.hpp -o "$B/$t" "$HERE/${t}_test.cpp" "$F/filter_set.cpp" \
      "$F/lpladder.cpp" "$F/hpladder.cpp" "$F/svf.cpp" "$F/filter.cpp" "$D/deluge/util/waves.cpp" \
      "$D/deluge/util/lookuptables/lookuptables.cpp" "$B/fw_functions.cpp"
  echo "== $t"
  $RUN "$B/$t" || status=1
done
exit $status
