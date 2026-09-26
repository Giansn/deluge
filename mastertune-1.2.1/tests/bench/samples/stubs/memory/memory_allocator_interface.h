// Bench stub: the DX7 engine (dsp/dx/engine.cpp) allocates its tables and voices with allocMaxSpeed.
#pragma once
#include <cstdint>
#include <cstdlib>
inline void* allocMaxSpeed(uint32_t size, void* = nullptr) {
	return aligned_alloc(64, (size + 63) & ~63u);
}
inline void* allocLowSpeed(uint32_t size, void* = nullptr) {
	return allocMaxSpeed(size);
}
inline void delugeDealloc(void* address) {
	free(address);
}
