#!/usr/bin/env python3
"""Patch 0123, fix 5 (upstream #4518): a synth preset loaded into a kit row while the browser stays open. Is the
view's active mod controllable (view.activeModControllableModelStack.modControllable) the freed old SoundDrum?

Sequence (device/analysis/2026-10-01-v13-stale-pointers.md, case A/K): session view, scrollY +9 (KIT on y=6), pad 0,6
(enter the kit clip), AFFECT (row mode), audition pad 17,0 held + SYNTH (the synth browser opens and swaps the drum:
swap 1), select encoder +1 (the next preset into the same row, browser still open: swap 2). MemoryRegion::dealloc()
is hooked to record freed blocks; after each swap the pointer is checked against them and against the allocator's
empty spaces.

Usage: synth_kit_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR"""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(TESTS, "stress", "ui"))
sys.path.insert(0, os.path.join(TESTS, "song"))
import song_emu as se  # noqa: E402
import stress_ui_emu as su  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R1  # noqa: E402

import ui_inputs as ui  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    ui.make_card(image, 2, os.path.join(a.out, "card-template.img"))
    rig = ui.boot(a.elf, a.tools, a.build, image)
    emu, sym = rig.emu, rig.emu.sym
    rig.ui_names[sym["loadInstrumentPresetUI"]] = "loadInstrumentPresetUI"
    inp = ui.Inputs(rig)
    mc_off, drum_off, err_off = se.gdb_values(emu, [
        "(int)&((View*)0)->activeModControllableModelStack.modControllable",
        "(int)&loadInstrumentPresetUI.soundDrumToReplace - (int)&loadInstrumentPresetUI",
        "(int)&loadInstrumentPresetUI.currentInstrumentLoadError - (int)&loadInstrumentPresetUI"])
    view, browser = sym["view"], sym["loadInstrumentPresetUI"]
    clips_off, current_off, yscroll_off, output_off, otype_off, kit_type = su.gdb_ints(emu, [
        "list Song::Song", "print (int)&((Song*)0)->sessionClips", "print (int)&((Song*)0)->currentClip",
        "print (int)&((Song*)0)->songViewYScroll", "print (int)&((Clip*)0)->output", "print (int)&((Output*)0)->type",
        "print (int)OutputType::KIT"])
    mc = lambda: emu.u32(view + mc_off)  # noqa: E731
    freed = []  # (address, size from its allocator header, during)
    swaps = []

    def on_dealloc(e):
        p = e.uc.reg_read(UC_ARM_REG_R1)
        freed.append((p, e.u32(p - 4) & 0x3FFFFFFF, rig.action_name))
    emu.intercept(sym["_ZN12MemoryRegion7deallocEPv"], on_dealloc)

    def on_swap(e):
        swaps.append(dict(during=rig.action_name, old_drum=emu.u32(browser + drum_off), mc_at_entry=mc(),
                          freed_from=len(freed)))
    emu.intercept(sym.find("_ZN22LoadInstrumentPresetUI21performLoadSynthToKit"), on_swap)
    emu.uc.ctl_flush_tb()

    def empty_spaces():
        region_size, empty, memory, count, msize, mstart, esize = rig.heap_offsets
        base = sym["_ZZN22GeneralMemoryAllocator3getEvE22generalMemoryAllocator"]
        out = []
        for r in range(3):
            arr = base + r * region_size + empty
            mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
            for i in range(n):
                rec = mem + ((first + i) % max(size, 1)) * es
                out.append((emu.u32(rec + 4), emu.u32(rec)))  # (address, length)
        return out

    def state(label):
        p = mc()
        sw = swaps[-1] if swaps else {}
        new_drum = emu.u32(browser + drum_off)
        frees = [f for f, _, _ in freed[sw.get("freed_from", 0):]]
        in_freed = any(f <= p < f + n for f, n, _ in freed[sw.get("freed_from", 0):])
        in_empty = any(addr <= p < addr + length for addr, length in empty_spaces())
        err = struct.unpack("<i", emu.uc.mem_read(browser + err_off, 4))[0]
        r = dict(label=label, ui=rig.ui_name(), swaps=len(swaps), dir=rig.string(sym["_ZN7Browser10currentDirE"]),
                 file=rig.browser_name(), load_error=err, frees_in_swap=len(frees), mod_controllable=hex(p),
                 old_drum=hex(sw.get("old_drum", 0)), mc_at_swap_entry=hex(sw.get("mc_at_entry", 0)),
                 new_drum=hex(new_drum), points_at_old_drum=p == sw.get("old_drum"), points_at_new_drum=p == new_drum,
                 old_drum_freed_in_swap=sw.get("old_drum") in frees, mc_in_block_freed_in_swap=in_freed,
                 mc_in_free_space_now=in_empty, header=hex(emu.u32(p - 4)) if p else None)
        print(json.dumps(r), flush=True)
        return r

    res = dict(elf=a.elf, steps=[])
    try:
        # The kit clip's row: clip i sits on y = i - songViewYScroll (make_sd's song with --synths 2: clips 0-1
        # synths, 2 KIT, 3 LOOP; it loads with yScrollSongView=-7). Scrolled until the kit is on y=6.
        song = rig.song()
        arr = song + clips_off
        _, _, memory, count, msize, mstart, esize = rig.heap_offsets
        mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
        clips = [emu.u32(mem + ((first + i) % max(size, 1)) * es) for i in range(n)]
        otype = lambda c: emu.u8(emu.u32(c + output_off) + otype_off)  # noqa: E731
        kit_i = next(i for i, c in enumerate(clips) if otype(c) == kit_type)
        scroll = lambda: struct.unpack("<i", emu.uc.mem_read(song + yscroll_off, 4))[0]  # noqa: E731
        res["kit_clip_index"], res["scroll_at_load"] = kit_i, scroll()
        for _ in range(12):
            if kit_i - scroll() == 6:
                break
            inp.turn("scrollY", 1 if kit_i - scroll() > 6 else -1)
            rig.tm(0.05, "scrollY")
        res["scroll"] = scroll()
        inp.pad(0, 6, 100)
        rig.tm(0.05, "pad 0,6 held")
        inp.pad(0, 6, 0)
        rig.tm(1.0, "enter clip")
        cur = emu.u32(song + current_off)
        res["steps"].append(dict(step="pad 0,6", ui=rig.ui_name(), current_is_kit_clip=cur == clips[kit_i]))
        inp.button("AFFECT", True)
        rig.tm(0.05, "AFFECT held")
        inp.button("AFFECT", False)
        rig.tm(0.2, "row mode")
        res["steps"].append(dict(step="AFFECT", ui=rig.ui_name(), mc=hex(mc())))
        inp.pad(17, 0, 100)
        rig.tm(0.1, "audition pad held")
        inp.button("SYNTH", True)
        rig.tm(0.1, "SYNTH held")
        inp.button("SYNTH", False)
        rig.tm(0.5, "browser open")
        inp.pad(17, 0, 0)
        rig.tm(0.5, "browser open, pad released")
        res["swap1"] = state("swap 1 (browser opened)")
        # The browser lists the song's own synths first (PADA, PADB: in memory, no file; their load fails and keeps
        # the old drum), then the preset files: the select encoder turned on until two swaps took a file
        res["ok_swaps"] = []
        for i in range(8):
            inp.turn("select", 1)
            rig.tm(1.0, "select +1")
            r = state(f"select +1 #{i + 1}")
            if r["new_drum"] != r["old_drum"]:
                res["ok_swaps"].append(r)
                if len(res["ok_swaps"]) == 2:
                    break
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    res["error_popups"] = rig.error_popups()
    res["swap_log"] = [dict(s, old_drum=hex(s["old_drum"]), mc_at_entry=hex(s["mc_at_entry"])) for s in swaps]
    json.dump(res, open(os.path.join(a.out, "synth_kit.json"), "w"), indent=1, default=str)
    print(json.dumps(dict(steps=res["steps"], problems=res["problems"], popups=res["error_popups"][:5]), default=str))
    swaps_ok = [s["points_at_new_drum"] and not s["mc_in_free_space_now"] for s in res["ok_swaps"]]
    ok = len(swaps_ok) == 2 and all(swaps_ok) and not res["problems"]
    for s in res["ok_swaps"]:
        print(f"  {s['file']}: the view's mod controllable {s['mod_controllable']}, the new drum {s['new_drum']}"
              f"{', in freed memory' if s['mc_in_free_space_now'] else ''}")
    print("PASS: after both swaps the view points at the new drum" if ok else
          "FAIL: the view still points at a freed drum (or the swaps didn't happen)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
