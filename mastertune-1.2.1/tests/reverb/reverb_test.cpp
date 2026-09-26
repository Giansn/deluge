// Host test for the reverb (v10): runs the firmware's reverb code (dsp/reverb) on the PC and measures it.
//  - stability at the longest settings, all models
//  - decay time of Mutable and Digital against Room size, and their level, so switching models doesn't jump
//  - stereo balance of Freeverb at every Width, stereo spread and Width of Digital
//  - the Mutable model's LFOs at Mutable Instruments' 0.5 and 0.3 Hz, and what that does to the tail
//  - low cut and high cut: cutoff frequencies and their ranges
//  - song files: damping and the old low cut read back at the same sound
// Build and run: see run.sh. "calibrate" as argument prints the measurements behind the constants in digital.hpp.

#include "dsp/reverb/reverb.hpp"
#include <cmath>
#include <complex>
#include <cstdio>
#include <cstring>
#include <memory>
#include <random>
#include <vector>

using deluge::dsp::Reverb;
using Model = Reverb::Model;
namespace rv = deluge::dsp::reverb;

static int checks = 0;
static int failures = 0;
#define CHECK(cond, ...)                                                                                               \
	do {                                                                                                               \
		checks++;                                                                                                      \
		if (!(cond)) {                                                                                                 \
			failures++;                                                                                                \
			printf("FAIL %s:%d: ", __FILE__, __LINE__);                                                                \
			printf(__VA_ARGS__);                                                                                       \
			printf("\n");                                                                                              \
		}                                                                                                              \
	} while (0)

constexpr int kBlock = 128;                // The audio engine's largest block
constexpr int32_t kPanAmplitude = 1 << 29; // What the audio engine passes without sidechain ducking
constexpr float kFs = 44100.f;

struct Settings {
	Model model = Model::MUTABLE;
	float room = 30.f / 50;
	float damping = 0.f; // Menu direction: 0 = none
	float width = 1.f;
	float hpf = 0.f;
	float lpf = 1.f;
};

struct Out {
	std::vector<double> l, r;
};

static std::unique_ptr<Reverb> make(const Settings& s) {
	auto reverb = std::make_unique<Reverb>();
	reverb->setModel(s.model);
	reverb->setRoomSize(s.room);
	reverb->setDamping(s.damping);
	reverb->setWidth(s.width);
	reverb->setHPF(s.hpf);
	reverb->setLPF(s.lpf);
	reverb->setPanLevels(kPanAmplitude, kPanAmplitude);
	return reverb;
}

// Runs input through the reverb in blocks like the audio engine, output as fraction of full scale
template <typename R>
static Out render(R& reverb, const std::vector<int32_t>& input) {
	Out out;
	out.l.reserve(input.size());
	out.r.reserve(input.size());
	std::vector<int32_t> in(kBlock);
	std::vector<StereoSample> buf(kBlock);
	for (size_t pos = 0; pos < input.size(); pos += kBlock) {
		size_t n = std::min<size_t>(kBlock, input.size() - pos);
		std::copy(input.begin() + pos, input.begin() + pos + n, in.begin());
		for (auto& s : buf) {
			s.l = s.r = 0;
		}
		reverb.process(std::span<int32_t>(in.data(), n), std::span<StereoSample>(buf.data(), n));
		for (size_t i = 0; i < n; i++) {
			out.l.push_back(buf[i].l / 2147483648.0);
			out.r.push_back(buf[i].r / 2147483648.0);
		}
	}
	return out;
}

static std::vector<int32_t> noise(size_t n, double amplitude, uint32_t seed = 1) {
	std::mt19937 rng(seed);
	std::uniform_real_distribution<double> d(-1, 1);
	std::vector<int32_t> v(n);
	for (auto& x : v) {
		x = (int32_t)(d(rng) * amplitude);
	}
	return v;
}

static double rms(const std::vector<double>& v, size_t from, size_t to) {
	double e = 0;
	for (size_t i = from; i < to; i++) {
		e += v[i] * v[i];
	}
	return std::sqrt(e / (double)(to - from));
}

static double db(double x) {
	return 20 * std::log10(x);
}

// Decay time from the impulse response by Schroeder integration: -5 to -25 dB, extrapolated to 60 dB
static double decayTime(const Out& o) {
	size_t n = o.l.size();
	std::vector<double> e(n + 1, 0);
	for (size_t i = n; i-- > 0;) {
		e[i] = e[i + 1] + o.l[i] * o.l[i] + o.r[i] * o.r[i];
	}
	double t5 = -1, t25 = -1;
	for (size_t i = 0; i < n; i++) {
		double level = 10 * std::log10(e[i] / e[0]);
		if (t5 < 0 && level <= -5) {
			t5 = i / kFs;
		}
		if (t25 < 0 && level <= -25) {
			t25 = i / kFs;
			break;
		}
	}
	return (t25 < 0 || t5 < 0) ? -1 : 3 * (t25 - t5);
}

static Out impulseResponse(const Settings& s, double seconds) {
	auto reverb = make(s);
	std::vector<int32_t> in((size_t)(seconds * kFs), 0);
	in[0] = 1 << 24;
	return render(*reverb, in);
}

static void fft(std::vector<std::complex<double>>& a) {
	size_t n = a.size();
	for (size_t i = 1, j = 0; i < n; i++) {
		size_t bit = n >> 1;
		for (; j & bit; bit >>= 1) {
			j ^= bit;
		}
		j ^= bit;
		if (i < j) {
			std::swap(a[i], a[j]);
		}
	}
	for (size_t l = 2; l <= n; l <<= 1) {
		std::complex<double> wl = std::polar(1.0, -2 * M_PI / l);
		for (size_t i = 0; i < n; i += l) {
			std::complex<double> wk = 1;
			for (size_t k = 0; k < l / 2; k++) {
				auto u = a[i + k];
				auto v = a[i + k + l / 2] * wk;
				a[i + k] = u + v;
				a[i + k + l / 2] = u - v;
				wk *= wl;
			}
		}
	}
}

// How much the tail's resonances stay put: correlation of the fine spectrum (1.3 Hz bins, 200 Hz to 4 kHz, in dB) of
// 0.5 s windows, 0.5 s apart, from 1 to 4 s after the impulse. Near 1: the same resonances ring on, which is what
// makes a reverb tail sound metallic. Lower: modulation keeps moving them.
static double tailStationarity(const std::vector<double>& x) {
	const size_t kN = 32768;
	const size_t len = (size_t)(0.5 * kFs);
	std::vector<std::vector<double>> spectra;
	for (double t = 1.0; t + 0.5 <= 4.0; t += 0.5) {
		size_t from = (size_t)(t * kFs);
		std::vector<std::complex<double>> a(kN);
		for (size_t i = 0; i < len; i++) {
			a[i] = x[from + i] * (0.5 - 0.5 * std::cos(2 * M_PI * i / (len - 1)));
		}
		fft(a);
		std::vector<double> spectrum;
		for (size_t k = (size_t)(200.0 * kN / kFs); k < (size_t)(4000.0 * kN / kFs); k++) {
			spectrum.push_back(10 * std::log10(std::norm(a[k]) + 1e-40));
		}
		double mean = 0;
		for (double v : spectrum) {
			mean += v;
		}
		mean /= spectrum.size();
		for (double& v : spectrum) {
			v -= mean;
		}
		spectra.push_back(spectrum);
	}
	double sum = 0;
	for (size_t f = 1; f < spectra.size(); f++) {
		double ab = 0, aa = 0, bb = 0;
		for (size_t k = 0; k < spectra[f].size(); k++) {
			ab += spectra[f][k] * spectra[f - 1][k];
			aa += spectra[f - 1][k] * spectra[f - 1][k];
			bb += spectra[f][k] * spectra[f][k];
		}
		sum += ab / std::sqrt(aa * bb);
	}
	return sum / (spectra.size() - 1);
}

// The Mutable model with its LFOs as slow as they ran in 1.2.1 (1/16), for comparison
struct SlowLfoMutable : rv::Mutable {
	SlowLfoMutable() {
		engine_.SetLFOFrequency(rv::LFO_1, 0.5f / kFs / 16);
		engine_.SetLFOFrequency(rv::LFO_2, 0.3f / kFs / 16);
	}
};

// 1.2.1's damping coefficient for a song file value
static float oldDampingCoefficient(float v) {
	return (v == 0.f) ? 1.f : 1.f - std::clamp((std::log2(((1.f - v) * 50.f) + 1.f) / 5.7f), 0.f, 1.f);
}

// Response of dsp::OnePole() as a low pass at frequency hz, in dB
static double onePoleLowPassDb(float coefficient, double hz) {
	std::complex<double> z = std::polar(1.0, -2 * M_PI * hz / kFs);
	double a = coefficient;
	return db(std::abs(a / (1.0 - (1.0 - a) * z)));
}

static const char* name(Model m) {
	return m == Model::FREEVERB ? "Freeverb" : m == Model::MUTABLE ? "Mutable" : "Digital";
}

int main(int argc, char** argv) {
	bool calibrate = argc > 1 && !strcmp(argv[1], "calibrate");

	// --- LFOs of the Mutable model
	{
		std::array<float, 64> buffer{};
		rv::FxEngine engine{buffer, {0.5f / kFs, 0.3f / kFs}};
		double lo[2] = {1, 1}, hi[2] = {0, 0};
		int crossings[2] = {0, 0};
		double last[2] = {engine.LFO(rv::LFO_1) - 0.5, engine.LFO(rv::LFO_2) - 0.5};
		size_t n = (size_t)(20 * kFs);
		for (size_t i = 0; i < n; i++) {
			engine.Advance();
			for (int k = 0; k < 2; k++) {
				double v = engine.LFO(k == 0 ? rv::LFO_1 : rv::LFO_2);
				lo[k] = std::min(lo[k], v);
				hi[k] = std::max(hi[k], v);
				if ((v - 0.5 >= 0) != (last[k] >= 0)) {
					crossings[k]++;
				}
				last[k] = v - 0.5;
			}
		}
		double f1 = crossings[0] / 2.0 / 20, f2 = crossings[1] / 2.0 / 20;
		printf("LFOs: %.3f Hz and %.3f Hz, range %.3f..%.3f\n", f1, f2, lo[0], hi[0]);
		// Mutable Instruments' approximate cosine oscillator runs about 10% slow at such low rates, in their modules too
		CHECK(std::abs(f1 / 0.5 - 1) < 0.15 && std::abs(f2 / 0.3 - 1) < 0.15, "LFO rates %.3f %.3f", f1, f2);
		CHECK(lo[0] > -0.02 && hi[0] < 1.02 && hi[0] - lo[0] > 0.95, "LFO range %.3f..%.3f", lo[0], hi[0]);
	}

	// --- Stability at the longest settings: 2 s of loud noise, then 40 s of silence, the level must only fall
	for (Model m : {Model::FREEVERB, Model::MUTABLE, Model::DIGITAL}) {
		for (float width : {0.f, 1.f}) {
			Settings s;
			s.model = m;
			s.room = 1.f;
			s.damping = 0.f;
			s.width = width;
			auto reverb = make(s);
			std::vector<int32_t> in = noise((size_t)(2 * kFs), 1 << 24);
			in.resize((size_t)(42 * kFs), 0);
			Out o = render(*reverb, in);
			double previous = 1e9;
			bool falling = true;
			bool finite = true;
			double peak = 0;
			for (size_t w = 0; w < 8; w++) {
				size_t from = (size_t)((2 + 5 * w) * kFs);
				double level = std::max(rms(o.l, from, from + (size_t)(5 * kFs)), rms(o.r, from, from + (size_t)(5 * kFs)));
				finite = finite && std::isfinite(level);
				falling = falling && (level <= previous * 1.001 || level < 1e-5); // Freeverb ends at its rounding floor
				previous = level;
			}
			for (size_t i = 0; i < o.l.size(); i++) {
				peak = std::max(peak, std::max(std::abs(o.l[i]), std::abs(o.r[i])));
			}
			printf("%-8s room 1, damping 0, width %.0f: peak %.1f dBFS, after 40 s %.1f dB below\n", name(m), width,
			       db(peak), db(rms(o.l, (size_t)(2 * kFs), (size_t)(4 * kFs)) / (previous + 1e-30)));
			CHECK(finite && falling, "%s width %.0f: tail not decaying", name(m), width);
			CHECK(peak < 0.99, "%s width %.0f: output clips at %.2f", name(m), width, peak);
		}
	}

	// --- Decay time against Room size, Mutable and Digital
	{
		printf("Decay time (s), damping 0:\n  room   Mutable  Digital\n");
		double lastDigital = 0;
		for (float room : {0.f, 0.2f, 0.4f, 0.6f, 0.8f, 0.9f}) {
			Settings s;
			s.room = room;
			s.model = Model::MUTABLE;
			double tm = decayTime(impulseResponse(s, 30));
			s.model = Model::DIGITAL;
			double td = decayTime(impulseResponse(s, 30));
			printf("  %.1f    %6.2f   %6.2f\n", room, tm, td);
			if (room == 0.6f) {
				CHECK(td > 0 && std::abs(std::log(td / tm)) < std::log(1.2), "room %.1f: Digital %.2f s vs Mutable %.2f s",
				      room, td, tm);
			}
			CHECK(td > lastDigital, "Digital's decay should grow with Room size");
			lastDigital = td;
		}
		Settings s;
		s.damping = 14.f / 50; // The default song's damping (36 in 1.2.1's direction)
		s.model = Model::MUTABLE;
		double tm = decayTime(impulseResponse(s, 30));
		s.model = Model::DIGITAL;
		double td = decayTime(impulseResponse(s, 30));
		printf("  default song (room 30, damping 14): Mutable %.2f s, Digital %.2f s\n", tm, td);
	}

	// --- Level: Digital as loud as Mutable at default settings (steady noise)
	{
		Settings s;
		s.damping = 14.f / 50;
		std::vector<int32_t> in = noise((size_t)(8 * kFs), 1 << 24);
		s.model = Model::MUTABLE;
		auto a = make(s);
		Out om = render(*a, in);
		s.model = Model::DIGITAL;
		auto b = make(s);
		Out od = render(*b, in);
		size_t from = (size_t)(4 * kFs), to = in.size();
		double lm = std::hypot(rms(om.l, from, to), rms(om.r, from, to));
		double ld = std::hypot(rms(od.l, from, to), rms(od.r, from, to));
		printf("Level at default settings: Digital %.2f dB relative to Mutable\n", db(ld / lm));
		CHECK(std::abs(db(ld / lm)) < 0.75, "Digital level %.2f dB off Mutable's", db(ld / lm));
	}

	// --- Freeverb: left and right equally loud at every Width
	for (float width : {0.f, 0.25f, 0.5f, 0.75f, 1.f}) {
		Settings s;
		s.model = Model::FREEVERB;
		s.width = width;
		auto reverb = make(s);
		Out o = render(*reverb, noise((size_t)(6 * kFs), 1 << 24));
		double balance = db(rms(o.r, (size_t)(2 * kFs), o.r.size()) / rms(o.l, (size_t)(2 * kFs), o.l.size()));
		printf("Freeverb width %.2f: right %.2f dB relative to left\n", width, balance);
		CHECK(std::abs(balance) < 0.5, "Freeverb width %.2f: imbalance %.2f dB", width, balance);
	}

	// --- Digital: stereo spread, and Width 0 gives mono
	for (float width : {1.f, 0.5f, 0.f}) {
		Settings s;
		s.model = Model::DIGITAL;
		s.width = width;
		Out o = impulseResponse(s, 4);
		double lr = 0, ll = 0, rr = 0;
		for (size_t i = (size_t)(0.1 * kFs); i < (size_t)(3 * kFs); i++) {
			lr += o.l[i] * o.r[i];
			ll += o.l[i] * o.l[i];
			rr += o.r[i] * o.r[i];
		}
		double correlation = lr / std::sqrt(ll * rr);
		printf("Digital width %.1f: correlation of left and right %.2f, right %.2f dB relative to left\n", width,
		       correlation, db(std::sqrt(rr / ll)));
		if (width == 1.f) {
			CHECK(std::abs(correlation) < 0.3, "Digital full width: correlation %.2f", correlation);
		}
		if (width == 0.f) {
			CHECK(correlation > 0.999, "Digital width 0: correlation %.3f", correlation);
		}
		CHECK(std::abs(db(std::sqrt(rr / ll))) < 1.0, "Digital width %.1f: imbalance", width);
	}

	// --- Modulation in the tail: the Mutable model now, with 1.2.1's slow LFOs, and Digital
	{
		Settings s;
		s.room = 0.8f;
		s.model = Model::MUTABLE;
		Out now = impulseResponse(s, 5);
		s.model = Model::DIGITAL;
		Out digital = impulseResponse(s, 5);
		auto slow = std::make_unique<SlowLfoMutable>();
		slow->setRoomSize(0.8f);
		slow->setDamping(0.f);
		slow->setPanLevels(kPanAmplitude, kPanAmplitude);
		std::vector<int32_t> in((size_t)(5 * kFs), 0);
		in[0] = 1 << 24;
		Out before = render(*slow, in);
		double a = tailStationarity(before.l);
		double b = tailStationarity(now.l);
		double c = tailStationarity(digital.l);
		printf("Tail: how much the resonances stay put from one half second to the next (1 = not at all moving): Mutable "
		       "with 1.2.1's LFOs %.2f, now %.2f, Digital %.2f\n",
		       a, b, c);
		CHECK(b < a, "the faster LFOs should move the resonances more (%.2f vs %.2f)", b, a);
	}

	// --- Low cut and high cut
	{
		CHECK(std::abs(rv::Base::hpfCutoffHz(0) - 20) < 0.01 && std::abs(rv::Base::hpfCutoffHz(1) - 542.2) < 1,
		      "low cut range");
		CHECK(std::abs(rv::Base::lpfCutoffHz(0) - 500) < 0.01 && std::abs(rv::Base::lpfCutoffHz(0.5) - 3162) < 2,
		      "high cut range");
		CHECK(rv::Base::lpfIsOff(1.f) && !rv::Base::lpfIsOff(49.f / 50), "high cut off only at 50");
		for (double hz : {20.0, 100.0, 540.0, 500.0, 2000.0, 5000.0}) {
			double response = onePoleLowPassDb(rv::Base::onePoleCoefficient(hz), hz);
			CHECK(std::abs(response + 3.01) < 0.2, "one-pole at %.0f Hz: %.2f dB", hz, response);
		}
		// The low cut in the model: noise through Mutable with the low cut at 25 (190 Hz), response at 50 Hz vs 2 kHz
		double r18k = onePoleLowPassDb(rv::Base::onePoleCoefficient(rv::Base::lpfCutoffHz(49.f / 50)), 10000);
		printf("High cut at 49: %.2f dB at 10 kHz\n", r18k);
		CHECK(r18k > -1.0, "high cut at 49 should be nearly open");
	}

	// --- Song files
	{
		bool exact = true;
		for (int i = 0; i <= 50; i++) {
			float file = i / 50.f;
			float menu = rv::dampingFromFile(file);
			rv::Mutable m;
			m.setDamping(menu);
			// Same loop filter as 1.2.1 for every file value, and written back unchanged
			exact = exact && std::abs(oldDampingCoefficient(file) - (float)(1.0 - std::clamp(std::log2(menu * 50.0 + 1) / 5.7, 0.0, 1.0))) < 1e-6f;
			float back = rv::dampingToFile(menu);
			exact = exact && (i == 50 ? back == 0.f : std::abs(back - file) < 1e-6f);
		}
		CHECK(exact, "damping from and to the file");
		CHECK(rv::dampingToFile(1.f) > 0.f && oldDampingCoefficient(rv::dampingToFile(1.f)) < 0.006f,
		      "darkest damping stays darkest in the file");
		// The old low cut read back at its actual cutoff
		bool same = true;
		for (int i = 0; i <= 50; i++) {
			float old = i / 50.f;
			float label = 20.f + (std::exp(1.5f * old) - 1.f) * 150.f;
			float f = label / kFs;
			float oldCoefficient = f / (1 + f);
			float now = rv::lowCutFromOldHPF(old);
			float nowCoefficient = rv::Base::onePoleCoefficient(rv::Base::hpfCutoffHz(now));
			if (now > 0) {
				same = same && std::abs(nowCoefficient / oldCoefficient - 1) < 0.01;
			}
			else {
				same = same && oldCoefficient <= rv::Base::onePoleCoefficient(20.f) * 1.001f;
			}
		}
		printf("Old low cut 50 is now %.1f (of 50), old 25 now %.1f\n", rv::lowCutFromOldHPF(1.f) * 50,
		       rv::lowCutFromOldHPF(0.5f) * 50);
		CHECK(same, "old low cut values at their actual cutoff");
		printf("Community firmware LPF 50 is now %.1f (of 50)\n", rv::highCutFromCommunityLPF(1.f) * 50);
		CHECK(std::abs(rv::Base::lpfCutoffHz(rv::highCutFromCommunityLPF(1.f)) - 2360) < 60, "community LPF 50");
	}

	if (calibrate) {
		// Digital's level and decay against Mutable's over several settings
		for (float room : {0.3f, 0.6f, 0.9f}) {
			Settings s;
			s.room = room;
			s.damping = 14.f / 50;
			std::vector<int32_t> in = noise((size_t)(10 * kFs), 1 << 24, 7);
			s.model = Model::MUTABLE;
			auto a = make(s);
			Out om = render(*a, in);
			s.model = Model::DIGITAL;
			auto b = make(s);
			Out od = render(*b, in);
			size_t from = (size_t)(6 * kFs), to = in.size();
			printf("calibrate room %.1f: Digital level %.2f dB relative to Mutable\n", room,
			       db(std::hypot(rms(od.l, from, to), rms(od.r, from, to))
			          / std::hypot(rms(om.l, from, to), rms(om.r, from, to))));
		}
	}

	printf("%d checks, %d failed\n", checks, failures);
	if (failures == 0) {
		printf("all reverb checks passed\n");
	}
	return failures ? 1 : 0;
}
