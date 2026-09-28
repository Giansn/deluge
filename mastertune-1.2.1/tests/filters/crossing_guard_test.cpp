// The crossing guard (mastertune v18, Community features > Filter crossing guard, off by default): with both filters
// on and the HPF's cutoff near the LPF's or above it, their resonance peaks stack; the guard lowers both resonances
// there (FilterSet::setConfig(), guardStrength() / guardResonance()), smoothly with the cutoffs' distance.
//
// 1. peaks: every LPF mode but drive (12 / 24 dB ladders, SVF band / notch) x HPF mode (HP ladder, SVF band), LPF
//    cutoff 200 Hz / 1 kHz / 5 kHz, the HPF's at 0.25 to 4 times it, resonance 10 to 50 (both filters the same), the
//    voice's filters (mono, the filter gain on the input) and the song's / a kit's (stereo, the gain on the output),
//    route HPF -> LPF, at -48 dBFS (nothing saturates: the highest peaks). The response from an impulse (after the
//    filters' switch-on fade), by DFT: a filter's peak height is its largest gain over the level it passes (the LPF
//    alone at fc / 4, the HPF alone at 4 fc up to 8 kHz; its gain compensation in both), the combination's over
//    both of those. Excess: the combination's height over the larger single one's. Fails where, with the guard on,
//    the excess is above kMaxExcess with the HPF at 0.7 times the LPF's cutoff or above (the spec's range), or above
//    kMaxExcessBelow further down. Printed per mode pair: the largest excess off and on, and how the combination's
//    absolute peak level moved (the guard lowers the resonance and with it the filters' gain compensation).
//    The parallel route and the drive ladder: printed only (the guard runs there too, except with drive).
// 2. sweep: the HPF's cutoff swept through the LPF's (2 octaves below to 2 above and back, 1 s each way, set per
//    128-sample block as a knob or automation sets it), a 110 + 330 + 550 Hz tone going through, guard on against
//    the same with every sample's own configuration (a per-sample-set reference; the resonance then follows the
//    cutoff per sample): the difference above 2 kHz in dBc. The guard's resonance moves per sample with the
//    coefficients (no steps): fails above kMaxZipper, as filter_neutral's zipper.
// 3. untouched: the guard on where it mustn't act renders bit for bit as off (one filter on, the drive ladder, both
//    resonances 0, the cutoffs far apart). (Off itself doesn't run at all: filter_neutral's static hash with REF= the
//    tree before it.)
// 4. strength: guardStrength() / guardResonance() themselves: 0 outside, continuous, the resonance monotonic.
// Arguments: the part (peaks, sweep, untouched, strength or all); VERBOSE=1 (or "verbose" as the 2nd): every case.
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
constexpr double kLevelDb = -48;
constexpr double kMaxExcess = 1.0;      // dB, HPF cutoff >= 0.7 x LPF's
constexpr double kMaxExcessBelow = 2.0; // dB, further down (the guard fades in from 1.5 octaves below)
constexpr double kMaxZipper = -55;      // dBc above 2 kHz
bool verbose = false;

int32_t knobValue(double pos) { return (int32_t)std::clamp(pos * 33554432.0, -2147483648.0, 2147483647.0); }
int32_t freqParam(int32_t neutral, int32_t knob) { return getExp(neutral, knob >> 2); }
// The resonance's param from the display's 0 to 50, as the knob makes it
int32_t resParam(double d) {
	int32_t knob = knobValue(d * 128.0 / 50 - 64);
	return lshiftAndSaturate<3>(multiply_32x32_rshift32((knob >> 2) + 536870912, 25 * 10737418));
}
constexpr int32_t kLpNeutral = 2000000, kHpNeutral = 2672947;

// Nominal cutoff of a frequency param: tan(pi f / fs) = tannedFrequency / 2^28 (Filter::curveFrequency())
double paramHz(int32_t param) {
	LpLadderFilter f;
	f.configure(param, 0, FilterMode::TRANSISTOR_24DB, 0, 1 << 28);
	return std::atan(f.tannedFrequency / 268435456.0) * kFs / M_PI;
}
int32_t paramForHz(int32_t neutral, double hz) {
	double lo = -64, hi = 64;
	for (int i = 0; i < 50; i++) {
		double m = (lo + hi) / 2;
		(paramHz(freqParam(neutral, knobValue(m))) < hz ? lo : hi) = m;
	}
	return freqParam(neutral, knobValue((lo + hi) / 2));
}

struct Cfg {
	FilterMode lpf = FilterMode::OFF, hpf = FilterMode::OFF;
	int32_t lpFreq = 0, hpFreq = 0, lpRes = 0, hpRes = 0;
	FilterRoute route = FilterRoute::HIGH_TO_LOW;
	bool global = false;
};

// One block through a FilterSet as the voice / the song's filters run it, the filter gain (setConfig()'s over its
// input) as the firmware applies it since v18: a voice's on its input (the oscillators' amplitudes), moving per sample
// from the last block's; the song's / a kit's on the output, the filters' part of it moving per sample (rampGain)
struct Runner {
	FilterSet fs;
	bool global;
	double lastG = -1;
	explicit Runner(bool g) : global(g) {
		fs.reset();
		fs.setConfig(0, 0, FilterMode::OFF, 0, 0, 0, FilterMode::OFF, 0, 1 << 28, FilterRoute::HIGH_TO_LOW, false,
		             nullptr);
	}
	// in: n (<= kBlock) samples, full scale 1; out: the same, the filter gain applied as the caller does
	void block(const Cfg& c, const double* in, double* out, int n) {
		static int32_t buf[kBlock * 2];
		const int32_t gainIn = global ? 167763968 : 134217728 << 1;
		const int32_t gain = fs.setConfig(c.lpFreq, c.lpRes, c.lpf, 0, c.hpFreq, c.hpRes, c.hpf, 0, gainIn, c.route,
		                                  false, nullptr);
		const double g = (double)gain / gainIn;
		const double from = lastG < 0 ? g : lastG;
		lastG = g;
		for (int i = 0; i < n; i++) {
			double v = in[i] * (global ? 1 : from + (g - from) * (i + 1) / n) * 2147483648.0;
			int32_t x = (int32_t)std::clamp(std::lround(v), -2147483648l, 2147483647l);
			if (global) {
				buf[2 * i] = buf[2 * i + 1] = x;
			}
			else {
				buf[i] = x;
			}
		}
		if (global) {
			fs.renderLongStereo(buf, buf + 2 * n, HpLadderFilter::kSaturationGlobal, LpLadderFilter::kSaturationGlobal,
			                    true);
		}
		else {
			fs.renderLong(buf, buf + n, n, 1, HpLadderFilter::kSaturationVoice, LpLadderFilter::kSaturationVoice);
		}
		for (int i = 0; i < n; i++) {
			out[i] = (global ? buf[2 * i] : buf[i]) / 2147483648.0 * (global ? g : 1);
		}
	}
};

// The impulse response (after 3 silent blocks: the switch-on fade over), height 10^(kLevelDb / 20), divided by it
constexpr int kIrLength = 32768;
std::vector<double> impulseResponse(const Cfg& c) {
	Runner r(c.global);
	std::vector<double> in(kBlock, 0.0), out(kBlock);
	for (int b = 0; b < 3; b++) {
		r.block(c, in.data(), out.data(), kBlock);
	}
	const double a = std::pow(10, kLevelDb / 20);
	std::vector<double> ir(kIrLength);
	for (int s = 0; s < kIrLength; s += kBlock) {
		std::fill(in.begin(), in.end(), 0.0);
		if (s == 0) {
			in[0] = a;
		}
		r.block(c, in.data(), ir.data() + s, kBlock);
	}
	for (double& v : ir) {
		v /= a;
	}
	return ir;
}
// |H(f)| in dB from the impulse response (a rotating phasor, no trig per sample)
double gainDb(const std::vector<double>& ir, double f) {
	const double w = 2 * M_PI * f / kFs;
	const double cr = std::cos(w), ci = -std::sin(w);
	double pr = 1, pi = 0, re = 0, im = 0;
	for (size_t n = 0; n < ir.size(); n++) {
		re += ir[n] * pr;
		im += ir[n] * pi;
		const double t = pr * cr - pi * ci;
		pi = pr * ci + pi * cr;
		pr = t;
		if ((n & 1023) == 1023) { // renormalise the phasor
			const double m = 1 / std::sqrt(pr * pr + pi * pi);
			pr *= m;
			pi *= m;
		}
	}
	return 10 * std::log10(std::max(re * re + im * im, 1e-30));
}
// The largest gain between lo and hi (1/12 octave steps, then refined to 1/200 octave)
double maxGainDb(const std::vector<double>& ir, double lo, double hi, double* at = nullptr) {
	double best = -1e9, bf = lo;
	for (double f = lo; f <= hi; f *= std::pow(2, 1 / 12.0)) {
		double g = gainDb(ir, f);
		if (g > best) {
			best = g, bf = f;
		}
	}
	for (double st = 1 / 24.0; st > 1 / 200.0; st /= 2) {
		double a = gainDb(ir, bf * std::pow(2, -st)), b = gainDb(ir, bf * std::pow(2, st));
		if (a > best && a >= b) {
			best = a, bf *= std::pow(2, -st);
		}
		else if (b > best) {
			best = b, bf *= std::pow(2, st);
		}
	}
	if (at) {
		*at = bf;
	}
	return best;
}

const char* lpName(FilterMode m) {
	switch (m) {
	case FilterMode::TRANSISTOR_12DB: return "LP12";
	case FilterMode::TRANSISTOR_24DB: return "LP24";
	case FilterMode::TRANSISTOR_24DB_DRIVE: return "drive";
	case FilterMode::SVF_BAND: return "SVFband";
	case FilterMode::SVF_NOTCH: return "SVFnotch";
	default: return "off";
	}
}
const char* hpName(FilterMode m) { return m == FilterMode::HPLADDER ? "HPladder" : m == FilterMode::SVF_BAND ? "HPsvf" : "HPnotch"; }

struct PeakResult {
	double excessOff, excessOn, absOff, absOn; // excess: dB over the larger single; abs: the combination's peak (dB re in)
};
PeakResult peakCase(Cfg c, double lpHz, double hpHz) {
	Cfg lp = c, hp = c;
	lp.hpf = FilterMode::OFF;
	hp.lpf = FilterMode::OFF;
	const double lo = std::max(20.0, std::min(lpHz, hpHz) / 4), hi = std::min(20000.0, std::max(lpHz, hpHz) * 4);
	FilterSet::crossingGuard = false; // (a single filter: the guard doesn't act anyway)
	auto irL = impulseResponse(lp), irH = impulseResponse(hp);
	const double pL = gainDb(irL, std::max(20.0, lpHz / 4)), pH = gainDb(irH, std::min(8000.0, hpHz * 4));
	const double hSingle = std::max(maxGainDb(irL, lo, hi) - pL, maxGainDb(irH, lo, hi) - pH);
	PeakResult r;
	for (int on = 0; on < 2; on++) {
		FilterSet::crossingGuard = on;
		auto ir = impulseResponse(c);
		double m = maxGainDb(ir, lo, hi);
		(on ? r.excessOn : r.excessOff) = m - pL - pH - hSingle;
		(on ? r.absOn : r.absOff) = m;
	}
	FilterSet::crossingGuard = false;
	return r;
}

int peaks() {
	const FilterMode lps[] = {FilterMode::TRANSISTOR_12DB, FilterMode::TRANSISTOR_24DB, FilterMode::SVF_BAND,
	                          FilterMode::SVF_NOTCH, FilterMode::TRANSISTOR_24DB_DRIVE};
	const FilterMode hps[] = {FilterMode::HPLADDER, FilterMode::SVF_BAND};
	const double fcs[] = {200, 1000, 5000};
	const double ratios[] = {0.25, 0.35, 0.5, 0.7, 0.85, 1.0, 1.2, 1.4, 2.0, 2.8, 4.0};
	const double resonances[] = {10, 15, 25, 40, 50};
	const FilterRoute routes[] = {FilterRoute::HIGH_TO_LOW, FilterRoute::PARALLEL};
	int failures = 0;
	printf("peaks at %.0f dBFS: excess = the combination's peak height over the larger single one's (dB); "
	       "abs: the combination's absolute peak, guard on minus off\n",
	       kLevelDb);
	for (FilterRoute route : routes) {
		for (FilterMode lpm : lps) {
			for (FilterMode hpm : hps) {
				const bool checked = route == FilterRoute::HIGH_TO_LOW && lpm != FilterMode::TRANSISTOR_24DB_DRIVE;
				for (int g = 0; g < 2; g++) {
					double worstOff = -1e9, worstOn = -1e9, worstOnBelow = -1e9, absUp = -1e9, absDown = 1e9;
					double worstAt[3] = {};
					for (double fc : fcs) {
						for (double ratio : ratios) {
							if (fc * ratio > 16000) {
								continue;
							}
							for (double res : resonances) {
								Cfg c;
								c.lpf = lpm, c.hpf = hpm, c.route = route, c.global = g;
								c.lpFreq = paramForHz(kLpNeutral, fc);
								c.hpFreq = paramForHz(kHpNeutral, fc * ratio);
								c.lpRes = c.hpRes = resParam(res);
								PeakResult r = peakCase(c, fc, fc * ratio);
								if (verbose) {
									printf("  %s+%s %s %s fc %5.0f x%.2f res %2.0f: excess off %5.1f on %5.1f, abs %+5.1f dB\n",
									       lpName(lpm), hpName(hpm), route == FilterRoute::PARALLEL ? "par" : "H2L",
									       g ? "song" : "voice", fc, ratio, res, r.excessOff, r.excessOn,
									       r.absOn - r.absOff);
								}
								worstOff = std::max(worstOff, r.excessOff);
								if (ratio >= 0.7 - 1e-9) {
									if (r.excessOn > worstOn) {
										worstOn = r.excessOn, worstAt[0] = fc, worstAt[1] = ratio, worstAt[2] = res;
									}
								}
								else {
									worstOnBelow = std::max(worstOnBelow, r.excessOn);
								}
								absUp = std::max(absUp, r.absOn - r.absOff);
								absDown = std::min(absDown, r.absOn - r.absOff);
							}
						}
					}
					bool fail = checked && (worstOn > kMaxExcess || worstOnBelow > kMaxExcessBelow);
					failures += fail;
					printf("%s %-8s + %-8s %s %-5s excess off up to %5.1f, on %5.1f (x0.7 and up; at %.0f Hz x%.2f res %.0f), "
					       "%5.1f below; abs %+5.1f to %+5.1f dB%s\n",
					       fail ? "FAIL" : (checked ? "ok  " : "    "), lpName(lpm), hpName(hpm),
					       route == FilterRoute::PARALLEL ? "par" : "H2L", g ? "song" : "voice", worstOff, worstOn,
					       worstAt[0], worstAt[1], worstAt[2], worstOnBelow, absDown, absUp,
					       checked ? "" : " (printed only)");
				}
			}
		}
	}
	return failures;
}

// The largest difference above 2 kHz between a block-set sweep and the per-sample-set one (dBc)
double highBandDb(const std::vector<double>& x) {
	// 2nd order Butterworth HP at 2 kHz, twice (24 dB / octave)
	const double k = std::tan(M_PI * 2000 / kFs), q = M_SQRT1_2;
	const double norm = 1 / (1 + k / q + k * k);
	const double b0 = norm, b1 = -2 * norm, b2 = norm, a1 = 2 * (k * k - 1) * norm, a2 = (1 - k / q + k * k) * norm;
	double e = 0;
	double s[2][4] = {};
	for (size_t n = 0; n < x.size(); n++) {
		double v = x[n];
		for (int st = 0; st < 2; st++) {
			double y = b0 * v + b1 * s[st][0] + b2 * s[st][1] - a1 * s[st][2] - a2 * s[st][3];
			s[st][1] = s[st][0], s[st][0] = v, s[st][3] = s[st][2], s[st][2] = y;
			v = y;
		}
		if (n > 4410) {
			e += v * v;
		}
	}
	return 10 * std::log10(std::max(e, 1e-30));
}
int sweep() {
	const FilterMode lps[] = {FilterMode::TRANSISTOR_24DB, FilterMode::TRANSISTOR_12DB, FilterMode::SVF_BAND};
	const FilterMode hps[] = {FilterMode::HPLADDER, FilterMode::SVF_BAND};
	int failures = 0;
	const int n = (int)(2 * kFs) / kBlock * kBlock;
	for (FilterMode lpm : lps) {
		for (FilterMode hpm : hps) {
			for (int g = 0; g < 2; g++) {
				for (double res : {25.0, 50.0}) {
					Cfg c;
					c.lpf = lpm, c.hpf = hpm, c.global = g;
					c.lpFreq = paramForHz(kLpNeutral, 1000);
					c.lpRes = c.hpRes = resParam(res);
					const double lo = std::log2((double)paramForHz(kHpNeutral, 250)),
					             hi = std::log2((double)paramForHz(kHpNeutral, 4000));
					auto hpAt = [&](int i) { // up for 1 s, down for 1 s, in log frequency
						double t = (double)i / (n / 2);
						t = t <= 1 ? t : 2 - t;
						return (int32_t)std::exp2(lo + (hi - lo) * t);
					};
					std::vector<double> in(n), block(n), sample(n);
					for (int i = 0; i < n; i++) {
						double t = i / kFs;
						in[i] = std::pow(10, -30 / 20.0)
						        * (std::sin(2 * M_PI * 110 * t) + std::sin(2 * M_PI * 330 * t) + std::sin(2 * M_PI * 550 * t))
						        / 3;
					}
					// guard on and off: the zipper the sweep has (a block-set sweep's own) and what the guard adds
					double z[2];
					for (int on = 0; on < 2; on++) {
						FilterSet::crossingGuard = on;
						Runner rb(g), rs(g);
						for (int s = 0; s < n; s += kBlock) {
							c.hpFreq = hpAt(s + kBlock);
							rb.block(c, in.data() + s, block.data() + s, kBlock);
						}
						for (int s = 0; s < n; s++) {
							c.hpFreq = hpAt(s + 1);
							rs.block(c, in.data() + s, sample.data() + s, 1);
						}
						FilterSet::crossingGuard = false;
						std::vector<double> diff(n);
						for (int i = 0; i < n; i++) {
							diff[i] = block[i] - sample[i];
						}
						double ref = 0;
						for (int i = 4410; i < n; i++) {
							ref += sample[i] * sample[i];
						}
						z[on] = highBandDb(diff) - 10 * std::log10(ref);
					}
					// Fails above kMaxZipper, unless within 3 dB of the same sweep without the guard (the HP ladder's
					// own, in the song's filters: -51 to -54 dBc with and without it)
					const bool fail = z[1] > kMaxZipper && z[1] > z[0] + 3;
					failures += fail;
					printf("%s sweep %s+%s %s res %2.0f: HPF 250 Hz <-> 4 kHz through the LPF's 1 kHz, block-set against "
					       "per-sample-set above 2 kHz: guard on %6.1f dBc, off %6.1f\n",
					       fail ? "FAIL" : "ok  ", lpName(lpm), hpName(hpm), g ? "song " : "voice", res, z[1], z[0]);
				}
			}
		}
	}
	return failures;
}

uint64_t fnv(uint64_t h, const std::vector<double>& v) {
	for (double d : v) {
		int64_t x = std::llround(d * 2147483648.0);
		for (int i = 0; i < 8; i++) {
			h = (h ^ (uint8_t)(x >> (8 * i))) * 1099511628211ull;
		}
	}
	return h;
}
int untouched() {
	struct Case {
		const char* what;
		FilterMode lpf, hpf;
		double lpHz, hpHz, res;
	};
	const Case cases[] = {
	    {"LPF alone", FilterMode::TRANSISTOR_24DB, FilterMode::OFF, 1000, 1000, 50},
	    {"HPF alone", FilterMode::OFF, FilterMode::HPLADDER, 1000, 1000, 50},
	    {"drive ladder + HP ladder crossing", FilterMode::TRANSISTOR_24DB_DRIVE, FilterMode::HPLADDER, 1000, 1000, 50},
	    {"both resonances 0, crossing", FilterMode::TRANSISTOR_24DB, FilterMode::HPLADDER, 1000, 1000, 0},
	    {"HPF 3 octaves below", FilterMode::TRANSISTOR_24DB, FilterMode::HPLADDER, 2000, 250, 50},
	    {"HPF 3 octaves above", FilterMode::TRANSISTOR_12DB, FilterMode::SVF_BAND, 250, 2000, 50},
	};
	int failures = 0;
	for (const Case& k : cases) {
		for (int g = 0; g < 2; g++) {
			Cfg c;
			c.lpf = k.lpf, c.hpf = k.hpf, c.global = g;
			c.lpFreq = paramForHz(kLpNeutral, k.lpHz);
			c.hpFreq = paramForHz(kHpNeutral, k.hpHz);
			c.lpRes = c.hpRes = resParam(k.res);
			uint64_t h[2];
			for (int on = 0; on < 2; on++) {
				FilterSet::crossingGuard = on;
				h[on] = fnv(1469598103934665603ull, impulseResponse(c));
			}
			FilterSet::crossingGuard = false;
			failures += h[0] != h[1];
			printf("%s untouched: %s (%s): guard on %s off (%016llx)\n", h[0] == h[1] ? "ok  " : "FAIL", k.what,
			       g ? "song" : "voice", h[0] == h[1] ? "==" : "!=", (unsigned long long)h[1]);
		}
	}
	return failures;
}

int strength() {
	int failures = 0;
	// 0 outside -1.5 .. 2.5 octaves, 1 from -0.5 to 0.5, continuous (no step bigger than a slope allows)
	double prev = FilterSet::guardStrength(-3.0f), maxStep = 0;
	for (double o = -3.0; o <= 3.0; o += 1.0 / 1024) {
		double s = FilterSet::guardStrength((float)o);
		maxStep = std::max(maxStep, std::fabs(s - prev));
		prev = s;
	}
	bool ok = FilterSet::guardStrength(-1.6f) == 0 && FilterSet::guardStrength(2.6f) == 0
	          && FilterSet::guardStrength(0.0f) == 1 && FilterSet::guardStrength(-0.5f) == 1
	          && FilterSet::guardStrength(0.5f) == 1 && maxStep < 2.0 / 1024;
	failures += !ok;
	printf("%s strength: 0 below -1.5 and above 2.5 octaves, 1 from -0.5 to 0.5, largest step per 1/1024 octave %.5f\n",
	       ok ? "ok  " : "FAIL", maxStep);
	// The resonance: monotonic at every strength, unchanged at 0, at most the cap at full strength
	bool mono = true, capped = true, same = true;
	for (double s = 0; s <= 1.0001; s += 1.0 / 16) {
		q31_t last = -1;
		for (int d = 0; d <= 50 * 16; d++) {
			q31_t r = resParam(d / 16.0);
			q31_t g = FilterSet::guardResonance(r, (float)s);
			mono &= g >= last;
			last = g;
			if (s == 0) {
				same &= g == r;
			}
			if (s == 1) {
				capped &= g <= (q31_t)(13.5 * 10737418.0) + 64;
			}
		}
	}
	ok = mono && capped && same;
	failures += !ok;
	printf("%s strength: the guarded resonance monotonic in the knob's (%s), unchanged at strength 0 (%s), at most "
	       "13.5 of 50 at full (%s): 50 -> %.1f, 25 -> %.1f, 15 -> %.1f, 5 -> %.1f\n",
	       ok ? "ok  " : "FAIL", mono ? "yes" : "no", same ? "yes" : "no", capped ? "yes" : "no",
	       FilterSet::guardResonance(resParam(50), 1) / 10737418.0, FilterSet::guardResonance(resParam(25), 1) / 10737418.0,
	       FilterSet::guardResonance(resParam(15), 1) / 10737418.0, FilterSet::guardResonance(resParam(5), 1) / 10737418.0);
	return failures;
}
} // namespace

int main(int argc, char** argv) {
	verbose = getenv("VERBOSE") != nullptr || (argc > 2 && !strcmp(argv[2], "verbose"));
	const char* only = argc > 1 && strcmp(argv[1], "all") ? argv[1] : nullptr;
	int failures = 0;
	if (!only || !strcmp(only, "strength")) {
		failures += strength();
	}
	if (!only || !strcmp(only, "untouched")) {
		failures += untouched();
	}
	if (!only || !strcmp(only, "sweep")) {
		failures += sweep();
	}
	if (!only || !strcmp(only, "peaks")) {
		failures += peaks();
	}
	printf("%s: crossing guard, %d failed\n", failures ? "FAIL" : "ok", failures);
	return failures ? 1 : 0;
}
