#!/usr/bin/env python3
"""The sampling profiler (mastertune-v15-prof) end to end, on the real firmware in the emulator (the harness of
../song): the load-test song loaded, Settings > CPU monitor > Profile switched on as the menu does
(cpu_stats::setMode(3), which starts OS timer 1), then the firmware's own task manager runs, stopped and then playing,
with OS timer 1's interrupt raised every emulated millisecond.

The interrupt is modelled as the CPU takes it (song_emu.Interrupts): only while the code has interrupts enabled, so
while an output renders (Song::renderAudio() disables them around renderOutput()) it waits until they're enabled again;
periods missed meanwhile merge into one, as the interrupt controller's pending bit does. Before each, the interrupted
address and CPSR go where irq_handler pushes them, at the top of the IRQ stack, which is where the handler reads them.

The SysEx the firmware sends on USB is taken from MIDIDeviceUSB::sendSysex() (sendBufferSpace() answers a free
ring), decoded with tools/deluge_profiler.py, named from the ELF's symbols and checked against what the emulator
measures itself:
- no message lost, no sample dropped, every sample taken from the main program's mode
- the samples' weights add up to the emulated milliseconds
- the share of time in the audio routine against the CPU monitor's own numbers (cpu_stats, the time in routine())
- the functions that run with interrupts enabled (reverb, drone, master compressor) against the emulator's
  instruction counts
- each output's render time as sent against the samples tagged with that output
- the names of the tasks and of the song's outputs

Usage: profiler_emu.py <deluge.elf> [--seconds S] [--tools PREFIX] [--out DIR]   (BLOCKCOUNT_DIR: where blockcount.so is)
Exit status 0 when every check passes."""
import argparse
import collections
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import deluge_profiler as dp  # noqa: E402
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_PC, UC_ARM_REG_R1, UC_ARM_REG_R2  # noqa: E402

PROFILE_MODE = 3
MENU_VALUE_OFFSET = 12  # Selection's value_ in the menu item (CpuMonitorMode::writeCurrentValue(): ldrb [r0, #12])


class Profiler:
    """Profile mode on, the timer interrupt raised every millisecond, the USB SysEx collected."""

    def __init__(self, emu):
        self.emu = emu
        sym = emu.sym
        self.isr = sym.find("profilerTimerInterrupt")
        # A linker script label without a size, which the harness's symbol table leaves out
        nm = subprocess.run([emu.tool_prefix + "nm", emu.elf], capture_output=True, text=True, check=True).stdout
        self.irq_top = next(int(line.split()[0], 16) for line in nm.splitlines() if line.endswith(" irq_stack_end"))
        self.period = se.CPU_HZ / 1000
        self.messages = []
        self.fired = 0
        self.ints = emu.interrupts or se.Interrupts(emu)
        emu.intercept(sym.find("_ZN13MIDIDeviceUSB15sendBufferSpaceEv"), lambda e: 4096 * 3)
        emu.intercept(sym.find("_ZN13MIDIDeviceUSB9sendSysexEPKhl"), self.sysex)
        self.hook_unmasking()
        emu.uc.ctl_flush_tb()
        # The mode the harness runs the firmware in (the Deluge: System mode, as irq_handler switches to)
        self.expected_other_mode = (emu.uc.reg_read(UC_ARM_REG_CPSR) & 0x1F) != 0x1F
        # As the menu does it (cpu_stats::setMode() is inlined into it): its value, then writeCurrentValue()
        menu = sym["cpuMonitorMenu"]
        emu.uc.mem_write(menu + MENU_VALUE_OFFSET, bytes([PROFILE_MODE]))
        emu.call(sym.find("_ZN6deluge3gui9menu_item14CpuMonitorMode17writeCurrentValueEv"), menu)
        self.next_at = emu.now() + self.period
        self.ints.schedule("profiler", self.next_at, self.isr, self.before)

    def hook_unmasking(self):
        """The CPU takes a pending interrupt right after the instruction that enables them (CPSIE i), where
        song_emu.Interrupts would only look again 1 us later: a stop is asked for right there. That matters for the
        samples taken as an output's render ends (Song::renderAudio() enables interrupts after each one)."""
        emu = self.emu
        dis = subprocess.run([emu.tool_prefix + "objdump", "-d", emu.elf], capture_output=True, text=True,
                             check=True).stdout
        self.unmask_sites = []
        for m in re.finditer(r"^\s*([0-9a-f]+):\s+([0-9a-f]{4})( [0-9a-f]{4}|[0-9a-f]{4})?\s+cpsie\s+i", dis, re.M):
            address = int(m.group(1), 16)
            length = 2 if m.group(3) is None else 4
            self.unmask_sites.append(address + length)

        # Interrupts.service() holds a due interrupt back while the code has them masked, moving it on by 400
        # instructions at a time: note when that happens to ours
        self.held_back = False
        service = self.ints.service

        def service_noting():
            p = self.ints.pending.get("profiler")
            if p is not None and p[0] <= emu.now() and emu.uc.reg_read(UC_ARM_REG_CPSR) & 0x80:
                self.held_back = True
            service()

        self.ints.service = service_noting

        def unmasked(uc, address, size, _):
            p = self.ints.pending.get("profiler")
            if self.held_back and p is not None:
                self.held_back = False
                p[0] = min(p[0], emu.now())  # Due now, as the CPU takes it
                emu.bc.bc_set_deadline(emu.bc.bc_total())  # Stops at the next block, where run() services it

        for site in self.unmask_sites:
            emu.uc.hook_add(UC_HOOK_CODE, unmasked, begin=site, end=site)

    def sysex(self, emu):
        uc = emu.uc
        data, length = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)
        raw = bytes(uc.mem_read(data, length))
        d = dp.decode(raw)
        if d is not None:
            self.messages.append(d)
        return 0  # Returns at once

    def new_window(self):
        """The output times' window starts afresh (as profiler::start() does), so each covers one phase only."""
        emu = self.emu
        ticks = int(emu.seconds() * se.PERIPHERAL_HZ) & 0xFFFFFFFF
        emu.w32(self.symbol("windowStartTicksE"), ticks)
        base, size = emu.sym.by_name["_ZN8profiler11outputTicksE"]
        emu.uc.mem_write(base, bytes(size))

    def symbol(self, suffix):
        nm = subprocess.run([self.emu.tool_prefix + "nm", self.emu.elf], capture_output=True, text=True).stdout
        return next(int(line.split()[0], 16) for line in nm.splitlines() if suffix in line and "profiler" in line)

    def before(self, emu):
        self.held_back = False
        uc = emu.uc
        # Where irq_handler pushes them: first lr - 4 (the instruction to resume at), then SPSR
        emu.w32(self.irq_top - 4, uc.reg_read(UC_ARM_REG_PC))
        emu.w32(self.irq_top - 8, uc.reg_read(UC_ARM_REG_CPSR))
        self.fired += 1
        # The next period; those missed while interrupts were disabled merge into this one (one pending bit)
        now = emu.now()
        self.next_at += self.period
        while self.next_at <= now:
            self.next_at += self.period
        self.ints.schedule("profiler", self.next_at, self.isr, self.before)


checks_totals = []


def run(emu, prof, seconds, label, checks):
    """The task manager for `seconds`; the profile of that time, checked. Returns the checks' results."""
    prof.new_window()
    start_messages = len(prof.messages)
    fired0 = prof.fired
    cpu = se.CpuStats(emu, mode=PROFILE_MODE)
    emu.bc.bc_reset_counts()
    t0 = emu.now()
    se.run_task_manager(emu, seconds)
    cpu.close()
    elapsed_ms = (emu.now() - t0) / se.CPU_HZ * 1000
    truth = se.profile_by_function(emu)  # Instructions per function over the same time
    truth_total = sum(truth.values())
    msgs = prof.messages[start_messages:]
    namer = dp.Namer(dp.symbols_from_elf(emu.elf, emu.tool_prefix + "nm"))
    r = dp.analyse(msgs, namer)
    total = r["total"]
    samples = [s for m in msgs if m["kind"] == "samples" for s in m["samples"]]
    print(f"\n== {label}: {elapsed_ms:.0f} ms emulated, {prof.fired - fired0} interrupts, "
          f"{sum(m['kind'] == 'samples' for m in msgs)} sample messages, {len(samples)} samples, weight {total}")

    def check(ok, what):
        checks.append(ok)
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")

    check(r["lost_messages"] == 0 and r["dropped"] == 0,
          f"no message lost ({r['lost_messages']}), no sample dropped ({r['dropped']})")
    check(all(s["other_mode"] == prof.expected_other_mode for s in samples),
          "every sample from the mode the firmware runs in")
    # Samples still in the ring when a phase ends go out in the next: up to the CPU monitor task's longest interval
    # (0.1 s, deluge.cpp; while the audio takes nearly all the time it runs that late) either way
    check(abs(total - elapsed_ms) <= 110, f"weights add up to the time: {total} of {elapsed_ms:.0f} ms")
    checks_totals.append((total, elapsed_ms))
    weights = collections.Counter(s["weight"] for s in samples)
    print(f"  weights: {dict(sorted(weights.items()))}")

    # The audio routine's share against the CPU monitor's own measurement (time in routine() / time)
    windows = cpu.summaries()
    if windows:
        busy = sum(w["dspAvgPermille"] for w in windows[1:] or windows) / len(windows[1:] or windows) / 10
        mine = 100.0 * r["audio"] / total
        check(abs(mine - busy) <= 4, f"audio routine {mine:.1f} % of the samples, the CPU monitor {busy:.1f} %")

    # Functions that run with interrupts enabled: sampled share against the instruction counts
    by_fn = r["by_function"]
    for key in ("Mutable::process", "Drone::renderVoice", "RMSFeedbackCompressor::render"):
        t = sum(n for f, n in truth.items() if key in f) / truth_total * 100
        s = sum(n for f, n in by_fn.items() if key in f) / total * 100
        if t < 1:
            print(f"  (skip) {key}: {t:.2f} % in the emulator, too little to compare")
            continue
        check(abs(s - t) <= max(2.0, 0.3 * t), f"{key}: {s:.1f} % sampled, {t:.1f} % of the instructions")

    # Each output's render time (measured around renderOutput()) against the samples tagged with it
    if r["window_ticks"]:
        timed = 100.0 * sum(r["output_ticks"].values()) / r["window_ticks"]
        tagged = 100.0 * sum(r["by_output"].values()) / total
        check(abs(timed - tagged) <= max(3.0, 0.2 * timed),
              f"outputs {timed:.1f} % of the time measured, {tagged:.1f} % of the samples tagged with an output")
        top = sorted(r["output_ticks"].items(), key=lambda x: -x[1])[:5]
        print("  outputs: " + ", ".join(f"{r['output_names'].get(o, o)} {100.0 * t / r['window_ticks']:.1f} %"
                                        for o, t in top))
    else:
        check(False, "output times sent")
    task_names = set(r["task_names"].values())
    check({"audio  routine", "cpu monitor"} <= task_names or any("audio" in n for n in task_names),
          f"task names: {sorted(task_names)[:8]}")
    check(any(n.startswith("S ") for n in r["output_names"].values()),
          f"output names: {list(r['output_names'].values())[:6]}")
    print("  top functions: " + ", ".join(f"{f.split('(')[0]} {100.0 * n / total:.1f}%"
                                         for f, n in by_fn.most_common(8)))
    if os.environ.get("PROFILER_DUMP"):
        import json
        with open(os.environ["PROFILER_DUMP"] + f".{label}.json", "w") as f:
            json.dump([[namer(s["address"]), s["task"], s["audio"], s["output"], s["weight"]] for s in samples], f)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--seconds", type=float, default=2.2)
    ap.add_argument("--tools")
    ap.add_argument("--out", default=os.getcwd())
    a = ap.parse_args()
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(a.out, exist_ok=True)
    sd = os.path.join(a.out, "profiler.img")
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1).encode()
    make_sd.fat32.build(sd, files)
    emu = se.Emulator(a.elf, sd, tools, os.environ.get("BLOCKCOUNT_DIR", HERE), lambda s: None)
    se.setup_sd(emu)
    se.boot(emu)
    se.load_startup_song(emu)
    dma = se.RealTimeDma(emu)
    prof = Profiler(emu)
    checks = []
    run(emu, prof, a.seconds, "stopped", checks)
    emu.call(emu.sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
    run(emu, prof, a.seconds, "playing", checks)
    dma.close()
    total = sum(t for t, _ in checks_totals)
    elapsed = sum(e for _, e in checks_totals)
    ok = elapsed - 110 <= total <= elapsed
    checks.append(ok)
    print(f"\n{'ok  ' if ok else 'FAIL'} all phases: weights {total} for {elapsed:.0f} ms (the last up to 0.1 s still in the "
          f"ring); unmasking sites hooked: {len(prof.unmask_sites)}")
    failed = checks.count(False)
    print(f"\nprofiler in the emulator: {len(checks)} checks, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
