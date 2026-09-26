// Benchmark of the oscillators, per voice and block of 128 samples: the firmware's Voice::renderOsc (cut out of
// model/voice/voice.cpp by run.sh) as Voice::renderBasicSource calls it for a synth voice: into a mono oscBuffer,
// with amplitude, once per unison part. Each case warms up, then counts 10 blocks (instructions per block with ARM=1).
// The checksum of the output is printed so the PC and the Cortex-A9 (and a changed firmware) can be compared bit for bit.
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
	    {"saw 220 Hz PW (sync path)", OscType::SAW, 220, 1u << 30, 0, 1, 0},
	    {"saw 330 Hz synced to 110 Hz", OscType::SAW, 330, 0, 110, 1, 0},
	    {"saw 220 Hz unison 4", OscType::SAW, 220, 0, 0, 4, 12},
	    {"saw 220 Hz unison 8", OscType::SAW, 220, 0, 0, 8, 12},
	    {"square 220 Hz PW unison 4", OscType::SQUARE, 220, 1u << 30, 0, 4, 12},
	};
	for (Case const& c : cases) {
		printf("%-30s checksum %016llx\n", c.name, (unsigned long long)runCase(c));
	}
	return 0;
}
