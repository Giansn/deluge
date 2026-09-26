#!/bin/sh
# Benchmark of the firmware's filters (dsp/filter: FilterSet with lpladder, hpladder, svf), per 128-sample block.
# Usage: ./run.sh /path/to/DelugeFirmware   (ARM=1 ./run.sh ...: instruction counts on the Deluge's Cortex-A9 in the
# emulator; ARM_PROFILE=1 in addition: by function). On the PC it prints an output hash per case (bit-exactness check).
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(cd "$HERE/../.." && pwd)
HERE=$T/delay . "$T/arm/select.sh" # select.sh finds tests/arm as $HERE/../arm
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
# quickLog and instantTan (filter setConfig) straight out of functions.cpp, which drags in the whole firmware
{ echo '#include "util/functions.h"'; sed -n '/^int32_t quickLog(/,/^}/p;/^int32_t instantTan(/,/^}/p' \
    "$D/deluge/util/functions.cpp"; } > "$B/fw_functions.cpp"
CXX="g++ -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -fno-sanitize=signed-integer-overflow -w"
RUN=""
[ -n "$ARM" ] && CXX="$ARM_CXX" && RUN="$ARM_RUN"
F="$D/deluge/dsp/filter"
$CXX -I "$HERE/stubs" -I "$T/delay/stubs" -I "$T/arm" -I "$D/deluge" -I "$D" -include host_shim.h \
    -o "$B/filter_bench" "$HERE/bench.cpp" "$F/filter_set.cpp" "$F/lpladder.cpp" "$F/hpladder.cpp" "$F/svf.cpp" \
    "$F/filter.cpp" "$D/deluge/util/waves.cpp" "$D/deluge/util/lookuptables/lookuptables.cpp" "$B/fw_functions.cpp"
$RUN "$B/filter_bench"
