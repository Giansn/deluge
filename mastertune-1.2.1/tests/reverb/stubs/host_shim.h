// Host test shim: ARM-only helpers the firmware headers expect
#pragma once
#include <algorithm>
#include <cstdint>
#include "util/fixedpoint.h"
#if !defined(__arm__)
static inline int32_t add_saturate(int32_t a, int32_t b) {
	int64_t s = (int64_t)a + b;
	return s > INT32_MAX ? INT32_MAX : (s < INT32_MIN ? INT32_MIN : (int32_t)s);
}
#endif
