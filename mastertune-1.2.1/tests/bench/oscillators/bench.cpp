// Benchmark of the oscillators, per voice and block of 128 samples: the firmware's Voice::renderOsc (cut out of
// model/voice/voice.cpp by run.sh) as Voice::renderBasicSource calls it for a synth voice: into a mono oscBuffer,
// with amplitude, once per unison part. Each case warms up, then counts 10 blocks (instructions per block with ARM=1).
// The checksum of the output is printed so a changed firmware can be compared bit for bit with the old one (on the same
// machine: on the PC, the firmware's fallbacks of the *_rounded multiplies in util/fixedpoint.h truncate instead of
// rounding, so the crude saw/square, the triangle below ~700 Hz and PW on saws differ from the Cortex-A9's smmulr/smmlar).
#include "emu_count.h"
#include "osc_shim.h"
#include <cmath>
#include <cstdio>
#include <cstring>

namespace AudioEngine {
int32_t cpuDireness = 0;
}
int32_t oscSyncRenderingBuffer[256] __attribute__((aligned(64)));

namespace {
constexpr int32_t kBlock = 128;
constexpr int kWarmup = 4;
constexpr int kCounted = 10;

struct Case {
	char const* name;
	OscType type;
	double hz;
	uint32_t pulseWidth; // As renderBasicSource passes it: 0 = no PW, 1 << 30 = a quarter of the way to the thinnest
	double syncHz;       // Osc A as the resetter of osc sync, 0 = off
	int unison;
	double detuneCents;  // Spread of the unison parts, from -detune to +detune
};

uint32_t phaseIncrementFor(double hz) {
	return (uint32_t)(hz / 44100.0 * 4294967296.0);
}

uint64_t runCase(Case const& c) {
	static int32_t buffer[kBlock] __attribute__((aligned(64)));
	Voice voice;
	uint32_t phase[8], inc[8], syncPos[8], syncInc[8];
	for (int u = 0; u < c.unison; u++) {
		double cents = c.unison > 1 ? -c.detuneCents + 2 * c.detuneCents * u / (c.unison - 1) : 0;
		double ratio = std::pow(2.0, cents / 1200);
		inc[u] = phaseIncrementFor(c.hz * ratio);
		syncInc[u] = c.syncHz > 0 ? phaseIncrementFor(c.syncHz * ratio) : 0;
		phase[u] = 0x9E3779B9u * (u + 1);
		syncPos[u] = 0x7F4A7C15u * (u + 1);
	}
	int32_t amplitude = 1 << 27;
	int32_t amplitudeIncrement = 300; // A slowly moving envelope
	uint64_t hash = 1469598103934665603ull;
	for (int block = 0; block < kWarmup + kCounted; block++) {
		memset(buffer, 0, sizeof buffer);
		bool counted = block >= kWarmup;
		if (counted) {
			EMU_COUNT_BEGIN(c.name);
		}
		for (int u = 0; u < c.unison; u++) {
			voice.renderOsc(0, c.type, amplitude, buffer, buffer + kBlock, kBlock, inc[u], c.pulseWidth, &phase[u], true,
			                amplitudeIncrement, c.syncHz > 0, syncPos[u], syncInc[u], 0, 0);
		}
		if (counted) {
			EMU_COUNT_END();
			for (int32_t i = 0; i < kBlock; i++) {
				hash = (hash ^ (uint32_t)buffer[i]) * 1099511628211ull;
			}
		}
		for (int u = 0; u < c.unison; u++) {
			syncPos[u] += syncInc[u] * kBlock;
		}
		amplitude += amplitudeIncrement * kBlock;
	}
	return hash;
}

// Randomized cases, for comparing a changed firmware bit for bit with the old one over far more than the fixed cases:
// random oscillator type, pitch (log-uniform, and right at the thresholds between the rendering paths and tables),
// pulse width, osc sync, unison parts, cpuDireness, amplitude and its slope, block sizes 1 to 128 (odd ones too) and
// changes of all of that between blocks; mono (accumulating into a buffer that holds other audio already) and stereo
// (rendering into a cleared buffer and panning it, as Voice::renderBasicSource does), and without amplitude (as for
// ring mod). The checksum covers the whole buffer each block, including what the vector loops write past the end, and
// the phases. Not counted: counting runs the emulator instruction by instruction, far too slow for this many.
struct Rng {
	uint64_t state;
	uint32_t next() {
		state ^= state << 13;
		state ^= state >> 7;
		state ^= state << 17;
		return (uint32_t)(state >> 32);
	}
	uint32_t below(uint32_t n) { return (uint32_t)(((uint64_t)next() * n) >> 32); }
	bool chance(uint32_t percent) { return below(100) < percent; }
	// Log-uniform between 2^lo and 2^hi
	uint32_t logUniform(double lo, double hi) { return (uint32_t)std::exp2(lo + (hi - lo) * (next() / 4294967296.0)); }
};

// Phase increments where the rendering changes: the triangle's crude/table switch and its tables, and the saw/square
// table numbers (getTableNumber)
constexpr uint32_t kThresholds[] = {69273666,  102261126, 143165576, 238609294, 429496729, 715827882, 1247086,
                                    1764571,   2494173,   3526245,   4982560,   7040929,   9988296,   14035840,
                                    19701684,  28256363,  40518559,  55063683,  79536431,  113025455, 165191049,
                                    306783378, 0x10000000, 0x40000000};

uint32_t randomPhaseIncrement(Rng& rng) {
	if (rng.chance(25)) {
		return kThresholds[rng.below(sizeof kThresholds / sizeof kThresholds[0])] + rng.below(5) - 2;
	}
	return rng.logUniform(10, 30.9); // Up to about 2026954652, the highest the firmware plays
}

uint32_t randomPulseWidth(Rng& rng) {
	if (rng.chance(40)) {
		return 0;
	}
	// Any value lshiftAndSaturate<1>() gives, short of INT32_MIN (which divides by zero on analog square in the
	// firmware, old and new)
	return (uint32_t)(int32_t)(rng.next() % 4294967295u - 2147483647);
}

constexpr int32_t kBufferPadding = 8; // The vector loops write up to 3 samples past the end

uint64_t runRandomCases(int numCases, uint64_t seed) {
	static int32_t oscBuffer[2 * kBlock + kBufferPadding] __attribute__((aligned(64)));
	static int32_t renderBuffer[kBlock + kBufferPadding] __attribute__((aligned(64)));
	static OscType const types[] = {OscType::SINE, OscType::TRIANGLE,     OscType::SQUARE,
	                                OscType::SAW,  OscType::ANALOG_SAW_2, OscType::ANALOG_SQUARE};
	Rng rng{seed};
	uint64_t hash = 1469598103934665603ull;
	auto add = [&](uint32_t x) { hash = (hash ^ x) * 1099511628211ull; };
	for (int32_t i = 0; i < 2 * kBlock + kBufferPadding; i++) {
		oscBuffer[i] = (int32_t)rng.next() >> 4;
	}
	for (int c = 0; c < numCases; c++) {
		Voice voice;
		OscType type = types[rng.below(sizeof types / sizeof types[0])];
		AudioEngine::cpuDireness = rng.chance(70) ? 0 : (int32_t)rng.below(15);
		int unison = 1 + rng.below(4);
		bool stereo = rng.chance(40);
		bool applyAmplitude = rng.chance(80);
		bool sync = rng.chance(25);
		uint32_t retriggerPhase = rng.chance(50) ? 0 : rng.next();
		uint32_t phase[4], syncPos[4];
		for (int u = 0; u < unison; u++) {
			phase[u] = rng.next();
			syncPos[u] = rng.next();
		}
		uint32_t phaseIncrement = randomPhaseIncrement(rng);
		uint32_t syncIncrement = rng.logUniform(12, 30.9);
		uint32_t pulseWidth = randomPulseWidth(rng);
		int32_t amplitude = (int32_t)rng.below(1 << 28);
		int numBlocks = 2 + rng.below(7);
		for (int block = 0; block < numBlocks; block++) {
			int32_t numSamples = rng.chance(20) ? kBlock : 1 + (int32_t)rng.below(kBlock);
			// Parameter changes between blocks
			if (block && rng.chance(30)) {
				phaseIncrement = rng.chance(50) ? randomPhaseIncrement(rng)
				                                : (uint32_t)std::min(phaseIncrement * (0.9 + 0.2 * rng.below(1000) / 1000),
				                                                     2026954652.0);
				if (phaseIncrement == 0) {
					phaseIncrement = 1;
				}
			}
			if (block && rng.chance(30)) {
				pulseWidth = randomPulseWidth(rng);
			}
			if (block && rng.chance(20)) {
				syncIncrement = rng.logUniform(12, 30.9);
			}
			int32_t amplitudeIncrement = applyAmplitude ? (int32_t)rng.below(1 << 21) - (1 << 20) : 0;
			if (!applyAmplitude || (int64_t)amplitude + (int64_t)amplitudeIncrement * numSamples < 0) {
				amplitudeIncrement = applyAmplitude ? (int32_t)rng.below(1 << 16) : 0;
			}
			int32_t renderAmplitude = applyAmplitude ? amplitude : 0;
			int32_t amplitudeL = (int32_t)rng.below(1 << 28), amplitudeR = (int32_t)rng.below(1 << 28);

			// Mono: straight into the buffer with other voices' audio in it. Stereo, and without amplitude: into a
			// buffer of its own (cleared for stereo; left as it was without amplitude, which overwrites it)
			bool ownBuffer = stereo || !applyAmplitude;
			int32_t* target = ownBuffer ? renderBuffer : oscBuffer;
			if (stereo && applyAmplitude) {
				memset(renderBuffer, 0, sizeof renderBuffer);
			}
			for (int u = 0; u < unison; u++) {
				double cents = unison > 1 ? -20 + 40.0 * u / (unison - 1) : 0;
				uint32_t inc = (uint32_t)std::min(phaseIncrement * std::exp2(cents / 1200), 2026954652.0);
				uint32_t syncInc = (uint32_t)(syncIncrement * std::exp2(cents / 1200));
				if (inc == 0) {
					inc = 1;
				}
				voice.renderOsc(0, type, renderAmplitude, target, target + numSamples, numSamples, inc, pulseWidth,
				                &phase[u], applyAmplitude, amplitudeIncrement, sync, syncPos[u], syncInc,
				                retriggerPhase, 0);
				if (ownBuffer) {
					for (int32_t i = 0; i < kBlock + kBufferPadding; i++) {
						add((uint32_t)renderBuffer[i]);
					}
				}
				syncPos[u] += syncInc * numSamples;
				add(phase[u]);
			}
			if (stereo) {
				for (int32_t i = 0; i < numSamples; i++) { // Adding modulo 2^32, as the Deluge does
					oscBuffer[i << 1] = (int32_t)((uint32_t)oscBuffer[i << 1]
					                              + ((uint32_t)multiply_32x32_rshift32(renderBuffer[i], amplitudeL) << 2));
					oscBuffer[(i << 1) + 1] =
					    (int32_t)((uint32_t)oscBuffer[(i << 1) + 1]
					              + ((uint32_t)multiply_32x32_rshift32(renderBuffer[i], amplitudeR) << 2));
				}
			}
			for (int32_t i = 0; i < 2 * kBlock + kBufferPadding; i++) {
				add((uint32_t)oscBuffer[i]);
				oscBuffer[i] >>= 1; // Keep the "other voices" from growing without bounds
			}
			amplitude += amplitudeIncrement * numSamples;
		}
	}
	AudioEngine::cpuDireness = 0;
	return hash;
}
} // namespace

int main() {
	static Case const cases[] = {
	    {"saw 220 Hz (table)", OscType::SAW, 220, 0, 0, 1, 0},
	    {"saw 55 Hz (crude)", OscType::SAW, 55, 0, 0, 1, 0},
	    {"saw 1760 Hz (table)", OscType::SAW, 1760, 0, 0, 1, 0},
	    {"square 220 Hz", OscType::SQUARE, 220, 0, 0, 1, 0},
	    {"square 55 Hz (crude)", OscType::SQUARE, 55, 0, 0, 1, 0},
	    {"square 220 Hz PW", OscType::SQUARE, 220, 1u << 30, 0, 1, 0},
	    {"sine 220 Hz", OscType::SINE, 220, 0, 0, 1, 0},
	    {"triangle 220 Hz", OscType::TRIANGLE, 220, 0, 0, 1, 0},
	    {"triangle 1760 Hz (table)", OscType::TRIANGLE, 1760, 0, 0, 1, 0},
	    {"analog saw 220 Hz", OscType::ANALOG_SAW_2, 220, 0, 0, 1, 0},
	    {"analog square 220 Hz", OscType::ANALOG_SQUARE, 220, 0, 0, 1, 0},
	    {"analog square 220 Hz PW", OscType::ANALOG_SQUARE, 220, 1u << 30, 0, 1, 0},
	    {"saw 220 Hz PW (sync path)", OscType::SAW, 220, 1u << 30, 0, 1, 0},
	    {"saw 330 Hz synced to 110 Hz", OscType::SAW, 330, 0, 110, 1, 0},
	    {"saw 220 Hz unison 4", OscType::SAW, 220, 0, 0, 4, 12},
	    {"saw 220 Hz unison 8", OscType::SAW, 220, 0, 0, 8, 12},
	    {"square 220 Hz PW unison 4", OscType::SQUARE, 220, 1u << 30, 0, 4, 12},
	};
	for (Case const& c : cases) {
		printf("%-30s checksum %016llx\n", c.name, (unsigned long long)runCase(c));
	}
	constexpr int kRandomCases = 30000;
	printf("%-30s checksum %016llx\n", "random cases (30000)",
	       (unsigned long long)runRandomCases(kRandomCases, 0x2545F4914F6CDD1Dull));
	return 0;
}
