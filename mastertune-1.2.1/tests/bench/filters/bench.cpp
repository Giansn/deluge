// Filter benchmark: the firmware's FilterSet (dsp/filter: lpladder, hpladder, svf, filter_set) as a voice runs it,
// setConfig then renderLong (mono, 128 samples) or renderLongStereo (128 interleaved frames) per block.
// In the emulator (ARM=1) it counts instructions per 128-sample block; on the PC it prints a hash of the output per
// case, so an optimisation can be checked for bit-exactness (same hash before and after). Randomized cases on top
// (2048, see randomCases) cover far more of the parameter space, block sizes and mode changes; compare hashes ARM
// against ARM (ARM=1) or PC against PC, as the PC fallbacks of the *_rounded helpers don't round.
#include "dsp/filter/filter_set.h"
#include "dsp/filter/lpladder.h"
#include "emu_count.h"
#include "util/functions.h"
#include <algorithm>
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
constexpr int32_t kMorph = 1 << 28; // morph full scale as the voice passes it (Filter::configure shifts it left by 2)

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
    {"SVF BPF res30", FilterMode::SVF_BAND, 1200, kRes / 10 * 3, kMorph - 16, OFF, 0, 0, 0},
    {"SVF notch res30", FilterMode::SVF_NOTCH, 1200, kRes / 10 * 3, 0, OFF, 0, 0, 0},
    {"HP ladder res0", OFF, 0, 0, 0, FilterMode::HPLADDER, 150, 0, 0},
    {"HP ladder res20", OFF, 0, 0, 0, FilterMode::HPLADDER, 150, kRes / 5, 0},
    // below ~39 % resonance the HP ladder does not saturate, above ~50 % it uses the antialiased 2D tanh
    {"HP ladder res60", OFF, 0, 0, 0, FilterMode::HPLADDER, 150, kRes / 10 * 6, 0},
    {"HP ladder res20 morph50", OFF, 0, 0, 0, FilterMode::HPLADDER, 150, kRes / 5, kMorph / 2},
    {"LP24+HP ladder res30/20", FilterMode::TRANSISTOR_24DB, 1200, kRes / 10 * 3, 0, FilterMode::HPLADDER, 150,
     kRes / 5, 0},
    {"LP24+HP parallel", FilterMode::TRANSISTOR_24DB, 1200, kRes / 10 * 3, 0, FilterMode::HPLADDER, 150, kRes / 5, 0,
     FilterRoute::PARALLEL},
    {"SVF LPF+SVF HPF res30/20", FilterMode::SVF_BAND, 1200, kRes / 10 * 3, 0, FilterMode::SVF_BAND, 150, kRes / 5,
     0},
};

static char labels[2 * std::size(cases) + 2][64];

// Randomized cases, for the bit-exactness check: random modes (switching between blocks too, OFF included, so the
// resets and the dry/wet fades run), routing, parameters (changing every block), cpuDireness (the drive ladder's
// oversampling), input (noise, saw, silence, some at full scale) and block sizes of 1-128 frames, odd ones included,
// mono (sometimes with a sampleIncrement of 2 or 3, which no caller uses) and stereo. The hash covers the output, jcong
// and the FilterSet's bytes (the filters' state) after every block.
constexpr int kRandomCases = 2048;
constexpr int kRandomBlocks = 64;
constexpr int kRandomGroup = 256; // cases per printed hash

struct Rng {
	uint64_t s;
	uint32_t next() { // splitmix64
		uint64_t z = (s += 0x9e3779b97f4a7c15ull);
		z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
		z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
		return (uint32_t)((z ^ (z >> 31)) >> 32);
	}
	int32_t range(int32_t lo, int32_t hi) { return lo + (int32_t)(next() % (uint32_t)(hi - lo + 1)); } // [lo, hi]
	bool chance(int percent) { return (int)(next() % 100) < percent; }
};

struct RandomSetting {
	FilterMode lpf, hpf;
	FilterRoute route;
	int32_t lpfFreq, lpfRes, lpfMorph, hpfFreq, hpfRes, hpfMorph, gain;
};

static bool isSvf(FilterMode m) {
	return m == FilterMode::SVF_BAND || m == FilterMode::SVF_NOTCH;
}

static void randomModes(Rng& rng, RandomSetting& p) {
	static const FilterMode lpfModes[] = {
	    FilterMode::TRANSISTOR_12DB, FilterMode::TRANSISTOR_24DB, FilterMode::TRANSISTOR_24DB_DRIVE,
	    FilterMode::SVF_BAND,        FilterMode::SVF_NOTCH,       FilterMode::OFF};
	static const FilterMode hpfModes[] = {FilterMode::HPLADDER, FilterMode::SVF_BAND, FilterMode::SVF_NOTCH,
	                                      FilterMode::OFF};
	p.lpf = lpfModes[rng.next() % std::size(lpfModes)];
	p.hpf = hpfModes[rng.next() % std::size(hpfModes)];
	p.route = (FilterRoute)(rng.next() % 3);
}

// Parameters in the ranges the voice passes (cutoff > 0, resonance 0 to full scale, morph up to full scale, negative
// too for the ladders), often exactly 0 or full scale, where the loops take other branches
static int32_t randomFreq(Rng& rng) {
	return std::max((int32_t)(rng.next() >> rng.range(1, 8)), (int32_t)65536);
}
static int32_t randomRes(Rng& rng) {
	int k = rng.range(0, 9);
	return k == 0 ? 0 : k == 1 ? kRes : rng.range(0, kRes);
}
static int32_t randomMorph(Rng& rng, FilterMode m) {
	if (rng.chance(40)) {
		return 0;
	}
	int32_t full = (1 << 29) - 1;
	return isSvf(m) ? rng.range(0, full) : rng.range(-full, full);
}

static void randomParams(Rng& rng, RandomSetting& p) {
	p.lpfFreq = randomFreq(rng);
	p.lpfRes = randomRes(rng);
	p.lpfMorph = randomMorph(rng, p.lpf);
	p.hpfFreq = randomFreq(rng);
	p.hpfRes = randomRes(rng);
	p.hpfMorph = randomMorph(rng, p.hpf);
	p.gain = rng.range(0, 1 << 30);
}

// Parameters drift a little from block to block, as they do when modulated, and sometimes jump
static void driftParam(Rng& rng, int32_t& v, int32_t lo, int32_t hi) {
	int64_t n = (int64_t)v + rng.range(-(1 << 22), 1 << 22);
	v = (int32_t)std::clamp(n, (int64_t)lo, (int64_t)hi);
}

static void randomInput(Rng& rng, int32_t* buf, int n, uint32_t* phase, uint32_t increment, int shift, int kind) {
	for (int i = 0; i < n; i++) {
		int32_t v;
		switch (kind) {
		case 0: // noise
			v = (int32_t)rng.next() >> shift;
			break;
		case 1: // saw
			*phase += increment;
			v = (int32_t)*phase >> shift;
			break;
		case 2: // saw plus noise
			*phase += increment;
			v = ((int32_t)*phase >> (shift + 1)) + ((int32_t)rng.next() >> (shift + 1));
			break;
		default: // silence
			v = 0;
		}
		buf[i] = v;
	}
}

static uint64_t hashWords(uint64_t hash, const void* data, size_t bytes) {
	const unsigned char* b = (const unsigned char*)data;
	for (size_t i = 0; i < bytes; i++) {
		hash = (hash ^ b[i]) * 1099511628211ull;
	}
	return hash;
}

static void randomCases() {
	uint64_t total = 1469598103934665603ull;
	uint64_t group = total;
	static FilterSet fs; // zeroed below, so the state is defined from the start
	for (int n = 0; n < kRandomCases; n++) {
		Rng rng{0x5eed0000ull + (uint64_t)n};
		std::memset((void*)&fs, 0, sizeof(fs));
		jcong = 380116160 + (uint32_t)n;
		bool stereo = rng.chance(40);
		int channels = stereo ? 2 : 1;
		RandomSetting p;
		randomModes(rng, p);
		randomParams(rng, p);
		uint32_t phase = 0;
		uint32_t increment = rng.range(1 << 20, 1 << 26);
		int shift = rng.chance(5) ? 0 : rng.range(1, 8);
		int kind = rng.range(0, 2);
		for (int b = 0; b < kRandomBlocks; b++) {
			if (rng.chance(12)) {
				randomModes(rng, p);
				randomParams(rng, p);
			}
			else if (rng.chance(15)) {
				randomParams(rng, p);
			}
			else {
				driftParam(rng, p.lpfFreq, 65536, INT32_MAX);
				driftParam(rng, p.lpfRes, 0, kRes);
				driftParam(rng, p.hpfFreq, 65536, INT32_MAX);
				driftParam(rng, p.hpfRes, 0, kRes);
			}
			if (rng.chance(10)) {
				kind = rng.range(0, 3);
				shift = rng.chance(5) ? 0 : rng.range(1, 8);
			}
			AudioEngine::cpuDireness = rng.range(0, 20);
			int frames = rng.chance(25) ? rng.range(1, 8) : rng.range(1, kBlock);
			int32_t buf[kBlock * 2];
			randomInput(rng, buf, frames * channels, &phase, increment, shift, kind);
			fs.setConfig(p.lpfFreq, p.lpfRes, p.lpf, p.lpfMorph, p.hpfFreq, p.hpfRes, p.hpf, p.hpfMorph, p.gain,
			             p.route, false, nullptr);
			int sampleIncrement = (!stereo && rng.chance(10)) ? rng.range(2, 3) : 1;
			// (not counted: the emulator counts through a hook on every instruction, which is slow)
			if (stereo) {
				fs.renderLongStereo(buf, buf + 2 * frames);
			}
			else {
				fs.renderLong(buf, buf + frames, frames, sampleIncrement);
			}
			group = hashWords(group, buf, frames * channels * sizeof(int32_t));
			group = hashWords(group, &jcong, sizeof(jcong));
			group = hashWords(group, &fs, sizeof(fs));
		}
		if ((n + 1) % kRandomGroup == 0) {
			char label[64];
			snprintf(label, sizeof(label), "random cases %03d-%03d", n + 1 - kRandomGroup, n);
			printf("%-45s hash %016llx\n", label, (unsigned long long)group);
			total = hashWords(total, &group, sizeof(group));
			group = 1469598103934665603ull;
		}
	}
	printf("%-45s hash %016llx\n", "random cases, all", (unsigned long long)total);
}

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
	randomCases();
	// setConfig alone (runs once per voice per block too; the double divisions are one slow instruction each), for a ladder pair and an SVF pair
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
