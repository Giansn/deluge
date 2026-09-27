// The filters' transitions (mastertune v18 "filter neutral"): the firmware's FilterSet (dsp/filter) as a voice (a
// synth, mono; a kit row, stereo) and the song / a kit (GlobalEffectable, stereo) run it: the configuration is set once
// per 128-sample block (FilterSet::setConfig()), then the block rendered, the filter gain applied as the caller does (a
// voice's to its input, the oscillators' amplitudes: per sample from the last block's since v18, jumping before; the
// song's and a kit's to the output, flat for the block).
//
// 1. static: every LPF mode x HPF mode x route in every context, at two settings, the configuration set again every
//    block without change: a hash of the output. A configuration that doesn't change must render exactly as before
//    the per-sample coefficient ramps (compare the hash with the one of the source before them: run.sh with REF=).
// 2. zipper: the cutoff, resonance or morph automated (set per block, 0.5 s up and 0.5 s down) against the same with
//    the configuration set on every sample (a per-sample-updated reference), with a tone of 110, 330 and 550 Hz going
//    through. Printed: the difference, full band and above 2 kHz (where a smoothly moving filter puts nothing, steps
//    at the block rate do), in dB against the output (dBc), and the output's own energy above 2 kHz against the
//    per-sample one's. Fails where the difference above 2 kHz is above kMaxZipper, unless the output has at least 1 dB
//    less there than the per-sample one: the HPF's resonance moves in 256 steps (FilterSet::setConfig()), which the
//    per-sample one takes as clicks and the block-set one moves through in straight lines (v17: -35 to -60 dBc, the
//    drive ladder and the HP ladder -40; v18: -58 to -112).
// 3. clicks: a mode change, a route change and a filter switched off, with a 110 / 220 / 330 Hz tone going through
//    (no steps of its own): the largest step between two samples in the 20 ms after the change against the largest
//    in the 100 ms before it, and the energy above 4 kHz in the 20 ms after it against the same case without the
//    change (dB). Fails where the step is more than kMaxStep times the one before or above 4 kHz more than kMaxBurst
//    dB comes in (v17: switching off / changing the mode or route jumps, up to 20x the step and +30 dB; with the v18
//    crossfades within both). Without FILTERSET_FADES (the source before the crossfades) only printed.
//
// Arguments: the part (static, zipper, clicks or all), then verbose: every static case's hash.
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
constexpr double kMaxZipper = -55; // dBc above 2 kHz, the block-set configuration against the per-sample one
constexpr double kMaxStep = 2.5;   // the largest sample step after a change against the one before it
constexpr double kMaxBurst = 6;    // dB above 4 kHz after a change against the case without it
bool verbose = false;

#ifdef HPF_SATURATION_PER_CONTEXT
constexpr int32_t kHpSatVoice = HpLadderFilter::kSaturationVoice, kHpSatGlobal = HpLadderFilter::kSaturationGlobal;
#else
constexpr int32_t kHpSatVoice = 1, kHpSatGlobal = 1;
#endif
#ifdef LPF_SATURATION_PER_CONTEXT
constexpr int32_t kLpSatVoice = LpLadderFilter::kSaturationVoice, kLpSatGlobal = LpLadderFilter::kSaturationGlobal;
#else
constexpr int32_t kLpSatVoice = 1, kLpSatGlobal = 1;
#endif

// Params from a knob position as setupFilterSetConfig() and the patcher make them, the display's 0 to 50
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

enum class Ctx { SYNTH, KIT_ROW, KIT };
const char* ctxName(Ctx c) {
	return c == Ctx::SYNTH ? "synth" : c == Ctx::KIT_ROW ? "kit row" : "kit";
}
bool isStereo(Ctx c) {
	return c != Ctx::SYNTH;
}
bool isGlobal(Ctx c) {
	return c == Ctx::KIT;
}
int32_t gainIn(Ctx c) {
	return isGlobal(c) ? 167763968 : 134217728 << 1;
}

const char* modeName(FilterMode m) {
	switch (m) {
	case FilterMode::TRANSISTOR_12DB:
		return "LP12";
	case FilterMode::TRANSISTOR_24DB:
		return "LP24";
	case FilterMode::TRANSISTOR_24DB_DRIVE:
		return "Drive";
	case FilterMode::SVF_BAND:
		return "SVFb";
	case FilterMode::SVF_NOTCH:
		return "SVFn";
	case FilterMode::HPLADDER:
		return "HPL";
	default:
		return "off";
	}
}
const char* routeName(FilterRoute r) {
	return r == FilterRoute::HIGH_TO_LOW ? "H2L" : r == FilterRoute::LOW_TO_HIGH ? "L2H" : "par";
}

struct Settings { // display units (0 to 50)
	FilterMode lpfMode = FilterMode::OFF, hpfMode = FilterMode::OFF;
	FilterRoute route = FilterRoute::HIGH_TO_LOW;
	double cut = 25, res = 0, morph = 0, hcut = 10, hres = 0, hmorph = 0;
};

// A filter as its caller runs it, one block at a time; out: the output with the gain as the caller applies it,
// relative to full scale
struct Runner {
	Ctx ctx;
	FilterSet* fs;
	int32_t lastGain = 0;
	bool first = true;
	explicit Runner(Ctx c) : ctx(c) {
		fs = new FilterSet();
		memset((void*)fs, 0, sizeof(FilterSet));
		fs->reset();
	}
	~Runner() { delete fs; }
	int32_t configure(const Settings& s) {
		return fs->setConfig(freqParam(s.cut), linearParam(s.res), s.lpfMode, linearParam(s.morph), freqParam(s.hcut),
		                     linearParam(s.hres), s.hpfMode, linearParam(s.hmorph), gainIn(ctx), s.route, false,
		                     nullptr);
	}
	// in: interleaved stereo, n frames; appends n frames (interleaved stereo; mono contexts: both the same)
	void block(const Settings& s, const float* in, int n, std::vector<double>& out, std::vector<int32_t>* raw) {
		int32_t gain = configure(s);
		bool st = isStereo(ctx);
		std::vector<int32_t> buf(st ? 2 * n : n);
		double g0 = first ? gain : lastGain, g1 = gain;
		for (int i = 0; i < n; i++) {
			double g = 1;
			if (!isGlobal(ctx)) {
#ifdef FILTERSET_RAMP_GAIN
				g = (g0 + (g1 - g0) * (i + 1) / n) / gainIn(ctx); // the voice moves its amplitudes per sample
#else
				g = g1 / gainIn(ctx); // ... jumped where the filter gain changed
#endif
			}
			if (st) {
				buf[2 * i] = (int32_t)std::lround(in[2 * i] * 2147483648.0 * g);
				buf[2 * i + 1] = (int32_t)std::lround(in[2 * i + 1] * 2147483648.0 * g);
			}
			else {
				buf[i] = (int32_t)std::lround(in[2 * i] * 2147483648.0 * g);
			}
		}
		if (st) {
#ifdef FILTERSET_RAMP_GAIN
			fs->renderLongStereo(buf.data(), buf.data() + 2 * n, isGlobal(ctx) ? kHpSatGlobal : kHpSatVoice,
			                     isGlobal(ctx) ? kLpSatGlobal : kLpSatVoice, isGlobal(ctx));
#else
			fs->renderLongStereo(buf.data(), buf.data() + 2 * n, isGlobal(ctx) ? kHpSatGlobal : kHpSatVoice,
			                     isGlobal(ctx) ? kLpSatGlobal : kLpSatVoice);
#endif
		}
		else {
			fs->renderLong(buf.data(), buf.data() + n, n, 1, kHpSatVoice, kLpSatVoice);
		}
		double og = isGlobal(ctx) ? (double)gain / gainIn(ctx) : 1.0;
		for (int i = 0; i < n; i++) {
			double l = st ? buf[2 * i] : buf[i], r = st ? buf[2 * i + 1] : buf[i];
			out.push_back(l * og / 2147483648.0);
			out.push_back(r * og / 2147483648.0);
		}
		if (raw) {
			raw->insert(raw->end(), buf.begin(), buf.end());
			raw->push_back(gain);
		}
		lastGain = gain;
		first = false;
	}
};

struct Rng {
	uint32_t s = 22222;
	float next() {
		s = s * 1664525u + 1013904223u;
		return (int32_t)s / 2147483648.0f;
	}
};

// Music: a saw chord, a kick and hats (stereo), about -20 dBFS RMS
std::vector<float> makeMusic(int frames) {
	std::vector<float> x(2 * frames);
	Rng rng;
	double ph[4] = {0, 0, 0, 0};
	const double chord[4] = {130.81, 155.56, 196.0, 233.08};
	for (int i = 0; i < frames; i++) {
		double t = i / kFs;
		float l = 0;
		for (int k = 0; k < 4; k++) {
			ph[k] += chord[k] * (1 + 0.002 * k) / kFs;
			ph[k] -= std::floor(ph[k]);
			l += (float)(2 * ph[k] - 1) * 0.05f;
		}
		double beat = std::fmod(t, 0.5);
		l += (float)(0.2 * std::exp(-beat * 18) * std::sin(2 * M_PI * (50 + 80 * std::exp(-beat * 30)) * beat));
		l += (float)(0.06 * std::exp(-std::fmod(t + 0.25, 0.25) * 60)) * rng.next();
		x[2 * i] = l;
		x[2 * i + 1] = l * 0.9f + 0.02f * rng.next();
	}
	return x;
}
// A tone without steps of its own: 110, 220 and 330 Hz, about -16 dBFS RMS
std::vector<float> makeTone(int frames) {
	std::vector<float> x(2 * frames);
	for (int i = 0; i < frames; i++) {
		double t = i / kFs;
		float v = (float)(0.12 * std::sin(2 * M_PI * 110 * t) + 0.08 * std::sin(2 * M_PI * 220 * t + 1)
		                  + 0.05 * std::sin(2 * M_PI * 330 * t + 2));
		x[2 * i] = v;
		x[2 * i + 1] = v * 0.95f;
	}
	return x;
}

struct HP { // 4th-order Butterworth high-pass
	double z[2][2] = {{0, 0}, {0, 0}};
	double f;
	explicit HP(double hz) : f(hz) {}
	double operator()(double h) {
		double w0 = 2 * M_PI * f / kFs, cw = std::cos(w0), sw = std::sin(w0);
		const double q[2] = {0.5412, 1.3066};
		for (int k = 0; k < 2; k++) {
			double alpha = sw / (2 * q[k]), a0 = 1 + alpha;
			double b0 = (1 + cw) / 2 / a0, b1 = -(1 + cw) / a0, b2 = b0, a1 = -2 * cw / a0, a2 = (1 - alpha) / a0;
			double y = b0 * h + z[k][0];
			z[k][0] = b1 * h - a1 * y + z[k][1];
			z[k][1] = b2 * h - a2 * y;
			h = y;
		}
		return h;
	}
};
double db(double p) {
	return 10 * std::log10(p + 1e-30);
}

uint64_t fnv(uint64_t h, const std::vector<int32_t>& v) {
	for (int32_t x : v) {
		h = (h ^ (uint32_t)x) * 1099511628211ull;
	}
	return h;
}

// ---- 1. static
uint64_t staticHash() {
	const int frames = 12 * kBlock;
	std::vector<float> in = makeMusic(frames);
	const FilterMode lpfModes[] = {FilterMode::OFF,      FilterMode::TRANSISTOR_12DB, FilterMode::TRANSISTOR_24DB,
	                               FilterMode::TRANSISTOR_24DB_DRIVE, FilterMode::SVF_BAND, FilterMode::SVF_NOTCH};
	const FilterMode hpfModes[] = {FilterMode::OFF, FilterMode::HPLADDER, FilterMode::SVF_BAND, FilterMode::SVF_NOTCH};
	const FilterRoute routes[] = {FilterRoute::HIGH_TO_LOW, FilterRoute::LOW_TO_HIGH, FilterRoute::PARALLEL};
	uint64_t all = 1469598103934665603ull;
	int cases = 0;
	for (Ctx ctx : {Ctx::SYNTH, Ctx::KIT_ROW, Ctx::KIT}) {
		for (FilterMode lm : lpfModes) {
			uint64_t group = 1469598103934665603ull;
			for (FilterMode hm : hpfModes) {
				for (FilterRoute rt : routes) {
					for (int set = 0; set < 3; set++) {
						Settings s;
						s.lpfMode = lm;
						s.hpfMode = hm;
						s.route = rt;
						if (set == 0) {
							s.cut = 20, s.res = 10, s.hcut = 8, s.hres = 5;
						}
						else if (set == 1) {
							s.cut = 38, s.res = 42, s.morph = 20, s.hcut = 15, s.hres = 40, s.hmorph = 10;
						}
						else {
							s.cut = 4, s.res = 25, s.hcut = 30, s.hres = 30;
						}
						Runner run(ctx);
						std::vector<double> out;
						std::vector<int32_t> raw;
						for (int b = 0; b < frames / kBlock; b++) {
							run.block(s, &in[2 * b * kBlock], kBlock, out, &raw);
						}
						uint64_t h = fnv(1469598103934665603ull, raw);
						if (verbose) {
							printf("  static %-7s %-5s %-4s %s set %d: %016llx\n", ctxName(ctx), modeName(lm),
							       modeName(hm), routeName(rt), set, (unsigned long long)h);
						}
						group = fnv(group, raw);
						all = fnv(all, raw);
						cases++;
					}
				}
			}
			printf("static %-7s LPF %-5s: %016llx\n", ctxName(ctx), modeName(lm), (unsigned long long)group);
		}
	}
	printf("static: %d cases, hash %016llx\n", cases, (unsigned long long)all);
	return all;
}

// ---- 2. zipper
struct Sweep {
	const char* name;
	Ctx ctx;
	Settings s;
	double Settings::*param;
	double from, to;
};

double settingAt(const Sweep& z, double t) { // t: 0 to 1, up for the first half, down for the second
	double u = t < 0.5 ? t * 2 : 2 - t * 2;
	return z.from + (z.to - z.from) * u;
}

int zipper() {
	const int frames = (int)kFs; // 1 s
	// A tone below 600 Hz: a filter that moves smoothly puts nothing above 2 kHz, steps every block do (at the block
	// rate, 345 Hz, and its multiples, around the tone)
	std::vector<float> in(2 * frames);
	for (int i = 0; i < frames; i++) {
		double t = i / kFs;
		float v = (float)(0.15 * std::sin(2 * M_PI * 110 * t) + 0.06 * std::sin(2 * M_PI * 330 * t + 1)
		                  + 0.04 * std::sin(2 * M_PI * 550 * t + 2));
		in[2 * i] = v;
		in[2 * i + 1] = v * 0.95f;
	}
	std::vector<Sweep> sweeps;
	auto add = [&](const char* name, Ctx ctx, FilterMode lm, FilterMode hm, double Settings::*p, double from,
	               double to, double cut, double res) {
		Settings s;
		s.lpfMode = lm;
		s.hpfMode = hm;
		s.cut = cut;
		s.res = res;
		s.hcut = hm != FilterMode::OFF ? cut : 10;
		s.hres = hm != FilterMode::OFF ? res : 0;
		sweeps.push_back({name, ctx, s, p, from, to});
	};
	add("LP24 cutoff", Ctx::SYNTH, FilterMode::TRANSISTOR_24DB, FilterMode::OFF, &Settings::cut, 8, 45, 0, 25);
	add("LP12 cutoff", Ctx::SYNTH, FilterMode::TRANSISTOR_12DB, FilterMode::OFF, &Settings::cut, 8, 45, 0, 25);
	add("Drive cutoff", Ctx::SYNTH, FilterMode::TRANSISTOR_24DB_DRIVE, FilterMode::OFF, &Settings::cut, 8, 40, 0, 15);
	add("SVF cutoff", Ctx::SYNTH, FilterMode::SVF_BAND, FilterMode::OFF, &Settings::cut, 8, 45, 0, 25);
	add("LP24 cutoff", Ctx::KIT, FilterMode::TRANSISTOR_24DB, FilterMode::OFF, &Settings::cut, 8, 45, 0, 25);
	add("LP24 reso", Ctx::KIT, FilterMode::TRANSISTOR_24DB, FilterMode::OFF, &Settings::res, 0, 45, 25, 0);
	add("LP24 reso", Ctx::SYNTH, FilterMode::TRANSISTOR_24DB, FilterMode::OFF, &Settings::res, 0, 45, 25, 0);
	add("LP24 morph", Ctx::KIT_ROW, FilterMode::TRANSISTOR_24DB, FilterMode::OFF, &Settings::morph, 0, 40, 25, 20);
	add("HPL cutoff", Ctx::KIT, FilterMode::OFF, FilterMode::HPLADDER, &Settings::hcut, 2, 35, 10, 25);
	add("HPL reso", Ctx::KIT_ROW, FilterMode::OFF, FilterMode::HPLADDER, &Settings::hres, 0, 45, 12, 0);
	add("SVF HP cutoff", Ctx::KIT, FilterMode::OFF, FilterMode::SVF_NOTCH, &Settings::hcut, 2, 35, 10, 25);
	int failures = 0;
	double worst = -300;
	for (Sweep& z : sweeps) {
		std::vector<double> blk, ref;
		{ // per block: the setting at the block's end, as the params give it when the block is set up
			Runner run(z.ctx);
			for (int b = 0; b < frames / kBlock; b++) {
				Settings s = z.s;
				s.*z.param = settingAt(z, (double)(b + 1) * kBlock / frames);
				run.block(s, &in[2 * b * kBlock], kBlock, blk, nullptr);
			}
		}
		{ // per sample
			Runner run(z.ctx);
			for (int i = 0; i < frames / kBlock * kBlock; i++) {
				Settings s = z.s;
				s.*z.param = settingAt(z, (double)(i + 1) / frames);
				run.block(s, &in[2 * i], 1, ref, nullptr);
			}
		}
		HP hd(2000), hb(2000), hr(2000);
		double sig = 0, e = 0, eh = 0, bh = 0, rh = 0;
		int skip = 2 * 2048; // the start (the per-sample one's first blocks differ in their first configuration)
		for (size_t i = 0; i < blk.size(); i += 2) {
			double d = blk[i] - ref[i];
			double dh = hd(d), b = hb(blk[i]), r = hr(ref[i]);
			if ((int)i >= skip) {
				sig += ref[i] * ref[i];
				e += d * d;
				eh += dh * dh;
				bh += b * b;
				rh += r * r;
			}
		}
		if (verbose) { // the residual per 1024 samples
			for (size_t w = 0; w + 2048 <= blk.size(); w += 2048) {
				double ew = 0, sw = 0;
				for (size_t i = w; i < w + 2048; i += 2) {
					ew += (blk[i] - ref[i]) * (blk[i] - ref[i]);
					sw += ref[i] * ref[i];
				}
				printf("    %5.3f s: %7.1f dBc\n", w / 2 / kFs, db(ew) - db(sw));
			}
		}
		// Above 2 kHz: the difference, and the output's own against the per-sample one's (where the configuration
		// itself moves in steps, as the HPF's resonance does in 256, the per-sample one has them too, as clicks)
		double full = db(e) - db(sig), hf = db(eh) - db(sig), more = db(bh) - db(rh);
		bool bad = hf > kMaxZipper && more > -1;
		failures += bad;
		worst = std::max(worst, hf);
		printf("zipper %-7s %-13s %4.1f..%4.1f: out %6.1f dBFS, block vs per-sample %6.1f dBc, above 2 kHz %6.1f dBc "
		       "(its own %6.1f dBc, %+5.1f dB vs per-sample)%s\n",
		       ctxName(z.ctx), z.name, z.from, z.to, db(sig / (blk.size() / 2 - skip / 2)), full, hf,
		       db(bh) - db(sig), more, bad ? "  ZIPPER" : "");
		fflush(stdout);
	}
	printf("zipper: worst above 2 kHz %.1f dBc (limit %.0f)\n", worst, kMaxZipper);
	return failures;
}

// ---- 3. clicks
struct Change {
	const char* name;
	Ctx ctx;
	Settings before, after;
};

struct ClickResult {
	double step, burst;
};

ClickResult measureChange(const Change& c, const std::vector<float>& in, bool change) {
	const int blocks = 60, at = 40; // the change at block 40 (0.116 s)
	Runner run(c.ctx);
	std::vector<double> out;
	for (int b = 0; b < blocks; b++) {
		run.block(change && b >= at ? c.after : c.before, &in[2 * b * kBlock], kBlock, out, nullptr);
	}
	int a = at * kBlock, w = (int)(0.02 * kFs), pre = (int)(0.1 * kFs);
	double stepBefore = 1e-12, stepAfter = 0, burst = 0;
	HP h(4000);
	for (int i = 1; i < blocks * kBlock; i++) {
		double y = out[2 * i], s = std::fabs(out[2 * i] - out[2 * (i - 1)]);
		double hy = h(y);
		if (i >= a - pre && i < a) {
			stepBefore = std::max(stepBefore, s);
		}
		if (i >= a && i < a + w) {
			stepAfter = std::max(stepAfter, s);
			burst += hy * hy;
		}
	}
	return {stepAfter / stepBefore, burst};
}

int clicks() {
	std::vector<float> in = makeTone(60 * kBlock);
	std::vector<Change> changes;
	auto lp = [](FilterMode m, double cut, double res) {
		Settings s;
		s.lpfMode = m;
		s.cut = cut;
		s.res = res;
		return s;
	};
	for (Ctx ctx : {Ctx::SYNTH, Ctx::KIT}) {
		changes.push_back({"LP24 -> LP12", ctx, lp(FilterMode::TRANSISTOR_24DB, 12, 30),
		                   lp(FilterMode::TRANSISTOR_12DB, 12, 30)});
		changes.push_back({"LP24 -> SVF", ctx, lp(FilterMode::TRANSISTOR_24DB, 12, 30), lp(FilterMode::SVF_BAND, 12, 30)});
		changes.push_back({"SVF -> Drive", ctx, lp(FilterMode::SVF_NOTCH, 12, 30),
		                   lp(FilterMode::TRANSISTOR_24DB_DRIVE, 12, 30)});
		changes.push_back({"LP24 off", ctx, lp(FilterMode::TRANSISTOR_24DB, 10, 30), lp(FilterMode::OFF, 10, 30)});
		changes.push_back({"SVF off", ctx, lp(FilterMode::SVF_BAND, 10, 30), lp(FilterMode::OFF, 10, 30)});
		Settings hp = lp(FilterMode::OFF, 25, 0);
		hp.hpfMode = FilterMode::HPLADDER;
		hp.hcut = 20;
		hp.hres = 30;
		Settings hp2 = hp;
		hp2.hpfMode = FilterMode::SVF_BAND;
		changes.push_back({"HPL -> SVF", ctx, hp, hp2});
		Settings hpOff = hp;
		hpOff.hpfMode = FilterMode::OFF;
		changes.push_back({"HPL off", ctx, hp, hpOff});
		Settings both = lp(FilterMode::TRANSISTOR_24DB, 22, 35);
		both.hpfMode = FilterMode::HPLADDER;
		both.hcut = 18;
		both.hres = 35;
		Settings l2h = both, par = both;
		l2h.route = FilterRoute::LOW_TO_HIGH;
		par.route = FilterRoute::PARALLEL;
		changes.push_back({"route H2L -> L2H", ctx, both, l2h});
		changes.push_back({"route H2L -> par", ctx, both, par});
		Settings bothOff = both;
		bothOff.lpfMode = FilterMode::OFF;
		bothOff.hpfMode = FilterMode::OFF;
		changes.push_back({"LPF+HPF off", ctx, both, bothOff});
	}
	int failures = 0;
	double worstStep = 0, worstBurst = -300;
	for (Change& c : changes) {
		ClickResult with = measureChange(c, in, true), without = measureChange(c, in, false);
		// Without the change, the case with the new settings from the start: the burst a change brings is what
		// either doesn't have (the new filter may simply let more above 4 kHz through)
		Change settled = c;
		settled.before = c.after;
		ClickResult newOnly = measureChange(settled, in, false);
		double burst = db(with.burst) - db(std::max(without.burst, newOnly.burst));
		bool bad = with.step > kMaxStep || burst > kMaxBurst;
#ifdef FILTERSET_FADES
		failures += bad;
#endif
		worstStep = std::max(worstStep, with.step);
		worstBurst = std::max(worstBurst, burst);
		printf("click %-7s %-17s: largest step %5.2fx the one before, above 4 kHz %+6.1f dB%s\n", ctxName(c.ctx), c.name,
		       with.step, burst, bad ? "  CLICK" : "");
		fflush(stdout);
	}
	printf("clicks: worst step %.2fx (limit %.1f), above 4 kHz %+.1f dB (limit %+.0f)\n", worstStep, kMaxStep,
	       worstBurst, kMaxBurst);
	return failures;
}
} // namespace

int main(int argc, char** argv) {
	// argv[1]: only that part (static, zipper, clicks); argv[2] "verbose" or VERBOSE in the environment (on the PC)
	verbose = getenv("VERBOSE") != nullptr || (argc > 2 && !strcmp(argv[2], "verbose"));
	const char* only = argc > 1 && strcmp(argv[1], "all") ? argv[1] : nullptr;
	int failures = 0;
	if (!only || !strcmp(only, "static")) {
		staticHash();
	}
	if (!only || !strcmp(only, "zipper")) {
		failures += zipper();
	}
	if (!only || !strcmp(only, "clicks")) {
		failures += clicks();
	}
	if (failures) {
		printf("FAIL: %d cases with zipper or clicks above the limits\n", failures);
		return 1;
	}
	printf("ok: no zipper above %.0f dBc, no clicks on a change (compare the static hash with REF=)\n", kMaxZipper);
	return 0;
}
