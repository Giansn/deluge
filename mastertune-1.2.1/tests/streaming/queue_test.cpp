// Host test for ClusterPriorityQueue (mastertune-v9): most urgent first, arrival order among equal priorities,
// lowest-priority detection, removal. Against the real OrderedResizeableArray code.
#include "storage/cluster/cluster_priority_queue.h"
#include <cstdio>
#include <random>
#include <vector>
#include <algorithm>
class Cluster { public: int id; };
static int failures = 0;
#define CHECK(c, ...) do { if (!(c)) { failures++; printf("FAIL: " __VA_ARGS__); printf("\n"); } else { printf("ok   " __VA_ARGS__); printf("\n"); } } while (0)
int main() {
	ClusterPriorityQueue q;
	std::vector<Cluster> cs(2000);
	for (int i = 0; i < 2000; i++) cs[i].id = i;
	std::mt19937 rng(7);
	// Mixed priorities like the firmware's: voice ratings 0x40000000..0xFFFFFFFE, sample starts 0xFFFFFFFF
	struct E { uint32_t prio; int seq; Cluster* c; };
	std::vector<E> ref;
	for (int i = 0; i < 1500; i++) {
		uint32_t prio;
		int kind = rng() % 4;
		if (kind == 0) prio = 0xFFFFFFFFu;
		else if (kind == 1) prio = 0x40000000u + (rng() % 1000);
		else if (kind == 2) prio = 0xC0000000u + (rng() % 5);        // many equal priorities
		else prio = (uint32_t)rng() | 0x40000000u;
		if (prio == 0xFFFFFFFFu && kind != 0) prio = 0xFFFFFFFEu;
		Cluster* c = &cs[i];
		q.add(c, prio);
		ref.push_back({prio, i, c});
	}
	CHECK(q.hasAnyLowestPriorityElements(), "lowest-priority elements detected");
	// remove some, like Sample::~Sample / reasons dropping
	for (int i = 0; i < 1500; i += 7) { q.removeIfPresent(&cs[i]); }
	ref.erase(std::remove_if(ref.begin(), ref.end(), [](const E& e) { return e.seq % 7 == 0; }), ref.end());
	std::stable_sort(ref.begin(), ref.end(), [](const E& a, const E& b) { return a.prio < b.prio; });
	bool ok = true; size_t k = 0;
	bool lowestSeenLast = true;
	while (Cluster* c = q.grabHead()) {
		if (k >= ref.size() || ref[k].c != c) { ok = false; }
		k++;
		// once no lowest remain, the check must say so
		bool anyLowestLeft = false;
		for (size_t j = k; j < ref.size(); j++) if (ref[j].prio == 0xFFFFFFFFu) anyLowestLeft = true;
		if (q.hasAnyLowestPriorityElements() != anyLowestLeft) lowestSeenLast = false;
	}
	CHECK(ok && k == ref.size(), "grabHead returns clusters by priority, first come first served among equals (%zu)", k);
	CHECK(lowestSeenLast, "hasAnyLowestPriorityElements always matches the queue content");
	// extremes
	q.add(&cs[1600], 0xFFFFFFFFu); q.add(&cs[1601], 1); q.add(&cs[1602], 0x80000000u); q.add(&cs[1603], 0x7FFFFFFFu);
	CHECK(q.grabHead() == &cs[1601] && q.grabHead() == &cs[1603] && q.grabHead() == &cs[1602] && q.grabHead() == &cs[1600],
	      "extremes ordered as unsigned: 1 < 0x7FFFFFFF < 0x80000000 < 0xFFFFFFFF");
	printf("%s\n", failures ? "FAILURES" : "all queue checks passed");
	return failures ? 1 : 0;
}
