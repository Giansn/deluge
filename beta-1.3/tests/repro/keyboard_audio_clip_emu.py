#!/usr/bin/env python3
"""KEYBOARD in an audio Clip's automation view, on the v1.3 beta (62a516c2): a crash. AutomationView's KEYBOARD
handler opens the keyboard view for whatever Clip it shows; an audio Clip has none (AudioClipView ignores KEYBOARD),
and the keyboard view takes the current Clip for an InstrumentClip: ColumnControlsKeyboard::renderSidebarPads() reads
its keyboard state's column pointers past the end of the AudioClip and calls through whatever is there. The fuzzer's
seed 291 (--mode deep, 7-segment, input 776, on the fourth delivered build) jumped to 0x0c000014 from there on
AFFECT + KEYBOARD in the audio track's automation view.

One boot of the real firmware in the emulator (OLED), make_card's song: the audio track's Clip entered from session
view, CLIP (its automation view), KEYBOARD. Checked: the audio Clip's view and its automation view open, KEYBOARD
leaves the automation view as it is (no keyboard view for an audio Clip), and no freeze, crash or access outside the
mapped memory.

Usage: keyboard_audio_clip_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
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
    sym = emu.sym
    for name in ("sessionView", "audioClipView", "automationView", "keyboardScreen"):
        rig.ui_names[sym[name]] = name
    song = kr.Song(rig)
    audio_type, = su.gdb_ints(emu, ["list Song::Song", "print (int)OutputType::AUDIO"])

    def press(name, what, wait=0.5):
        inp.button(name, True)
        rig.tm(0.05, f"{what} on")
        inp.button(name, False)
        rig.tm(wait, f"{what} off")
    res = {}
    try:
        clips = song.clips()
        index = next(i for i, c in enumerate(clips)
                     if emu.u8(emu.u32(c + song.output_off) + song.otype_off) == audio_type)
        res["entered"] = song.enter(inp, index)
        res["clip_view"] = rig.ui_name()
        press("CLIP", "CLIP (the audio Clip's automation view)")
        res["automation"] = rig.ui_name()
        press("KEYBOARD", "KEYBOARD")
        res["after_keyboard"] = rig.ui_name()
        rig.tm(0.5, "rendering")
        res["ui_after"] = rig.ui_name()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["invalid"] = [(hex(k[0]), k[1], k[2]) for k in rig.invalid]
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    ok = (res.get("clip_view") == "audioClipView" and res.get("automation") == "automationView"
          and res.get("after_keyboard") == "automationView" and res.get("ui_after") == "automationView"
          and not res["invalid"] and not res["problems"])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
