// Benchmark of sample playback and the DX7 engine, per voice and block of 128 samples, on the firmware's own code:
// - SampleLowLevelReader::readSamplesResampled (windowed sinc, dsp/interpolation/interpolate.h + shift_buffer.h, or
//   linear, interpolate_linear.h) and readSamplesNative, cut out of model/sample/sample_low_level_reader.cpp by run.sh,
//   as VoiceSample::render calls them for one window of 128 samples (16-bit WAV in cluster memory, with the reader's
//   deliberate 2-byte misalignment).
// - Time stretching per block: readSamplesForTimeStretching without considerUpcomingWindow, i.e. two play heads
//   (newer and older) crossfading, each through readSamplesResampled/Native, and TimeStretcher::readFromBuffer. The
//   hop search (TimeStretcher::hopEnd) runs once per hop, needs Sample/clusters/perc cache and is not measured.
// - DX7: DxVoice::compute (dsp/dx, with neon_fm_kernel.s on ARM) for one voice as Voice::renderBasicSource calls it,
//   including the memset of uniBuf.
// Each case warms up, then counts 10 blocks (instructions per block with ARM=1). The checksum lets a changed firmware
// be compared bit for bit with the old one on the same machine (on the PC the DX7 NEON kernel is a scalar stand-in and
// the *_rounded multiplies truncate, so PC and ARM checksums differ).
#include "emu_count.h"
#include "samples_shim.h"
#include "dsp/dx/engine.h"
#include "dsp/dx/dx7note.h"
#include <cmath>
#include <cstdio>
#include <cstring>

#if !defined(__arm__)
#include "dsp/dx/math_lut.h"
// Scalar stand-in for neon_fm_kernel.s on the PC: out = busin + sin(phase + in) * gain.
extern "C" void neon_fm_kernel(const int32_t* in, const int32_t* busin, int32_t* out, int count, int32_t phase0,
                               int32_t freq, int32_t gain1, int32_t dgain) {
	for (int i = 0; i < count; i++) {
		gain1 += dgain;
		out[i] = busin[i] + (int32_t)(((int64_t)Sin::lookup((int32_t)((uint32_t)phase0 + (uint32_t)in[i])) * gain1) >> 24);
		phase0 = (int32_t)((uint32_t)phase0 + (uint32_t)freq); // Wraps, as on ARM
	}
}
#endif

namespace {
constexpr int32_t kBlock = 128;
constexpr int kWarmup = 4;
constexpr int kCounted = 10;
constexpr int32_t kFrames = 16384;

int16_t sampleData[2 * kFrames + 64] __attribute__((aligned(64))); // 16-bit stereo, interleaved, as in the WAV
int32_t out[2 * kBlock] __attribute__((aligned(64)));
char cacheMem[kBlock * 2 * kCacheByteDepth * (kWarmup + kCounted) + 16];

uint64_t hashOut(uint64_t hash, int32_t const* p, int32_t n) {
	for (int32_t i = 0; i < n; i++) {
		hash = (hash ^ (uint32_t)p[i]) * 1099511628211ull;
	}
	return hash;
}

int32_t incFor(double semitones) {
	return (int32_t)std::lround(16777216.0 * std::pow(2.0, semitones / 12));
}

enum class Mode { SINC, LINEAR, NATIVE };
struct SampleCase {
	char const* name;
	Mode mode;
	double semitones;
	int32_t numChannels; // In the file
	int32_t channelsOut; // After condensing
	int heads;           // 2 = time stretcher crossfading between two play heads
	bool writingCache;
};

uint64_t runSampleCase(SampleCase const& c) {
	Sample sample{0xFFFF0000u, 2};
	int32_t jumpAmount = sample.byteDepth * c.numChannels;
	int32_t phaseIncrement = c.mode == Mode::NATIVE ? kMaxSampleValue : incFor(c.semitones);
	int32_t whichKernel = getWhichKernel(phaseIncrement);
	int32_t bufferSize = c.mode == Mode::LINEAR ? 2 : kInterpolationMaxNumSamples;
	SampleLowLevelReader heads[2];
	bool doneAny[2] = {false, false};
	int32_t amplitude[2] = {1 << 27, 1 << 26};
	int32_t amplitudeIncrement[2] = {2000, -2000}; // Crossfade: newer head rising, older falling
	for (int h = 0; h < c.heads; h++) {
		heads[h].currentPlayPos = (char*)&sampleData[32 + 2000 * h * c.numChannels] - 2; // Firmware misalignment
		heads[h].clusters[0] = (Cluster*)sampleData;
	}
	char* cachePos = cacheMem;
	uint64_t hash = 1469598103934665603ull;
	for (int block = 0; block < kWarmup + kCounted; block++) {
		memset(out, 0, sizeof out);
		bool counted = block >= kWarmup;
		if (counted) {
			EMU_COUNT_BEGIN(c.name);
		}
		for (int h = 0; h < c.heads; h++) {
			int32_t* pos = out;
			if (c.mode == Mode::NATIVE) {
				heads[h].readSamplesNative(&pos, kBlock, &sample, jumpAmount, c.numChannels, c.channelsOut,
				                           &amplitude[h], amplitudeIncrement[h]);
			}
			else {
				heads[h].readSamplesResampled(&pos, kBlock, &sample, jumpAmount, c.numChannels, c.channelsOut,
				                              phaseIncrement, &amplitude[h], amplitudeIncrement[h], bufferSize,
				                              c.writingCache, &cachePos, &doneAny[h], nullptr, false, whichKernel);
			}
		}
		if (counted) {
			EMU_COUNT_END();
			hash = hashOut(hash, out, kBlock * c.channelsOut);
		}
	}
	return hash;
}

uint64_t runReadFromBuffer() {
	static int32_t tsBuffer[TimeStretch::kBufferSize * 2];
	for (int32_t i = 0; i < TimeStretch::kBufferSize * 2; i++) {
		tsBuffer[i] = (int32_t)(0x9E3779B9u * (i + 1));
	}
	TimeStretcher ts;
	ts.buffer = tsBuffer;
	int32_t readPos = 0;
	uint64_t hash = 1469598103934665603ull;
	for (int block = 0; block < kWarmup + kCounted; block++) {
		memset(out, 0, sizeof out);
		bool counted = block >= kWarmup;
		if (counted) {
			EMU_COUNT_BEGIN("timestretch readFromBuffer stereo");
		}
		ts.readFromBuffer(out, kBlock, 2, 2, 1 << 27, 1000, &readPos);
		if (counted) {
			EMU_COUNT_END();
			hash = hashOut(hash, out, kBlock * 2);
		}
	}
	return hash;
}

struct DxCase {
	char const* name;
	int algorithm; // 1 ... 32
	int feedback;  // 0 ... 7
	int engineMode;
	int ampModSens; // 0 ... 3 per operator, with the LFO's amp mod depth up
};

uint64_t runDxCase(DxCase const& c) {
	static int32_t uniBuf[DX_MAX_N] __attribute__((aligned(64)));
	DxEngine* engine = getDxEngine();
	DxPatch* patch = engine->newPatch();
	static int const coarse[6] = {1, 1, 1, 1, 14, 1};
	for (int op = 0; op < 6; op++) {
		uint8_t* p = &patch->params[op * 21];
		p[0] = 95, p[1] = 29, p[2] = 20, p[3] = 50; // Rates: fast attack, slow decays
		p[4] = 99, p[5] = 95, p[6] = 90, p[7] = 0;  // Levels
		p[14] = c.ampModSens;
		p[16] = op == 4 ? 60 : 90; // Output level
		p[18] = coarse[op];
		p[20] = 7 + (op & 1);
	}
	patch->params[134] = c.algorithm - 1;
	patch->params[135] = c.feedback;
	patch->params[137] = 35;                         // LFO speed
	patch->params[140] = c.ampModSens ? 30 : 0;      // LFO amp mod depth
	patch->params[142] = 4;                          // LFO sine
	patch->setEngineMode(c.engineMode, true);
	DxVoice* voice = engine->solicitDxVoice();
	voice->init(*patch, 60, 100);
	uint32_t phaseIncrement = (uint32_t)(261.6256 / 44100 * 4294967296.0);
	int adjpitch = (int)(log2f(phaseIncrement) * (1 << 24)) - 278023814; // As Voice::renderBasicSource
	DxVoiceCtrl ctrl{};
	uint64_t hash = 1469598103934665603ull;
	for (int block = 0; block < kWarmup + kCounted; block++) {
		patch->computeLfo(kBlock); // Once per sound and block, not per voice
		bool counted = block >= kWarmup;
		if (counted) {
			EMU_COUNT_BEGIN(c.name);
		}
		memset(uniBuf, 0, sizeof uniBuf);
		voice->compute(uniBuf, kBlock, adjpitch, patch, &ctrl);
		if (counted) {
			EMU_COUNT_END();
			hash = hashOut(hash, uniBuf, kBlock);
		}
	}
	return hash;
}
} // namespace

int main() {
	for (int32_t i = 0; i < 2 * kFrames + 64; i++) { // Two detuned sines plus a little noise, near full scale
		double t = i >> 1;
		double v = 0.45 * std::sin(t * 0.031 + (i & 1)) + 0.35 * std::sin(t * 0.0071) + 0.05 * std::sin(t * 1.7);
		sampleData[i] = (int16_t)std::lround(v * 32767);
	}
	static SampleCase const sampleCases[] = {
	    {"native stereo (no pitch)", Mode::NATIVE, 0, 2, 2, 1, false},
	    {"linear stereo +7 st", Mode::LINEAR, 7, 2, 2, 1, false},
	    {"sinc mono +7 st", Mode::SINC, 7, 1, 1, 1, false},
	    {"sinc stereo -5 st", Mode::SINC, -5, 2, 2, 1, false},
	    {"sinc stereo +7 st", Mode::SINC, 7, 2, 2, 1, false},
	    {"sinc stereo +12 st", Mode::SINC, 12, 2, 2, 1, false},
	    {"sinc stereo +7 st, writing cache", Mode::SINC, 7, 2, 2, 1, true},
	    {"timestretch 2 heads native stereo", Mode::NATIVE, 0, 2, 2, 2, false},
	    {"timestretch 2 heads sinc stereo +3 st", Mode::SINC, 3, 2, 2, 2, false},
	};
	for (auto const& c : sampleCases) {
		printf("%-40s checksum %016llx\n", c.name, (unsigned long long)runSampleCase(c));
	}
	printf("%-40s checksum %016llx\n", "timestretch readFromBuffer stereo", (unsigned long long)runReadFromBuffer());
	static DxCase const dxCases[] = {
	    {"dx7 alg5 fb6 (modern, NEON)", 5, 6, 0, 0},
	    {"dx7 alg32 fb6 (6 carriers)", 32, 6, 0, 0},
	    {"dx7 alg5 fb6 + LFO amp mod", 5, 6, 0, 2},
	    {"dx7 alg5 fb6 (MkI engine)", 5, 6, 2, 0},
	    {"dx7 alg4 fb6 (MkI fb loop)", 4, 6, 0, 0},
	};
	for (auto const& c : dxCases) {
		printf("%-40s checksum %016llx\n", c.name, (unsigned long long)runDxCase(c));
	}
	return 0;
}
