// PC only: scalar stand-in for the few NEON intrinsics the oscillator code uses, lane by lane with the same
// saturation and rounding as the Cortex-A9, so the PC build gives the same output (the checksums must match ARM=1).
#pragma once
#include <cstdint>
#include <cstring>

struct int32x4_t { int32_t v[4]; };
struct uint32x4_t { uint32_t v[4]; };
struct int16x4_t { int16_t v[4]; };
struct uint16x4_t { uint16_t v[4]; };
struct int16x4x2_t { int16x4_t val[2]; };
struct int32x2_t { int32_t v[2]; };
struct int64x2_t { int64_t v[2]; };

static inline int32_t neonSat32(int64_t x) { return x > INT32_MAX ? INT32_MAX : x < INT32_MIN ? INT32_MIN : (int32_t)x; }

static inline int32x4_t vdupq_n_s32(int32_t x) { return {{x, x, x, x}}; }
static inline int16x4_t vdup_n_s16(int16_t x) { return {{x, x, x, x}}; }
static inline int32x4_t vsetq_lane_s32(int32_t x, int32x4_t a, int i) { a.v[i] = x; return a; }
static inline uint16x4_t vset_lane_u16(uint16_t x, uint16x4_t a, int i) { a.v[i] = x; return a; }
static inline int16x4_t vset_lane_s16(int16_t x, int16x4_t a, int i) { a.v[i] = x; return a; }
static inline int32x4_t vld1q_s32(const int32_t* p) { int32x4_t r; memcpy(r.v, p, 16); return r; }
static inline void vst1q_s32(int32_t* p, int32x4_t a) { memcpy(p, a.v, 16); }
static inline uint32x4_t vld1q_lane_u32(const uint32_t* p, uint32x4_t a, int i) { memcpy(&a.v[i], p, 4); return a; }
static inline int32x4_t vaddq_s32(int32x4_t a, int32x4_t b) {
	for (int i = 0; i < 4; i++) a.v[i] = (int32_t)((uint32_t)a.v[i] + (uint32_t)b.v[i]);
	return a;
}
static inline int32x4_t vshlq_n_s32(int32x4_t a, int n) {
	for (int i = 0; i < 4; i++) a.v[i] = (int32_t)((uint32_t)a.v[i] << n);
	return a;
}
static inline int32x4_t vqdmulhq_s32(int32x4_t a, int32x4_t b) {
	for (int i = 0; i < 4; i++) a.v[i] = neonSat32((2 * (int64_t)a.v[i] * b.v[i]) >> 32);
	return a;
}
static inline int32x4_t vqrdmulhq_s32(int32x4_t a, int32x4_t b) {
	for (int i = 0; i < 4; i++) {
		if (a.v[i] == INT32_MIN && b.v[i] == INT32_MIN) { a.v[i] = INT32_MAX; continue; }
		a.v[i] = (int32_t)((2 * (int64_t)a.v[i] * b.v[i] + ((int64_t)1 << 31)) >> 32);
	}
	return a;
}
static inline uint16x4_t vshr_n_u16(uint16x4_t a, int n) { for (int i = 0; i < 4; i++) a.v[i] >>= n; return a; }
static inline int16x4_t vreinterpret_s16_u16(uint16x4_t a) { int16x4_t r; memcpy(r.v, a.v, 8); return r; }
static inline uint16x4_t vmovn_u32(uint32x4_t a) { return {{(uint16_t)a.v[0], (uint16_t)a.v[1], (uint16_t)a.v[2], (uint16_t)a.v[3]}}; }
static inline uint16x4_t vshrn_n_u32(uint32x4_t a, int n) {
	return {{(uint16_t)(a.v[0] >> n), (uint16_t)(a.v[1] >> n), (uint16_t)(a.v[2] >> n), (uint16_t)(a.v[3] >> n)}};
}
static inline int32x4_t vshll_n_s16(int16x4_t a, int n) {
	int32x4_t r;
	for (int i = 0; i < 4; i++) r.v[i] = (int32_t)((uint32_t)(int32_t)a.v[i] << n);
	return r;
}
static inline int16x4_t vsub_s16(int16x4_t a, int16x4_t b) { for (int i = 0; i < 4; i++) a.v[i] = (int16_t)(a.v[i] - b.v[i]); return a; }
static inline int16x4_t vorr_s16(int16x4_t a, int16x4_t b) { for (int i = 0; i < 4; i++) a.v[i] |= b.v[i]; return a; }
static inline int16x4_t vand_s16(int16x4_t a, int16x4_t b) { for (int i = 0; i < 4; i++) a.v[i] &= b.v[i]; return a; }
static inline int32x4_t vqdmull_s16(int16x4_t a, int16x4_t b) {
	int32x4_t r;
	for (int i = 0; i < 4; i++) r.v[i] = neonSat32(2 * (int64_t)a.v[i] * b.v[i]);
	return r;
}
static inline int32x4_t vqdmlal_s16(int32x4_t acc, int16x4_t a, int16x4_t b) {
	int32x4_t p = vqdmull_s16(a, b);
	for (int i = 0; i < 4; i++) acc.v[i] = neonSat32((int64_t)acc.v[i] + p.v[i]);
	return acc;
}

// Used by the optimised oscillators (v12 perf): NEON interpolation weights, vld2 table reads, smmlar on 4 lanes.
static inline uint32x4_t vdupq_n_u32(uint32_t x) { return {{x, x, x, x}}; }
static inline uint32x4_t vld1q_u32(const uint32_t* p) { uint32x4_t r; memcpy(r.v, p, 16); return r; }
static inline uint32x4_t vaddq_u32(uint32x4_t a, uint32x4_t b) { for (int i = 0; i < 4; i++) a.v[i] += b.v[i]; return a; }
static inline uint32x4_t vsubq_u32(uint32x4_t a, uint32x4_t b) { for (int i = 0; i < 4; i++) a.v[i] -= b.v[i]; return a; }
static inline uint32x4_t vminq_u32(uint32x4_t a, uint32x4_t b) {
	for (int i = 0; i < 4; i++) a.v[i] = a.v[i] < b.v[i] ? a.v[i] : b.v[i];
	return a;
}
static inline uint32x4_t vcgeq_u32(uint32x4_t a, uint32x4_t b) {
	for (int i = 0; i < 4; i++) a.v[i] = a.v[i] >= b.v[i] ? 0xFFFFFFFFu : 0;
	return a;
}
static inline int32x4_t vreinterpretq_s32_u32(uint32x4_t a) { int32x4_t r; memcpy(r.v, a.v, 16); return r; }
static inline uint32x4_t vreinterpretq_u32_s32(int32x4_t a) { uint32x4_t r; memcpy(r.v, a.v, 16); return r; }
static inline int32x4_t vnegq_s32(int32x4_t a) { for (int i = 0; i < 4; i++) a.v[i] = (int32_t)(0u - (uint32_t)a.v[i]); return a; }
static inline int32x4_t veorq_s32(int32x4_t a, int32x4_t b) { for (int i = 0; i < 4; i++) a.v[i] ^= b.v[i]; return a; }
static inline int32x2_t vget_low_s32(int32x4_t a) { return {{a.v[0], a.v[1]}}; }
static inline int32x2_t vget_high_s32(int32x4_t a) { return {{a.v[2], a.v[3]}}; }
static inline int32x4_t vcombine_s32(int32x2_t a, int32x2_t b) { return {{a.v[0], a.v[1], b.v[0], b.v[1]}}; }
static inline int64x2_t vmull_s32(int32x2_t a, int32x2_t b) { return {{(int64_t)a.v[0] * b.v[0], (int64_t)a.v[1] * b.v[1]}}; }
static inline int32x2_t vrshrn_n_s64(int64x2_t a, int n) { // Bits n.. of a + 2^(n-1), modulo 2^64 as the NEON adder
	int32x2_t r;
	for (int i = 0; i < 2; i++) r.v[i] = (int32_t)(uint32_t)(((uint64_t)a.v[i] + ((uint64_t)1 << (n - 1))) >> n);
	return r;
}
static inline int16x4x2_t vld2_dup_s16(const int16_t* p) {
	int16x4x2_t r;
	for (int i = 0; i < 4; i++) { memcpy(&r.val[0].v[i], p, 2); memcpy(&r.val[1].v[i], p + 1, 2); }
	return r;
}
static inline int16x4x2_t vld2_lane_s16(const int16_t* p, int16x4x2_t a, int i) {
	memcpy(&a.val[0].v[i], p, 2);
	memcpy(&a.val[1].v[i], p + 1, 2);
	return a;
}
