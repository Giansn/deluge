// Host test of the sampling profiler's core (patch 0053): the ring the timer interrupt fills (order, full,
// dropped samples, wrap of the indices), the context packing and the three SysEx messages. Writes the messages with
// what they hold to the JSON file given as argument; decode_test.py decodes them with tools/deluge_profiler.py and
// decode_test.js with tools/profiler.html, and both must read exactly that.
//
// Build: see run.sh (links the firmware's profiler_core.cpp)

#include "processing/engines/profiler_core.h"
#include <cstdio>
#include <random>
#include <string>
#include <vector>

using namespace profiler;

static int failures = 0;
static int checks = 0;

#define CHECK(cond)                                                                                                    \
	do {                                                                                                               \
		checks++;                                                                                                      \
		if (!(cond)) {                                                                                                 \
			printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                                                     \
			failures++;                                                                                                \
		}                                                                                                              \
	} while (0)

static std::string hex(const uint8_t* data, size_t n) {
	std::string s;
	char b[3];
	for (size_t i = 0; i < n; i++) {
		snprintf(b, sizeof(b), "%02x", data[i]);
		s += b;
	}
	return s;
}

static bool sysexShaped(const uint8_t* m, size_t n) {
	if (n < 7 || m[0] != 0xF0 || m[n - 1] != 0xF7) {
		return false;
	}
	for (size_t i = 1; i + 1 < n; i++) {
		if (m[i] & 0x80) {
			return false;
		}
	}
	return m[1] == 0x00 && m[2] == 0x21 && m[3] == 0x7B && m[4] == 0x01;
}

int main(int argc, char** argv) {
	if (argc < 2) {
		fprintf(stderr, "usage: profiler_test cases.json\n");
		return 2;
	}
	std::mt19937 rng(7);

	// Context packing
	{
		uint32_t c = packContext(5, true, false, 17, 3);
		CHECK((c & 31) == 5 && (c & 0x20) && !(c & 0x40) && ((c >> 8) & 0xFF) == 17 && ((c >> 16) & 31) == 3);
		CHECK(((packContext(kNoTask, false, true, kNoOutput, 0) >> 16) & 31) == 1);  // Weight at least 1
		CHECK(((packContext(kNoTask, false, true, kNoOutput, 500) >> 16) & 31) == 31); // At most 31
		CHECK((packContext(kNoTask, false, true, kNoOutput, 1) & 0x40) != 0);
		CHECK(packContext(0, false, false, 0, 1) < (1u << 21));
	}

	// The ring: in order, full, dropped, and over many wraps of its indices
	{
		static Ring ring; // 1024 entries: not on the stack
		Sample out[64];
		CHECK(ring.pop(out, 64) == 0);
		for (uint32_t i = 0; i < Ring::kSize; i++) {
			CHECK(ring.push({i, i & 0xFFFF}) || (printf("  push %u\n", i), false));
		}
		CHECK(!ring.push({9999, 0}));
		CHECK(!ring.push({9998, 0}));
		CHECK(ring.droppedTotal() == 2);
		CHECK(ring.size() == Ring::kSize);
		uint32_t expected = 0;
		size_t n;
		while ((n = ring.pop(out, 64)) > 0) {
			for (size_t i = 0; i < n; i++) {
				if (out[i].address != expected) {
					CHECK(out[i].address == expected);
					break;
				}
				expected++;
			}
		}
		CHECK(expected == Ring::kSize);
		// Interleaved pushes and pops, 5 million samples: the indices wrap around many times
		uint32_t next = 0, got = 0;
		bool inOrder = true;
		for (int step = 0; step < 200000; step++) {
			int pushes = (int)(rng() % 40);
			for (int i = 0; i < pushes; i++) {
				if (ring.push({next, 0})) {
					next++;
				}
			}
			size_t k = ring.pop(out, rng() % 64);
			for (size_t i = 0; i < k; i++) {
				inOrder &= (out[i].address == got++);
			}
		}
		CHECK(inOrder);
		ring.clear();
		CHECK(ring.size() == 0);
	}

	FILE* f = fopen(argv[1], "w");
	if (!f) {
		perror(argv[1]);
		return 2;
	}
	fprintf(f, "{\"samples\": [\n");

	// Samples messages: random samples, edge values
	uint8_t msg[kMaxSamplesMessageLength];
	for (int c = 0; c < 40; c++) {
		size_t n = (c == 0) ? 0 : (c == 1) ? 1 : (c == 2) ? kMaxSamplesPerMessage : (size_t)(rng() % (kMaxSamplesPerMessage + 1));
		std::vector<Sample> samples(n);
		for (size_t i = 0; i < n; i++) {
			uint32_t address = (c == 3 && i == 0) ? 0xFFFFFFFFu : (uint32_t)rng();
			samples[i] = {address, packContext(rng() % 32, rng() & 1, rng() & 1, rng() % 256, 1 + rng() % 31)};
		}
		uint32_t seq = (c == 4) ? 0x3FFF : (uint32_t)c * 977;
		uint32_t dropped = (c == 5) ? 0xFFFFFFFFu : (uint32_t)(rng() % 100000);
		size_t len = encodeSamples(samples.data(), n, seq, dropped, msg);
		CHECK(len == kSamplesHeaderLength + n * kBytesPerSample + 1);
		CHECK(len <= kMaxSamplesMessageLength);
		CHECK(sysexShaped(msg, len));
		fprintf(f, "%s{\"hex\": \"%s\", \"seq\": %u, \"dropped\": %u, \"samples\": [", c ? ",\n" : "",
		        hex(msg, len).c_str(), seq & 0x3FFF, dropped > 0x1FFFFF ? 0x1FFFFF : dropped);
		for (size_t i = 0; i < n; i++) {
			uint32_t x = samples[i].context;
			fprintf(f, "%s[%u, %u, %u, %u, %u, %u]", i ? ", " : "", samples[i].address, x & 31, (x >> 5) & 1, (x >> 6) & 1,
			        (x >> 8) & 0xFF, (x >> 16) & 31);
		}
		fprintf(f, "]}");
	}
	fprintf(f, "\n], \"names\": [\n");

	// Names messages: short, long (cut to kMaxNameLength), unprintable characters, indices above 127, a full message
	{
		const char* names[40];
		uint8_t indices[40];
		std::vector<std::string> store(40);
		for (int i = 0; i < 40; i++) {
			store[i] = "S track " + std::to_string(i);
			if (i == 3) {
				store[i] = "A name longer than twenty-four characters";
			}
			if (i == 5) {
				store[i] = std::string("K caf") + (char)0xE9 + "\t!";
			}
			names[i] = store[i].c_str();
			indices[i] = (uint8_t)(i == 6 ? 200 : i);
		}
		names[7] = nullptr; // Left out
		uint8_t nm[kMaxNamesMessageLength];
		size_t sizes[] = {kMaxNamesMessageLength, 60, 9};
		for (int c = 0; c < 3; c++) {
			size_t len = encodeNames(1, names, indices, 40, nm, sizes[c]);
			CHECK(len <= sizes[c]);
			CHECK(sysexShaped(nm, len));
			size_t count = nm[7];
			fprintf(f, "%s{\"hex\": \"%s\", \"which\": \"outputs\", \"names\": {", c ? ",\n" : "", hex(nm, len).c_str());
			size_t written = 0;
			for (int i = 0; i < 40 && written < count; i++) {
				if (names[i] == nullptr) {
					continue;
				}
				std::string s(names[i]);
				if (s.size() > kMaxNameLength) {
					s.resize(kMaxNameLength);
				}
				for (char& ch : s) {
					if (ch < 0x20 || ch > 0x7E) {
						ch = '?';
					}
				}
				fprintf(f, "%s\"%u\": \"%s\"", written ? ", " : "", indices[i], s.c_str());
				written++;
			}
			fprintf(f, "}}");
		}
		CHECK(encodeNames(0, names, indices, 40, nm, 8) == 0); // Not even the frame fits
	}
	fprintf(f, "\n], \"output_times\": [\n");

	// Output times: sparse, all 255 outputs (several messages), big tick counts
	{
		static uint32_t ticks[kMaxOutputs];
		for (size_t i = 0; i < kMaxOutputs; i++) {
			ticks[i] = (i % 3 == 0) ? 0 : (uint32_t)rng();
		}
		ticks[254] = 0xFFFFFFFFu;
		uint8_t om[kMaxOutputTimesMessageLength];
		size_t next = 0;
		int messages = 0;
		size_t seen = 0;
		while (next < kMaxOutputs) {
			size_t from = next;
			size_t len = encodeOutputTimes(42, 33330000, ticks, &next, om, sizeof(om));
			CHECK(len <= sizeof(om));
			CHECK(sysexShaped(om, len));
			CHECK(next > from);
			fprintf(f, "%s{\"hex\": \"%s\", \"number\": 42, \"window\": 33330000, \"ticks\": {", messages ? ",\n" : "",
			        hex(om, len).c_str());
			size_t w = 0;
			for (size_t i = from; i < next; i++) {
				if (ticks[i]) {
					fprintf(f, "%s\"%zu\": %u", w++ ? ", " : "", i, ticks[i]);
				}
			}
			seen += w;
			fprintf(f, "}}");
			messages++;
		}
		size_t nonzero = 0;
		for (size_t i = 0; i < kMaxOutputs; i++) {
			nonzero += ticks[i] != 0;
		}
		CHECK(seen == nonzero);
		CHECK(messages > 1);
	}
	fprintf(f, "\n]}\n");
	fclose(f);

	printf("profiler core: %d checks, %d failed\n", checks, failures);
	return failures ? 1 : 0;
}
