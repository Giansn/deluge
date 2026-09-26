// Bench shim: what the effect functions cut out of model/mod_controllable/mod_controllable_audio.cpp (processFX with
// mod FX and EQ, processSRRAndBitcrushing, extracted by run.sh) need, without the rest of ModControllableAudio.
// The class here holds only the members those functions touch; the delay is a no-op (benchmarked in tests/delay).
#pragma once
#include "definitions_cxx.hpp"
#include "dsp/stereo_sample.h"
#include "modulation/lfo.h"
#include "modulation/params/param.h"
#include "util/fixedpoint.h"
#include "util/functions.h"
#include <array>
#include <span>

namespace params = deluge::modulation::params;

namespace AudioEngine {
inline void logAction(const char*) {
}
} // namespace AudioEngine

// Only the grain's tempo sync reads these
struct BenchSong {
	float getTimePerTimerTickFloat() { return 1.f; }
};
struct BenchPlaybackHandler {
	float calculateBPM(float) { return 120.f; }
	int32_t getCurrentInternalTickCount() { return 0; }
};
inline BenchSong* currentSong = nullptr;
inline BenchPlaybackHandler playbackHandler;

struct UnpatchedParamSet {
	std::array<int32_t, params::kMaxNumUnpatchedParams> values{};
	int32_t getValue(int32_t p) { return values[p]; }
};
struct ParamManager {
	UnpatchedParamSet unpatched;
	UnpatchedParamSet* getUnpatchedParamSet() { return &unpatched; }
};

struct Delay {
	struct State {};
	void process(std::span<StereoSample>, const State&) {}
};

#include "grain.h" // struct Grain, as mod_controllable_audio.h has it (run.sh copies it out)

class ModControllableAudio {
public:
	void processFX(StereoSample* buffer, int32_t numSamples, ModFXType modFXType, int32_t modFXRate, int32_t modFXDepth,
	               const Delay::State& delayWorkingState, int32_t* postFXVolume, ParamManager* paramManager);
	void processSRRAndBitcrushing(StereoSample* buffer, int32_t numSamples, int32_t* postFXVolume,
	                              ParamManager* paramManager);
	bool isBitcrushingEnabled(ParamManager* paramManager);
	bool isSRREnabled(ParamManager* paramManager);
	bool hasBassAdjusted(ParamManager* paramManager);
	bool hasTrebleAdjusted(ParamManager* paramManager);
	void doEQ(bool doBass, bool doTreble, int32_t* inputL, int32_t* inputR, int32_t bassAmount, int32_t trebleAmount);

	StereoSample phaserMemory;
	StereoSample allpassMemory[kNumAllpassFiltersPhaser];
	int32_t bassFreq, trebleFreq, withoutTrebleL, bassOnlyL, withoutTrebleR, bassOnlyR;
	Delay delay;
	bool sampleRateReductionOnLastTime;
	StereoSample* modFXBuffer;
	uint16_t modFXBufferWriteIndex;
	LFO modFXLFO;
	int32_t wrapsToShutdown;
	StereoSample* modFXGrainBuffer;
	uint32_t modFXGrainBufferWriteIndex;
	int32_t grainSize, grainRate, grainShift;
	Grain grains[8];
	int32_t grainFeedbackVol, grainVol, grainDryVol;
	int8_t grainPitchType;
	bool grainLastTickCountIsZero, grainInitialized;
	uint32_t lowSampleRatePos, highSampleRatePos;
	StereoSample lastSample, grabbedSample, lastGrabbedSample;
};
