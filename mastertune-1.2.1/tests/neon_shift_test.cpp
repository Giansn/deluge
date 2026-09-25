// Runs on the real Cortex-A9 instruction set (in an emulator): the NEON shift must give exactly the buffer the old
// element-by-element loop gave, for every shift distance, followed by the caller refilling the freed positions.
#include "dsp/interpolation/shift_buffer.h"
#include <cstdint>

static uint32_t rng = 12345;
static int16_t next() {
	rng = rng * 1664525u + 1013904223u;
	return (int16_t)(rng >> 16);
}

// The 1.2.1 code, verbatim in effect
[[gnu::noinline]] static void referenceShift(int16_t* buffer, int32_t n) {
	for (int32_t i = 16 - 1; i >= n; i--) {
		buffer[i] = buffer[i - n];
	}
}

extern "C" [[gnu::used]] int32_t runTest() {
	int32_t mismatches = 0;
	for (int32_t round = 0; round < 2000; round++) {
		alignas(16) int16x4_t fast[4];
		alignas(16) int16_t slow[16];
		for (int32_t i = 0; i < 16; i++) {
			slow[i] = next();
			((int16_t*)fast)[i] = slow[i];
		}
		int32_t n = round % 18; // 0 ... 17
		referenceShift(slow, n);
		deluge::dsp::shiftInterpolationBuffer(fast, n);
		// The caller writes the new samples into positions n-1 ... 0
		for (int32_t i = (n < 16 ? n : 16) - 1; i >= 0; i--) {
			int16_t value = next();
			slow[i] = value;
			((int16_t*)fast)[i] = value;
		}
		for (int32_t i = 0; i < 16; i++) {
			if (slow[i] != ((int16_t*)fast)[i]) {
				mismatches++;
			}
		}
	}
	return mismatches;
}

extern "C" [[gnu::used, gnu::naked]] void _start() {
	asm volatile("bl runTest\n bkpt #0");
}
