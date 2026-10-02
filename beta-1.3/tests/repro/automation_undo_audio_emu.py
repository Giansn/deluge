#!/usr/bin/env python3
"""Undo from an instrument Clip's note velocity editor back into an audio Clip's automation view, on the v1.3 beta
(62a516c2). The automation view keeps its editor type (automationParamType) for whatever Clip it shows. Undo or redo
of an action of another Clip in the same view only makes that Clip current and calls focusRegained()
(ActionLogger: Animation::CHANGE_CLIP), so the type stays NOTE_VELOCITY when that Clip is an audio one, and the note
editor's code takes the AudioClip for an InstrumentClip: its rendering reads note rows from past the AudioClip's end,
a grid pad creates a note row in that foreign array. Found by an audit of the automation view's InstrumentClip casts
after 0023 (the same kind: an audio Clip taken for an InstrumentClip).

One boot of the real firmware in the emulator (OLED), make_card's song: the audio track's Clip entered, CLIP (its
automation view), its LPF frequency pad (that parameter's editor) and a grid pad (an automation edit, undoable); SONG,
the first synth Clip entered, CLIP, an audition pad held and the velocity shortcut pad (15,1) pressed (its note velocity
editor); BACK (undo: the audio Clip's edit reverted, the audio Clip current again in the automation view), then a grid
pad. Checked: the velocity editor was open, undo brought the audio Clip back, and no freeze, crash or access outside
the mapped memory.

Usage: automation_undo_audio_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot, Inputs and Song; puts the rig on the path)
import stress_ui_emu as su  # noqa: E402

LPF_PAD = (8, 7)  # UNPATCHED_LPF_FREQ in the global parameters' shortcuts (an audio Clip's)
VELOCITY_PAD = (15, 1)  # kVelocityShortcutX, kVelocityShortcutY (automation_view.cpp)


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
    for name in ("sessionView", "audioClipView", "automationView", "instrumentClipView"):
        rig.ui_names[sym[name]] = name
    song = kr.Song(rig)
    audio_type, synth_type, type_off, velocity_type = su.gdb_ints(emu, [
        "list AutomationView::inNoteEditor", "print (int)OutputType::AUDIO", "print (int)OutputType::SYNTH",
        "print (int)&((AutomationView*)0)->automationParamType", "print (int)AutomationParamType::NOTE_VELOCITY"])
    view = sym["automationView"]

    def press(name, what, wait=0.5):
        inp.button(name, True)
        rig.tm(0.05, f"{what} on")
        inp.button(name, False)
        rig.tm(wait, f"{what} off")

    def tap(x, y, what, wait=0.3):
        inp.pad(x, y, 100)
        rig.tm(0.05, f"{what} on")
        inp.pad(x, y, 0)
        rig.tm(wait, f"{what} off")

    def otype(c):
        return emu.u8(emu.u32(c + song.output_off) + song.otype_off)
    res = {}
    try:
        clips = song.clips()
        audio = next(c for c in clips if otype(c) == audio_type)
        synth = next(c for c in clips if otype(c) == synth_type)
        res["audio_entered"] = song.enter(inp, song.clips().index(audio))
        press("CLIP", "CLIP (the audio Clip's automation view)")
        res["audio_view"] = rig.ui_name()
        tap(*LPF_PAD, "the LPF frequency pad (its automation editor)")
        tap(4, 3, "a grid pad: an automation edit")
        press("SONG", "SONG (session view)")
        res["synth_entered"] = song.enter(inp, song.clips().index(synth))
        press("CLIP", "CLIP (the synth Clip's automation view)")
        res["synth_view"] = rig.ui_name()
        inp.pad(17, 3, 100)
        rig.tm(0.1, "audition pad 17,3 held")
        tap(*VELOCITY_PAD, "the velocity shortcut (the note velocity editor)")
        inp.pad(17, 3, 0)
        rig.tm(0.3, "audition pad released")
        res["velocity_editor"] = emu.u8(view + type_off) == velocity_type
        press("BACK", "BACK (undo the audio Clip's edit)", 1.0)
        res["after_undo_current"] = "audio" if song.current() == audio else hex(song.current())
        res["after_undo_view"] = rig.ui_name()
        tap(6, 2, "a grid pad")
        res["ui_after"] = rig.ui_name()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["invalid"] = [(hex(k[0]), k[1], k[2]) for k in rig.invalid]
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    ok = (res.get("audio_view") == "automationView" and res.get("synth_view") == "automationView"
          and res.get("velocity_editor") and res.get("after_undo_current") == "audio"
          and res.get("ui_after") == "automationView" and not res["invalid"] and not res["problems"])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
