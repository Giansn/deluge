// Bench stub for util/functions.h: the DX7 code (dsp/dx/dx7note.cpp) needs only getNoise(), the firmware's CONG
// generator. The real header drags in FatFS, the GUI colours and strings.
#pragma once
#include "util/fixedpoint.h"
#include <cstdint>
#include <cstring>
inline uint32_t benchJcong = 380116160;
[[gnu::always_inline]] inline int32_t getNoise() {
	benchJcong = 69069 * benchJcong + 1234567;
	return (int32_t)benchJcong;
}
