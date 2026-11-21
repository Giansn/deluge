// Host test of the arp's fixed note orders (features/0008, modulation/arp_note_order.h): for 1 to 7 notes held, each
// order's cycle as written out by hand, every index within the notes, every note played in a cycle (Thumb and Pinky:
// every note, the held one between), and the cycle lengths. Usage: g++ -std=c++20 -I <tree>/src -I <tree>/src/deluge
// arp_note_order_test.cpp && ./a.out   (exit 0 and "all checks passed" when it holds)
#include "modulation/arp_note_order.h"
#include <cstdio>
#include <set>
#include <string>
#include <vector>

namespace {
int failures = 0;

std::vector<int> cycle(ArpNoteMode mode, int n) {
	std::vector<int> out;
	for (int s = 0; s < arp_note_order::cycleLength(mode, n); s++) {
		out.push_back(arp_note_order::noteIndex(mode, s, n));
	}
	return out;
}

std::string str(std::vector<int> const& v) {
	std::string s;
	for (int x : v) {
		s += std::to_string(x) + " ";
	}
	return s;
}

void expect(char const* name, ArpNoteMode mode, int n, std::vector<int> const& want) {
	auto got = cycle(mode, n);
	if (got != want) {
		std::printf("FAIL %s n=%d: %s(expected %s)\n", name, n, str(got).c_str(), str(want).c_str());
		failures++;
	}
}
} // namespace

int main() {
	using enum ArpNoteMode;
	// Written out by hand (0 the lowest held note)
	expect("converge", CONVERGE, 4, {0, 3, 1, 2});
	expect("converge", CONVERGE, 5, {0, 4, 1, 3, 2});
	expect("diverge", DIVERGE, 4, {2, 1, 3, 0});
	expect("diverge", DIVERGE, 5, {2, 3, 1, 4, 0});
	expect("upDownExcl", UP_DOWN_EXCL, 4, {0, 1, 2, 3, 2, 1});
	expect("upDownExcl", UP_DOWN_EXCL, 2, {0, 1});
	expect("thumb", THUMB, 4, {0, 1, 0, 2, 0, 3});
	expect("pinky", PINKY, 4, {0, 3, 1, 3, 2, 3});
	for (ArpNoteMode mode : {CONVERGE, DIVERGE, UP_DOWN_EXCL, THUMB, PINKY}) {
		expect("one note", mode, 1, {0});
		for (int n = 1; n <= 7; n++) {
			auto c = cycle(mode, n);
			std::set<int> seen(c.begin(), c.end());
			bool in_range = *seen.begin() >= 0 && *seen.rbegin() < n;
			if (!in_range || (int)seen.size() != n) {
				std::printf("FAIL mode %d n=%d: %s\n", (int)mode, n, str(c).c_str());
				failures++;
			}
			// The step wraps: step + cycle plays what step plays
			for (int s = 0; s < (int)c.size(); s++) {
				if (arp_note_order::noteIndex(mode, s + (int)c.size(), n) != c[s]) {
					std::printf("FAIL mode %d n=%d: step %d doesn't wrap\n", (int)mode, n, s);
					failures++;
				}
			}
		}
	}
	static_assert(isOrderedArpNoteMode(CONVERGE) && isOrderedArpNoteMode(PINKY) && !isOrderedArpNoteMode(PATTERN));
	std::printf(failures ? "%d check(s) failed\n" : "all checks passed\n", failures);
	return failures ? 1 : 0;
}
