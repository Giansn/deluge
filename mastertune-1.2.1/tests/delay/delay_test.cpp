// Host test for the delay: runs the firmware's delay code (dsp/delay) on the PC and measures what happens to the
// repeats, before (1.2.1 / v10) and after the v11 changes.
#include "dsp/delay/delay.h"
#include "emu_count.h"
#include <cmath>
#include <complex>
#include <cstdio>
#include <cstring>
#include <memory>
#include <random>
#include <functional>
#include <vector>

int hostAllocations = 0;
bool hostAllocationsFail = false;
int32_t spareRenderingBuffer[4][SSI_TX_BUFFER_NUM_SAMPLES];
namespace AudioEngine {
bool renderInStereo = true;
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

constexpr int kBlock = 128;
constexpr double kFs = 44100;
constexpr int32_t kNativeRate = 1 << 24; // Rate for the neutral 16384-sample buffer

// Delay time in samples for a rate
static double delaySamples(int32_t rate) {
	return 16384.0 * (1 << 24) / rate;
}

struct Runner {
	Delay delay;
	int32_t feedback = 1 << 29; // Feedback amount as the patcher delivers it
	const char* countLabel = nullptr; // Instructions of process() counted in the emulator (tests/arm) under this
	Runner() {
		delay.pingPong = false;
		delay.analog = false;
		delay.syncLevel = SYNC_LEVEL_NONE;
		delay.countCyclesWithoutChange = 0;
		delay.userRateLastTime = 0;
		delay.sizeLeftUntilBufferSwap = 0;
		delay.postLPFL = delay.postLPFR = 0;
	}
	// Runs one block of mono input through the delay (as the audio engine does), returns the output
	std::vector<StereoSample> block(const int32_t* in, int n, int32_t rate) {
		std::vector<StereoSample> buf(n);
		for (int i = 0; i < n; i++) {
			buf[i].l = buf[i].r = in ? in[i] : 0;
		}
		Delay::State state{};
		state.userDelayRate = rate;
		state.delayFeedbackAmount = feedback;
		delay.setupWorkingState(state, 0, true);
		if (countLabel) {
			EMU_COUNT_BEGIN(countLabel);
		}
		delay.process(std::span<StereoSample>(buf.data(), n), state);
		if (countLabel) {
			EMU_COUNT_END();
		}
		return buf;
	}
	// Runs n samples, returns the left output
	std::vector<double> run(const std::vector<int32_t>& in, int32_t rate) {
		std::vector<double> out;
		for (size_t pos = 0; pos < in.size(); pos += kBlock) {
			int n = std::min<size_t>(kBlock, in.size() - pos);
			auto b = block(in.data() + pos, n, rate);
			for (auto& s : b) {
				out.push_back(s.l / 2147483648.0);
			}
		}
		return out;
	}
	std::vector<double> silence(size_t n, int32_t rate) { return run(std::vector<int32_t>(n, 0), rate); }
};

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

// Level of the first repeat of an impulse at 2, 5, 10 and 15 kHz relative to 500 Hz, in dB: how much treble a repeat
// loses. Sends an impulse, finds the repeat around the delay time and takes its spectrum.
struct Response {
	double at2k, at5k, at10k, at15k;
};
static Response repeatResponse(Runner& r, int32_t rate) {
	std::vector<int32_t> in(kBlock, 0);
	in[0] = 1 << 28;
	std::vector<double> out = r.run(in, rate);
	size_t d = (size_t)delaySamples(rate);
	std::vector<double> more = r.silence(d + 4096, rate);
	out.insert(out.end(), more.begin(), more.end());
	// The repeat: 1024 samples around the delay time
	const size_t kN = 1024;
	size_t start = d - kN / 2 + 64;
	std::vector<std::complex<double>> a(kN);
	for (size_t i = 0; i < kN; i++) {
		a[i] = out[start + i];
	}
	fft(a);
	auto at = [&](double hz) { return std::abs(a[(size_t)std::round(hz * kN / kFs)]); };
	double ref = at(500);
	return {20 * std::log10(at(2000) / ref), 20 * std::log10(at(5000) / ref), 20 * std::log10(at(10000) / ref),
	        20 * std::log10(at(15000) / ref)};
}

// Largest second difference of a steady tone through the delay after 1.5 s, while the rate follows a path (factor of
// the base rate), relative to the largest before: a click shows as a step
static double stepRatio(std::function<double(double)> ratePath, double seconds, int32_t rate) {
	Runner r;
	const double hz = 440;
	std::vector<double> out;
	size_t t = 0;
	size_t n = (size_t)(seconds * kFs);
	for (size_t pos = 0; pos < n; pos += kBlock) {
		std::vector<int32_t> v(kBlock);
		for (int i = 0; i < kBlock; i++) {
			v[i] = (int32_t)(std::sin(2 * M_PI * hz * (t + i) / kFs) * (1 << 27));
		}
		t += kBlock;
		auto b = r.block(v.data(), kBlock, (int32_t)(rate * ratePath(pos / kFs)));
		for (auto& x : b) {
			out.push_back(x.l / 2147483648.0);
		}
	}
	double steady = 0, after = 0;
	size_t split = (size_t)(1.5 * kFs);
	for (size_t i = 30001; i < split; i++) {
		steady = std::max(steady, std::abs(out[i + 1] - 2 * out[i] + out[i - 1]));
	}
	for (size_t i = split; i + 1 < out.size(); i++) {
		after = std::max(after, std::abs(out[i + 1] - 2 * out[i] + out[i - 1]));
	}
	return after / steady;
}

int main() {
	const int32_t rate = (int32_t)(kNativeRate * 1.37); // A free delay time of about 0.27 s

	// 1. Steady delay time: bit-transparent repeats
	{
		Runner r;
		r.silence(40000, rate); // Let it allocate and fill
		Response resp = repeatResponse(r, rate);
		printf("steady time: native %d, repeat at 10 kHz %.2f dB, 15 kHz %.2f dB\n", r.delay.primaryBuffer.isNative(),
		       resp.at10k, resp.at15k);
		CHECK(r.delay.primaryBuffer.isNative(), "steady delay should run native");
		CHECK(std::abs(resp.at10k) < 0.1 && std::abs(resp.at15k) < 0.1, "steady repeats should keep their treble");
	}

	// 2. Knob turned and back: after settling, native again
	{
		Runner r;
		r.silence(40000, rate);
		r.silence(kBlock * 4, (int32_t)(rate * 1.05)); // 12 ms elsewhere
		r.silence(200000, rate);                       // 4.5 s back at the original time
		Response resp = repeatResponse(r, rate);
		printf("time moved and back: native %d, repeat at 10 kHz %.2f dB, 15 kHz %.2f dB\n",
		       r.delay.primaryBuffer.isNative(), resp.at10k, resp.at15k);
		CHECK(r.delay.primaryBuffer.isNative(), "delay should return to native after the time settles");
		CHECK(resp.at10k > -0.5, "repeats should keep their treble after the time settles (%.2f dB)", resp.at10k);
	}

	// 3. Time doubled speed (half the delay): after settling, native again
	{
		Runner r;
		r.silence(40000, rate);
		int32_t fast = rate * 2 + 12345;
		r.silence(300000, fast);
		Response resp = repeatResponse(r, fast);
		printf("time halved: native %d, repeat at 10 kHz %.2f dB, 15 kHz %.2f dB\n", r.delay.primaryBuffer.isNative(),
		       resp.at10k, resp.at15k);
		CHECK(r.delay.primaryBuffer.isNative(), "delay should return to native after halving the time");
	}

	// 4. Long delay (over 2 s): stays resampled, but must not keep reallocating
	{
		Runner r;
		int32_t slow = kNativeRate / 7; // About 2.6 s
		r.silence(200000, slow);
		int before = hostAllocations;
		r.silence(400000, slow);
		printf("long delay: native %d, allocations while holding the time %d\n", r.delay.primaryBuffer.isNative(),
		       hostAllocations - before);
		CHECK(hostAllocations - before == 0, "no reallocation while a long delay holds its time");
	}

	// 5. No click when the delay goes back to native: a steady tone through the delay while the time moves and comes
	// back. The second difference of the output shows any step; compare it with the tone's own.
	{
		Runner r;
		const double hz = 440;
		auto tone = [&](size_t from, size_t n) {
			std::vector<int32_t> v(n);
			for (size_t i = 0; i < n; i++) {
				v[i] = (int32_t)(std::sin(2 * M_PI * hz * (from + i) / kFs) * (1 << 27));
			}
			return v;
		};
		size_t t = 0;
		std::vector<double> out;
		auto play = [&](size_t n, int32_t rt) {
			auto o = r.run(tone(t, n), rt);
			t += n;
			out.insert(out.end(), o.begin(), o.end());
		};
		play(60000, rate);                        // Settled
		size_t steadyEnd = out.size();
		play(kBlock * 4, (int32_t)(rate * 1.05)); // Knob moved
		play(120000, rate);                       // And back: resampling, then a new buffer and the swap
		CHECK(r.delay.primaryBuffer.isNative(), "native again after the swap");
		auto maxD2 = [&](size_t from, size_t to) {
			double m = 0;
			for (size_t i = from + 1; i + 1 < to; i++) {
				m = std::max(m, std::abs(out[i + 1] - 2 * out[i] + out[i - 1]));
			}
			return m;
		};
		double steady = maxD2(30000, steadyEnd);
		double after = maxD2(steadyEnd + kBlock * 4 + 2000, out.size());
		printf("tone through the swap back to native: largest step %.2f times the steady one\n", after / steady);
		CHECK(after < steady * 1.5, "a click where the delay goes back to native (%.2f x)", after / steady);
	}

	// 6. Modulated delay time: a tone through the delay while an LFO moves the time (0.5 Hz, +-2%). Stepping the speed
	// once per 128-sample block adds a buzz at multiples of 344 Hz; gliding doesn't. Measures the level around the
	// tone +- 344 Hz relative to the tone, on the first repeat.
	{
		Runner r;
		r.feedback = 1 << 29;
		const double hz = 2000;
		const size_t n = (size_t)(6 * kFs);
		std::vector<double> out;
		for (size_t pos = 0; pos < n; pos += kBlock) {
			std::vector<int32_t> in(kBlock);
			for (int i = 0; i < kBlock; i++) {
				in[i] = (int32_t)(std::sin(2 * M_PI * hz * (pos + i) / kFs) * (1 << 26));
			}
			// Around 1.3 times the buffer's own speed, so it keeps resampling in the one buffer (between 1x and 2x)
			int32_t rt = (int32_t)(rate * (1.3 + 0.02 * std::sin(2 * M_PI * 0.5 * pos / kFs)));
			if (pos < kFs) {
				rt = rate; // First settle at the plain rate
			}
			auto b = r.block(in.data(), kBlock, rt);
			for (auto& x : b) {
				out.push_back(x.l / 2147483648.0);
			}
		}
		// The output holds the dry tone plus the modulated repeats; take the spectrum of the last 3 s
		const size_t kN = 131072;
		std::vector<std::complex<double>> a(kN);
		size_t from = out.size() - kN;
		for (size_t i = 0; i < kN; i++) {
			a[i] = out[from + i] * (0.5 - 0.5 * std::cos(2 * M_PI * i / (kN - 1)));
		}
		fft(a);
		auto band = [&](double f, double width) {
			double e = 0;
			for (size_t k = (size_t)((f - width) * kN / kFs); k <= (size_t)((f + width) * kN / kFs); k++) {
				e += std::norm(a[k]);
			}
			return e;
		};
		double toneEnergy = band(hz, 60);
		double spur = band(hz - kFs / kBlock, 60) + band(hz + kFs / kBlock, 60);
		double level = 10 * std::log10(spur / toneEnergy);
		printf("modulated time: buzz at +-344 Hz around the tone %.1f dB below it (buffers made: %d)\n", -level,
		       hostAllocations);
		CHECK(level < -60, "stepping buzz at %.1f dB", level);
	}

	// 7. The first repeat's treble while the time is being modulated (so the delay keeps resampling): a tiny, slow
	// wobble, and an impulse through it
	{
		Runner r;
		auto wobble = [&](size_t pos) { return (int32_t)(rate * (1.0 + 0.001 * std::sin(2 * M_PI * 0.3 * pos / kFs))); };
		size_t pos = 0;
		auto runFor = [&](const std::vector<int32_t>& in) {
			std::vector<double> out;
			for (size_t p = 0; p < in.size(); p += kBlock) {
				int n = std::min<size_t>(kBlock, in.size() - p);
				auto b = r.block(in.data() + p, n, wobble(pos));
				pos += n;
				for (auto& x : b) {
					out.push_back(x.l / 2147483648.0);
				}
			}
			return out;
		};
		runFor(std::vector<int32_t>(60000, 0));
		std::vector<int32_t> in(40000, 0);
		in[0] = 1 << 28;
		std::vector<double> out = runFor(in);
		size_t d = (size_t)delaySamples(rate);
		const size_t kN = 1024;
		std::vector<std::complex<double>> a(kN);
		for (size_t i = 0; i < kN; i++) {
			a[i] = out[d - kN / 2 + 64 + i];
		}
		fft(a);
		auto at = [&](double hz) { return std::abs(a[(size_t)std::round(hz * kN / kFs)]); };
		double r10 = 20 * std::log10(at(10000) / at(500)), r15 = 20 * std::log10(at(15000) / at(500));
		printf("modulated time: repeat at 10 kHz %.2f dB, 15 kHz %.2f dB (resampling %d)\n", r10, r15,
		       !r.delay.primaryBuffer.isNative());
		CHECK(!r.delay.primaryBuffer.isNative(), "should be resampling while modulated");
		CHECK(r10 > -1.5, "modulated repeats should keep their treble (%.2f dB at 10 kHz)", r10);
	}

	// 8. Changing the time in all directions: no step larger than the pitch change itself makes
	{
		struct Case {
			const char* name;
			std::function<double(double)> path;
			double limit;
		};
		std::vector<Case> cases = {
		    // 1.2.1 had 1.90, 1.69, 2.46, 3.77, 4.05, 1.72 and 1.69 here. A larger jump makes a new buffer at once, which
		    // has to glide along with the one it replaces, or the time jumps when it takes over (1.82, 5.75 and 1.45 in
		    // v11 before its review).
		    {"5% longer", [](double s) { return s < 1.5 ? 1.0 : 0.95; }, 0.5},
		    {"40% longer", [](double s) { return s < 1.5 ? 1.0 : 0.7; }, 0.5},
		    {"70% longer", [](double s) { return s < 1.5 ? 1.0 : 0.6; }, 0.5},
		    {"5% shorter", [](double s) { return s < 1.5 ? 1.0 : 1.05; }, 0.5},
		    {"less than half", [](double s) { return s < 1.5 ? 1.0 : 2.2; }, 0.5},
		    {"sweep across the buffer's speed",
		     [](double s) { return s < 1.5 ? 1.0 : (s < 1.6 ? 1.05 : std::max(0.95, 1.05 - (s - 1.6) * 0.2)); }, 0.5},
		    {"LFO +-3% at 1 Hz", [](double s) { return s < 1.5 ? 1.0 : 1.0 + 0.03 * std::sin(2 * M_PI * (s - 1.5)); },
		     0.5},
		};
		for (auto& c : cases) {
			double ratio = stepRatio(c.path, 5, rate);
			printf("time change, %s: largest step %.2f times the steady one\n", c.name, ratio);
			CHECK(ratio < c.limit, "%s: step %.2f", c.name, ratio);
		}
	}

#ifndef NO_DELAY_FILTERS // For measuring older versions, which don't have them
	// 9. Tone filters in the feedback: the first repeat through the high cut at 25 (3.2 kHz) and the low cut at 25
	// (190 Hz), against one-pole filters at those frequencies
	{
		Runner r;
		r.delay.highCut = 25;
		r.delay.lowCut = 25;
		r.silence(40000, rate);
		Response resp = repeatResponse(r, rate);
		// One-pole low pass at 3.16 kHz: -10.4 dB at 10 kHz relative to 500 Hz (-0.1 dB); the low cut at 190 Hz takes
		// 0.6 dB off 500 Hz
		printf("tone filters at 25/25: repeat at 2 kHz %.2f dB, 10 kHz %.2f dB\n", resp.at2k, resp.at10k);
		CHECK(std::abs(resp.at10k + 9.8) < 1.0, "high cut at 25: %.2f dB at 10 kHz", resp.at10k);
		Runner off;
		off.silence(40000, rate);
		Response flat = repeatResponse(off, rate);
		CHECK(std::abs(flat.at10k) < 0.1, "filters off: flat repeats");
	}
#endif

	// 10. Very long delay, where the buffer runs below half speed: 1.2.1 wrote there with triangles, which lost half
	// the bandwidth (-0.95 dB at 2 kHz, -6.89 dB at 5 kHz here). The buffer itself stops at 0.38 x 22.05 kHz = 8.5 kHz,
	// and the cubic kernels for writing and reading take 1.5 dB each at 5 kHz.
	{
		Runner r;
		int32_t slowest = kNativeRate / 14; // About 5.2 s, the buffer at 0.38x
		r.silence(240000, slowest);
		Response resp = repeatResponse(r, slowest);
		printf("5 s delay: repeat at 2 kHz %.2f dB, 5 kHz %.2f dB\n", resp.at2k, resp.at5k);
		CHECK(resp.at5k > -4.f, "5 s delay: repeats should keep more of 5 kHz (%.2f dB)", resp.at5k);
	}

#ifndef NO_DELAY_FILTERS
	// 11. The low cut in the digital mode at full feedback: the repeats stay at the clipping level, which leaves the
	// headroom for adding them to the sound, give or take the DC blocker after it (1.14 without the low cut, as in
	// 1.2.1). With the low cut after the clipping, its overshoot reached 1.84, and wrapped around.
	{
		Runner r;
		r.feedback = 2147483647;
		r.delay.lowCut = 50;
		int64_t peak = 0;
		for (size_t pos = 0; pos < 3 * 44100; pos += kBlock) {
			std::vector<int32_t> in(kBlock);
			for (int i = 0; i < kBlock; i++) {
				size_t t = pos + i;
				double saw = (double)((t * 55) % 44100) / 44100.0 * 2 - 1; // 55 Hz
				in[i] = (t < 44100) ? (int32_t)(saw * (1 << 29)) : 0;
			}
			auto out = r.block(in.data(), kBlock, rate);
			for (int i = 0; i < kBlock; i++) {
				peak = std::max(peak, std::abs((int64_t)out[i].l - in[i]));
			}
		}
		printf("low cut at full feedback: repeats peak at %.2f of the clipping level\n", peak / 1073741824.0);
		CHECK(peak < (int64_t)(1.15 * 1073741824.0), "repeats beyond the clipping level (%.2f)", peak / 1073741824.0);
	}
#endif

#ifndef NO_DELAY_FILTERS
	// 12. The low cut switched off while the high cut stays on, and the high cut later: no step either time. (Until
	// the review, the low cut's state froze when it went off, and came back all at once when the high cut went off.)
	{
		Runner r;
		r.delay.highCut = 25;
		r.delay.lowCut = 40;
		std::vector<double> out;
		size_t t = 0;
		auto play = [&](size_t n) {
			for (size_t pos = 0; pos < n; pos += kBlock) {
				std::vector<int32_t> in(kBlock);
				for (int i = 0; i < kBlock; i++) {
					in[i] = (int32_t)(std::sin(2 * M_PI * 60 * (double)(t + i) / kFs) * (1 << 28));
				}
				t += kBlock;
				auto b = r.block(in.data(), kBlock, rate);
				for (auto& x : b) {
					out.push_back(x.l / 2147483648.0);
				}
			}
		};
		play(66150);
		r.delay.lowCut = 0;
		play(44100);
		size_t highCutOff = out.size();
		r.delay.highCut = Delay::kHighCutOff;
		play(44100);
		auto maxD2 = [&](size_t from, size_t to) {
			double m = 0;
			for (size_t i = from + 1; i + 1 < to; i++) {
				m = std::max(m, std::abs(out[i + 1] - 2 * out[i] + out[i - 1]));
			}
			return m;
		};
		double steady = maxD2(30000, 66150);
		double atHighCutOff = maxD2(highCutOff - 256, highCutOff + 256);
		printf("low cut off, then high cut off: step when the high cut goes %.2f times the steady one\n",
		       atHighCutOff / steady);
		CHECK(atHighCutOff / steady < 1.5, "switching the high cut off after the low cut steps (%.2f)",
		      atHighCutOff / steady);
		CHECK(std::abs(r.delay.lowCutStateL) < 1.f, "the low cut's state has gone");
	}
#endif

	// Cost of the delay per block of 128 samples, on the Deluge's Cortex-A9 when this runs in the emulator (tests/arm;
	// on the PC nothing is counted): steady (native), with the time modulated (resampling), with the filters on, and
	// the analog mode
	{
		auto measure = [&](const char* label, bool modulated, bool filters, bool analog) {
			Runner r;
			r.delay.analog = analog;
#ifndef NO_DELAY_FILTERS
			if (filters) {
				r.delay.highCut = 25;
				r.delay.lowCut = 25;
			}
#endif
			std::vector<int32_t> in(kBlock);
			for (int i = 0; i < kBlock; i++) {
				in[i] = (int32_t)(std::sin(2 * M_PI * 440 * i / kFs) * (1 << 27));
			}
			for (int b = 0; b < 400; b++) {
				int32_t rt = modulated ? (int32_t)(rate * (1.0 + 0.03 * std::sin(b * 0.05))) : rate;
				r.countLabel = (b >= 380) ? label : nullptr;
				r.block(in.data(), kBlock, rt);
			}
		};
		measure("delay steady, 128 samples", false, false, false);
		measure("delay modulated (resampling), 128 samples", true, false, false);
		measure("delay modulated, filters on, 128 samples", true, true, false);
		measure("delay modulated, analog, filters on, 128 samples", true, true, true);
	}

	printf("%d checks, %d failed\n", checks, failures);
	if (failures == 0) {
		printf("all delay checks passed\n");
	}
	return failures ? 1 : 0;
}
