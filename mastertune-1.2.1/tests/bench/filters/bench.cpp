// Filter benchmark: the firmware's FilterSet (dsp/filter: lpladder, hpladder, svf, filter_set) as a voice runs it,
// setConfig then renderLong (mono, 128 samples) or renderLongStereo (128 interleaved frames) per block.
// In the emulator (ARM=1) it counts instructions per 128-sample block; on the PC it prints a hash of the output per
// case, so an optimisation can be checked for bit-exactness (same hash before and after).
#include "dsp/filter/filter_set.h"
#include "dsp/filter/lpladder.h"
#include "emu_count.h"
#include "util/functions.h"
#include <cmath>
#include <cstdio>
#include <cstring>

namespace AudioEngine {
int32_t cpuDireness = 0; // idle engine: the drive ladder may oversample at high cutoff and resonance
bool renderInStereo = true;
} // namespace AudioEngine

using namespace deluge::dsp::filter;

constexpr int kBlock = 128;
constexpr int kWarm = 10;  // blocks before counting (the fade-in after reset takes ~690 samples)
constexpr int kCount = 10; // blocks counted
constexpr double kFs = 44100;

// Cutoff parameter (as paramFinalValues[LOCAL_*_FREQ]) for a cutoff in Hz, found through the firmware's own
// curveFrequency: fc = t/(1+t) in Q31, t = tan(pi f / fs)
static int32_t freqFor(double hz) {
	double target = std::tan(M_PI * hz / kFs);
	target = target / (1 + target) * 2147483648.0;
	int32_t lo = 0, hi = 1 << 26;
	while (hi - lo > 1) {
		int32_t mid = lo + (hi - lo) / 2;
		SVFilter f;
		f.curveFrequency(mid);
		(f.fc < target ? lo : hi) = mid;
	}
	return hi;
}

constexpr int32_t kRes = 536870911; // resonance full scale (paramFinalValues[LOCAL_LPF_RESONANCE])
constexpr int32_t kMorph = 1 << 28; // morph / drive full scale (q28; configure shifts it to q31... well, << 2)

struct Case {
	const char* name;
	FilterMode lpf;
	double lpfHz;
	int32_t lpfRes, lpfMorph;
	FilterMode hpf;
	double hpfHz;
	int32_t hpfRes, hpfMorph;
	FilterRoute route = FilterRoute::HIGH_TO_LOW;
};

constexpr auto OFF = FilterMode::OFF;
static const Case cases[] = {
    {"LP12 ladder res30", FilterMode::TRANSISTOR_12DB, 1200, kRes / 10 * 3, 0, OFF, 0, 0, 0},
    {"LP24 ladder res30", FilterMode::TRANSISTOR_24DB, 1200, kRes / 10 * 3, 0, OFF, 0, 0, 0},
    {"LP24 ladder res0", FilterMode::TRANSISTOR_24DB, 1200, 0, 0, OFF, 0, 0, 0},
    {"LP24 ladder res30 morph50", FilterMode::TRANSISTOR_24DB, 1200, kRes / 10 * 3, kMorph / 2, OFF, 0, 0, 0},
    {"LP24 drive res30", FilterMode::TRANSISTOR_24DB_DRIVE, 1200, kRes / 10 * 3, 0, OFF, 0, 0, 0},
    {"LP24 drive res60 9k (oversampled)", FilterMode::TRANSISTOR_24DB_DRIVE, 9000, kRes / 10 * 6, 0, OFF, 0, 0, 0},
    {"SVF LPF res30", FilterMode::SVF_BAND, 1200, kRes / 10 * 3, 0, OFF, 0, 0, 0},
    {"SVF BPF res30", FilterMode::SVF_BAND, 1200, kRes / 10 * 3, kMorph / 2 - 16, OFF, 0, 0, 0},
    {"HP ladder res0", OFF, 0, 0, 0, FilterMode::HPLADDER, 150, 0, 0},
    {"HP ladder res20", OFF, 0, 0, 0, FilterMode::HPLADDER, 150, kRes / 5, 0},
    {"HP ladder res20 morph50", OFF, 0, 0, 0, FilterMode::HPLADDER, 150, kRes / 5, kMorph / 2},
    {"LP24+HP ladder res30/20", FilterMode::TRANSISTOR_24DB, 1200, kRes / 10 * 3, 0, FilterMode::HPLADDER, 150,
     kRes / 5, 0},
    {"LP24+HP parallel", FilterMode::TRANSISTOR_24DB, 1200, kRes / 10 * 3, 0, FilterMode::HPLADDER, 150, kRes / 5, 0,
     FilterRoute::PARALLEL},
    {"SVF LPF+SVF HPF res30/20", FilterMode::SVF_BAND, 1200, kRes / 10 * 3, 0, FilterMode::SVF_BAND, 150, kRes / 5,
     0},
};

static char labels[2 * std::size(cases) + 2][64];

// A saw (110 Hz, slightly detuned on the right) at roughly a voice's level before the filter
static void input(int32_t* buf, int frames, int channels, uint32_t* phase) {
	for (int i = 0; i < frames; i++) {
		for (int c = 0; c < channels; c++) {
			phase[c] += (uint32_t)((110.0 + c * 0.7) / kFs * 4294967296.0);
			buf[i * channels + c] = (int32_t)phase[c] >> 4;
		}
	}
}

int main() {
	int n = 0;
	for (int stereo = 0; stereo < 2; stereo++) {
		for (const Case& c : cases) {
			char* label = labels[n++];
			snprintf(label, sizeof(labels[0]), "%s %s", stereo ? "stereo" : "mono  ", c.name);
			jcong = 380116160; // the firmware's noise, from its start value
			FilterSet fs;
			fs.reset();
			int32_t lpfFreq = c.lpf == OFF ? 0 : freqFor(c.lpfHz);
			int32_t hpfFreq = c.hpf == OFF ? 0 : freqFor(c.hpfHz);
			int32_t buf[kBlock * 2];
			uint32_t phase[2] = {0, 0};
			uint64_t hash = 1469598103934665603ull;
			int channels = stereo ? 2 : 1;
			for (int b = 0; b < kWarm + kCount; b++) {
				input(buf, kBlock, channels, phase);
				bool counting = b >= kWarm;
				// As the voice does it every block (voice.cpp), with filterGain = volumeNeutralValueForUnison << 1
				fs.setConfig(lpfFreq, c.lpfRes, c.lpf, c.lpfMorph, hpfFreq, c.hpfRes, c.hpf, c.hpfMorph, 1 << 28,
				             c.route, false, nullptr);
				if (counting) {
					EMU_COUNT_BEGIN(label);
				}
				if (stereo) {
					fs.renderLongStereo(buf, buf + 2 * kBlock);
				}
				else {
					fs.renderLong(buf, buf + kBlock, kBlock);
				}
				if (counting) {
					EMU_COUNT_END();
				}
				for (int i = 0; i < kBlock * channels; i++) {
					hash = (hash ^ (uint32_t)buf[i]) * 1099511628211ull;
				}
			}
			printf("%-45s hash %016llx\n", label, (unsigned long long)hash);
		}
	}
	// setConfig alone (runs once per voice per block too), for a ladder pair and an SVF pair
	{
		FilterSet fs;
		fs.reset();
		int32_t lf = freqFor(1200), hf = freqFor(150);
		for (int b = 0; b < kWarm + kCount; b++) {
			if (b >= kWarm) {
				EMU_COUNT_BEGIN("setConfig LP24+HP ladder");
			}
			fs.setConfig(lf + b, kRes / 10 * 3, FilterMode::TRANSISTOR_24DB, 0, hf + b, kRes / 5, FilterMode::HPLADDER, 0,
			             1 << 28, FilterRoute::HIGH_TO_LOW, false, nullptr);
			if (b >= kWarm) {
				EMU_COUNT_END();
			}
		}
		for (int b = 0; b < kWarm + kCount; b++) {
			if (b >= kWarm) {
				EMU_COUNT_BEGIN("setConfig SVF LPF+HPF");
			}
			fs.setConfig(lf + b, kRes / 10 * 3, FilterMode::SVF_BAND, 0, hf + b, kRes / 5, FilterMode::SVF_BAND, 0,
			             1 << 28, FilterRoute::HIGH_TO_LOW, false, nullptr);
			if (b >= kWarm) {
				EMU_COUNT_END();
			}
		}
	}
	return 0;
}
