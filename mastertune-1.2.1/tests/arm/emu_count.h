// Counts the instructions a stretch of code runs on the Deluge's Cortex-A9, when a test runs in the emulator
// (arm_run.py prints the counts at the end, per call). On the PC these do nothing.
#pragma once
#if defined(__arm__)
#define EMU_COUNT_BEGIN(label) asm volatile("mov r0, %0\n\tsvc #0x41" : : "r"(label) : "r0", "memory")
#define EMU_COUNT_END() asm volatile("svc #0x42" : : : "memory")
#else
#define EMU_COUNT_BEGIN(label) ((void)0)
#define EMU_COUNT_END() ((void)0)
#endif
