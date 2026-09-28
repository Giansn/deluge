// The volume knobs' 0.5 dB detents and their display (mastertune v18, modulation/params/volume_steps.cpp), on the PC.
// Checks: every grid point from -60 dB to the top round-trips (stored -> dB within 0.001 dB), one detent moves exactly
// one grid step, a value between the steps is shown to 0.1 dB and the first detent snaps to the grid in the direction
// turned, below -60 dB comes off (and off turned up gives -60.0), the top is the stored maximum, and the stored values
// of v17's menu steps 0..50 are shown (the table: what an old song's volume reads as).
#include "modulation/params/volume_steps.h"
#include <cmath>
#include <cstdio>
#include <cstring>

using namespace deluge::modulation::params::volume;

static int fails = 0;
static void check(bool ok, const char* what) {
	if (!ok) {
		printf("FAIL: %s\n", what);
		fails++;
	}
}

int main() {
	for (Scale s : {Scale::SOUND, Scale::GLOBAL}) {
		const char* name = s == Scale::SOUND ? "sound / kit row (0 dB at 25)" : "kit / audio track / song (0 dB at 35.4)";
		double top = storedToDb(INT32_MAX, s);
		int32_t topGrid = (int32_t)std::floor(top * 2 + 1e-9);
		printf("== %s: top %.3f dB\n", name, top);
		// the grid round-trips and steps by one
		for (int32_t k = kFloorGrid; k <= topGrid; k++) {
			int32_t v = dbToStored(k / 2.0, s);
			check(std::fabs(storedToDb(v, s) - k / 2.0) < 0.001, "grid point round trip");
			int32_t up = step(v, 1, s);
			int32_t down = step(v, -1, s);
			if (k < topGrid) {
				check(std::fabs(storedToDb(up, s) - (k + 1) / 2.0) < 0.001, "one detent up = +0.5 dB");
			}
			else {
				check(up == INT32_MAX, "the top detent up = the stored maximum");
			}
			if (k > kFloorGrid) {
				check(std::fabs(storedToDb(down, s) - (k - 1) / 2.0) < 0.001, "one detent down = -0.5 dB");
			}
			else {
				check(down == kOff, "below -60 dB = off");
			}
		}
		check(std::fabs(storedToDb(step(kOff, 1, s), s) + 60.0) < 0.001, "off turned up = -60.0 dB");
		check(step(kOff, -1, s) == kOff, "off turned down stays off");
		check(std::fabs(storedToDb(step(INT32_MAX, -1, s), s) - topGrid / 2.0) < 0.001, "the maximum turned down");
		// from off to the top and back: detents
		int32_t v = kOff, n = 0;
		while (v != INT32_MAX && n < 1000) {
			v = step(v, 1, s);
			n++;
		}
		printf("detents from off to the top: %d (off, -60.0 .. %+.1f dB in 0.5 dB, the maximum)\n", n, topGrid / 2.0);
		// v17's menu steps: shown to 0.1 dB, snapping on the first detent
		printf("v17 menu value -> shown | first detent up / down\n");
		for (int32_t m = 0; m <= 50; m++) {
			int32_t stored = m == 50 ? INT32_MAX : (int32_t)(((int64_t)m - 25) * 2147483648LL / 25);
			char t[12], up[12], dn[12], seg[12];
			toText(stored, s, t, true);
			toText(stored, s, seg, false);
			toText(step(stored, 1, s), s, up, true);
			toText(step(stored, -1, s), s, dn, true);
			double db = storedToDb(stored, s);
			double h = db * 2;
			bool onGrid = std::isfinite(h) && std::fabs(h - std::round(h)) < 0.02;
			if (m % 5 == 0 || m < 3 || m > 48) {
				printf("  %2d -> %-9s (7-seg %-5s) | %-9s / %s\n", m, t, seg, up, dn);
			}
			if (std::isfinite(db) && db > -60 && !onGrid && m < 50) {
				check(std::fabs(storedToDb(step(stored, 1, s), s) - std::ceil(h) / 2.0) < 0.001, "snap up");
				check(std::fabs(storedToDb(step(stored, -1, s), s) - std::floor(h) / 2.0) < 0.001, "snap down");
			}
		}
	}
	// the texts
	char t[12];
	toText(dbToStored(-3.5, Scale::SOUND), Scale::SOUND, t, true);
	check(!strcmp(t, "-3.5 dB"), "text -3.5 dB");
	toText(dbToStored(12.0, Scale::SOUND), Scale::SOUND, t, true);
	check(!strcmp(t, "+12.0 dB"), "text +12.0 dB");
	toText(dbToStored(0.0, Scale::SOUND), Scale::SOUND, t, true);
	check(!strcmp(t, "0.0 dB"), "text 0.0 dB");
	toText(kOff, Scale::SOUND, t, true);
	check(!strcmp(t, "-inf dB"), "text -inf dB");
	toText(kOff, Scale::SOUND, t, false);
	check(!strcmp(t, "OFF"), "7-seg OFF");
	toText(dbToStored(-12.5, Scale::GLOBAL), Scale::GLOBAL, t, false);
	check(!strcmp(t, "-12.5"), "7-seg -12.5");
	printf("%s\n", fails ? "FAIL" : "ok");
	return fails != 0;
}
