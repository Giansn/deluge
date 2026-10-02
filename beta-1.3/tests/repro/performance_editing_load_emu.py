#!/usr/bin/env python3
"""BACK in the performance view after a song load left its editing mode on, on the v1.3 beta (62a516c2): a crash. The
performance view's editing modes (SHIFT + KEYBOARD, or the sound editor's editing mode) open the view again on top of
a UI, and BACK closes that instance (PerformanceView::buttonAction(): close() while defaultEditingMode). A song load
replaces all UIs without closing it, and the flag stays set. The next time the performance view is the root UI (KEYBOARD
in session view), BACK still takes it for the editing instance and closes it: closeUI() looks for it only above the
root, doesn't find it, and takes the UI "below" the root from before uiNavigationHierarchy[], calling focusRegained()
through whatever is there. The fuzzer's MIDI round 4 (seed 381, --mode deep, OLED, on the sixth delivered build) jumped
to 0x0c000014 from closeUI() on that BACK (editing mode on, LOAD, a song loaded; later KEYBOARD and BACK).

One boot of the real firmware in the emulator (OLED), make_card's song: KEYBOARD (the performance view, the root UI),
SHIFT + KEYBOARD (its editing mode: the view opened again on top), LOAD and SONG001 loaded (rig.change_song(): the song
browser opened on top, the encoder pressed); then KEYBOARD in session view (the performance view as the root UI again)
and BACK; then closeUI() called for the sound editor, which isn't open. Checked: the editing mode open before the load,
the performance view the only UI after KEYBOARD and not in editing mode, still the root UI after BACK and after the
closeUI() (nothing closed), and no freeze, crash or access outside the mapped memory.

Usage: performance_editing_load_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot and Inputs; puts the rig on the path)
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
    for name in ("sessionView", "performanceView", "loadSongUI"):
        rig.ui_names[sym[name]] = name
    editing_off, = su.gdb_ints(emu, ["list Song::Song", "print (int)&((PerformanceView*)0)->defaultEditingMode"])
    view = sym["performanceView"]

    def stack():
        n = emu.u32(sym["numUIsOpen"])
        return [rig.ui_names.get(emu.u32(sym["uiNavigationHierarchy"] + 4 * i), "?") for i in range(min(n, 8))]

    def editing():
        return emu.u8(view + editing_off)

    def press(name, what, wait=0.5):
        inp.button(name, True)
        rig.tm(0.05, f"{what} on")
        inp.button(name, False)
        rig.tm(wait, f"{what} off")
    res = {}
    try:
        press("KEYBOARD", "KEYBOARD (the performance view)")
        res["performance"] = stack()
        inp.button("SHIFT", True)
        rig.tm(0.05, "SHIFT held")
        press("KEYBOARD", "SHIFT + KEYBOARD (its editing mode)")
        inp.button("SHIFT", False)
        rig.tm(0.3, "SHIFT released")
        res["editing"] = dict(stack=stack(), flag=editing())
        res["load"] = bool(rig.change_song("SONG001", 1.0))
        res["after_load"] = dict(stack=stack(), flag=editing())
        press("KEYBOARD", "KEYBOARD in session view (the performance view as the root UI)")
        res["root_again"] = dict(stack=stack(), flag=editing())
        press("BACK", "BACK")
        res["after_back"] = stack()
        rig.tm(0.5, "rendering")
        res["ui_after"] = rig.ui_name()
        # and closeUI() itself, asked to close a UI that isn't open (the sound editor)
        oom.call(rig, "closeUI(the sound editor, not open)", sym.find("_Z7closeUIP2UI"), (sym["soundEditor"],))
        rig.tm(0.3, "after closeUI()")
        res["after_close_not_open"] = stack()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["invalid"] = [(hex(k[0]), k[1], k[2]) for k in rig.invalid]
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    ok = (res.get("performance") == ["performanceView"]
          and res.get("editing") == dict(stack=["performanceView", "performanceView"], flag=1)
          and res.get("root_again") == dict(stack=["performanceView"], flag=0)
          and res.get("after_back") == ["performanceView"] and res.get("ui_after") == "performanceView"
          and res.get("after_close_not_open") == ["performanceView"] and not res["invalid"] and not res["problems"])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
