// Host test stub: malloc-based stand-in for the Deluge's allocator
#pragma once
#include "memory/memory_allocator_interface.h"
class GeneralMemoryAllocator {
public:
	static GeneralMemoryAllocator& get() {
		static GeneralMemoryAllocator a;
		return a;
	}
	void checkStack(char const*) {}
};
