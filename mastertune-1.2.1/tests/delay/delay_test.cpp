// Host test for the delay: runs the firmware's delay code (dsp/delay) on the PC and measures what happens to the
// repeats, before (1.2.1 / v10) and after the v11 changes, and (v14) the fade mode for changes of the delay time, the
// first repeat, the end of the repeats, long delays and ping-pong.
#include "dsp/delay/delay.h"
#include "emu_count.h"
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdio>
#include <cstring>
#include <memory>
#include <random>
#include <string>
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

// v14: what a change of the delay time does. Trees before v14 only have the tape mode: build with
// -DNO_DELAY_TIME_CHANGE (as with -DNO_DELAY_FILTERS before v11) to measure them.
static void setFade(Delay& d, bool fade) {
#ifndef NO_DELAY_TIME_CHANGE
	d.timeChange = fade ? Delay::TimeChange::FADE : Delay::TimeChange::TAPE;
#endif
}

struct Runner {
	Delay delay;
	int32_t feedback = 1 << 29; // Feedback amount as the patcher delivers it
	bool soundComingIn = true;
	uint32_t tickInverse = 0; // For a synced delay: what the playback handler gives as the time per tick's inverse
	const char* countLabel = nullptr; // Instructions of process() counted in the emulator (tests/arm) under this
	Runner(bool fade = false) {
		delay.pingPong = false;
		delay.analog = false;
		delay.syncLevel = SYNC_LEVEL_NONE;
		delay.countCyclesWithoutChange = 0;
		delay.userRateLastTime = 0;
		delay.sizeLeftUntilBufferSwap = 0;
		delay.postLPFL = delay.postLPFR = 0;
		setFade(delay, fade); // The tape mode unless asked: sections 1 to 12 measure it, as before v14
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
		delay.setupWorkingState(state, tickInverse, soundComingIn);
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
// the base rate), relative to the tone's own (v14: rather than to the largest before, which held the steps of the
// repeats of the tone's start, and before v14 the step the first repeat started with): a click shows as a step. The
// repeats, at feedback 0.5, bent up in pitch by up to p in the tape mode, add up to (1 + p^2) times that without one.
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
	double after = 0;
	size_t split = (size_t)(1.5 * kFs);
	for (size_t i = split; i + 1 < out.size(); i++) {
		after = std::max(after, std::abs(out[i + 1] - 2 * out[i] + out[i - 1]));
	}
	double w = 2 * M_PI * hz / kFs;
	return after / ((1.0 / 16) * w * w);
}

// v14 helpers --------------------------------------------------------------------------------------------------------

// A mono signal through the delay, with the rate, feedback and whether sound is coming in following paths; returns what
// the delay adds (the output less the input), left and right
struct Wet {
	std::vector<double> l, r;
};
static Wet playWet(Runner& r, size_t n, std::function<double(size_t)> in, std::function<int32_t(size_t)> rate,
                   std::function<void(size_t)> before = nullptr) {
	Wet w;
	std::vector<int32_t> v(kBlock);
	for (size_t pos = 0; pos < n; pos += kBlock) {
		if (before) {
			before(pos);
		}
		for (int i = 0; i < kBlock; i++) {
			v[i] = (int32_t)(in(pos + i) * 2147483648.0);
		}
		auto b = r.block(v.data(), kBlock, rate(pos));
		for (int i = 0; i < kBlock; i++) {
			w.l.push_back((b[i].l - (double)v[i]) / 2147483648.0);
			w.r.push_back((b[i].r - (double)v[i]) / 2147483648.0);
		}
	}
	return w;
}

// A 440 Hz tone at 0.25, fading in over 20 ms with a raised cosine, so its start makes no treble of its own
static double tone440(size_t t) {
	double fadeIn = (t < 882) ? 0.5 - 0.5 * std::cos(M_PI * t / 882.0) : 1.0;
	return 0.25 * fadeIn * std::sin(2 * M_PI * 440 * t / kFs);
}

// The loudest treble (above 4 kHz) in frames of 1024 samples (hop 256) over a stretch, in dB against a 0.25 sine in
// such a frame: what a click or a step leaves. A 440 Hz tone leaves nothing there (below -130 dB).
static double trebleLevel(const std::vector<double>& x, size_t from, size_t to) {
	const size_t kN = 1024;
	double worst = 0;
	for (size_t s = from; s + kN <= std::min(to, x.size()); s += 256) {
		std::vector<std::complex<double>> a(kN);
		for (size_t i = 0; i < kN; i++) {
			a[i] = x[s + i] * (0.5 - 0.5 * std::cos(2 * M_PI * i / (kN - 1)));
		}
		fft(a);
		double e = 0;
		for (size_t k = (size_t)(4000.0 * kN / kFs); k < kN / 2; k++) {
			e += std::norm(a[k]);
		}
		worst = std::max(worst, e);
	}
	return 10 * std::log10(worst / std::pow(0.25 * kN / 4, 2) + 1e-30);
}

// The pitch of the repeats of a 440 Hz burst: finds each repeat (where the signal is above 1e-4; its start where it
// first reaches a tenth of its peak), and in the middle of each longer than 40 ms, fits a line to the phase of the
// signal against 440 Hz (Hann windows of 512 samples every 64). Returns the largest deviation in cents over the repeats
// starting from `from`, and how many there were. Repeats that overlap each other (the fit leaves more than 0.01 rad) are
// left out.
struct Pitch {
	double worstCents = 0;
	int repeats = 0;
	std::vector<size_t> starts;
};
static Pitch repeatPitch(const std::vector<double>& x, size_t from) {
	Pitch p;
	const double w = 2 * M_PI * 440 / kFs;
	const size_t kW = 512;
	size_t i = from;
	while (i < x.size()) {
		while (i < x.size() && std::abs(x[i]) < 1e-4) {
			i++;
		}
		size_t start = i;
		size_t quiet = 0;
		while (i < x.size() && quiet < 200) {
			quiet = (std::abs(x[i]) < 1e-4) ? quiet + 1 : 0;
			i++;
		}
		size_t end = i - quiet;
		if (end <= start + 1764) {
			continue;
		}
		double peak = 0;
		for (size_t k = start; k < end; k++) {
			peak = std::max(peak, std::abs(x[k]));
		}
		size_t onset = start;
		while (std::abs(x[onset]) < 0.1 * peak) {
			onset++;
		}
		p.starts.push_back(onset);
		std::vector<double> ts, phases;
		for (size_t c = start + 300; c + kW <= end - 300; c += 64) {
			std::complex<double> z = 0;
			for (size_t k = 0; k < kW; k++) {
				z += x[c + k] * (0.5 - 0.5 * std::cos(2 * M_PI * k / kW)) * std::polar(1.0, -w * (double)(c + k));
			}
			double ph = std::arg(z);
			if (!phases.empty()) {
				while (ph - phases.back() > M_PI) {
					ph -= 2 * M_PI;
				}
				while (ph - phases.back() < -M_PI) {
					ph += 2 * M_PI;
				}
			}
			ts.push_back(c + kW / 2.0);
			phases.push_back(ph);
		}
		size_t n = ts.size();
		if (n < 3) {
			continue;
		}
		double mt = 0, mp = 0;
		for (size_t k = 0; k < n; k++) {
			mt += ts[k] / n;
			mp += phases[k] / n;
		}
		double num = 0, den = 0;
		for (size_t k = 0; k < n; k++) {
			num += (ts[k] - mt) * (phases[k] - mp);
			den += (ts[k] - mt) * (ts[k] - mt);
		}
		double slope = num / den;
		double residual = 0;
		for (size_t k = 0; k < n; k++) {
			residual = std::max(residual, std::abs(phases[k] - mp - slope * (ts[k] - mt)));
		}
		if (residual > 0.01) {
			continue;
		}
		double hz = 440 + slope * kFs / (2 * M_PI);
		p.worstCents = std::max(p.worstCents, std::abs(1200 * std::log2(hz / 440)));
		p.repeats++;
	}
	return p;
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

	// 4. Long delay (longer than the largest buffer, 2 s before v14, 4 s since): stays resampled, but must not keep
	// reallocating
	{
		Runner r;
		int32_t slow = kNativeRate / 14; // About 5.2 s
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
		// Limits 1.1 x (1 + p^2), p the largest pitch factor (see stepRatio()). Against the largest step before, as up to
		// v13, 1.2.1 had 1.90, 1.69, 2.46, 3.77, 4.05, 1.72 and 1.69 here, v11 to v13 0.08 to 0.14. A larger jump makes a
		// new buffer at once, which has to glide along with the one it replaces, or the time jumps when it takes over
		// (1.82, 5.75 and 1.45 in v11 before its review).
		std::vector<Case> cases = {
		    {"5% longer", [](double s) { return s < 1.5 ? 1.0 : 0.95; }, 2.2},
		    {"40% longer", [](double s) { return s < 1.5 ? 1.0 : 0.7; }, 2.2},
		    {"70% longer", [](double s) { return s < 1.5 ? 1.0 : 0.6; }, 2.2},
		    {"5% shorter", [](double s) { return s < 1.5 ? 1.0 : 1.05; }, 2.31},
		    {"less than half", [](double s) { return s < 1.5 ? 1.0 : 2.2; }, 6.42},
		    {"sweep across the buffer's speed",
		     [](double s) { return s < 1.5 ? 1.0 : (s < 1.6 ? 1.05 : std::max(0.95, 1.05 - (s - 1.6) * 0.2)); }, 2.31},
		    {"LFO +-3% at 1 Hz", [](double s) { return s < 1.5 ? 1.0 : 1.0 + 0.03 * std::sin(2 * M_PI * (s - 1.5)); },
		     2.33},
		};
		for (auto& c : cases) {
			double ratio = stepRatio(c.path, 5, rate);
			printf("tape mode, time change, %s: largest step %.2f times the tone's own (limit %.2f)\n", c.name, ratio,
			       c.limit);
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
		// Against the tone (0.125): the filters' own phase shift, dropped at once, leaves a small step when the high cut
		// goes from 3.2 kHz straight to off (the menu goes there from 18.6 kHz). The low cut's state held back, as
		// before the review, stepped by most of the tone.
		double atHighCutOff = maxD2(highCutOff - 256, highCutOff + 256) / 0.125;
		printf("low cut off, then high cut off: step when the high cut goes %.1e of the tone\n", atHighCutOff);
		CHECK(atHighCutOff < 3e-3, "switching the high cut off after the low cut steps (%.1e)", atHighCutOff);
		CHECK(std::abs(r.delay.lowCutStateL) < 1.f, "the low cut's state has gone");
	}
#endif

	// 13. (v14) The first repeat, when the delay starts: an exact copy of the sound, from its first sample. Before v14,
	// the first buffer was only read once what went into it had come round, at the end of the block that happened in,
	// by when up to a block of the first repeat had gone: it started with a step (0.02 here, a click).
	for (bool fade : {false, true}) {
		Runner r(fade);
		// Starting at full level, as a drum hit does
		auto hit = [](size_t t) { return 0.25 * std::cos(2 * M_PI * 440 * t / kFs); };
		Wet w = playWet(r, 30000, hit, [&](size_t) { return rate; });
		size_t d = (size_t)delaySamples(rate);
		double worst = 0; // Against the input, a sample later by the delay time and at the feedback's level
		for (size_t t = 0; t < 600; t++) {
			double want = hit(t) * r.feedback / 1073741824.0;
			double best = 1;
			for (size_t dd = d - 2; dd <= d + 2; dd++) {
				best = std::min(best, std::abs(w.l[t + dd] - want));
			}
			worst = std::max(worst, best);
		}
		printf("%s mode: first repeat against the sound (0.125), largest difference over its first 600 samples %.1e\n",
		       fade ? "fade" : "tape", worst);
		// The DC blocker on the delay's output makes up the rest (0.125 before v14: the first 74 samples were missing)
		CHECK(worst < 1e-2, "the first repeat doesn't start as the sound did (%.1e)", worst);
	}

	// 14. (v14) The end of the repeats: when no sound has come in for long enough, the delay gives up
	// (repeatsUntilAbandon) and frees its buffer. 1.2.1 cut the repeats off there, 42 to 60 dB under the sound, a tick in
	// the quiet; the last time round now fades out. And sound coming in again during it brings the repeats back.
	{
		Runner r;
		r.feedback = (int32_t)(0.2 * (1 << 30)); // The worst case measured: cut at -42.7 dB
		Wet w = playWet(r, 44100 * 4, [](size_t t) { return t < 44100 ? tone440(t) : 0; },
		                [&](size_t) { return rate; }, [&](size_t pos) { r.soundComingIn = pos < 44100; });
		size_t last = 0;
		for (size_t i = 0; i < w.l.size(); i++) {
			if (std::abs(w.l[i]) > 1e-9) {
				last = i;
			}
		}
		double stepDb = 20 * std::log10(std::abs(w.l[last]) / 0.25 + 1e-30);
		printf("end of the repeats: after %.2f s, last sample %.1f dB under the sound, buffer freed %d\n", last / kFs,
		       -stepDb, !r.delay.isActive());
		CHECK(stepDb < -90, "the repeats stop with a step (%.1f dB)", stepDb);
		CHECK(!r.delay.isActive(), "the delay should have freed its buffer");

		// Sound again in the last time round (the 3rd after the sound stopped at feedback 0.2), as it fades: the delay
		// goes on, and its repeats end up as if there had been no pause
		double peaks[2];
		size_t restart = 44100 + (size_t)(1.6 * delaySamples(rate));
		for (int pause = 0; pause < 2; pause++) {
			Runner again;
			again.feedback = (int32_t)(0.2 * (1 << 30));
			auto on = [&](size_t t) { return !pause || t < 44100 || t >= restart; };
			Wet w2 = playWet(again, 44100 * 4, [&](size_t t) { return on(t) ? tone440(t) : 0; },
			                 [&](size_t) { return rate; }, [&](size_t pos) { again.soundComingIn = on(pos); });
			peaks[pause] = 0;
			for (size_t i = w2.l.size() - (size_t)delaySamples(rate); i < w2.l.size(); i++) {
				peaks[pause] = std::max(peaks[pause], std::abs(w2.l[i]));
			}
			CHECK(again.delay.isActive(), "the delay should go on");
		}
		printf("sound again in the last time round: repeats at %.4f, without the pause %.4f\n", peaks[1], peaks[0]);
		CHECK(std::abs(peaks[1] / peaks[0] - 1) < 0.001, "the repeats should come back (%.4f)", peaks[1]);
	}

	// 15. (v14) Long delays: a 3 s delay now has a buffer of its own speed (up to 4 s, was 2 s), and its repeats are
	// exact. Beyond, the buffer runs slower than the sound, and treble folds back (measured: a 15 kHz tone through a
	// 2.5 s delay came back with a tone at 20.3 kHz only 7 dB under it before v14; at 5 s now the same).
	for (double secs : {3.0, 5.0}) {
		Runner r(true);
		r.feedback = 1 << 28;
		int32_t rt = (int32_t)(16384.0 * (1 << 24) / (secs * kFs));
		size_t d = (size_t)delaySamples(rt);
		Wet w = playWet(r, d + 50000, [](size_t t) { return t < 30000 ? 0.25 * std::sin(2 * M_PI * 15000 * t / kFs) : 0; },
		                [&](size_t) { return rt; });
		const size_t kN = 16384;
		std::vector<std::complex<double>> a(kN);
		for (size_t i = 0; i < kN; i++) {
			a[i] = w.l[d + 5000 + i] * (0.5 - 0.5 * std::cos(2 * M_PI * i / (kN - 1)));
		}
		fft(a);
		size_t kf = (size_t)std::round(15000.0 * kN / kFs);
		double atTone = std::abs(a[kf]), other = 0;
		for (size_t k = 10; k < kN / 2; k++) {
			if (k + 80 < kf || k > kf + 80) { // Past the window's leakage
				other = std::max(other, std::abs(a[k]));
			}
		}
		double db = 20 * std::log10(other / atTone);
		printf("%.0f s delay, 15 kHz: largest other tone %.1f dB against it (native %d)\n", secs, db,
		       r.delay.primaryBuffer.isNative());
		if (secs == 3.0) {
			CHECK(r.delay.primaryBuffer.isNative() && db < -100, "a 3 s delay should repeat exactly (%.1f dB)", db);
		}
	}

	// 16. (v14) Ping-pong: the repeats alternate between right and left (the sound goes in on the right), in both
	// modes, and on through a change of the time
	for (bool fade : {false, true}) {
		Runner r(fade);
		r.delay.pingPong = true;
		int32_t newRate = (int32_t)(rate * 1.25);
		size_t change = 100 + (size_t)(2.5 * delaySamples(rate)); // Between the 2nd and 3rd repeat
		Wet w = playWet(r, 44100 * 3, [](size_t t) { return (t >= 100 && t < 2000) ? tone440(t - 100) * 2 : 0; },
		                [&](size_t pos) { return pos < change ? rate : newRate; });
		Pitch pl = repeatPitch(w.l, 2000), pr = repeatPitch(w.r, 2000);
		// Merge the starts: each repeat should be on one side only
		std::vector<std::pair<size_t, char>> starts;
		for (size_t s0 : pl.starts) {
			starts.push_back({s0, 'L'});
		}
		for (size_t s0 : pr.starts) {
			starts.push_back({s0, 'R'});
		}
		std::sort(starts.begin(), starts.end());
		std::string sides;
		for (auto& [at, side] : starts) {
			sides += side;
		}
		bool alternates = sides.size() >= 6;
		for (size_t k = 0; k < sides.size(); k++) {
			alternates = alternates && sides[k] == ((k % 2) ? 'L' : 'R');
		}
		printf("%s mode, ping-pong through a change of time: repeats %s\n", fade ? "fade" : "tape", sides.c_str());
		CHECK(alternates, "ping-pong repeats should alternate R, L, R, ... (%s)", sides.c_str());
	}

#ifndef NO_DELAY_TIME_CHANGE
	{
		// 17. (v14) Fade mode: the repeats keep their pitch through a change of the time, and the new time takes over.
		// A 60 ms burst at 440 Hz, repeating at feedback 0.85, and the time changed between two repeats or while one
		// goes in (so it is split between the old and the new buffer). The tape mode for comparison, where they bend.
		struct Case {
			const char* name;
			double factor;    // New rate against the old (above 1: shorter)
			double changeAt;  // In delay times from the start of the burst
			bool analog, pingPong, filters;
		};
		std::vector<Case> cases = {
		    {"25% shorter, between repeats", 1.25, 2.5, false, false, false},
		    {"25% longer, while a repeat goes in", 0.8, 3.02, false, false, false},
		    {"less than half", 2.2, 2.5, false, false, false},
		    {"more than double", 0.45, 2.5, false, false, false},
		    {"25% shorter, analog, filters, ping-pong", 1.25, 2.5, true, true, true},
		};
		for (auto& c : cases) {
			for (bool fade : {false, true}) {
				Runner r(fade);
				r.feedback = (int32_t)(0.85 * (1 << 30));
				r.delay.analog = c.analog;
				r.delay.pingPong = c.pingPong;
				if (c.filters) {
					r.delay.highCut = 45; // Gentle, for the pitch fit
					r.delay.lowCut = 5;
				}
				int32_t newRate = (int32_t)(rate * c.factor);
				size_t change = (size_t)(c.changeAt * delaySamples(rate));
				auto burst = [](size_t t) {
					if (t >= 2646) {
						return 0.0;
					}
					double edge = std::min(1.0, std::min(t, 2646 - t) / 220.0);
					return 0.25 * (0.5 - 0.5 * std::cos(M_PI * edge)) * std::sin(2 * M_PI * 440 * t / kFs);
				};
				Wet w = playWet(r, 44100 * 6, burst, [&](size_t pos) { return pos < change ? rate : newRate; });
				std::vector<double> sum(w.l.size());
				for (size_t i = 0; i < sum.size(); i++) {
					sum[i] = w.l[i] + w.r[i];
				}
				Pitch p = repeatPitch(sum, change);
				// The spacing of the repeats a second after the change: the new time
				double spacing = 0;
				for (size_t k = 0; k + 2 < p.starts.size(); k++) {
					if (p.starts[k] > change + 44100) {
						spacing = (double)(p.starts[k + 2] - p.starts[k]) / 2;
						break;
					}
				}
				double want = delaySamples(newRate);
				printf("%s mode, time %s: repeats after the change %d, pitch off by up to %.3f cents, then %.1f samples "
				       "apart (new time %.1f)\n",
				       fade ? "fade" : "tape", c.name, p.repeats, p.worstCents, spacing, want);
				if (fade) {
					CHECK(p.repeats >= 3 && p.worstCents < 1, "%s: the repeats bend by %.3f cents", c.name, p.worstCents);
					// The analog mode's impulse response adds a little to each time round
					double tolerance = c.analog ? 0.01 * want : 2;
					CHECK(std::abs(spacing - want) < tolerance, "%s: repeats %.1f samples apart, not %.1f", c.name,
					      spacing, want);
				}
				else if (c.factor == 1.25 || c.factor == 0.8) { // 386 cents; the fit is at 440 Hz, and misses more
					CHECK(p.worstCents > 380, "%s: the tape mode should bend the repeats (%.1f cents)", c.name,
					      p.worstCents);
				}
			}
		}

		// 18. (v14) Fade mode, a synced delay (an 8th) when the tempo goes from 120 to 100 BPM: pitch kept, new time.
		{
			Runner r(true);
			r.feedback = (int32_t)(0.85 * (1 << 30));
			r.delay.syncLevel = SYNC_LEVEL_8TH;
			// setupWorkingState: rate x inverse / 2^32, shifted up by the sync level + 5. An 8th at 120 BPM is 0.25 s.
			auto inverseFor = [&](double bpm) {
				double wantRate = 16384.0 * (1 << 24) / (kFs * 30 / bpm);
				return (uint32_t)(wantRate / (1 << (SYNC_LEVEL_8TH + 5)) / kNativeRate * 4294967296.0);
			};
			size_t change = 30000;
			auto burst = [](size_t t) { return t < 2646 ? tone440(t) * (t < 2400 ? 1.0 : (2646 - t) / 246.0) : 0; };
			Wet w = playWet(r, 44100 * 5, burst, [&](size_t) { return kNativeRate; },
			                [&](size_t pos) { r.tickInverse = inverseFor(pos < change ? 120 : 100); });
			Pitch p = repeatPitch(w.l, change);
			double spacing = (double)(p.starts.back() - p.starts[p.starts.size() - 3]) / 2;
			printf("fade mode, synced 8th, 120 to 100 BPM: pitch off by up to %.3f cents, repeats %.0f samples apart "
			       "(an 8th at 100 BPM: %.0f)\n",
			       p.worstCents, spacing, kFs * 0.3);
			CHECK(p.worstCents < 1 && std::abs(spacing - kFs * 0.3) < 30, "synced: %.3f cents, %.0f samples apart",
			      p.worstCents, spacing);
		}

		// 19. (v14) Fade mode: no click, whatever the time does. A steady tone through the delay while the time jumps,
		// sweeps as the knob is turned, wobbles, steps every 50 ms, and the mode is switched while it changes: the
		// treble (above 4 kHz) after, against a 0.25 tone. The tape mode for comparison.
		{
			struct Path {
				const char* name;
				std::function<double(double)> f;
				std::function<bool(double)> fadeAt; // Which mode, over time
			};
			auto always = [](bool v) { return [v](double) { return v; }; };
			std::vector<Path> paths = {
			    {"5% longer", [](double s) { return s < 1.5 ? 1.0 : 0.95; }, always(true)},
			    {"70% longer", [](double s) { return s < 1.5 ? 1.0 : 0.6; }, always(true)},
			    {"less than half", [](double s) { return s < 1.5 ? 1.0 : 2.2; }, always(true)},
			    {"knob turned for a second", [](double s) { return s < 1.5 ? 1.0 : std::pow(2.0, -std::min(s - 1.5, 1.0)); },
			     always(true)},
			    {"LFO +-3% at 1 Hz", [](double s) { return s < 1.5 ? 1.0 : 1.0 + 0.03 * std::sin(2 * M_PI * (s - 1.5)); },
			     always(true)},
			    {"a step every 50 ms", [](double s) { return s < 1.5 ? 1.0 : 1.0 + 0.1 * (int)((s - 1.5) / 0.05) * (s < 2.5); },
			     always(true)},
			    {"tape to fade while gliding", [](double s) { return s < 1.5 ? 1.0 : std::pow(2.0, -std::min(s - 1.5, 1.0)); },
			     [](double s) { return s > 1.8; }},
			    {"fade to tape while fading", [](double s) { return s < 1.5 ? 1.0 : (s < 1.7 ? 0.7 : 0.8); },
			     [](double s) { return s < 1.6; }},
			};
			for (auto& p : paths) {
				double tapeAfter = 0;
				for (bool tape : {true, false}) {
					Runner r(!tape);
					Wet w = playWet(r, 44100 * 5, tone440, [&](size_t pos) { return (int32_t)(rate * p.f(pos / kFs)); },
					                [&](size_t pos) {
						                if (!tape) {
							                setFade(r.delay, p.fadeAt(pos / kFs));
						                }
					                });
					double before = trebleLevel(w.l, 20000, 66150);
					double after = trebleLevel(w.l, 66150, w.l.size());
					if (!tape) {
						// Switching modes: no worse than the tape mode's own gliding (its resampling leaves treble
						// 46 to 78 dB down while the time moves)
						double limit = std::max(-80.0, tapeAfter + 3);
						printf("fade mode, %s: treble %.1f dB before, %.1f dB after (buffers made so far %d)\n", p.name,
						       before, after, hostAllocations);
						CHECK(after < limit, "%s: a click in the fade mode (%.1f dB)", p.name, after);
					}
					else {
						tapeAfter = after;
						printf("tape mode, %s: treble %.1f dB before, %.1f dB after\n", p.name, before, after);
					}
				}
			}
		}
	}
#endif

	// Cost of the delay per block of 128 samples, on the Deluge's Cortex-A9 when this runs in the emulator (tests/arm;
	// on the PC nothing is counted): steady (native), with the time modulated (resampling), with the filters on, and
	// the analog mode
	{
		auto measure = [&](const char* label, bool modulated, bool filters, bool analog, bool fade = false) {
			Runner r(fade);
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
#ifndef NO_DELAY_TIME_CHANGE
		measure("delay steady, fade mode, 128 samples", false, false, false, true);
#endif
#ifndef NO_DELAY_TIME_CHANGE
		{
			// v14: while the time changes in the fade mode, two buffers are read and written
			Runner r(true);
			std::vector<int32_t> in(kBlock);
			for (int i = 0; i < kBlock; i++) {
				in[i] = (int32_t)(std::sin(2 * M_PI * 440 * i / kFs) * (1 << 27));
			}
			for (int b = 0; b < 400; b++) {
				r.block(in.data(), kBlock, rate);
			}
			for (int b = 0; b < 60; b++) {
				r.countLabel = (b >= 30 && r.delay.fading) ? "delay fade mode, changing time, 128 samples" : nullptr;
				r.block(in.data(), kBlock, (int32_t)(rate * 1.3));
			}
		}
#endif
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
