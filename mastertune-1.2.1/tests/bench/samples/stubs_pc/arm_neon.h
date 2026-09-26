// PC only: the NEON intrinsics the sample interpolation (dsp/interpolation/interpolate.h, shift_buffer.h) uses, lane by
// lane with the Cortex-A9's wrapping and saturation, on GCC vector types so the firmware's lane subscripts
// (interpolationBuffer[0][0][i]) work as on ARM.
#pragma once
#include <cstdint>
#include <cstring>
typedef int16_t int16x4_t __attribute__((vector_size(8)));
typedef int16_t int16x8_t __attribute__((vector_size(16)));
typedef int32_t int32x2_t __attribute__((vector_size(8)));
typedef int32_t int32x4_t __attribute__((vector_size(16)));
static inline int16x8_t vld1q_s16(const int16_t* p) { int16x8_t r; memcpy(&r, p, 16); return r; }
static inline int16x8_t vcombine_s16(int16x4_t lo, int16x4_t hi) {
	int16x8_t r;
	for (int i = 0; i < 4; i++) { r[i] = lo[i]; r[i + 4] = hi[i]; }
	return r;
}
static inline int16x4_t vget_low_s16(int16x8_t a) { return int16x4_t{a[0], a[1], a[2], a[3]}; }
static inline int16x4_t vget_high_s16(int16x8_t a) { return int16x4_t{a[4], a[5], a[6], a[7]}; }
static inline int32x2_t vget_low_s32(int32x4_t a) { return int32x2_t{a[0], a[1]}; }
static inline int32x2_t vget_high_s32(int32x4_t a) { return int32x2_t{a[2], a[3]}; }
static inline int16x8_t vextq_s16(int16x8_t a, int16x8_t b, int n) {
	int16x8_t r;
	for (int i = 0; i < 8; i++) r[i] = i + n < 8 ? a[i + n] : b[i + n - 8];
	return r;
}
static inline int16x8_t vaddq_s16(int16x8_t a, int16x8_t b) {
	for (int i = 0; i < 8; i++) a[i] = (int16_t)(uint16_t)((uint16_t)a[i] + (uint16_t)b[i]);
	return a;
}
static inline int16x8_t vsubq_s16(int16x8_t a, int16x8_t b) {
	for (int i = 0; i < 8; i++) a[i] = (int16_t)(uint16_t)((uint16_t)a[i] - (uint16_t)b[i]);
	return a;
}
static inline int16x8_t vqdmulhq_n_s16(int16x8_t a, int16_t b) {
	for (int i = 0; i < 8; i++) {
		int32_t p = (2 * (int32_t)a[i] * b) >> 16;
		a[i] = p > 32767 ? 32767 : (int16_t)p;
	}
	return a;
}
static inline int32x4_t vmull_s16(int16x4_t a, int16x4_t b) {
	int32x4_t r;
	for (int i = 0; i < 4; i++) r[i] = (int32_t)a[i] * b[i];
	return r;
}
static inline int32x4_t vmlal_s16(int32x4_t acc, int16x4_t a, int16x4_t b) {
	for (int i = 0; i < 4; i++) acc[i] = (int32_t)((uint32_t)acc[i] + (uint32_t)((int32_t)a[i] * b[i]));
	return acc;
}
static inline int32x2_t vadd_s32(int32x2_t a, int32x2_t b) {
	for (int i = 0; i < 2; i++) a[i] = (int32_t)((uint32_t)a[i] + (uint32_t)b[i]);
	return a;
}
static inline int32_t vget_lane_s32(int32x2_t a, int i) { return a[i]; }
