// Bench shim: what the playback part of model/sample/sample_low_level_reader.cpp (bufferIndividualSampleForInterpolation
// ... readSamplesNative, cut out by run.sh) and TimeStretcher::readFromBuffer need, without Sample, clusters, the
// audio file manager or VoiceSample. The classes hold only the members that code touches, with the firmware's layout
// of interpolationBuffer (int16x4_t[2][4]).
#pragma once
#include "arm_neon_shim.h"
#include "definitions_cxx.hpp"
#include "util/fixedpoint.h"
#include "util/lookuptables/lookuptables.h"
#include "dsp/interpolation/shift_buffer.h"

struct Cluster;
struct Sample { // The real Sample has much more; the cut-out code reads only these two
	uint32_t bitMask;
	uint8_t byteDepth;
};

class TimeStretcher;

class SampleLowLevelReader {
public:
	void readSamplesNative(int32_t** __restrict__ oscBufferPos, int32_t numSamplesTotal, Sample* sample,
	                       int32_t jumpAmount, int32_t numChannels, int32_t numChannelsAfterCondensing,
	                       int32_t* amplitude, int32_t amplitudeIncrement, TimeStretcher* timeStretcher = NULL,
	                       bool bufferingToTimeStretcher = false);
	void readSamplesResampled(int32_t** __restrict__ oscBufferPos, int32_t numSamples, Sample* sample,
	                          int32_t jumpAmount, int32_t numChannels, int32_t numChannelsAfterCondensing,
	                          int32_t phaseIncrement, int32_t* amplitude, int32_t amplitudeIncrement,
	                          int32_t bufferSize, bool writingCache, char** __restrict__ cacheWritePos,
	                          bool* doneAnySamplesYet, TimeStretcher* timeStretcher, bool bufferingToTimeStretcher,
	                          int32_t whichKernel);
	void bufferIndividualSampleForInterpolation(uint32_t bitMask, int32_t numChannels, int32_t byteDepth,
	                                            char* playPosNow);
	void bufferZeroForInterpolation(int32_t numChannels);
	void jumpForwardZeroes(int32_t bufferSize, int32_t numChannels, int32_t phaseIncrement);
	void jumpForwardLinear(int32_t numChannels, int32_t byteDepth, uint32_t bitMask, int32_t jumpAmount,
	                       int32_t phaseIncrement);
	void interpolate(int32_t* __restrict__ sampleRead, int32_t numChannelsNow, int32_t whichKernel);
	void interpolateLinear(int32_t* __restrict__ sampleRead, int32_t numChannelsNow, int32_t whichKernel);

	uint32_t oscPos = 0;
	char* currentPlayPos = nullptr;
	int16x4_t interpolationBuffer[2][kInterpolationMaxNumSamples >> 2] = {};
	Cluster* clusters[kNumClustersReaderAhead] = {};
};

class TimeStretcher {
public:
	void readFromBuffer(int32_t* __restrict__ oscBufferPos, int32_t numSamples, int32_t numChannels,
	                    int32_t numChannelsAfterCondensing, int32_t sourceAmplitudeNow, int32_t amplitudeIncrementNow,
	                    int32_t* __restrict__ bufferReadPos);
	int32_t* buffer = nullptr;
};

int32_t getWhichKernel(int32_t phaseIncrement);
