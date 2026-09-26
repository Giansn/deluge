#!/bin/sh
# Benchmark of sample playback and the DX7 engine, per voice and block of 128 samples: the firmware's
# SampleLowLevelReader playback functions (sinc and linear interpolation, native), cut out of
# model/sample/sample_low_level_reader.cpp as they are, TimeStretcher::readFromBuffer and getWhichKernel likewise, and
# dsp/dx whole (with neon_fm_kernel.s on ARM).
# Usage: ./run.sh /path/to/DelugeFirmware   (ARM=1 ./run.sh ...: on the Deluge's Cortex-A9 in the emulator, with
# instruction counts per block; ARM_PROFILE=1 in addition shows the top functions)
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")/.." && pwd) # select.sh finds the emulator at $HERE/../arm
. "$HERE/../arm/select.sh"
HERE=$HERE/samples
# KEEP=/some/dir keeps the build there (for objdump of the hot loops).
if [ -n "$KEEP" ]; then B=$KEEP; mkdir -p "$B"; else B=$(mktemp -d); trap 'rm -rf "$B"' EXIT; fi
D="$FW/src"
R="$D/deluge/model/sample/sample_low_level_reader.cpp"
# From bufferIndividualSampleForInterpolation (with the pragma before it) to the end of readSamplesNative, unchanged.
{
	echo '#include "samples_shim.h"'
	echo '#pragma GCC push_options'
	echo "#line $(grep -n 'no-tree-loop-distribute-patterns' "$R" | cut -d: -f1) \"$R\""
	awk '/no-tree-loop-distribute-patterns/ {p = 1} /^bool SampleLowLevelReader::readSamplesForTimeStretching/ {p = 0} p' "$R"
	T="$D/deluge/dsp/timestretch/time_stretcher.cpp"
	echo "#line $(grep -n '^void TimeStretcher::readFromBuffer' "$T" | cut -d: -f1) \"$T\""
	awk '/^void TimeStretcher::readFromBuffer/ {p = 1} p; p && /^}/ {p = 0}' "$T"
	U="$D/deluge/util/functions.cpp"
	echo "#line $(grep -n '^int32_t getWhichKernel' "$U" | cut -d: -f1) \"$U\""
	awk '/^int32_t getWhichKernel/ {p = 1} p; p && /^}/ {p = 0}' "$U"
} > "$B/playback.cpp"
for f in 'SampleLowLevelReader::readSamplesResampled' 'SampleLowLevelReader::readSamplesNative' \
    'TimeStretcher::readFromBuffer' 'getWhichKernel'; do
	grep -q "$f" "$B/playback.cpp" || { echo "$f not found in the firmware"; exit 1; }
done
X="$D/deluge/dsp/dx"
DX="$X/dx7note.cpp $X/engine.cpp $X/EngineMkI.cpp $X/fm_core.cpp $X/fm_op_kernel.cpp $X/env.cpp $X/pitchenv.cpp $X/math_lut.cpp"
# -m32: the firmware computes addresses in 32 bits. No alignment check: the reader loads 32 bits at the deliberately
# misaligned play position, as the Cortex-A9 allows. No signed overflow check: the DX7 code (msfa) lets its phase
# accumulators wrap throughout.
CXX="g++ -m32 -std=gnu++23 -O2 -g -fsanitize=undefined -fno-sanitize-recover=undefined -fno-sanitize=alignment,signed-integer-overflow -w -I $HERE/stubs_pc"
RUN=""
if [ -n "$ARM" ]; then
	CXX="$ARM_CXX"
	RUN="$ARM_RUN"
	# The firmware assembles neon_fm_kernel.s with the same flags; arm_build.py takes C/C++ sources only, so a
	# C++ file includes it as top-level asm.
	echo "__asm__(\".include \\\"$X/neon_fm_kernel.s\\\"\");" > "$B/neon_fm_kernel_s.cpp"
	DX="$DX $B/neon_fm_kernel_s.cpp"
fi
$CXX -I "$HERE/stubs" -I "$HERE/../../delay/stubs" -I "$HERE/../../arm" -I "$D/deluge" -I "$D" -include host_shim.h \
    -o "$B/samples_bench" "$HERE/bench.cpp" "$B/playback.cpp" "$D/deluge/util/lookuptables/lookuptables.cpp" $DX
$RUN "$B/samples_bench"
