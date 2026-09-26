// Counts the instructions the emulated Cortex-A9 executes, per translated block (unicorn's UC_HOOK_BLOCK) instead of per
// instruction, in C: fast enough for billions of instructions. For each block (start address and size in bytes) it
// keeps how often it ran and how many instructions it has, decoded once from the code bytes (Thumb-2: a halfword whose
// top five bits are 0b11101, 0b11110 or 0b11111 starts a 32-bit instruction; ARM: 4 bytes each). song_emu.py reads the
// running total at the boundaries it cares about and the table for the profile by function.
#include <stdint.h>
#include <string.h>
#include <unicorn/unicorn.h>

#define TABLE_BITS 20
#define TABLE_SIZE (1u << TABLE_BITS)

typedef struct {
	uint64_t key; // address | size << 32, 0 = empty
	uint64_t count;
	uint32_t instructions;
} Entry;

static Entry table[TABLE_SIZE];
static uint64_t total;       // Instructions executed since bc_install()
static uint32_t used;        // Entries in the table
static const uint8_t* code;  // Host memory holding the code (the firmware's internal RAM)
static uint32_t code_base, code_size;
static uc_hook hook;

static uint32_t decode(uc_engine* uc, uint64_t address, uint32_t size) {
	uint32_t cpsr = 0;
	uc_reg_read(uc, UC_ARM_REG_CPSR, &cpsr);
	if (!(cpsr & 0x20)) {
		return size / 4; // ARM
	}
	uint32_t n = 0;
	uint32_t at = (uint32_t)address;
	uint32_t end = at + size;
	while (at < end) {
		uint16_t half;
		if (at >= code_base && at + 2 <= code_base + code_size) {
			memcpy(&half, code + (at - code_base), 2);
		}
		else if (uc_mem_read(uc, at, &half, 2) != UC_ERR_OK) {
			break;
		}
		uint32_t top = half >> 11;
		at += (top == 0x1D || top == 0x1E || top == 0x1F) ? 4 : 2;
		n++;
	}
	return n ? n : 1;
}

static void on_block(uc_engine* uc, uint64_t address, uint32_t size, void* user_data) {
	uint64_t key = (address & 0xFFFFFFFFu) | ((uint64_t)size << 32);
	uint32_t i = (uint32_t)((key * 0x9E3779B97F4A7C15ull) >> (64 - TABLE_BITS));
	for (;;) {
		Entry* e = &table[i];
		if (e->key == key) {
			e->count++;
			total += e->instructions;
			return;
		}
		if (!e->key) {
			if (used >= TABLE_SIZE - 1024) {
				total += size / 2; // Table full: still count (roughly), no profile
				return;
			}
			e->key = key;
			e->count = 1;
			e->instructions = decode(uc, address, size);
			used++;
			total += e->instructions;
			return;
		}
		i = (i + 1) & (TABLE_SIZE - 1);
	}
}

int bc_install(uc_engine* uc, const uint8_t* code_memory, uint32_t base, uint32_t size) {
	code = code_memory;
	code_base = base;
	code_size = size;
	return uc_hook_add(uc, &hook, UC_HOOK_BLOCK, (void*)on_block, NULL, 1, 0);
}

uint64_t bc_total(void) {
	return total;
}

// Forgets how often each block ran (the running total stays), so the profile covers only what runs from here on
void bc_reset_counts(void) {
	for (uint32_t i = 0; i < TABLE_SIZE; i++) {
		table[i].count = 0;
	}
}

// Copies out the blocks that ran since bc_reset_counts(): address, instructions per run and runs. Returns how many.
uint32_t bc_dump(uint32_t* addresses, uint32_t* instructions, uint64_t* counts, uint32_t max) {
	uint32_t n = 0;
	for (uint32_t i = 0; i < TABLE_SIZE && n < max; i++) {
		if (table[i].key && table[i].count) {
			addresses[n] = (uint32_t)table[i].key;
			instructions[n] = table[i].instructions;
			counts[n] = table[i].count;
			n++;
		}
	}
	return n;
}
