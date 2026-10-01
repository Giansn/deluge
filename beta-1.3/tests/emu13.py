#!/usr/bin/env python3
"""The emulator rig of tests/song (song_emu.py) adapted to the community firmware v1.3 (upstream main, the daily beta
build), to look for its crashes. What differs from 1.2.1 is patched in here, song_emu.py stays as it is for mastertune:
- SD card: v1.3 inlines sd_read_sect() into disk_read_without_streaming_first() (an .isra clone: buffer, sector,
  count), which the cluster loading and disk_read() call; writes go through disk_write() (.isra: the same arguments).

Usage: import emu13 (before song_emu's functions are used), or run it like song_emu.py."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "mastertune-1.2.1", "tests", "song"))  # mastertune 1.2.1's rig
import song_emu  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2  # noqa: E402


def setup_sd(emu):
    """song_emu.setup_sd() for v1.3: the sector reads and writes go to the image file, the card's initialisation in
    mount_volume() is skipped as in 1.2.1."""
    uc = emu.uc
    read_fn = "sd_read_sect" if "sd_read_sect" in emu.sym.by_name else "disk_read_without_streaming_first"
    write_fn = "disk_write"

    def read_sectors(e):
        buf, sector, count = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        uc.mem_write(buf, os.pread(e.sd_fd, count * 512, sector * 512).ljust(count * 512, b"\0"))
        e.sd_reads += count
        e.sd_read_commands += 1
        if e.sd_log is not None:
            e.sd_log.append(("r", sector, count, e.now()))
        if e.sd_model:
            return e.sd_model.wait(count, False)
        return 0

    def write_sectors(e):
        buf, sector, count = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        os.pwrite(e.sd_fd, bytes(uc.mem_read(buf, count * 512)), sector * 512)
        e.sd_writes += count
        e.sd_write_commands += 1
        if e.sd_log is not None:
            e.sd_log.append(("w", sector, count, e.now()))
        if e.sd_model:
            return e.sd_model.wait(count, True)
        return 0

    emu.intercept(emu.sym.find(read_fn), read_sectors)
    emu.intercept(emu.sym.find(write_fn), write_sectors)
    # mount_volume(): from where it has cleared fs->fs_type and checked the write protection (strh r3, [r2, #0], then a
    # branch) and disk_initialize() (inlined, the SDHI driver) begins, to where it calls check_fs(fs, 0) (ldr r0,
    # [sp, #8]; movs r1, #0), with the card ready (diskStatus 0)
    code = song_emu.disassemble(emu, "mount_volume")
    clear = next(i for i, (a, m, o) in enumerate(code[:80]) if m == "strh" and o.startswith("r3, [r2, #0]"))
    branch = next(i for i in range(clear + 1, clear + 4) if code[i][1].startswith("b"))
    call = next(i for i, (a, m, o) in enumerate(code) if m == "bl" and "<check_fs>" in o)
    if not (code[call - 2][1] == "ldr" and code[call - 2][2].startswith("r0, [sp") and code[call - 1][1] == "movs"):
        raise SystemExit(f"mount_volume() doesn't look as expected: {code[call - 3:call + 1]}")
    disk_status = emu.sym["diskStatus"]
    emu.skip_to(code[branch + 1][0], code[call - 2][0], lambda e: e.uc.mem_write(disk_status, b"\0"))


def run_task_manager(emu, seconds):
    """song_emu.run_task_manager() for v1.3: TaskManager::yield(until, Time timeout, returnOnIdle) is a clone with
    this and returnOnIdle (false) propagated; Time is int64 ticks of the 33.33 MHz timer, passed in r2:r3."""
    import struct
    sym = emu.sym
    if not getattr(emu, "task_manager_setup", None):
        task_size, current_id, handle = song_emu.gdb_values(emu, ["sizeof(Task)", "(int)&((TaskManager*)0)->currentID",
                                                                  "(int)&((Task*)0)->handle"])
        base, size = sym.by_name["taskManager"]
        free = [i for i in range(size // task_size) if not emu.u32(base + i * task_size + handle)]
        if not free:
            raise SystemExit("no free slot in the task list")
        emu.uc.mem_write(song_emu.NEVER, b"\x00\x20\x70\x47")
        emu.intercept(sym.find("uartFlushIfNotSending"), lambda e: song_emu.drain_uarts(e))
        emu.uc.ctl_flush_tb()
        emu.task_manager_setup = (base + current_id, free[-1], sym.find("_ZN11TaskManager5yieldEPFbvE4Timeb"))
    at, slot, yield_ = emu.task_manager_setup
    emu.uc.mem_write(at, struct.pack("<b", slot))
    song_emu.drain_uarts(emu)
    ticks = int(seconds * 33330000)
    emu.call(yield_, song_emu.NEVER | 1, 0, ticks & 0xFFFFFFFF, ticks >> 32)


def boot(emu):
    """song_emu.boot() for v1.3: resetprg() up to where deluge_main() starts the task manager (TaskManager::start(),
    inlined: its startClock()). v1.3 also calls startClock() earlier in the boot (the first yield before the task
    manager runs starts it), so the stop is the call from deluge_main() itself once it has set sdRoutineLock false
    (right before registerTasks() and startTaskManager()), not the first call."""
    import time
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_SP
    lock = emu.sym["sdRoutineLock"]

    def at_start_clock(e):
        caller = e.sym.name_at(e.uc.reg_read(UC_ARM_REG_LR))
        if "TaskManager::yield" not in caller and "deluge_main" in caller and not e.u8(lock):
            e.stop()

    emu.intercept(emu.sym.find("_ZN11TaskManager10startClockEv"), at_start_clock)
    emu.uc.reg_write(UC_ARM_REG_SP, song_emu.PROGRAM_STACK_TOP)
    t = time.time()
    emu.run(emu.sym["resetprg"] | 1, song_emu.STOP, timeout_s=5)
    emu.log(f"boot: {emu.bc.bc_total() / 1e6:,.1f}M instructions ({time.time() - t:.1f} s)")


song_emu.setup_sd = setup_sd
song_emu.boot = boot
song_emu.run_task_manager = run_task_manager

if __name__ == "__main__":
    song_emu.main()


def backtrace(emu, depth=2048, limit=24):
    """Likely callers: the words on the stack (from SP up) that are return addresses into code (Thumb bit set, right
    after a BL/BLX), as function+offset."""
    from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
    uc = emu.uc
    sp = uc.reg_read(UC_ARM_REG_SP)
    out = [f"pc {emu.sym.name_at(uc.reg_read(UC_ARM_REG_PC))}", f"lr {emu.sym.name_at(uc.reg_read(UC_ARM_REG_LR))}"]
    try:
        data = bytes(uc.mem_read(sp, depth))
    except Exception:
        return out
    import struct
    for i in range(0, len(data), 4):
        w = struct.unpack_from("<I", data, i)[0]
        if not (w & 1) or not (0x20000000 <= w < 0x20200000):
            continue
        a = (w & ~1) - 4
        try:
            hw = struct.unpack("<HH", bytes(uc.mem_read(a, 4)))
        except Exception:
            continue
        if (hw[0] & 0xF800) == 0xF000 and (hw[1] & 0xD000) in (0xD000, 0xC000):  # BL / BLX (Thumb-2)
            out.append(f"sp+{i:#x}: {emu.sym.name_at(w & ~1)}")
            if len(out) >= limit:
                break
    return out
