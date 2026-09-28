// The output limiter (mastertune v18, dsp/output_limiter.cpp) on the Deluge's Cortex-A9. Levels: 0 dBFS = 2^24 inside
// the firmware (the output's full scale, where lshiftAndSaturate<8> clips). Off, the firmware doesn't call it (the
// output is bit for bit as before); here, on:
//   below     a sine peaking at -1.5 dBFS (under the knee at -1 dB) and music-like noise: the output is the input
//             delayed by 32 samples, bit for bit
//   boost     sines of 1 / 5 / 7 / 11 kHz driven 3 / 6 / 12 dB over full scale: the hard clip (what the output did
//             without it) against the limiter: samples at full scale, harmonics (THD) and aliasing (everything off the
//             harmonics, as the 44.1 kHz output folds them); fails where the limiter's output reaches full scale or
//             its aliasing isn't at least 20 dB below the hard clip's
//   cpu       instructions per block of 128: idle (quiet input) and active (clipping)
#include "dsp/output_limiter.h"
#include "emu_count.h"
#include <cmath>
#include <complex>
#include <cstdio>
#include <cstring>
#include <vector>

using deluge::dsp::OutputLimiter;
constexpr double kFS = 16777216.0;

// power per bin, 4-term Blackman-Harris window (sidelobes -92 dB), N a power of two
static std::vector<double> dft(const std::vector<double>& x) {
	size_t n = x.size();
	std::vector<std::complex<double>> a(n);
	for (size_t i = 0; i < n; i++) {
		double t = 2 * M_PI * i / n;
		a[i] = x[i] * (0.35875 - 0.48829 * std::cos(t) + 0.14128 * std::cos(2 * t) - 0.01168 * std::cos(3 * t));
	}
	for (size_t i = 1, j = 0; i < n; i++) { // iterative radix-2 FFT
		size_t bit = n >> 1;
		for (; j & bit; bit >>= 1) {
			j ^= bit;
		}
		j ^= bit;
		if (i < j) {
			std::swap(a[i], a[j]);
		}
	}
	for (size_t len = 2; len <= n; len <<= 1) {
		std::complex<double> w = std::polar(1.0, -2 * M_PI / len);
		for (size_t i = 0; i < n; i += len) {
			std::complex<double> v = 1;
			for (size_t k = 0; k < len / 2; k++, v *= w) {
				auto u = a[i + k], t = a[i + k + len / 2] * v;
				a[i + k] = u + t;
				a[i + k + len / 2] = u - t;
			}
		}
	}
	std::vector<double> p(n / 2);
	for (size_t i = 0; i < n / 2; i++) {
		p[i] = std::norm(a[i]);
	}
	return p;
}

// THD and aliasing of y (a sine at f0 through a nonlinearity) in dBc: power at the harmonics below Nyquist, and the
// rest (bins away from DC and the harmonics)
static void distortion(const std::vector<double>& y, double f0, double* thd, double* alias) {
	auto p = dft(y);
	size_t n = y.size();
	double binHz = 44100.0 / n;
	double fund = 0, harm = 0, rest = 0;
	for (size_t b = 6; b < p.size(); b++) {
		double f = b * binHz;
		double k = f / f0;
		double nearest = std::round(k);
		bool onHarmonic = std::fabs(f - nearest * f0) <= 6 * binHz; // (the window's main lobe: 4 bins)
		if (onHarmonic && nearest == 1) {
			fund += p[b];
		}
		else if (onHarmonic) {
			harm += p[b];
		}
		else {
			rest += p[b];
		}
	}
	*thd = 10 * std::log10(harm / fund + 1e-30);
	*alias = 10 * std::log10(rest / fund + 1e-30);
}

static int testBelow() {
	OutputLimiter lim;
	lim.reset();
	constexpr int32_t kBlocks = 200;
	std::vector<StereoSample> in(kBlocks * 128), out;
	uint32_t r = 1;
	for (size_t i = 0; i < in.size(); i++) {
		double s = kFS * std::pow(10, -1.5 / 20) * std::sin(2 * M_PI * 997 * i / 44100.0);
		in[i].l = (int32_t)s;
		r = r * 1664525u + 1013904223u;
		in[i].r = (int32_t)((int32_t)r >> 9); // noise within +-2^22 (-12 dBFS)
	}
	out = in;
	int32_t pos = 0;
	uint32_t seed = 7;
	while (pos < (int32_t)out.size()) {
		seed = seed * 1664525u + 1013904223u;
		int32_t n = std::min<int32_t>(20 + (seed >> 26) + (seed >> 27), out.size() - pos); // (20..128, as the firmware's blocks)
		lim.process(&out[pos], n);
		pos += n;
	}
	int32_t differ = 0;
	for (size_t i = OutputLimiter::kLatency; i < out.size(); i++) {
		differ += (out[i].l != in[i - OutputLimiter::kLatency].l) + (out[i].r != in[i - OutputLimiter::kLatency].r);
	}
	printf("below the knee (sine at -1.5 dBFS, noise at -12 dBFS, blocks of 20..113): %d samples differ from the input "
	       "delayed by %d\n",
	       differ, OutputLimiter::kLatency);
	printf("%s\n", differ ? "FAIL" : "ok");
	return differ != 0;
}

static int testBoost() {
	int fails = 0;
	constexpr int32_t kN = 16384;
	printf("%-7s %-6s | %-34s | %-34s\n", "sine", "over", "hard clip: at FS / THD / aliasing", "limiter: at FS / THD / aliasing");
	for (double f0 : {1000.0, 5000.0, 7000.0, 11000.0}) {
		for (double overDb : {3.0, 6.0, 12.0}) {
			OutputLimiter lim;
			lim.reset();
			std::vector<StereoSample> buf(kN + 4096);
			for (size_t i = 0; i < buf.size(); i++) {
				double s = kFS * std::pow(10, overDb / 20) * std::sin(2 * M_PI * f0 * i / 44100.0);
				buf[i].l = buf[i].r = (int32_t)s;
			}
			std::vector<double> hard(kN), soft(kN);
			int32_t hardAtFs = 0, softAtFs = 0;
			for (int32_t i = 0; i < kN; i++) {
				int32_t x = buf[i + 4096].l;
				int32_t h = std::clamp<int32_t>(x, -(1 << 24), (1 << 24) - 1);
				hard[i] = h;
				hardAtFs += (x >= (1 << 24) - 1 || x <= -(1 << 24));
			}
			for (size_t pos = 0; pos < buf.size(); pos += 128) {
				lim.process(&buf[pos], std::min<size_t>(128, buf.size() - pos));
			}
			for (int32_t i = 0; i < kN; i++) {
				int32_t y = buf[i + 4096].l; // (4096 samples in: past the latency and the start)
				soft[i] = std::clamp<int32_t>(y, -(1 << 24), (1 << 24) - 1);
				softAtFs += (y >= (1 << 24) - 1 || y <= -(1 << 24));
			}
			double thdH, alH, thdS, alS;
			distortion(hard, f0, &thdH, &alH);
			distortion(soft, f0, &thdS, &alS);
			printf("%5.0f Hz %+4.0f dB | %5d  %7.1f dBc %7.1f dBc | %5d  %7.1f dBc %7.1f dBc\n", f0, overDb, hardAtFs,
			       thdH, alH, softAtFs, thdS, alS);
			fails += softAtFs > 0 || alS > alH - 20;
		}
	}
	printf("%s\n", fails ? "FAIL" : "ok");
	return fails != 0;
}

static int testCpu() {
	static StereoSample buf[128];
	OutputLimiter lim;
	lim.reset();
	for (int b = 0; b < 4; b++) {
		lim.process(buf, 128);
	}
	EMU_COUNT_BEGIN("limiter idle (quiet input), 128 samples");
	lim.process(buf, 128);
	EMU_COUNT_END();
	for (int32_t i = 0; i < 128; i++) {
		buf[i].l = buf[i].r = (int32_t)(kFS * 2 * std::sin(2 * M_PI * 1000 * i / 44100.0));
	}
	lim.process(buf, 128);
	for (int32_t i = 0; i < 128; i++) {
		buf[i].l = buf[i].r = (int32_t)(kFS * 2 * std::sin(2 * M_PI * 1000 * (i + 128) / 44100.0));
	}
	EMU_COUNT_BEGIN("limiter active (clipping), 128 samples");
	lim.process(buf, 128);
	EMU_COUNT_END();
	return 0;
}

int main(int argc, char** argv) {
	const char* c = argc > 1 ? argv[1] : "below";
	if (!strcmp(c, "below")) {
		return testBelow();
	}
	if (!strcmp(c, "boost")) {
		return testBoost();
	}
	if (!strcmp(c, "cpu")) {
		return testCpu();
	}
	return 2;
}
