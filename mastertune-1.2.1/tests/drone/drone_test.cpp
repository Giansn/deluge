// Host test for the drone (v12): runs the firmware's drone DSP on the PC and
// measures its tones.
#include "dsp/drone/drone.h"
#include "emu_count.h"
#include <chrono>
#include <cmath>
#include <complex>
#include <cstdio>
#include <functional>
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
		// 3 ms after the grid, half the edge
		double worstOffset = 0;
		int onsets = 0;
		bool wasOn = true;
		for (size_t i = 88200; i + 60 < o.l.size(); i++) {
			bool isOn = maxAbs(o.l, i - 55, i + 55) > 0.5 * kFullScale;
			if (isOn && !wasOn) {
				double q = 0.3 + i / kFs * 2.0; // Quarter notes from position 0
				double sixteenths = q * 4;
				double offMs = (sixteenths - std::floor(sixteenths)) / 8.0 * 1000; // After the last 16th
				worstOffset = std::max(worstOffset, std::abs(offMs - 3.0));
				onsets++;
			}
			wasOn = isOn;
		}
		printf("synced 1/16 at 120 BPM: %d pulses in 1 s, onsets within %.2f ms of "
		       "the grid\n",
		       onsets, worstOffset);
		CHECK(onsets >= 7 && onsets <= 9, "8 pulses a second");
		CHECK(worstOffset < 1.5, "locked to the grid");
		CHECK(std::abs(Drone::syncedBeatHz(5, 2.f) - 8.f) < 1e-6, "1/16 at 120 BPM is 8 Hz");
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
