// Host test stub: malloc-based stand-in for the Deluge's allocator
#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
class GeneralMemoryAllocator {
public:
	static GeneralMemoryAllocator& get() {
		static GeneralMemoryAllocator a;
		return a;
	}
	void* alloc(uint32_t size) {
		uint32_t* p = (uint32_t*)malloc(size + 8);
		p[0] = size;
		return p + 2;
	}
	void* allocMaxSpeed(uint32_t s, void* = nullptr) { return alloc(s); }
	void* allocLowSpeed(uint32_t s, void* = nullptr) { return alloc(s); }
	void* allocExternal(uint32_t s) { return alloc(s); }
	void dealloc(void* a) {
		if (a)
			free((uint32_t*)a - 2);
	}
	void deallocExternal(void* a) { dealloc(a); }
	uint32_t getAllocatedSize(void* a) { return ((uint32_t*)a - 2)[0]; }
	void extend(void*, uint32_t, uint32_t, uint32_t* l, uint32_t* r, void* = nullptr) { *l = *r = 0; }
	uint32_t shortenLeft(void*, uint32_t, uint32_t = 0) { return 0; }
	uint32_t shortenRight(void*, uint32_t) { return 0; }
};
inline void* delugeAlloc(unsigned int s, bool = true) {
	return GeneralMemoryAllocator::get().alloc(s);
}
inline void delugeDealloc(void* a) {
	GeneralMemoryAllocator::get().dealloc(a);
}
