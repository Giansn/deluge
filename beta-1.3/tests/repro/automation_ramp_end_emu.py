#!/usr/bin/env python3
"""A ramp drawn with two pads in the arranger's automation editor up to the arrangement's end, on the v1.3 beta
(62a516c2): E427. AutomationEditorLayoutModControllable::handleAutomationMultiPadPress() takes the view's length (the
arranger: arrangerView.getMaxLength(), where the arrangement ends) and returns only when the second pad starts after
it; a pad that starts exactly at the end gets through. The last pad is then given "the rest of the pad" as
min(length, its right edge) - its left edge - kParamNodeWidth (3 ticks): 0 - 3. AutoParam::setValueForRegion()
returns only for a position past the parameter's own length (the Song's, longer than the arrangement), so the
negative length reaches homogenizeRegion(), which freezes with E427 (a release build edits the automation with a
negative region). The fuzzer found it on the delivered build (seed 241, --mode deep, input 752; traced: pos 2496,
length -3 from handleAutomationMultiPadPress()).

One boot of the real firmware in the emulator (OLED), the song with an arrangement of 4 bars (every Output one clip
instance, as export_repeat_emu.py writes it): SONG (arranger view), CLIP (its automation view), the song's LPF
frequency pad (its automation editor), the horizontal encoder held and turned out until the arrangement
fills less than the 16 columns, then pad 1,3 held and the first pad after the arrangement's end pressed (a ramp up
to it), both released. Checked: the editor was open, that pad starts exactly at the end, the multi-pad press was
handled, and nothing froze, crashed or hung.

Usage: automation_ramp_end_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_repeat_emu as er  # noqa: E402  (with_arrangement(); puts the rig on the path)
import fat32  # noqa: E402
import fuzz_ui  # noqa: E402
import make_sd  # noqa: E402
import stress_ui_emu as su  # noqa: E402

BARS = 4
LENGTH = BARS * 384  # the arrangement's end (ticks)
NAVIGATION_ARRANGEMENT = 1
SONG_LPF_PAD = (8, 7)  # UNPATCHED_LPF_FREQ in unpatchedGlobalParamShortcuts (modulation/params/param.h)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    files, lengths = make_sd.samples()
    xml, _ = er.with_arrangement(make_sd.song_xml(lengths, 1, 1, False, False, False), LENGTH)
    files["SONGS/DEFAULT.XML"] = xml.encode()
    fat32.build(image, files)

    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    emu = rig.emu
    inp = fuzz_ui.Inputs(rig, "v13")
    sym = emu.sym
    for name in ("automationView", "arrangerView"):
        rig.ui_names[sym[name]] = name
    zoom_off, = su.gdb_ints(emu, ["list Song::Song", "print (int)&((Song*)0)->xZoom"])
    shortcut = SONG_LPF_PAD
    in_editor_fn = sym.find("_ZN14AutomationView18inAutomationEditorEv")
    multi = []
    emu.intercept(sym.find("_ZN37AutomationEditorLayoutModControllable29handleAutomationMultiPadPress"),
                  lambda e: multi.append(1) and None)
    emu.uc.ctl_flush_tb()

    def zoom():
        return struct.unpack("<I", bytes(emu.uc.mem_read(rig.song() + zoom_off + 4 * NAVIGATION_ARRANGEMENT, 4)))[0]

    def press(name, what):
        inp.button(name, True)
        rig.tm(0.05, f"{what} on")
        inp.button(name, False)
        rig.tm(0.5, f"{what} off")
    res = dict(shortcut=shortcut)
    try:
        press("SONG", "SONG (arranger view)")
        res["arranger"] = rig.ui_name()
        press("CLIP", "CLIP (the arranger's automation view)")
        res["view"] = rig.ui_name()
        inp.pad(*shortcut, 100)
        rig.tm(0.05, "parameter pad on")
        inp.pad(*shortcut, 0)
        rig.tm(0.3, "the parameter's automation editor")
        res["in_editor"] = bool(emu.call(in_editor_fn, sym["automationView"]) & 0xFF)
        inp.button("X_ENC", True)
        rig.tm(0.05, "X_ENC held")
        for _ in range(6):  # zoomMagnitude = -offset: a turn left zooms out
            if LENGTH % zoom() == 0 and LENGTH // zoom() < 16:
                break
            inp.turn("scrollX", -1)
            rig.tm(0.3, "zoom out")
        inp.button("X_ENC", False)
        rig.tm(0.3, "X_ENC released")
        res["zoom"] = zoom()
        end_pad = LENGTH // zoom()
        res["end_pad"] = end_pad
        inp.pad(1, 3, 100)
        rig.tm(0.1, "pad 1,3 held")
        inp.pad(end_pad, 3, 100)
        rig.tm(0.1, f"pad {end_pad},3 pressed: a ramp to the arrangement's end")
        inp.pad(end_pad, 3, 0)
        inp.pad(1, 3, 0)
        rig.tm(0.5, "pads released")
        res["ui_after"] = rig.ui_name()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["multi_pad_presses"] = len(multi)
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    at_end = LENGTH % max(res.get("zoom", 1), 1) == 0 and 1 < res.get("end_pad", 99) < 16
    ok = (res.get("arranger") == "arrangerView" and res.get("view") == "automationView" and res.get("in_editor")
          and at_end and multi and not res["problems"])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
