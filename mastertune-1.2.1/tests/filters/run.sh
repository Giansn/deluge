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
# filter_neutral_test.cpp (mastertune v18): a configuration that doesn't change renders exactly as before (a hash of
# every mode / route / context; REF=/path/to/older/DelugeFirmware builds it there too and compares), no zipper when
# the cutoff, resonance or morph is automated (block-set against per-sample-set, above 2 kHz), no clicks when a mode or
# the route changes or a filter is switched off.
# lpf_ramp_math_test.cpp (v18): the LP ladders' per-sample coefficient ramps in float64: every mid-ramp set stable and
# within 0.5 dB of the real ladder with its moveability (30 Hz <-> 18 kHz in one block); where the ladders sing
# against their cutoff (printed).
# svf_precision_test.cpp (v18): the SVF at low cutoff against a float64 model: the error's DC, A-weighted error, tail
# (v17 truncated: DC -88 dBFS, tail stuck at -101; v18: DC <= -175, tail -178).
# filter_neutral also: slow sweeps (1 and 1/8 octave per second), jumps (28 Hz <-> 16 kHz every block), hpres (the HP
# ladder's level in the resonance's finest steps), glide (the filter params' 10 ms glide; ONLY=<name> for one sweep).
# lpf_ramp_math also: the ramps in pieces (Filter::rampKnots(), PIECES=0: one straight line) and edge (where the ladders
# sing at the top of the cutoff).
# drive_test.cpp (v18): the drive ladder: its bass compensation (40 Hz within 1 dB of resonance 0's; 1.2.1: -14 dB),
# its own tone (level, pitch), aliasing 2x against 1x, the half-band pair (passband, images, delay), the CPU guard's
# switch (crossfaded), instructions per block (ARM); an older tree gives its numbers only.
# filter_neutral parallel (v18): the parallel route's levels (an off filter adds nothing, both at half level).
# crossing_guard_test.cpp (v18, Community features > Filter crossing guard): with both filters on and the HPF near the
# LPF or above it, the combined resonance peak within 1 dB of the higher single one (off: up to 24 dB above), the
# resonance moving smoothly with a swept cutoff, bit for bit as off where it mustn't act. A tree without the guard:
# skipped.
# silence_restart_test.cpp (v18.1): a kit's / audio track's filters after silence (FilterSet::restartAfterSilence()):
# settings changed while the effects skip their blocks, then a tone renders bit for bit as through filters set to the
# new settings all along (the coefficients' ramp, a crossfade, a switch-on fade-in). A tree without it: skipped.
# TEST=silence_restart, TEST=crossing_guard, TEST=drive, TEST=filter_tone, TEST=hpf_whistle, TEST=lpf_whistle, TEST=lpf_precision, TEST=svf_precision, TEST=lpf_ramp_math or
# TEST=filter_neutral: only that one.
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
# ... the per-sample gain ramps and the crossfades (mastertune v18)
filterDefs() {
  d=""
  grep -q kSaturationGlobal "$1/deluge/dsp/filter/hpladder.h" && d="-DHPF_SATURATION_PER_CONTEXT"
  grep -q kSaturationGlobal "$1/deluge/dsp/filter/lpladder.h" && d="$d -DLPF_SATURATION_PER_CONTEXT"
  grep -q rampGain "$1/deluge/dsp/filter/filter_set.h" && d="$d -DFILTERSET_RAMP_GAIN"
  grep -q kFadeSamples "$1/deluge/dsp/filter/filter_set.h" && d="$d -DFILTERSET_FADES"
  grep -q noiseLastValue "$1/deluge/dsp/filter/lpladder.h" && d="$d -DLPF_CUTOFF_NOISE"
  grep -q kFadeInSamples "$1/deluge/dsp/filter/filter.h" && d="$d -DFILTER_FADE_IN"
  grep -q feedbackFromMoveability "$1/deluge/dsp/filter/lpladder.h" && d="$d -DLPF_RAMP_HORNER"
  grep -q "doubled()" "$1/deluge/dsp/filter/lpladder.h" && d="$d -DLPF_RAMP_PAIRS"
  grep -q "vrhaddq_s32" "$1/deluge/dsp/filter/filter_set.cpp" && d="$d -DFILTERSET_PARALLEL_HALF"
  grep -q "driveCompensate" "$1/deluge/dsp/filter/lpladder.h" && d="$d -DDRIVE_BASS_COMP"
  grep -q "driveWantsOversampling" "$1/deluge/dsp/filter/lpladder.h" && d="$d -DDRIVE_WHERE_121"
  echo "$d"
}
DEFS=$(filterDefs "$D")
CXX="g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -fno-sanitize=signed-integer-overflow -w"
RUN=""
[ -n "$ARM" ] && CXX="$ARM_CXX" && RUN="$ARM_RUN"
F="$D/deluge/dsp/filter"
# For lpf_precision: lpladder.h with its private members public (the coefficients), and fixedpoint.h's rounded
# multiplies rounding on the PC as the Deluge's smmulr / smmlar do (the PC's fallback truncates)
mkdir -p "$B/prec/dsp/filter" "$B/prec/util"
sed 's/^private:/public:/' "$F/lpladder.h" > "$B/prec/dsp/filter/lpladder.h"
# (and Filter's protected members: the ramps' pieces, for lpf_ramp_math)
sed 's/^protected:/public:/' "$F/filter.h" > "$B/prec/dsp/filter/filter.h"
# (and the SVF's, for svf_precision)
sed 's/^private:/public:/' "$F/svf.h" > "$B/prec/dsp/filter/svf.h"
# (and FilterSet's, for drive's where: which way its ladder runs)
sed 's/^private:/public:/' "$F/filter_set.h" > "$B/prec/dsp/filter/filter_set.h"
awk '/_rounded\(q31_t.*\) \{$/ {r = 1} r && /int64_t\)b\) >> 32\)/ {sub(/\* \(int64_t\)b\) >> 32\)/, "* (int64_t)b + 0x80000000LL) >> 32)"); r = 0} /^}/ {r = 0} {print}' \
    "$D/deluge/util/fixedpoint.h" > "$B/prec/util/fixedpoint.h"
grep -q '0x80000000LL) >> 32)' "$B/prec/util/fixedpoint.h" || { echo "run.sh: fixedpoint.h's rounded multiplies not found"; exit 1; }
status=0
for t in ${TEST:-filter_tone hpf_whistle lpf_whistle lpf_precision svf_precision lpf_ramp_math filter_neutral drive crossing_guard silence_restart}; do
  if [ "$t" = crossing_guard ] && ! grep -q crossingGuard "$F/filter_set.h"; then
    echo "== $t: no crossing guard in this tree"
    continue
  fi
  if [ "$t" = silence_restart ] && ! grep -q restartAfterSilence "$F/filter_set.h"; then
    echo "== $t: no FilterSet::restartAfterSilence() in this tree (before mastertune v18.1)"
    continue
  fi
  INC=""
  { [ "$t" = lpf_precision ] || [ "$t" = lpf_ramp_math ] || [ "$t" = svf_precision ] || [ "$t" = drive ]; } && INC="-I $B/prec"
  $CXX $DEFS $INC -I "$T/bench/filters/stubs" -I "$T/delay/stubs" -I "$T/arm" -I "$D/deluge" -I "$D" -include host_shim.h \
      -include definitions_cxx.hpp -o "$B/$t" "$HERE/${t}_test.cpp" "$F/filter_set.cpp" \
      "$F/lpladder.cpp" "$F/hpladder.cpp" "$F/svf.cpp" "$F/filter.cpp" "$D/deluge/util/waves.cpp" \
      "$D/deluge/util/lookuptables/lookuptables.cpp" "$B/fw_functions.cpp"
  echo "== $t"
  if [ "$t" = filter_neutral ] && [ -n "$REF" ]; then
    # The static hash of the older source (REF), built the same way, against this one's
    R=$(cd "$REF" && pwd)/src
    $CXX $(filterDefs "$R") -I "$T/bench/filters/stubs" -I "$T/delay/stubs" -I "$T/arm" -I "$R/deluge" -I "$R" \
        -include host_shim.h -include definitions_cxx.hpp -o "$B/${t}_ref" "$HERE/${t}_test.cpp" \
        "$R/deluge/dsp/filter/filter_set.cpp" "$R/deluge/dsp/filter/lpladder.cpp" "$R/deluge/dsp/filter/hpladder.cpp" \
        "$R/deluge/dsp/filter/svf.cpp" "$R/deluge/dsp/filter/filter.cpp" "$R/deluge/util/waves.cpp" \
        "$R/deluge/util/lookuptables/lookuptables.cpp" "$B/fw_functions.cpp"
    $RUN "$B/${t}_ref" static | grep '^static' > "$B/ref_static.txt"
    $RUN "$B/$t" static | grep '^static' > "$B/new_static.txt"
    if diff "$B/ref_static.txt" "$B/new_static.txt" > "$B/static.diff"; then
      echo "static: the same output as $REF in all $(grep -c 'LPF' "$B/new_static.txt") groups ($(tail -1 "$B/new_static.txt"))"
    else
      echo "static: DIFFERENT from $REF:"; cat "$B/static.diff"; status=1
    fi
    $RUN "$B/$t" zipper || status=1
    $RUN "$B/$t" clicks || status=1
    $RUN "$B/$t" fadein || status=1
    $RUN "$B/$t" slots || status=1
    $RUN "$B/$t" jumps || status=1
    $RUN "$B/$t" hpres || status=1
    $RUN "$B/$t" glide || status=1
    case "$DEFS" in *FILTERSET_PARALLEL_HALF*) $RUN "$B/$t" parallel || status=1 ;; esac
    continue
  fi
  if [ "$t" = drive ] && [ ! -f "$F/halfband.h" ]; then
    # (a tree before v18's drive ladder: its numbers, for comparison; the limits are v18's)
    $RUN "$B/$t" all || true
    continue
  fi
  $RUN "$B/$t" || status=1
done
exit $status
