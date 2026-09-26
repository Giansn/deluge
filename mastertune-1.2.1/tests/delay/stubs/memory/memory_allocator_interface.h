// Host test stub: malloc-based stand-in for the Deluge's allocator, with a switch to make allocations fail
#pragma once
#include <cstdint>
#include <cstdlib>
extern int hostAllocations;
extern bool hostAllocationsFail;
inline void* allocLowSpeed(uint32_t size, void* = nullptr) {
	if (hostAllocationsFail) {
		return nullptr;
	}
	hostAllocations++;
	return malloc(size);
}
inline void delugeDealloc(void* address) {
	free(address);
}
