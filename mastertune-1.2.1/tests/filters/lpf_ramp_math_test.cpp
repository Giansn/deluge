// The LP ladders' coefficient ramps and self-oscillation in float64 (mastertune v18 "filter neutral", spec points 5
// and 6): the firmware's LpLadderFilter::setConfig() gives the coefficients, a float64 model of lpladder.cpp's
// do12dBLPFOnSample() / do24dBLPFOnSample() (every product exact, no saturation: the linear part) gives the ladder's
// state-space matrices (A, B, C, D, probed with unit states and input) for any set of them.
//
// 1. ramp: the ladder moves every coefficient in a straight line per sample from the last block's set to this one's
//    (a jump of 30 Hz -> 18 kHz and back in one 128-sample block, the worst case, and 200 Hz -> 2 kHz). A set in the
//    middle of a ramp belongs to no real configuration (lpf1..3Feedback go with moveability^3..1, the divisor with
//    1 / (1 + k moveability^4)). Per mid-ramp set: the spectral radius of A (< 1: the frozen ladder is stable) and
//    its response against the real configuration with the same moveability and resonance (setConfig() at the cutoff
//    whose moveability that is): the largest difference in dB where the real one is within 12 dB of its maximum
//    (passband and resonance peak; below that, the stopband, where a difference isn't heard). Fails where the radius
//    reaches 1 or the difference exceeds kMaxDeviationDb while the ladder is linear (resonance below saturation).
// 2. selfosc: where the linear ladder starts to sing (the resonance where the radius reaches 1) and at what frequency
//    (the angle of that pole pair) against the configured cutoff (atan(tan) of setConfig()'s moveability), in cents;
//    and the firmware's own ladder (integer, with its tanh) at full resonance after an impulse: the frequency it
//    sings at, from its zero crossings over 1 s. Printed only (the ladders' feedback is 1.2.1's, which the files and
//    the sound keep: the ladders' own detuning is documented, not changed here).
// Built by run.sh with lpladder.h's private members public (as lpf_precision).
#include "dsp/filter/lpladder.h"
#include "util/functions.h"
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace AudioEngine {
int32_t cpuDireness = 0;
bool renderInStereo = true;
} // namespace AudioEngine

using namespace deluge::dsp::filter;
using cd = std::complex<double>;

namespace {
constexpr double kFs = 44100;
constexpr int kBlock = 128;
constexpr double P32 = 4294967296.0;
constexpr double kMaxDeviationDb = 0.5;

int32_t linearParam(double display) {
	double pos = display * 128.0 / 50 - 64;
	int32_t knob = (int32_t)std::clamp(pos * 33554432.0, -2147483648.0, 2147483647.0);
	return lshiftAndSaturate<3>(multiply_32x32_rshift32((knob >> 2) + 536870912, 25 * 10737418));
}

struct Coefs { // lpladder.h's Coefficients, as doubles
	double m, pr, div, c1, c2, c3, d1;
};
Coefs coefsOf(const LpLadderFilter& f) {
	return {(double)f.moveability,  (double)f.processedResonance, (double)f.divideByTotalMoveabilityAndProcessedResonance,
	        (double)f.lpf1Feedback, (double)f.lpf2Feedback,       (double)f.lpf3Feedback,
	        (double)f.divideBy1PlusTannedFrequency};
}
Coefs coefsOf(const LpLadderFilter::Coefficients& c) {
	return {(double)c.moveability,  (double)c.processedResonance, (double)c.divideByTotalMoveabilityAndProcessedResonance,
	        (double)c.lpf1Feedback, (double)c.lpf2Feedback,       (double)c.lpf3Feedback,
	        (double)c.divideBy1PlusTannedFrequency};
}
// The sets of a ramp from a to b over n samples, as the firmware steps them (lpladder.cpp renderLoop()): the knots
// from Filter::rampKnots() (b moving from a's configuration), within each piece knot + k * steps (truncated). PIECES=0
// in the environment: one straight line over the block (v18 before the pieces)
std::vector<Coefs> rampSets(const LpLadderFilter& a, const LpLadderFilter& b, int n, int* piecesOut) {
	LpLadderFilter f = b;
	f.lastParams_ = a.params_;
	LpLadderFilter::Knot<LpLadderFilter::Coefficients> knots[LpLadderFilter::kMaxKnots];
	const auto from = a.coefficients<FilterMode::TRANSISTOR_24DB>();
	const auto to = b.coefficients<FilterMode::TRANSISTOR_24DB>();
	int count;
	const char* env = getenv("PIECES");
	if (env && !strcmp(env, "0")) {
		count = 2;
		knots[0] = {0, from, from};
		knots[1] = {n, to, to};
	}
	else {
		count = f.rampKnots(n, from, to, knots);
	}
	*piecesOut = count - 1;
	if (getenv("DBG")) {
		for (int j = 0; j < count; j++) {
			printf("knot %d at %d: m %d pr %d -> m %d pr %d\n", j, knots[j].sample, knots[j].in.moveability,
			       knots[j].in.processedResonance, knots[j].out.moveability, knots[j].out.processedResonance);
		}
	}
	std::vector<Coefs> sets;
	LpLadderFilter::forEachPiece(knots, count,
	                             [&](int begin, int end, const LpLadderFilter::Coefficients& start,
	                                 const LpLadderFilter::Coefficients& d) {
		                             auto now = start;
		                             for (int k = begin; k < end; k++) {
			                             now.step(d);
			                             sets.push_back(coefsOf(now));
		                             }
	                             });
	if ((int)sets.size() != n) {
		printf("rampSets: %d sets for %d samples\n", (int)sets.size(), n);
		exit(1);
	}
	return sets;
}

// One sample of the linear ladder (lpf_precision's model without the tanh): state s (4, or 3 for 12 dB), input x;
// returns the output and updates s
double tick(bool twelve, const Coefs& c, double* s, double in) {
	auto stage = [&](int k, double x, bool apf) {
		double a = (x - s[k]) * c.m / P32 * 2;
		double b = a + s[k];
		s[k] = b + a;
		return apf ? b * 2 - x : b;
	};
	double fs = (s[0] * c.c1 + s[1] * c.c2 + (twelve ? s[2] * c.d1 : s[2] * c.c3 + s[3] * c.d1)) / P32 * 4;
	double x = (in - fs * c.pr / P32 * 8) * c.div / P32 * 4;
	double y = stage(0, x, false);
	y = stage(1, y, false);
	if (twelve) {
		y = stage(2, y, true);
	}
	else {
		y = stage(2, y, false);
		y = stage(3, y, false);
	}
	return y * 2;
}

struct StateSpace {
	int n;
	double A[4][4], B[4], C[4], D;
};
StateSpace probe(bool twelve, const Coefs& c) {
	StateSpace ss{};
	ss.n = twelve ? 3 : 4;
	for (int j = 0; j <= ss.n; j++) {
		double s[4] = {0, 0, 0, 0};
		double in = 0;
		if (j < ss.n) {
			s[j] = 1;
		}
		else {
			in = 1;
		}
		double y = tick(twelve, c, s, in);
		for (int i = 0; i < ss.n; i++) {
			if (j < ss.n) {
				ss.A[i][j] = s[i];
			}
			else {
				ss.B[i] = s[i];
			}
		}
		if (j < ss.n) {
			ss.C[j] = y;
		}
		else {
			ss.D = y;
		}
	}
	return ss;
}

// Eigenvalues: the characteristic polynomial (Faddeev-LeVerrier), its roots (Durand-Kerner). converged: false where
// the iteration didn't settle (repeated roots, as the ladder's four equal poles without resonance)
std::vector<cd> eigenvalues(const StateSpace& ss, bool* converged = nullptr) {
	int n = ss.n;
	double M[4][4] = {}, AM[4][4];
	std::vector<double> coef(n + 1); // p(z) = z^n + coef[1] z^(n-1) + ... + coef[n]
	coef[0] = 1;
	for (int k = 1; k <= n; k++) {
		// M_k = A M_(k-1) + c_(k-1) I, c_k = -tr(A M_k) / k
		for (int i = 0; i < n; i++) {
			for (int j = 0; j < n; j++) {
				double v = 0;
				for (int l = 0; l < n; l++) {
					v += ss.A[i][l] * M[l][j];
				}
				AM[i][j] = v + (i == j ? coef[k - 1] : 0);
			}
		}
		std::copy(&AM[0][0], &AM[0][0] + 16, &M[0][0]);
		double tr = 0;
		for (int i = 0; i < n; i++) {
			for (int l = 0; l < n; l++) {
				tr += ss.A[i][l] * M[l][i];
			}
		}
		coef[k] = -tr / k;
	}
	std::vector<cd> r(n);
	for (int i = 0; i < n; i++) {
		r[i] = std::pow(cd(0.4, 0.9), i);
	}
	for (int it = 0; it < 2000; it++) {
		double moved = 0;
		for (int i = 0; i < n; i++) {
			cd p = 1;
			for (int k = 1; k <= n; k++) {
				p = p * r[i] + coef[k];
			}
			cd q = 1;
			for (int j = 0; j < n; j++) {
				if (j != i) {
					q *= r[i] - r[j];
				}
			}
			cd d = p / q;
			r[i] -= d;
			moved = std::max(moved, std::abs(d));
		}
		if (moved < 1e-15) {
			if (converged) {
				*converged = true;
			}
			return r;
		}
	}
	if (converged) {
		*converged = false;
	}
	return r;
}
// The spectral radius; where the roots didn't settle, from Gelfand's formula instead: ||A^k||^(1/k), k = 2^24 by
// repeated squaring (normalised as it goes), within about 1e-6
double radius(const StateSpace& ss) {
	bool converged = false;
	double m = 0;
	for (cd e : eigenvalues(ss, &converged)) {
		m = std::max(m, std::abs(e));
	}
	if (converged) {
		return m;
	}
	int n = ss.n;
	double P[4][4], Q[4][4];
	std::copy(&ss.A[0][0], &ss.A[0][0] + 16, &P[0][0]);
	double logScale = 0; // log of the factor taken out of P so far, per power of A: A^k = P * exp(logScale * ...)
	double logNorm = 0;
	int k = 1;
	for (int it = 0; it < 24; it++) {
		double norm = 0;
		for (int i = 0; i < n; i++) {
			for (int j = 0; j < n; j++) {
				norm = std::max(norm, std::fabs(P[i][j]));
			}
		}
		// A^k = P * e^logNorm
		logNorm += std::log(norm);
		for (int i = 0; i < n; i++) {
			for (int j = 0; j < n; j++) {
				P[i][j] /= norm;
			}
		}
		for (int i = 0; i < n; i++) {
			for (int j = 0; j < n; j++) {
				double v = 0;
				for (int l = 0; l < n; l++) {
					v += P[i][l] * P[l][j];
				}
				Q[i][j] = v;
			}
		}
		std::copy(&Q[0][0], &Q[0][0] + 16, &P[0][0]);
		logNorm *= 2;
		k *= 2;
	}
	double norm = 0;
	for (int i = 0; i < n; i++) {
		for (int j = 0; j < n; j++) {
			norm = std::max(norm, std::fabs(P[i][j]));
		}
	}
	(void)logScale;
	return std::exp((logNorm + std::log(norm)) / k);
}

// H(e^jw) = D + C (zI - A)^-1 B
double gainAt(const StateSpace& ss, double f) {
	int n = ss.n;
	cd z = std::polar(1.0, 2 * M_PI * f / kFs);
	cd M[4][5];
	for (int i = 0; i < n; i++) {
		for (int j = 0; j < n; j++) {
			M[i][j] = (i == j ? z : cd(0)) - ss.A[i][j];
		}
		M[i][n] = ss.B[i];
	}
	for (int c = 0; c < n; c++) {
		int p = c;
		for (int i = c + 1; i < n; i++) {
			if (std::abs(M[i][c]) > std::abs(M[p][c])) {
				p = i;
			}
		}
		std::swap(M[c], M[p]);
		for (int i = 0; i < n; i++) {
			if (i != c) {
				cd f2 = M[i][c] / M[c][c];
				for (int j = c; j <= n; j++) {
					M[i][j] -= f2 * M[c][j];
				}
			}
		}
	}
	cd h = ss.D;
	for (int i = 0; i < n; i++) {
		h += ss.C[i] * M[i][n] / M[i][i];
	}
	return std::abs(h);
}

double cutoffHz(double moveability) { // the configured cutoff: moveability = g / (1 + g), g = tan(pi f / fs)
	double G = moveability / 2147483648.0;
	return std::atan(G / (1 - G)) * kFs / M_PI;
}

// A ladder configured at the frequency param whose moveability is the nearest to `m` (bisection; the moveability grows
// with the param)
LpLadderFilter configuredAtMoveability(FilterMode mode, double m, int32_t res) {
	int64_t lo = 0, hi = INT32_MAX;
	LpLadderFilter f{};
	while (hi - lo > 1) {
		int64_t mid = (lo + hi) / 2;
		f.configure((q31_t)mid, res, mode, 0, 134217728);
		if (f.moveability < m) {
			lo = mid;
		}
		else {
			hi = mid;
		}
	}
	f.configure((q31_t)hi, res, mode, 0, 134217728);
	return f;
}
LpLadderFilter configuredAtHz(FilterMode mode, double hz, int32_t res) {
	double G = std::tan(M_PI * hz / kFs);
	return configuredAtMoveability(mode, G / (1 + G) * 2147483648.0, res);
}

std::vector<double> logGrid() {
	std::vector<double> f;
	for (int i = 0; i <= 240; i++) {
		f.push_back(20 * std::pow(1000.0, i / 240.0));
	}
	return f;
}

// 1. ramp
int ramp() {
	int failures = 0;
	double worstDev = 0, worstRadius = 0;
	const std::vector<double> grid = logGrid();
	// One ramp from (fromHz, resonance resFrom) to (toHz, resTo) in one block
	auto check = [&](bool twelve, double fromHz, double toHz, double resFrom, double resTo) {
		FilterMode mode = twelve ? FilterMode::TRANSISTOR_12DB : FilterMode::TRANSISTOR_24DB;
		const int32_t rA = linearParam(resFrom), rB = linearParam(resTo);
		LpLadderFilter a = configuredAtHz(mode, fromHz, rA), b = configuredAtHz(mode, toHz, rB);
		double rad = 0, dev = 0, devAt = 0, devF = 0;
		bool unstable = false; // a mid-ramp set unstable where the real ladder at its moveability is stable
		bool linear = a.processedResonance <= 510000000 && b.processedResonance <= 510000000;
		int pieces = 0;
		const std::vector<Coefs> sets = rampSets(a, b, kBlock, &pieces);
		for (int k = 1; k <= kBlock; k++) {
			const Coefs& mid = sets[k - 1];
			StateSpace sm = probe(twelve, mid);
			double rm = radius(sm);
			rad = std::max(rad, rm);
			// The real ladder at this moveability, the resonance where the ramp has got to by now
			const int32_t r = (int32_t)(rA + ((int64_t)rB - rA) * k / kBlock);
			LpLadderFilter ref = configuredAtMoveability(mode, mid.m, r);
			StateSpace sr = probe(twelve, coefsOf(ref));
			unstable |= rm >= 1 && radius(sr) < 1;
			double peak = 0;
			std::vector<double> hr(grid.size()), hm(grid.size());
			for (size_t i = 0; i < grid.size(); i++) {
				hr[i] = gainAt(sr, grid[i]);
				hm[i] = gainAt(sm, grid[i]);
				peak = std::max(peak, hr[i]);
			}
			for (size_t i = 0; i < grid.size(); i++) {
				if (hr[i] >= peak * std::pow(10, -12 / 20.0)) {
					double d = std::fabs(20 * std::log10(hm[i] / hr[i]));
					if (d > dev) {
						dev = d;
						devAt = k;
						devF = grid[i];
					}
				}
			}
		}
		bool bad = unstable || (linear && dev > kMaxDeviationDb);
		failures += bad;
		worstRadius = std::max(worstRadius, rad);
		if (linear) {
			worstDev = std::max(worstDev, dev);
		}
		printf("ramp %s res %4.1f -> %4.1f %5.0f -> %5.0f Hz in %d samples, %2d pieces: largest radius %.6f, largest "
		       "difference from the real ladder %.2f dB (sample %3.0f, %5.0f Hz)%s%s%s\n",
		       twelve ? "12dB" : "24dB", resFrom, resTo, fromHz, toHz, kBlock, pieces, rad, dev, devAt, devF,
		       linear ? "" : " (saturating: not counted)", rad >= 1 && !unstable ? " (as the real ladder: sings)" : "",
		       bad ? "  FAIL" : "");
		fflush(stdout);
	};
	struct Jump {
		double from, to;
	} jumps[] = {{30, 18000}, {18000, 30}, {200, 2000}, {2000, 200}};
	for (bool twelve : {false, true}) {
		for (double res : {0.0, 13.0, 25.0, 38.0}) {
			for (Jump j : jumps) {
				check(twelve, j.from, j.to, res, res);
			}
		}
		// The resonance jumping (and with the cutoff)
		for (double hz : {100.0, 1000.0, 10000.0}) {
			check(twelve, hz, hz, 0, 13);
			check(twelve, hz, hz, 13, 0);
			check(twelve, hz, hz, 0, 50);
			check(twelve, hz, hz, 50, 0);
		}
		check(twelve, 30, 18000, 0, 13);
		check(twelve, 18000, 30, 13, 0);
	}
	printf("ramp: largest radius %.6f, largest difference %.2f dB (limit %.1f) where the ladder is linear\n",
	       worstRadius, worstDev, kMaxDeviationDb);
	return failures;
}

// 2. selfosc
void selfOsc() {
	for (bool twelve : {false, true}) {
		FilterMode mode = twelve ? FilterMode::TRANSISTOR_12DB : FilterMode::TRANSISTOR_24DB;
		for (double hz : {100.0, 300.0, 1000.0, 3000.0, 8000.0}) {
			// The linear ladder: the resonance (display) where it starts to sing
			double lo = 0, hi = 50;
			LpLadderFilter f = configuredAtHz(mode, hz, linearParam(50));
			bool sings = radius(probe(twelve, coefsOf(f))) >= 1;
			if (sings) {
				for (int it = 0; it < 40; it++) {
					double mid = (lo + hi) / 2;
					LpLadderFilter g = configuredAtHz(mode, hz, linearParam(mid));
					(radius(probe(twelve, coefsOf(g))) >= 1 ? hi : lo) = mid;
				}
			}
			LpLadderFilter g = configuredAtHz(mode, hz, linearParam(sings ? hi : 50));
			double fc = cutoffHz(g.moveability), fPole = 0, rMax = 0;
			for (cd e : eigenvalues(probe(twelve, coefsOf(g)))) {
				if (std::abs(e) > rMax && e.imag() > 0) {
					rMax = std::abs(e);
					fPole = std::arg(e) * kFs / (2 * M_PI);
				}
			}
			// The firmware's ladder at full resonance: an impulse, then 2 s; the frequency from the zero crossings of
			// the last second
			LpLadderFilter h = configuredAtHz(mode, hz, linearParam(50));
			h.setSaturation(LpLadderFilter::kSaturationVoice);
			std::vector<int32_t> buf((2 * (int)kFs + kBlock - 1) / kBlock * kBlock, 0); // whole blocks
			buf[0] = 1 << 24;
			for (size_t o = 0; o < buf.size(); o += kBlock) {
				h.filterMono(&buf[o], &buf[o] + kBlock, 1);
			}
			double first = -1, last = -1;
			int crossings = 0;
			double level = 0;
			for (size_t i = buf.size() / 2; i + 1 < buf.size(); i++) {
				level = std::max(level, std::fabs((double)buf[i]));
				if (buf[i] < 0 && buf[i + 1] >= 0) {
					double t = i + (double)-buf[i] / ((double)buf[i + 1] - buf[i]);
					if (first < 0) {
						first = t;
					}
					else {
						crossings++;
					}
					last = t;
				}
			}
			double fSing = crossings > 0 && level > 1 << 16 ? crossings / (last - first) * kFs : 0;
			printf("selfosc %s cutoff %6.1f Hz: the linear ladder %s at resonance %5.2f, pole %7.1f Hz (%+6.0f "
			       "cents); the firmware's at full resonance %s",
			       twelve ? "12dB" : "24dB", fc, sings ? "sings" : "doesn't sing even", sings ? hi : 50.0, fPole,
			       1200 * std::log2(fPole / fc), fSing > 0 ? "sings at " : "doesn't sing");
			if (fSing > 0) {
				printf("%7.1f Hz (%+6.0f cents), %.1f dBFS peak", fSing, 1200 * std::log2(fSing / fc),
				       20 * std::log10(level / 2147483648.0));
			}
			printf("\n");
			fflush(stdout);
		}
	}
}
// 3. edge: the linear ladder's spectral radius (1: where it starts to sing) at the top of the cutoff's range, per
// display cutoff and resonance, in ppm from 1 (printed only). Above 1 the noiseless ladder keeps a tone however slowly
// it grows (lpf_whistle: the 24 dB ladder at 80 % resonance and ~19.7 kHz, which 1.2.1's analog noise on the cutoff
// happened to damp)
void edge() {
	for (bool twelve : {false, true}) {
		FilterMode mode = twelve ? FilterMode::TRANSISTOR_12DB : FilterMode::TRANSISTOR_24DB;
		for (int cut = 36; cut <= 42; cut++) { // (from 41 on the cutoff is at its top: the same)
			int32_t knob = (int32_t)std::min<int64_t>((int64_t)std::lround(cut * 128.0 / 50 - 64) << 25, INT32_MAX);
			int32_t freq = getFinalParameterValueExp(2000000, cableToExpParamShortcut(knob)); // as lpf_whistle
			printf("edge %s cutoff %2d:", twelve ? "12dB" : "24dB", cut);
			for (int res = 36; res <= 50; res += 2) {
				LpLadderFilter f{};
				f.configure(freq, linearParam(res), mode, 0, 134217728);
				printf(" r%d %+8.0f", res, (radius(probe(twelve, coefsOf(f))) - 1) * 1e6);
			}
			LpLadderFilter f{};
			f.configure(freq, linearParam(40), mode, 0, 134217728);
			printf("  (%.0f Hz)\n", cutoffHz(f.moveability));
		}
	}
}
} // namespace

int main(int argc, char** argv) {
	const char* only = argc > 1 ? argv[1] : nullptr;
	int failures = 0;
	if (!only || !strcmp(only, "ramp")) {
		failures += ramp();
	}
	if (!only || !strcmp(only, "selfosc")) {
		selfOsc();
	}
	if (!only || !strcmp(only, "edge")) {
		edge();
	}
	if (failures) {
		printf("FAIL: %d ramps unstable or off the real ladder by more than %.1f dB\n", failures, kMaxDeviationDb);
		return 1;
	}
	printf("ok: every mid-ramp ladder stable and within %.1f dB of the real one\n", kMaxDeviationDb);
	return 0;
}
