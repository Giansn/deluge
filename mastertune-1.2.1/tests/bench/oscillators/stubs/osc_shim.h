// Bench shim: what the oscillator part of model/voice/voice.cpp (extracted by run.sh, from renderWave's instance to the
// end of Voice::renderOsc) needs, without the rest of the Voice. Voice here holds only the members renderOsc touches.
#pragma once
#include "arm_neon_shim.h"
#include "definitions_cxx.hpp"
#include "memory/general_memory_allocator.h"
#include "util/fixedpoint.h"
#include "util/lookuptables/lookuptables.h"
#include "util/waves.h"
#include "processing/render_wave.h"
#include <bit>

namespace AudioEngine {
extern int32_t cpuDireness;
}
extern int32_t oscSyncRenderingBuffer[];

// Wavetables are not benchmarked (setting one up needs a Sample, clusters and the FFT); this only lets the branch compile.
struct WaveTable {
	uint32_t render(int32_t*, int32_t, uint32_t, uint32_t phase, bool, uint32_t, uint32_t, int32_t, uint32_t, int32_t,
	                int32_t) {
		return phase;
	}
};
struct BenchAudioFileHolder {
	void* audioFile;
};
struct BenchGuide {
	BenchAudioFileHolder* audioFileHolder;
};

class Voice {
public:
	void renderOsc(int32_t s, OscType type, int32_t amplitude, int32_t* thisSample, int32_t* bufferEnd,
	               int32_t numSamples, uint32_t phaseIncrementNow, uint32_t phaseWidth, uint32_t* thisPhase,
	               bool applyAmplitude, int32_t amplitudeIncrement, bool doOscSync, uint32_t resetterPhase,
	               uint32_t resetterPhaseIncrement, uint32_t retriggerPhase, int32_t waveIndexIncrement);
	int32_t sourceWaveIndexesLastTime[2] = {0, 0};
	BenchGuide guides[2] = {};
};
