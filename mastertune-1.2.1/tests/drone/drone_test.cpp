// Host test for the drone (v12): runs the firmware's drone DSP on the PC and
// measures its tones. mastertune-v15: life, FM and the pulse timbre (sections 11 to 18).
#include "dsp/drone/drone.h"
#include "emu_count.h"
#include <chrono>
#include <cmath>
#include <complex>
#include <cstdio>
#include <functional>
#include <string>
#include <vector>

using deluge::dsp::Drone;
using deluge::dsp::DroneSettings;
using Mode = Drone::Mode;
using Timbre = Drone::Timbre;
using Tone = Drone::Tone;

// A drone renderer with its settings, as the song holds them
struct TestDrone {
	Drone drone;
	DroneSettings settings;
	std::array<Tone, 16>& tones = settings.tones;
	int32_t& volume = settings.volume;
	TestDrone() = default;
	TestDrone(const TestDrone& o) : drone(o.drone), settings(o.settings) {}
	void render(std::span<StereoSample> b, const Drone::Context& c) { drone.render(b, settings, c); }
	bool isSounding() const { return drone.isSounding(settings); }
};

// A tone that's on
static Tone on(Mode mode, Timbre timbre, int32_t frequency, int32_t beat, int32_t sync, int32_t level, int32_t pan) {
	Tone t;
	t.active = true;
	t.mode = mode;
	t.timbre = timbre;
	t.frequency = frequency;
	t.beat = beat;
	t.sync = sync;
	t.level = level;
	t.pan = pan;
	return t;
}

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

constexpr double kFs = 44100;
constexpr int kBlock = 128;
constexpr double kFullScale = 4194304.0; // Tone at level 50, volume 50

static std::vector<float> tableMemory(Drone::kTableMemoryFloats);

struct Out {
	std::vector<double> l, r;
};

// Renders n samples in blocks, with a callback before each block to change
// settings
static Out run(TestDrone& d, size_t n, std::function<void(size_t, Drone::Context&)> before = nullptr,
               Drone::Context context = {}) {
	Out o;
	for (size_t pos = 0; pos < n; pos += kBlock) {
		size_t m = std::min<size_t>(kBlock, n - pos);
		std::vector<StereoSample> buf(m);
		Drone::Context c = context;
		if (context.position >= 0) {
			c.position = context.position + pos / kFs * context.quarterNotesPerSecond;
		}
		if (before) {
			before(pos, c);
		}
		d.render(std::span<StereoSample>(buf.data(), m), c);
		for (auto& s : buf) {
			o.l.push_back(s.l);
			o.r.push_back(s.r);
		}
	}
	return o;
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
	for (size_t len = 2; len <= n; len <<= 1) {
		std::complex<double> wl = std::polar(1.0, -2 * M_PI / len);
		for (size_t i = 0; i < n; i += len) {
			std::complex<double> w = 1;
			for (size_t k = 0; k < len / 2; k++) {
				auto u = a[i + k];
				auto v = a[i + k + len / 2] * w;
				a[i + k] = u + v;
				a[i + k + len / 2] = u - v;
				w *= wl;
			}
		}
	}
}

// 7-term Blackman-Harris window: sidelobes below -180 dB, so the measurements
// see far below the tones
static double window(size_t i, size_t n) {
	static const double a[7] = {0.27105140069342, 0.43329793923448, 0.21812299954311, 0.06592544638803,
	                            0.01081174209837, 0.00077658482522, 0.00001388721735};
	double t = 2 * M_PI * i / (n - 1), w = 0;
	for (int k = 0; k < 7; k++) {
		w += ((k & 1) ? -a[k] : a[k]) * cos(k * t);
	}
	return w;
}

// Amplitude of the component at exactly hz (windowed DFT at that frequency)
static double amplitudeAt(const std::vector<double>& x, size_t start, size_t n, double hz) {
	std::complex<double> acc = 0;
	double wsum = 0;
	for (size_t i = 0; i < n; i++) {
		double w = window(i, n);
		acc += x[start + i] * w * std::polar(1.0, -2 * M_PI * hz * i / kFs);
		wsum += w;
	}
	return 2 * std::abs(acc) / wsum;
}

// Magnitude spectrum of n samples from start
static std::vector<double> spectrum(const std::vector<double>& x, size_t start, size_t n) {
	std::vector<std::complex<double>> a(n);
	for (size_t i = 0; i < n; i++) {
		a[i] = x[start + i] * window(i, n);
	}
	fft(a);
	std::vector<double> m(n / 2);
	for (size_t i = 0; i < n / 2; i++) {
		m[i] = std::abs(a[i]);
	}
	return m;
}

// Frequency of the strongest peak between lo and hi Hz (parabolic
// interpolation)
static double peakHz(const std::vector<double>& m, size_t n, double lo, double hi) {
	size_t a = (size_t)(lo * n / kFs), b = (size_t)(hi * n / kFs);
	size_t best = a;
	for (size_t i = a; i <= b; i++) {
		if (m[i] > m[best]) {
			best = i;
		}
	}
	double y0 = std::log(m[best - 1]), y1 = std::log(m[best]), y2 = std::log(m[best + 1]);
	double d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2);
	return (best + d) * kFs / n;
}

// Largest level (dB, relative to the strongest bin) outside +-guard bins of the
// given frequencies
static double worstOther(const std::vector<double>& m, size_t n, std::vector<double> allowedHz, size_t guard = 12) {
	double top = 0;
	for (double v : m) {
		top = std::max(top, v);
	}
	double worst = 0;
	for (size_t i = 3; i < m.size(); i++) {
		bool allowed = false;
		for (double f : allowedHz) {
			if (std::abs((double)i - f * n / kFs) <= guard) {
				allowed = true;
			}
		}
		if (!allowed) {
			worst = std::max(worst, m[i]);
		}
	}
	return 20 * std::log10(worst / top + 1e-30);
}

static double maxAbs(const std::vector<double>& x, size_t from, size_t to) {
	double m = 0;
	for (size_t i = from; i < to && i < x.size(); i++) {
		m = std::max(m, std::abs(x[i]));
	}
	return m;
}

// Largest second difference, the sign of a step or kink
static double maxD2(const std::vector<double>& x, size_t from, size_t to) {
	double m = 0;
	for (size_t i = std::max<size_t>(from, 1); i + 1 < to && i + 1 < x.size(); i++) {
		m = std::max(m, std::abs(x[i + 1] - 2 * x[i] + x[i - 1]));
	}
	return m;
}

static TestDrone makeDrone() {
	TestDrone d;
	d.volume = 50;
	return d;
}

// A steady drone through every path: all modes and timbres, a synced beat locking to the grid, a note following the
// master tune, glides, a fade through silence for a new timbre and mode, a tone switched off, ducking and blocks of
// different sizes. Returns the FNV-1a hash of the output.
static uint64_t steadyScenarioHash(Drone& drone, DroneSettings& s) {
	auto set = [](Drone::Tone& t, Drone::Mode mode, Drone::Timbre timbre, int32_t frequency, int32_t beat, int32_t level,
	              int32_t pan) {
		t.active = true;
		t.mode = mode;
		t.timbre = timbre;
		t.frequency = frequency;
		t.beat = beat;
		t.level = level;
		t.pan = pan;
	};
	s.volume = 45;
	set(s.tones[0], Drone::Mode::TONE, Drone::Timbre::SINE, 44000, 0, 45, -10);
	set(s.tones[1], Drone::Mode::BINAURAL, Drone::Timbre::SOFT, 20000, 750, 40, 12);
	set(s.tones[2], Drone::Mode::MONAURAL, Drone::Timbre::ORGAN, 15000, 400, 38, 0);
	set(s.tones[3], Drone::Mode::ISOCHRONIC, Drone::Timbre::RICH, 30000, 0, 42, -5);
	s.tones[3].sync = 5;
	s.tones[3].triplet = true;
	s.tones[3].pulseAttack = 10;
	s.tones[3].pulseRelease = 20;
	set(s.tones[4], Drone::Mode::TONE, Drone::Timbre::RICH, 0, 0, 30, 32);
	s.tones[4].byNote = true;
	s.tones[4].note = 45;
	s.tones[4].cents = 13;
	set(s.tones[5], Drone::Mode::BINAURAL, Drone::Timbre::RICH, 300000, 2000, 35, -32);
	uint64_t hash = 1469598103934665603ull;
	const size_t sizes[] = {128, 61, 300, 128, 7, 128, 200};
	size_t pos = 0;
	for (int block = 0; pos < 3 * 44100; block++) {
		size_t n = sizes[block % 7];
		double t = pos / 44100.0;
		if (t >= 1.0) {
			s.tones[1].frequency = 26000;
		}
		if (t >= 1.5) {
			s.tones[0].timbre = Drone::Timbre::SOFT;
		}
		if (t >= 2.0) {
			s.tones[2].active = false;
		}
		if (t >= 2.2) {
			s.tones[3].mode = Drone::Mode::BINAURAL;
		}
		Drone::Context c;
		c.quarterNotesPerSecond = 2.f;
		c.position = 0.5 + t * 2.0;
		c.duck = (float)(0.55 + 0.45 * std::cos(t * 13.0));
		c.volume = 0.9f;
		c.a4Hz = (t < 1.2) ? 440.f : 432.f;
		std::vector<StereoSample> buf(n);
		for (auto& x : buf) {
			x.l = x.r = 1000; // Added to what's there
		}
		drone.render(std::span<StereoSample>(buf.data(), n), s, c);
		for (auto& x : buf) {
			for (int32_t v : {x.l, x.r}) {
				for (int b = 0; b < 4; b++) {
					hash = (hash ^ (uint8_t)(v >> (8 * b))) * 1099511628211ull;
				}
			}
		}
		pos += n;
	}
	return hash;
}

/// What the steady scenario gives with the drone of mastertune-v14 (c1d1c8bb), built for the PC as here
constexpr uint64_t kSteadyHashV14 = 0x4e3344279eb960e2ull;

// Frequency of a sine over [from, to), from its rising zero crossings (interpolated)
static double sineHz(const std::vector<double>& x, size_t from, size_t to) {
	double first = -1, last = -1;
	int cycles = -1;
	for (size_t i = from; i + 1 < to; i++) {
		if (x[i] < 0 && x[i + 1] >= 0) {
			double t = i + x[i] / (x[i] - x[i + 1]);
			if (first < 0) {
				first = t;
			}
			last = t;
			cycles++;
		}
	}
	return cycles > 0 ? cycles * kFs / (last - first) : 0;
}

struct Spread {
	double sd = 0, lo = 1e300, hi = -1e300, speedHz = 0; // speedHz: how fast it moves, see courseSpread()
};

// Standard deviation around 0 and range of sequences (one per run), and the course's speed: for noise through two
// one-pole low-passes at fc, the derivative's standard deviation is 2 pi fc times the value's
static Spread courseSpread(const std::vector<std::vector<double>>& runs, double dt) {
	Spread r;
	double sum2 = 0, diff2 = 0;
	size_t n = 0, nd = 0;
	for (auto& x : runs) {
		for (size_t i = 0; i < x.size(); i++) {
			sum2 += x[i] * x[i];
			n++;
			r.lo = std::min(r.lo, x[i]);
			r.hi = std::max(r.hi, x[i]);
			if (i) {
				diff2 += (x[i] - x[i - 1]) * (x[i] - x[i - 1]);
				nd++;
			}
		}
	}
	r.sd = std::sqrt(sum2 / n);
	r.speedHz = std::sqrt(diff2 / nd) / dt / (2 * M_PI * r.sd);
	return r;
}

// Energy (sum of squares) of the spectrum between lo and hi Hz
static double bandEnergy(const std::vector<double>& m, size_t n, double lo, double hi) {
	double e = 0;
	for (size_t i = (size_t)(lo * n / kFs); i <= (size_t)(hi * n / kFs) && i < m.size(); i++) {
		e += m[i] * m[i];
	}
	return e;
}

int main() {
	Drone::initTables(tableMemory.data());
	const size_t kN = 65536;

	// 1. A pure tone: level and purity
	{
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::TONE, Timbre::SINE, 44000, 0, 0, 50, 0);
		Out o = run(d, 20000 + kN);
		double peak = maxAbs(o.l, 20000, 20000 + kN);
		auto m = spectrum(o.l, 20000, kN);
		double f = peakHz(m, kN, 400, 480);
		double other = worstOther(m, kN, {440});
		printf("sine 440 Hz: peak %.3f of full scale, at %.4f Hz, strongest other "
		       "component %.1f dB\n",
		       peak / kFullScale, f, other);
		CHECK(std::abs(peak / kFullScale - 1) < 0.01, "level at 50/50 should be -12 dBFS");
		CHECK(std::abs(f - 440) < 0.01, "frequency %.4f", f);
		CHECK(other < -100, "sine purity %.1f dB", other);
		CHECK(o.l == o.r, "tone in the middle: both sides the same");
	}

	// 2. Binaural: 195 Hz left, 205 Hz right for 200 Hz with a 10 Hz beat
	{
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::BINAURAL, Timbre::SINE, 20000, 1000, 0, 50, 0);
		Out o = run(d, 20000 + kN);
		double fl = peakHz(spectrum(o.l, 20000, kN), kN, 150, 250);
		double fr = peakHz(spectrum(o.r, 20000, kN), kN, 150, 250);
		double ol = worstOther(spectrum(o.l, 20000, kN), kN, {195});
		printf("binaural 200 Hz, 10 Hz beat: left %.4f Hz, right %.4f Hz, left's "
		       "other components %.1f dB\n",
		       fl, fr, ol);
		CHECK(std::abs(fl - 195) < 0.01 && std::abs(fr - 205) < 0.01, "binaural frequencies");
		CHECK(ol < -100, "binaural purity %.1f dB", ol);
	}

	// 3. Monaural: both tones in both ears, the level beating at 10 Hz
	{
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::MONAURAL, Timbre::SINE, 20000, 1000, 0, 50, 0);
		Out o = run(d, 20000 + kN);
		double a = amplitudeAt(o.l, 20000, kN, 195), b = amplitudeAt(o.l, 20000, kN, 205);
		double peak = maxAbs(o.l, 20000, 20000 + kN);
		printf("monaural: 195 and 205 Hz within %.2f dB of each other, peak %.3f "
		       "of full scale\n",
		       20 * std::log10(a / b), peak / kFullScale);
		CHECK(std::abs(20 * std::log10(a / b)) < 0.1, "monaural balance");
		CHECK(std::abs(peak / kFullScale - 1) < 0.01, "monaural peak");
		CHECK(o.l == o.r, "monaural: the same in both ears");
	}

	// 4. Isochronic: 10 Hz pulses, on half the time, soft edges
	{
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::ISOCHRONIC, Timbre::SINE, 20000, 1000, 0, 50, 0);
		Out o = run(d, 20000 + 44100);
		// Envelope: the peak in each 1 ms window
		int on = 0, windows = 0, pulses = 0;
		bool wasOn = false;
		for (size_t w = 20000; w + 44 < 20000 + 44100; w += 44) {
			bool isOn = maxAbs(o.l, w, w + 44) > 0.5 * kFullScale;
			on += isOn;
			windows++;
			if (isOn && !wasOn) {
				pulses++;
			}
			wasOn = isOn;
		}
		double steady = maxD2(o.l, 20000, 20000 + 44100);
		double toneD2 = kFullScale * std::pow(2 * M_PI * 200 / kFs, 2);
		printf("isochronic 10 Hz: %d pulses in 1 s, on %.0f%% of the time, largest "
		       "step %.2f times the tone's own\n",
		       pulses, 100.0 * on / windows, steady / toneD2);
		CHECK(pulses >= 9 && pulses <= 11, "10 pulses a second");
		CHECK(std::abs(100.0 * on / windows - 50) < 8, "on half the time");
		CHECK(steady / toneD2 < 1.5, "isochronic edges should be soft");
	}

	// 5. Changes: none may step. Measured against the tone's own second
	// difference.
	{
		struct Case {
			const char* name;
			std::function<void(TestDrone&)> change;
		};
		std::vector<Case> cases = {
		    {"frequency 200 -> 300 Hz", [](TestDrone& d) { d.tones[0].frequency = 30000; }},
		    {"level 50 -> 20", [](TestDrone& d) { d.tones[0].level = 20; }},
		    {"level 20 -> 50", [](TestDrone& d) { d.tones[0].level = 50; }},
		    {"pan to the left", [](TestDrone& d) { d.tones[0].pan = -32; }},
		    {"tone -> binaural", [](TestDrone& d) { d.tones[0].mode = Mode::BINAURAL; }},
		    {"tone -> isochronic", [](TestDrone& d) { d.tones[0].mode = Mode::ISOCHRONIC; }},
		    {"sine -> rich", [](TestDrone& d) { d.tones[0].timbre = Timbre::RICH; }},
		    {"off", [](TestDrone& d) { d.tones[0].active = false; }},
		    {"volume 50 -> 10", [](TestDrone& d) { d.volume = 10; }},
		};
		for (auto& c : cases) {
			TestDrone d = makeDrone();
			d.tones[0] = on(Mode::TONE, Timbre::SINE, 20000, 1000, 0, 50, 0);
			const size_t at = 30000 + 37; // Not on a block boundary of the test (the
			                              // drone sees it at the next)
			Out o = run(d, 60000, [&](size_t pos, Drone::Context&) {
				if (pos <= at && at < pos + kBlock) {
					c.change(d);
				}
			});
			// Against the sound's own curvature before the change and once it has
			// settled (a richer timbre or a higher tone has more)
			double own = std::max(
			    {maxD2(o.l, 20000, at), maxD2(o.r, 20000, at), maxD2(o.l, 50000, 60000), maxD2(o.r, 50000, 60000)});
			double toneD2 = std::max(own, kFullScale * std::pow(2 * M_PI * 200 / kFs, 2) * 0.01);
			double after = std::max(maxD2(o.l, at, 50000), maxD2(o.r, at, 50000));
			printf("change, %s: largest step %.2f times the sound's own\n", c.name, after / toneD2);
			CHECK(after / toneD2 < 1.2, "%s steps", c.name);
		}
		// Switched off, it goes quiet and stops
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::TONE, Timbre::SINE, 20000, 1000, 0, 50, 0);
		run(d, 20000);
		d.tones[0].active = false;
		Out o = run(d, 22050);
		CHECK(!d.isSounding(), "stops after being switched off");
		CHECK(maxAbs(o.l, 11025, 22050) == 0, "silent after the fade");
	}

	// 6. Sidechain: the gain glides over each block from where the last one ended
	// to the new duck (the v3 sidechain fix), so the output is the tone times
	// that line, without steps at the block edges
	{
		auto duckAt = [](size_t pos) {
			// A kick every 0.5 s: the envelope (as SideChain::render() gives it, once
			// per block) down to 0.1 over two blocks, back up over 100 ms
			double t = std::fmod(pos / kFs, 0.5);
			return (float)(t < 0.006 ? 1.0 - 0.9 * t / 0.006 : std::min(1.0, 0.1 + 0.9 * (t - 0.006) / 0.1));
		};
		TestDrone plain = makeDrone();
		plain.tones[0] = on(Mode::TONE, Timbre::SINE, 20000, 1000, 0, 50, 0);
		Out ref = run(plain, 44100);
		TestDrone d = makeDrone();
		d.tones[0] = plain.tones[0];
		Out o = run(d, 44100, [&](size_t pos, Drone::Context& c) { c.duck = duckAt(pos + kBlock); });
		double worstError = 0, lowest = 1;
		double before = duckAt(kBlock);
		for (size_t pos = 0; pos < 44100; pos += kBlock) {
			double end = duckAt(pos + kBlock);
			size_t m = std::min<size_t>(kBlock, 44100 - pos);
			for (size_t s = 0; s < m; s++) {
				double g = before + (end - before) * (double)(s + 1) / m;
				if (pos >= 20000) { // Once the tone's own fade-in is done
					worstError = std::max(worstError, std::abs(o.l[pos + s] - ref.l[pos + s] * g) / kFullScale);
				}
				lowest = std::min(lowest, g);
			}
			before = end;
		}
		printf("sidechain: follows the duck, gliding over each block, to within "
		       "%.1e of full scale (down to %.2f)\n",
		       worstError, lowest);
		CHECK(worstError < 1e-4, "ducking follows the glide (%.1e)", worstError);
	}

	// 7. Band-limited: a rich tone at 3 kHz has harmonics up to 18 kHz and
	// nothing else
	{
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::TONE, Timbre::RICH, 300000, 0, 0, 50, 0);
		Out o = run(d, 20000 + kN);
		auto m = spectrum(o.l, 20000, kN);
		double other = worstOther(m, kN, {3000, 6000, 9000, 12000, 15000, 18000});
		double h7 =
		    20 * std::log10(m[(size_t)std::round(21000.0 * kN / kFs)] / m[(size_t)std::round(3000.0 * kN / kFs)]);
		printf("rich 3 kHz: 7th harmonic (21 kHz) %.1f dB, anything else %.1f dB\n", h7, other);
		CHECK(other < -80, "aliasing %.1f dB", other);
		CHECK(h7 < -80, "harmonics above 18 kHz dropped");
		// And the timbres at 100 Hz: soft, organ and rich all peak at full scale
		for (Timbre t : {Timbre::SOFT, Timbre::ORGAN, Timbre::RICH}) {
			TestDrone e = makeDrone();
			e.tones[0] = on(Mode::TONE, t, 10000, 0, 0, 50, 0);
			Out p = run(e, 20000 + 44100);
			double pk = maxAbs(p.l, 20000, 20000 + 44100) / kFullScale;
			printf("timbre %d at 100 Hz: peak %.3f of full scale\n", (int)t, pk);
			CHECK(pk < 1.001 && pk > 0.95, "timbre peak");
		}
	}

	// 8. Synced beat: 1/16 at 120 BPM is 8 Hz, and while playing the pulses start
	// on the 16ths
	{
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::ISOCHRONIC, Timbre::SINE, 40000, 0, 5, 50, 0);
		Drone::Context c;
		c.quarterNotesPerSecond = 2.f;
		c.position = 0.3; // Playing, from somewhere off the grid
		Out o = run(d, 44100 * 3, nullptr, c);
		// Pulse onsets in the last second against the 16th grid: the envelope
		// (largest value over the tone's period, 2.5 ms, centred) crosses half way
		// after half the attack: the default attack is 6 % of the cycle, 7.5 ms at 8 Hz
		double worstOffset = 0;
		int onsets = 0;
		bool wasOn = true;
		for (size_t i = 88200; i + 60 < o.l.size(); i++) {
			bool isOn = maxAbs(o.l, i - 55, i + 55) > 0.5 * kFullScale;
			if (isOn && !wasOn) {
				double q = 0.3 + i / kFs * 2.0; // Quarter notes from position 0
				double sixteenths = q * 4;
				double offMs = (sixteenths - std::floor(sixteenths)) / 8.0 * 1000; // After the last 16th
				worstOffset = std::max(worstOffset, std::abs(offMs - 3.75));
				onsets++;
			}
			wasOn = isOn;
		}
		printf("synced 1/16 at 120 BPM: %d pulses in 1 s, onsets within %.2f ms of "
		       "the grid\n",
		       onsets, worstOffset);
		CHECK(onsets >= 7 && onsets <= 9, "8 pulses a second");
		CHECK(worstOffset < 1.5, "locked to the grid");
		CHECK(std::abs(Drone::syncedBeatHz(5, false, 2.f) - 8.f) < 1e-6, "1/16 at 120 BPM is 8 Hz");
		CHECK(std::abs(Drone::syncedBeatHz(5, true, 2.f) - 12.f) < 1e-6, "1/16T at 120 BPM is 12 Hz");
		CHECK(std::abs(Drone::syncedBeatHz(3, true, 2.f) - 3.f) < 1e-6, "1/4T at 120 BPM is 3 Hz");
	}

	// 8a. Triplets (mastertune-v13): 1/16T at 120 BPM pulses 12 times a second, locked to the triplet grid (six to a
	// quarter note)
	{
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::ISOCHRONIC, Timbre::SINE, 40000, 0, 5, 50, 0);
		d.tones[0].triplet = true;
		Drone::Context c;
		c.quarterNotesPerSecond = 2.f;
		c.position = 0.3;
		Out o = run(d, 44100 * 3, nullptr, c);
		double expectedMs = 0.06 / 12.0 / 2.0 * 1000; // Half the default attack (6 % of the cycle)
		double worstOffset = 0;
		int onsets = 0;
		bool wasOn = true;
		for (size_t i = 88200; i + 60 < o.l.size(); i++) {
			bool isOn = maxAbs(o.l, i - 55, i + 55) > 0.5 * kFullScale;
			if (isOn && !wasOn) {
				double q = 0.3 + i / kFs * 2.0;
				double cycles = q * 6;
				double offMs = (cycles - std::floor(cycles)) / 12.0 * 1000;
				worstOffset = std::max(worstOffset, std::abs(offMs - expectedMs));
				onsets++;
			}
			wasOn = isOn;
		}
		printf("synced 1/16T at 120 BPM: %d pulses in 1 s, onsets within %.2f ms of the triplet grid\n", onsets,
		       worstOffset);
		CHECK(onsets >= 11 && onsets <= 13, "12 pulses a second");
		CHECK(worstOffset < 1.5, "locked to the triplet grid");
	}

	// 8b. Isochronic attack and release (mastertune-v13): at a 2 Hz beat, attack 25 is half the on-half (125 ms), release
	// 10 is 10 % of the cycle (50 ms); smoothstep ramps take 0.608 of their time from 10 % to 90 %. And with both at 0
	// the edges still take 1 ms, so nothing clicks.
	{
		auto edges = [](int32_t beat, int32_t attack, int32_t release, double* rise, double* fall) {
			TestDrone d = makeDrone();
			d.tones[0] = on(Mode::ISOCHRONIC, Timbre::SINE, 200000, beat, 0, 50, 0); // 2 kHz carrier: a sharp envelope
			d.tones[0].pulseAttack = attack;
			d.tones[0].pulseRelease = release;
			Out o = run(d, 44100 * 3);
			std::vector<double> e(o.l.size(), 0.0);
			for (size_t i = 12; i + 12 < o.l.size(); i++) {
				e[i] = maxAbs(o.l, i - 11, i + 12) / kFullScale;
			}
			// The last full rise and fall in the third second
			double t10 = -1, t90 = -1, f90 = -1, f10 = -1;
			*rise = *fall = 0;
			for (size_t i = 88200; i + 1 < e.size(); i++) {
				if (e[i] < 0.1 && e[i + 1] >= 0.1) t10 = i;
				if (e[i] < 0.9 && e[i + 1] >= 0.9 && t10 >= 0) { t90 = i; *rise = (t90 - t10) / kFs * 1000; }
				if (e[i] >= 0.9 && e[i + 1] < 0.9) f90 = i;
				if (e[i] >= 0.1 && e[i + 1] < 0.1 && f90 >= 0) { f10 = i; *fall = (f10 - f90) / kFs * 1000; }
			}
		};
		double rise, fall;
		edges(200, 25, 10, &rise, &fall);
		printf("isochronic 2 Hz, attack 25, release 10: rise %.1f ms (76.0 expected), fall %.1f ms (30.4 expected)\n",
		       rise, fall);
		CHECK(std::abs(rise - 76.0) < 3, "attack sets the rise");
		CHECK(std::abs(fall - 30.4) < 3, "release sets the fall");
		edges(1000, 0, 0, &rise, &fall);
		printf("isochronic 10 Hz, attack and release 0: rise %.2f ms, fall %.2f ms (at least 1 ms edges)\n", rise, fall);
		CHECK(rise > 0.5 && fall > 0.5, "edges never shorter than 1 ms");
	}

	// 9. Pitch by note, following the master tune: A4 at 440 and at 432 Hz, and no step when the master tune changes
	// while it sounds
	{
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::TONE, Timbre::SINE, 0, 0, 0, 50, 0);
		d.tones[0].byNote = true;
		d.tones[0].note = 69;
		Out o = run(d, 20000 + kN);
		double at440 = peakHz(spectrum(o.l, 20000, kN), kN, 400, 480);
		Drone::Context c;
		c.a4Hz = 432.f;
		TestDrone e = makeDrone();
		e.tones[0] = d.tones[0];
		e.tones[0].cents = -50; // And a quarter tone down from there
		Out p = run(e, 20000 + kN, nullptr, c);
		double at432 = peakHz(spectrum(p.l, 20000, kN), kN, 400, 480);
		double expected = 432.0 * std::pow(2.0, -0.5 / 12);
		printf("note A4: %.3f Hz at 440, a quarter tone down at 432: %.3f Hz (%.3f expected)\n", at440, at432,
		       expected);
		CHECK(std::abs(at440 - 440) < 0.01, "A4 at 440");
		CHECK(std::abs(at432 - expected) < 0.01, "A4 - 50 cents at 432");
		TestDrone g = makeDrone();
		g.tones[0] = d.tones[0];
		Out q = run(g, 44100, [](size_t pos, Drone::Context& ctx) { ctx.a4Hz = pos < 22050 ? 440.f : 452.f; });
		double own = kFullScale * std::pow(2 * M_PI * 452 / kFs, 2);
		double worst = maxD2(q.l, 20000, 44100);
		printf("master tune 440 -> 452 while sounding: largest step %.2f times the tone's own\n", worst / own);
		CHECK(worst / own < 1.2, "master tune change glides");
	}

	// 10a. Between two songs (no song while one loads, Clear Song): fadeOut()
	// takes what's sounding down to silence where it is, and the next song's
	// drone starts from silence
	{
		TestDrone d = makeDrone();
		d.tones[0] = on(Mode::BINAURAL, Timbre::SOFT, 20000, 700, 0, 50, -10);
		d.tones[3] = on(Mode::TONE, Timbre::SINE, 44000, 0, 0, 40, 20);
		Drone::Context ducked;
		ducked.duck = 0.5f;
		Out o = run(d, 30000, nullptr, ducked);
		size_t blocks = 0;
		while (d.drone.isSounding() && blocks < 100) {
			std::vector<StereoSample> buf(kBlock);
			d.drone.fadeOut(std::span<StereoSample>(buf.data(), kBlock));
			for (auto& s : buf) {
				o.l.push_back(s.l);
				o.r.push_back(s.r);
			}
			blocks++;
		}
		size_t at = 30000 - 30000 % kBlock + (30000 % kBlock ? kBlock : 0);
		double before = std::max(maxD2(o.l, 20000, at), maxD2(o.r, 20000, at));
		double during = std::max(maxD2(o.l, at - 8, o.l.size()), maxD2(o.r, at - 8, o.r.size()));
		double firstBlock = std::max(maxAbs(o.l, at, at + kBlock), maxAbs(o.r, at, at + kBlock));
		double lastBefore = std::max(maxAbs(o.l, at - kBlock, at), maxAbs(o.r, at - kBlock, at));
		printf("fade out between songs: silent after %zu blocks, largest step %.2f times the steady one, first block "
		       "%.2f of the last\n",
		       blocks, during / before, firstBlock / lastBefore);
		CHECK(blocks > 1 && blocks <= 40, "fades out in a few blocks, %zu", blocks);
		CHECK(during / before < 1.2, "no step at the start of the fade");
		CHECK(firstBlock / lastBefore < 1.05, "the fade doesn't jump up (it keeps the sidechain's gain)");
		// The next song: from silence, gliding up
		Out n = run(d, 2 * kBlock);
		double start = std::max(maxAbs(n.l, 0, 4), maxAbs(n.r, 0, 4));
		printf("  next song: first samples %.4f of full scale\n", start / kFullScale);
		CHECK(start < 0.01 * kFullScale, "next song starts from silence");
	}

	// 11. Life and FM off (mastertune-v15): the drone of v14, bit for bit, whatever the other new settings are
	{
		uint64_t plain, others;
		{
			Drone drone;
			DroneSettings s;
			plain = steadyScenarioHash(drone, s);
		}
		{
			Drone drone;
			drone.seed(12345);
			DroneSettings s;
			s.lifeRate = 40;
			s.fmForm = DroneSettings::FmForm::SAW;
			s.pulseWidth = 12;
			others = steadyScenarioHash(drone, s);
		}
		printf("life 0, FM 0: steady scenario %016llx, with the other new settings changed %016llx, v14 %016llx\n",
		       (unsigned long long)plain, (unsigned long long)others, (unsigned long long)kSteadyHashV14);
		CHECK(plain == others, "life's other settings change nothing while life and FM are 0");
#if !defined(__arm__)
		CHECK(plain == kSteadyHashV14, "the steady drone is v14's bit for bit");
#endif
	}

	// 12. Life 50: the pitch drifts (6 cents standard deviation), the level breathes (3 dB), each on its own course,
	// at the speeds set (0.08 and 0.12 Hz at rate 25, twice as fast each 12.5 steps). A 1 kHz sine, 8 runs of 64 s,
	// measured every 50 ms after the first 10 s (the courses start at rest).
	{
		const size_t kWindow = 2205;
		const double dt = kWindow / kFs;
		Spread drift[3], breath[3];
		const int32_t rates[3] = {12, 25, 38};
		for (int r = 0; r < 3; r++) {
			std::vector<std::vector<double>> cents, db;
			for (uint32_t seed = 1; seed <= 8; seed++) {
				TestDrone d = makeDrone();
				d.drone.seed(seed);
				d.settings.life = 50;
				d.settings.lifeRate = rates[r];
				d.tones[0] = on(Mode::TONE, Timbre::SINE, 100000, 0, 0, 50, 0);
				std::vector<double> c, l;
				for (size_t w = 0; w < (size_t)(64 * kFs / kWindow); w++) {
					Out o = run(d, kWindow);
					if (w * dt < 10) {
						continue;
					}
					std::vector<double> mono(kWindow);
					double power = 0;
					for (size_t i = 0; i < kWindow; i++) {
						mono[i] = o.l[i] + o.r[i];
						power += o.l[i] * o.l[i] + o.r[i] * o.r[i];
					}
					c.push_back(1200 * std::log2(sineHz(mono, 0, kWindow) / 1000));
					l.push_back(10 * std::log10(power / kWindow / (kFullScale * kFullScale)));
				}
				cents.push_back(c);
				db.push_back(l);
			}
			drift[r] = courseSpread(cents, dt);
			breath[r] = courseSpread(db, dt);
			printf("life 50, rate %d: drift %.2f cents sd (%.1f to %+.1f), moving at %.3f Hz; breath %.2f dB sd (%.1f "
			       "to %+.1f), at %.3f Hz\n",
			       (int)rates[r], drift[r].sd, drift[r].lo, drift[r].hi, drift[r].speedHz, breath[r].sd, breath[r].lo,
			       breath[r].hi, breath[r].speedHz);
		}
		CHECK(std::abs(drift[1].sd - 6) < 1.5, "drift 6 cents sd: %.2f", drift[1].sd);
		CHECK(drift[1].lo > -18.5 && drift[1].hi < 18.5, "drift within 3 sd");
		CHECK(std::abs(breath[1].sd - 3) < 0.75, "breath 3 dB sd: %.2f", breath[1].sd);
		CHECK(breath[1].lo > -9.3 && breath[1].hi < 9.3, "breath within 3 sd");
		CHECK(std::abs(drift[1].speedHz / 0.08 - 1) < 0.25, "drift at 0.08 Hz: %.3f", drift[1].speedHz);
		CHECK(std::abs(breath[1].speedHz / 0.12 - 1) < 0.25, "breath at 0.12 Hz: %.3f", breath[1].speedHz);
		double factor = std::exp2(13 / 12.5);
		for (int r : {0, 2}) {
			double expected = (r == 2) ? factor : 1 / factor;
			CHECK(std::abs(drift[r].speedHz / drift[1].speedHz / expected - 1) < 0.25, "drift follows the rate");
			CHECK(std::abs(breath[r].speedHz / breath[1].speedHz / expected - 1) < 0.25, "breath follows the rate");
			CHECK(std::abs(drift[r].sd / drift[1].sd - 1) < 0.3, "the rate leaves the depth");
		}
	}

	// 13. FM 50 on a 500 Hz sine: sidebands at the harmonics, their depth blooming and fading on its course (the index
	// up to 2 radians, a quarter of the time 0), and nothing near 0 Hz (the DC blocker)
	{
		TestDrone d = makeDrone();
		d.drone.seed(3);
		d.settings.fm = 50;
		d.tones[0] = on(Mode::TONE, Timbre::SINE, 50000, 0, 0, 50, 0);
		const size_t kW = 4096;
		double lo = 0, hi = -300, dc = 0;
		int blooms = 0;
		bool up = false;
		run(d, 44100);
		for (size_t w = 0; w < (size_t)(60 * kFs / kW); w++) {
			Out o = run(d, kW);
			auto m = spectrum(o.l, 0, kW);
			double ratio = 10 * std::log10(bandEnergy(m, kW, 750, 20000) / bandEnergy(m, kW, 20, 20000) + 1e-30);
			lo = std::min(lo, ratio);
			hi = std::max(hi, ratio);
			if (!up && ratio > -6) {
				blooms++;
				up = true;
			}
			if (up && ratio < -20) {
				up = false;
			}
			double mean = 0;
			for (double v : o.l) {
				mean += v;
			}
			dc = std::max(dc, std::abs(mean / kW) / kFullScale);
		}
		printf("FM 50 on 500 Hz: sidebands %.1f to %.1f dB of the whole, %d blooms in 60 s; largest DC (93 ms) %.1f dB\n",
		       lo, hi, blooms, 20 * std::log10(dc + 1e-30));
		CHECK(hi > -3, "FM blooms deep: %.1f dB", hi);
		CHECK(lo < -60, "and fades to nothing: %.1f dB", lo);
		CHECK(blooms >= 2 && blooms <= 20, "blooms %d", blooms);
		CHECK(20 * std::log10(dc) < -40, "no DC from FM");
	}

	// 14. FM form: the saw modulator (8 harmonics, 1.3 times as deep) reaches far higher harmonics than the sine, on the
	// same course (same seed)
	{
		double upper[2];
		for (int form = 0; form < 2; form++) {
			TestDrone d = makeDrone();
			d.drone.seed(5);
			d.settings.fm = 50;
			d.settings.fmForm = (DroneSettings::FmForm)form;
			d.tones[0] = on(Mode::TONE, Timbre::SINE, 30000, 0, 0, 50, 0);
			const size_t kW = 8192;
			double second = 0, high = 0;
			run(d, 44100);
			for (size_t w = 0; w < (size_t)(30 * kFs / kW); w++) {
				Out o = run(d, kW);
				auto m = spectrum(o.l, 0, kW);
				second += bandEnergy(m, kW, 550, 650);
				for (int h = 3; h <= 10; h++) {
					high += bandEnergy(m, kW, 300 * h - 50, 300 * h + 50);
				}
			}
			upper[form] = 10 * std::log10(high / second);
		}
		printf("FM form on 300 Hz: harmonics 3 to 10 against the 2nd, sine %.1f dB, saw %.1f dB\n", upper[0], upper[1]);
		CHECK(upper[1] > upper[0] + 10, "the saw modulator reaches higher");
	}

	// 15. Pulse: 30 % wide at life 0, the setting's width, and with life 50 wandering (+-12 % standard deviation)
	// between 8 and 50 %; measured by its edges (the steepest rise and fall of each cycle) at 100 Hz
	{
		auto widths = [](int32_t life, int32_t width, uint32_t seed, double seconds) {
			TestDrone d = makeDrone();
			d.drone.seed(seed);
			d.settings.life = life;
			d.settings.pulseWidth = width;
			d.tones[0] = on(Mode::TONE, Timbre::PULSE, 10000, 0, 0, 50, 0);
			run(d, 22050);
			std::vector<double> w;
			const size_t kW = 4410; // 10 cycles
			for (size_t n = 0; n < (size_t)(seconds * kFs / kW); n++) {
				Out o = run(d, kW);
				std::vector<double> diff(kW, 0.0);
				double steepest = 0;
				for (size_t i = 1; i < kW; i++) {
					diff[i] = (o.l[i] + o.r[i]) - (o.l[i - 1] + o.r[i - 1]);
					steepest = std::max(steepest, std::abs(diff[i]));
				}
				std::vector<std::pair<size_t, int>> edges;
				for (size_t i = 2; i + 1 < kW; i++) {
					if (std::abs(diff[i]) > 0.5 * steepest && std::abs(diff[i]) >= std::abs(diff[i - 1])
					    && std::abs(diff[i]) > std::abs(diff[i + 1])) {
						edges.push_back({i, diff[i] > 0 ? 1 : -1});
					}
				}
				for (size_t e = 0; e + 2 < edges.size(); e++) {
					if (edges[e].second == 1 && edges[e + 1].second == -1 && edges[e + 2].second == 1) {
						w.push_back((double)(edges[e + 1].first - edges[e].first)
						            / (double)(edges[e + 2].first - edges[e].first));
					}
				}
			}
			return w;
		};
		auto stats = [](const std::vector<double>& w, double* mean, double* sd, double* lo, double* hi) {
			double s = 0, s2 = 0;
			*lo = 1, *hi = 0;
			for (double x : w) {
				s += x;
				s2 += x * x;
				*lo = std::min(*lo, x);
				*hi = std::max(*hi, x);
			}
			*mean = s / w.size();
			*sd = std::sqrt(std::max(s2 / w.size() - *mean * *mean, 0.0));
		};
		double mean, sd, lo, hi;
		stats(widths(0, 30, 1, 2), &mean, &sd, &lo, &hi);
		printf("pulse at life 0: %.1f %% wide (%.1f to %.1f)\n", mean * 100, lo * 100, hi * 100);
		CHECK(std::abs(mean - 0.30) < 0.005 && hi - lo < 0.01, "pulse 30 %% wide");
		stats(widths(0, 10, 1, 2), &mean, &sd, &lo, &hi);
		printf("pulse at life 0, width 10: %.1f %% wide\n", mean * 100);
		CHECK(std::abs(mean - 0.10) < 0.005, "pulse 10 %% wide");
		std::vector<double> all;
		for (uint32_t seed = 1; seed <= 4; seed++) {
			auto w = widths(50, 30, seed, 60);
			all.insert(all.end(), w.begin(), w.end());
		}
		stats(all, &mean, &sd, &lo, &hi);
		printf("pulse at life 50: width %.1f %% on average, %.1f %% sd, %.1f to %.1f %% (%zu cycles)\n", mean * 100,
		       sd * 100, lo * 100, hi * 100, all.size());
		CHECK(lo > 0.075 && hi < 0.505, "the width stays between 8 and 50 %%");
		CHECK(sd > 0.07 && sd < 0.14, "the width wanders: %.3f", sd);
		CHECK(lo < 0.12 && hi > 0.46, "all the way");
	}

	// 16. Changes while a tone sounds: none may click. Around the change (100 ms windows before, at and after it), the
	// largest second difference against the sound's own, each relative to the window's peak (life moves the level).
	{
		struct Case {
			const char* name;
			std::function<void(TestDrone&)> setup;
			std::function<void(TestDrone&)> change;
		};
		auto soft = [](TestDrone& d) { d.tones[0] = on(Mode::BINAURAL, Timbre::SOFT, 20000, 600, 0, 50, 0); };
		auto pulse = [](TestDrone& d) { d.tones[0] = on(Mode::TONE, Timbre::PULSE, 20000, 0, 0, 50, 0); };
		std::vector<Case> cases = {
		    {"life 0 -> 50", soft, [](TestDrone& d) { d.settings.life = 50; }},
		    {"life 50 -> 0", [&](TestDrone& d) { soft(d), d.settings.life = 50; },
		     [](TestDrone& d) { d.settings.life = 0; }},
		    {"rate 25 -> 50", [&](TestDrone& d) { soft(d), d.settings.life = 50; },
		     [](TestDrone& d) { d.settings.lifeRate = 50; }},
		    {"FM 0 -> 50", soft, [](TestDrone& d) { d.settings.fm = 50; }},
		    {"FM 50 -> 0", [&](TestDrone& d) { soft(d), d.settings.fm = 50; }, [](TestDrone& d) { d.settings.fm = 0; }},
		    {"FM form sine -> saw", [&](TestDrone& d) { soft(d), d.settings.fm = 50; },
		     [](TestDrone& d) { d.settings.fmForm = DroneSettings::FmForm::SAW; }},
		    {"FM form saw -> sine",
		     [&](TestDrone& d) { soft(d), d.settings.fm = 50, d.settings.fmForm = DroneSettings::FmForm::SAW; },
		     [](TestDrone& d) { d.settings.fmForm = DroneSettings::FmForm::SINE; }},
		    {"soft -> pulse", [&](TestDrone& d) { soft(d), d.settings.life = 50, d.settings.fm = 50; },
		     [](TestDrone& d) { d.tones[0].timbre = Timbre::PULSE; }},
		    {"pulse width 30 -> 50", pulse, [](TestDrone& d) { d.settings.pulseWidth = 50; }},
		    {"pulse, life and FM 0 -> 50", pulse, [](TestDrone& d) { d.settings.life = 50, d.settings.fm = 50; }},
		};
		for (auto& c : cases) {
			// The same course with and without the change, so it's made where the FM blooms most
			size_t at = 0;
			{
				TestDrone probe = makeDrone();
				probe.drone.seed(9);
				c.setup(probe);
				double most = -1;
				run(probe, 88200);
				for (size_t w = 0; w < 40; w++) {
					Out o = run(probe, 2048);
					double e = bandEnergy(spectrum(o.l, 0, 2048), 2048, 450, 20000);
					if (e > most) {
						most = e;
						at = 88200 + w * 2048 + 1024 + 37;
					}
				}
			}
			TestDrone d = makeDrone();
			d.drone.seed(9);
			c.setup(d);
			Out o = run(d, at + 20000, [&](size_t pos, Drone::Context&) {
				if (pos <= at && at < pos + kBlock) {
					c.change(d);
				}
			});
			auto sharpness = [&](size_t from, size_t to) {
				return std::max(maxD2(o.l, from, to) / (maxAbs(o.l, from, to) + 1),
				                maxD2(o.r, from, to) / (maxAbs(o.r, from, to) + 1));
			};
			double own = std::max(sharpness(at - 8820, at - 4410), sharpness(at + 8820, at + 13230));
			own = std::max(own, std::pow(2 * M_PI * 200 / kFs, 2)); // A sine's, at the least
			double change = std::max(sharpness(at - 4410, at), std::max(sharpness(at, at + 4410), sharpness(at + 4410, at + 8820)));
			printf("change, %s: largest step %.2f times the sound's own\n", c.name, change / own);
			CHECK(change / own < 1.3, "%s clicks", c.name);
		}
	}

	// 17. FM on high tones: the index is limited so the sidebands don't alias. The worst component that's no harmonic
	// (nor a sideband turning next to one) against the strongest, at the deepest bloom over 20 s.
	{
		struct Case {
			Timbre timbre;
			int32_t frequency;
			DroneSettings::FmForm form;
		};
		const Case cases[] = {
		    {Timbre::PULSE, 11000, DroneSettings::FmForm::SINE}, {Timbre::PULSE, 11000, DroneSettings::FmForm::SAW},
		    {Timbre::PULSE, 44000, DroneSettings::FmForm::SINE}, {Timbre::PULSE, 44000, DroneSettings::FmForm::SAW},
		    {Timbre::RICH, 100000, DroneSettings::FmForm::SINE}, {Timbre::RICH, 100000, DroneSettings::FmForm::SAW},
		    {Timbre::SINE, 300000, DroneSettings::FmForm::SINE}, {Timbre::SINE, 300000, DroneSettings::FmForm::SAW},
		    {Timbre::SINE, 500000, DroneSettings::FmForm::SAW},
		};
		for (const Case& k : cases) {
			TestDrone d = makeDrone();
			d.drone.seed(7);
			d.settings.fm = 50;
			d.settings.fmForm = k.form;
			d.tones[0] = on(Mode::TONE, k.timbre, k.frequency, 0, 0, 50, 0);
			const size_t kW = 16384;
			double f = k.frequency / 100.0;
			std::vector<double> harmonics;
			for (double h = f; h < 22050; h += f) {
				harmonics.push_back(h);
			}
			double worst = -300, deepest = 0;
			run(d, 44100);
			for (size_t w = 0; w < (size_t)(20 * kFs / kW); w++) {
				Out o = run(d, kW);
				auto m = spectrum(o.l, 0, kW);
				double sidebands = bandEnergy(m, kW, f * 1.5, 22000) / bandEnergy(m, kW, 5, 22000);
				double other = worstOther(m, kW, harmonics, (size_t)(40.0 * kW / kFs) + 12);
				if (sidebands > deepest) {
					deepest = sidebands;
				}
				worst = std::max(worst, other);
			}
			printf("FM 50 %s on %s at %.0f Hz: worst aliasing %.1f dB\n", k.form == DroneSettings::FmForm::SAW ? "saw" : "sine",
			       k.timbre == Timbre::PULSE ? "pulse" : (k.timbre == Timbre::RICH ? "rich" : "sine"), f, worst);
			CHECK(worst < -50, "FM aliasing %.1f dB", worst);
		}
	}

	// 18. Song file: life, FM and the pulse width round trip through the drone's attributes; a file without them loads
	// the defaults, and values out of range come back within it. The Pulse timbre is saved as 4.
	{
		DroneSettings s;
		s.life = 37;
		s.lifeRate = 11;
		s.fm = 44;
		s.fmForm = DroneSettings::FmForm::SAW;
		s.pulseWidth = 17;
		std::vector<std::pair<std::string, int32_t>> file;
		s.writeLifeAttributes([&](const char* name, int32_t value) { file.push_back({name, value}); });
		DroneSettings back;
		bool allKnown = true;
		for (auto& [name, value] : file) {
			allKnown = allKnown && DroneSettings::isLifeAttribute(name.c_str());
			back.readLifeAttribute(name.c_str(), value);
		}
		CHECK(file.size() == 5 && allKnown, "five attributes, all known");
		CHECK(back.life == 37 && back.lifeRate == 11 && back.fm == 44 && back.fmForm == DroneSettings::FmForm::SAW
		          && back.pulseWidth == 17,
		      "round trip");
		CHECK(!back.lifeIsDefault() && DroneSettings{}.lifeIsDefault(), "non-default is written");
		DroneSettings old;
		CHECK(old.life == 0 && old.lifeRate == 25 && old.fm == 0 && old.fmForm == DroneSettings::FmForm::SINE
		          && old.pulseWidth == 30,
		      "a file without them: the defaults");
		DroneSettings wild;
		for (auto name : {"life", "lifeRate", "fm", "fmForm"}) {
			wild.readLifeAttribute(name, 1000);
		}
		wild.readLifeAttribute("pulseWidth", -3);
		CHECK(wild.life == 50 && wild.lifeRate == 50 && wild.fm == 50 && wild.fmForm == DroneSettings::FmForm::SAW
		          && wild.pulseWidth == 5,
		      "clamped to the ranges");
		CHECK(!DroneSettings::isLifeAttribute("volume") && !DroneSettings::isLifeAttribute("tone"), "others aren't");
		CHECK((int)Timbre::PULSE == 4 && DroneSettings::kNumTimbres == 5, "pulse is timbre 4");
		printf("song file: %zu attributes round trip, defaults without them, clamped\n", file.size());
	}

	// 10. Cost per block of 128 samples, on the Deluge's Cortex-A9 when this runs
	// in the emulator (tests/arm; on the PC nothing is counted), and no overflow
	// with all 16 tones at full level
	{
		auto measure = [](const char* label, int tones, Mode mode, Timbre timbre) {
			TestDrone d = makeDrone();
			for (int i = 0; i < tones; i++) {
				d.tones[i] = on(mode, timbre, 5000 + 3000 * i, 800, 0, 50, i * 4 - 32);
			}
			std::vector<StereoSample> buf(kBlock);
			for (int b = 0; b < 60; b++) {
				if (b >= 50) {
					EMU_COUNT_BEGIN(label);
				}
				d.render(std::span<StereoSample>(buf.data(), kBlock), Drone::Context{});
				if (b >= 50) {
					EMU_COUNT_END();
				}
			}
			return d;
		};
		measure("drone 1 tone, sine, 128 samples", 1, Mode::TONE, Timbre::SINE);
		measure("drone 1 tone, binaural, 128 samples", 1, Mode::BINAURAL, Timbre::SINE);
		measure("drone 1 tone, isochronic, 128 samples", 1, Mode::ISOCHRONIC, Timbre::SINE);
		measure("drone 4 tones, binaural, 128 samples", 4, Mode::BINAURAL, Timbre::SINE);
		measure("drone 16 tones, binaural, 128 samples", 16, Mode::BINAURAL, Timbre::RICH);
		TestDrone d = measure("drone 16 tones, monaural, 128 samples", 16, Mode::MONAURAL, Timbre::RICH);
		Out o = run(d, 44100);
		printf("16 tones at full level: peak %.2f of a tone's full level\n", maxAbs(o.l, 0, o.l.size()) / kFullScale);
	}

	printf("%d checks, %d failed\n", checks, failures);
	if (failures == 0) {
		printf("all drone checks passed\n");
	}
	return failures ? 1 : 0;
}
