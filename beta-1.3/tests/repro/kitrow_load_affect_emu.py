#!/usr/bin/env python3
"""A kit row's sample browser works on the row's new drum, also with Affect Entire on (patch 0114). On the v1.3 beta
(62a516c2) every grid pad press in a kit clip view calls SoundEditor::potentialShortcutPadAction(), which sets
soundEditor.setupKitGlobalFXMenu while Affect Entire is on, even when the pad is no shortcut (a note); only the sound
editor's exit clears it. An audition pad held + LOAD then makes a new drum for that row and calls soundEditor.setup()
for its file selector: with the flag set, setup() took the kit's own FX, left currentSound null, and the sample
browser opened anyway. Choosing a sample (or the select encoder with auto-load on, which the release of that LOAD
press turns on) called currentSound->killAllVoices() through null: a crash in SampleBrowser::claimCurrentFile() (the
fuzzer's seed 186, --mode deep, stop run 7, input 291).

One boot of the real firmware in the emulator (OLED), the kit clip of the card's song:
  (1) Affect Entire on, a grid pad pressed (a note), audition pad 17,1 held + LOAD: the sample browser is open on the
      row's new drum (soundEditor.currentSound is the Sound of the kit's selected SoundDrum, not null)
  (2) the select encoder +1 (the next sample, auto-loaded), then pressed: the sample kept, the sound editor open on
      that row's source menu (v1.3 goes there after a file is chosen)
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0113 and 0024: (1) currentSound null, (2) a crash.

Usage: kitrow_load_affect_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot(), Inputs and Song)

se, su = kr.se, kr.su


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    a.elf, a.out = os.path.abspath(a.elf), os.path.abspath(a.out)
    os.makedirs(a.out, exist_ok=True)
    rig, inp = kr.boot(a, "boot")
    emu = rig.emu
    rig.ui_names[emu.sym["sampleBrowser"]] = "sampleBrowser"
    rig.ui_names[emu.sym["instrumentClipView"]] = "instrumentClipView"
    current_sound, kit_fx_flag, affect_off, output_off, selected_off, sound_drum_size = su.gdb_ints(emu, [
        "list InstrumentClipView::enterDrumCreator", "print (int)&soundEditor.currentSound",
        "print (int)&soundEditor.setupKitGlobalFXMenu", "print (int)&((InstrumentClip*)0)->affectEntire",
        "print (int)&((Clip*)0)->output", "print (int)&((Kit*)0)->selectedDrum", "print (int)sizeof(SoundDrum)"])
    vtables, vtables_size = emu.sym.by_name["_ZTV9SoundDrum"]  # its Sound part's vptr points into these
    song = kr.Song(rig)

    def press(name, seconds=0.5):
        inp.button(name, True)
        rig.tm(0.05, f"{name} held")
        inp.button(name, False)
        rig.tm(seconds, name)

    res = {}
    try:
        kit_i = next(i for i, c in enumerate(song.clips()) if song.is_kit(c))
        res["kit_entered"] = song.enter(inp, kit_i)
        clip = song.current()
        if not emu.u8(clip + affect_off):
            press("AFFECT")
        affect = emu.u8(clip + affect_off)
        inp.pad(3, 2, 100)  # a note, no shortcut
        rig.tm(0.1, "grid pad")
        inp.pad(3, 2, 0)
        rig.tm(0.3, "grid pad released")
        flag = emu.u8(kit_fx_flag)
        inp.pad(17, 1, 100)
        rig.tm(0.1, "audition pad held")
        press("LOAD", 0.5)
        inp.pad(17, 1, 0)
        rig.tm(0.5, "audition pad released")
        drum = emu.u32(emu.u32(clip + output_off) + selected_off)
        sound = emu.u32(current_sound)
        res["1"] = dict(kit_entered=res["kit_entered"], affect_entire=affect, kit_fx_flag_after_pad=flag,
                        ui=rig.ui_name(), selected_drum=hex(drum), current_sound=hex(sound),
                        vptr=hex(emu.u32(sound)) if sound else None, sound_drum_vtables=hex(vtables),
                        ok=res["kit_entered"] and affect == 1 and rig.ui_name() == "sampleBrowser" and sound != 0
                        and vtables <= emu.u32(sound) < vtables + vtables_size and 0 < drum - sound < sound_drum_size)
        print("1", json.dumps(res["1"]), flush=True)
        inp.turn("select", 1)
        rig.tm(1.0, "select +1")
        turned = rig.ui_name()
        press("SELECT_ENC", 1.0)
        # A file chosen for a new row goes on to the sound editor's source menu (SoundEditor::setup() with the file
        # selector sets sampleBrowser.parentMenuHeadingTo)
        res["2"] = dict(after_turn=turned, after_select=rig.ui_name(), current_sound=hex(emu.u32(current_sound)),
                        ok=turned == "sampleBrowser" and rig.ui_name() == "soundEditor"
                        and emu.u32(current_sound) == sound)
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "kitrow_load_affect.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
