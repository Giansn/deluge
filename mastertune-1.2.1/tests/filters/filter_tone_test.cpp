// The resonant HPF's whistle (mastertune-v16 fix, filter-fix): the firmware's FilterSet (dsp/filter) as the song, a kit
// or audio track (GlobalEffectable) and a voice (a synth or a kit row) run it, with the HPF on and its resonance from
// medium to full, and the LPF turned down and up again with the gold knob (slow and fast), for every HPF mode, every
// route and every LPF mode.
//
// A user on v16 heard "a painful high tone" with the song's HPF on with resonance while turning its LPF. From about 70
// % resonance the HP ladder oscillates on its own, a sine at its cutoff (2.2 kHz with the knob at 30), whatever comes
// in. Since community #336 its tanh limits 2 to 3 bits higher than in the original firmware, so that sine was 12 to 18
// dB louder than there: on the song's filters up to 24 dB above the music. The LPF after it (H2L, the default) hides it
// while it's below the HPF's cutoff, so it seems to come up when the LPF is turned.
//
// Measured per case: the output's level (power mean) while the LPF is turned and after, against the level of the music
// itself (the HPF off, the LPF open), each with the filter gain as the firmware applies it (the song's and a kit's to
// the output, as the audio engine does; a voice's to the input, as Voice::render() does). It must stay within
// kMaxWhistle dB: louder than that, the HPF drowns out the music (v16: up to +13 dB on the song's filters, +16 in a
// synth or a kit row; the fix: at most +4 with the HP ladder, +6 with the SVF, as in v16). Also printed: the most
// energy above 8 kHz the turn adds against the same case with the LPF left open (for information: turning makes none
// to speak of, in v16 or the fix).
//
// Contexts (input levels as measured in tests/song's song at the filters' inputs, RMS in dB re full scale):
//   song      the song's filters (AudioEngine::renderSongFX), stereo, a mix (-49.7)
//   kit       a kit's / an audio track's filters (GlobalEffectableForClip), stereo, a mix (-39)
//   synth     a synth's voice filters, mono, a saw (-44.5)
//   kit row   a kit row's voice filters, stereo, a drum loop (-44.5)
#include "dsp/filter/filter_set.h"
#include "util/functions.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace AudioEngine {
int32_t cpuDireness = 0;
bool renderInStereo = true;
} // namespace AudioEngine

using namespace deluge::dsp::filter;

namespace {
constexpr double kFs = 44100;
constexpr int kBlock = 128;
constexpr int kWindow = 1024;
constexpr double kMaxWhistle = 10; // dB above the music (with the resonance at 0)

// The HP ladder's saturation per context: the fixed firmware takes it from the caller, 1.2.1 to v16 had one for all
#ifdef HPF_SATURATION_PER_CONTEXT
constexpr int32_t kSatVoice = HpLadderFilter::kSaturationVoice;
constexpr int32_t kSatGlobal = HpLadderFilter::kSaturationGlobal;
#endif

// Params as GlobalEffectable::setupFilterSetConfig() and the patcher make them from a knob position (-64 to 64)
int32_t knobValue(int32_t pos) {
	int64_t v = (int64_t)pos << 25;
	return (int32_t)std::min<int64_t>(v, INT32_MAX);
}
int32_t freqParam(int32_t neutral, int32_t knob) {
	return getExp(neutral, knob >> 2); // getFinalParameterValueExp(neutral, cableToExpParamShortcut(knob))
}
int32_t linearParam(int32_t knob) { // getFinalParameterValueLinear(25 * 10737418, cableToLinearParamShortcut(knob))
	return lshiftAndSaturate<3>(multiply_32x32_rshift32((knob >> 2) + 536870912, 25 * 10737418));
}
int32_t knobFromDisplay(int32_t display) { // 0 to 50 as the Deluge shows it
	return (int32_t)std::lround(display * 128.0 / 50) - 64;
}

enum class Kind { SONG, KIT, SYNTH, KIT_ROW };
struct Context {
	const char* name;
	Kind kind;
	bool stereo;
	bool global; // the filter gain applies after the filters (GlobalEffectable), else to the input (Voice)
};
const Context contexts[] = {{"song", Kind::SONG, true, true},
                            {"kit", Kind::KIT, true, true},
                            {"synth", Kind::SYNTH, false, false},
                            {"kit row", Kind::KIT_ROW, true, false}};

struct Rng {
	uint32_t s = 22222;
	float next() { // -1 to 1
		s = s * 1664525u + 1013904223u;
		return (int32_t)s / 2147483648.0f;
	}
};

// 2 s of input, looped: interleaved stereo (mono contexts use the left), scaled to the context's RMS level
std::vector<float> makeInput(Kind kind) {
	const int n = (int)(2 * kFs);
	std::vector<float> x(2 * n);
	Rng rng;
	float lp = 0, hp = 0;
	double ph[4] = {0, 0, 0, 0};
	const double chord[4] = {130.81, 155.56, 196.0, 233.08}; // Cm7
	for (int i = 0; i < n; i++) {
		double t = i / kFs;
		float l = 0, r = 0;
		if (kind == Kind::SONG || kind == Kind::KIT) {
			for (int k = 0; k < 4; k++) { // a pad: saws, softened
				ph[k] += chord[k] * (1 + 0.002 * k) / kFs;
				ph[k] -= std::floor(ph[k]);
				l += (float)(2 * ph[k] - 1) * 0.15f;
			}
			lp += 0.2f * (l - lp);
			l = lp;
			double beat = std::fmod(t, 0.5);
			l += (float)(0.6 * std::exp(-beat * 18)
			             * std::sin(2 * M_PI * (50 + 80 * std::exp(-beat * 30)) * beat)); // kick
			double hat = std::fmod(t + 0.25, 0.25);
			float w = rng.next();
			hp = w - hp * 0.2f;
			l += (float)(0.25 * std::exp(-hat * 60)) * hp; // hats
			r = l * 0.9f + 0.1f * lp;
		}
		else if (kind == Kind::SYNTH) {
			ph[0] += 110.0 / kFs;
			ph[0] -= std::floor(ph[0]);
			l = r = (float)(2 * ph[0] - 1);
		}
		else { // a drum loop: a noisy hit and a tuned body every 1/4 s
			double hit = std::fmod(t, 0.25);
			float w = rng.next();
			l = (float)(std::exp(-hit * 40) * (0.6 * w + 0.4 * std::sin(2 * M_PI * 180 * hit)));
			r = (float)(std::exp(-hit * 40) * (0.5 * w + 0.5 * std::sin(2 * M_PI * 181 * hit)));
		}
		x[2 * i] = l;
		x[2 * i + 1] = r;
	}
	double power = 0;
	for (float v : x) {
		power += (double)v * v;
	}
	double rms = std::sqrt(power / x.size());
	double target = kind == Kind::SONG ? -49.7 : kind == Kind::KIT ? -39.0 : -44.5;
	float scale = (float)(std::pow(10.0, target / 20) / rms);
	for (float& v : x) {
		v *= scale;
	}
	return x;
}

struct Case {
	FilterMode lpf, hpf;
	FilterRoute route;
	int32_t hpfKnob, hpfRes, lpfRes; // display 0 to 50
	double sweepSeconds;             // each way: down from open to closed, and back
};

constexpr double kHold = 0.25; // LPF open before the turn
constexpr double kTail = 0.5;  // and after

// One run: the output's level per 1024-sample window (dB re full scale), and above 8 kHz; turned or with the LPF left
// open
struct Run {
	std::vector<double> level, high;
};

Run run(const Context& ctx, const std::vector<float>& input, const Case& c, bool turn, int32_t hpfRes,
        bool hpfOn = true) {
	static FilterSet fs;
	fs.reset();
	jcong = 380116160;
	int total = (int)((kHold + 2 * c.sweepSeconds + kTail) * kFs);
	int inFrames = (int)input.size() / 2;
	std::vector<double> out(total);
	int32_t buf[kBlock * 2];
	int32_t hpfFreq = freqParam(2672947, knobValue(knobFromDisplay(c.hpfKnob)));
	int32_t hpfResParam = linearParam(knobValue(knobFromDisplay(hpfRes)));
	int32_t lpfResParam = linearParam(knobValue(knobFromDisplay(c.lpfRes)));
	for (int s = 0; s < total; s += kBlock) {
		// The knob: one click (1/128 of its range) at a time, from 64 (open, the LPF off) to -64 and back
		double t = s / kFs - kHold;
		int32_t pos = 64;
		if (turn && t > 0) {
			int32_t clicks = (int32_t)(t / c.sweepSeconds * 128);
			pos = clicks <= 128 ? 64 - clicks : std::min<int32_t>(-64 + (clicks - 128), 64);
		}
		int32_t lpfKnob = knobValue(pos);
		bool lpfOn = c.lpf == FilterMode::TRANSISTOR_24DB_DRIVE || lpfKnob < 2147483602;
		int32_t gainIn = ctx.global ? 167763968 : 134217728 << 1; // the song's; a voice's (volumeNeutralValue << 1)
		int32_t gain =
		    fs.setConfig(freqParam(2000000, lpfKnob), lpfResParam, lpfOn ? c.lpf : FilterMode::OFF, 0, hpfFreq,
		                 hpfResParam, hpfOn ? c.hpf : FilterMode::OFF, 0, gainIn, c.route, false, nullptr);
		double inScale = ctx.global ? 1.0 : (double)gain / gainIn;  // Voice: the oscillators' amplitude
		double outScale = ctx.global ? (double)gain / gainIn : 1.0; // GlobalEffectable: masterVolumeAdjustment
		int n = std::min(kBlock, total - s);
		for (int i = 0; i < n; i++) {
			int j = (s + i) % inFrames;
			for (int ch = 0; ch < 2; ch++) {
				buf[2 * i + ch] = (int32_t)(input[2 * j + ch] * inScale * 2147483647.0);
			}
			if (!ctx.stereo) {
				buf[i] = buf[2 * i];
			}
		}
#ifdef HPF_SATURATION_PER_CONTEXT
		int32_t sat = ctx.global ? kSatGlobal : kSatVoice;
		if (ctx.stereo) {
			fs.renderLongStereo(buf, buf + 2 * n, sat);
		}
		else {
			fs.renderLong(buf, buf + n, n, 1, sat);
		}
#else
		if (ctx.stereo) {
			fs.renderLongStereo(buf, buf + 2 * n);
		}
		else {
			fs.renderLong(buf, buf + n, n);
		}
#endif
		for (int i = 0; i < n; i++) {
			out[s + i] = buf[ctx.stereo ? 2 * i : i] / 2147483648.0 * outScale;
		}
	}
	// Levels per window; above 8 kHz through a 4th-order Butterworth high-pass (two biquads)
	Run r;
	double w0 = 2 * M_PI * 8000 / kFs, cw = std::cos(w0), sw = std::sin(w0);
	double q[2] = {0.5412, 1.3066};
	double z[2][2] = {{0, 0}, {0, 0}};
	double sum = 0, sumHigh = 0;
	for (int i = 0; i < total; i++) {
		double v = out[i];
		double h = v;
		for (int k = 0; k < 2; k++) {
			double alpha = sw / (2 * q[k]);
			double a0 = 1 + alpha;
			double b0 = (1 + cw) / 2 / a0, b1 = -(1 + cw) / a0, b2 = b0, a1 = -2 * cw / a0, a2 = (1 - alpha) / a0;
			double y = b0 * h + z[k][0];
			z[k][0] = b1 * h - a1 * y + z[k][1];
			z[k][1] = b2 * h - a2 * y;
			h = y;
		}
		sum += v * v;
		sumHigh += h * h;
		if ((i + 1) % kWindow == 0) {
			r.level.push_back(10 * std::log10(sum / kWindow + 1e-30));
			r.high.push_back(10 * std::log10(sumHigh / kWindow + 1e-30));
			sum = sumHigh = 0;
		}
	}
	return r;
}

double meanLevel(const Run& r) { // power mean over the whole run
	double p = 0;
	for (double l : r.level) {
		p += std::pow(10, l / 10);
	}
	return 10 * std::log10(p / r.level.size());
}

const char* modeName(FilterMode m) {
	switch (m) {
	case FilterMode::TRANSISTOR_12DB:
		return "LP12";
	case FilterMode::TRANSISTOR_24DB:
		return "LP24";
	case FilterMode::TRANSISTOR_24DB_DRIVE:
		return "drive";
	case FilterMode::SVF_BAND:
		return "SVF band";
	case FilterMode::SVF_NOTCH:
		return "SVF notch";
	case FilterMode::HPLADDER:
		return "HP ladder";
	default:
		return "off";
	}
}
const char* routeName(FilterRoute r) {
	return r == FilterRoute::HIGH_TO_LOW ? "H2L" : r == FilterRoute::LOW_TO_HIGH ? "L2H" : "parallel";
}
} // namespace

int main() {
	const FilterMode hpfModes[] = {FilterMode::HPLADDER, FilterMode::SVF_BAND, FilterMode::SVF_NOTCH};
	const FilterMode lpfModes[] = {FilterMode::TRANSISTOR_12DB, FilterMode::TRANSISTOR_24DB,
	                               FilterMode::TRANSISTOR_24DB_DRIVE, FilterMode::SVF_BAND, FilterMode::SVF_NOTCH};
	const FilterRoute routes[] = {FilterRoute::HIGH_TO_LOW, FilterRoute::LOW_TO_HIGH, FilterRoute::PARALLEL};
	int failures = 0;
	for (const Context& ctx : contexts) {
		std::vector<float> input = makeInput(ctx.kind);
		std::vector<Case> cases;
		// Every HPF mode and route, resonance medium to full, the HPF low and high, the LPF (24 dB) turned fast
		for (FilterMode hpf : hpfModes) {
			for (FilterRoute route : routes) {
				for (int32_t knob : {15, 30}) {
					for (int32_t res : {25, 35, 42, 50}) {
						cases.push_back({FilterMode::TRANSISTOR_24DB, hpf, route, knob, res, 0, 0.25});
					}
				}
			}
		}
		// Every LPF mode, turned slowly, with the HPF (each mode) at full resonance
		for (FilterMode hpf : hpfModes) {
			for (FilterMode lpf : lpfModes) {
				cases.push_back({lpf, hpf, FilterRoute::HIGH_TO_LOW, 30, 50, 0, 2.0});
			}
		}
		double worst[3] = {-1e9, -1e9, -1e9};
		double worstHigh = -1e9;
		const Case* worstCase[3] = {};
		for (const Case& c : cases) {
			Run ref = run(ctx, input, c, false, 0, false); // the music: the HPF off, the LPF open
			double music = meanLevel(ref);
			Run r = run(ctx, input, c, true, c.hpfRes);
			Run open = run(ctx, input, c, false, c.hpfRes);
			double level = meanLevel(r), added = -1e9;
			for (size_t i = 0; i < r.level.size(); i++) {
				if (r.high[i] > r.level[i] - 40) { // above 8 kHz, where it isn't far below the output's level
					added = std::max(added, r.high[i] - open.high[std::min(i, open.high.size() - 1)]);
				}
			}
			double whistle = level - music;
			int m = c.hpf == FilterMode::HPLADDER ? 0 : c.hpf == FilterMode::SVF_BAND ? 1 : 2;
			if (whistle > worst[m]) {
				worst[m] = whistle;
				worstCase[m] = &c;
			}
			worstHigh = std::max(worstHigh, added);
			bool bad = whistle > kMaxWhistle;
			failures += bad;
			if (bad || getenv("VERBOSE")) {
				printf("%s %-8s HPF %-9s knob %2d res %2d, LPF %-9s %s turned in %.2f s: %+5.1f dB against the "
				       "music (%5.1f dBFS), above 8 kHz %+5.1f dB against the LPF left open\n",
				       bad ? "FAIL" : "    ", ctx.name, modeName(c.hpf), (int)c.hpfKnob, (int)c.hpfRes, modeName(c.lpf),
				       routeName(c.route), c.sweepSeconds, whistle, music, added);
			}
		}
		for (int m = 0; m < 3; m++) {
			const Case& c = *worstCase[m];
			printf("%-8s %-9s: %zu cases, at most %+5.1f dB against the music (knob %d, res %d, LPF %s, %s)%s\n",
			       ctx.name, modeName(hpfModes[m]), cases.size() / 3, worst[m], (int)c.hpfKnob, (int)c.hpfRes,
			       modeName(c.lpf), routeName(c.route), worst[m] > kMaxWhistle ? "  FAIL" : "");
		}
		printf("%-8s turning adds at most %+5.1f dB above 8 kHz (against the LPF left open)\n", ctx.name, worstHigh);
		fflush(stdout);
	}
	if (failures) {
		printf("\n%d cases whistle more than %.0f dB above the music\n", failures, kMaxWhistle);
	}
	else {
		printf("\nall cases within %.0f dB of the music\n", kMaxWhistle);
	}
	return failures ? 1 : 0;
}
