// A kit's or audio track's filters after silence (mastertune v18.1, FilterSet::restartAfterSilence()): where the
// effects gave out silence and skip their blocks (GlobalEffectableForClip::renderOutput(): the early return, which
// doesn't set the filters up, or the check after rendering, which has set them up for the block), the next sound must
// render as through filters that had been set to the new settings all along. Each case settles a FilterSet on silence
// at the old settings, skips blocks as the firmware does while changing the settings, restarts it, then renders a
// 110 / 330 / 550 Hz tone at the new settings; the reference settles on silence at the new settings and renders the
// same tone. Fails unless the two are the same bit for bit. Printed beside it: the difference without the restart
// (v18: the coefficients ramping from the old settings, a crossfade or a switch-on fade-in frozen in the silence).
//   ramp       LP24 + HP ladder, cutoffs and resonances changed while skipped by the early return (no setup)
//   crossfade  LP24 -> SVF band while silent, set up by the check after rendering: the mode change's crossfade
//   fadein     the LPF switched on (off -> LP24) while silent, set up by the check after rendering: its fade-in
#include "dsp/filter/filter_set.h"
#include "util/functions.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

namespace AudioEngine {
int32_t cpuDireness = 0;
bool renderInStereo = true;
} // namespace AudioEngine

using namespace deluge::dsp::filter;

namespace {
constexpr int kBlock = 128;
constexpr int32_t kGainIn = 167763968; // a kit's / audio track's (setupFilterSetConfig())

// Params from a knob position, the display's 0 to 50 (as filter_neutral_test.cpp)
int32_t knobFromDisplay(double display) {
	double pos = display * 128.0 / 50 - 64;
	return (int32_t)std::clamp(pos * 33554432.0, -2147483648.0, 2147483647.0);
}
int32_t freqParam(double display) {
	return getExp(2000000, knobFromDisplay(display) >> 2);
}
int32_t linearParam(double display) {
	return lshiftAndSaturate<3>(multiply_32x32_rshift32((knobFromDisplay(display) >> 2) + 536870912, 25 * 10737418));
}

struct Settings {
	FilterMode lpfMode = FilterMode::OFF, hpfMode = FilterMode::OFF;
	double cut = 25, res = 0, hcut = 10, hres = 0;
};

void configure(FilterSet& fs, const Settings& s) {
	fs.setConfig(freqParam(s.cut), linearParam(s.res), s.lpfMode, 0, freqParam(s.hcut), linearParam(s.hres), s.hpfMode,
	             0, kGainIn, FilterRoute::HIGH_TO_LOW, false, nullptr);
}
// One block set up and rendered as a kit's filters are (stereo, the gain compensation ramped)
void render(FilterSet& fs, const Settings& s, int32_t* buf) {
	configure(fs, s);
	fs.renderLongStereo(buf, buf + 2 * kBlock, HpLadderFilter::kSaturationGlobal, LpLadderFilter::kSaturationGlobal,
	                    true);
}
// blocks of silence at s
void settle(FilterSet& fs, const Settings& s, int blocks) {
	std::vector<int32_t> buf(2 * kBlock);
	for (int b = 0; b < blocks; b++) {
		std::fill(buf.begin(), buf.end(), 0);
		render(fs, s, buf.data());
	}
}
// 8 blocks of the tone at s, appended to out
void tone(FilterSet& fs, const Settings& s, std::vector<int32_t>& out) {
	std::vector<int32_t> buf(2 * kBlock);
	for (int b = 0; b < 8; b++) {
		for (int i = 0; i < kBlock; i++) {
			int t = b * kBlock + i;
			double x = 0;
			for (double f : {110.0, 330.0, 550.0}) {
				x += 0.08 * std::sin(2 * M_PI * f * t / 44100.0);
			}
			buf[2 * i] = buf[2 * i + 1] = (int32_t)std::lround(x * 2147483648.0);
		}
		render(fs, s, buf.data());
		out.insert(out.end(), buf.begin(), buf.end());
	}
}

// setUpWhileSilent: the check after rendering (setConfig() every skipped block), else the early return (none)
int runCase(const char* name, const Settings& before, const Settings& after, bool setUpWhileSilent) {
	std::vector<int32_t> ref, with, without;
	{
		FilterSet fs;
		settle(fs, after, 64);
		tone(fs, after, ref);
	}
	for (int restart = 0; restart < 2; restart++) {
		FilterSet fs;
		settle(fs, before, 64);
		for (int b = 0; b < 16; b++) { // the skipped blocks, 46 ms
			if (setUpWhileSilent) {
				configure(fs, after);
			}
			if (restart) {
				fs.restartAfterSilence();
			}
		}
		tone(fs, after, restart ? with : without);
	}
	auto maxDiff = [&](const std::vector<int32_t>& v) {
		double m = 0;
		for (size_t i = 0; i < v.size(); i++) {
			m = std::max(m, std::fabs((double)v[i] - ref[i]));
		}
		return m;
	};
	double dWith = maxDiff(with), dWithout = maxDiff(without);
	bool ok = dWith == 0;
	printf("%s %-9s restarted: %s; without the restart the largest difference %.1f dBFS\n", ok ? "ok  " : "FAIL", name,
	       ok ? "the same as filters set to the new settings all along, bit for bit" : "DIFFERS",
	       dWithout > 0 ? 20 * std::log10(dWithout / 2147483648.0) : -999.0);
	return ok ? 0 : 1;
}
} // namespace

int main() {
	int bad = 0;
	Settings a, b;
	a.lpfMode = b.lpfMode = FilterMode::TRANSISTOR_24DB;
	a.hpfMode = b.hpfMode = FilterMode::HPLADDER;
	a.cut = 45, a.res = 10, a.hcut = 5, a.hres = 5;
	b.cut = 22, b.res = 30, b.hcut = 12, b.hres = 20;
	bad += runCase("ramp", a, b, false);

	Settings c = a, d = a;
	d.lpfMode = FilterMode::SVF_BAND;
	d.cut = 25, d.res = 25;
	bad += runCase("crossfade", c, d, true);

	Settings e, f;
	e.hpfMode = f.hpfMode = FilterMode::HPLADDER;
	e.hcut = f.hcut = 8;
	f.lpfMode = FilterMode::TRANSISTOR_24DB;
	f.cut = 20, f.res = 20;
	bad += runCase("fadein", e, f, true);

	printf(bad ? "silence restart: %d FAILED\n" : "silence restart: all ok\n", bad);
	return bad ? 1 : 0;
}
