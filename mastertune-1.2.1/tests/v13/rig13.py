#!/usr/bin/env python3
"""The stress rig of tests/stress/ui (stress_ui_emu.Rig) for the community firmware v1.3 (upstream main, the daily beta
build): the firmware boots, its own task manager runs (song_emu.run_task_manager()) and loads the startup song with its
own conditional task, as on the Deluge (v1.3 registers setupStartupSong() as a task; 1.2.1 called it directly), the
SSI's DMA in real time (stress_ui_emu.LightDma). Records what the Rig records: a crash (an emulator error such as a
write through a null pointer), freezeWithError() and the fault handlers, invalid accesses, hangs, error popups.

Usage (smoke test): rig13.py <deluge.elf> --tools PREFIX --build DIR --out DIR [--seconds N]"""
import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(TESTS, "stress", "ui"))
sys.path.insert(0, os.path.join(TESTS, "song"))
import emu13  # noqa: E402  (song_emu.setup_sd and run_task_manager for v1.3)
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402
import stress_ui_emu as su  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_PC  # noqa: E402

assert emu13.setup_sd is se.setup_sd  # patched in


class Rig13(su.Rig):
    def __init__(self, elf, tools, build, image, sd_latency="instant", oled=True, label=""):
        self.problems = []
        self.popups = []
        self.invalid = collections.Counter()
        self.label = label
        self.action_name = "boot"
        if not su.Rig._patched:
            orig = se.Emulator.on_unmapped

            def on_unmapped(emu, uc, access, address, size, value, _):
                if address >= 0x100000 and not any(lo <= address < hi for lo, hi in su.PERIPHERAL):
                    rig = getattr(emu, "rig", None)
                    if rig is not None:
                        rig.invalid[(address & ~0xFFF, emu.sym.name_at(uc.reg_read(UC_ARM_REG_PC)),
                                     rig.action_name)] += 1
                return orig(emu, uc, access, address, size, value, _)
            se.Emulator.on_unmapped = on_unmapped
            su.Rig._patched = True
        self.emu = emu = se.Emulator(elf, image, tools, build, lambda s: None)
        emu.rig = self
        sym = emu.sym
        self.hook_faults()
        se.setup_sd(emu)
        self.guard("boot", lambda: se.boot(emu), 30)
        swap = "_ZN6deluge3hid7display15swapDisplayTypeEv"
        if oled and swap in sym.by_name:
            emu.call(sym[swap])
        # The startup song: SONGS/DEFAULT.XML as a template (Settings > Defaults > Startup song), which the task
        # "load startup song" loads once the card is ready; it is done when it deletes its canary file
        # (SONGS/__STARTUP_OFF_CHECK_...); what it says on the way (consoleText: "Startup fault F1" etc.) is kept
        emu.w32(sym["_ZN12FlashStorage22defaultStartupSongModeE"], 1)  # StartupSongMode::TEMPLATE
        self.startup_done = False
        self.console = []

        def on_unlink(e):
            path = e.ram_str(e.uc.reg_read(se.UC_ARM_REG_R0)) if hasattr(e, "ram_str") else ""
            if "__STARTUP_OFF_CHECK_" in path:
                self.startup_done = True
        emu.ram_str = getattr(emu, "ram_str", None) or (
            lambda p, n=96: bytes(emu.uc.mem_read(p, n)).split(b"\0")[0].decode(errors="replace"))
        emu.intercept(sym.find("f_unlink"), on_unlink)
        for name in sym.by_name:
            if "consoleText" in name and name.startswith("_ZN6deluge3hid7display"):
                emu.intercept(sym[name], lambda e: self.console.append(e.ram_str(e.uc.reg_read(se.UC_ARM_REG_R1)))
                              and None)
        emu.uc.ctl_flush_tb()
        self.song_name_off, = su.gdb_ints(emu, ["list Song::Song", "print (int)&((Song*)0)->name"])
        (self.string_memory, region_size, empty, memory, count, msize, mstart,
         esize) = se.gdb_values(emu, ["(int)&((String*)0)->stringMemory", "sizeof(MemoryRegion)",
                                      "(int)&((MemoryRegion*)0)->emptySpaces", "(int)&((ResizeableArray*)0)->memory",
                                      "(int)&((ResizeableArray*)0)->numElements",
                                      "(int)&((ResizeableArray*)0)->memorySize",
                                      "(int)&((ResizeableArray*)0)->memoryStart",
                                      "(int)&((ResizeableArray*)0)->elementSize"])
        self.heap_offsets = (region_size, empty, memory, count, msize, mstart, esize)
        if sd_latency and sd_latency != "instant":
            se.SdModel(emu, *(float(x) for x in sd_latency.split(",")), wait="yield")
        self.dma = su.LightDma(emu)
        self.cpu = None
        self.cpu_seen = 0
        se.run_task_manager(emu, 0.001)
        self.task_at, self.task_slot, _ = emu.task_manager_setup
        self.a = {}
        for n in ("_Z6openUIP2UI", "_ZN7Buttons12buttonActionEhbb", "_Z12getCurrentUIv", "_Z9getRootUIv",
                  "_ZN15PlaybackHandler17playButtonPressedEl", "_ZN10LoadSongUI19selectEncoderActionEa"):
            try:
                self.a[n] = sym.find(n)
            except KeyError:
                pass
        self.v = {n: sym[n] for n in ("currentUIMode", "currentSong", "loadSongUI", "sessionView", "playbackHandler",
                                      "_ZN8QwertyUI11enteredTextE") if n in sym.by_name}
        self.ui_names = {sym[n]: n for n in ("sessionView", "instrumentClipView", "arrangerView", "loadSongUI",
                                             "soundEditor", "audioClipView", "keyboardScreen", "automationView",
                                             "saveSongUI", "performanceView", "performanceSessionView")
                         if n in sym.by_name}
        self.folder_reads = 0
        self.swaps = []
        self.arms = []

    def playing(self):
        """PlaybackHandler::playbackState (its offset from the debug info: 20 in v1.3, 16 in 1.2.1)."""
        if not hasattr(self, "_playback_state_off"):
            self._playback_state_off, = se.gdb_values(self.emu, ["(int)&((PlaybackHandler*)0)->playbackState"])
        return self.emu.u8(self.v["playbackHandler"] + self._playback_state_off)

    def load_startup_song(self, limit_s=40, slice_s=0.25):
        """The task manager until its startup-song task is done (it has deleted its canary file); the console
        messages must not report a fault."""
        waited = 0.0
        while not self.startup_done and waited < limit_s:
            self.tm(slice_s, "startup song (task manager)")
            waited += slice_s
        faults = [m for m in self.console if "fault" in m.lower() or "missing" in m.lower()]
        if not self.startup_done or faults:
            raise SystemExit(f"startup song not loaded after {waited:g} s (console {self.console})")
        return waited


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--synths", type=int, default=5)
    ap.add_argument("--sd-latency", default="1000,42.67")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    make_sd.main([image, "--synths", str(a.synths)]) if hasattr(make_sd, "main") and False else None
    if not os.path.exists(image):
        import subprocess
        subprocess.run([sys.executable, os.path.join(TESTS, "song", "make_sd.py"), image, "--synths", str(a.synths)],
                       check=True, stdout=subprocess.DEVNULL)
    result = dict(problems=[], phases=[])
    rig = None
    try:
        rig = Rig13(a.elf, a.tools, a.build, image, a.sd_latency)
        w = rig.load_startup_song()
        result["phases"].append(dict(phase="startup song", waited_s=w, heap=rig.heap(), dma=rig.dma.take()))
        rig.play()
        t = 0.0
        while t < a.seconds:
            rig.tm(1.0, "playing")
            t += 1.0
            result["phases"].append(dict(phase=f"playing {t:g} s", heap=rig.heap(), dma=rig.dma.take()))
    except (su.Stop, SystemExit) as ex:
        result["stopped"] = str(ex)
    if rig:
        result["problems"] = rig.problems
        result["invalid"] = [dict(page=hex(k[0]), at=k[1], during=k[2], n=n) for k, n in rig.invalid.most_common(20)]
        result["error_popups"] = rig.error_popups()
        result["popups"] = rig.popups[-20:]
    json.dump(result, open(os.path.join(a.out, "smoke.json"), "w"), indent=1, default=str)
    print(json.dumps(dict(stopped=result.get("stopped"), problems=result["problems"], invalid=result.get("invalid"),
                          error_popups=result.get("error_popups"), last=result["phases"][-1:] if result["phases"] else None),
                     indent=1, default=str))


if __name__ == "__main__":
    main()
