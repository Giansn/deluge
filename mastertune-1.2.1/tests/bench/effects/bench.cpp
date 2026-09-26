// Effects benchmark, per stereo block of 128 samples, on the firmware's own code:
// - ModControllableAudio::processFX (mod FX + EQ; the delay inside is a no-op here) and processSRRAndBitcrushing, cut
//   out of mod_controllable_audio.cpp by run.sh, as a Sound calls them (sound.cpp: SRR/bitcrush, then processFX)
// - ModControllableAudio::processReverbSendAndVolume (volume ramp, pan, reverb send), as sound.cpp, the clips and the
//   song call it
// - RMSFeedbackCompressor::render (song compressor, as audio_engine.cpp calls it) and renderVolNeutral (per sound/clip)
// - ImpulseResponseProcessor::process over a block, as the analog delay runs it (delay.cpp)
// In the emulator (ARM=1) it counts instructions per block. It prints a hash of the output per case, and then one per
// function over random cases (random parameters changing between blocks, blocks of 1-128 samples, mono and stereo
// sources, silence, full scale and extreme values, random start states), so an optimisation can be checked for
// bit-exactness: same hashes before and after, ARM against ARM.
#include "dsp/compressor/rms_feedback.h"
#include "dsp/convolution/impulse_response_processor.h"
#include "emu_count.h"
#include "fx_shim.h"
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>

constexpr int kBlock = 128;
constexpr int kWarm = 10;  // blocks before counting
constexpr int kCount = 10; // blocks counted
constexpr double kFs = 44100;
constexpr int32_t kOff = INT32_MIN; // bitcrush / SRR param "off"
constexpr int kRandomCases = 4000; // per function

// Two detuned saws per side plus a sine, peaks around 2^27 like a sound's buffer before its effects
static void input(StereoSample* buf, uint32_t* ph) {
	for (int i = 0; i < kBlock; i++) {
		ph[0] += (uint32_t)(110.0 / kFs * 4294967296.0);
		ph[1] += (uint32_t)(110.7 / kFs * 4294967296.0);
		ph[2] += (uint32_t)(330.0 / kFs * 4294967296.0);
		int32_t s = (int32_t)(std::sin(ph[2] * (2 * M_PI / 4294967296.0)) * (1 << 25));
		buf[i].l = ((int32_t)ph[0] >> 5) + ((int32_t)ph[1] >> 6) + s;
		buf[i].r = ((int32_t)ph[1] >> 5) + ((int32_t)ph[0] >> 6) - s;
	}
}

static uint64_t hashBlock(uint64_t h, const StereoSample* buf) {
	for (int i = 0; i < kBlock; i++) {
		h = (h ^ (uint32_t)buf[i].l) * 1099511628211ull;
		h = (h ^ (uint32_t)buf[i].r) * 1099511628211ull;
	}
	return h;
}

enum class Kind { FX, COMP_SONG, COMP_SONG_BLEND, COMP_NEUTRAL, IR, VOL_SOUND, VOL_SOUND_NOREVERB, VOL_CLIP_PAN, VOL_SONG };

struct Case {
	const char* name;
	Kind kind;
	ModFXType modFX = ModFXType::NONE;
	int32_t feedback = 0, offset = 0, bass = 0, treble = 0, bitcrush = kOff, srr = kOff;
};

static const Case cases[] = {
    {"chorus", Kind::FX, ModFXType::CHORUS},
    {"chorus stereo", Kind::FX, ModFXType::CHORUS_STEREO},
    {"flanger fb50", Kind::FX, ModFXType::FLANGER},
    {"flanger fb80", Kind::FX, ModFXType::FLANGER, 1288490188},
    {"phaser fb50", Kind::FX, ModFXType::PHASER},
    {"EQ bass+treble", Kind::FX, ModFXType::NONE, 0, 0, 1 << 29, 1 << 29},
    {"bitcrush preset4", Kind::FX, ModFXType::NONE, 0, 0, 0, 0, 0},
    {"SRR 1/4 (11 kHz)", Kind::FX, ModFXType::NONE, 0, 0, 0, 0, kOff, -(1 << 30)},
    {"SRR 1/4 + bitcrush", Kind::FX, ModFXType::NONE, 0, 0, 0, 0, 0, -(1 << 30)},
    {"chorus+EQ+SRR+bitcrush", Kind::FX, ModFXType::CHORUS, 0, 0, 1 << 29, 1 << 29, 0, -(1 << 30)},
    {"compressor song (full wet)", Kind::COMP_SONG},
    {"compressor song blend50", Kind::COMP_SONG_BLEND},
    {"compressor per sound (volNeutral)", Kind::COMP_NEUTRAL},
    {"analog delay IR (26 taps)", Kind::IR},
    {"volume ramp + reverb send, into the mix", Kind::VOL_SOUND},
    {"volume ramp, no reverb, into the mix", Kind::VOL_SOUND_NOREVERB},
    {"volume ramp + pan + reverb, in place", Kind::VOL_CLIP_PAN},
    {"song volume + reverb (no ramp)", Kind::VOL_SONG},
};

namespace AudioEngine {
bool renderInStereo = true;
uint32_t audioSampleTimer = 0;
uint32_t timeThereWasLastSomeReverb = 0;
} // namespace AudioEngine

// ---- random cases -------------------------------------------------------------------------------------------------

struct Rng {
	uint64_t s;
	uint32_t next() {
		s ^= s << 13;
		s ^= s >> 7;
		s ^= s << 17;
		return (uint32_t)(s >> 32);
	}
	uint32_t below(uint32_t n) { return next() % n; }
	bool chance(uint32_t percent) { return below(100) < percent; }
	// Any int32_t, often an extreme or special one
	int32_t any() {
		static const int32_t special[] = {INT32_MIN, INT32_MIN + 1, -(1 << 30), -1, 0, 1, 1 << 27, 1 << 30, INT32_MAX};
		return chance(20) ? special[below(sizeof(special) / sizeof(special[0]))] : (int32_t)next();
	}
	// A knob / param position: often in the usual range, sometimes anything
	int32_t knob() { return chance(70) ? (int32_t)(next() >> (1 + below(4))) : any(); }
	int32_t blockSize() {
		if (chance(30)) {
			return kBlock;
		}
		return chance(25) ? 1 + below(4) : 1 + below(kBlock);
	}
};

static void randomSource(Rng& r, StereoSample* buf) {
	int mode = r.below(8);
	bool mono = r.chance(30);
	int shift = r.below(9);
	int32_t a = r.next(), b = r.next(), da = r.next() >> 8, db = r.next() >> 10;
	for (int i = 0; i < kBlock; i++) {
		int32_t l, rr;
		switch (mode) {
		case 0: // silence
			l = rr = 0;
			break;
		case 1: // anything, full scale
			l = r.next();
			rr = r.next();
			break;
		case 2: // extremes
			l = r.any();
			rr = r.any();
			break;
		case 3: // noise, quieter
			l = (int32_t)r.next() >> shift;
			rr = (int32_t)r.next() >> shift;
			break;
		case 4: // full-scale square
			l = (i >> 3) & 1 ? INT32_MAX : INT32_MIN;
			rr = (i >> 2) & 1 ? INT32_MIN : INT32_MAX;
			break;
		case 5: // tiny
			l = (int32_t)r.below(7) - 3;
			rr = (int32_t)r.below(7) - 3;
			break;
		default: // saws, as a sound's buffer
			a += da;
			b += db;
			l = a >> shift;
			rr = b >> shift;
			break;
		}
		buf[i].l = l;
		buf[i].r = mono ? l : rr;
	}
}

static uint64_t mix(uint64_t h, uint32_t v) {
	return (h ^ v) * 1099511628211ull;
}
static uint64_t mixBlock(uint64_t h, const StereoSample* buf, int n) {
	for (int i = 0; i < n; i++) {
		h = mix(mix(h, buf[i].l), buf[i].r);
	}
	return h;
}

// processReverbSendAndVolume: all its arguments at random, the ramp's start carried over between blocks
static uint64_t randomVolume(int cases) {
	Rng r{0x5eed0001};
	uint64_t h = 1469598103934665603ull;
	static StereoSample buf[kBlock], add[kBlock];
	static int32_t reverb[kBlock];
	for (int c = 0; c < cases; c++) {
		auto fx = std::make_unique<ModControllableAudio>();
		fx->postReverbVolumeLastTime = r.chance(50) ? r.knob() : r.any();
		int32_t postFXVolume = r.knob(), postReverbVolume = r.knob(), send = r.chance(25) ? 0 : r.knob();
		int32_t pan = r.chance(40) ? 0 : r.any();
		int blocks = 1 + r.below(8);
		for (int b = 0; b < blocks; b++) {
			if (r.chance(60)) {
				postFXVolume = r.chance(80) ? r.knob() : r.any();
			}
			if (r.chance(60)) {
				postReverbVolume = r.chance(80) ? r.knob() : r.any();
			}
			if (r.chance(30)) {
				send = r.chance(25) ? 0 : r.any();
			}
			if (r.chance(20)) {
				pan = r.chance(40) ? 0 : r.any();
			}
			randomSource(r, buf);
			randomSource(r, add);
			for (int32_t& v : reverb) {
				v = r.chance(50) ? 0 : r.any();
			}
			int n = r.blockSize();
			bool ramp = r.chance(75);
			StereoSample* addTo = r.chance(50) ? add : nullptr;
			AudioEngine::renderInStereo = r.chance(85);
			fx->processReverbSendAndVolume(buf, n, reverb, postFXVolume, postReverbVolume, send, pan, ramp, addTo);
			AudioEngine::audioSampleTimer += n;
			h = mixBlock(mixBlock(h, buf, kBlock), add, kBlock);
			for (int32_t v : reverb) {
				h = mix(h, v);
			}
			h = mix(mix(h, fx->postReverbVolumeLastTime), AudioEngine::timeThereWasLastSomeReverb);
		}
	}
	AudioEngine::renderInStereo = true;
	return h;
}

// processFX: each mod FX type (grain rarely: its buffer is big) and the EQ, all parameters at random, changing between
// blocks, from a random state
static uint64_t randomFX(int cases) {
	Rng r{0x5eed0002};
	uint64_t h = 1469598103934665603ull;
	static StereoSample modFXBuffer[kModFXBufferSize];
	static StereoSample grainBuffer[kModFXGrainBufferSize];
	static StereoSample buf[kBlock];
	static const ModFXType types[] = {ModFXType::NONE,          ModFXType::CHORUS, ModFXType::CHORUS_STEREO,
	                                  ModFXType::FLANGER,       ModFXType::PHASER, ModFXType::FLANGER,
	                                  ModFXType::PHASER,        ModFXType::CHORUS, ModFXType::CHORUS_STEREO};
	for (int c = 0; c < cases; c++) {
		jcong = r.next();
		auto fx = std::make_unique<ModControllableAudio>();
		for (StereoSample& s : modFXBuffer) {
			s.l = (int32_t)r.next() >> r.below(8);
			s.r = (int32_t)r.next() >> r.below(8);
		}
		fx->modFXBuffer = modFXBuffer;
		fx->modFXBufferWriteIndex = r.below(kModFXBufferSize);
		fx->modFXLFO.phase = r.next();
		fx->modFXLFO.holdValue = r.next();
		bool grain = r.chance(3);
		if (grain) {
			memset(grainBuffer, 0, sizeof(grainBuffer));
			fx->modFXGrainBuffer = grainBuffer;
			fx->modFXGrainBufferWriteIndex = r.below(kModFXGrainBufferSize);
		}
		int32_t stateShift = r.below(6);
		fx->phaserMemory = {(int32_t)r.next() >> stateShift, (int32_t)r.next() >> stateShift};
		for (StereoSample& s : fx->allpassMemory) {
			s = {(int32_t)r.next() >> stateShift, (int32_t)r.next() >> stateShift};
		}
		fx->withoutTrebleL = (int32_t)r.next() >> stateShift;
		fx->withoutTrebleR = (int32_t)r.next() >> stateShift;
		fx->bassOnlyL = (int32_t)r.next() >> stateShift;
		fx->bassOnlyR = (int32_t)r.next() >> stateShift;
		fx->bassFreq = r.knob();
		fx->trebleFreq = r.knob();
		ModFXType type = grain ? ModFXType::GRAIN : types[r.below(sizeof(types) / sizeof(types[0]))];
		ParamManager pm;
		auto& p = pm.unpatched.values;
		int32_t rate = r.knob() >> r.below(12), depth = r.knob();
		int blocks = 1 + r.below(10);
		for (int b = 0; b < blocks; b++) {
			if (!grain && r.chance(8)) {
				type = types[r.below(sizeof(types) / sizeof(types[0]))];
			}
			if (b == 0 || r.chance(50)) {
				p[params::UNPATCHED_MOD_FX_FEEDBACK] = r.any();
				p[params::UNPATCHED_MOD_FX_OFFSET] = r.any();
				p[params::UNPATCHED_BASS] = r.chance(40) ? 0 : r.any();
				p[params::UNPATCHED_TREBLE] = r.chance(40) ? 0 : r.any();
				p[params::UNPATCHED_BASS_FREQ] = r.any();
				p[params::UNPATCHED_TREBLE_FREQ] = r.any();
			}
			if (r.chance(40)) {
				rate = r.chance(70) ? r.knob() >> r.below(12) : r.any();
				depth = r.chance(70) ? r.knob() : r.any();
			}
			if (type == ModFXType::GRAIN && rate == 0) {
				rate = 1; // quickLog(0): clz(0), fine on ARM, not in the PC build
			}
			int32_t postFXVolume = r.chance(70) ? 134217728 : r.any();
			randomSource(r, buf);
			int n = r.blockSize();
			Delay::State delayState;
			fx->processFX(buf, n, type, rate, depth, delayState, &postFXVolume, &pm);
			h = mix(mixBlock(h, buf, kBlock), postFXVolume);
			h = mixBlock(mixBlock(h, &fx->phaserMemory, 1), fx->allpassMemory, kNumAllpassFiltersPhaser);
			h = mix(mix(mix(mix(h, fx->withoutTrebleL), fx->withoutTrebleR), fx->bassOnlyL), fx->bassOnlyR);
			h = mix(mix(mix(mix(h, fx->bassFreq), fx->trebleFreq), fx->modFXBufferWriteIndex), fx->modFXLFO.phase);
			h = mixBlock(h, modFXBuffer, kModFXBufferSize);
			if (grain) {
				h = mix(mix(h, fx->modFXGrainBufferWriteIndex), fx->wrapsToShutdown);
			}
		}
	}
	return h;
}

// processSRRAndBitcrushing: both on and off in turns, all settings, from a random state
static uint64_t randomSRR(int cases) {
	Rng r{0x5eed0003};
	uint64_t h = 1469598103934665603ull;
	static StereoSample buf[kBlock];
	for (int c = 0; c < cases; c++) {
		auto fx = std::make_unique<ModControllableAudio>();
		fx->sampleRateReductionOnLastTime = r.chance(50);
		fx->lowSampleRatePos = r.chance(50) ? r.below(1 << 23) : r.next();
		fx->highSampleRatePos = r.chance(50) ? r.below(1 << 23) : r.next();
		fx->lastSample = {r.any(), r.any()};
		fx->grabbedSample = {r.any(), r.any()};
		fx->lastGrabbedSample = {r.any(), r.any()};
		ParamManager pm;
		auto& p = pm.unpatched.values;
		int blocks = 1 + r.below(10);
		for (int b = 0; b < blocks; b++) {
			if (b == 0 || r.chance(40)) {
				p[params::UNPATCHED_BITCRUSHING] = r.chance(35) ? kOff : r.any();
				p[params::UNPATCHED_SAMPLE_RATE_REDUCTION] = r.chance(35) ? kOff : r.any();
			}
			int32_t postFXVolume = r.chance(70) ? 134217728 : r.any();
			randomSource(r, buf);
			int n = r.blockSize();
			fx->processSRRAndBitcrushing(buf, n, &postFXVolume, &pm);
			h = mix(mixBlock(h, buf, kBlock), postFXVolume);
			h = mix(mix(mix(h, fx->sampleRateReductionOnLastTime), fx->lowSampleRatePos), fx->highSampleRatePos);
			h = mixBlock(mixBlock(mixBlock(h, &fx->lastSample, 1), &fx->grabbedSample, 1), &fx->lastGrabbedSample, 1);
		}
	}
	return h;
}

// RMSFeedbackCompressor::render and renderVolNeutral: all settings, the song's and a sound's way
static uint64_t randomCompressor(int cases) {
	Rng r{0x5eed0004};
	uint64_t h = 1469598103934665603ull;
	static StereoSample buf[kBlock];
	auto pos = [&] { return (int32_t)(r.next() >> 1); }; // a knob, 0 to ONE_Q31
	for (int c = 0; c < cases; c++) {
		RMSFeedbackCompressor comp;
		comp.setup(pos(), pos(), r.chance(20) ? 0 : pos(), pos(), pos(), r.chance(40) ? ONE_Q31 : pos(),
		           r.chance(60) ? 1.35f : 0.5f + r.below(100) / 100.f);
		int blocks = 1 + r.below(10);
		for (int b = 0; b < blocks; b++) {
			if (r.chance(25)) {
				comp.setThreshold(r.chance(20) ? 0 : pos());
			}
			if (r.chance(15)) {
				comp.setBlend(r.chance(40) ? ONE_Q31 : pos());
			}
			if (r.chance(10)) {
				comp.reset();
			}
			randomSource(r, buf);
			int n = r.blockSize();
			if (r.chance(50)) {
				comp.renderVolNeutral(buf, n, r.chance(50) ? 134217728 : pos());
			}
			else {
				int32_t volL = r.chance(50) ? 167763968 >> 1 : pos() >> 1;
				int32_t volR = r.chance(70) ? volL : pos() >> 1;
				comp.render(buf, n, volL, volR, r.chance(50) ? 67108864 >> 3 : pos() >> 3);
			}
			h = mix(mixBlock(h, buf, kBlock), comp.gainReduction);
		}
	}
	return h;
}

int main() {
	static StereoSample modFXBuffer[kModFXBufferSize];
	for (const Case& c : cases) {
		jcong = 380116160;
		auto fx = std::make_unique<ModControllableAudio>(); // value-initialised: all state zero, as after clearing
		memset(modFXBuffer, 0, sizeof(modFXBuffer));
		fx->modFXBuffer = modFXBuffer;
		ParamManager pm;
		auto& p = pm.unpatched.values;
		p[params::UNPATCHED_MOD_FX_FEEDBACK] = c.feedback;
		p[params::UNPATCHED_MOD_FX_OFFSET] = c.offset;
		p[params::UNPATCHED_BASS] = c.bass;
		p[params::UNPATCHED_TREBLE] = c.treble;
		p[params::UNPATCHED_BITCRUSHING] = c.bitcrush;
		p[params::UNPATCHED_SAMPLE_RATE_REDUCTION] = c.srr;
		RMSFeedbackCompressor comp;
		comp.setThreshold(1 << 30); // half way: compresses this input
		if (c.kind == Kind::COMP_SONG_BLEND) {
			comp.setBlend(1 << 30);
		}
		auto ir = std::make_unique<ImpulseResponseProcessor>();
		static StereoSample mixBuf[kBlock];
		static int32_t reverbBuf[kBlock];
		memset(mixBuf, 0, sizeof(mixBuf));
		memset(reverbBuf, 0, sizeof(reverbBuf));
		fx->postReverbVolumeLastTime = 1 << 27;
		Delay::State delayState;
		StereoSample buf[kBlock];
		uint32_t ph[3] = {0, 0, 0};
		uint64_t hash = 1469598103934665603ull;
		for (int b = 0; b < kWarm + kCount; b++) {
			input(buf, ph);
			bool counting = b >= kWarm;
			int32_t postFXVolume = 134217728;
			if (counting) {
				EMU_COUNT_BEGIN(c.name);
			}
			switch (c.kind) {
			case Kind::FX:
				// sound.cpp order; rate ~0.9 Hz, depth half way (paramFinalValues)
				fx->processSRRAndBitcrushing(buf, kBlock, &postFXVolume, &pm);
				fx->processFX(buf, kBlock, c.modFX, 90000, 1 << 30, delayState, &postFXVolume, &pm);
				break;
			case Kind::COMP_SONG:
			case Kind::COMP_SONG_BLEND:
				// audio_engine.cpp: masterVolumeAdjustment 167763968 >> 1, neutral song volume >> 3
				comp.render(buf, kBlock, 167763968 >> 1, 167763968 >> 1, 67108864 >> 3);
				break;
			case Kind::COMP_NEUTRAL:
				comp.renderVolNeutral(buf, kBlock, postFXVolume);
				break;
			case Kind::IR:
				for (StereoSample& s : buf) {
					ir->process(s, s);
				}
				break;
			// sound.cpp (paramFinalValues of a sound at about neutral volume; the post-reverb volume moves, as with
			// sidechain ducking, so the ramp is never flat), the clips (pan, in place) and the song (no ramp)
			case Kind::VOL_SOUND:
				fx->processReverbSendAndVolume(buf, kBlock, reverbBuf, postFXVolume, (1 << 27) + (b << 22), 1 << 28, 0,
				                               true, mixBuf);
				break;
			case Kind::VOL_SOUND_NOREVERB:
				fx->processReverbSendAndVolume(buf, kBlock, reverbBuf, postFXVolume, (1 << 27) + (b << 22), 0, 0, true,
				                               mixBuf);
				break;
			case Kind::VOL_CLIP_PAN:
				fx->processReverbSendAndVolume(buf, kBlock, reverbBuf, postFXVolume, (1 << 27) - (b << 22), 1 << 28,
				                               1 << 29, true);
				break;
			case Kind::VOL_SONG:
				fx->processReverbSendAndVolume(buf, kBlock, reverbBuf, postFXVolume, 1 << 27, 1 << 27);
				break;
			}
			if (counting) {
				EMU_COUNT_END();
			}
			hash = hashBlock(hash, buf);
			if (c.kind == Kind::FX) {
				hash = (hash ^ (uint32_t)postFXVolume) * 1099511628211ull;
			}
			if (c.kind >= Kind::VOL_SOUND) {
				hash = mixBlock(hash, mixBuf, kBlock);
				for (int32_t v : reverbBuf) {
					hash = mix(hash, v);
				}
			}
		}
		printf("%-40s hash %016llx\n", c.name, (unsigned long long)hash);
	}
	printf("%-40s hash %016llx\n", "random: processReverbSendAndVolume", (unsigned long long)randomVolume(kRandomCases));
	printf("%-40s hash %016llx\n", "random: processFX", (unsigned long long)randomFX(kRandomCases));
	printf("%-40s hash %016llx\n", "random: processSRRAndBitcrushing", (unsigned long long)randomSRR(kRandomCases));
	printf("%-40s hash %016llx\n", "random: compressor", (unsigned long long)randomCompressor(kRandomCases));
	return 0;
}
