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
//    (no steps of its own): the largest step between two samples in the 20 ms after the change against the larger of
//    the largest in the 100 ms before it and the largest with the new settings from the start, and the energy above 4
//    kHz in the 20 ms after it (dBc) against the larger of the same without the change, with the new settings from
//    the start and kMinBurst. Fails where the step is more than kMaxStep times that or above 4 kHz more than
//    kMaxBurst dB come in. Without FILTERSET_FADES (the source before the crossfades) only printed.
//
// 4. fadein: a filter switched on fades in from its input: linear, the same weight for left and right, exact after
//    320 frames (see fadeIn()).
// 5. slots: more crossfades at once than there are slots: the one beyond switches at once, counted (see slots()).
// 6. jumps: the cutoff jumping from 28 Hz to 16 kHz and back every block: no mid-ramp ringing (see jumps()).
// 7. hpres: the HP ladder's level in the resonance's finest steps: no 1 / resonance to 8 bits (see hpRes()).
// 8. glide: the filter params' 10 ms glide: its time constant, and a MIDI CC's steps on the cutoff (see glide()).
// 9. parallel: the parallel route's levels (an off filter adds nothing, both at half level; see parallel()).
//
// Arguments: the part (static, zipper, clicks, fadein, slots, jumps, hpres, glide, parallel or all), then verbose: every static case's hash.
#include "dsp/filter/filter_set.h"
#if __has_include("dsp/filter/param_glide.h")
#include "dsp/filter/param_glide.h"
#define FILTER_PARAM_GLIDE
#endif
#include "util/functions.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <type_traits>
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
constexpr double kMaxZipperSlow = -100; // ... for the slow sweeps
constexpr double kMaxStep = 2.5;   // the largest sample step after a change against the one before it
constexpr double kMaxBurst = 6;    // dB above 4 kHz after a change against the case without it
constexpr double kMinBurst = -70;  // dBc above 4 kHz that don't count (a 7 ms linear crossfade's corners: -80 dBc)
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
#elif defined(LPF_SATURATION_PER_CONTEXT)
			fs->renderLongStereo(buf.data(), buf.data() + 2 * n, isGlobal(ctx) ? kHpSatGlobal : kHpSatVoice,
			                     isGlobal(ctx) ? kLpSatGlobal : kLpSatVoice);
#else // (mastertune-v17: the HP ladder's saturation only)
			fs->renderLongStereo(buf.data(), buf.data() + 2 * n, isGlobal(ctx) ? kHpSatGlobal : kHpSatVoice);
#endif
		}
		else {
#ifdef LPF_SATURATION_PER_CONTEXT
			fs->renderLong(buf.data(), buf.data() + n, n, 1, kHpSatVoice, kLpSatVoice);
#else
			fs->renderLong(buf.data(), buf.data() + n, n, 1, kHpSatVoice);
#endif
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
			// Two groups: the serial routes and the parallel one (whose levels v18 changed on purpose)
			uint64_t group = 1469598103934665603ull, groupPar = 1469598103934665603ull;
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
						if (rt == FilterRoute::PARALLEL) {
							groupPar = fnv(groupPar, raw);
						}
						else {
							group = fnv(group, raw);
						}
						all = fnv(all, raw);
						cases++;
					}
				}
			}
			printf("static %-7s LPF %-5s serial  : %016llx\n", ctxName(ctx), modeName(lm), (unsigned long long)group);
			printf("static %-7s LPF %-5s parallel: %016llx\n", ctxName(ctx), modeName(lm),
			       (unsigned long long)groupPar);
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
	double limit;       // dBc above 2 kHz
	double level = 1.0; // the tone's level against the common one (-16 dBFS)
};

double settingAt(const Sweep& z, double t) { // t: 0 to 1, up for the first half, down for the second
	double u = t < 0.5 ? t * 2 : 2 - t * 2;
	return z.from + (z.to - z.from) * u;
}

int zipper() {
	const int frames = (int)kFs; // 1 s
	// A tone below 600 Hz: a filter that moves smoothly puts nothing above 2 kHz, steps every block do (at the block
	// rate, 345 Hz, and its multiples, around the tone)
	std::vector<float> in0(2 * frames);
	for (int i = 0; i < frames; i++) {
		double t = i / kFs;
		float v = (float)(0.15 * std::sin(2 * M_PI * 110 * t) + 0.06 * std::sin(2 * M_PI * 330 * t + 1)
		                  + 0.04 * std::sin(2 * M_PI * 550 * t + 2));
		in0[2 * i] = v;
		in0[2 * i + 1] = v * 0.95f;
	}
	std::vector<Sweep> sweeps;
	auto add = [&](const char* name, Ctx ctx, FilterMode lm, FilterMode hm, double Settings::*p, double from,
	               double to, double cut, double res, double limit = kMaxZipper) {
		Settings s;
		s.lpfMode = lm;
		s.hpfMode = hm;
		s.cut = cut;
		s.res = res;
		s.hcut = hm != FilterMode::OFF ? cut : 10;
		s.hres = hm != FilterMode::OFF ? res : 0;
		sweeps.push_back({name, ctx, s, p, from, to, limit});
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
	// At -16 dBFS the HP ladder's tanh, which switches on at a resonance of 39 % (hpfProcessedResonance 750000000,
	// 1.2.1's threshold, per block), changes the level by itself there (14 dB in that block), in the block-set one a
	// block earlier than in the per-sample one: -38 dBc above 2 kHz from that alone (limit -35 here). At a voice's
	// usual level (-46 dBFS) the tanh doesn't change it, and the usual limit holds
	add("HPL reso", Ctx::KIT_ROW, FilterMode::OFF, FilterMode::HPLADDER, &Settings::hres, 0, 45, 12, 0, -35);
	add("HPL reso -46", Ctx::KIT_ROW, FilterMode::OFF, FilterMode::HPLADDER, &Settings::hres, 0, 45, 12, 0);
	sweeps.back().level = 0.0316;
	add("SVF HP cutoff", Ctx::KIT, FilterMode::OFF, FilterMode::SVF_NOTCH, &Settings::hcut, 2, 35, 10, 25);
	// Slow sweeps: about 1 and 1/8 octave per second (a display step is about 0.2 octave here), where 1.2.1's steps
	// were small but still there every block (v17: -89 to -105 dBc above 2 kHz; v18: -131 to -153), against the
	// stricter kMaxZipperSlow
	add("LP24 slow 1oct", Ctx::SYNTH, FilterMode::TRANSISTOR_24DB, FilterMode::OFF, &Settings::cut, 18, 20.5, 0, 25,
	    kMaxZipperSlow);
	add("LP24 slow 1/8", Ctx::KIT, FilterMode::TRANSISTOR_24DB, FilterMode::OFF, &Settings::cut, 18, 18.3, 0, 25,
	    kMaxZipperSlow);
	add("LP12 slow 1oct", Ctx::KIT_ROW, FilterMode::TRANSISTOR_12DB, FilterMode::OFF, &Settings::cut, 18, 20.5, 0, 25,
	    kMaxZipperSlow);
	add("SVF slow 1oct", Ctx::KIT, FilterMode::SVF_BAND, FilterMode::OFF, &Settings::cut, 18, 20.5, 0, 25,
	    kMaxZipperSlow);
	add("HPL slow 1oct", Ctx::SYNTH, FilterMode::OFF, FilterMode::HPLADDER, &Settings::hcut, 15, 17.5, 10, 12,
	    kMaxZipperSlow);
	int failures = 0;
	double worst = -300;
	for (Sweep& z : sweeps) {
		if (getenv("ONLY") && !strstr(z.name, getenv("ONLY"))) {
			continue;
		}
		std::vector<double> blk, ref;
		std::vector<float> in = in0;
		for (float& v : in) {
			v *= (float)z.level;
		}
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
		bool bad = hf > z.limit && more > -1;
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
	double stepBefore, stepAfter; // the largest sample step in the 100 ms before the change and the 20 ms after
	double burst;                 // the energy above 4 kHz in the 20 ms after, against the energy there (dBc)
};

ClickResult measureChange(const Change& c, const std::vector<float>& in, bool change) {
	const int blocks = 60, at = 40; // the change at block 40 (0.116 s)
	Runner run(c.ctx);
	std::vector<double> out;
	for (int b = 0; b < blocks; b++) {
		run.block(change && b >= at ? c.after : c.before, &in[2 * b * kBlock], kBlock, out, nullptr);
	}
	int a = at * kBlock, w = (int)(0.02 * kFs), pre = (int)(0.1 * kFs);
	double stepBefore = 1e-12, stepAfter = 0, burst = 0, all = 0;
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
			all += y * y;
		}
	}
	return {stepBefore, stepAfter, db(burst) - db(all)};
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
		changes.push_back({"LP24 on", ctx, lp(FilterMode::OFF, 10, 30), lp(FilterMode::TRANSISTOR_24DB, 10, 30)});
		changes.push_back({"SVF on", ctx, lp(FilterMode::OFF, 10, 30), lp(FilterMode::SVF_BAND, 10, 30)});
		changes.push_back({"HPL on", ctx, hpOff, hp});
		Settings bothOff = both;
		bothOff.lpfMode = FilterMode::OFF;
		bothOff.hpfMode = FilterMode::OFF;
		changes.push_back({"LPF+HPF off", ctx, both, bothOff});
#ifdef FILTERSET_PARALLEL_HALF
		// Parallel: a filter switched on beside one that is on (the sum goes to half level) or off
		Settings parLp = par;
		parLp.hpfMode = FilterMode::OFF;
		changes.push_back({"par: HPL on beside LP24", ctx, parLp, par});
		changes.push_back({"par: HPL off", ctx, par, parLp});
		Settings parHp = par;
		parHp.lpfMode = FilterMode::OFF;
		changes.push_back({"par: LP24 off", ctx, par, parHp});
		changes.push_back({"par: LP24 on beside HPL", ctx, parHp, par});
#endif
	}
	int failures = 0;
	double worstStep = 0, worstBurst = -300;
	for (Change& c : changes) {
		ClickResult with = measureChange(c, in, true), without = measureChange(c, in, false);
		// And the case with the new settings from the start: a change may simply bring a louder signal, with larger
		// steps, or let more above 4 kHz through. The step against the larger of the one before and the one of the
		// new settings; above 4 kHz what comes in beyond both (where it's above kMinBurst dBc: below that, the
		// crossfade's own corners)
		Change settled = c;
		settled.before = c.after;
		ClickResult newOnly = measureChange(settled, in, false);
		double step = with.stepAfter / std::max(with.stepBefore, newOnly.stepAfter);
		double burst = with.burst - std::max({without.burst, newOnly.burst, kMinBurst});
		bool bad = step > kMaxStep || burst > kMaxBurst;
#ifdef FILTERSET_FADES
		failures += bad;
#endif
		worstStep = std::max(worstStep, step);
		worstBurst = std::max(worstBurst, burst);
		printf("click %-7s %-17s: largest step %6.2fx, above 4 kHz %6.1f dBc (%+6.1f dB)%s\n", ctxName(c.ctx), c.name,
		       step, with.burst, burst, bad ? "  CLICK" : "");
		fflush(stdout);
	}
	printf("clicks: worst step %.2fx (limit %.1f), above 4 kHz %+.1f dB (limit %+.0f)\n", worstStep, kMaxStep,
	       worstBurst, kMaxBurst);
	return failures;
}
// 4. fadein: a filter switched on fades in from its input (Filter::reset(true)): the same filter, configured the same,
//    once reset with the fade and once without, run on the same tone (left = right). The fade's output must be
//    wet + (dry - wet) * left / 320 per frame, left from 319 down to 0 (linear in amplitude, within 2 LSB), the same
//    for both channels of a frame, and exactly the plain filter's from frame 320 on. v17 (1.2.1's fade): exponential,
//    per interleaved sample (left and right differ), 0.1 % jumped at the end: fails.
#ifdef FILTER_FADE_IN
constexpr int kFadeIn = LpLadderFilter::kFadeInSamples;
#else
constexpr int kFadeIn = 320; // the source before (1.2.1's fade: about 460 samples, exponential)
#endif
template <typename F>
int fadeInCase(const char* name, FilterMode mode, bool stereo, int32_t saturation) {
	constexpr int kFrames = 4 * kBlock;
	std::vector<float> tone = makeTone(kFrames);
	F a{}, b{};
	for (F* f : {&a, &b}) {
		f->configure(freqParam(12), linearParam(30), mode, 0, 134217728);
	}
	a.reset(true);
	b.reset(false);
	int ch = stereo ? 2 : 1;
	std::vector<int32_t> dry(ch * kFrames), wa(ch * kFrames), wb(ch * kFrames);
	for (int i = 0; i < kFrames; i++) {
		for (int c = 0; c < ch; c++) {
			dry[ch * i + c] = (int32_t)std::lround(tone[2 * i] * 2147483648.0);
		}
	}
	wa = dry;
	wb = dry;
	for (int blk = 0; blk < 4; blk++) {
		int o = blk * kBlock * ch;
		for (F* f : {&a, &b}) {
			if constexpr (requires { f->setSaturation(saturation); }) {
				f->setSaturation(saturation);
			}
			std::vector<int32_t>& w = f == &a ? wa : wb;
			if (stereo) {
				f->filterStereo(&w[o], &w[o] + 2 * kBlock);
			}
			else {
				f->filterMono(&w[o], &w[o] + kBlock, 1);
			}
		}
	}
	double worstLaw = 0;
	int lrDiffer = 0, after = 0;
	for (int i = 0; i < kFrames; i++) {
		for (int c = 0; c < ch; c++) {
			int64_t got = wa[ch * i + c], wet = wb[ch * i + c], d = dry[ch * i + c];
			if (i >= kFadeIn) {
				after += got != wet;
				continue;
			}
			double keep = (double)(kFadeIn - 1 - i) / kFadeIn;
			double want = wet + (d - wet) * keep;
			worstLaw = std::max(worstLaw, std::fabs(got - want));
		}
		if (stereo) {
			lrDiffer += wa[2 * i] != wa[2 * i + 1];
		}
	}
	bool bad = worstLaw > 2 || lrDiffer || after;
	printf("fadein %-5s %-6s: largest error against the linear law %.1f LSB, left != right in %d frames, %d samples "
	       "differ from the plain filter after %d frames%s\n",
	       name, stereo ? "stereo" : "mono", worstLaw, lrDiffer, after, kFadeIn, bad ? "  FAIL" : "");
	return bad;
}
int fadeIn() {
	int failures = 0;
	for (bool st : {false, true}) {
		failures += fadeInCase<LpLadderFilter>("LP24", FilterMode::TRANSISTOR_24DB, st, kLpSatVoice);
		failures += fadeInCase<SVFilter>("SVF", FilterMode::SVF_BAND, st, 0);
		failures += fadeInCase<HpLadderFilter>("HPL", FilterMode::HPLADDER, st, kHpSatVoice);
	}
	return failures;
}

#ifdef FILTERSET_FADES
// 5. slots: kNumFades + 1 kits switch their LPF off in the same block: kNumFades crossfade, the one beyond switches
//    at once (as 1.2.1); counted by FilterSet::isOn(), which stays true while a switched-off filter fades out. After
//    kFadeSamples every slot is free again: kNumFades more fade.
int slots() {
	constexpr int n = FilterSet::kNumFades + 1;
	std::vector<float> tone = makeTone(8 * kBlock);
	std::vector<Runner*> runs;
	Settings on;
	on.lpfMode = FilterMode::TRANSISTOR_24DB;
	on.cut = 15;
	Settings off = on;
	off.lpfMode = FilterMode::OFF;
	for (int i = 0; i < n; i++) {
		runs.push_back(new Runner(Ctx::KIT));
	}
	std::vector<double> out;
	auto blockAll = [&](const Settings& s, int b) {
		for (Runner* r : runs) {
			r->block(s, &tone[2 * b * kBlock], kBlock, out, nullptr);
		}
	};
	blockAll(on, 0);
	blockAll(off, 1);
	int fading = 0;
	for (Runner* r : runs) {
		fading += r->fs->isOn();
	}
	for (int b = 2; b < 5; b++) {
		blockAll(off, b);
	}
	int stillFading = 0;
	for (Runner* r : runs) {
		stillFading += r->fs->isOn();
	}
	blockAll(on, 5);
	blockAll(off, 6);
	int fadingAgain = 0;
	for (Runner* r : runs) {
		fadingAgain += r->fs->isOn();
	}
	for (Runner* r : runs) {
		delete r;
	}
	bool bad = fading != n - 1 || stillFading != 0 || fadingAgain != n - 1;
	printf("slots: %d FilterSets switch off at once: %d crossfade, %d switch at once (%d slots); %d still fading after "
	       "%d samples; then %d crossfade again%s\n",
	       n, fading, n - fading, FilterSet::kNumFades, stillFading, 3 * kBlock, fadingAgain, bad ? "  FAIL" : "");
	return bad;
}
#endif
// 6. jumps: the cutoff jumping between about 28 Hz and 16 kHz (display 5 and 40) every block, the most a ramp has to
//    cover in one block, at resonance 0, 13, 25 and 38, every LP mode and the HP ladder, as a synth and a kit: the
//    output's peak against the larger of the peaks with the filter held at either end. Fails where it's more than
//    kMaxJumpDb above that (a set in the middle of a ramp that rings or runs away)
int jumps() {
	constexpr double kMaxJumpDb = 6;
	const int blocks = 120;
	std::vector<float> music = makeMusic(blocks * kBlock);
	int failures = 0;
	double worst = -300;
	const FilterMode modes[] = {FilterMode::TRANSISTOR_24DB, FilterMode::TRANSISTOR_12DB,
	                            FilterMode::TRANSISTOR_24DB_DRIVE, FilterMode::SVF_BAND, FilterMode::HPLADDER};
	for (Ctx ctx : {Ctx::SYNTH, Ctx::KIT}) {
		for (FilterMode m : modes) {
			for (double res : {0.0, 13.0, 25.0, 38.0}) {
				bool hp = m == FilterMode::HPLADDER;
				Settings lo, hi;
				for (Settings* s : {&lo, &hi}) {
					if (hp) {
						s->hpfMode = m;
						s->hres = res;
						s->hcut = s == &lo ? 5 : 40;
					}
					else {
						s->lpfMode = m;
						s->res = res;
						s->cut = s == &lo ? 5 : 40;
					}
				}
				auto peak = [&](int pattern) { // 0: lo, 1: hi, 2: alternating
					Runner run(ctx);
					std::vector<double> out;
					for (int b = 0; b < blocks; b++) {
						bool high = pattern == 1 || (pattern == 2 && (b & 1));
						run.block(high ? hi : lo, &music[2 * b * kBlock], kBlock, out, nullptr);
					}
					double p = 1e-12;
					for (size_t i = 2 * 8 * kBlock; i < out.size(); i++) { // after the first 8 blocks
						p = std::max(p, std::fabs(out[i]));
					}
					return p;
				};
				double held = std::max(peak(0), peak(1)), jump = peak(2);
				double over = 20 * std::log10(jump / held);
				bool bad = over > kMaxJumpDb;
				failures += bad;
				worst = std::max(worst, over);
				if (verbose || bad) {
					printf("jumps %-7s %-5s res %4.1f: peak %6.1f dBFS, %+5.1f dB over the larger held one%s\n",
					       ctxName(ctx), modeName(m), res, 20 * std::log10(jump), over, bad ? "  FAIL" : "");
				}
			}
		}
	}
	printf("jumps: cutoff 28 Hz <-> 16 kHz every block: the peak at most %+.1f dB over the larger held one (limit "
	       "%+.0f)\n",
	       worst, kMaxJumpDb);
	return failures;
}
// 7. hpres: the HP ladder's level as its resonance moves in its finest steps (1.2.1: 256 over the range): a 500 Hz
//    tone (500 Hz: the antialiased tanh from a resonance of 50 %, a two-sample average, takes 0.1 dB off 2 kHz) at
//    -57 dBFS (below the ladder's tanh) through the HP ladder at cutoff 5, a synth's, at 360 resonances from 0
//    to 45 (above that the level rises steeply of itself towards self-oscillation); the largest level step against
//    its neighbours' slope. 1.2.1 normalised with 1 / resonance to 8 bits and cut the resonance to 256 steps (steps up
//    to 0.77 dB); fails above kMaxHpStepDb
int hpRes() {
	constexpr double kMaxHpStepDb = 0.1; // (where the resonance leaves its minimum, 1.2.1's, the slope bends: 0.085)
	const int frames = 16 * kBlock;
	std::vector<float> tone(2 * frames);
	for (int i = 0; i < frames; i++) {
		tone[2 * i] = tone[2 * i + 1] = (float)(0.002 * std::sin(2 * M_PI * 500 * i / kFs)); // -57 dBFS: below the tanh
	}
	std::vector<double> levels;
	for (int k = 0; k <= 360; k++) {
		Settings s;
		s.hpfMode = FilterMode::HPLADDER;
		s.hcut = 5;
		s.hres = 45.0 * k / 360;
		Runner run(Ctx::SYNTH);
		std::vector<double> out;
		for (int b = 0; b < frames / kBlock; b++) {
			run.block(s, &tone[2 * b * kBlock], kBlock, out, nullptr);
		}
		double p = 0;
		for (int i = frames; i < 2 * frames; i += 2) { // the second half
			p += out[i] * out[i];
		}
		levels.push_back(db(p / (frames / 2)));
	}
	// A step against the neighbours' slope: |L[k+1] - 2 L[k] + L[k-1]| (the level's own slope, steep at low
	// resonance, doesn't count)
	double worst = 0, worstAt = 0;
	for (size_t k = 1; k + 1 < levels.size(); k++) {
		double d = std::fabs(levels[k + 1] - 2 * levels[k] + levels[k - 1]);
		if (d > worst) {
			worst = d;
			worstAt = 45.0 * k / 360;
		}
		if (verbose) {
			printf("  hpres %6.3f: %8.3f dBFS (%+.3f)\n", 45.0 * k / 360, levels[k], d);
		}
	}
	bool bad = worst > kMaxHpStepDb;
	printf("hpres: HP ladder level at 500 Hz as the resonance moves: largest step %.3f dB (at %.2f; limit %.2f)%s\n",
	       worst, worstAt, kMaxHpStepDb, bad ? "  FAIL" : "");
	return bad;
}
#ifdef FILTER_PARAM_GLIDE
// 8. glide: the filter params' one-pole (dsp/filter/param_glide.h), as Sound::doFilterGlide() and
//    GlobalEffectable::setupFilterSetConfig() run it once per block. (a) Its time constant: a step, advanced in blocks
//    of 32, 60 and 128 samples, reaches 1 - 1/e after 10 ms (441 samples) within one block, and lands exactly on the
//    target, no overshoot. (b) A MIDI CC turning the song's LPF cutoff (a kit's LP24, resonance 25): one CC step
//    (a display step of 50 / 128) every 10 ms, 0.5 s up and down, taken at once (1.2.1: every block the new value) and
//    through the glide: the output's energy above 2 kHz from the steps (against the same cutoff moving per sample in
//    a straight line). Fails where the glide leaves more than kMaxGlideHfDb of it.
int glide() {
	using deluge::dsp::filter::FilterParamGlide;
	int failures = 0;
	for (int block : {32, 60, 128}) {
		FilterParamGlide g{};
		g.value[0] = 0;
		const int32_t target = 1 << 30;
		int n = 0, reached = -1;
		bool over = false;
		while (g.value[0] != target && n < 44100) {
			g.active |= 1;
			g.advance(0, target, FilterParamGlide::blockFactor(block));
			n += block;
			over |= g.value[0] > target;
			if (reached < 0 && g.value[0] >= (int32_t)(target * (1 - std::exp(-1.0)))) {
				reached = n;
			}
		}
		bool bad = over || g.value[0] != target || std::abs(reached - 441) > block;
		failures += bad;
		printf("glide: blocks of %3d: 63 %% after %d samples (10 ms: 441), exactly there after %d, overshoot %s%s\n",
		       block, reached, n, over ? "yes" : "no", bad ? "  FAIL" : "");
	}
	constexpr double kMaxGlideHfDb = -12;
	const int frames = (int)kFs;
	std::vector<float> in = makeTone(frames);
	auto cc = [&](int i) { // the CC's value at sample i, display units: one step (50 / 128) every 441 samples
		double t = (double)i / frames, u = t < 0.5 ? t * 2 : 2 - t * 2;
		return 10 + std::floor(u * 25 * 128 / 50) * 50 / 128; // 10 to 35 on the display
	};
	auto run = [&](int how) { // 0: at once per block, 1: glide per block, 2: glide per sample (the reference)
		Runner r(Ctx::KIT);
		std::vector<double> out;
		FilterParamGlide g{};
		bool started = false;
		int step = how == 2 ? 1 : kBlock;
		for (int i = 0; i + step <= frames; i += step) {
			double target = cc(i);
			if (!started) {
				g.value[0] = (int32_t)(target * 1e6);
				started = true;
			}
			g.advance(0, (int32_t)(target * 1e6), FilterParamGlide::blockFactor(step));
			Settings s;
			s.lpfMode = FilterMode::TRANSISTOR_24DB;
			s.res = 25;
			s.cut = how == 0 ? target : g.value[0] / 1e6;
			r.block(s, &in[2 * i], step, out, nullptr);
		}
		return out;
	};
	std::vector<double> atOnce = run(0), glided = run(1), ref = run(2);
	auto hf = [&](const std::vector<double>& y) {
		HP h(2000), hr(2000);
		double e = 0;
		for (size_t i = 0; i < y.size() && i < ref.size(); i += 2) {
			double d = h(y[i] - ref[i]);
			if (i > 2 * 4096) {
				e += d * d;
			}
		}
		return db(e);
	};
	double sig = 0;
	for (size_t i = 0; i < ref.size(); i += 2) {
		sig += ref[i] * ref[i];
	}
	double eOnce = hf(atOnce) - db(sig), eGlide = hf(glided) - db(sig);
	bool bad = eGlide - eOnce > kMaxGlideHfDb;
	failures += bad;
	printf("glide: a CC step every 10 ms on the kit's LPF: above 2 kHz against the glide per sample: at once %.1f dBc, "
	       "glided per block %.1f dBc (%+.1f dB; limit %+.0f)%s\n",
	       eOnce, eGlide, eGlide - eOnce, kMaxGlideHfDb, bad ? "  FAIL" : "");
	return failures;
}
#endif
} // namespace

#ifdef LPF_RAMP_HORNER
// The ladders' ladderTanH<amount>() (ladder_components.h, v18: fewer instructions) against getTanHUnknown(): the same
// for every amount the ladders use, over the edges and 2^20 inputs spread over the whole range
int tanhExact() {
	int bad = 0;
	auto check = [&](auto amountTag, int32_t x) {
		constexpr uint32_t a = decltype(amountTag)::value;
		if (ladderTanH<a>(x) != getTanHUnknown(x, a)) {
			if (bad++ < 5) {
				printf("tanh: amount %u input %d: %d instead of %d\n", a, x, ladderTanH<a>(x), getTanHUnknown(x, a));
			}
		}
		// With the scaling shift in front merged (lpladder.cpp scaleInput(): amounts 2 and 3)
		if constexpr (a <= 3) {
			if (ladderTanH<a, 2>(x) != getTanHUnknown(lshiftAndSaturate<2>(x), a)) {
				if (bad++ < 5) {
					printf("tanh: amount %u pre 2 input %d: %d instead of %d\n", a, x, ladderTanH<a, 2>(x),
					       getTanHUnknown(lshiftAndSaturate<2>(x), a));
				}
			}
		}
	};
	uint32_t r = 12345;
	for (int i = 0; i < (1 << 20) + 6; i++) {
		int32_t x = i == 0 ? INT32_MIN : i == 1 ? INT32_MAX : i == 2 ? 0 : i == 3 ? -1 : i == 4 ? 1 : (int32_t)(r = r * 1664525u + 1013904223u);
		if (i > 5 && (i & 1)) {
			x >>= (r >> 27); // small inputs too
		}
		check(std::integral_constant<uint32_t, 2>{}, x);
		check(std::integral_constant<uint32_t, 3>{}, x);
		check(std::integral_constant<uint32_t, 4>{}, x);
		check(std::integral_constant<uint32_t, 7>{}, x);
	}
	printf("tanh: ladderTanH() %s getTanHUnknown() (amounts 2, 3, 4, 7, and 2, 3 after lshiftAndSaturate<2>; %d inputs)\n", bad ? "DIFFERS from" : "= ",
	       (1 << 20) + 6);
	return bad ? 1 : 0;
}
#endif

#ifdef FILTERSET_PARALLEL_HALF
// 9. parallel (v18): the parallel route with a filter off is that filter alone (bit-exact against the same filter in
// series with the other off; v17 added the dry input), with none on the input itself, with both the two filters'
// outputs summed at half level (bit-exact against (a + b + 1) >> 1 of each filter alone). Printed: the level against
// v17's sum (LPF + HPF) and against the input, for a split-band setting (LPF low, HPF high) and both wide open.
int parallel() {
	constexpr int kBlocks = 80;
	std::vector<float> in = makeMusic(kBlocks * kBlock);
	int failures = 0;
	// Returns the filter gain setConfig() gave (the same every block: the settings stay)
	auto run = [&](Ctx ctx, const Settings& s, std::vector<double>& out, std::vector<int32_t>& raw) {
		Runner r(ctx);
		for (int b = 0; b < kBlocks; b++) {
			r.block(s, in.data() + 2 * b * kBlock, kBlock, out, &raw);
		}
		return (double)r.lastGain;
	};
	auto rms = [](const std::vector<double>& v) {
		double e = 0;
		for (size_t i = v.size() / 4; i < v.size(); i++) { // after the first quarter (the filters settled)
			e += v[i] * v[i];
		}
		return e / (double)(v.size() - v.size() / 4);
	};
	struct Case {
		const char* name;
		double cut, hcut;
		FilterMode lp, hp;
	};
	const Case cases[] = {
	    {"split LP24 cut 15 / HPL cut 30", 15, 30, FilterMode::TRANSISTOR_24DB, FilterMode::HPLADDER},
	    {"split SVFb cut 18 / SVFb cut 32", 18, 32, FilterMode::SVF_BAND, FilterMode::SVF_BAND},
	    {"wide open LP12 cut 50 / HPL cut 0", 50, 0, FilterMode::TRANSISTOR_12DB, FilterMode::HPLADDER},
	};
	for (Ctx ctx : {Ctx::SYNTH, Ctx::KIT_ROW, Ctx::KIT}) {
		for (const Case& c : cases) {
			Settings both;
			both.route = FilterRoute::PARALLEL;
			both.lpfMode = c.lp;
			both.hpfMode = c.hp;
			both.cut = c.cut;
			both.hcut = c.hcut;
			Settings lpOnly = both, hpOnly = both, lpSeries = both, hpSeries = both, none = both;
			lpOnly.hpfMode = FilterMode::OFF;
			hpOnly.lpfMode = FilterMode::OFF;
			lpSeries = lpOnly;
			lpSeries.route = FilterRoute::HIGH_TO_LOW;
			hpSeries = hpOnly;
			hpSeries.route = FilterRoute::HIGH_TO_LOW;
			none.lpfMode = none.hpfMode = FilterMode::OFF;
			std::vector<double> oBoth, oLp, oHp, oLpS, oHpS, oNone, oLpBothGain, oHpBothGain;
			std::vector<int32_t> rBoth, rLp, rHp, rLpS, rHpS, rNone;
			double gBoth = run(ctx, both, oBoth, rBoth);
			double gLp = run(ctx, lpOnly, oLp, rLp);
			double gHp = run(ctx, hpOnly, oHp, rHp);
			run(ctx, lpSeries, oLpS, rLpS);
			run(ctx, hpSeries, oHpS, rHpS);
			run(ctx, none, oNone, rNone);
			bool okOne = rLp == rLpS && rHp == rHpS;
			// none: as in series with both off (the input with the caller's gain)
			Settings noneSeries = none;
			noneSeries.route = FilterRoute::HIGH_TO_LOW;
			std::vector<double> oNoneS;
			std::vector<int32_t> rNoneS;
			run(ctx, noneSeries, oNoneS, rNoneS);
			bool okNone = rNone == rNoneS;
			std::vector<double> dry;
			for (int i = 0; i < kBlocks * kBlock; i++) {
				dry.push_back(in[2 * i]);
				dry.push_back(in[2 * i + 1]);
			}
			// Both: each filter alone with both's gain (the voice's input carries both's: render each alone with it)
			// is what goes into the sum; the sum at half level against v17's full one
			double eBoth = rms(oBoth), eSum = 0, eIn = rms(dry);
			{
				// v17's parallel sum: each filter's output with both's gain compensation (the other filter's part in
				// it), summed at full level. At resonance 0 nothing saturates, so the gain's place (a voice's input, the
				// song's / a kit's output) doesn't matter: each alone, scaled by both's gain over its own
				std::vector<double> sum(oLp.size());
				for (size_t i = 0; i < sum.size(); i++) {
					sum[i] = oLp[i] * gBoth / gLp + oHp[i] * gBoth / gHp;
				}
				eSum = rms(sum);
			}
			double vsV17 = db(eBoth / eSum), vsIn = db(eBoth / eIn), v17VsIn = db(eSum / eIn);
			double eIn2 = rms(oNoneS); // the input as the filters get it (the caller's gain, none on)
			vsIn = db(eBoth / eIn2);
			v17VsIn = db(eSum / eIn2);
			(void)eIn;
			bool bad = !okOne || !okNone || std::fabs(vsV17 + 6.02) > 1.0;
			failures += bad;
			printf("parallel %-8s %-34s both: %+6.2f dB against v17 (LPF + HPF), %+6.2f dB against the input "
			       "(v17 %+6.2f); one on = that filter alone: %s; none on = the input: %s%s\n",
			       ctxName(ctx), c.name, vsV17, vsIn, v17VsIn, okOne ? "bit-exact" : "DIFFERENT",
			       okNone ? "yes" : "NO", bad ? "  FAIL" : "");
		}
	}
	return failures;
}
#endif

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
	if (!only || !strcmp(only, "fadein")) {
		failures += fadeIn();
	}
#ifdef FILTERSET_FADES
	if (!only || !strcmp(only, "slots")) {
		failures += slots();
	}
#endif
	if (!only || !strcmp(only, "jumps")) {
		failures += jumps();
	}
	if (!only || !strcmp(only, "hpres")) {
		failures += hpRes();
	}
#ifdef FILTER_PARAM_GLIDE
	if (!only || !strcmp(only, "glide")) {
		failures += glide();
	}
#endif
#ifdef FILTERSET_PARALLEL_HALF
	if (!only || !strcmp(only, "parallel")) {
		failures += parallel();
	}
#endif
#ifdef LPF_RAMP_HORNER
	if (!only || !strcmp(only, "tanh")) {
		failures += tanhExact();
	}
#endif
	if (failures) {
		printf("FAIL: %d cases with zipper or clicks above the limits\n", failures);
		return 1;
	}
	printf("ok: no zipper above %.0f dBc, no clicks on a change (compare the static hash with REF=)\n", kMaxZipper);
	return 0;
}
