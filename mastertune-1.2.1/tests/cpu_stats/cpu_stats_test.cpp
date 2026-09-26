// Host test of the CPU monitor core (mastertune-v12-diag): the collector with a simulated timer (including a wrap of
// the 32-bit counter), merging, the OLED line and the SysEx encoding. Writes the SysEx test cases to the file given
// as argument, which decode_test.js decodes with the code of tools/cpu_monitor.html.
//
// Build: see run.sh (links the firmware's cpu_stats_core.cpp and lib/printf.c)

#include "processing/engines/cpu_stats_core.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>

using namespace cpu_stats;

extern "C" void putchar_(char c) {
	putchar(c);
}

static int failures = 0;

#define CHECK(cond)                                                                                                    \
	do {                                                                                                               \
		if (!(cond)) {                                                                                                 \
			printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                                                     \
			failures++;                                                                                                \
		}                                                                                                              \
	} while (0)

#define CHECK_NEAR(a, b, tol)                                                                                          \
	do {                                                                                                               \
		double va = (double)(a), vb = (double)(b);                                                                     \
		if (std::fabs(va - vb) > (tol)) {                                                                              \
			printf("FAIL %s:%d: %s = %.3f, expected %.3f (+-%g)\n", __FILE__, __LINE__, #a, va, vb, (double)(tol));   \
			failures++;                                                                                                \
		}                                                                                                              \
	} while (0)

constexpr double kTicksPerSample = (double)kTicksPerSecond / kAudioSampleRate;

static uint32_t ticksFor(double seconds) {
	return (uint32_t)std::llround(seconds * kTicksPerSecond);
}

/// A steady audio routine: every call renders `blockSamples` and takes `load` of that audio's duration.
/// One call per half second is `peakLoad`, one call per second comes 5 ms late, 10 % of the blocks run with
/// direness 3, 3 voices get culled and 11 clusters are read per second. Starts just before the timer wraps.
static void testSteadyLoad() {
	Collector c;
	uint32_t now = UINT32_MAX - ticksFor(0.3); // wraps 0.3 s in
	uint32_t sampleCounter = 123456789;
	const uint32_t blockSamples = 16;
	const double load = 0.30;
	const double peakLoad = 0.60;
	const uint32_t interval = (uint32_t)std::llround(blockSamples * kTicksPerSample);
	uint32_t halfSeq = 0, secondSeq = 0;
	Window halves[8];
	Window seconds[4];
	int numHalves = 0, numSeconds = 0;

	for (int call = 0; numSeconds < 3; call++) {
		int inSecond = call % 2756; // ~1 s of 16-sample calls
		uint32_t start = now;
		if (inSecond == 1000) {
			start += ticksFor(0.005); // 5 ms late
		}
		bool peak = (call % 1378) == 500;
		double thisLoad = peak ? peakLoad : load;
		uint32_t busy = (uint32_t)std::llround(blockSamples * kTicksPerSample * thisLoad);

		c.routineStart(start, sampleCounter);
		sampleCounter += blockSamples;
		int direness = (call % 10 == 0) ? 3 : 0;
		uint32_t voices = (inSecond == 2000) ? 30 : 24;
		c.routineDone(start + busy, sampleCounter, voices, direness);
		if (inSecond == 100 || inSecond == 101 || inSecond == 102) {
			c.voiceCulled();
		}
		if (inSecond % 250 == 0 && inSecond < 2750) {
			c.clusterLoaded(ticksFor(inSecond == 0 ? 0.009 : 0.001));
		}
		now = start + interval;

		Window w;
		if (c.readHalf(w, halfSeq) && numHalves < 8) {
			halves[numHalves++] = w;
		}
		if (c.readSecond(w, secondSeq)) {
			seconds[numSeconds++] = w;
		}
	}

	CHECK(numHalves >= 6);
	// A second is exactly the merge of its two halves
	for (int s = 0; s < 3; s++) {
		Window m;
		merge(m, halves[2 * s]);
		merge(m, halves[2 * s + 1]);
		CHECK(memcmp(&m, &seconds[s], sizeof(Window)) == 0);
	}

	// The middle second is a clean one: check every number
	Summary s = summarize(seconds[1]);
	printf("steady: window %u ms, avg %u, peak %u permille, voices %u/%u, dire %u (%u permille), culled %u, "
	       "sd %u x %u us (max %u), gap %u us, samples %u\n",
	       s.windowMs, s.dspAvgPermille, s.dspPeakPermille, s.voicesNow, s.voicesMax, s.direMax, s.direSharePermille,
	       s.culled, s.sdLoads, s.sdAvgUs, s.sdMaxUs, s.maxGapUs, s.samples);
	CHECK_NEAR(s.windowMs, 1000, 8);
	CHECK_NEAR(s.dspAvgPermille, 300, 2);
	CHECK_NEAR(s.dspPeakPermille, 600, 2);
	CHECK(s.voicesNow == 24);
	CHECK(s.voicesMax == 30);
	CHECK(s.direMax == 3);
	CHECK_NEAR(s.direSharePermille, 100, 2);
	CHECK(s.culled == 3);
	CHECK(s.sdLoads == 11);
	CHECK_NEAR(s.sdAvgUs, (9000.0 + 10 * 1000.0) / 11, 1);
	CHECK_NEAR(s.sdMaxUs, 9000, 1);
	CHECK_NEAR(s.maxGapUs, 5000 + interval / (kTicksPerSecond / 1e6), 1);
	CHECK_NEAR(s.samples, (s.windowMs - 5) * 44.1, 60); // the 5 ms late call rendered nothing extra
}

/// Stopping and starting again throws away the old window and doesn't count the pause as a gap
static void testRestart() {
	Collector c;
	uint32_t halfSeq = 0;
	Window w;
	c.routineStart(1000, 0);
	c.routineDone(2000, 32, 5, 0);
	c.stop();
	CHECK(!c.running());
	c.routineStart(1000 + ticksFor(30), 32); // 30 s later
	CHECK(c.running());
	uint32_t t = 1000 + ticksFor(30);
	for (int i = 0; i < 2000; i++) {
		c.routineStart(t, 32 + 16 * i);
		c.routineDone(t + 100, 32 + 16 * (i + 1), 1, 0);
		t += 12093;
	}
	CHECK(c.readHalf(w, halfSeq));
	Summary s = summarize(w);
	CHECK(s.maxGapUs < 4000);
	CHECK(s.voicesMax == 1);
}

/// A call that rendered under 16 samples doesn't set the peak, but counts for the average
static void testSmallCalls() {
	Collector c;
	uint32_t halfSeq = 0;
	Window w;
	uint32_t t = 0, n = 0;
	for (int i = 0; i < 1500; i++) {
		c.routineStart(t, n);
		n += (i % 2) ? 4 : 32;
		uint32_t busy = (i % 2) ? 4 * 755 * 3 : 32 * 755 / 2; // small calls: 300 %, big ones: 50 %
		c.routineDone(t + busy, n, 1, 0);
		t += 24000;
	}
	CHECK(c.readHalf(w, halfSeq));
	Summary s = summarize(w);
	CHECK_NEAR(s.dspPeakPermille, 500, 2);
	CHECK(s.dspAvgPermille > 600);
}

static void testLine() {
	char line[32];
	Summary s{};
	s.dspAvgPermille = 431;
	s.dspPeakPermille = 712;
	s.voicesNow = 24;
	s.direMax = 0;
	s.sdLoads = 5;
	s.sdAvgUs = 2449;
	s.sdMaxUs = 8800;
	formatLine(s, line, sizeof(line));
	printf("line: \"%s\"\n", line);
	CHECK(strcmp(line, "C43/71% V24 D0 S2.4/9") == 0);

	s.sdLoads = 0;
	formatLine(s, line, sizeof(line));
	CHECK(strcmp(line, "C43/71% V24 D0 S-") == 0);

	s.dspAvgPermille = 1004;
	s.dspPeakPermille = 2500;
	s.voicesNow = 128;
	s.direMax = 14;
	s.sdLoads = 3;
	s.sdAvgUs = 12400;
	s.sdMaxUs = 140000;
	formatLine(s, line, sizeof(line));
	printf("line: \"%s\"\n", line);
	CHECK(strcmp(line, "C100/250 V128 D14") == 0); // "S140" doesn't fit any more

	char seg[8];
	s.dspAvgPermille = 431;
	formatSevenSegment(s, seg, sizeof(seg));
	CHECK(strcmp(seg, "C 43") == 0);
	s.dspAvgPermille = 99999;
	formatSevenSegment(s, seg, sizeof(seg));
	CHECK(strcmp(seg, "C999") == 0);

	// Whatever the numbers, the line fits
	std::mt19937 rng(7);
	auto pick = [&](uint32_t max) {
		uint32_t digits = rng() % 10;
		uint64_t v = rng();
		for (uint32_t i = 0; i < digits; i++) {
			v = v * 10 + rng() % 10;
		}
		return (uint32_t)(v % ((uint64_t)max + 1));
	};
	for (int i = 0; i < 200000; i++) {
		Summary r{};
		r.dspAvgPermille = pick(UINT32_MAX);
		r.dspPeakPermille = pick(UINT32_MAX);
		r.voicesNow = pick(5000);
		r.direMax = pick(20);
		r.sdLoads = pick(3);
		r.sdAvgUs = pick(UINT32_MAX);
		r.sdMaxUs = pick(UINT32_MAX);
		formatLine(r, line, sizeof(line));
		if (strlen(line) > kLineChars || line[0] != 'C') {
			printf("FAIL line too long: \"%s\"\n", line);
			failures++;
			break;
		}
	}
}

static uint32_t sat(uint32_t v, int bytes) {
	uint32_t max = (1u << (7 * bytes)) - 1;
	return v > max ? max : v;
}

static void writeCase(FILE* f, const Summary& s, uint32_t seq, bool first) {
	uint8_t msg[kSysexLength + 8];
	memset(msg, 0xEE, sizeof(msg));
	size_t len = encodeSysex(s, seq, msg);
	CHECK(len == kSysexLength);
	CHECK(msg[len] == 0xEE); // nothing written past the end
	CHECK(msg[0] == 0xF0 && msg[1] == 0x00 && msg[2] == 0x21 && msg[3] == 0x7B && msg[4] == 0x01);
	CHECK(msg[5] == kSysexCommand);
	CHECK(msg[len - 1] == 0xF7);
	for (size_t i = 1; i + 1 < len; i++) {
		CHECK(msg[i] < 0x80);
	}
	fprintf(f, "%s\n{\"bytes\":[", first ? "" : ",");
	for (size_t i = 0; i < len; i++) {
		fprintf(f, "%s%u", i ? "," : "", msg[i]);
	}
	fprintf(f,
	        "],\"expected\":{\"version\":%u,\"seq\":%u,\"windowMs\":%u,\"dspAvgPermille\":%u,\"dspPeakPermille\":%u,"
	        "\"voicesNow\":%u,\"voicesMax\":%u,\"direMax\":%u,\"direSharePermille\":%u,\"culled\":%u,\"sdLoads\":%u,"
	        "\"sdAvgUs\":%u,\"sdMaxUs\":%u,\"maxGapUs\":%u,\"samples\":%u}}",
	        kSysexFormatVersion, seq & 0x3FFF, sat(s.windowMs, 2), sat(s.dspAvgPermille, 2), sat(s.dspPeakPermille, 2),
	        sat(s.voicesNow, 2), sat(s.voicesMax, 2), sat(s.direMax, 1), sat(s.direSharePermille, 2), sat(s.culled, 2),
	        sat(s.sdLoads, 2), sat(s.sdAvgUs, 4), sat(s.sdMaxUs, 4), sat(s.maxGapUs, 4), sat(s.samples, 4));
}

static void testSysex(const char* path) {
	FILE* f = fopen(path, "w");
	if (!f) {
		printf("FAIL cannot write %s\n", path);
		failures++;
		return;
	}
	fprintf(f, "[");
	Summary zero{};
	writeCase(f, zero, 0, true);
	Summary big;
	memset(&big, 0xFF, sizeof(big)); // everything saturates
	writeCase(f, big, 0xFFFFFFFF, false);
	Summary typical{1000, 431, 712, 24, 30, 3, 100, 3, 11, 1727, 9000, 5363, 44100};
	writeCase(f, typical, 16384 + 5, false);
	std::mt19937 rng(42);
	for (int i = 0; i < 500; i++) {
		Summary r;
		uint32_t* fields = reinterpret_cast<uint32_t*>(&r);
		for (size_t k = 0; k < sizeof(Summary) / sizeof(uint32_t); k++) {
			fields[k] = rng() >> (rng() % 32);
		}
		writeCase(f, r, rng(), false);
	}
	fprintf(f, "\n]\n");
	fclose(f);
}

int main(int argc, char** argv) {
	testSteadyLoad();
	testRestart();
	testSmallCalls();
	testLine();
	testSysex(argc > 1 ? argv[1] : "cpu_stats_cases.json");
	if (failures) {
		printf("%d FAILED\n", failures);
		return 1;
	}
	printf("cpu_stats core: all ok\n");
	return 0;
}
