// The resonant LPF's own tone: the firmware's LPF (dsp/filter, FilterSet) against the original Synthstrom firmware's
// (fc76a9cd, before community #336, rebuilt below from its filter_set.cpp / filter_set_config.cpp), in the song's (and
// a kit's / audio track's: GlobalEffectable, stereo) and a voice's (a synth, mono; a kit row, stereo) filters, every LPF
// mode, the resonance 0 to 50 on the display, the cutoff held at several positions and turned (slow and fast).
//
// Per case: music (a chord of saws and a little noise, at the context's level) for 0.75 s, then 1 s of silence with the
// LPF still on. A filter that doesn't self-oscillate dies away; one that does keeps a sine at its cutoff (the last 0.5 s
// above -100 dBFS and no more than 1 dB below the 0.5 s before). Printed per context and mode: from which resonance it
// keeps a tone, how loud (dBFS, with the output gain the firmware applies) and at what frequency, next to the original.
// Turned: the loudest 1024-sample window while the cutoff goes from open to closed and back with the music on, against
// the original's.
//
// Fails where the firmware's own tone is louder than the original's by more than kMaxLouder dB (same context, mode,
// resonance and cutoff), or it keeps a tone at a resonance at which the original doesn't at any cutoff. VERBOSE=1:
// every resonance. FULL=1: every resonance and more cutoffs (slow in the emulator).
#include "dsp/filter/filter_set.h"
#include "util/functions.h"
#include <algorithm>
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
constexpr int kMusicBlocks = 258;   // 0.75 s
constexpr int kSilenceBlocks = 345; // 1 s
constexpr int kTailBlocks = 172;    // the last 0.5 s
constexpr double kMaxLouder = 1.5;  // dB above the original firmware's own tone

// ---- The original firmware's LPF (fc76a9cd), as it was: FilterSetConfig::init() (the LPF part) and
// FilterSet::renderLPFLong(), with its extraSaturation / extraSaturationDrive per context (voices 1 / 1: filter_set.h
// renderLong(); the song, kits, audio tracks 2 / 1: global_effectable.cpp processFilters())
namespace orig {
enum Mode { LP12, LP24, DRIVE, SVF };
const int16_t resonanceThresholdsForOversampling[] = {
    16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384,
    16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384,
    16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384,
    16384, 16384, 16384, 16384, 15500, 20735, 17000, 9000,  9000,  9000,  9000,  9000,  9000,  9000,  9000,  9000,
    9000};
const int16_t resonanceLimitTable[] = {
    32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767,
    32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767,
    32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767, 32767,
    32767, 32767, 32767, 32767, 28415, 20000, 17000, 17000, 17000, 17000, 17000, 17000, 17000, 17000, 17000, 17000,
    17000};

struct Comp {
	int32_t memory = 0;
	int32_t doFilter(int32_t input, int32_t moveability) {
		int32_t a = multiply_32x32_rshift32_rounded(input - memory, moveability) << 1;
		int32_t b = a + memory;
		memory = b + a;
		return b;
	}
	int32_t doAPF(int32_t input, int32_t moveability) {
		int32_t a = multiply_32x32_rshift32_rounded(input - memory, moveability) << 1;
		int32_t b = a + memory;
		memory = a + b;
		return b * 2 - input;
	}
	int32_t getFeedbackOutput(int32_t f) { return multiply_32x32_rshift32_rounded(memory, f) << 2; }
	int32_t getFeedbackOutputWithoutLshift(int32_t f) { return multiply_32x32_rshift32_rounded(memory, f); }
};

struct Config {
	int32_t moveability, processedResonance, divideByTotalMoveabilityAndProcessedResonance, lpf1Feedback,
	    lpf2Feedback, lpf3Feedback, divideBy1PlusTannedFrequency, lpfRawResonance;
	bool doOversampling;

	int32_t init(int32_t lpfFrequency, int32_t lpfResonance, Mode lpfMode, int32_t filterGain) {
		if (lpfMode == DRIVE) {
			int32_t resonance = 2147483647 - (lpfResonance << 2);
			processedResonance = 2147483647 - resonance;
			int32_t logFreq = quickLog(lpfFrequency);
			doOversampling = false;
			logFreq = std::min(logFreq, (int32_t)63 << 24);
			if (AudioEngine::cpuDireness < 14 && (logFreq >> 24) > 51) {
				int32_t resonanceThreshold = interpolateTableSigned(logFreq, 30, resonanceThresholdsForOversampling, 6);
				doOversampling = (processedResonance > resonanceThreshold);
			}
			if (doOversampling) {
				lpfFrequency >>= 1;
				logFreq -= 33554432;
				lpfFrequency -= (multiply_32x32_rshift32_rounded(logFreq, lpfFrequency) >> 8) * 34;
				lpfFrequency = std::min((int32_t)39056384, lpfFrequency);
				int32_t resonanceLimit = interpolateTableSigned(logFreq, 30, resonanceLimitTable, 6);
				processedResonance = std::min(processedResonance, resonanceLimit);
			}
		}
		int32_t tannedFrequency = instantTan(lshiftAndSaturate<5>(lpfFrequency));
		if (lpfMode != DRIVE) {
			int32_t howMuchTooLow = 0;
			if (tannedFrequency < 6000000) {
				howMuchTooLow = 6000000 - tannedFrequency;
			}
			int32_t howMuchToKeep = 2147483647 - howMuchTooLow * 33;
			int32_t resonanceUpperLimit = 510000000;
			tannedFrequency = std::max(tannedFrequency, (int32_t)540817);
			int32_t resonance = 2147483647 - (std::min(lpfResonance, resonanceUpperLimit) << 2);
			lpfRawResonance = resonance;
			resonance = multiply_32x32_rshift32_rounded(resonance, resonance) << 1;
			processedResonance = 2147483647 - resonance;
			processedResonance = multiply_32x32_rshift32_rounded(processedResonance, howMuchToKeep) << 1;
		}
		divideBy1PlusTannedFrequency = (int64_t)2147483648u * 134217728 / (134217728 + (tannedFrequency >> 1));
		moveability = multiply_32x32_rshift32_rounded(tannedFrequency, divideBy1PlusTannedFrequency) << 4;
		if (lpfMode == LP12) {
			int32_t moveabilityNegative = moveability - 1073741824;
			lpf2Feedback = multiply_32x32_rshift32_rounded(moveabilityNegative, divideBy1PlusTannedFrequency) << 1;
			lpf1Feedback = multiply_32x32_rshift32_rounded(lpf2Feedback, moveability) << 1;
			divideByTotalMoveabilityAndProcessedResonance =
			    (int64_t)67108864 * 1073741824
			    / (67108864
			       + multiply_32x32_rshift32_rounded(
			           processedResonance,
			           multiply_32x32_rshift32_rounded(moveabilityNegative,
			                                           multiply_32x32_rshift32_rounded(moveability, moveability))));
		}
		else {
			lpf3Feedback = multiply_32x32_rshift32_rounded(divideBy1PlusTannedFrequency, moveability);
			lpf2Feedback = multiply_32x32_rshift32_rounded(lpf3Feedback, moveability) << 1;
			lpf1Feedback = multiply_32x32_rshift32_rounded(lpf2Feedback, moveability) << 1;
			int32_t onePlusThing =
			    67108864
			    + (multiply_32x32_rshift32_rounded(
			        moveability,
			        multiply_32x32_rshift32_rounded(
			            moveability,
			            multiply_32x32_rshift32_rounded(moveability,
			                                            multiply_32x32_rshift32_rounded(moveability, processedResonance)))));
			divideByTotalMoveabilityAndProcessedResonance = (int64_t)67108864 * 1073741824 / onePlusThing;
		}
		if (lpfMode != DRIVE) {
			if (tannedFrequency <= 304587486) {
				processedResonance = multiply_32x32_rshift32_rounded(processedResonance, 1150000000) << 1;
			}
			else {
				processedResonance >>= 1;
			}
			int32_t a = std::min(lpfResonance, (int32_t)536870911);
			a = 536870912 - a;
			a = multiply_32x32_rshift32(a, a) << 3;
			a = 536870912 - a;
			int32_t gainModifier = 268435456 + a;
			filterGain = multiply_32x32_rshift32(filterGain, gainModifier) << 3;
		}
		else {
			filterGain *= 0.8;
		}
		return multiply_32x32_rshift32(filterGain, 1720000000) << 1;
	}
};

struct Set {
	Comp lpf1, lpf2, lpf3, lpf4;
	int32_t low = 0, band = 0, noiseLastValue = 0;

	int32_t moveabilityNow(const Config& c) {
		int32_t noise = getNoise() >> 2;
		int32_t distanceToGo = noise - noiseLastValue;
		noiseLastValue += distanceToGo >> 7;
		return c.moveability + multiply_32x32_rshift32(c.moveability, noiseLastValue);
	}
	int32_t feedbacks24(const Config& c) {
		return (lpf1.getFeedbackOutputWithoutLshift(c.lpf1Feedback) + lpf2.getFeedbackOutputWithoutLshift(c.lpf2Feedback)
		        + lpf3.getFeedbackOutputWithoutLshift(c.lpf3Feedback)
		        + lpf4.getFeedbackOutputWithoutLshift(c.divideBy1PlusTannedFrequency))
		       << 2;
	}
	int32_t do24(int32_t input, const Config& c, int saturationLevel) {
		int32_t m = moveabilityNow(c);
		int32_t x = multiply_32x32_rshift32_rounded(
		                (input - (multiply_32x32_rshift32_rounded(feedbacks24(c), c.processedResonance) << 3)),
		                c.divideByTotalMoveabilityAndProcessedResonance)
		            << 2;
		if (saturationLevel) {
			x = getTanHUnknown(x, saturationLevel);
		}
		return lpf4.doFilter(lpf3.doFilter(lpf2.doFilter(lpf1.doFilter(x, m), m), m), m) << 1;
	}
	int32_t doDrive(int32_t input, const Config& c, int extraSaturation) {
		int32_t m = moveabilityNow(c);
		int32_t feedbacksSum = getTanHUnknown(feedbacks24(c), 6 + extraSaturation);
		int32_t x = multiply_32x32_rshift32_rounded(
		                (input - (multiply_32x32_rshift32_rounded(feedbacksSum, c.processedResonance) << 3)),
		                c.divideByTotalMoveabilityAndProcessedResonance)
		            << 2;
		return lpf4.doFilter(lpf3.doFilter(lpf2.doFilter(lpf1.doFilter(x, m), m), m), m) << 1;
	}
	void render(int32_t* s, int32_t* end, const Config& c, Mode mode, int inc, int extraSaturation,
	            int extraSaturationDrive) {
		for (; s < end; s += inc) {
			if (mode == LP12) {
				int32_t m = moveabilityNow(c);
				int32_t feedbacksSum = lpf1.getFeedbackOutput(c.lpf1Feedback) + lpf2.getFeedbackOutput(c.lpf2Feedback)
				                       + lpf3.getFeedbackOutput(c.divideBy1PlusTannedFrequency);
				int32_t x = multiply_32x32_rshift32_rounded(
				                (*s - (multiply_32x32_rshift32_rounded(feedbacksSum, c.processedResonance) << 3)),
				                c.divideByTotalMoveabilityAndProcessedResonance)
				            << 2;
				x = getTanHUnknown(x, 1 + extraSaturation);
				*s = lpf3.doAPF(lpf2.doFilter(lpf1.doFilter(x, m), m), m) << 1;
			}
			else if (mode == LP24) {
				*s = do24(*s, c, c.processedResonance > 900000000 ? 1 + extraSaturation : 0);
			}
			else if (mode == DRIVE) {
				if (c.doOversampling) {
					doDrive(*s, c, extraSaturationDrive);
				}
				*s = getTanHUnknown(doDrive(*s, c, extraSaturationDrive), 3 + extraSaturationDrive);
			}
			else { // SVFilter::doSVF(), the LPF output
				int32_t f = c.moveability;
				int32_t q = c.lpfRawResonance;
				f = add_saturation(f, (f >> 2));
				f = add_saturation(f, 26508640);
				int32_t in = 2147483647 - c.processedResonance;
				low = low + multiply_32x32_rshift32(f, band);
				int32_t high = add_saturation((multiply_32x32_rshift32(*s, in) << 1), 0 - low);
				high = add_saturation(high, 0 - (multiply_32x32_rshift32(q, band) << 3));
				band = multiply_32x32_rshift32(f, high) + band;
				band = getTanHUnknown(band, 3);
				*s = low << 1;
			}
		}
	}
};
} // namespace orig

// ---- Contexts and params
struct Context {
	const char* name;
	bool stereo;
	bool global; // the song's / a kit's / an audio track's (GlobalEffectable): the gain after the filters
	double level; // the music's RMS at the filters' input, dBFS (as measured in tests/song's song)
};
const Context contexts[] = {{"song/kit", true, true, -47.0}, {"synth", false, false, -44.5}, {"kit row", true, false, -44.5}};

struct Mode {
	const char* name;
	FilterMode fw;
	orig::Mode orig;
};
const Mode modes[] = {{"LP12", FilterMode::TRANSISTOR_12DB, orig::LP12},
                      {"LP24", FilterMode::TRANSISTOR_24DB, orig::LP24},
                      {"Drive", FilterMode::TRANSISTOR_24DB_DRIVE, orig::DRIVE},
                      {"SVF", FilterMode::SVF_BAND, orig::SVF}};

int32_t knobValue(double display) { // 0 to 50 as the Deluge shows it
	int64_t v = (int64_t)std::lround(display * 128.0 / 50 - 64) << 25;
	return (int32_t)std::min<int64_t>(v, INT32_MAX);
}
int32_t freqParam(int32_t knob) {
	return getFinalParameterValueExp(2000000, cableToExpParamShortcut(knob));
}
int32_t resParam(int32_t knob) {
	return getFinalParameterValueLinear(25 * 10737418, cableToLinearParamShortcut(knob));
}

std::vector<int32_t> music; // interleaved stereo at 0 dBFS rms

void makeMusic() {
	static const double notes[] = {130.81, 155.56, 196.0, 261.63};
	int n = (kMusicBlocks + 2 * 345) * kBlock;
	std::vector<double> m(2 * n);
	uint32_t r = 1;
	double sumSq = 0;
	for (int i = 0; i < n; i++) {
		double t = i / kFs;
		for (int c = 0; c < 2; c++) {
			double v = 0;
			for (double f : notes) {
				double ff = f * (c ? 1.003 : 1.0);
				for (int h = 1; h * ff < 8000; h++) {
					v += std::sin(2 * M_PI * ff * h * t) / h;
				}
			}
			r = r * 1664525 + 1013904223;
			v += ((int32_t)r / 2147483648.0) * 0.3;
			m[2 * i + c] = v;
			sumSq += v * v;
		}
	}
	double scale = 1 / std::sqrt(sumSq / (2 * n));
	music.resize(2 * n);
	for (int i = 0; i < 2 * n; i++) {
		music[i] = (int32_t)(m[i] * scale * 2147483648.0 * 0.001); // -60 dBFS, scaled up per context
	}
}

struct Result {
	double tailDb = -300, tailHz = 0; // the tone after the music, with the output gain
	bool steady = false;
	double loudestDb = -300; // turned: the loudest window with the music on
};

// sweepSeconds 0: the cutoff held at cutoffDisplay, music then silence. Otherwise turned from open to closed and back,
// sweepSeconds each way, with the music on
Result runCase(bool original, const Context& ctx, const Mode& mode, double resDisplay, double cutoffDisplay,
               double sweepSeconds) {
	static FilterSet fs;
	fs.reset();
	static orig::Set os[2];
	os[0] = orig::Set();
	os[1] = orig::Set();
	orig::Config oc{};
	jcong = 380116160;
	double musicScale = std::pow(10, (ctx.level + 60) / 20);
	int musicBlocks = sweepSeconds ? (int)(2 * sweepSeconds * kFs / kBlock) + 1 : kMusicBlocks;
	int totalBlocks = sweepSeconds ? musicBlocks : kMusicBlocks + kSilenceBlocks;
	int32_t lpfRes = resParam(knobValue(resDisplay));
	int32_t buf[kBlock * 2];
	double tailSq = 0, beforeSq = 0, windowSq = 0;
	int crossings = 0, lastSign = 0, tailSamples = 0, windowSamples = 0;
	Result res;
	for (int b = 0; b < totalBlocks; b++) {
		double knob = cutoffDisplay;
		if (sweepSeconds) { // one click (1/128 of the range) at a time
			int clicks = (int)(b * kBlock / kFs / sweepSeconds * 128);
			int pos = clicks <= 128 ? 64 - clicks : std::min(-64 + (clicks - 128), 64);
			knob = (pos + 64) * 50.0 / 128;
		}
		int32_t lpfKnob = knobValue(knob);
		bool lpfOn = mode.fw == FilterMode::TRANSISTOR_24DB_DRIVE || lpfKnob < 2147483602;
		int32_t gainIn = ctx.global ? 167763968 : 134217728 << 1;
		int32_t gain = gainIn;
		if (original) {
			if (lpfOn) {
				gain = oc.init(freqParam(lpfKnob), lpfRes, mode.orig, gainIn);
			}
			else {
				gain = multiply_32x32_rshift32(gainIn, 1720000000) << 1;
			}
		}
		else {
			gain = fs.setConfig(freqParam(lpfKnob), lpfRes, lpfOn ? mode.fw : FilterMode::OFF, 0, 0, 0,
			                    FilterMode::OFF, 0, gainIn, FilterRoute::HIGH_TO_LOW, false, nullptr);
		}
		double inScale = ctx.global ? 1.0 : (double)gain / gainIn;
		double outScale = ctx.global ? (double)gain / gainIn : 1.0;
		for (int i = 0; i < kBlock; i++) {
			for (int c = 0; c < 2; c++) {
				int j = (b * kBlock + i) % (int)(music.size() / 2);
				buf[2 * i + c] = b < musicBlocks ? (int32_t)(music[j * 2 + c] * musicScale * inScale) : 0;
			}
			if (!ctx.stereo) {
				buf[i] = buf[2 * i];
			}
		}
		if (original) {
			if (lpfOn) {
				int extra = ctx.global ? 2 : 1;
				if (ctx.stereo) {
					os[0].render(buf, buf + 2 * kBlock, oc, mode.orig, 2, extra, 1);
					os[1].render(buf + 1, buf + 2 * kBlock, oc, mode.orig, 2, extra, 1);
				}
				else {
					os[0].render(buf, buf + kBlock, oc, mode.orig, 1, extra, 1);
				}
			}
			else {
				os[0] = orig::Set();
				os[1] = orig::Set();
			}
		}
		else if (ctx.stereo) {
#if defined(LPF_SATURATION_PER_CONTEXT)
			fs.renderLongStereo(buf, buf + 2 * kBlock,
			                    ctx.global ? HpLadderFilter::kSaturationGlobal : HpLadderFilter::kSaturationVoice,
			                    ctx.global ? LpLadderFilter::kSaturationGlobal : LpLadderFilter::kSaturationVoice);
#elif defined(HPF_SATURATION_PER_CONTEXT)
			fs.renderLongStereo(buf, buf + 2 * kBlock,
			                    ctx.global ? HpLadderFilter::kSaturationGlobal : HpLadderFilter::kSaturationVoice);
#else
			fs.renderLongStereo(buf, buf + 2 * kBlock);
#endif
		}
		else {
			fs.renderLong(buf, buf + kBlock, kBlock);
		}
		int step = ctx.stereo ? 2 : 1;
		for (int i = 0; i < kBlock; i++) {
			double v = buf[i * step] / 2147483648.0 * outScale;
			if (sweepSeconds) {
				windowSq += v * v;
				if (++windowSamples == 1024) {
					res.loudestDb = std::max(res.loudestDb, 10 * std::log10(windowSq / 1024 + 1e-30) + 3.01);
					windowSq = 0;
					windowSamples = 0;
				}
			}
			else if (b >= totalBlocks - 2 * kTailBlocks && b < totalBlocks - kTailBlocks) {
				beforeSq += v * v;
			}
			else if (b >= totalBlocks - kTailBlocks) {
				tailSq += v * v;
				int sign = buf[i * step] > 0 ? 1 : buf[i * step] < 0 ? -1 : 0;
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
	if (!sweepSeconds) {
		res.tailDb = 10 * std::log10(tailSq / tailSamples + 1e-30) + 3.01;
		double beforeDb = 10 * std::log10(beforeSq / tailSamples + 1e-30) + 3.01;
		res.tailHz = crossings / 2.0 / (tailSamples / kFs);
		res.steady = res.tailDb > -100 && res.tailDb - beforeDb > -1;
	}
	return res;
}
} // namespace

int main() {
	makeMusic();
	bool verbose = std::getenv("VERBOSE"), full = std::getenv("FULL"), detail = std::getenv("DETAIL");
	std::vector<double> cutoffs = full ? std::vector<double>{5, 10, 15, 20, 25, 30, 35, 40, 44, 47, 49}
	                                   : std::vector<double>{10, 20, 30, 40, 47};
	int resStep = full ? 1 : 5;
	int failures = 0;
	for (const Context& ctx : contexts) {
		for (const Mode& mode : modes) {
			// The original's SVF isn't compared: it sang from resonance 0 with the cutoff high (fixed by community
			// #212); only a tone at low resonance counts there
			bool compare = mode.orig != orig::SVF;
			int onset[2] = {-1, -1};
			double loudest[2] = {-300, -300}, loudestHz[2] = {0, 0}, worst = -300, worstRes = 0, worstCut = 0;
			int bad = 0;
			for (int r = 0; r <= 50; r += resStep) {
				double rowLoudest[2] = {-300, -300}, rowHz[2] = {0, 0};
				for (double cut : cutoffs) {
					Result o = compare ? runCase(true, ctx, mode, r, cut, 0) : Result{};
					Result f = runCase(false, ctx, mode, r, cut, 0);
					const Result* rr[2] = {&o, &f};
					for (int k = 0; k < 2; k++) {
						if (rr[k]->steady && rr[k]->tailHz >= 15) { // a tone (not a DC offset)
							if (onset[k] < 0) {
								onset[k] = r;
							}
							if (rr[k]->tailDb > rowLoudest[k]) {
								rowLoudest[k] = rr[k]->tailDb, rowHz[k] = rr[k]->tailHz;
							}
							if (rr[k]->tailDb > loudest[k]) {
								loudest[k] = rr[k]->tailDb, loudestHz[k] = rr[k]->tailHz;
							}
						}
					}
					if (detail && (o.steady || f.steady)) {
						printf("    %-8s %-5s res %2d cut %2.0f: orig %d %6.1f dBFS %6.0f Hz | this %d %6.1f dBFS %6.0f Hz\n",
						       ctx.name, mode.name, r, cut, o.steady, o.tailDb, o.tailHz, f.steady, f.tailDb, f.tailHz);
					}
					bool tone = f.steady && f.tailHz >= 15;
					if (tone && r <= 10) {
						bad++; // at low resonance: a bug, not self-oscillation
					}
					if (tone && compare) {
						// Louder than the original's (steady, or dying away in the same time) or from a lower resonance
						double over = f.tailDb - std::max(o.tailDb, -80.0);
						if (over > worst) {
							worst = over, worstRes = r, worstCut = cut;
						}
						bad += over > kMaxLouder;
					}
				}
				if (verbose && (rowLoudest[0] > -300 || rowLoudest[1] > -300)) {
					printf("  %-8s %-5s res %2d: original %6.1f dBFS %5.0f Hz, this %6.1f dBFS %5.0f Hz\n", ctx.name,
					       mode.name, r, rowLoudest[0], rowHz[0], rowLoudest[1], rowHz[1]);
				}
			}
			// Turned with the music on: the loudest window, at a few resonances
			double turned = -300;
			if (compare) {
				for (int r : {0, 25, 40, 50}) {
					for (double sec : {0.25, 2.0}) {
						double o = runCase(true, ctx, mode, r, 0, sec).loudestDb;
						double f = runCase(false, ctx, mode, r, 0, sec).loudestDb;
						turned = std::max(turned, f - o);
					}
				}
				bad += turned > kMaxLouder;
			}
			failures += bad > 0;
			printf("%-8s %-5s own tone: ", ctx.name, mode.name);
			if (compare) {
				printf("original from res %2d, loudest %6.1f dBFS at %5.0f Hz; ", onset[0], loudest[0], loudestHz[0]);
			}
			printf("this from %2d, loudest %6.1f dBFS at %5.0f Hz", onset[1], loudest[1], loudestHz[1]);
			if (compare) {
				printf("; at most %+5.1f dB over the original (res %2.0f, cutoff %2.0f); turned %+5.1f dB", worst,
				       worstRes, worstCut, turned);
			}
			printf("%s\n", bad ? "  FAIL" : "");
		}
	}
	if (failures) {
		printf("FAILED: in %d context/mode pairs the LPF's own tone is louder than the original firmware's (by more "
		       "than %.1f dB), starts from a lower resonance, or sounds at a resonance of 20 %% or less\n",
		       failures, kMaxLouder);
		return 1;
	}
	printf("ok: the LPF's own tone is no louder than in the original firmware and none below 20 %% resonance\n");
	return 0;
}
