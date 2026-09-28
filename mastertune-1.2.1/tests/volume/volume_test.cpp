// The volume stage of mastertune v18 (dsp/gain_ramp.h, ModControllableAudio::processReverbSendAndVolume()) on the
// Deluge's Cortex-A9, against v17's (the per-sample semantics of its loop, rebuilt here in scalar code: the volume
// set per block, the sidechain alone ramped with a truncated step, smmul << 5 truncating, sums wrapping).
// Levels in dBFS: 0 dBFS = a sine of peak 2^24 inside the firmware (where the output clips, audio_engine.cpp).
// Cases (argv[1]):
//   ramp       RampSteps: sample n lands exactly on the target and every sample within 0.5 of the straight line
//              (random starts, ends, block lengths 1..128); GainRamp: blocks join, the target is reached.
//   neutral    the stage at volume 25 (unity), sidechain neutral, no pan / send: output == input, bit for bit (in
//              place and adding); static gains: within 0.5 LSB of the exact product up to unity, 2^(s-1) above.
//   precision  static gains: the error against the exact product (float64) for v17 and v18, and the SNR of a quiet
//              signal at low volume (fails: v18 error above -138 dBFS).
//   zipper     volume automation (10 -> 40), pan (-25 -> +25) and the sidechain moving at once, blocks of 60..128:
//              output against the ideal (the gain linear between the blocks' targets); fails above -130 dBFS.
//   bigstep    volume 0 -> 25 and 25 -> 0 at once: v18 takes >= 5 ms (221 samples), the largest per-sample step
//              (fails if any step is more than 1/221 of the move + rounding).
//   pan        the sound's pan: ramps land exactly; mono -> stereo equals stereo on the doubled input.
//   cpu        instructions per 128 samples: v18 steady / ramping, with and without the reverb send, against
//              v17's NEON loop where run.sh found it (git show of the v17 tree, V17_KERNELS).
#include "dsp/gain_ramp.h"
#include "emu_count.h"
#include "volume_shim.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#ifdef V17_KERNELS
#include "v17_kernels.h"
#endif

namespace AudioEngine {
bool renderInStereo = true;
uint32_t audioSampleTimer = 0;
uint32_t timeThereWasLastSomeReverb = 0;
} // namespace AudioEngine

int32_t getFinalParameterValueVolume(int32_t paramNeutralValue, int32_t patchedValue); // functions.cpp, cut out

using namespace deluge::dsp::gain;

static uint32_t rng = 12345;
static uint32_t rnd() {
	rng = rng * 1664525u + 1013904223u;
	return rng;
}
static int32_t rndRange(int32_t lo, int32_t hi) {
	return lo + (int32_t)(rnd() % (uint32_t)(hi - lo + 1));
}

constexpr double kFullScale = 16777216.0; // 2^24: the output clip
static double dbfsRms(double rms) {
	return 20.0 * std::log10(std::max(rms, 1e-30) / (kFullScale / std::sqrt(2.0)));
}

// The volume param as stored for menu value n (0..50), and the stage's post-FX volume from it (a sound's: unity at 25)
static int32_t storedForMenu(double n) {
	double v = (n - 25.0) * 2147483648.0 / 25.0;
	return (int32_t)std::clamp(v, -2147483648.0, 2147483647.0);
}
static int32_t postFXForMenu(double n) {
	return getFinalParameterValueVolume(134217728, storedForMenu(n) >> 2);
}

// v17's processReverbSendAndVolume(), per sample, as its loop computed (sums and the volume ramp wrapping)
struct V17Stage {
	int32_t lastPostReverb = -1;
	void process(StereoSample* buffer, int32_t n, int32_t* reverb, int32_t postFX, int32_t postReverb, int32_t send,
	             int32_t pan, StereoSample* addTo) {
		if (lastPostReverb == -1) {
			lastPostReverb = postReverb;
		}
		int32_t rv = multiply_32x32_rshift32(postFX, send) << 5;
		int32_t volL = multiply_32x32_rshift32(lastPostReverb, postFX) << 5;
		int32_t volR = volL;
		int32_t inc = (int32_t)((double)(postReverb - lastPostReverb) / (double)n);
		int32_t incL = multiply_32x32_rshift32(postFX, inc) << 5;
		int32_t incR = incL;
		if (pan != 0 && AudioEngine::renderInStereo) {
			int32_t aL, aR;
			shouldDoPanning(pan, &aL, &aR);
			volL = multiply_32x32_rshift32(volL, aL) << 2;
			volR = multiply_32x32_rshift32(volR, aR) << 2;
			incL = multiply_32x32_rshift32(incL, aL) << 2;
			incR = multiply_32x32_rshift32(incR, aR) << 2;
		}
		StereoSample* out = addTo ? addTo : buffer;
		for (int32_t i = 0; i < n; i++) {
			StereoSample s = buffer[i];
			if (send) {
				reverb[i] = (int32_t)((uint32_t)reverb[i]
				                      + ((uint32_t)multiply_32x32_rshift32((int32_t)((uint32_t)s.l + (uint32_t)s.r), rv)
				                         << 1));
			}
			volL = (int32_t)((uint32_t)volL + (uint32_t)incL);
			volR = (int32_t)((uint32_t)volR + (uint32_t)incR);
			int32_t oL = multiply_32x32_rshift32(s.l, volL) << 5;
			int32_t oR = multiply_32x32_rshift32(s.r, volR) << 5;
			if (addTo) {
				out[i].l = (int32_t)((uint32_t)out[i].l + (uint32_t)oL);
				out[i].r = (int32_t)((uint32_t)out[i].r + (uint32_t)oR);
			}
			else {
				out[i].l = oL;
				out[i].r = oR;
			}
		}
		lastPostReverb = postReverb;
	}
};

// The gain v17 / v18 aim at per side for a block (v17's formula; v18 takes it as the target)
static void targetGains(int32_t postFX, int32_t postReverb, int32_t pan, int32_t* gL, int32_t* gR) {
	*gL = *gR = multiply_32x32_rshift32(postReverb, postFX) << 5;
	if (pan != 0) {
		int32_t aL, aR;
		shouldDoPanning(pan, &aL, &aR);
		*gL = multiply_32x32_rshift32(*gL, aL) << 2;
		*gR = multiply_32x32_rshift32(*gR, aR) << 2;
	}
}

static void sine(StereoSample* buf, int32_t n, double peak, double freq, int64_t& t) {
	for (int32_t i = 0; i < n; i++, t++) {
		double v = peak * std::sin(2 * M_PI * freq * (double)t / 44100.0);
		buf[i].l = (int32_t)std::lround(v);
		buf[i].r = (int32_t)std::lround(v * 0.7);
	}
}

// ---- ramp
static int testRamp() {
	int fails = 0;
	double worst = 0;
	for (int c = 0; c < 200000; c++) {
		int32_t start = (int32_t)(rnd() >> 1);
		int32_t end = (c % 5 == 0) ? start : (int32_t)(rnd() >> 1);
		if (c % 7 == 0) {
			end = 0;
		}
		int32_t n = rndRange(1, 128);
		RampSteps r(start, end, n);
		int32_t i = 0;
		int32_t g[132];
		for (; i + 4 <= n; i += 4) {
			int32x4_t v = r.next4();
			vst1q_s32(&g[i], v);
		}
		for (; i < n; i++) {
			g[i] = r.next();
		}
		for (int32_t k = 0; k < n; k++) {
			double ideal = start + ((double)end - start) * (k + 1) / n;
			double e = std::fabs(g[k] - ideal);
			worst = std::max(worst, e);
		}
		if (g[n - 1] != end) {
			fails++;
		}
	}
	printf("RampSteps: 200000 blocks, last sample != target: %d, worst distance from the straight line %.3f LSB\n",
	       fails, worst);
	if (worst > 0.5001) {
		fails++;
	}
	// GainRamp: the blocks join (a block starts where the last ended) and a small step lands in its own block
	GainRamp gr;
	int32_t joinFails = 0, landFails = 0;
	int32_t last = gr.advance(1 << 27, 100);
	for (int b = 0; b < 20000; b++) {
		int32_t target = (int32_t)std::min<int64_t>((int64_t)last * (900 + rndRange(0, 300)) / 1000, INT32_MAX);
		if (b % 97 == 0) {
			target = rndRange(0, 1 << 29);
		}
		int32_t n = rndRange(20, 128);
		int32_t before = gr.current();
		if (before != last) {
			joinFails++;
		}
		last = gr.advance(target, n);
		if (!isBigStep(before, target) && last != target) {
			landFails++;
		}
	}
	printf("GainRamp: 20000 blocks, not joined: %d, small steps not on target at the block's end: %d\n", joinFails,
	       landFails);
	fails += joinFails + landFails;
	printf("%s\n", fails ? "FAIL" : "ok");
	return fails != 0;
}

// ---- neutral
static int testNeutral() {
	int fails = 0;
	constexpr int32_t n = 128;
	StereoSample in[n], buf[n], out[n], acc[n];
	int32_t reverb[n] = {};
	int64_t t = 0;
	ModControllableAudio stage;
	int32_t unity = postFXForMenu(25);
	printf("volume 25: post-FX volume %d (2^27 = %d)\n", unity, 1 << 27);
	int64_t differing = 0;
	for (int b = 0; b < 200; b++) {
		for (int32_t i = 0; i < n; i++) {
			// (below 2^30, 36 dB over the output's clip: unity is the multiplier 2^31 - 1, exact up to there)
			in[i].l = (int32_t)rnd() >> rndRange(2, 12);
			in[i].r = (int32_t)rnd() >> rndRange(2, 12);
			acc[i].l = (int32_t)rnd() >> 4;
			acc[i].r = (int32_t)rnd() >> 4;
		}
		memcpy(buf, in, sizeof(in));
		stage.processReverbSendAndVolume(buf, n, reverb, unity, 134217728, 0, 0, true);
		memcpy(out, acc, sizeof(acc));
		memcpy(buf, in, sizeof(in));
		stage.processReverbSendAndVolume(buf, n, reverb, unity, 134217728, 0, 0, true, out);
		for (int32_t i = 0; i < n; i++) {
			// (in place, the first call wrote buf; the second one read in again)
			differing += (out[i].l != (int32_t)std::clamp<int64_t>((int64_t)acc[i].l + in[i].l, INT32_MIN, INT32_MAX));
			differing += (out[i].r != (int32_t)std::clamp<int64_t>((int64_t)acc[i].r + in[i].r, INT32_MIN, INT32_MAX));
		}
		memcpy(buf, in, sizeof(in));
		stage.processReverbSendAndVolume(buf, n, reverb, unity, 134217728, 0, 0, true);
		for (int32_t i = 0; i < n; i++) {
			differing += (buf[i].l != in[i].l) + (buf[i].r != in[i].r);
		}
	}
	printf("unity (volume 25, no ramp): %lld of %d samples differ from the input (null test)\n", (long long)differing,
	       200 * n * 4);
	fails += differing != 0;

	// Static gains: v18 within 0.5 of the exact product up to unity, 2^(s-1) above (s: the headroom shift, 1 up to
	// +6 dB, 2 up to +12 dB); v18 - v17 in [-2^(s-1), 32] (v17 truncated to 32 LSBs)
	bool worstFail = false;
	int32_t dmin = INT32_MAX, dmax = INT32_MIN;
	printf("static gains: volume, gain, headroom shift, v18's worst distance from the exact product (LSB)\n");
	for (double m : {2.0, 5.0, 10.0, 17.0, 25.0, 31.6, 35.4, 40.0, 50.0}) {
		ModControllableAudio s18;
		V17Stage s17;
		int32_t pf = postFXForMenu(m);
		int32_t g = multiply_32x32_rshift32(134217728, pf) << 5;
		int32_t sh = headroomShift<kVolumeShift>(g);
		double worst = 0;
		for (int b = 0; b < 40; b++) {
			sine(in, n, 0.5 * kFullScale, 997, t);
			memcpy(buf, in, sizeof(in));
			s18.processReverbSendAndVolume(buf, n, reverb, pf, 134217728, 0, 0, true);
			memcpy(out, in, sizeof(in));
			s17.process(out, n, reverb, pf, 134217728, 0, 0, nullptr);
			for (int32_t i = 0; i < n; i++) {
				double exact = (double)in[i].l * g / 134217728.0;
				worst = std::max(worst, std::fabs(buf[i].l - exact));
				dmin = std::min(dmin, buf[i].l - out[i].l);
				dmax = std::max(dmax, buf[i].l - out[i].l);
			}
		}
		double limit = sh ? (1 << (sh - 1)) + 1e-6 : 0.5 + 1e-6;
		printf("  %4.1f %+7.2f dB  s=%d  %.3f (limit %.1f)\n", m, 20 * std::log10(g / 134217728.0), sh, worst, limit);
		worstFail |= worst > limit;
	}
	printf("v18 - v17 from %d to %d LSB\n", dmin, dmax);
	fails += worstFail || dmin < -8 || dmax > 32;
	printf("%s\n", fails ? "FAIL" : "ok");
	return fails != 0;
}

// ---- precision
static int testPrecision() {
	int fails = 0;
	constexpr int32_t n = 128;
	StereoSample in[n], a[n], b[n];
	int32_t reverb[n] = {};
	printf("%-7s %-9s %-9s | %-26s | %-26s\n", "volume", "gain dB", "input", "v17 error dBFS / SNR dB",
	       "v18 error dBFS / SNR dB");
	double worst18 = -1000;
	for (double m : {1.0, 2.5, 5.0, 12.5, 25.0, 50.0}) {
		for (double inDb : {-6.0, -40.0, -80.0}) {
			ModControllableAudio s18;
			V17Stage s17;
			int32_t pf = postFXForMenu(m);
			int32_t g = multiply_32x32_rshift32(134217728, pf) << 5;
			double e17 = 0, e18 = 0, sig = 0;
			int64_t t = 0;
			int32_t cnt = 0;
			for (int blk = 0; blk < 80; blk++) {
				sine(in, n, kFullScale * std::pow(10.0, inDb / 20.0), 997, t);
				memcpy(a, in, sizeof(in));
				memcpy(b, in, sizeof(in));
				s18.processReverbSendAndVolume(a, n, reverb, pf, 134217728, 0, 0, true);
				s17.process(b, n, reverb, pf, 134217728, 0, 0, nullptr);
				for (int32_t i = 0; i < n; i++) {
					double exact = (double)in[i].l * g / 134217728.0;
					e18 += (a[i].l - exact) * (a[i].l - exact);
					e17 += (b[i].l - exact) * (b[i].l - exact);
					sig += exact * exact;
					cnt++;
				}
			}
			double r17 = std::sqrt(e17 / cnt), r18 = std::sqrt(e18 / cnt), rs = std::sqrt(sig / cnt);
			printf("%-7.1f %-+9.2f %-+9.0f | %8.1f / %6.1f         | %8.1f / %6.1f\n", m, 40 * std::log10(m / 25.0),
			       inDb, dbfsRms(r17), 20 * std::log10(rs / r17), dbfsRms(r18), 20 * std::log10(rs / r18));
			worst18 = std::max(worst18, dbfsRms(r18));
		}
	}
	printf("v18 worst error %.1f dBFS (rounded to 1 LSB up to unity gain, 2^s above; 1 LSB = 0.5 LSB of the 24-bit "
	       "output)\n",
	       worst18);
	fails += worst18 > -138;
	printf("%s\n", fails ? "FAIL" : "ok");
	return fails != 0;
}

// ---- zipper: automation of the volume, the pan and the sidechain moving at once
static int testZipper() {
	int fails = 0;
	constexpr int32_t total = 44100;
	ModControllableAudio s18;
	V17Stage s17;
	StereoSample in[128], a[128], b[128];
	int32_t reverb[128] = {};
	int64_t t = 0;
	int32_t done = 0;
	double e17 = 0, e18 = 0;
	int32_t cnt = 0;
	double lastIdealL = -1, lastIdealR = -1;
	double maxStep17 = 0, maxStep18 = 0;
	double lastG17 = -1;
	while (done < total) {
		int32_t n = rndRange(60, 128);
		double x = (double)(done + n) / total; // where the automation has got to at this block's end
		int32_t pf = postFXForMenu(10 + 30 * x);
		int32_t pan = (int32_t)((x * 2 - 1) * 1073741823.0);
		// the sidechain: ducking by up to 12 dB at 4 Hz
		double duck = 0.5 + 0.5 * std::cos(2 * M_PI * 4 * (done + n) / 44100.0);
		int32_t pr = (int32_t)(134217728.0 * std::pow(10.0, -12.0 * duck / 20.0));
		int32_t gL, gR;
		targetGains(pf, pr, pan >> 1, &gL, &gR);
		sine(in, n, 0.1 * kFullScale, 1000, t);
		memcpy(a, in, sizeof(StereoSample) * n);
		memcpy(b, in, sizeof(StereoSample) * n);
		s18.processReverbSendAndVolume(a, n, reverb, pf, pr, 0, pan >> 1, true);
		s17.process(b, n, reverb, pf, pr, 0, pan >> 1, nullptr);
		if (lastIdealL < 0) {
			lastIdealL = gL;
			lastIdealR = gR;
		}
		for (int32_t i = 0; i < n; i++) {
			// ideal: the gain linear from the last block's target to this one's
			double gi = lastIdealL + (gL - lastIdealL) * (i + 1) / n;
			double exact = (double)in[i].l * gi / 134217728.0;
			if (done > 2000) { // (after v17's first blocks)
				e18 += (a[i].l - exact) * (a[i].l - exact);
				e17 += (b[i].l - exact) * (b[i].l - exact);
				cnt++;
			}
		}
		// the gain v17 applied per sample: its steps where the block starts (volume and pan set per block)
		double g17start = (double)(multiply_32x32_rshift32(s17.lastPostReverb, pf) << 5);
		(void)g17start;
		lastIdealL = gL;
		lastIdealR = gR;
		done += n;
	}
	// the largest step of the gain from one sample to the next, in dB: v17 at a block's start, v18 (a straight line)
	{
		ModControllableAudio s;
		V17Stage v;
		StereoSample one[128];
		double prev17 = -1, prev18 = -1;
		int32_t d = 0;
		rng = 999;
		while (d < total) {
			int32_t n = rndRange(60, 128);
			double x = (double)(d + n) / total;
			int32_t pf = postFXForMenu(10 + 30 * x);
			int32_t pan = (int32_t)((x * 2 - 1) * 1073741823.0);
			double duck = 0.5 + 0.5 * std::cos(2 * M_PI * 4 * (d + n) / 44100.0);
			int32_t pr = (int32_t)(134217728.0 * std::pow(10.0, -12.0 * duck / 20.0));
			for (int32_t i = 0; i < n; i++) {
				one[i].l = one[i].r = 1 << 24; // the gain itself, at 2^24 = 1: out = gain * 2^24 / 2^27
			}
			memcpy(a, one, sizeof(StereoSample) * n);
			memcpy(b, one, sizeof(StereoSample) * n);
			s.processReverbSendAndVolume(a, n, reverb, pf, pr, 0, pan >> 1, true);
			v.process(b, n, reverb, pf, pr, 0, pan >> 1, nullptr);
			for (int32_t i = 0; i < n; i++) {
				if (prev18 > 0 && a[i].l > 0) {
					maxStep18 = std::max(maxStep18, std::fabs(20 * std::log10(a[i].l / prev18)));
				}
				if (prev17 > 0 && b[i].l > 0) {
					maxStep17 = std::max(maxStep17, std::fabs(20 * std::log10(b[i].l / prev17)));
				}
				prev18 = a[i].l;
				prev17 = b[i].l;
			}
			d += n;
		}
	}
	(void)lastG17;
	double r17 = std::sqrt(e17 / cnt), r18 = std::sqrt(e18 / cnt);
	printf("volume 10 -> 40, pan -25 -> +25 and the sidechain (12 dB at 4 Hz) at once, blocks of 60..128, a 1 kHz sine "
	       "at -20 dBFS:\n");
	printf("  output against the gain linear between the blocks' targets: v17 %.1f dBFS, v18 %.1f dBFS\n",
	       dbfsRms(r17), dbfsRms(r18));
	printf("  largest gain step between two samples (left): v17 %.3f dB, v18 %.4f dB\n", maxStep17, maxStep18);
	fails += dbfsRms(r18) > -130;
	printf("%s\n", fails ? "FAIL" : "ok");
	return fails != 0;
}

// ---- bigstep
static int testBigStep() {
	int fails = 0;
	for (int32_t blockLen : {32, 64, 128}) {
		for (int dir = 0; dir < 2; dir++) {
			ModControllableAudio s;
			StereoSample buf[128];
			int32_t reverb[128] = {};
			int32_t from = dir ? postFXForMenu(25) : postFXForMenu(0);
			int32_t to = dir ? postFXForMenu(0) : postFXForMenu(25);
			for (int32_t i = 0; i < blockLen; i++) {
				buf[i].l = buf[i].r = 1 << 24;
			}
			s.processReverbSendAndVolume(buf, blockLen, reverb, from, 134217728, 0, 0, true);
			double prev = buf[blockLen - 1].l;
			int32_t samples = 0, reached = -1;
			double maxStep = 0;
			for (int b = 0; b < 12; b++) {
				for (int32_t i = 0; i < blockLen; i++) {
					buf[i].l = buf[i].r = 1 << 24;
				}
				s.processReverbSendAndVolume(buf, blockLen, reverb, to, 134217728, 0, 0, true);
				for (int32_t i = 0; i < blockLen; i++) {
					samples++;
					maxStep = std::max(maxStep, std::fabs(buf[i].l - prev));
					prev = buf[i].l;
					if (reached < 0 && buf[i].l == (dir ? 0 : (1 << 24))) {
						reached = samples;
					}
				}
			}
			// full move = 2^24 here; the steps may be one LSB over the straight line of 221 samples
			double limit = (1 << 24) / 221.0 + 2;
			printf("volume %s in blocks of %3d: there after %d samples (%.1f ms), largest step %.0f (%.2f %% of the move; "
			       "v17: 100 %%)\n",
			       dir ? "25 -> 0" : "0 -> 25", blockLen, reached, reached / 44.1, maxStep, maxStep / (1 << 24) * 100);
			fails += reached < kMinRampSamples || maxStep > limit;
		}
	}
	printf("%s\n", fails ? "FAIL" : "ok");
	return fails != 0;
}

// ---- pan
static int testPan() {
	int fails = 0;
	constexpr int32_t n = 100;
	int32_t st[2 * n], mono[2 * n];
	for (int c = 0; c < 2000; c++) {
		int32_t sL = rndRange(0, 1073741823), eL = rndRange(0, 1073741823);
		int32_t sR = rndRange(0, 1073741823), eR = rndRange(0, 1073741823);
		int32_t len = rndRange(1, n);
		for (int32_t i = 0; i < len; i++) {
			int32_t v = (int32_t)rnd() >> 2;
			mono[i] = v;
			st[2 * i] = st[2 * i + 1] = v;
		}
		panInPlace(st, len, sL, eL, sR, eR);
		panMonoToStereo(mono, len, sL, eL, sR, eR);
		fails += memcmp(st, mono, sizeof(int32_t) * 2 * len) != 0;
	}
	printf("mono -> stereo against stereo on the doubled input: %d of 2000 blocks differ\n", fails);
	printf("%s\n", fails ? "FAIL" : "ok");
	return fails != 0;
}

// ---- cpu
static int testCpu() {
	constexpr int32_t n = 128;
	static StereoSample buf[n], out[n];
	static int32_t reverb[n];
	int64_t t = 0;
	sine(buf, n, 0.1 * kFullScale, 1000, t);
	ModControllableAudio s;
	int32_t pf = postFXForMenu(20);
	for (int k = 0; k < 4; k++) {
		s.processReverbSendAndVolume(buf, n, reverb, pf, 134217728, 0, 0, true, out);
	}
	EMU_COUNT_BEGIN("v18 steady, adding into the output");
	s.processReverbSendAndVolume(buf, n, reverb, pf, 134217728, 0, 0, true, out);
	EMU_COUNT_END();
	ModControllableAudio s2;
	for (int k = 0; k < 4; k++) {
		s2.processReverbSendAndVolume(buf, n, reverb, pf, 134217728, 1 << 28, 0, true, out);
	}
	EMU_COUNT_BEGIN("v18 steady, send, adding");
	s2.processReverbSendAndVolume(buf, n, reverb, pf, 134217728, 1 << 28, 0, true, out);
	EMU_COUNT_END();
	EMU_COUNT_BEGIN("v18 ramping (sidechain), send, adding");
	s2.processReverbSendAndVolume(buf, n, reverb, pf, 120000000, 1 << 28, 0, true, out);
	EMU_COUNT_END();
	ModControllableAudio s3;
	for (int k = 0; k < 4; k++) {
		s3.processReverbSendAndVolume(buf, n, reverb, pf, 134217728, 0, 0, true, out);
	}
	EMU_COUNT_BEGIN("v18 ramping (sidechain), no send, adding");
	s3.processReverbSendAndVolume(buf, n, reverb, pf, 120000000, 0, 0, true, out);
	EMU_COUNT_END();
#ifdef V17_KERNELS
	using namespace deluge::dsp::track_fx;
	EMU_COUNT_BEGIN("v17 loop, adding");
	reverbSendAndVolume<false, true>(buf, out, n, reverb, 0, 1 << 27, 1 << 27, 0, 0);
	EMU_COUNT_END();
	EMU_COUNT_BEGIN("v17 loop, send, adding");
	reverbSendAndVolume<true, true>(buf, out, n, reverb, 1 << 27, 1 << 27, 1 << 27, 0, 0);
	EMU_COUNT_END();
	EMU_COUNT_BEGIN("v17 loop, ramping, send, adding");
	reverbSendAndVolume<true, true>(buf, out, n, reverb, 1 << 27, 1 << 27, 1 << 27, 1000, 1000);
	EMU_COUNT_END();
#endif
	printf("(counts per call of 128 samples below)\n");
	return 0;
}

int main(int argc, char** argv) {
	const char* c = argc > 1 ? argv[1] : "ramp";
	if (!strcmp(c, "ramp")) {
		return testRamp();
	}
	if (!strcmp(c, "neutral")) {
		return testNeutral();
	}
	if (!strcmp(c, "precision")) {
		return testPrecision();
	}
	if (!strcmp(c, "zipper")) {
		return testZipper();
	}
	if (!strcmp(c, "bigstep")) {
		return testBigStep();
	}
	if (!strcmp(c, "pan")) {
		return testPan();
	}
	if (!strcmp(c, "cpu")) {
		return testCpu();
	}
	printf("unknown case %s\n", c);
	return 2;
}
