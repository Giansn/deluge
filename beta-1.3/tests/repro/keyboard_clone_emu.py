#!/usr/bin/env python3
"""The keyboard view of a cloned Clip, on the v1.3 beta (62a516c2): a crash once the original is deleted. A Clip's
keyboard state has the column controls of the keyboard view (ColumnControlState): the columns themselves and leftCol
and rightCol, pointers to the two shown, which point at the state's own columns. InstrumentClip::copyBasicsFrom()
copies the keyboard state with the implicit copy, pointers and all, so a clone (session view: a clip held and another
row pressed; a pending overdub; the arranger's clone) points at the original's columns. Once the original is deleted
and its memory reused, the clone's keyboard view renders its sidebar through a freed column's vtable: the fuzzer's seed
291 (--mode deep, 7-segment, input 775, on the fourth delivered build) jumped to 0x0c000014 from
ColumnControlsKeyboard::renderSidebarPads() on AFFECT + KEYBOARD.

One boot of the real firmware in the emulator (OLED), make_card's song: in session view the first Clip's pad held and
an empty row's pad pressed (the Clip cloned), the clone's leftCol and rightCol looked at, then the original deleted
(SessionView::removeClip(), as its pad held + the delete would) and the clone's keyboard view opened (its pad, then
KEYBOARD). Checked: the clone made, its two column pointers inside the clone itself (not in the original), the
keyboard view open, and no freeze, crash or access outside the mapped memory.

Usage: keyboard_clone_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot, Inputs and Song; puts the rig on the path)
import oom_robust_emu as oom  # noqa: E402  (call(): a direct call under the rig's watchdog)
import stress_ui_emu as su  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rig, inp = kr.boot(a, "boot")
    emu = rig.emu
    sym = emu.sym
    for name in ("sessionView", "instrumentClipView", "keyboardScreen"):
        rig.ui_names[sym[name]] = name
    song = kr.Song(rig)
    left_off, right_off, clip_size = su.gdb_ints(emu, [
        "list Song::Song", "print (int)&((InstrumentClip*)0)->keyboardState.columnControl.leftCol",
        "print (int)&((InstrumentClip*)0)->keyboardState.columnControl.rightCol", "print (int)sizeof(InstrumentClip)"])
    res = {}
    try:
        res["ui"] = rig.ui_name()
        before = song.clips()
        original = before[0]
        y_from = 0 - song.scroll()
        y_to = next(y for y in range(8) if not 0 <= y + song.scroll() < len(before))  # an empty row
        res["rows"] = (y_from, y_to)
        inp.pad(0, y_from, 100)
        rig.tm(0.1, f"the first Clip's pad 0,{y_from} held")
        inp.pad(0, y_to, 100)
        rig.tm(0.1, f"an empty row's pad 0,{y_to}: the Clip cloned")
        inp.pad(0, y_to, 0)
        inp.pad(0, y_from, 0)
        rig.tm(0.5, "pads released")
        new = [c for c in song.clips() if c not in before]
        res["cloned"] = len(new) == 1
        clone = new[0]
        cols = {"leftCol": emu.u32(clone + left_off), "rightCol": emu.u32(clone + right_off)}
        res["columns"] = {k: ("in the clone" if clone <= v < clone + clip_size else
                              "in the original" if original <= v < original + clip_size else hex(v))
                          for k, v in cols.items()}

        oom.call(rig, "SessionView::removeClip(the original)", sym.find("_ZN11SessionView10removeClipEP4Clip"),
                 (sym["sessionView"], original))
        rig.tm(0.3, "the original deleted")
        res["original_gone"] = original not in song.clips()
        index = song.clips().index(clone)
        res["entered_clone"] = song.enter(inp, index)
        inp.button("KEYBOARD", True)
        rig.tm(0.05, "KEYBOARD on")
        inp.button("KEYBOARD", False)
        rig.tm(0.5, "the clone's keyboard view")
        res["keyboard"] = rig.ui_name()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["invalid"] = [(hex(k[0]), k[1], k[2]) for k in rig.invalid]
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    ok = (res.get("cloned") and set(res.get("columns", {}).values()) == {"in the clone"} and res.get("original_gone")
          and res.get("keyboard") == "keyboardScreen" and not res["invalid"] and not res["problems"])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
