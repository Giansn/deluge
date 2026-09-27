#!/bin/sh
# The resonant HPF's whistle (mastertune-v16 fix): the firmware's filters (dsp/filter, FilterSet) with the HPF on and
# resonance medium to full while the LPF is turned, in the song's, a kit's, a synth's and a kit row's filters, every
# HPF mode, route and LPF mode (see filter_tone_test.cpp). Fails where the HPF whistles more than 10 dB above the music
# (v16: the HP ladder, up to ~24 dB on the song's filters).
# Usage: ./run.sh /path/to/DelugeFirmware   (ARM=1 ./run.sh ...: on the Deluge's Cortex-A9 in the emulator, the
# firmware's own arithmetic; VERBOSE=1: every case)
# song_filter_emu.py: the same in the whole firmware (song view, the gold knob), for a case or two at a time.
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(cd "$HERE/.." && pwd)
H=$HERE; HERE=$T/delay; . "$T/arm/select.sh"; HERE=$H # select.sh finds tests/arm as $HERE/../arm
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
# getExp (the knobs' params) and quickLog / instantTan (setConfig) straight out of functions.cpp
{ echo '#include "util/functions.h"'; sed -n '/^int32_t quickLog(/,/^}/p;/^int32_t instantTan(/,/^}/p;/^int32_t getExp(/,/^}/p;/^int32_t interpolateTable(/,/^}/p' \
    "$D/deluge/util/functions.cpp"; } > "$B/fw_functions.cpp"
# The fixed firmware takes the HP ladder's saturation from the caller (song/kit/track or voice)
DEFS=""
grep -q kSaturationGlobal "$D/deluge/dsp/filter/hpladder.h" && DEFS="-DHPF_SATURATION_PER_CONTEXT"
CXX="g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -fno-sanitize=signed-integer-overflow -w"
RUN=""
[ -n "$ARM" ] && CXX="$ARM_CXX" && RUN="$ARM_RUN"
F="$D/deluge/dsp/filter"
$CXX $DEFS -I "$T/bench/filters/stubs" -I "$T/delay/stubs" -I "$T/arm" -I "$D/deluge" -I "$D" -include host_shim.h \
    -include definitions_cxx.hpp -o "$B/filter_tone_test" "$HERE/filter_tone_test.cpp" "$F/filter_set.cpp" \
    "$F/lpladder.cpp" "$F/hpladder.cpp" "$F/svf.cpp" "$F/filter.cpp" "$D/deluge/util/waves.cpp" \
    "$D/deluge/util/lookuptables/lookuptables.cpp" "$B/fw_functions.cpp"
$RUN "$B/filter_tone_test"
