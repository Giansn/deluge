#!/usr/bin/env python3
"""Stem export with the recorder's RAM failing (upstream #3309, #4471, #4639), on the real firmware in the emulator.

tests/stress/ui's Rig boots the firmware with make_sd.py's song (2 synths, the kit, the audio track) and a card that
takes time (SdModel, "yield": every SD wait runs the task manager, as on the Deluge). In Song view SAVE is held and
RECORD pressed: the clip stem export (offline rendering, the default) runs inside that press. On chosen calls
SampleRecorder::createNextCluster() returns INSUFFICIENT_RAM, as it does on the device when RAM runs out: the
recording aborts. Recorded: freezeWithError (M123: a block freed twice), hangs, crashes, and a trace of the card
accesses, yields, AudioRecorder::finishRecording() and ~SampleRecorder() calls before the end.

mastertune v19.0 (1.2.1's code), 1000,42.7 (a typical SDHC card), failures at calls 3 and 40: M123 at 1.735 s. The
task "audio recorder slow" runs finishRecording() -> discardRecorder(); ~SampleRecorder() reads the card (FatFs
move_window -> disk_read), the wait yields, the task manager runs the same task again, and with the recorder pointer
not yet cleared it destructs and frees the same SampleRecorder a second time. With an instant card (no yields) the
export goes on after the aborts. Upstream's fix 9c3f9a70 (#4671) returns from AudioRecorder::slowRoutine() while
sdRoutineLock is set; with it ported this run should export all stems.

Usage: export_abort_emu.py <deluge.elf> [--out DIR] [--fail-at 3,40] [--sd-latency CMD_US,SECTOR_US | instant]
                           [--limit S] [--tools PREFIX] [--build DIR]
Exit status 0 when the export finished without a problem."""
import argparse
import collections
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.normpath(os.path.join(HERE, ".."))
for d in ("scan", "stress/ui", "song"):
    sys.path.insert(0, os.path.join(TESTS, d))
import scan_view_emu as sv  # noqa: E402

ui, se, make_sd, fat32 = sv.ui, sv.se, sv.make_sd, sv.fat32
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0  # noqa: E402

TRACED = (("disk_write", "disk_write"), ("disk_read", "disk_read"), ("f_unlink", "f_unlink"), ("f_write", "f_write"),
          ("_ZN11TaskManager5yieldEPFbvEd", "yield"), ("_ZN13AudioRecorder15finishRecordingEv", "finishRecording"),
          ("_ZN14SampleRecorderD2Ev", "~SampleRecorder"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "export"))
    ap.add_argument("--fail-at", default="3,40", help="createNextCluster() calls that fail, e.g. 3,40")
    ap.add_argument("--sd-latency", default="1000,42.7")
    ap.add_argument("--limit", type=float, default=300, help="emulated seconds the export may take")
    ap.add_argument("--tools")
    ap.add_argument("--build", default=os.environ.get("BLOCKCOUNT_DIR"))
    a = ap.parse_args()
    a.elf, a.out = os.path.abspath(a.elf), os.path.abspath(a.out)
    a.tools = a.tools or os.path.join(os.path.dirname(a.elf),
                                      "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    a.build = os.path.abspath(a.build or a.out)
    os.makedirs(a.out, exist_ok=True)
    if not os.path.exists(os.path.join(a.build, "blockcount.so")):
        import unicorn
        uc = os.path.dirname(unicorn.__file__)
        subprocess.run(["cc", "-O2", "-shared", "-fPIC", f"-I{uc}/include", os.path.join(TESTS, "song", "blockcount.c"),
                        "-o", os.path.join(a.build, "blockcount.so"), f"-L{uc}/lib", "-l:libunicorn.so.2",
                        f"-Wl,-rpath,{uc}/lib"], check=True)
    image = os.path.join(a.out, "export.img")
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 2).encode()
    fat32.build(image, files)

    rig = ui.Rig(a.elf, a.tools, a.build, image, a.sd_latency)
    emu, sym = rig.emu, rig.emu.sym
    rig.action("CPU monitor off", sym.find("_ZN9cpu_stats7setModeEh"), 0)
    lock = sym["sdRoutineLock"]
    fail_at = {int(x) for x in a.fail_at.split(",") if x}
    calls, events = [0], collections.deque(maxlen=40)

    def next_cluster(e):
        calls[0] += 1
        if calls[0] in fail_at:
            print(f"createNextCluster() call {calls[0]} fails (INSUFFICIENT_RAM) at {e.seconds():.3f} s", flush=True)
            return 1
        return None

    def note(kind):
        return lambda e: events.append((e.seconds(), kind, e.uc.reg_read(UC_ARM_REG_R0),
                                        e.sym.name_at(e.uc.reg_read(UC_ARM_REG_LR)), e.u8(lock))) and None

    emu.intercept(sym.find("_ZN14SampleRecorder17createNextClusterEv"), next_cluster)
    for name, kind in TRACED:
        emu.intercept(sym.find(name), note(kind))
    emu.uc.ctl_flush_tb()

    save, record = ui.button_xy(6, 3), ui.button_xy(8, 2)
    rig.tm(0.5)
    finished = False
    try:
        rig.button(save, True)
        rig.tm(0.05)
        _, took = rig.button(record, True, limit_s=a.limit)  # The export runs inside this press
        print(f"the export returned after {took / se.CPU_HZ:.2f} s", flush=True)
        rig.button(record, False)
        rig.button(save, False)
        rig.tm(1)
        finished = True
    except ui.Stop as ex:
        print(f"stopped in: {ex}")
    print(f"createNextCluster() calls: {calls[0]}")
    for p in rig.problems:
        print(f"problem: {p['kind']}: {p['detail']} at {p.get('at_s')} s")
    print("the last card accesses, yields and recorder teardowns:")
    for t, kind, r0, caller, locked in events:
        print(f"  {t:.5f} s  {kind:<16} r0={r0:#010x}  from {caller}  sdRoutineLock={locked}")
    os.remove(image)
    ok = finished and not rig.problems
    print("all stems exported, no problem" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
