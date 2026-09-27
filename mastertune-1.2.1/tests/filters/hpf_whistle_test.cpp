// The song's, a kit's and an audio track's filters (GlobalEffectable::setupFilterSetConfig() + processFilters(): the
// FilterSet in stereo, set up every block from the unpatched params) with the HPF's resonance up and the LPF turned:
// how loud does the HPF whistle on its own? Every LPF mode x HPF mode x route, the HPF at 5 positions and 2 resonances,
// the LPF's resonance 0 and 50 %: 1 s of music at the song master's level (a chord of saws and some noise, -47 dBFS rms
// as the song in tests/song has it there) while the LPF is turned from open (50) down to 10 and up to 45, then 1.5 s of
// silence with the LPF still on. A filter that doesn't self-oscillate dies away in the silence; one that does keeps a
// steady tone (the last 0.5 s above -90 dBFS and no more than 1 dB below the 0.5 s before). The HP ladder from about
// 67 % resonance and the SVF from about 94 % keep a sine going at the HPF's resonance frequency (e.g. 2.2 kHz with the
// HPF at 30), as they did in the original firmware. Since community #336 the HP ladder's tanh limits 2 bits higher than
// there, so in 1.2.1 to v16 that sine is up to 14.5 dB louder than the music. The fix (mastertune-v16 filter-fix,
// HpLadderFilter::kSaturationGlobal) brings it back to the original firmware's level.
//
// Fails where the HPF's own tone is more than kMaxWhistle dB louder than the music (with the output gain the firmware
// applies, GlobalEffectable's masterVolumeAdjustment), as filter_tone_test.cpp. A tone that the same case keeps with
// the HPF off too is the LPF's own: the drive ladder (TRANSISTOR_24DB_DRIVE) self-oscillates from about 50 % resonance
// (here at most -56 dBFS, 10 dB below the music; as in 1.2.1). It's counted apart and doesn't fail, unless the HPF
// makes it more than 6 dB louder.
#include "dsp/filter/filter_set.h"
#include "util/functions.h"
#include <cmath>
#include <cstdio>
#include <cstring>

namespace AudioEngine {
int32_t cpuDireness = 0;
bool renderInStereo = true;
} // namespace AudioEngine

using namespace deluge::dsp::filter;

constexpr int kBlock = 128;
constexpr double kFs = 44100;
constexpr int kMusicBlocks = 345;      // 1 s
constexpr int kSilenceBlocks = 517;    // 1.5 s
constexpr int kTailBlocks = 172;       // the last 0.5 s
constexpr double kMaxWhistle = 10;     // dB above the music
constexpr int32_t kGainIn = 167763968; // the song's filter gain (GlobalEffectable)

// The song's unpatched params as the knobs set them (0-50 on the display), and what setupFilterSetConfig() makes of
// them (paramNeutralValues: LPF 2000000, HPF 2672947, resonance 25 * 10737418)
static int32_t knob(double k) {
	return k >= 50 ? INT32_MAX : k <= 0 ? INT32_MIN : (int32_t)std::lround((k - 25) * 2147483647.0 / 25);
}

static int32_t music[kMusicBlocks * kBlock * 2];

static void makeMusic() {
	// C minor chord of band-limited saws (harmonics up to 8 kHz) and a little noise, -47 dBFS rms
	static const double notes[] = {130.81, 155.56, 196.0, 261.63};
	uint32_t r = 1;
	double sumSq = 0;
	static double m[kMusicBlocks * kBlock * 2];
	for (int i = 0; i < kMusicBlocks * kBlock; i++) {
		double t = i / kFs, v[2] = {0, 0};
		for (int c = 0; c < 2; c++) {
			for (double f : notes) {
				double ff = f * (c ? 1.003 : 1.0);
				for (int h = 1; h * ff < 8000; h++) {
					v[c] += std::sin(2 * M_PI * ff * h * t) / h;
				}
			}
			r = r * 1664525 + 1013904223;
			v[c] += ((int32_t)r / 2147483648.0) * 0.3;
			m[i * 2 + c] = v[c];
			sumSq += v[c] * v[c];
		}
	}
	double scale = std::pow(10, -47 / 20.0) / std::sqrt(sumSq / (kMusicBlocks * kBlock * 2)) * 2147483648.0;
	for (int i = 0; i < kMusicBlocks * kBlock * 2; i++) {
		music[i] = (int32_t)(m[i] * scale);
	}
}

struct Result {
	double tailDb; // with the output gain (masterVolumeAdjustment)
	double tailHz;
	bool steady;    // sounding on its own: not dying away
	double musicDb; // the whole music part, likewise
};

// hpfOn false: the HPF off (its frequency knob at 0), lpfOn false: the LPF off (open, not turned)
static Result runCase(FilterMode lpf, FilterMode hpf, FilterRoute route, double hpfKnob, double hpfResKnob,
                      double lpfResKnob, bool hpfOn = true, bool lpfOn = true) {
	static FilterSet fs;
	std::memset((void*)&fs, 0, sizeof(fs));
	jcong = 380116160;
	int32_t buf[kBlock * 2];
	double tailSq = 0, beforeSq = 0, musicSq = 0;
	int crossings = 0, lastSign = 0, tailSamples = 0;
	for (int b = 0; b < kMusicBlocks + kSilenceBlocks; b++) {
		// The LPF turned: 50 -> 10 in the first half second, up to 45 in the second, then held
		double lpfKnob = b < kMusicBlocks / 2 ? 50 - 40.0 * b / (kMusicBlocks / 2)
		                 : b < kMusicBlocks   ? 10 + 35.0 * (b - kMusicBlocks / 2) / (kMusicBlocks - kMusicBlocks / 2)
		                                      : 45;
		int32_t lpfFreqKnob = knob(lpfOn ? lpfKnob : 50), hpfFreqKnob = knob(hpfOn ? hpfKnob : 0);
		int32_t lpfFrequency = getFinalParameterValueExp(2000000, cableToExpParamShortcut(lpfFreqKnob));
		int32_t lpfResonance =
		    getFinalParameterValueLinear(25 * 10737418, cableToLinearParamShortcut(knob(lpfResKnob)));
		int32_t hpfFrequency = getFinalParameterValueExp(2672947, cableToExpParamShortcut(hpfFreqKnob));
		int32_t hpfResonance =
		    getFinalParameterValueLinear(25 * 10737418, cableToLinearParamShortcut(knob(hpfResKnob)));
		bool doLPF = lpf == FilterMode::TRANSISTOR_24DB_DRIVE || lpfFreqKnob < 2147483602;
		bool doHPF = hpfFreqKnob > INT32_MIN;
		FilterMode lpfModeForRender = doLPF ? lpf : FilterMode::OFF;
		FilterMode hpfModeForRender = doHPF ? hpf : FilterMode::OFF;
		int32_t gain = fs.setConfig(lpfFrequency, lpfResonance, lpfModeForRender,
		                            getFinalParameterValueLinear(25 * 10737418, -536870912), hpfFrequency, hpfResonance,
		                            hpfModeForRender, getFinalParameterValueLinear(25 * 10737418, -536870912), kGainIn,
		                            route, false, nullptr);
		double outScale = (double)gain / kGainIn; // GlobalEffectable: masterVolumeAdjustment
		if (b < kMusicBlocks) {
			std::memcpy(buf, &music[b * kBlock * 2], sizeof(buf));
		}
		else {
			std::memset(buf, 0, sizeof(buf));
		}
#ifdef HPF_SATURATION_PER_CONTEXT
		fs.renderLongStereo(buf, buf + kBlock * 2, HpLadderFilter::kSaturationGlobal);
#else
		fs.renderLongStereo(buf, buf + kBlock * 2);
#endif
		if (b < kMusicBlocks) {
			for (int i = 0; i < kBlock * 2; i += 2) {
				double v = buf[i] / 2147483648.0 * outScale;
				musicSq += v * v;
			}
		}
		if (b >= kMusicBlocks + kSilenceBlocks - 2 * kTailBlocks && b < kMusicBlocks + kSilenceBlocks - kTailBlocks) {
			for (int i = 0; i < kBlock * 2; i += 2) {
				double v = buf[i] / 2147483648.0 * outScale;
				beforeSq += v * v;
			}
		}
		if (b >= kMusicBlocks + kSilenceBlocks - kTailBlocks) {
			for (int i = 0; i < kBlock * 2; i += 2) {
				double v = buf[i] / 2147483648.0 * outScale;
				tailSq += v * v;
				int sign = buf[i] > 0 ? 1 : buf[i] < 0 ? -1 : 0;
				if (sign && lastSign && sign != lastSign) {
					crossings++;
				}
				if (sign) {
					lastSign = sign;
				}
				tailSamples++;
			}
		}
	}
	double musicDb = 10 * std::log10(musicSq / (kMusicBlocks * kBlock) + 1e-30) + 3.01;
	double tailDb = 10 * std::log10(tailSq / tailSamples + 1e-30) + 3.01;
	double beforeDb = 10 * std::log10(beforeSq / tailSamples + 1e-30) + 3.01;
	return {tailDb, crossings / 2.0 / (tailSamples / kFs), tailDb > -90 && tailDb - beforeDb > -1, musicDb};
}

int main() {
	makeMusic();
	struct Mode {
		const char* name;
		FilterMode mode;
	};
	const Mode lpfs[] = {{"LP12", FilterMode::TRANSISTOR_12DB},
	                     {"LP24", FilterMode::TRANSISTOR_24DB},
	                     {"Drive", FilterMode::TRANSISTOR_24DB_DRIVE},
	                     {"SVF band", FilterMode::SVF_BAND},
	                     {"SVF notch", FilterMode::SVF_NOTCH}};
	const Mode hpfs[] = {
	    {"HP ladder", FilterMode::HPLADDER}, {"SVF band", FilterMode::SVF_BAND}, {"SVF notch", FilterMode::SVF_NOTCH}};
	const Mode routes[] = {{"HPF>LPF", (FilterMode)FilterRoute::HIGH_TO_LOW},
	                       {"LPF>HPF", (FilterMode)FilterRoute::LOW_TO_HIGH},
	                       {"parallel", (FilterMode)FilterRoute::PARALLEL}};
	const double hpfKnobs[] = {10, 20, 25, 30, 33};
	const double hpfResKnobs[] = {40, 50}; // 80 %, 100 %
	const double lpfResKnobs[] = {0, 25};  // 0, 50 %
	// The music itself: both filters off
	const double music =
	    runCase(FilterMode::TRANSISTOR_24DB, FilterMode::HPLADDER, FilterRoute::HIGH_TO_LOW, 0, 0, 0, false, false)
	        .musicDb;
	printf("the music: %.1f dBFS; a steady tone more than %.0f dB above it fails\n", music, kMaxWhistle);
	int cases = 0, whistles = 0, lpfOwn = 0, failures = 0;
	double worst = -1000, worstLpf = -1000; // dBFS of the loudest steady tone: the HPF's, the LPF's own
	for (const Mode& hpf : hpfs) {
		for (const Mode& lpf : lpfs) {
			for (const Mode& route : routes) {
				// The same with the HPF off: a tone there is the LPF's own
				Result alone[2];
				for (int k = 0; k < 2; k++) {
					alone[k] = runCase(lpf.mode, hpf.mode, (FilterRoute)route.mode, 0, 0, lpfResKnobs[k], false);
				}
				int n = 0, own = 0, bad = 0;
				double loudest = -1000, loudestHz = 0, atKnob = 0, atRes = 0, loudestOwn = -1000;
				for (double hk : hpfKnobs) {
					for (double hr : hpfResKnobs) {
						for (int k = 0; k < 2; k++) {
							Result r = runCase(lpf.mode, hpf.mode, (FilterRoute)route.mode, hk, hr, lpfResKnobs[k]);
							cases++;
							if (!r.steady) {
								continue;
							}
							if (alone[k].steady && r.tailDb < alone[k].tailDb + 6) {
								own++;
								lpfOwn++;
								loudestOwn = std::max(loudestOwn, r.tailDb);
								continue;
							}
							n++;
							whistles++;
							if (r.tailDb > music + kMaxWhistle) {
								bad++;
								failures++;
							}
							if (r.tailDb > loudest) {
								loudest = r.tailDb, loudestHz = r.tailHz, atKnob = hk, atRes = hr;
							}
						}
					}
				}
				worst = std::max(worst, loudest);
				worstLpf = std::max(worstLpf, loudestOwn);
				printf("HPF %-9s LPF %-9s %-8s: %2d of 20 whistle on their own", hpf.name, lpf.name, route.name, n);
				if (n) {
					printf(", loudest %+5.1f dB against the music at %5.0f Hz (HPF %2.0f, resonance %3.0f %%)",
					       loudest - music, loudestHz, atKnob, atRes * 2);
				}
				if (own) {
					printf("; %d the LPF's own (as with the HPF off), loudest %.1f dBFS", own, loudestOwn);
				}
				printf("%s\n", bad ? "  FAIL" : "");
			}
		}
	}
	printf(
	    "%d of %d cases: the HPF whistles on its own after the music (a steady tone 1-1.5 s into the silence), "
	    "loudest %+.1f dB against the music; %d cases: the LPF's own tone (the drive ladder from 50 %% resonance, as "
	    "in 1.2.1), loudest %.1f dBFS\n",
	    whistles, cases, worst - music, lpfOwn, worstLpf);
	if (failures) {
		printf("FAILED: in %d cases the HPF whistles more than %.0f dB above the music\n", failures, kMaxWhistle);
		return 1;
	}
	printf("ok: the HPF whistles at most %.0f dB above the music\n", kMaxWhistle);
	return 0;
}
