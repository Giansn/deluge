// Host test for the reverb's Modulation and Pre-delay (v14): the firmware's reverb code on the PC.
//   - Modulation 0: a held 220 Hz tone stays steady in the reverb (level within 0.5 dB over 7 s), for Mutable and
//     Digital; at the full depth it moves by more than 3 dB (the v10 to v13 behaviour, which the song test checks
//     bit-exactly against v13)
//   - Pre-delay: the output with 20 ms is the output without, 882 samples later, bit for bit (at Modulation 0, where
//     the reverb doesn't depend on time), for all three models; 0 ms leaves the input untouched
//   - Pre-delay switched on after a while: nothing of the earlier input comes out of the delay
#include "dsp/reverb/reverb.hpp"
#include <cmath>
#include <cstdio>
#include <vector>

using deluge::dsp::Reverb;
using Model = Reverb::Model;
static int checks = 0, failures = 0;
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

static std::vector<int32_t> run(Reverb& reverb, const std::vector<int32_t>& input) {
	std::vector<int32_t> out;
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
			out.push_back(buf[i].l);
			out.push_back(buf[i].r);
		}
	}
	return out;
}

static void setup(Reverb& r, Model m, float modulation, int32_t preDelayMs) {
	r.setModel(m);
	r.setRoomSize(30.f / 50);
	r.setDamping(14.f / 50);
	r.setWidth(1.f);
	r.setHPF(0.f);
	r.setLPF(1.f);
	r.setModulation(modulation);
	r.setPreDelayMs(preDelayMs);
	r.setPanLevels(1 << 29, 1 << 29);
}

// Level of the left output in 50 ms windows from 4 to 11 s of a held 220 Hz tone: spread in dB (max over min)
static double heldToneLevelSpreadDb(Model m, float modulation) {
	static Reverb r; // big (the delay buffers), so not on the stack
	setup(r, m, modulation, 0);
	size_t n = (size_t)(11 * kFs);
	std::vector<int32_t> in(n);
	for (size_t i = 0; i < n; i++) {
		in[i] = (int32_t)(0.5 * std::sin(2 * M_PI * 220.0 * i / kFs) * (1 << 26));
	}
	auto out = run(r, in);
	double lo = 1e300, hi = 0;
	size_t w = (size_t)(0.05 * kFs);
	for (size_t s = (size_t)(4 * kFs); s + w <= n; s += w) {
		double e = 0;
		for (size_t i = s; i < s + w; i++) {
			e += (double)out[2 * i] * out[2 * i];
		}
		lo = std::min(lo, e);
		hi = std::max(hi, e);
	}
	return 10 * std::log10(hi / lo);
}

int main() {
	const char* names[] = {"Freeverb", "Mutable", "Digital"};
	for (Model m : {Model::MUTABLE, Model::DIGITAL}) {
		double still = heldToneLevelSpreadDb(m, 0.f);
		double full = heldToneLevelSpreadDb(m, 1.f);
		printf("%s: held tone, level spread %.2f dB at Modulation 0, %.1f dB at 50\n", names[(int)m], still, full);
		CHECK(still < 0.5, "%s: held tone moves %.2f dB at Modulation 0", names[(int)m], still);
		CHECK(full > 3, "%s: held tone moves only %.1f dB at full modulation", names[(int)m], full);
	}
	// Pre-delay: an exact shift at Modulation 0. The burst comes after 1 s of silence, when the ramp of the pan levels
	// (at the start, see Base::startPanRamp()) is over
	std::vector<int32_t> burst((size_t)(3 * kFs), 0);
	for (size_t i = 44100; i < 46000; i++) {
		burst[i] = (int32_t)(std::sin(i * 0.05) * (1 << 27));
	}
	for (Model m : {Model::FREEVERB, Model::MUTABLE, Model::DIGITAL}) {
		static Reverb a, b;
		setup(a, m, 0.f, 0);
		setup(b, m, 0.f, 20);
		auto outA = run(a, burst);
		auto outB = run(b, burst);
		const size_t d = 882 * 2;
		bool same = true;
		for (size_t i = 0; i + d < outA.size(); i++) {
			same &= outB[i + d] == outA[i];
		}
		bool silentBefore = true;
		for (size_t i = 0; i < d; i++) {
			silentBefore &= outB[i] == 0;
		}
		CHECK(same && silentBefore, "%s: 20 ms pre-delay is not the output 882 samples later", names[(int)m]);
		CHECK(b.getPreDelayMs() == 20 && a.getPreDelayMs() == 0, "%s: pre-delay read back", names[(int)m]);
	}
	// Switched on later: the delay starts empty
	{
		static Reverb a, b;
		setup(a, Model::MUTABLE, 0.f, 0);
		setup(b, Model::MUTABLE, 0.f, 0);
		auto first = run(a, burst); // a has seen the burst, b hasn't
		a.setPreDelayMs(50);
		b.setPreDelayMs(50);
		std::vector<int32_t> silence((size_t)(0.05 * kFs), 0);
		auto outA = run(a, silence);
		auto outB = run(b, silence);
		// a: only its reverb tail from before, no burst again. b: silent.
		bool bSilent = true;
		for (int32_t v : outB) {
			bSilent &= v == 0;
		}
		CHECK(bSilent, "pre-delay switched on: old input came out of the delay");
		static Reverb c;
		setup(c, Model::MUTABLE, 0.f, 0);
		run(c, burst);
		auto outC = run(c, silence); // the same reverb tail without a pre-delay
		CHECK(outA == outC, "pre-delay switched on: the first 50 ms differ from the plain reverb tail");
	}
	// Clamping
	{
		static Reverb r;
		r.setPreDelayMs(500);
		CHECK(r.getPreDelayMs() == Reverb::kMaxPreDelayMs, "pre-delay not clamped: %d", (int)r.getPreDelayMs());
		r.setModulation(2.f);
		CHECK(r.getModulation() == 1.f, "modulation not clamped");
	}
	printf("%d checks, %d failures\n", checks, failures);
	return failures != 0;
}
