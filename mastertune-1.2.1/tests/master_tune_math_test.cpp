// Host-side check of the master tune arithmetic used by the firmware (model/tuning/master_tune_math.h).
// Build: g++ -std=c++20 -O2 -I<firmware>/src/deluge master_tune_math_test.cpp -o test && ./test
#include "model/tuning/master_tune_math.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>

using namespace MasterTune::math;

static int failures = 0;
#define CHECK(cond, ...)                                                                                               \
	do {                                                                                                               \
		if (!(cond)) {                                                                                                 \
			std::printf("FAIL %s:%d: ", __FILE__, __LINE__);                                                          \
			std::printf(__VA_ARGS__);                                                                                  \
			std::printf("\n");                                                                                         \
			failures++;                                                                                                \
		}                                                                                                              \
	} while (0)

int main() {
	// 440.0 Hz must leave everything untouched
	CHECK(ratioQ30(4400) == kUnityRatioQ30, "unity ratio");
	CHECK(cents(4400) == 0.0, "zero cents at 440");
	CHECK(midiFineTuning(cents(4400)) == kMIDIFineTuningCentre, "RPN centre at 440");
	for (uint64_t inc = 1; inc <= UINT32_MAX; inc = inc * 3 + 7) {
		CHECK(scalePhaseIncrement((uint32_t)inc, ratioQ30(4400)) == inc, "identity for %llu", (unsigned long long)inc);
	}

	// Range ends are one semitone each way
	CHECK(std::fabs(cents(4153) + 100.0) < 0.02, "415.3 Hz is -100 cents (got %f)", cents(4153));
	CHECK(std::fabs(cents(4662) - 100.13) < 0.02, "466.2 Hz is +100.13 cents (got %f)", cents(4662));

	double worstCents = 0, worstHz = 0;
	int32_t previousRPN = -1;
	for (int32_t tenths = 4153; tenths <= 4662; tenths++) {
		// A4 frequency implied by the Q30 ratio
		double hz = 440.0 * ratioQ30(tenths) / (double)kUnityRatioQ30;
		worstHz = std::max(worstHz, std::fabs(hz - tenths / 10.0));

		// Phase increments across the audible range (32-bit phase at 44.1 kHz: 8 Hz is about 780000)
		double exactRatio = tenths / 4400.0;
		for (uint32_t inc = 780000; inc < (1u << 31); inc = inc + inc / 3 + 1) {
			uint32_t scaled = scalePhaseIncrement(inc, ratioQ30(tenths));
			double error = 1200.0 * std::log2(scaled / (inc * exactRatio));
			worstCents = std::max(worstCents, std::fabs(error));
		}

		// RPN values rise with the tuning and stay in 14 bits
		int32_t rpn = midiFineTuning(cents(tenths));
		CHECK(rpn >= 0 && rpn <= 16383, "RPN in range for %d", tenths);
		CHECK(rpn >= previousRPN, "RPN monotonic at %d", tenths);
		previousRPN = rpn;
		// RPN 1 carries -100 ... +99.988 cents; only 415.3 Hz (-100.02) and 466.2 Hz (+100.13) lie beyond (<= 0.15 cents)
		double rpnCents = (rpn - 8192) * 100.0 / 8192.0;
		double allowed = (std::fabs(cents(tenths)) < 99.99) ? 0.0062 : 0.15;
		CHECK(std::fabs(rpnCents - cents(tenths)) <= allowed, "RPN off by %f cents at %d",
		      rpnCents - cents(tenths), tenths);
	}
	CHECK(worstHz < 0.0001, "A4 frequency off by %g Hz", worstHz);
	CHECK(worstCents < 0.01, "phase increment off by %g cents", worstCents);

	// Audio recorded on the Deluge at tuning R and played at tuning T is shifted by exactly T / R, not at all at T == R
	double worstRecordedCents = 0;
	for (int32_t recorded = 4153; recorded <= 4662; recorded += 7) {
		CHECK(ratioQ30(recorded, recorded) == kUnityRatioQ30, "no shift at the recording tuning %d", recorded);
		for (int32_t tenths = 4153; tenths <= 4662; tenths += 3) {
			double ratio = ratioQ30(tenths, recorded) / (double)kUnityRatioQ30;
			double error = 1200.0 * std::log2(ratio * recorded / tenths);
			worstRecordedCents = std::max(worstRecordedCents, std::fabs(error));
		}
	}
	CHECK(worstRecordedCents < 0.00001, "recorded tuning ratio off by %g cents", worstRecordedCents);

	// Whole chain, never tuned twice: note m recorded at tuning R, found by the pitch detection
	// (Sample::workOutMIDINote(), float like the firmware) and played as note n at tuning T must sound like note n of a
	// synth at tuning T. R = 440 Hz also stands for every sample that wasn't recorded on the Deluge.
	double worstChainCents = 0;
	for (int32_t recorded : {4153, 4320, 4400, 4420, 4662}) {
		for (int32_t tenths : {4153, 4320, 4400, 4420, 4662}) {
			for (int32_t recordedNote : {33, 57, 69, 81, 100}) {
				double contained = recorded / 10.0 * std::pow(2.0, (recordedNote - 69) / 12.0);
				float midiNote = 69 + log2f((float)contained / 440) * 12;
				if (recorded != 4400) {
					midiNote -= cents(recorded) / 100;
				}
				for (int32_t playedNote : {24, 60, 69, 72, 108}) {
					double played = contained * std::pow(2.0, (playedNote - midiNote) / 12.0)
					                * (ratioQ30(tenths, recorded) / (double)kUnityRatioQ30);
					double expected = tenths / 10.0 * std::pow(2.0, (playedNote - 69) / 12.0);
					worstChainCents = std::max(worstChainCents, std::fabs(1200.0 * std::log2(played / expected)));
				}
			}
		}
	}
	CHECK(worstChainCents < 0.01, "recorded sample off by %g cents", worstChainCents);

	// Never wraps around at the top
	CHECK(scalePhaseIncrement(UINT32_MAX, ratioQ30(4662)) == UINT32_MAX, "saturates");

	std::printf("worst A4 error %.7f Hz, worst phase increment error %.6f cents, RPN at 415.3/440/466.2: %d/%d/%d\n",
	            worstHz, worstCents, midiFineTuning(cents(4153)), midiFineTuning(cents(4400)),
	            midiFineTuning(cents(4662)));
	std::printf("recorded tuning: worst ratio error %.7f cents, worst error of a detected recorded note %.5f cents\n",
	            worstRecordedCents, worstChainCents);
	std::printf(failures ? "%d FAILURES\n" : "all checks passed\n", failures);
	return failures ? EXIT_FAILURE : EXIT_SUCCESS;
}
