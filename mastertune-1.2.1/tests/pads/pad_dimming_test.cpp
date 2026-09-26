// Host unit test of the firmware's hid/led/pad_dimming.h (flicker-free pad dimming).
// Build and run: g++ -std=c++23 -O1 -Wall -Wextra -I<firmware>/src/deluge pad_dimming_test.cpp -o /tmp/t && /tmp/t
// (run.sh does it with the pad-dim worktree.)
//
// Checks: the legacy timing is 1.2.1's table (the emulator test compares it with the 1.2.1 ELF's output too); the
// flicker-free timing keeps the scan period at 23 and the refresh time at 10 or more; the colour scale is 1 down to
// 48 % and then exactly 1.2.1's light over the PIC's (to 1/65536); scaleChannel() is the identity at full scale,
// monotonic, rounds to nearest, and keeps a lit channel lit. Prints, for the lowest levels, the light a full channel
// gets against 1.2.1 and the hue shifts of the firmware's palette colours (and the darkest ones) through the rounding.
#include "hid/led/pad_dimming.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iterator>

using namespace deluge::hid::pad_dimming;

static int failures = 0;
#define CHECK(cond, ...)                                                                                               \
	do {                                                                                                               \
		if (!(cond)) {                                                                                                 \
			std::printf("FAIL %s:%d: ", __FILE__, __LINE__);                                                           \
			std::printf(__VA_ARGS__);                                                                                  \
			std::printf("\n");                                                                                         \
			failures++;                                                                                                \
		}                                                                                                              \
	} while (0)

// 1.2.1's setDimmerInterval() output, as the 1.2.1 ELF sends it (tests/pads/pad_dim_emu.py prints it)
constexpr int kLegacy[26][2] = {{23, 0},  {22, 1},  {21, 2},  {20, 3},  {19, 4},  {18, 5},  {17, 6},
                                {16, 7},  {15, 8},  {14, 9},  {13, 10}, {12, 11}, {11, 12}, {10, 13},
                                {9, 14},  {8, 15},  {8, 19},  {8, 24},  {8, 30},  {8, 37},  {8, 46},
                                {8, 61},  {8, 74},  {8, 91},  {8, 112}, {8, 148}};

static_assert(colourScale(0) == kFullScale && colourScale(13) == kFullScale && colourScale(14) < kFullScale);
static_assert(scaleChannel(255, kFullScale) == 255 && scaleChannel(1, 1) == 1 && scaleChannel(0, 1) == 0);

static double hue(double r, double g, double b) {
	double mx = std::fmax(r, std::fmax(g, b)), mn = std::fmin(r, std::fmin(g, b));
	if (mx == mn) {
		return -1; // grey
	}
	double h = mx == r ? (g - b) / (mx - mn) : mx == g ? 2 + (b - r) / (mx - mn) : 4 + (r - g) / (mx - mn);
	return std::fmod(h * 60 + 360, 360);
}

int main() {
	for (int i = 0; i <= kMaxDimmerInterval; i++) {
		PicTiming legacy = legacyTiming(i);
		CHECK(legacy.refreshTime == kLegacy[i][0] && legacy.dimmerInterval == kLegacy[i][1], "legacy %d: %d %d", i,
		      (int)legacy.refreshTime, (int)legacy.dimmerInterval);
		PicTiming pic = flickerFreeTiming(i);
		CHECK(pic.refreshTime + pic.dimmerInterval == kScanPeriod && pic.refreshTime >= kMinRefreshTime,
		      "flicker-free %d: %d %d", i, (int)pic.refreshTime, (int)pic.dimmerInterval);
		double wanted = (double)legacy.refreshTime / (legacy.refreshTime + legacy.dimmerInterval)
		                / ((double)pic.refreshTime / kScanPeriod);
		uint32_t scale = colourScale(i);
		CHECK(std::fabs(scale - wanted * kFullScale) <= 0.5, "scale %d: %u, wanted %f", i, scale, wanted * kFullScale);
		CHECK(i <= 13 ? scale == kFullScale : scale < colourScale(i - 1), "scale %d: %u not decreasing", i, scale);
		for (int v = 0; v < 256; v++) {
			int q = scaleChannel(v, scale);
			double exact = v * (double)scale / kFullScale;
			CHECK(q >= 0 && q <= v, "channel %d at %d: %d", v, i, q);
			CHECK(v == 0 || q >= 1, "channel %d at %d went dark", v, i);
			CHECK(exact < 0.5 || std::fabs(q - exact) <= 0.5 + 1e-9, "channel %d at %d: %d, exact %f", v, i, q, exact);
			CHECK(v == 0 || q >= scaleChannel(v - 1, scale), "channel %d at %d not monotonic", v, i);
		}
	}
	for (int v = 0; v < 256; v++) {
		CHECK(scaleChannel(v, kFullScale) == v, "identity %d", v);
	}

	// The light at the lowest levels: a full channel's value and light (PIC x value) against 1.2.1's
	std::printf("interval  UI   scale  255 ->  light / 1.2.1 | darkest lit value 1 = 1.2.1's value\n");
	for (int i = 13; i <= kMaxDimmerInterval; i++) {
		PicTiming legacy = legacyTiming(i), pic = flickerFreeTiming(i);
		double lOld = (double)legacy.refreshTime / (legacy.refreshTime + legacy.dimmerInterval);
		double lPic = (double)pic.refreshTime / kScanPeriod;
		uint32_t scale = colourScale(i);
		std::printf("%8d %3d%%  %.3f  %3d  %.4f %.3f | %.1f\n", i, (25 - i) * 4, scale / 65536.0,
		            scaleChannel(255, scale), lPic * scaleChannel(255, scale) / 255, lPic * scaleChannel(255, scale) / 255 / lOld,
		            lPic / lOld);
	}

	// Hue shifts through the rounding (and the lit-stays-lit rule) at 20 %, 4 % and 0 %
	constexpr int kColours[][3] = {
	    {255, 0, 0},   {255, 128, 0}, {255, 255, 0},  {0, 255, 6},   {0, 0, 255},   {128, 0, 255}, {255, 48, 0},
	    {255, 44, 50}, {60, 15, 15},  {60, 15, 60},   {30, 30, 10},  {54, 29, 3},   {46, 16, 2},   {37, 15, 37},
	    {221, 72, 13}, {85, 182, 72}, {51, 109, 145}, {130, 120, 130}, {0, 128, 128}, {10, 5, 2}, {4, 2, 1},
	};
	const char* names[] = {"red",        "orange",       "yellow",      "enabled",     "blue",       "purple",
	                       "amber",      "pink",         "red_dull",    "magenta_dull", "selected_drum", "tail orange",
	                       "pastel orangeTail", "pastel pinkTail", "pastel orange", "pastel green", "pastel blue",
	                       "flash",      "cyan",         "(10,5,2)",    "(4,2,1)"};
	for (int i : {20, 24, 25}) {
		uint32_t scale = colourScale(i);
		std::printf("interval %d (%d %%), scale %.3f: colour -> sent, hue shift (degrees)\n", i, (25 - i) * 4,
		            scale / 65536.0);
		double worstPalette = 0;
		for (size_t c = 0; c < std::size(kColours); c++) {
			int r = kColours[c][0], g = kColours[c][1], b = kColours[c][2];
			int qr = scaleChannel(r, scale), qg = scaleChannel(g, scale), qb = scaleChannel(b, scale);
			double h0 = hue(r, g, b), h1 = hue(qr, qg, qb);
			double d = (h0 < 0 || h1 < 0) ? (h0 == h1 ? 0 : 999) : std::fabs(std::remainder(h1 - h0, 360.0));
			if (c < 17) {
				worstPalette = std::fmax(worstPalette, d);
			}
			std::printf("  %-18s (%3d,%3d,%3d) -> (%2d,%2d,%2d) %5.1f\n", names[c], r, g, b, qr, qg, qb, d);
		}
		std::printf("  worst palette/tail hue shift %.1f degrees\n", worstPalette);
	}

	if (failures) {
		std::printf("%d FAILED\n", failures);
		return 1;
	}
	std::printf("all checks passed\n");
	return 0;
}
