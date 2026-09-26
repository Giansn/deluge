// PC only: the oscillator bench's scalar stand-ins for NEON intrinsics, plus the few more that track_fx_kernels.h uses,
// lane by lane as on the Cortex-A9.
#pragma once
#include "../../oscillators/stubs_pc/arm_neon.h"

struct int32x4x2_t { int32x4_t val[2]; };

static inline int32x4x2_t vld2q_s32(const int32_t* p) {
	int32x4x2_t r;
	for (int i = 0; i < 4; i++) {
		r.val[0].v[i] = p[2 * i];
		r.val[1].v[i] = p[2 * i + 1];
	}
	return r;
}
static inline void vst2q_s32(int32_t* p, int32x4x2_t a) {
	for (int i = 0; i < 4; i++) {
		p[2 * i] = a.val[0].v[i];
		p[2 * i + 1] = a.val[1].v[i];
	}
}
static inline int32x4_t vreinterpretq_s32_s64(int64x2_t a) { int32x4_t r; memcpy(r.v, a.v, 16); return r; }
static inline int32x4x2_t vuzpq_s32(int32x4_t a, int32x4_t b) {
	return {{{{a.v[0], a.v[2], b.v[0], b.v[2]}}, {{a.v[1], a.v[3], b.v[1], b.v[3]}}}};
}
static inline uint32x4_t vmlaq_n_u32(uint32x4_t a, uint32x4_t b, uint32_t c) {
	for (int i = 0; i < 4; i++) a.v[i] += b.v[i] * c;
	return a;
}
static inline int32x4_t vandq_s32(int32x4_t a, int32x4_t b) {
	for (int i = 0; i < 4; i++) a.v[i] &= b.v[i];
	return a;
}
