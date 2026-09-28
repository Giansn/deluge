// Test shim (mastertune v18): what ModControllableAudio::processReverbSendAndVolume() and restartVolumeRamps(), cut
// out of model/mod_controllable/mod_controllable_audio.cpp by run.sh, need, without the rest of the class
#pragma once
#include "definitions_cxx.hpp"
#include "dsp/gain_ramp.h"
#include "dsp/stereo_sample.h"
#include "util/fixedpoint.h"
#include <cstdint>

namespace AudioEngine {
extern bool renderInStereo;                 // volume_test.cpp
extern uint32_t audioSampleTimer;           // volume_test.cpp
extern uint32_t timeThereWasLastSomeReverb; // volume_test.cpp
} // namespace AudioEngine

bool shouldDoPanning(int32_t panAmount, int32_t* amplitudeL, int32_t* amplitudeR); // functions.cpp, cut out

class ModControllableAudio {
public:
	void processReverbSendAndVolume(StereoSample* buffer, int32_t numSamples, int32_t* reverbBuffer,
	                                int32_t postFXVolume, int32_t postReverbVolume, int32_t reverbSendAmount,
	                                int32_t pan = 0, bool doAmplitudeIncrement = false,
	                                StereoSample* addToBuffer = nullptr, float gainRampStart = 1.0f);
	void restartVolumeRamps();
	deluge::dsp::gain::GainRamp volumeRampL_;
	deluge::dsp::gain::GainRamp volumeRampR_;
	deluge::dsp::gain::GainRamp reverbSendRamp_;
};
