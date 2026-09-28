// The drive ladder (TRANSISTOR_24DB_DRIVE) of mastertune v18: its bass compensation and its 2x oversampling with
// half-band filters (dsp/filter/lpladder.cpp, halfband.h), through the firmware's FilterSet as a kit row (a voice,
// stereo: the filter gain on the input) and a kit (GlobalEffectable: on the output). Built like lpf_precision (the
// filters' private members public). Levels in dBFS of the q31 full scale (a voice's filter input is typically -45
// dBFS RMS, the song's -50).
// Parts (argv[1]):
//   bass       the gain at 40 Hz against resonance 0's, resonance 0..50 (display), cutoff 300 Hz / 1 / 4 kHz, at -60,
//              -45 and -30 dBFS RMS: fails where it is more than 1 dB from resonance 0's (1.2.1: down to -14 dB),
//              judged up to resonance 30 at every level and up to 50 from -45 dBFS (above 25 the ladder sings on its
//              own at about -50 dBFS, over a -60 dBFS input).
//   selfosc    the ladder singing on its own (resonance 30, 40, 50 at 500 Hz): its level and frequency (printed, and
//              against the cutoff: fails more than 25 cents off).
//   alias      a sine of 5, 9, 13, 17 kHz at -40 and -20 dBFS RMS through the ladder (cutoff 50 = wide open, resonance
//              25 %): what isn't the tone or its harmonics below Nyquist (aliases, noise) in dBc, 2x against 1x (the
//              CPU guard's case): fails where 2x doesn't take it at least 3 dB down (unless below -90 dBc). The
//              ladder's tanhs clip hard (the feedback's at about -54 dBFS), so harmonics far above 44 kHz still fold
//              back at 2x: the gain is what the half-bands take out between 22 and 66 kHz.
//   toggle     the CPU guard switching the oversampling off and on (crossfaded): no step above 2.5x the tone's own.
//   halfband   the half-band pair alone (up then down): gain 20 Hz..18 kHz (fails beyond +-0.05 dB), images above
//              26.1 kHz (fails above -70 dB), group delay.
//   cpu        instructions per 128 frames (emulator), stereo kit row: 1x and 2x, steady and with the cutoff moving.
#include "dsp/filter/filter_set.h"
#if __has_include("dsp/filter/halfband.h")
#include "dsp/filter/halfband.h"
#define DRIVE_V18
#endif
#include "emu_count.h"
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

namespace {
constexpr double kFs = 44100;
constexpr int kBlock = 128;

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
double db(double x) {
	return 20 * std::log10(std::max(x, 1e-30));
}
// The cutoff (Hz) for a display value: tan(pi f / fs) = tannedFrequency / 2^28 at the sample rate
double cutoffHz(double display) {
	LpLadderFilter f;
	f.configure(freqParam(display), 0, FilterMode::TRANSISTOR_24DB, 0, 1 << 28);
	return std::atan(f.tannedFrequency / 268435456.0) * kFs / M_PI;
}
double displayForHz(double hz) {
	double lo = 0, hi = 50;
	for (int i = 0; i < 40; i++) {
		double mid = (lo + hi) / 2;
		(cutoffHz(mid) < hz ? lo : hi) = mid;
	}
	return (lo + hi) / 2;
}

// A drive ladder as a kit row (voice, stereo, gain on the input) or a kit (global: on the output)
struct Drive {
	FilterSet* fs;
	bool global;
	double cut, res;
	explicit Drive(bool g, double c, double r) : global(g), cut(c), res(r) {
		fs = new FilterSet();
		memset((void*)fs, 0, sizeof(FilterSet));
		fs->reset();
	}
	~Drive() { delete fs; }
	int32_t gainIn() const { return global ? 167763968 : 134217728 << 1; }
	// in: mono, full scale 1; out: mono (left), with the caller's gain
	void block(const double* in, double* out, int n) {
		int32_t gain = fs->setConfig(freqParam(cut), linearParam(res), FilterMode::TRANSISTOR_24DB_DRIVE, 0, 0, 0,
		                             FilterMode::OFF, 0, gainIn(), FilterRoute::HIGH_TO_LOW, false, nullptr);
		double g = (double)gain / gainIn();
		static int32_t buf[2 * kBlock];
		for (int i = 0; i < n; i++) {
			int32_t v = (int32_t)std::clamp(std::llround(in[i] * (global ? 1.0 : g) * 2147483648.0), -2147483648LL,
			                                2147483647LL);
			buf[2 * i] = buf[2 * i + 1] = v;
		}
		fs->renderLongStereo(buf, buf + 2 * n, HpLadderFilter::kSaturationVoice,
		                     global ? LpLadderFilter::kSaturationGlobal : LpLadderFilter::kSaturationVoice, global);
		for (int i = 0; i < n; i++) {
			out[i] = buf[2 * i] / 2147483648.0 * (global ? g : 1.0);
		}
	}
	std::vector<double> run(const std::vector<double>& in) {
		std::vector<double> out(in.size());
		for (size_t b = 0; b + kBlock <= in.size(); b += kBlock) {
			block(&in[b], &out[b], kBlock);
		}
		return out;
	}
};

// Amplitude of f in x[from..] over a whole number of periods
double amplitudeAt(const std::vector<double>& x, double f, size_t from) {
	size_t n = x.size() - from;
	size_t m = (size_t)std::lround(std::floor(n * f / kFs) * kFs / f);
	double c = 0, s = 0;
	for (size_t i = x.size() - m; i < x.size(); i++) {
		c += x[i] * std::cos(2 * M_PI * f * i / kFs);
		s += x[i] * std::sin(2 * M_PI * f * i / kFs);
	}
	return 2 * std::hypot(c, s) / m;
}
std::vector<double> sine(double f, double rmsDb, int n) {
	std::vector<double> x(n);
	double a = std::pow(10, rmsDb / 20) * std::sqrt(2.0);
	for (int i = 0; i < n; i++) {
		x[i] = a * std::sin(2 * M_PI * f * i / kFs);
	}
	return x;
}

int bass() {
	int bad = 0;
	double worst = 0;
	for (bool global : {false, true}) {
		for (double hz : {300.0, 1000.0, 4000.0}) {
			double cut = displayForHz(hz);
			for (double level : {-60.0, -45.0, -30.0}) {
				std::vector<double> in = sine(40, level, 128 * 160);
				Drive d0(global, cut, 0);
				double ref = amplitudeAt(d0.run(in), 40, in.size() / 2);
				printf("bass %-8s cutoff %5.0f Hz %3.0f dBFS: 40 Hz against resonance 0:", global ? "kit" : "kit row",
				       hz, level);
				for (double res : {10.0, 20.0, 25.0, 30.0, 40.0, 50.0}) {
					Drive d(global, cut, res);
					double g = db(amplitudeAt(d.run(in), 40, in.size() / 2) / ref);
					printf(" %2.0f:%+6.2f", res, g);
					// (Judged below the ladder's own tone: from resonance 25 it sings on its own, at about -50 dBFS
					// in a voice at 40 and 50; a -60 dBFS input under it is no longer what the output follows)
					bool judged = res <= 30 || level >= -45;
					if (judged) {
						worst = std::max(worst, std::fabs(g));
						bad += std::fabs(g) > 1.0;
					}
				}
				printf("\n");
			}
		}
	}
	printf("bass: at most %.2f dB from resonance 0's (limit 1; resonance up to 30 at every level, up to 50 from -45 "
	       "dBFS)\n",
	       worst);
	return bad ? 1 : 0;
}

// The ladder's own tone: a burst of noise, then silence; after 1 s the level (RMS) and the frequency (zero crossings)
int selfOsc() {
	int bad = 0;
	for (bool global : {false, true}) {
		for (double res : {30.0, 40.0, 50.0}) {
			double cut = displayForHz(500);
			Drive d(global, cut, res);
			int n = 128 * 400;
			std::vector<double> in(n, 0.0);
			uint32_t r = 1;
			for (int i = 0; i < 256; i++) {
				r = r * 1664525u + 1013904223u;
				in[i] = (int32_t)r / 2147483648.0 * 0.01;
			}
			std::vector<double> out = d.run(in);
			double e = 0;
			int crossings = 0;
			for (int i = n / 2; i < n; i++) {
				e += out[i] * out[i];
				crossings += (out[i - 1] < 0) != (out[i] < 0);
			}
			double rms = std::sqrt(e / (n / 2));
			double f = crossings / 2.0 / ((n / 2) / kFs);
			double cents = rms > 1e-7 ? 1200 * std::log2(f / cutoffHz(cut)) : 0;
			printf("selfosc %-8s cutoff %.0f Hz resonance %2.0f: %6.1f dBFS RMS at %6.1f Hz (%+.0f cents)\n",
			       global ? "kit" : "kit row", cutoffHz(cut), res, db(rms), rms > 1e-7 ? f : 0.0, cents);
		}
	}
	return bad;
}

// FFT power per bin, 4-term Blackman-Harris window (sidelobes -92 dB: a tone's leakage stays out of the rest)
std::vector<double> spectrum(const std::vector<double>& x, size_t from, int n) {
	std::vector<std::complex<double>> a(n);
	for (int i = 0; i < n; i++) {
		double t = 2 * M_PI * i / n;
		double w = 0.35875 - 0.48829 * std::cos(t) + 0.14128 * std::cos(2 * t) - 0.01168 * std::cos(3 * t);
		a[i] = x[from + i] * w;
	}
	// iterative radix-2
	for (int i = 1, j = 0; i < n; i++) {
		int bit = n >> 1;
		for (; j & bit; bit >>= 1) {
			j ^= bit;
		}
		j ^= bit;
		if (i < j) {
			std::swap(a[i], a[j]);
		}
	}
	for (int len = 2; len <= n; len <<= 1) {
		std::complex<double> w(std::cos(-2 * M_PI / len), std::sin(-2 * M_PI / len));
		for (int i = 0; i < n; i += len) {
			std::complex<double> u(1);
			for (int j = 0; j < len / 2; j++) {
				std::complex<double> t = a[i + j + len / 2] * u, v = a[i + j];
				a[i + j] = v + t;
				a[i + j + len / 2] = v - t;
				u *= w;
			}
		}
	}
	std::vector<double> p(n / 2);
	for (int i = 0; i < n / 2; i++) {
		p[i] = std::norm(a[i]);
	}
	return p;
}
// Energy that isn't the tone's harmonics (below Nyquist) against the fundamental's, dBc
double aliasDbc(const std::vector<double>& out, double f) {
	const int n = 16384;
	std::vector<double> p = spectrum(out, out.size() - n, n);
	double binHz = kFs / n, fund = 0, other = 0;
	for (int i = 2; i < n / 2; i++) {
		double hz = i * binHz;
		double k = std::round(hz / f);
		bool harmonic = k >= 1 && std::fabs(hz - k * f) < 6 * binHz; // (the window's main lobe: +-4 bins)
		if (harmonic && k == 1) {
			fund += p[i];
		}
		else if (!harmonic) {
			other += p[i];
		}
	}
	return 10 * std::log10(std::max(other, 1e-300) / fund);
}
int alias() {
	int bad = 0;
	for (bool global : {false, true}) {
		for (double level : {-40.0, -20.0}) {
			for (double f : {5000.0, 9000.0, 13000.0, 17000.0}) {
				double r[2];
				for (int os = 0; os < 2; os++) {
					AudioEngine::cpuDireness = os ? 0 : 14;
					std::vector<double> in = sine(f, level, 128 * 200);
					Drive d(global, 50, 12.5);
					r[os] = aliasDbc(d.run(in), f);
				}
				AudioEngine::cpuDireness = 0;
				bool fail = r[1] > -90 && r[1] > r[0] - 3;
				bad += fail;
				printf("alias %-8s %5.0f Hz %3.0f dBFS, cutoff wide open, resonance 25 %%: not the tone's harmonics: 1x "
				       "%6.1f dBc, 2x %6.1f dBc (%+5.1f dB)%s\n",
				       global ? "kit" : "kit row", f, level, r[0], r[1], r[1] - r[0], fail ? "  FAIL" : "");
			}
		}
	}
	return bad;
}

// The CPU guard switching the oversampling off and on again (cpuDireness 14 and back) under a 110 Hz tone: the largest
// step between two samples in the 20 ms after each switch against the largest in the 100 ms before (crossfaded)
int toggle() {
	int bad = 0;
	for (bool global : {false, true}) {
		std::vector<double> in = sine(110, -30, 128 * 120);
		Drive d(global, 30, 20);
		std::vector<double> out(in.size());
		for (int b = 0; b < 120; b++) {
			AudioEngine::cpuDireness = (b >= 40 && b < 80) ? 14 : 0;
			d.block(&in[b * 128], &out[b * 128], 128);
		}
		AudioEngine::cpuDireness = 0;
		auto maxStep = [&](int from, int to) {
			double m = 0;
			for (int i = from + 1; i < to; i++) {
				m = std::max(m, std::fabs(out[i] - out[i - 1]));
			}
			return m;
		};
		for (int at : {40 * 128, 80 * 128}) {
			double before = maxStep(at - 4410, at), after = maxStep(at, at + 882);
			bool fail = after > 2.5 * before;
			bad += fail;
			printf("toggle %-8s oversampling %s at sample %d: largest step %.2fx the one before%s\n",
			       global ? "kit" : "kit row", at == 40 * 128 ? "off" : "on ", at, after / before, fail ? "  FAIL" : "");
		}
	}
	return bad;
}

#ifdef DRIVE_V18
int halfband() {
	int bad = 0;
	double worst = 0;
	const int n = 16384;
	for (double f : {20.0, 100.0, 1000.0, 5000.0, 10000.0, 15000.0, 18000.0}) {
		HalfbandUp up{};
		HalfbandDown down{};
		std::vector<double> y(n);
		double a = 0.25 * 2147483648.0;
		for (int i = 0; i < n; i++) {
			q31_t e, o;
			up.process((q31_t)std::lround(a * std::sin(2 * M_PI * f * i / kFs)), e, o);
			y[i] = down.process(e, o) / a;
		}
		double g = db(amplitudeAt(y, f, n / 2));
		// phase: correlate against the input, delay = -phase / (2 pi f) samples
		size_t m = (size_t)std::lround(std::floor((n / 2) * f / kFs) * kFs / f);
		double c = 0, s = 0;
		for (size_t i = n - m; i < (size_t)n; i++) {
			c += y[i] * std::cos(2 * M_PI * f * i / kFs);
			s += y[i] * std::sin(2 * M_PI * f * i / kFs);
		}
		double phase = std::atan2(c, s); // y ~ sin(w i + phase)
		double delay = std::fmod(-phase / (2 * M_PI * f) * kFs + 1000 * kFs / f, kFs / f);
		worst = std::max(worst, std::fabs(g));
		printf("halfband %5.0f Hz: up + down %+8.5f dB, delay %.2f samples at 44.1 kHz (phase delay)\n", f, g, delay);
	}
	bad += worst > 0.05;
	// Images: a 1 kHz .. 18 kHz sine upsampled; energy above 26.1 kHz at 88.2 kHz against the tone
	double worstImage = -300;
	for (double f : {1000.0, 7000.0, 12000.0, 18000.0}) {
		HalfbandUp up{};
		std::vector<double> x(2 * n);
		for (int i = 0; i < n; i++) {
			q31_t e, o;
			up.process((q31_t)std::lround(0.25 * 2147483648.0 * std::sin(2 * M_PI * f * i / kFs)), e, o);
			x[2 * i] = e / 2147483648.0;
			x[2 * i + 1] = o / 2147483648.0;
		}
		std::vector<double> p = spectrum(x, x.size() - n, n);
		double tone = 0, img = 0;
		for (int i = 1; i < n / 2; i++) {
			double hz = i * 2 * kFs / n;
			if (std::fabs(hz - f) < 100) {
				tone += p[i];
			}
			else if (hz >= 26100) {
				img = std::max(img, p[i]);
			}
		}
		double d = 10 * std::log10(img / tone);
		worstImage = std::max(worstImage, d);
		printf("halfband image of %5.0f Hz above 26.1 kHz: %6.1f dB\n", f, d);
	}
	bad += worstImage > -70;
	printf("halfband: passband within %.5f dB (limit 0.05), images at most %.1f dB (limit -70)\n", worst, worstImage);
	return bad;
}
#endif

// (An older tree: 1.2.1's oversampling where its condition has it, cutoff 45 with resonance)
int cpu() {
	static int32_t buf[2 * kBlock];
	for (int i = 0; i < 2 * kBlock; i++) {
		buf[i] = (int32_t)(0.01 * 2147483648.0 * std::sin(2 * M_PI * 220 * (i / 2) / kFs));
	}
	for (double cut : {30.0, 45.0}) {
		for (int os = 0; os < 2; os++) {
			AudioEngine::cpuDireness = os ? 0 : 14;
			FilterSet* fs = new FilterSet();
			memset((void*)fs, 0, sizeof(FilterSet));
			fs->reset();
			auto config = [&](double c) {
				fs->setConfig(freqParam(c), linearParam(20), FilterMode::TRANSISTOR_24DB_DRIVE, 0, 0, 0,
				              FilterMode::OFF, 0, 1 << 28, FilterRoute::HIGH_TO_LOW, false, nullptr);
			};
			for (int k = 0; k < 4; k++) {
				config(cut);
				fs->renderLongStereo(buf, buf + 2 * kBlock);
			}
			char label[96];
			config(cut);
			snprintf(label, sizeof label, "drive stereo, cutoff %.0f, cpuDireness %d, steady", cut, os ? 0 : 14);
			EMU_COUNT_BEGIN(label);
			fs->renderLongStereo(buf, buf + 2 * kBlock);
			EMU_COUNT_END();
			config(cut + 1);
			snprintf(label, sizeof label, "drive stereo, cutoff %.0f, cpuDireness %d, cutoff moving", cut, os ? 0 : 14);
			EMU_COUNT_BEGIN(label);
			fs->renderLongStereo(buf, buf + 2 * kBlock);
			EMU_COUNT_END();
			delete fs;
		}
	}
	AudioEngine::cpuDireness = 0;
	printf("(counts per call of 128 frames below)\n");
	return 0;
}
} // namespace

int main(int argc, char** argv) {
	const char* part = argc > 1 ? argv[1] : "all";
	int bad = 0;
	auto is = [&](const char* p) { return !strcmp(part, "all") || !strcmp(part, p); };
#ifdef DRIVE_V18
	if (is("halfband")) {
		bad += halfband();
	}
#endif
	if (is("bass")) {
		bad += bass();
	}
	if (is("selfosc")) {
		bad += selfOsc();
	}
	if (is("alias")) {
		bad += alias();
	}
	if (is("toggle")) {
		bad += toggle();
	}
	if (is("cpu")) {
		bad += cpu();
	}
	printf(bad ? "FAIL: %d\n" : "ok\n", bad);
	return bad ? 1 : 0;
}
