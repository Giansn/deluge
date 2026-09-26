// Effects benchmark, per stereo block of 128 samples, on the firmware's own code:
// - ModControllableAudio::processFX (mod FX + EQ; the delay inside is a no-op here) and processSRRAndBitcrushing, cut
//   out of mod_controllable_audio.cpp by run.sh, as a Sound calls them (sound.cpp: SRR/bitcrush, then processFX)
// - RMSFeedbackCompressor::render (song compressor, as audio_engine.cpp calls it) and renderVolNeutral (per sound/clip)
// - ImpulseResponseProcessor::process over a block, as the analog delay runs it (delay.cpp)
// In the emulator (ARM=1) it counts instructions per block; on the PC it prints a hash of the output per case, so an
// optimisation can be checked for bit-exactness (same hash before and after).
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

enum class Kind { FX, COMP_SONG, COMP_SONG_BLEND, COMP_NEUTRAL, IR };

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
};

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
			}
			if (counting) {
				EMU_COUNT_END();
			}
			hash = hashBlock(hash, buf);
			if (c.kind == Kind::FX) {
				hash = (hash ^ (uint32_t)postFXVolume) * 1099511628211ull;
			}
		}
		printf("%-36s hash %016llx\n", c.name, (unsigned long long)hash);
	}
	return 0;
}
