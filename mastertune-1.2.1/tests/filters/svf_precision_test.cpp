// The SVF's arithmetic at low cutoff (mastertune v18 "filter neutral", spec point 2: rounded products in every
// recursive state): the firmware's SVFilter against a float64 model of the same filter (svf.cpp's doSVF() with every
// product exact and the same tanh table, interpolated exactly) with the same coefficients (read from the filter).
// What differs is the firmware's rounding: 1.2.1 truncated every product (smmul), on average half an LSB low, which
// the two integrators add up into an offset and, at low cutoff, a dead band where the state stops following its input.
//
// Per case (a synth voice's filter with a 110 Hz saw at -44.5 dBFS, a kit's with a pad and drums mix at -39 dBFS;
// cutoff 5, 15, 20 on the display; resonance 0, 25, 75 %; the SVF as LPF, band mode, morph 0): 1 s of signal, then
// 0.6 s of silence. Printed: the output level, the error against the model over the last 0.6 s of signal, A-weighted
// (dBFS and dBc), its DC (the offset) and the tail: what the firmware still puts out in the last 0.3 s of silence.
// Fails where, at cutoff 5 or 15, the error's DC is above kMaxDc or the A-weighted error above kMaxErrorA, or the tail
// is above kMaxTail where the model's has died away (the output word's LSB, 24 bit, is at -150 dBFS here). v17 (smmul,
// getTanHUnknown()): DC -88 to -106 dBFS, error A -154 to -171, tail -101 to -133 dBFS; v18 (rounded, the band's tanh
// at full resolution, the integrators precise below fc 1/4): DC -175 to -228, error A -184 to -189, tail -178 (three
// LSB of the tripled output, a rounded half LSB) where the model's dies away. Built by run.sh with svf.h's private members public and, on the
// PC, fixedpoint.h's rounded multiplies rounding as the Deluge's smmulr / smmlar do.
#include "dsp/filter/svf.h"
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
constexpr double kMaxDc = -170;     // dBFS, the error's mean, at cutoff 5 and 15
constexpr double kMaxTail = -175;   // dBFS, at resonance 0 and 25 %, where the model's tail is below it
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

// svf.cpp's doSVF() in doubles, every product exact
struct Model {
	double in, fc, q, cLow, cBand, cHigh;
	bool bandMode;
	double low = 0, band = 0;
	double tick(double x) {
		x = in * x / P32;
		auto half = [&](double& lo, double& hi, double& bd) {
			lo = lo + 2 * bd * fc / P32;
			hi = x - lo - 2 * bd * q / P32;
			bd = 2 * hi * fc / P32 + bd;
		};
		double high = 0;
		half(low, high, band);
		band = tanhTable(band, 3);
		double lowi = low, highi = high, bandi = band;
		half(low, high, band);
		lowi += low;
		highi += high;
		bandi += band;
		double result = lowi * cLow / P32 + highi * cHigh / P32 + (bandMode ? bandi * cBand / P32 : 0);
		band = tanhTable(band, 3);
		return 3 * result;
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
		bool mix;
		double level;
	} ctxs[] = {{"synth", false, -44.5}, {"kit", true, -39.0}};
	int failures = 0;
	double worstA = -400, worstDc = -400, worstTail = -400;
	for (auto& ctx : ctxs) {
		std::vector<double> in0 = makeInput(ctx.mix, ctx.level);
		for (int cut : {5, 15, 20}) {
			for (int res : {0, 13, 38}) {
				SVFilter f{};
				int32_t svfRes = linearParam(knobValue(knobFromDisplay(res)));
				int32_t freq = freqParam(2000000, knobValue(knobFromDisplay(cut)));
				f.setConfig(freq, svfRes, FilterMode::SVF_BAND, 0, 134217728);
				Model md{(double)f.in,    (double)f.fc,     (double)f.q,     (double)f.c_low,
				         (double)f.c_band, (double)f.c_high, f.band_mode};
				int n = kSignal + kTail;
				std::vector<int32_t> buf(n);
				for (int i = 0; i < n; i++) {
					buf[i] = i < kSignal ? (int32_t)in0[i] : 0;
				}
				std::vector<double> ref(n);
				for (int i = 0; i < n; i++) {
					ref[i] = md.tick(buf[i]) / 2147483648.0;
				}
				for (int s = 0; s < n; s += kBlock) {
					f.doFilter(&buf[s], &buf[std::min(s + kBlock, n)], 1);
				}
				AWeight as, ae;
				double sig = 0, sigA = 0, eA = 0, dc = 0, tail = 0, tailRef = 0;
				for (int i = 0; i < n; i++) {
					double y = buf[i] / 2147483648.0, d = y - ref[i];
					double va = as(ref[i]), da = ae(d);
					if (i >= kWarm && i < kSignal) {
						sig += ref[i] * ref[i];
						sigA += va * va;
						eA += da * da;
						dc += d;
					}
					if (i >= kSignal + kTail / 2) {
						tail += y * y;
						tailRef += ref[i] * ref[i];
					}
				}
				double N = kSignal - kWarm, NT = kTail - kTail / 2;
				sig = db(sig / N), sigA = db(sigA / N), eA = db(eA / N);
				dc = 20 * std::log10(std::fabs(dc / N) + 1e-20);
				tail = db(tail / NT), tailRef = db(tailRef / NT);
				bool badA = cut <= 15 && (eA > kMaxErrorA || dc > kMaxDc);
				bool badTail = res <= 13 && tailRef < kMaxTail && tail > kMaxTail;
				if (cut <= 15) {
					worstA = std::max(worstA, eA);
					worstDc = std::max(worstDc, dc);
				}
				if (res <= 13 && tailRef < kMaxTail) {
					worstTail = std::max(worstTail, tail);
				}
				failures += badA || badTail;
				printf("SVF %-5s cutoff %2d res %2d: out %6.1f dBFS; error A %7.1f dBFS (%+6.1f dBc A), DC "
				       "%7.1f dBFS; tail %7.1f dBFS (model %7.1f)%s%s\n",
				       ctx.name, cut, res, sig, eA, eA - sigA, dc, tail, tailRef,
				       badA ? "  ERROR TOO HIGH" : "", badTail ? "  TAIL NOT SILENT" : "");
				fflush(stdout);
			}
		}
	}
	printf("worst: error A %.1f dBFS, DC %.1f dBFS at cutoff 5 / 15 (limits %.0f, %.0f), tail %.1f dBFS at resonance "
	       "0 / 25 %% (limit %.0f)\n",
	       worstA, worstDc, kMaxErrorA, kMaxDc, worstTail, kMaxTail);
	if (failures) {
		printf("FAIL: %d cases with the SVF's rounding above the limits\n", failures);
		return 1;
	}
	printf("ok: the SVF at low cutoff rounds without an offset\n");
	return 0;
}
