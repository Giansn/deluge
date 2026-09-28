// The crossing guard (mastertune v18, Community features > Filter crossing guard, off by default): with both filters
// on and the HPF's cutoff near the LPF's or above it, their resonance peaks stack; the guard lowers both resonances
// there (FilterSet::setConfig(), guardStrength() / guardResonance()), smoothly with the cutoffs' distance.
//
// 1. peaks: every LPF mode (12 / 24 dB and drive ladders, SVF band / notch) x HPF mode (HP ladder, SVF band), LPF
//    cutoff 250 Hz / 1 kHz / 2.5 kHz, the HPF's at 0.5 to 2.8 times it (up to 2.5 kHz: its passband, at 4 times it,
//    below 10 kHz), resonance 10 / 25 / 35 (both filters the same), the voice's filters (mono, the filter gain on the
//    input) and the song's / a kit's (stereo, the gain on the output), route HPF -> LPF. From the impulse response
//    (-66 dBFS: every filter linear, where the peaks are highest; after the filters' switch-on fade), by FFT: a
//    filter's peak height is its largest gain over the level it passes (the LPF alone at fc / 4, the HPF alone at 4 fc;
//    its gain compensation in), the combination's over the level both pass at the resonances they then have (all
//    against both filters off). Excess: the combination's height over the higher single one's. Fails where, with the
//    guard on, the excess is above kMaxExcess with the HPF at 0.7 times the LPF's cutoff or above (the spec's range),
//    or above kMaxExcessBelow from 0.35 times it (where the guard fades in; further down, FULL's 0.25: printed).
//    Cases where a filter sings on its own (its impulse response doesn't die away: no peak height, its level the
//    saturation's) are counted apart. Printed: the excess off and on, and how the combination's absolute peak moved
//    (guard on minus off). FULL=1 (on the PC): 6 cutoffs from 120 Hz, 13 ratios (0.25 to 4), 10 resonances, also
//    LPF -> HPF and the parallel route (printed only: there the guard doesn't run; the sum at half level hardly
//    stacks). ONLY=<LPF>+<HPF>/<route> (e.g. LP24+HPladder/L2H): that pair only.
// 2. sweep: the HPF's cutoff swept through the LPF's (250 Hz to 4 kHz and back, 1 s each way, set per 128-sample block
//    as a knob or automation sets it), a 110 + 330 + 550 Hz tone going through, against the same with every sample's
//    own configuration (a per-sample-set reference): the difference above 2 kHz in dBc, guard on and off, and the
//    output's own energy above 2 kHz against the reference's (steps or clicks would add some). The guard's resonance
//    moves per sample with the coefficients: fails above kMaxZipper, unless the block-set output has no more energy
//    above 2 kHz than the per-sample-set one (within 0.1 dB). (With the HP ladder at resonance 25 the guard moves its
//    resonance across about 25, where the ladder switches between its anti-aliased and its plain tanh, at a block's
//    start in one and at a sample in the other: -53 to -66 dBc of difference, no energy added; a turned or automated
//    resonance crosses it the same way, filter_neutral's "HPL reso": -38 dBc.)
// 3. untouched: the guard on where it mustn't act renders bit for bit as off (one filter on, both resonances 0, the
//    cutoffs far apart). (Off itself doesn't run at all: filter_neutral's static hash with REF= the tree before it.)
// 4. strength: guardStrength() / guardResonance() themselves: 0 outside, continuous, the resonance monotonic.
// 5. heights: the guard's peak-height tables (FilterSet::guardPeaks()) against the filters measured alone here.
// Arguments: the part (peaks, sweep, untouched, strength, heights or all); VERBOSE=1 (or "verbose" as the 2nd): every case.
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
constexpr double kLevelDb = -66; // the impulse's height: every filter linear (the drive ladder's bass compensation limits from -54 dBFS)
constexpr double kMaxExcess = 1.0;      // dB, HPF cutoff >= 0.7 x LPF's
constexpr double kMaxExcessBelow = 2.5; // dB, further down (the guard fades in from 2 octaves below)
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
			int32_t x = (int32_t)std::clamp(std::llround(v), -2147483648ll, 2147483647ll);
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
// The magnitude response in dB from the impulse response: an FFT of it (0.67 Hz a bin)
constexpr int kFftLength = kIrLength;
struct Spectrum {
	std::vector<double> db; // bins 0 .. kFftLength / 2
	double at(double f) const {  // (linear between bins)
		double x = f * kFftLength / kFs;
		int i = std::clamp((int)x, 0, kFftLength / 2 - 1);
		return db[i] + (db[i + 1] - db[i]) * (x - i);
	}
	// The largest between lo and hi (parabolic between the bins), its frequency in *where
	double max(double lo, double hi, double* where = nullptr) const {
		int a = std::max(1, (int)(lo * kFftLength / kFs)), b = std::min(kFftLength / 2 - 1, (int)(hi * kFftLength / kFs) + 1);
		int best = a;
		for (int i = a; i <= b; i++) {
			if (db[i] > db[best]) {
				best = i;
			}
		}
		double l = db[best - 1], m = db[best], r = db[best + 1], d = l - 2 * m + r;
		double off = d < 0 ? std::clamp(0.5 * (l - r) / d, -0.5, 0.5) : 0;
		if (where) {
			*where = (best + off) * kFs / kFftLength;
		}
		return m - 0.25 * (l - r) * off;
	}
};
Spectrum spectrum(const std::vector<double>& ir) {
	std::vector<double> re(kFftLength, 0.0), im(kFftLength, 0.0);
	std::copy(ir.begin(), ir.end(), re.begin());
	for (int i = 1, j = 0; i < kFftLength; i++) { // bit reversal
		int bit = kFftLength >> 1;
		for (; j & bit; bit >>= 1) {
			j ^= bit;
		}
		j ^= bit;
		if (i < j) {
			std::swap(re[i], re[j]);
		}
	}
	for (int len = 2; len <= kFftLength; len <<= 1) {
		const double ang = -2 * M_PI / len;
		const double wr = std::cos(ang), wi = std::sin(ang);
		for (int i = 0; i < kFftLength; i += len) {
			double cr = 1, ci = 0;
			for (int k = 0; k < len / 2; k++) {
				const int u = i + k, v = u + len / 2;
				const double tr = re[v] * cr - im[v] * ci, ti = re[v] * ci + im[v] * cr;
				re[v] = re[u] - tr, im[v] = im[u] - ti;
				re[u] += tr, im[u] += ti;
				const double t = cr * wr - ci * wi;
				ci = cr * wi + ci * wr;
				cr = t;
			}
		}
	}
	Spectrum s;
	s.db.resize(kFftLength / 2 + 1);
	for (int i = 0; i <= kFftLength / 2; i++) {
		s.db[i] = 10 * std::log10(std::max(re[i] * re[i] + im[i] * im[i], 1e-30));
	}
	return s;
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

// A single filter's spectrum, the level it passes (pass, dB) and whether it sings on its own (its impulse response
// doesn't die away: the last 4096 samples within 40 dB of the first; a peak height without a limit)
struct Single {
	Spectrum s;
	double pass;
	bool sings;
};
Single single(const Cfg& c, double passHz) {
	FilterSet::crossingGuard = false; // (a single filter: the guard doesn't act anyway)
	const std::vector<double> ir = impulseResponse(c);
	double head = 0, tail = 0;
	for (int i = 0; i < 4096; i++) {
		head += ir[i] * ir[i];
		tail += ir[kIrLength - 4096 + i] * ir[kIrLength - 4096 + i];
	}
	Single r{spectrum(ir), 0, tail > head * 1e-4};
	r.pass = r.s.at(passHz);
	return r;
}
// The resonance setConfig() gives a filter with the guard on (the same arithmetic: quickLog of the cutoff params)
q31_t guarded(q31_t resonance, FilterMode mode, int32_t lpFreq, int32_t hpFreq) {
	const float octaves = (float)(quickLog(hpFreq) - quickLog(lpFreq)) * (1.0f / 33554432.0f);
	return FilterSet::guardResonance(resonance, FilterSet::guardStrength(octaves), FilterSet::guardPeaks(mode));
}

// Every LPF mode x HPF mode x route x context, the worst over the cutoffs, ratios and resonances
struct Worst {
	double off = -1e9, on = -1e9, onBelow = -1e9, farBelow = -1e9, absUp = -1e9, absDown = 1e9;
	double at[3] = {};
	int cases = 0, skipped = 0;
};
int peaks() {
	const FilterMode lps[] = {FilterMode::TRANSISTOR_12DB, FilterMode::TRANSISTOR_24DB,
	                          FilterMode::TRANSISTOR_24DB_DRIVE, FilterMode::SVF_BAND, FilterMode::SVF_NOTCH};
	const FilterMode hps[] = {FilterMode::HPLADDER, FilterMode::SVF_BAND};
	constexpr int nL = 5, nH = 2;
	// FULL=1 (on the PC): a finer grid. The HPF's cutoff up to 2.5 kHz: its passband (at 4 times it) below 10 kHz,
	// where the level it passes is still its own (above, the HP ladder's anti-aliasing and the SVF's top octave)
	const bool full = getenv("FULL") != nullptr;
	// (On the Deluge's CPU in the emulator, ARM=1, about 200 times slower: 1 kHz only, the guard's full range)
#ifdef __arm__
	const std::vector<double> fcs = {1000};
	const std::vector<double> ratios = {0.5, 0.7, 1.0, 1.4};
#else
	const std::vector<double> fcs = full ? std::vector<double>{120, 250, 500, 1000, 1800, 2500}
	                                     : std::vector<double>{250, 1000, 2500};
	const std::vector<double> ratios = full ? std::vector<double>{0.25, 0.35, 0.5, 0.6, 0.7, 0.85, 1.0, 1.2, 1.4, 1.7,
	                                                              2.0, 2.8, 4.0}
	                                        : std::vector<double>{0.5, 0.7, 1.0, 1.4, 2.8};
#endif
	const std::vector<double> resonances = full ? std::vector<double>{5, 10, 15, 20, 25, 30, 35, 40, 45, 50}
	                                            : std::vector<double>{10, 25, 35};
	// (By default the route HPF -> LPF; FULL: also LPF -> HPF, and the parallel route, where the guard doesn't run)
	const FilterRoute routes[] = {FilterRoute::HIGH_TO_LOW, FilterRoute::LOW_TO_HIGH, FilterRoute::PARALLEL};
	const char* routeNames[] = {"H2L", "L2H", "par"};
	const int nRoutes = full ? 3 : 1;
	Worst worst[3][nL][nH][2];
	printf("peaks at %.0f dBFS: excess = the combination's peak over the level it passes, against the higher single "
	       "filter's over its own (dB); abs: the combination's absolute peak, guard on minus off\n",
	       kLevelDb);
	for (int g = 0; g < 2; g++) {
		// Both filters off: the level the filter gain alone gives (0.801, -1.93 dB, FilterSet::setConfig()). Each
		// single's pass level has it in once, the combination's too: taken out of the singles' sum once
		Cfg none;
		none.global = g;
		const double offDb = single(none, 1000).pass;
		for (double fc : fcs) {
			const int32_t lpFreq = paramForHz(kLpNeutral, fc);
			for (double res : resonances) {
				std::vector<Single> sL;
				for (FilterMode lpm : lps) {
					Cfg c;
					c.lpf = lpm, c.global = g, c.lpFreq = lpFreq, c.lpRes = resParam(res);
					sL.push_back(single(c, std::max(20.0, fc / 4)));
				}
				for (double ratio : ratios) {
					const double hpHz = fc * ratio;
					if (hpHz > 2600) {
						continue;
					}
					const int32_t hpFreq = paramForHz(kHpNeutral, hpHz);
					const double lo = std::max(20.0, std::min(fc, hpHz) / 4), hi = std::min(20000.0, std::max(fc, hpHz) * 4);
					for (int hi_ = 0; hi_ < nH; hi_++) {
						Cfg ch;
						ch.hpf = hps[hi_], ch.global = g, ch.hpFreq = hpFreq, ch.hpRes = resParam(res);
						const Single sH = single(ch, hpHz * 4);
						const double hH = sH.s.max(lo, hi) - sH.pass;
						// The HPF alone at the resonance the guard gives it: the level it passes then
						Cfg chg = ch;
						chg.hpRes = guarded(ch.hpRes, hps[hi_], lpFreq, hpFreq);
						const double passHg = chg.hpRes == ch.hpRes ? sH.pass : single(chg, hpHz * 4).pass;
						for (int li = 0; li < nL; li++) {
							const double hL = sL[li].s.max(lo, hi) - sL[li].pass;
							Cfg clg;
							clg.lpf = lps[li], clg.global = g, clg.lpFreq = lpFreq;
							clg.lpRes = guarded(resParam(res), lps[li], lpFreq, hpFreq);
							const double passLg =
							    clg.lpRes == resParam(res) ? sL[li].pass : single(clg, std::max(20.0, fc / 4)).pass;
							for (int ri = 0; ri < nRoutes; ri++) {
								// ONLY=<LPF mode>+<HPF mode>/<route> (e.g. LP24+HPladder/L2H): that pair only
								if (const char* only = getenv("ONLY")) {
									char name[64];
									snprintf(name, sizeof name, "%s+%s/%s", lpName(lps[li]), hpName(hps[hi_]), routeNames[ri]);
									if (strcmp(only, name) != 0) {
										continue;
									}
								}
								Cfg c = ch;
								c.lpf = lps[li], c.lpFreq = lpFreq, c.lpRes = resParam(res);
								c.route = routes[ri];
								double m[2];
								for (int on = 0; on < 2; on++) {
									FilterSet::crossingGuard = on;
									m[on] = spectrum(impulseResponse(c)).max(lo, hi);
								}
								FilterSet::crossingGuard = false;
								const bool guardRuns = ri != 2; // (the parallel route: no guard)
								// The combination's peak over the level it passes (the singles' pass levels at the
								// resonances it has), against the higher single one's over its own
								const double eOff = m[0] - (sL[li].pass + sH.pass - offDb) - std::max(hL, hH);
								const double eOn = m[1] - (guardRuns ? passLg + passHg : sL[li].pass + sH.pass) + offDb
								                   - std::max(hL, hH);
								Worst& w = worst[ri][li][hi_][g];
								const bool sings = sL[li].sings || sH.sings;
								if (verbose) {
									printf("  %s+%s %s %s fc %5.0f x%.2f res %2.0f: excess off %5.1f on %5.1f, abs %+5.1f "
									       "dB%s\n",
									       lpName(lps[li]), hpName(hps[hi_]), routeNames[ri], g ? "song" : "voice", fc,
									       ratio, res, eOff, eOn, m[1] - m[0], sings ? " (a filter sings alone)" : "");
								}
								if (sings) { // no peak height to compare with (and its level is the saturation's)
									w.skipped++;
									continue;
								}
								w.absUp = std::max(w.absUp, m[1] - m[0]);
								w.absDown = std::min(w.absDown, m[1] - m[0]);
								w.cases++;
								w.off = std::max(w.off, eOff);
								if (ratio >= 0.7 - 1e-9) {
									if (eOn > w.on) {
										w.on = eOn, w.at[0] = fc, w.at[1] = ratio, w.at[2] = res;
									}
								}
								else if (ratio >= 0.35 - 1e-9) { // (where the guard acts: from 1.5 octaves below)
									w.onBelow = std::max(w.onBelow, eOn);
								}
								else {
									w.farBelow = std::max(w.farBelow, eOn);
								}
							}
						}
					}
				}
			}
		}
	}
	int failures = 0;
	for (int ri = 0; ri < nRoutes; ri++) {
		for (int li = 0; li < nL; li++) {
			for (int hi_ = 0; hi_ < nH; hi_++) {
				for (int g = 0; g < 2; g++) {
					const Worst& w = worst[ri][li][hi_][g];
					const bool checked = ri != 2;
					const bool fail = checked && (w.on > kMaxExcess || w.onBelow > kMaxExcessBelow);
					failures += fail;
					char far[32] = "";
					if (w.farBelow > -1e8) {
						snprintf(far, sizeof far, " (x0.25: %.1f)", w.farBelow);
					}
					printf("%s %-8s + %-8s %s %-5s excess off up to %5.1f, on %5.1f (x0.7 and up; worst at %.0f Hz x%.2f res "
					       "%.0f), %5.1f at x0.35 to x0.7%s; abs %+5.1f to %+5.1f dB; %d cases (+%d where a filter sings "
					       "alone)%s\n",
					       fail ? "FAIL" : (checked ? "ok  " : "    "), lpName(lps[li]), hpName(hps[hi_]), routeNames[ri],
					       g ? "song" : "voice", w.off, w.on, w.at[0], w.at[1], w.at[2], w.onBelow, far, w.absDown, w.absUp,
					       w.cases, w.skipped, checked ? "" : " (no guard in parallel: printed only)");
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
#ifdef __arm__
	const FilterMode lps[] = {FilterMode::TRANSISTOR_24DB}; // (ARM=1: the 24 dB ladder only)
#else
	const FilterMode lps[] = {FilterMode::TRANSISTOR_24DB, FilterMode::TRANSISTOR_12DB, FilterMode::SVF_BAND};
#endif
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
					double z[2], own[2];
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
						own[on] = highBandDb(block) - highBandDb(sample);
					}
					// Fails above kMaxZipper, unless the block-set output has no more energy above 2 kHz than the
					// per-sample-set one (within 0.1 dB): then the difference is where each switches the HP ladder's
					// saturation (its anti-aliased tanh above 900000000 of processed resonance, about 25 of 50; per block
					// in one, at a sample in the other), not steps (filter_neutral's rule for a moved resonance)
					const bool fail = z[1] > kMaxZipper && own[1] > 0.1;
					failures += fail;
					printf("%s sweep %s+%s %s res %2.0f: HPF 250 Hz <-> 4 kHz through the LPF's 1 kHz, block-set against "
					       "per-sample-set above 2 kHz: guard on %6.1f dBc, off %6.1f; the output's own above 2 kHz against the "
					       "per-sample-set one's: on %+5.2f dB, off %+5.2f\n",
					       fail ? "FAIL" : "ok  ", lpName(lpm), hpName(hpm), g ? "song " : "voice", res, z[1], z[0], own[1],
					       own[0]);
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
	// 0 outside -2 .. 3 octaves, 1 from -0.5 to 0.5, continuous (no step bigger than its slope allows)
	double prev = FilterSet::guardStrength(-4.0f), maxStep = 0;
	for (double o = -4.0; o <= 4.0; o += 1.0 / 1024) {
		double s = FilterSet::guardStrength((float)o);
		maxStep = std::max(maxStep, std::fabs(s - prev));
		prev = s;
	}
	bool ok = FilterSet::guardStrength(-2.01f) == 0 && FilterSet::guardStrength(3.01f) == 0
	          && FilterSet::guardStrength(0.0f) == 1 && FilterSet::guardStrength(-0.5f) == 1
	          && FilterSet::guardStrength(0.5f) == 1 && maxStep < 2.0 / 1024;
	failures += !ok;
	printf("%s strength: 0 below -2 and above 3 octaves, 1 from -0.5 to 0.5, largest step per 1/1024 octave %.5f\n",
	       ok ? "ok  " : "FAIL", maxStep);
	// The resonance: monotonic in the knob's at every strength, unchanged at strength 0, and at full strength where
	// the mode's peak is half as high (+ 0.5 dB) by its own table
	const FilterMode modes[] = {FilterMode::TRANSISTOR_12DB, FilterMode::TRANSISTOR_24DB,
	                            FilterMode::TRANSISTOR_24DB_DRIVE, FilterMode::SVF_BAND, FilterMode::HPLADDER};
	const char* names[] = {"LP12", "LP24", "drive", "SVF", "HPladder"};
	for (int mi = 0; mi < 5; mi++) {
		const float* peaks = FilterSet::guardPeaks(modes[mi]);
		bool mono = true, same = true;
		for (double st = 0; st <= 1.0001; st += 1.0 / 16) {
			q31_t last = -1;
			for (int d = 0; d <= 50 * 16; d++) {
				q31_t r = resParam(d / 16.0);
				q31_t g = FilterSet::guardResonance(r, (float)st, peaks);
				mono &= g >= last && g <= r;
				last = g;
				if (st == 0) {
					same &= g == r;
				}
			}
		}
		ok = mono && same && peaks != nullptr;
		failures += !ok;
		printf("%s strength: %-8s the guarded resonance monotonic and never above the knob's (%s), unchanged at strength "
		       "0 (%s); at full: 10 -> %.1f, 20 -> %.1f, 30 -> %.1f, 40 -> %.1f, 50 -> %.1f\n",
		       ok ? "ok  " : "FAIL", names[mi], mono ? "yes" : "no", same ? "yes" : "no",
		       FilterSet::guardResonance(resParam(10), 1, peaks) / 10737418.0,
		       FilterSet::guardResonance(resParam(20), 1, peaks) / 10737418.0,
		       FilterSet::guardResonance(resParam(30), 1, peaks) / 10737418.0,
		       FilterSet::guardResonance(resParam(40), 1, peaks) / 10737418.0,
		       FilterSet::guardResonance(resParam(50), 1, peaks) / 10737418.0);
	}
	return failures;
}
// The firmware's peak-height tables (FilterSet::guardPeaks()) against the filters' own, measured here: each mode
// alone at 250 Hz, 1 kHz and 2.5 kHz, voice and song, resonance 0 to 50 in steps of 2.5, where it doesn't sing on its
// own and peaks at most kNearSinging dB. Fails where a table value is more than kMaxTableError off
constexpr double kMaxTableError = 2.0; // dB (the tables are the 1 kHz ones; 250 Hz / 2.5 kHz within 1.7)
constexpr double kNearSinging = 25; // dB: compared up to this peak height
int heights() {
	const FilterMode modes[] = {FilterMode::TRANSISTOR_12DB, FilterMode::TRANSISTOR_24DB,
	                            FilterMode::TRANSISTOR_24DB_DRIVE, FilterMode::SVF_BAND, FilterMode::HPLADDER};
	const char* names[] = {"LP12", "LP24", "drive", "SVF", "HPladder"};
	int failures = 0;
	for (int mi = 0; mi < 5; mi++) {
		const float* table = FilterSet::guardPeaks(modes[mi]);
		const bool hp = modes[mi] == FilterMode::HPLADDER;
		double worst = 0, worstAt = 0, singsFrom = 50;
		int n = 0;
		for (int g = 0; g < 2; g++) {
#ifdef __arm__
			for (double fc : {1000.0}) { // (ARM=1: 1 kHz only)
#else
			for (double fc : {250.0, 1000.0, 2500.0}) {
#endif
				for (int step = 0; step <= 20; step++) {
					Cfg c;
					c.global = g;
					if (hp) {
						c.hpf = modes[mi], c.hpFreq = paramForHz(kHpNeutral, fc), c.hpRes = resParam(step * 2.5);
					}
					else {
						c.lpf = modes[mi], c.lpFreq = paramForHz(kLpNeutral, fc), c.lpRes = resParam(step * 2.5);
					}
					const Single s = single(c, hp ? fc * 4 : fc / 4);
					if (s.sings) {
						singsFrom = std::min(singsFrom, step * 2.5);
						break;
					}
					const double h = s.s.max(fc / 4, std::min(20000.0, fc * 4)) - s.pass;
					if (h > kNearSinging) { // (there the peak moves by many dB with the cutoff and the context)
						break;
					}
					n++;
					if (std::fabs(h - table[step]) > std::fabs(worst)) {
						worst = h - table[step], worstAt = step * 2.5;
					}
				}
			}
		}
		const bool fail = std::fabs(worst) > kMaxTableError;
		failures += fail;
		printf("%s heights: %-8s table against measured (%d points; sings alone from resonance %.1f): largest "
		       "difference %+.2f dB at resonance %.1f (compared up to %.0f dB)\n",
		       fail ? "FAIL" : "ok  ", names[mi], n, singsFrom, -worst, worstAt, kNearSinging);
	}
	return failures;
}
} // namespace

int main(int argc, char** argv) {
	verbose = getenv("VERBOSE") != nullptr || (argc > 2 && !strcmp(argv[2], "verbose"));
	const char* only = argc > 1 && strcmp(argv[1], "all") ? argv[1] : nullptr;
	int failures = 0;
	if (only && !strcmp(only, "ring")) { // the resonance where each filter alone stops dying away
		for (FilterMode m : {FilterMode::TRANSISTOR_12DB, FilterMode::TRANSISTOR_24DB, FilterMode::SVF_BAND, FilterMode::HPLADDER}) {
			for (int g = 0; g < 2; g++) {
				for (double fc : {200.0, 1000.0, 5000.0}) {
					for (double res = 20; res <= 50; res += 1) {
						Cfg c;
						c.global = g;
						if (m == FilterMode::HPLADDER) {
							c.hpf = m, c.hpFreq = paramForHz(kHpNeutral, fc), c.hpRes = resParam(res);
						}
						else {
							c.lpf = m, c.lpFreq = paramForHz(kLpNeutral, fc), c.lpRes = resParam(res);
						}
						auto ir = impulseResponse(c);
						double head = 0, tail = 0;
						for (int i = 0; i < 4096; i++) head += ir[i] * ir[i], tail += ir[kIrLength - 4096 + i] * ir[kIrLength - 4096 + i];
						double t = 10 * std::log10(std::max(tail, 1e-30) / head);
						if (t > -60) {
							printf("%s %s fc %.0f: tail %.0f dB from res %.0f\n", m == FilterMode::HPLADDER ? "HPladder" : lpName(m), g ? "song" : "voice", fc, t, res);
							break;
						}
					}
				}
			}
		}
		return 0;
	}
	if (!only || !strcmp(only, "strength")) {
		failures += strength();
	}
	if (!only || !strcmp(only, "heights")) {
		failures += heights();
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
