// The bass / treble EQ of mastertune v18 (dsp/eq_shelves.h: TPT one-pole shelves) on the Deluge's Cortex-A9, against
// v17's (1.2.1's doEQ(), rebuilt here in scalar code exactly: forward-Euler one-poles, truncating products, wrapping
// sums, gains and corners set per block) and a float64 model of the shelves.
// Levels: 0 dBFS = a sine of peak 2^24 inside the firmware (the output's clip).
// Cases (argv[1]):
//   neutral    gains at the centre, any corners: output == input bit for bit, and a band that went back to the
//              centre stops after its ramp (bit-exact again).
//   response   steady-state gain at 20 Hz..20 kHz for bass / treble at +-6 dB, +12 dB, full cut and corners at the
//              centre / -3 / +3 / +6 octaves: v18 against the float64 TPT shelves (fails above 0.02 dB) and v17
//              printed beside it (its treble at +6 octaves does nothing: c past 1).
//   corner     the -3 dB point of each band at full cut, knobs' centre: v18 against v17's (fails 0.05 octave apart).
//   precision  a quiet tone (-90 dBFS) and a DC step through the bass at its lowest corner (3.1 Hz) and +12 dB:
//              the error against the float64 model (v17: dead band and DC bias) and where the output settles.
//   clicks     the gain jumping (centre -> +12 dB, -> full cut, and back), a band switched on and off, the corner
//              automated across its range per block, with a 100 + 500 Hz tone: energy above 6 kHz (dBc) in the
//              30 ms after each change against the tone's own (fails above -60 dBc, or 3 dB above the setting's own); v17 printed.
//   cpu        instructions per 128 samples: v17 both bands, v18 both bands steady / ramping, one band.
#include "dsp/eq_shelves.h"
#include "emu_count.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

using namespace deluge::dsp::eq;

namespace {
constexpr double kFs = 44100;
constexpr double kFullScale = 16777216.0; // 2^24
double db(double x) {
	return 20 * std::log10(std::max(x, 1e-30));
}

// ---- v17's EQ (mod_controllable_audio.cpp / track_fx_kernels.h eqChannel() at mastertune-v17 2cb5e31b)
int32_t mul32(int32_t a, int32_t b) {
	return (int32_t)(((int64_t)a * b) >> 32);
}
int32_t getExpLike(int32_t base, int32_t adj) {
	// getExp(base, adj): base * 2^(adj / 2^26), saturating (the table's interpolation: exact exp2 here, within its
	// few ppm)
	double v = base * std::exp2(adj / 67108864.0);
	return (int32_t)std::min(v, 2147483647.0);
}
struct V17Eq {
	int32_t withoutTrebleL = 0, withoutTrebleR = 0, bassOnlyL = 0, bassOnlyR = 0;
	void channel(int32_t& input, int32_t& withoutTreble, int32_t& bassOnly, bool doBass, bool doTreble,
	             int32_t bassFreq, int32_t trebleFreq, int32_t bassAmount, int32_t trebleAmount) {
		int32_t trebleOnly = 0;
		if (doTreble) {
			int32_t d = (int32_t)((uint32_t)input - (uint32_t)withoutTreble);
			withoutTreble = (int32_t)((uint32_t)withoutTreble + (uint32_t)(mul32(d, trebleFreq) << 1));
			trebleOnly = (int32_t)((uint32_t)input - (uint32_t)withoutTreble);
			input = withoutTreble;
		}
		if (doBass) {
			int32_t d = (int32_t)((uint32_t)input - (uint32_t)bassOnly);
			bassOnly = (int32_t)((uint32_t)bassOnly + (uint32_t)mul32(d, bassFreq));
		}
		if (doTreble) {
			input = (int32_t)((uint32_t)input + (uint32_t)(mul32(trebleOnly, trebleAmount) << 3));
		}
		if (doBass) {
			input = (int32_t)((uint32_t)input + (uint32_t)(mul32(bassOnly, bassAmount) << 3));
		}
	}
	void process(StereoSample* buf, int32_t n, int32_t bass, int32_t treble, int32_t bassFreqP, int32_t trebleFreqP) {
		bool doBass = bass != 0, doTreble = treble != 0;
		int32_t positive = (bass >> 1) + 1073741824;
		int32_t bassAmount = (int32_t)(((((int64_t)positive * positive) + (1LL << 31)) >> 32) << 1) - 536870912;
		positive = (treble >> 1) + 1073741824;
		int32_t trebleAmount = (int32_t)((((int64_t)positive * positive) + (1LL << 31)) >> 32) << 1;
		if (!doBass && !doTreble) {
			return;
		}
		int32_t bassFreq = getExpLike(120000000, (bassFreqP >> 5) * 6);
		int32_t trebleFreq = getExpLike(700000000, (trebleFreqP >> 5) * 6);
		for (int32_t i = 0; i < n; i++) {
			channel(buf[i].l, withoutTrebleL, bassOnlyL, doBass, doTreble, bassFreq, trebleFreq, bassAmount,
			        trebleAmount);
			channel(buf[i].r, withoutTrebleR, bassOnlyR, doBass, doTreble, bassFreq, trebleFreq, bassAmount,
			        trebleAmount);
		}
	}
};

// ---- the float64 model of v18's shelves (constant settings)
struct ModelEq {
	double sb = 0, st = 0;
	double gb, gt, Gb, Gt;
	ModelEq(int32_t bass, int32_t treble, int32_t bassFreqP, int32_t trebleFreqP) {
		gb = gainForParam(bass) / 536870912.0;
		gt = gainForParam(treble) / 536870912.0;
		auto G = [](double log2f) {
			double g = std::tan(M_PI * std::exp2(log2f) / kFs);
			return g / (1 + g);
		};
		Gb = G(log2CornerForParam(bassFreqP, kBassCorner));
		Gt = G(log2CornerForParam(trebleFreqP, kTrebleCorner));
	}
	double step(double x) {
		double v = (x - st) * Gt, lp = st + v;
		st += 2 * v;
		double y = x + (gt - 1) * (x - lp);
		v = (y - sb) * Gb;
		lp = sb + v;
		sb += 2 * v;
		return y + (gb - 1) * lp;
	}
};

// The stored gain param for a gain in dB (4 p^2 = g, param = (2p - 1) 2^31); the centre is 0
int32_t gainParam(double dbGain) {
	if (dbGain <= -200) {
		return INT32_MIN;
	}
	double p = std::sqrt(std::pow(10.0, dbGain / 20) / 4);
	return (int32_t)std::clamp((2 * p - 1) * 2147483648.0, -2147483648.0, 2147483647.0);
}
// The frequency param for a corner that many octaves from the centre ((param >> 5) * 6 / 2^26 octaves)
int32_t freqParam(double octaves) {
	return (int32_t)std::clamp(octaves / 6 * 2147483648.0, -2147483648.0, 2147483647.0);
}

// Steady-state gain (dB) of a processor at f: a sine of -20 dBFS, the second half of 16384 samples correlated
template <typename Proc>
double gainAt(Proc proc, double f) {
	const int n = 16384;
	std::vector<StereoSample> buf(n);
	for (int i = 0; i < n; i++) {
		int32_t v = (int32_t)std::lround(0.1 * kFullScale * std::sin(2 * M_PI * f * i / kFs));
		buf[i].l = v;
		buf[i].r = v;
	}
	for (int b = 0; b < n; b += 128) {
		proc(&buf[b], 128);
	}
	// (over a whole number of periods in the second half: no leakage from the window)
	const int m = (int)std::lround(std::floor((n / 2) * f / kFs) * kFs / f);
	double c = 0, s = 0;
	for (int i = n - m; i < n; i++) {
		c += buf[i].l * std::cos(2 * M_PI * f * i / kFs);
		s += buf[i].l * std::sin(2 * M_PI * f * i / kFs);
	}
	double amp = 2 * std::sqrt(c * c + s * s) / m;
	return db(amp / (0.1 * kFullScale));
}
double modelGainAt(const ModelEq& m0, double f) {
	ModelEq m = m0;
	const int n = 16384;
	const int w = (int)std::lround(std::floor((n / 2) * f / kFs) * kFs / f);
	double c = 0, s = 0;
	for (int i = 0; i < n; i++) {
		double y = m.step(std::sin(2 * M_PI * f * i / kFs));
		if (i >= n - w) {
			c += y * std::cos(2 * M_PI * f * i / kFs);
			s += y * std::sin(2 * M_PI * f * i / kFs);
		}
	}
	return db(2 * std::sqrt(c * c + s * s) / w);
}

// ---- neutral
int neutral() {
	int bad = 0;
	std::vector<StereoSample> in(4096), buf;
	uint32_t r = 1;
	for (auto& s : in) {
		r = r * 1664525u + 1013904223u;
		s.l = (int32_t)r >> 4;
		r = r * 1664525u + 1013904223u;
		s.r = (int32_t)r >> 4;
	}
	for (int32_t f : {(int32_t)INT32_MIN, (int32_t)-300000000, (int32_t)0, (int32_t)700000000, (int32_t)INT32_MAX}) {
		EqShelves eq;
		buf = in;
		for (int b = 0; b < 4096; b += 128) {
			eq.process(&buf[b], 128, 0, 0, f, -f);
		}
		bad += memcmp(buf.data(), in.data(), in.size() * sizeof(StereoSample)) != 0;
	}
	// A band on, then back at the centre: after its ramp it stops, and the output is the input again
	EqShelves eq;
	buf = in;
	int stoppedAfter = -1;
	for (int b = 0; b < 4096; b += 128) {
		int32_t bass = b < 1024 ? gainParam(6) : 0, treble = b < 512 ? gainParam(-6) : 0;
		eq.process(&buf[b], 128, bass, treble, 0, 0);
		if (b >= 1024 && !eq.isActive() && stoppedAfter < 0) {
			stoppedAfter = b + 128 - 1024;
		}
	}
	bool tailExact = memcmp(&buf[2048], &in[2048], 2048 * sizeof(StereoSample)) == 0;
	bad += !tailExact || stoppedAfter < 0;
	printf("neutral: centre gains, 5 corner settings: output %s input; a band back at the centre stops %d samples "
	       "later (its 5 ms ramp), then output %s input\n",
	       bad ? "DIFFERS from" : "==", stoppedAfter, tailExact ? "==" : "DIFFERS from");
	return bad ? 1 : 0;
}

// ---- response
int response() {
	struct Case {
		const char* name;
		double bassDb, trebleDb, bassOct, trebleOct;
	};
	const Case cases[] = {
	    {"bass +6 dB", 6, 0, 0, 0},           {"bass -6 dB", -6, 0, 0, 0},
	    {"bass +12 dB (max)", 12.04, 0, 0, 0}, {"bass full cut", -300, 0, 0, 0},
	    {"bass +6 dB, -3 oct", 6, 0, -3, 0},   {"bass +6 dB, +3 oct", 6, 0, 3, 0},
	    {"treble +6 dB", 0, 6, 0, 0},          {"treble -6 dB", 0, -6, 0, 0},
	    {"treble full cut", 0, -300, 0, 0},    {"treble +6 dB, +3 oct", 0, 6, 0, 3},
	    {"treble +6 dB, +6 oct", 0, 6, 0, 6},  {"treble +6 dB, -3 oct", 0, 6, 0, -3},
	    {"both +6 / -6 dB", 6, -6, 0, 0},
	};
	const double freqs[] = {20, 50, 100, 200, 500, 1000, 2000, 3000, 5000, 10000, 15000, 18000};
	double worst = 0;
	for (const Case& c : cases) {
		int32_t b = gainParam(c.bassDb), t = gainParam(c.trebleDb), bf = freqParam(c.bassOct),
		        tf = freqParam(c.trebleOct);
		ModelEq model(b, t, bf, tf);
		printf("response %-22s", c.name);
		for (double f : freqs) {
			EqShelves eq;
			double v18 = gainAt([&](StereoSample* s, int n) { eq.process(s, n, b, t, bf, tf); }, f);
			V17Eq old;
			double v17 = gainAt([&](StereoSample* s, int n) { old.process(s, n, b, t, bf, tf); }, f);
			double m = modelGainAt(model, f);
			// (full cut: -60 dB and below, where the -20 dBFS tone is near the rounding: to 0.1 dB)
			double err = std::fabs(v18 - m);
			if (m > -50) {
				worst = std::max(worst, err);
			}
			printf(" %5.0f:%+6.2f(%+6.2f)", f, v18, v17);
		}
		printf("\n");
	}
	printf("response: v18 (v17) in dB at the frequencies; v18 against the float64 shelves: at most %.4f dB "
	       "(limit 0.02)\n",
	       worst);
	return worst > 0.02 ? 1 : 0;
}

// ---- corner: the -3 dB point of each band's lowpass at the centre (bass full cut: the lowshelf is x - lp, a
// highpass whose -3 dB point is the lowpass's; treble full cut: the lowpass itself)
int corner() {
	auto find = [](auto gainFn, bool rising) {
		double lo = 5, hi = 21000;
		for (int i = 0; i < 40; i++) {
			double mid = std::sqrt(lo * hi);
			double g = gainFn(mid);
			bool above = g > -3.0103;
			((above == rising) ? hi : lo) = mid;
		}
		return std::sqrt(lo * hi);
	};
	int32_t cut = INT32_MIN;
	double v18b = find(
	    [&](double f) {
		    EqShelves eq;
		    return gainAt([&](StereoSample* s, int n) { eq.process(s, n, cut, 0, 0, 0); }, f);
	    },
	    true);
	double v17b = find(
	    [&](double f) {
		    V17Eq eq;
		    return gainAt([&](StereoSample* s, int n) { eq.process(s, n, cut, 0, 0, 0); }, f);
	    },
	    true);
	double v18t = find(
	    [&](double f) {
		    EqShelves eq;
		    return gainAt([&](StereoSample* s, int n) { eq.process(s, n, 0, cut, 0, 0); }, f);
	    },
	    false);
	double v17t = find(
	    [&](double f) {
		    V17Eq eq;
		    return gainAt([&](StereoSample* s, int n) { eq.process(s, n, 0, cut, 0, 0); }, f);
	    },
	    false);
	// (v17's bass cut is x minus its forward-Euler lowpass, whose -3 dB point lies 3 % above the lowpass's own, 198.9
	// Hz, which v18 takes as the corner: the TPT lowpass and x minus it share theirs)
	double eb = std::fabs(std::log2(v18b / v17b)), et = std::fabs(std::log2(v18t / v17t));
	printf("corner: bass cut -3 dB at %.1f Hz (v17 %.1f), treble cut %.1f Hz (v17 %.1f): %.3f / %.3f octaves apart "
	       "(limit 0.05)\n",
	       v18b, v17b, v18t, v17t, eb, et);
	return (eb > 0.05 || et > 0.05) ? 1 : 0;
}

// ---- precision
int precision() {
	const int32_t b = gainParam(12.04), bf = INT32_MIN; // +12 dB below 3.1 Hz
	const int n = 128 * 690; // 2 s, whole blocks
	// A -90 dBFS 1 kHz tone on a DC step of -60 dBFS that goes away after 0.5 s
	auto input = [&](int i) {
		double dc = i < n / 4 ? 0.001 * kFullScale : 0;
		return dc + 3.16e-5 * kFullScale * std::sin(2 * M_PI * 1000 * i / kFs);
	};
	std::vector<StereoSample> a(n), c(n);
	for (int i = 0; i < n; i++) {
		a[i].l = a[i].r = (int32_t)std::lround(input(i));
	}
	c = a;
	EqShelves eq;
	V17Eq old;
	for (int i = 0; i < n; i += 128) {
		eq.process(&a[i], 128, b, 0, bf, 0);
		old.process(&c[i], 128, b, 0, bf, 0);
	}
	ModelEq m(b, 0, bf, 0);
	double e18 = 0, e17 = 0, dc18 = 0, dc17 = 0;
	int cnt = 0;
	for (int i = 0; i < n; i++) {
		double y = m.step(std::lround(input(i)));
		if (i > n / 2) {
			e18 += (a[i].l - y) * (a[i].l - y);
			e17 += (c[i].l - y) * (c[i].l - y);
			dc18 += a[i].l - y;
			dc17 += c[i].l - y;
			cnt++;
		}
	}
	auto dbfs = [](double rms) { return db(rms / (kFullScale / std::sqrt(2.0))); };
	double r18 = dbfs(std::sqrt(e18 / cnt)), r17 = dbfs(std::sqrt(e17 / cnt));
	printf("precision: bass +12 dB at 3.1 Hz, -90 dBFS tone after a DC step: error against float64 %.1f dBFS "
	       "(v17 %.1f), DC %.2f LSB (v17 %.2f) (fails above -130 dBFS)\n",
	       r18, r17, dc18 / cnt, dc17 / cnt);
	return r18 > -130 ? 1 : 0;
}

// ---- clicks
// Energy above 6 kHz (4th-order Butterworth high-pass, float64) in the 30 ms after sample `at`, against the tone's
struct HP {
	double z[2][2] = {{0, 0}, {0, 0}};
	double operator()(double h) {
		double w0 = 2 * M_PI * 6000 / kFs, cw = std::cos(w0), sw = std::sin(w0);
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
template <typename Proc>
double burst(Proc proc, int at, int n) {
	std::vector<StereoSample> buf(n);
	double e = 0;
	for (int i = 0; i < n; i++) {
		double v = 0.1 * kFullScale * (std::sin(2 * M_PI * 100 * i / kFs) + 0.3 * std::sin(2 * M_PI * 500 * i / kFs));
		buf[i].l = buf[i].r = (int32_t)std::lround(v);
		e += v * v;
	}
	for (int b = 0; b < n; b += 128) {
		proc(&buf[b], 128, b);
	}
	HP hp;
	double h = 0;
	for (int i = 0; i < n; i++) {
		double y = hp(buf[i].l);
		if (i >= at && i < at + 1323) {
			h += y * y;
		}
	}
	return 10 * std::log10(std::max(h / 1323, 1e-30) / (e / n));
}
int clicks() {
	const int n = 128 * 64, at = 128 * 32;
	struct Case {
		const char* name;
		double fromDb, toDb;
		bool treble;
	};
	const Case cases[] = {{"bass centre -> +12 dB", 0, 12.04, false}, {"bass +12 dB -> centre", 12.04, 0, false},
	                      {"bass centre -> full cut", 0, -300, false}, {"bass one step on (+0.7 dB)", 0, 0.7, false},
	                      {"bass one step off", 0.7, 0, false},        {"treble centre -> +12 dB", 0, 12.04, true},
	                      {"treble centre -> full cut", 0, -300, true}, {"treble one step off", -0.7, 0, true}};
	int bad = 0;
	for (const Case& c : cases) {
		auto params = [&](int b, int32_t& bass, int32_t& treble) {
			int32_t g = gainParam(b < at ? c.fromDb : c.toDb);
			if (std::fabs(b < at ? c.fromDb : c.toDb) < 1e-9) {
				g = 0;
			}
			bass = c.treble ? 0 : g;
			treble = c.treble ? g : 0;
		};
		EqShelves eq;
		double v18 = burst(
		    [&](StereoSample* s, int len, int b) {
			    int32_t bass, treble;
			    params(b, bass, treble);
			    eq.process(s, len, bass, treble, 0, 0);
		    },
		    at, n);
		V17Eq old;
		double v17 = burst(
		    [&](StereoSample* s, int len, int b) {
			    int32_t bass, treble;
			    params(b, bass, treble);
			    old.process(s, len, bass, treble, 0, 0);
		    },
		    at, n);
		// The same without the change (the new setting from the start), for what the setting itself lets through
		EqShelves eq2;
		double settled = burst(
		    [&](StereoSample* s, int len, int) {
			    int32_t bass, treble;
			    params(at, bass, treble);
			    eq2.process(s, len, bass, treble, 0, 0);
		    },
		    at, n);
		bool fail = v18 > std::max(-60.0, settled + 3);
		bad += fail;
		printf("clicks %-28s above 6 kHz in the 30 ms after: %6.1f dBc (v17 %6.1f; settled %6.1f)%s\n", c.name, v18,
		       v17, settled, fail ? "  FAIL" : "");
	}
	// The corner automated: bass +12 dB, its corner from -6 to +6 octaves over 0.5 s, stepped per block (v17: steps)
	for (bool treble : {false, true}) {
		auto proc = [&](auto& e) {
			return burst(
			    [&](StereoSample* s, int len, int b) {
				    double oct = -6 + 12.0 * b / n;
				    int32_t g = gainParam(12.04);
				    e.process(s, len, treble ? 0 : g, treble ? g : 0, treble ? 0 : freqParam(oct),
				              treble ? freqParam(oct) : 0);
			    },
			    0, n);
		};
		EqShelves eq;
		V17Eq old;
		double v18 = proc(eq), v17 = proc(old);
		printf("clicks %-28s above 6 kHz: %6.1f dBc (v17 %6.1f)\n", treble ? "treble corner swept" : "bass corner swept",
		       v18, v17);
	}
	return bad;
}

// ---- cpu
int cpu() {
	constexpr int n = 128;
	static StereoSample buf[n];
	for (int i = 0; i < n; i++) {
		buf[i].l = buf[i].r = (int32_t)(0.1 * kFullScale * std::sin(2 * M_PI * 440 * i / kFs));
	}
	int32_t b = gainParam(6), t = gainParam(-6);
	V17Eq old;
	old.process(buf, n, b, t, 0, 0);
	EMU_COUNT_BEGIN("v17 bass + treble");
	old.process(buf, n, b, t, 0, 0);
	EMU_COUNT_END();
	EqShelves eq;
	for (int k = 0; k < 8; k++) {
		eq.process(buf, n, b, t, 0, 0);
	}
	EMU_COUNT_BEGIN("v18 bass + treble, steady");
	eq.process(buf, n, b, t, 0, 0);
	EMU_COUNT_END();
	EMU_COUNT_BEGIN("v18 bass + treble, gains and corners moving");
	eq.process(buf, n, gainParam(7), gainParam(-7), 10000000, 10000000);
	EMU_COUNT_END();
	EqShelves one;
	for (int k = 0; k < 8; k++) {
		one.process(buf, n, b, 0, 0, 0);
	}
	EMU_COUNT_BEGIN("v18 bass only, steady");
	one.process(buf, n, b, 0, 0, 0);
	EMU_COUNT_END();
	EqShelves off;
	EMU_COUNT_BEGIN("v18 both at the centre (off)");
	off.process(buf, n, 0, 0, 0, 0);
	EMU_COUNT_END();
	printf("(counts per call of 128 samples below; the v17 loop here is scalar C++ like the firmware's)\n");
	return 0;
}
} // namespace

int main(int argc, char** argv) {
	const char* c = argc > 1 ? argv[1] : "neutral";
	if (!strcmp(c, "neutral")) {
		return neutral();
	}
	if (!strcmp(c, "response")) {
		return response();
	}
	if (!strcmp(c, "corner")) {
		return corner();
	}
	if (!strcmp(c, "precision")) {
		return precision();
	}
	if (!strcmp(c, "clicks")) {
		return clicks();
	}
	if (!strcmp(c, "cpu")) {
		return cpu();
	}
	printf("unknown case %s\n", c);
	return 2;
}
