// Simulates UsbAudioStream: the Deluge's audio routine (producer, bursts on the codec clock) against USB frames
// (consumer, on the computer's clock), with clock drift, a host that stops reading, and an engine stall.
#include "io/usb/usb_audio_stream.h"
#include <cmath>
#include <cstdio>
#include <random>
#include <vector>

static UsbAudioStream* sp = nullptr;
#define s (*sp)
static int failures = 0;
#define CHECK(c, ...) do { if (!(c)) { failures++; printf("FAIL: "); printf(__VA_ARGS__); printf("\n"); } else { printf("ok   "); printf(__VA_ARGS__); printf("\n"); } } while (0)

struct Result { uint64_t produced = 0, consumed = 0; int minLevel = 1 << 30, maxLevel = 0; uint32_t sizes[64] = {}; uint32_t silent, dropped, resyncs; double lastT; bool orderOk = true; uint32_t jumps = 0; };

// Producer writes a running frame counter into the samples, so the consumer can check order and gaps
static Result run(double devicePpm, double hostPpm, double seconds, double hostPauseAt = -1, double hostPauseLen = 0,
                  double engineStallAt = -1, double engineStallLen = 0, unsigned seed = 1) {
	delete sp;
	sp = new UsbAudioStream();
	s.start();
	std::mt19937 rng(seed);
	std::uniform_real_distribution<double> routineGap(0.3e-3, 2.5e-3), isrJitter(0, 30e-6);
	double fsDevice = 44100.0 * (1 + devicePpm * 1e-6), usbPeriod = 1e-3 / (1 + hostPpm * 1e-6);
	double tRoutine = 0, tUsb = 0.5e-3 + isrJitter(rng);
	double codecPos = 0; uint64_t pushedFrames = 0; uint64_t expectNext = 0; bool started = false;
	Result r;
	std::vector<int32_t> buf(2 * 256);
	uint8_t pkt[UsbAudioStream::kMaxPacketBytes];
	while (true) {
		double t = std::min(tRoutine, tUsb);
		if (t > seconds) break;
		if (tRoutine <= tUsb) {
			bool stalled = engineStallAt >= 0 && t >= engineStallAt && t < engineStallAt + engineStallLen;
			if (!stalled) {
				uint64_t codecNow = (uint64_t)(t * fsDevice);
				uint64_t n = codecNow - (uint64_t)codecPos;
				if (n > 128) n = 128; // the TX ring holds 128 frames; after a stall the codec skipped the rest
				codecPos = (double)codecNow;
				for (uint64_t i = 0; i < n; i++) { int32_t v = (int32_t)(((pushedFrames + i) % 0x7FFFFE) + 1) << 8; buf[i * 2] = v; buf[i * 2 + 1] = -v; }
				s.push(buf.data(), (int32_t)n);
				pushedFrames += n; r.produced += n;
			}
			tRoutine = t + routineGap(rng);
		}
		else {
			bool paused = hostPauseAt >= 0 && t >= hostPauseAt && t < hostPauseAt + hostPauseLen;
			if (!paused) {
				uint32_t bytes = s.takePacket(pkt);
				uint32_t n = bytes / 6;
				r.sizes[n]++;
				r.consumed += n;
				for (uint32_t i = 0; i < n; i++) {
					int32_t l = pkt[i * 6] | pkt[i * 6 + 1] << 8 | (int8_t)pkt[i * 6 + 2] << 16;
					int32_t rr = pkt[i * 6 + 3] | pkt[i * 6 + 4] << 8 | (int8_t)pkt[i * 6 + 5] << 16;
					if (l == 0 && rr == 0) continue; // silence (prefill or rebuffering)
					uint64_t frameNo = (uint64_t)l;
					if (rr != -l) r.orderOk = false;
					if (started && frameNo != expectNext) {
						// a jump is allowed only after a resync (the frames dropped while the ring was full) or
						// rebuffering
						if (s.resyncs == 0 && s.silentFrames == 0) r.orderOk = false;
						r.jumps++;
					}
					started = true; expectNext = frameNo % 0x7FFFFE + 1;
				}
				r.resyncs = s.resyncs; r.silent = s.silentFrames;
				int lv = s.level();
				if (t > 2.0 && !(hostPauseAt >= 0 && t < hostPauseAt + hostPauseLen + 2) && !(engineStallAt >= 0 && t < engineStallAt + engineStallLen + 2)) {
					if (lv < r.minLevel) r.minLevel = lv;
					if (lv > r.maxLevel) r.maxLevel = lv;
				}
			}
			tUsb += usbPeriod; tUsb += isrJitter(rng) - 15e-6;
		}
	}
	r.silent = s.silentFrames; r.dropped = s.droppedFrames; r.resyncs = s.resyncs;
	return r;
}

int main() {
	// Byte format: 24 bits little endian from the top of 32
	{
		UsbAudioStream t; t.start();
		std::vector<int32_t> f(2 * 300);
		for (int i = 0; i < 300; i++) { f[i * 2] = (0x123456 + i) << 8; f[i * 2 + 1] = -(0x123456 + i) * 256; }
		t.push(f.data(), 300);
		uint8_t p[UsbAudioStream::kMaxPacketBytes];
		for (int k = 0; k < 6; k++) t.takePacket(p); // 256 frames of prefill silence first
		uint32_t n = t.takePacket(p) / 6;
		int32_t l = p[0] | p[1] << 8 | (int8_t)p[2] << 16, r = p[3] | p[4] << 8 | (int8_t)p[5] << 16;
		CHECK(n >= 44 && l >= 0x123456 && l < 0x123456 + 300 && r == -l, "24-bit little-endian packing (l=%06x r=%d)", l, r);
	}
	struct Case { const char* name; double dev, host, secs, pauseAt, pauseLen, stallAt, stallLen; };
	Case cases[] = {
	    {"same clock", 0, 0, 600, -1, 0, -1, 0},
	    {"Deluge +300 ppm", 300, 0, 600, -1, 0, -1, 0},
	    {"Deluge -300 ppm", -300, 0, 600, -1, 0, -1, 0},
	    {"host +500 ppm, Deluge -500 ppm", -500, 500, 600, -1, 0, -1, 0},
	    {"host -500 ppm, Deluge +500 ppm", 500, -500, 600, -1, 0, -1, 0},
	    {"host +700 ppm, Deluge -700 ppm", -700, 700, 300, -1, 0, -1, 0},
	    {"computer stops reading 200 ms", 100, 0, 60, 20, 0.2, -1, 0},
	    {"engine stalls 20 ms", -100, 0, 60, -1, 0, 30, 0.02},
	};
	for (auto& c : cases) {
		Result r = run(c.dev, c.host, c.secs, c.pauseAt, c.pauseLen, c.stallAt, c.stallLen);
		uint32_t other = 0; for (int i = 0; i < 64; i++) if (i != 44 && i != 45) other += r.sizes[i];
		double ratio = r.sizes[45] ? (double)r.sizes[44] / r.sizes[45] : 0;
		printf("-- %s: 44:%u 45:%u other:%u, level %d..%d, silent %u, dropped %u, resyncs %u\n", c.name, r.sizes[44], r.sizes[45], other, r.minLevel, r.maxLevel, r.silent, r.dropped, r.resyncs);
		CHECK(other == 0, "%s: packets only 44 or 45 frames", c.name);
		CHECK(r.orderOk, "%s: frames in order, left/right intact", c.name);
		bool disturbed = c.pauseAt >= 0 || c.stallAt >= 0;
		if (!disturbed) {
			CHECK(r.silent == 256 * 0 + r.silent && r.silent == 0 && r.dropped == 0 && r.resyncs == 0, "%s: no silence, drops or resyncs", c.name);
			CHECK(r.minLevel > 45 && r.maxLevel < 1000, "%s: level stays between one packet and the resync limit (%d..%d)", c.name, r.minLevel, r.maxLevel);
		}
		else {
			CHECK(r.minLevel > 45, "%s: back to a safe level afterwards (min %d)", c.name, r.minLevel);
		}
		if (c.pauseAt >= 0) CHECK(r.resyncs == 1 && r.jumps == 1, "%s: exactly one resync and one jump (%u)", c.name, r.jumps);
		if (!disturbed) CHECK(r.jumps == 0, "%s: no jumps in the frame sequence", c.name);
		if (c.stallAt >= 0) CHECK(r.silent > 0 && r.silent < 44100 / 5, "%s: one short silent gap (%u frames), no clicks", c.name, r.silent);
	}
	printf("%s\n", failures ? "FAILURES" : "all stream checks passed");
	return failures ? 1 : 0;
}
