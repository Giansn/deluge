// Host test of the DJ filter's knob (mastertune-v19.0, dsp/dj/dj_filter.h): one knob for the song's LPF and HPF cutoff.
//
// 1. The curve: from -64 (low-pass closed) through 0 (both off) to +64 (high-pass open), only one of the two on at a
//    time, each over its whole range; fromFilterKnobs() its inverse.
// 2. A detent on the curve: filterKnobs() of the position one further, at every position, both ways.
// 3. From any LPF and HPF knob (the two set apart, both on): a detent moves one of them by at most 2 knob steps, the
//    position shown by at most 1 and only the way turned; on the curve it stays there; 128 detents reach the end.
//
// Usage: dj_filter_test
#include "dsp/dj/dj_filter.h"
#include <cstdio>
#include <cstdlib>

using namespace deluge::dsp::dj;

static int32_t failures = 0;

static void check(bool ok, const char* what, int32_t a, int32_t b) {
	if (!ok && failures++ < 20) {
		printf("FAILED: %s (%d, %d)\n", what, a, b);
	}
}

static bool onCurve(int32_t lpf, int32_t hpf) {
	return lpf == 64 || hpf == -64;
}

static void testCurve() {
	for (int32_t dj = -64; dj <= 64; dj++) {
		int32_t lpf, hpf;
		filterKnobs(dj, &lpf, &hpf);
		check(lpf >= -64 && lpf <= 64 && hpf >= -64 && hpf <= 64, "knobs in range", lpf, hpf);
		check(dj >= 0 || hpf == -64, "left of the middle the high-pass off", dj, hpf);
		check(dj <= 0 || lpf == 64, "right of the middle the low-pass off", dj, lpf);
		check(fromFilterKnobs(lpf, hpf) == dj, "fromFilterKnobs the inverse", dj, fromFilterKnobs(lpf, hpf));
	}
	int32_t lpf[3], hpf[3];
	filterKnobs(-64, &lpf[0], &hpf[0]);
	filterKnobs(0, &lpf[1], &hpf[1]);
	filterKnobs(64, &lpf[2], &hpf[2]);
	check(lpf[0] == -64, "at -64 the low-pass closed", lpf[0], hpf[0]);
	check(lpf[1] == 64 && hpf[1] == -64, "at 0 both off", lpf[1], hpf[1]);
	check(hpf[2] == 64, "at 64 the high-pass open", lpf[2], hpf[2]);
	printf("curve (LPF, HPF knob): -64 (%d, %d), 0 (%d, %d), 64 (%d, %d)\n", lpf[0], hpf[0], lpf[1], hpf[1], lpf[2],
	       hpf[2]);
}

static void testStepOnCurve() {
	for (int32_t dj = -64; dj <= 64; dj++) {
		for (int32_t direction : {-1, 1}) {
			int32_t lpf, hpf, wantLpf, wantHpf;
			filterKnobs(dj, &lpf, &hpf);
			stepFilterKnobs(direction, &lpf, &hpf);
			filterKnobs(dj + direction, &wantLpf, &wantHpf);
			check(lpf == wantLpf && hpf == wantHpf, "a detent on the curve", dj, direction);
		}
	}
	printf("a detent on the curve: filterKnobs(position +- 1) at all 129 positions\n");
}

static void testStepSetApart() {
	int32_t cases = 0;
	for (int32_t lpf0 = -64; lpf0 <= 64; lpf0++) {
		for (int32_t hpf0 = -64; hpf0 <= 64; hpf0++) {
			for (int32_t direction : {-1, 1}) {
				int32_t lpf = lpf0, hpf = hpf0;
				stepFilterKnobs(direction, &lpf, &hpf);
				int32_t moved = std::abs(lpf - lpf0) + std::abs(hpf - hpf0);
				check(moved <= 2 && (lpf == lpf0 || hpf == hpf0), "one knob, at most 2 steps", lpf0, hpf0);
				int32_t shown = fromFilterKnobs(lpf, hpf) - fromFilterKnobs(lpf0, hpf0);
				check(shown * direction >= 0 && std::abs(shown) <= 1, "the position shown by at most 1, the way turned",
				      lpf0, hpf0);
				check(!onCurve(lpf0, hpf0) || onCurve(lpf, hpf), "on the curve it stays", lpf0, hpf0);
				for (int32_t i = 0; i < 127; i++) {
					stepFilterKnobs(direction, &lpf, &hpf);
				}
				check((direction > 0) ? (lpf == 64 && hpf == 64) : (lpf == -64 && hpf == -64), "128 detents: the end",
				      lpf0, hpf0);
				cases++;
			}
		}
	}
	printf("set apart: %d starts (every LPF and HPF knob, both ways), no jump\n", cases);
}

int main() {
	testCurve();
	testStepOnCurve();
	testStepSetApart();
	printf(failures ? "%d FAILURES\n" : "all checks passed\n", failures);
	return failures ? 1 : 0;
}
