#!/usr/bin/env python3
"""Freezes on the v1.3 beta (62a516c2) that need no user error: out of external RAM, a garbage cluster pointer, the
OLED's PIC handshake out of step. One boot of the real firmware in the emulator (OLED), the startup song loaded, then:

(c) The OLED frame queue and its PIC handshake (oled_low_level.c, oled.c), each from a state set up directly and
    restored afterwards; on a beta build each check below was a FREEZE_WITH_ERROR:
  c1 a SELECT reply (the OLED_LOW_LEVEL timer, as deluge.cpp sets it on the PIC's reply) with no frame queued (a
     duplicate or late reply): "OLED frame queue slot is empty". Must not freeze and must deselect again.
  c2 the OLED DMA's completion interrupt (oledTransferComplete()) with the read position on an empty slot before the
     write position (what a double advance leaves): "OLED frame queue slot is empty". Must skip it and deselect.
  c3 a frame enqueued (OLED::sendMainImage()) on an idle bus while a DESELECT reply is still awaited: "OLED message
     already pending". Must not freeze and must start the SELECT.
  c4 a frame enqueued with the queue full: "FULL". Must drop the frame.
(b) SampleCluster::getCluster() on a SampleCluster whose cluster pointer is outside the stealable region (planted:
    the external region's start + 64, on a cluster not loaded): the always-on FREEZE_WITH_ERROR("invalid") (#4952).
    Must return a new Cluster in the stealable region and keep it.
(a) Out of external RAM: GeneralMemoryAllocator::allocExternal() made to fail (return null) while the user opens and
    scrolls menus and browsers (OOM_ACTIONS). The firmware's operator new (memory/operators.cpp, #4598) and the
    deluge::vector etc. allocator (memory/external_allocator.h) only used allocExternal() and threw BAD_ALLOC, which
    nothing catches there: std::terminate() -> Terminate() -> freezeWithError("TERM"). On 62a516c2 the sound editor's
    gate menu (gate::Selection::getOptions(), a deluge::vector) is the first to hit it, the song browser's
    std::strings (FavouritesManager) next. Must survive (both fall back to the stealable region), and operator new
    must have been called during the window (else the check proves nothing). An "insufficient RAM" error popup is
    fine: String (d_string.cpp) still uses allocExternal() alone, but fails gracefully.

Usage: oom_robust_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR [--discover]
  --discover   no OOM window: only counts operator new calls per action (to choose OOM_ACTIONS)
Last line: PASS (all checks hold) or FAIL (which ones didn't); exit 0/1."""
import argparse
import collections
import json
import os
import struct
import sys

RIG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # beta-1.3/tests
sys.path.insert(0, RIG)
import rig13  # noqa: E402,F401  (sets up the paths of mastertune's rig)
import fuzz_ui  # noqa: E402
import song_emu as se  # noqa: E402
import stress_ui_emu as su  # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,  # noqa: E402
                               UC_ARM_REG_R3, UC_ARM_REG_SP)

SELECT, DESELECT, NONE = 248, 249, 256
QUEUE = 32  # OLED_FRAME_QUEUE_SIZE


def call(rig, what, address, regs=(), stack=(), limit_s=5):
    """A firmware function called directly (up to 4 register arguments, the rest on the stack), under the rig's
    watchdog: a freeze or crash raises su.Stop."""
    emu = rig.emu
    uc = emu.uc

    def run():
        se.drain_uarts(emu)
        emu.uc.mem_write(rig.task_at, struct.pack("<b", rig.task_slot))
        for reg, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), regs):
            uc.reg_write(reg, v & 0xFFFFFFFF)
        sp = se.PROGRAM_STACK_TOP - 256
        if stack:
            uc.mem_write(sp, struct.pack(f"<{len(stack)}I", *(v & 0xFFFFFFFF for v in stack)))
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, se.STOP | 1)
        emu.run(address | 1, se.STOP, 0)
        return uc.reg_read(UC_ARM_REG_R0)
    return rig.guard(what, run, limit_s)


class Oled:
    """The OLED queue and handshake variables, saved and restored around each check."""

    def __init__(self, rig):
        self.rig, emu = rig, rig.emu
        sym = emu.sym
        self.v = {n: sym[n] for n in ("oledFrameQueue", "oledFrameQueueReadPos", "oledFrameQueueWritePos",
                                      "oledWaitingForMessage", "oledPendingMessageToSend", "spiBusCurrentlySending",
                                      "oled_sending", "last_message_sent", "cvPriorityPending")}
        self.needs_sending = sym["_ZN6deluge3hid7display4OLED12needsSendingE"]
        self.send_main_image = sym["_ZN6deluge3hid7display4OLED13sendMainImageEv"]
        self.transfer_complete = sym["oledTransferComplete"]
        self.routine = sym.find("_ZN14UITimerManager7routineEv")
        (self.timer_trigger, self.timer_active, self.next_event, self.trigger_size) = se.gdb_values(emu, [
            "(int)&uiTimerManager.timers_._M_elems[(int)TimerName::OLED_LOW_LEVEL].triggerTime",
            "(int)&uiTimerManager.timers_._M_elems[(int)TimerName::OLED_LOW_LEVEL].active",
            "(int)&uiTimerManager.timeNextEvent",
            "sizeof(uiTimerManager.timeNextEvent)"])
        self.sample_timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
        # Set from the hardware at boot: the emulated Deluge has none (the rig swaps the display to the OLED), so it
        # is set while the PIC reply's timer runs, as on an OLED Deluge (ui_timer_manager.cpp checks it)
        self.have_oled = sym["_ZN6deluge3hid7display16have_oled_screenE"]

    def u8(self, n):
        return self.rig.emu.u8(self.v[n])

    def i32(self, n):
        return struct.unpack("<i", self.rig.emu.uc.mem_read(self.v[n], 4))[0]

    def w8(self, n, value):
        self.rig.emu.uc.mem_write(self.v[n], bytes([value & 0xFF]))

    def w32(self, n, value):
        self.rig.emu.uc.mem_write(self.v[n], struct.pack("<i", value))

    def save(self):
        emu = self.rig.emu
        return (bytes(emu.uc.mem_read(self.v["oledFrameQueue"], 4 * QUEUE)), emu.u8(self.have_oled),
                {n: self.u8(n) for n in ("oledFrameQueueReadPos", "oledFrameQueueWritePos", "spiBusCurrentlySending",
                                         "oled_sending", "cvPriorityPending")},
                {n: self.i32(n) for n in ("oledWaitingForMessage", "oledPendingMessageToSend", "last_message_sent")})

    def restore(self, state):
        queue, have_oled, b, w = state
        self.rig.emu.uc.mem_write(self.v["oledFrameQueue"], queue)
        self.rig.emu.uc.mem_write(self.have_oled, bytes([have_oled]))
        for n, x in b.items():
            self.w8(n, x)
        for n, x in w.items():
            self.w32(n, x)

    def queue(self, entries, read=0):
        """The queue holding these entries from read on (the other slots empty)."""
        slots = [0] * QUEUE
        for i, e in enumerate(entries):
            slots[(read + i) % QUEUE] = e
        self.rig.emu.uc.mem_write(self.v["oledFrameQueue"], struct.pack(f"<{QUEUE}I", *slots))
        self.w8("oledFrameQueueReadPos", read)
        self.w8("oledFrameQueueWritePos", (read + len(entries)) % QUEUE)

    def fire_timer(self):
        """The OLED_LOW_LEVEL timer due now (as deluge.cpp sets it on the awaited PIC reply), then the UI timers'
        routine: oledLowLevelTimerCallback()."""
        emu = self.rig.emu
        now = emu.u32(self.sample_timer)
        emu.uc.mem_write(self.timer_trigger, struct.pack("<I", (now - 10) & 0xFFFFFFFF))
        emu.uc.mem_write(self.timer_active, b"\x01")
        emu.uc.mem_write(self.next_event, struct.pack("<I", (now - 10) & 0xFFFFFFFF)[:self.trigger_size])
        emu.uc.mem_write(self.have_oled, b"\x01")
        call(self.rig, "UI timers: OLED_LOW_LEVEL", self.routine)

    def state(self):
        return dict(read=self.u8("oledFrameQueueReadPos"), write=self.u8("oledFrameQueueWritePos"),
                    waiting=self.i32("oledWaitingForMessage"), pending=self.i32("oledPendingMessageToSend"),
                    bus=self.u8("spiBusCurrentlySending"))


def check(results, name, fn):
    """Runs one check: its verdict (ok, detail), or the freeze/crash that stopped it."""
    try:
        ok, detail = fn()
    except su.Stop:
        rig = results["_rig"]
        p = rig.problems[-1] if rig.problems else {}
        ok, detail = False, f"{p.get('kind')}: {p.get('detail')}"
    results[name] = dict(ok=ok, detail=detail)
    print(f"{name}: {'ok' if ok else 'FAILED'} - {detail}", flush=True)


def oled_checks(rig, results):
    o = Oled(rig)
    saved = o.save()
    print("OLED state before:", o.state(), flush=True)

    def c1():
        o.queue([], read=5)
        o.w32("oledWaitingForMessage", SELECT)
        o.w32("oledPendingMessageToSend", 0)
        o.w8("spiBusCurrentlySending", 1)
        o.w8("oled_sending", 1)
        o.fire_timer()
        s = o.state()
        return s["pending"] == DESELECT and s["read"] == s["write"] == 5, s

    def c2():
        o.queue([0, 0], read=9)  # Read != write, but the slots are empty
        o.w32("oledWaitingForMessage", NONE)
        o.w32("oledPendingMessageToSend", 0)
        o.w32("last_message_sent", SELECT)
        o.w8("cvPriorityPending", 0)
        o.w8("spiBusCurrentlySending", 1)
        o.w8("oled_sending", 1)
        call(rig, "oledTransferComplete", o.transfer_complete, (0,))
        s = o.state()
        return s["pending"] == DESELECT and s["read"] == s["write"] == 11, s

    def c3():
        o.queue([], read=3)
        o.w32("oledWaitingForMessage", DESELECT)
        o.w32("oledPendingMessageToSend", 0)
        o.w8("spiBusCurrentlySending", 0)
        o.w8("oled_sending", 0)
        o.w8("cvPriorityPending", 0)
        rig.emu.uc.mem_write(o.needs_sending, b"\x01")
        call(rig, "OLED::sendMainImage (bus idle, DESELECT awaited)", o.send_main_image)
        s = o.state()
        return s["pending"] == SELECT and s["write"] == 4, s

    def c4():
        o.queue([0x100 + 4 * i for i in range(QUEUE - 1)], read=7)  # Full: 31 distinct dummy frames
        o.w8("spiBusCurrentlySending", 1)
        rig.emu.uc.mem_write(o.needs_sending, b"\x01")
        call(rig, "OLED::sendMainImage (queue full)", o.send_main_image)
        s = o.state()
        return s["read"] == 7 and s["write"] == 6, s

    for name, fn in (("c1 SELECT reply, nothing queued", c1), ("c2 DMA done, empty slot queued", c2),
                     ("c3 SELECT while DESELECT awaited", c3), ("c4 queue full", c4)):
        check(results, name, fn)
        o.restore(saved)


def cluster_check(rig, results):
    emu = rig.emu
    sym = emu.sym
    stealable, external = 0, 2  # MEMORY_REGION_STEALABLE, MEMORY_REGION_EXTERNAL (#defines, not in the debug info)
    (files_off, clusters_off, cluster_off, type_off, sample_type, start_off, end_off,
     region_size) = se.gdb_values(emu, [
        "(int)&((AudioFileManager*)0)->audioFiles", "(int)&((Sample*)0)->clusters",
        "(int)&((SampleCluster*)0)->cluster", "(int)&((AudioFile*)0)->type", "(int)AudioFileType::SAMPLE",
        "(int)&((MemoryRegion*)0)->start", "(int)&((MemoryRegion*)0)->end", "sizeof(MemoryRegion)"])
    _, _, memory, count, msize, mstart, esize = rig.heap_offsets
    gma = sym["_ZZN22GeneralMemoryAllocator3getEvE22generalMemoryAllocator"]
    regions_off, = se.gdb_values(emu, ["(int)&((GeneralMemoryAllocator*)0)->regions"])

    def region(r):
        a = gma + regions_off + r * region_size
        return emu.u32(a + start_off), emu.u32(a + end_off)

    def array(addr):
        mem, n, size, first, es = (emu.u32(addr + o) for o in (memory, count, msize, mstart, esize))
        return [mem + ((first + i) % max(size, 1)) * es for i in range(n)]

    steal_lo, steal_hi = region(stealable)
    ext_lo, _ = region(external)
    target = None
    for element in array(sym["audioFileManager"] + files_off):
        f = emu.u32(element)  # NamedThingVectorElement::namedThing
        if emu.u8(f + type_off) != sample_type:
            continue
        scs = array(f + clusters_off)
        for i, sc in enumerate(scs):
            if i >= 2 and emu.u32(sc + cluster_off) == 0:
                target = (f, i, sc)
        if target:
            break
    if not target:
        results["b garbage cluster pointer"] = dict(ok=False, detail="no sample with an unloaded cluster found")
        print("b: no target", flush=True)
        return
    f, i, sc = target
    fake = ext_lo + 64
    emu.uc.mem_write(sc + cluster_off, struct.pack("<I", fake))
    get_cluster = sym.find("_ZN13SampleCluster10getClusterEP6SamplemlmP5Error")
    loads = se.gdb_values(emu, ["(int)CLUSTER_ENQUEUE"])[0]

    def b():
        r = call(rig, "SampleCluster::getCluster (pointer outside the stealable region)", get_cluster,
                 (sc, f, i, loads), (0xFFFFFFFF, 0))
        kept = emu.u32(sc + cluster_off)
        return (steal_lo <= r < steal_hi and kept == r), dict(returned=hex(r), kept=hex(kept), cluster_index=i,
                                                              stealable=(hex(steal_lo), hex(steal_hi)))
    check(results, "b garbage cluster pointer", b)
    if emu.u32(sc + cluster_off) == fake:
        emu.uc.mem_write(sc + cluster_off, b"\0\0\0\0")  # Not recovered: undo the plant for what follows


# Menus and browsers, each its own UI action (Inputs); a pause of the task manager after each (the OLED rendering)
OOM_ACTIONS = [
    ("SHIFT on", lambda inp: inp.button("SHIFT", True)),
    ("SELECT_ENC on (settings)", lambda inp: inp.button("SELECT_ENC", True)),
    ("SELECT_ENC off", lambda inp: inp.button("SELECT_ENC", False)),
    ("SHIFT off", lambda inp: inp.button("SHIFT", False)),
    ("select +1", lambda inp: inp.turn("select", 1)),
    ("select +1", lambda inp: inp.turn("select", 1)),
    ("SELECT_ENC on (enter)", lambda inp: inp.button("SELECT_ENC", True)),
    ("SELECT_ENC off", lambda inp: inp.button("SELECT_ENC", False)),
    ("select +1", lambda inp: inp.turn("select", 1)),
    ("BACK on", lambda inp: inp.button("BACK", True)),
    ("BACK off", lambda inp: inp.button("BACK", False)),
    ("BACK on", lambda inp: inp.button("BACK", True)),
    ("BACK off", lambda inp: inp.button("BACK", False)),
    ("LOAD on (song browser)", lambda inp: inp.button("LOAD", True)),
    ("LOAD off", lambda inp: inp.button("LOAD", False)),
    ("select +1", lambda inp: inp.turn("select", 1)),
    ("BACK on", lambda inp: inp.button("BACK", True)),
    ("BACK off", lambda inp: inp.button("BACK", False)),
    ("tempo +1", lambda inp: inp.turn("tempo", 1)),
]


def oom_check(rig, inp, results, discover):
    emu = rig.emu
    sym = emu.sym
    state = dict(oom=False, failed=0, news=0)
    callers = collections.Counter()
    ext_callers = collections.Counter()  # Who called allocExternal() while it failed

    def on_alloc_external(e):
        if state["oom"]:
            state["failed"] += 1
            ext_callers[e.sym.name_at(e.uc.reg_read(UC_ARM_REG_LR)).split("(")[0]] += 1
            return 0
        return None

    def on_new(e):
        if state["oom"] or discover:
            state["news"] += 1
            callers[e.sym.name_at(e.uc.reg_read(UC_ARM_REG_LR)).split("(")[0]] += 1
        return None
    emu.intercept(sym.find("_ZN22GeneralMemoryAllocator13allocExternalEm"), on_alloc_external)
    emu.intercept(sym["_Znwj"], on_new)
    emu.uc.ctl_flush_tb()
    per_action = []

    def run():
        state["oom"] = not discover
        try:
            for name, fn in OOM_ACTIONS:
                before = state["news"]
                fn(inp)
                rig.tm(0.15, f"after {name}")
                per_action.append((name, state["news"] - before, rig.ui_name()))
        finally:
            state["oom"] = False
        detail = dict(operator_new_calls=state["news"], alloc_external_failed=state["failed"],
                      per_action=per_action, callers=callers.most_common(8))
        return (state["news"] > 0 and not discover), detail
    check(results, "a out of external RAM (operator new, deluge::vector)", run)
    print("per action (name, operator new calls, UI after):", per_action, flush=True)
    print("operator new callers:", callers.most_common(8), "allocExternal callers:", ext_callers.most_common(8),
          flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--discover", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    fuzz_ui.make_card(image, 2, os.path.join(a.out, "card-template.img"))
    results = {}
    try:
        rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    except (su.Stop, SystemExit) as ex:
        print(f"boot failed: {ex}")
        print("FAIL: boot")
        return 1
    results["_rig"] = rig
    inp = fuzz_ui.Inputs(rig, "v13")  # it wakes v1.3's encoder task after each turn
    rig.tm(0.3, "settle")
    if not a.discover:
        oled_checks(rig, results)
        cluster_check(rig, results)
    oom_check(rig, inp, results, a.discover)
    del results["_rig"]
    out = dict(results=results, problems=rig.problems, error_popups=rig.error_popups(),
               invalid=[dict(page=hex(k[0]), at=k[1], during=k[2], n=n) for k, n in rig.invalid.most_common(10)])
    json.dump(out, open(os.path.join(a.out, "oom_robust.json"), "w"), indent=1, default=str)
    if a.discover:
        print("DISCOVER done")
        return 0
    failed = [n for n, r in results.items() if not r["ok"]]
    if failed:
        print("FAIL: " + "; ".join(failed))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
