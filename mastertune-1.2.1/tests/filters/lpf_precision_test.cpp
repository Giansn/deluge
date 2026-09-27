// The 12 dB and 24 dB LP ladders' rustle at low cutoff (the report on v17): the firmware's LpLadderFilter against a
// float64 model of the same ladder with the same coefficients (read from the filter) and a steady cutoff, as the
// ladder should be. What differs is the firmware's rounding and, up to v18, its analog noise on the cutoff (a heavily
// lowpassed random value, about 0.44 % rms, on every sample's moveability): the rustle, -21 to -42 dBc A-weighted.
//
// Per case (12 / 24 dB; a synth voice's filter with a 110 Hz saw at -44.5 dBFS, a kit's with a pad and drums mix at
// -39 dBFS; cutoff 5, 15, 20 on the display, 28 to 195 Hz; resonance 0, 25, 75 %): 1 s of signal, then 0.6 s of
// silence. Printed: the output level, the error against the model over the last 0.6 s of signal, A-weighted (dBFS and
// dBc) and above 2 kHz, and the tail: what the firmware still puts out in the last 0.3 s of silence.
//
// Fails where, at cutoff 5 or 15, the A-weighted error is above kMaxErrorA (v17: the analog noise, -65 to -85 dBFS, and
// the rounding below it, -161 to -178 dBFS; with the noise off and the stages keeping their state to 32 bits below the
// LSB, v18, it's at the output word's own rounding, -183 to -200), or, at resonance 0 and 25 %, the tail is above
// kMaxTail where the model's has died away (v17: -114 to -175 dBFS, the state stuck in the stages' dead band; v18:
// silent). Built by run.sh with lpladder.h's private members public (to read
// the coefficients) and, on the PC, fixedpoint.h's rounded multiplies rounding as the Deluge's smmulr / smmlar do.
#include "dsp/filter/lpladder.h"
#include "util/functions.h"
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace AudioEngine {
int32_t cpuDireness = 0;
bool renderInStereo = true;
} // namespace AudioEngine

using namespace deluge::dsp::filter;
extern const int16_t tanHSmall[];

namespace {
constexpr double kFs = 44100;
constexpr int kBlock = 128;
constexpr double P32 = 4294967296.0;
constexpr double kMaxErrorA = -180; // dBFS, A-weighted, at cutoff 5 and 15
constexpr double kMaxTail = -180;   // dBFS, at resonance 0 and 25 %, where the model's tail is below it
constexpr int kSignal = (int)(1.0 * kFs), kWarm = (int)(0.4 * kFs), kTail = (int)(0.6 * kFs);

int32_t knobValue(int32_t pos) { return (int32_t)std::min<int64_t>((int64_t)pos << 25, INT32_MAX); }
int32_t freqParam(int32_t neutral, int32_t knob) { return getExp(neutral, knob >> 2); }
int32_t linearParam(int32_t knob) {
	return lshiftAndSaturate<3>(multiply_32x32_rshift32((knob >> 2) + 536870912, 25 * 10737418));
}
int32_t knobFromDisplay(int32_t display) { return (int32_t)std::lround(display * 128.0 / 50) - 64; }

// getTanHUnknown without its roundings: the same table, interpolated exactly
double tanhTable(double x, int sat) {
	double wv = std::clamp(x * (1 << sat), -2147483648.0, 2147483647.0) + 2147483648.0;
	double pos = wv / 16777216.0;
	int i = std::min((int)pos, 255);
	double f = pos - i;
	return ((double)tanHSmall[i] * (1 - f) + (double)tanHSmall[i + 1] * f) * 65536.0 / (1 << (sat + 2));
}

struct Coefs {
	double m, pr, div, c1, c2, c3, d1;
};

// The 12 / 24 dB ladder in doubles: lpladder.cpp's do12dBLPFOnSample() / do24dBLPFOnSample() with every product exact
struct Model {
	bool twelve;
	Coefs c;
	bool saturate;
	int sat;
	double s[4] = {0, 0, 0, 0};
	double stage(int k, double x, double m, bool apf = false) {
		double a = (x - s[k]) * m / P32 * 2;
		double b = a + s[k];
		s[k] = b + a;
		return apf ? b * 2 - x : b;
	}
	double tick(double in) {
		double m = c.m; // a steady cutoff
		double fs = (s[0] * c.c1 + s[1] * c.c2 + (twelve ? s[2] * c.d1 : s[2] * c.c3 + s[3] * c.d1)) / P32 * 4;
		double x = (in - fs * c.pr / P32 * 8) * c.div / P32 * 4;
		if (saturate) {
			x = tanhTable(x, sat);
		}
		double y = stage(0, x, m);
		y = stage(1, y, m);
		if (twelve) {
			y = stage(2, y, m, true);
		}
		else {
			y = stage(2, y, m);
			y = stage(3, y, m);
		}
		return y * 2;
	}
};

struct HP2k { // 4th-order Butterworth high-pass at 2 kHz
	double z[2][2] = {{0, 0}, {0, 0}};
	double operator()(double h) {
		double w0 = 2 * M_PI * 2000 / kFs, cw = std::cos(w0), sw = std::sin(w0);
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
struct AWeight { // A-weighting from first-order bilinear sections, 0 dB at 1 kHz
	struct S {
		double b0, b1, a1, z = 0;
		double operator()(double x) {
			double y = b0 * x + z;
			z = b1 * x - a1 * y;
			return y;
		}
	};
	std::vector<S> s;
	double g = 1;
	AWeight() {
		const double f[6] = {20.598997, 20.598997, 107.65265, 737.86223, 12194.217, 12194.217};
		double K = 2 * kFs;
		for (int i = 0; i < 6; i++) {
			double w = 2 * kFs * std::tan(M_PI * f[i] / kFs);
			double a0 = K + w;
			s.push_back(i < 4 ? S{K / a0, -K / a0, (w - K) / a0} : S{w / a0, w / a0, (w - K) / a0});
		}
		std::complex<double> z = std::polar(1.0, -2 * M_PI * 1000 / kFs), h = 1;
		for (auto& x : s) {
			h *= (x.b0 + x.b1 * z) / (1.0 + x.a1 * z);
		}
		g = 1 / std::abs(h);
	}
	double operator()(double x) {
		for (auto& f : s) {
			x = f(x);
		}
		return x * g;
	}
};
double db(double p) { return 10 * std::log10(p + 1e-40); }

// A 110 Hz saw (band-limited, the synth's) or a pad + drums mix (a kit's), at an RMS level, in q31
std::vector<double> makeInput(bool mix, double levelDb) {
	std::vector<double> x(kSignal);
	uint32_t r = 22222;
	for (int i = 0; i < kSignal; i++) {
		double t = i / kFs, v = 0;
		if (!mix) {
			for (int h = 1; h * 110 < 20000; h++) {
				v += std::sin(2 * M_PI * 110 * h * t) / h;
			}
		}
		else {
			const double chord[4] = {130.81, 155.56, 196.0, 233.08};
			for (double f : chord) {
				for (int h = 1; h * f < 20000; h++) {
					v += 0.15 * std::sin(2 * M_PI * f * h * t) / h;
				}
			}
			double beat = std::fmod(t, 0.5);
			v += 0.6 * std::exp(-beat * 18) * std::sin(2 * M_PI * (50 + 80 * std::exp(-beat * 30)) * beat);
			r = r * 1664525u + 1013904223u;
			v += 0.25 * std::exp(-std::fmod(t + 0.25, 0.25) * 60) * ((int32_t)r / 2147483648.0);
		}
		x[i] = v;
	}
	double p = 0;
	for (double v : x) {
		p += v * v;
	}
	double sc = std::pow(10, levelDb / 20) / std::sqrt(p / kSignal) * 2147483648.0;
	for (double& v : x) {
		v = std::round(v * sc);
	}
	return x;
}
} // namespace

int main() {
	struct Ctx {
		const char* name;
		bool global;
		bool mix;
		double level;
	} ctxs[] = {{"synth", false, false, -44.5}, {"kit", true, true, -39.0}};
	int failures = 0;
	double worstA = -400, worstTail = -400;
	for (bool twelve : {false, true}) {
		for (auto& ctx : ctxs) {
			std::vector<double> in0 = makeInput(ctx.mix, ctx.level);
			for (int cut : {5, 15, 20}) {
				for (int res : {0, 13, 38}) {
					LpLadderFilter f{};
					FilterMode mode = twelve ? FilterMode::TRANSISTOR_12DB : FilterMode::TRANSISTOR_24DB;
					int32_t lpfRes = linearParam(knobValue(knobFromDisplay(res)));
					int32_t freq = freqParam(2000000, knobValue(knobFromDisplay(cut)));
					int32_t gainIn = ctx.global ? 167763968 : 134217728 << 1;
					int32_t gain = f.setConfig(freq, lpfRes, mode, 0, gainIn);
					int sat = 2;
#ifdef LPF_SATURATION_PER_CONTEXT
					f.setSaturation(ctx.global ? LpLadderFilter::kSaturationGlobal : LpLadderFilter::kSaturationVoice);
					sat = ctx.global && f.processedResonance > 510000000 ? 3 : 2;
#endif
					bool saturate = f.processedResonance > 510000000;
					// A voice's filter gain goes on its input (the oscillators' amplitude), a kit's on its output
					double inScale = ctx.global ? 1.0 : (double)gain / gainIn;
					double outScale = (ctx.global ? (double)gain / gainIn : 1.0) / 2147483648.0;
					Coefs c{(double)f.moveability,   (double)f.processedResonance, (double)f.divideByTotalMoveabilityAndProcessedResonance,
					        (double)f.lpf1Feedback,  (double)f.lpf2Feedback,       (double)f.lpf3Feedback,
					        (double)f.divideBy1PlusTannedFrequency};
					double hz = std::atan(c.m / (2147483648.0 - c.m)) * kFs / M_PI;
					int n = kSignal + kTail;
					std::vector<int32_t> buf(n);
					for (int i = 0; i < n; i++) {
						buf[i] = i < kSignal ? (int32_t)std::round(in0[i] * inScale) : 0;
					}
					Model md{twelve, c, saturate, sat};
					jcong = 380116160; // (the firmware up to v18 took its analog noise from it)
					std::vector<double> ref(n);
					for (int i = 0; i < n; i++) {
						ref[i] = md.tick(buf[i]) * outScale;
					}
					for (int s = 0; s < n; s += kBlock) {
						f.doFilter(&buf[s], &buf[std::min(s + kBlock, n)], 1);
					}
					HP2k he;
					AWeight as, ae;
					double sig = 0, sigA = 0, e = 0, eA = 0, eHF = 0, tail = 0, tailRef = 0;
					for (int i = 0; i < n; i++) {
						double y = buf[i] * outScale, d = y - ref[i];
						double va = as(ref[i]), da = ae(d), dh = he(d);
						if (i >= kWarm && i < kSignal) {
							sig += ref[i] * ref[i];
							sigA += va * va;
							e += d * d;
							eA += da * da;
							eHF += dh * dh;
						}
						if (i >= kSignal + kTail / 2) {
							tail += y * y;
							tailRef += ref[i] * ref[i];
						}
					}
					double N = kSignal - kWarm, NT = kTail - kTail / 2;
					sig = db(sig / N), sigA = db(sigA / N), eA = db(eA / N), eHF = db(eHF / N);
					tail = db(tail / NT), tailRef = db(tailRef / NT);
					bool badA = cut <= 15 && eA > kMaxErrorA;
					bool badTail = res <= 13 && tailRef < kMaxTail && tail > kMaxTail;
					if (cut <= 15) {
						worstA = std::max(worstA, eA);
					}
					if (res <= 13 && tailRef < kMaxTail) {
						worstTail = std::max(worstTail, tail);
					}
					failures += badA || badTail;
					printf("%s %-5s cutoff %2d (%5.1f Hz) res %2d%s: out %6.1f dBFS; error A %7.1f dBFS (%+6.1f dBc A), "
					       ">2k %7.1f dBFS; tail %7.1f dBFS (model %7.1f)%s%s\n",
					       twelve ? "12dB" : "24dB", ctx.name, cut, hz, res, saturate ? " sat" : "    ", sig, eA,
					       eA - sigA, eHF, tail, tailRef, badA ? "  ERROR FLOOR TOO HIGH" : "",
					       badTail ? "  TAIL NOT SILENT" : "");
					fflush(stdout);
				}
			}
		}
	}
	printf("worst: error A %.1f dBFS at cutoff 5 / 15 (limit %.0f), tail %.1f dBFS at resonance 0 / 25 %% (limit %.0f)\n",
	       worstA, kMaxErrorA, worstTail, kMaxTail);
	if (failures) {
		printf("FAIL: %d cases with the LP ladder's rustle or rounding above the limits\n", failures);
		return 1;
	}
	printf("ok: no rustle: the LP ladders at low cutoff are at the output word's own rounding from a steady ladder\n");
	return 0;
}
