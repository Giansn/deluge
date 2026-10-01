#!/usr/bin/env python3
"""The arranger's automation view and the clip-type buttons, on the v1.3 beta (62a516c2): E369. In arranger view CLIP
opens the automation view for the song's params (automationView.onArrangerView). AutomationView::buttonAction() still
handles SCALE, KIT, SYNTH, MIDI and CV as in a clip's automation view: on the current clip, through
InstrumentClipMinder::changeOutputType() etc. After a song load no clip has been entered, the song has no current
clip, and the model stack's getTimelineCounter() freezes with E369 (View::changeOutputType()); the fuzzer found it
(seed 1071, --mode deep, input 556, "CV on" in the automation view). With a current clip, the buttons changed a clip
the arranger doesn't show.

One boot of the real firmware in the emulator (OLED), the startup song loaded (no clip entered): session view, SONG
(arranger view), CLIP (its automation view), then CV, MIDI, KIT, SYNTH and SCALE, each pressed and released. Must not
freeze or crash, must stay in the automation view, and the song's clips must keep their output types.

Usage: arranger_automation_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot, Inputs and Song; puts the rig on the path)
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
    rig.ui_names[emu.sym["automationView"]] = "automationView"
    rig.ui_names[emu.sym["arrangerView"]] = "arrangerView"
    song = kr.Song(rig)
    types = lambda: [emu.u8(emu.u32(c + song.output_off) + song.otype_off) for c in song.clips()]  # noqa: E731
    res = dict(current_clip=hex(song.current()), types_before=types())

    def press(name, what):
        inp.button(name, True)
        rig.tm(0.1, f"{what} on")
        inp.button(name, False)
        rig.tm(0.3, f"{what} off")
    try:
        press("SONG", "SONG (arranger view)")
        res["arranger"] = rig.ui_name()
        press("CLIP", "CLIP (arranger automation view)")
        res["automation"] = rig.ui_name()
        for b in ("CV", "MIDI", "KIT", "SYNTH", "SCALE"):
            press(b, b)
        res["ui_after"] = rig.ui_name()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["types_after"] = types()
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    ok = (res.get("arranger") == "arrangerView" and res.get("automation") == "automationView"
          and res.get("ui_after") == "automationView" and not res["problems"]
          and res["types_after"] == res["types_before"])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
