// Finds the firmware's reads of memory it never wrote (song_emu.py --uninit): a shadow byte per byte of the internal
// RAM and the SDRAM (their uncached mirrors are the same bytes), set when the CPU writes the byte (UC_HOOK_MEM_WRITE)
// or song_emu.py does (the ELF's segments, .bss, sectors read from the SD card, its own pokes: un_mark()). A read
// (UC_HOOK_MEM_READ) that covers a byte never written is recorded per instruction address (PC): how often, and the
// first one's data address, size, LR and phase (un_set_phase(): boot, load, playback). Values aren't tracked through
// registers: a value copied from unwritten memory to elsewhere counts as written there, so each entry is where
// uninitialised memory is first read, not every place its value reaches.
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unicorn/unicorn.h>

#define IRAM 0x20000000u
#define IRAM_SIZE 0x300000u
#define SDRAM 0x0C000000u
#define SDRAM_SIZE 0x04000000u
#define MIRROR 0x40000000u
#define TABLE_SIZE 65536u

typedef struct {
	uint32_t pc, count, first_address, first_size, first_lr, first_phase;
	uint32_t window; // un_set_window() value at the first read
} Entry;

static uint8_t* shadow_iram;
static uint8_t* shadow_sdram;
static Entry table[TABLE_SIZE];
static uint32_t used, phase, window;
static uc_hook hooks[8];

static uint8_t* shadow_at(uint32_t address) {
	if (address >= MIRROR) {
		address -= MIRROR;
	}
	if (address >= IRAM && address < IRAM + IRAM_SIZE) {
		return shadow_iram + (address - IRAM);
	}
	if (address >= SDRAM && address < SDRAM + SDRAM_SIZE) {
		return shadow_sdram + (address - SDRAM);
	}
	return NULL;
}

void un_mark(uint32_t address, uint32_t n) {
	for (uint32_t i = 0; i < n; i++) {
		uint8_t* s = shadow_at(address + i);
		if (s) {
			*s = 1;
		}
	}
}

void un_set_phase(uint32_t p) {
	phase = p;
}

void un_set_window(uint32_t w) {
	window = w;
}

static void record(uc_engine* uc, uint32_t address, int size) {
	uint32_t pc = 0, lr = 0;
	uc_reg_read(uc, UC_ARM_REG_PC, &pc);
	uint32_t i = (pc * 2654435761u) & (TABLE_SIZE - 1);
	for (;;) {
		Entry* e = &table[i];
		if (e->count && e->pc == pc) {
			e->count++;
			return;
		}
		if (!e->count) {
			if (used >= TABLE_SIZE - 1) {
				return;
			}
			uc_reg_read(uc, UC_ARM_REG_LR, &lr);
			*e = (Entry){pc, 1, address, (uint32_t)size, lr, phase, window};
			used++;
			return;
		}
		i = (i + 1) & (TABLE_SIZE - 1);
	}
}

static void on_access(uc_engine* uc, uc_mem_type type, uint64_t address, int size, int64_t value, void* user_data) {
	uint32_t a = (uint32_t)address;
	if (type == UC_MEM_WRITE) {
		un_mark(a, (uint32_t)size);
		return;
	}
	for (int i = 0; i < size; i++) {
		uint8_t* s = shadow_at(a + i);
		if (s && !*s) {
			record(uc, a, size);
			return;
		}
	}
}

int un_install(uc_engine* uc, int types) {
	if (!types) {
		types = UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE;
	}
	shadow_iram = calloc(IRAM_SIZE, 1);
	shadow_sdram = calloc(SDRAM_SIZE, 1);
	if (!shadow_iram || !shadow_sdram) {
		return -1;
	}
	const uint32_t ranges[4][2] = {{IRAM, IRAM_SIZE}, {IRAM + MIRROR, IRAM_SIZE}, {SDRAM, SDRAM_SIZE},
	                               {SDRAM + MIRROR, SDRAM_SIZE}};
	for (int r = 0; r < 4; r++) {
		if (uc_hook_add(uc, &hooks[r], types, (void*)on_access, NULL, ranges[r][0],
		                ranges[r][0] + ranges[r][1] - 1)
		    != UC_ERR_OK) {
			return -1;
		}
	}
	return 0;
}

// Copies up to max entries (7 uint32 each: pc, count, first address, size, lr, phase, window); returns how many
uint32_t un_dump(uint32_t* out, uint32_t max) {
	uint32_t n = 0;
	for (uint32_t i = 0; i < TABLE_SIZE && n < max; i++) {
		if (table[i].count) {
			memcpy(out + 7 * n, &table[i], sizeof(Entry));
			n++;
		}
	}
	return n;
}
