#!/usr/bin/env python3
"""Undo of an edit made in the note row editor, from another clip, goes back to the clip's view (patch 0118). On the
v1.3 beta (62a516c2) an Action records the UI it was made in (getCurrentUI()). The sound editor's note editor and note
row editor may make Actions (they edit notes on the grid), so such an Action recorded the sound editor. Undone from
another clip, ActionLogger::revert() changes clip (CHANGE_CLIP) and made the recorded UI the root UI: the sound editor,
closed long before, opened with no menu item and called through null in SoundEditor::beginScreen() (the fuzzer's seed
260, --mode deep, stop run 11, input 461).

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track):
  (1) PADA's clip view, audition pad 17,6 held + SELECT (the note row editor), a grid pad (a note: an Action), BACK
  (2) SONG, PADB's clip view, BACK (undo): back in PADA's clip view, the note gone
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0117: (2) crashes.

Usage: undo_note_row_editor_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
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
    rig.ui_names[emu.sym["instrumentClipView"]] = "instrumentClipView"
    first_action, = su.gdb_ints(emu, ["list ActionLogger::revert", "print (int)&actionLogger.firstAction[0]"])
    song = kr.Song(rig)

    def press(name, seconds=0.6):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)

    res = {}
    try:
        pada, padb = song.clips()[0], song.clips()[1]
        entered = song.enter(inp, 0)
        inp.pad(17, 6, 100)
        rig.tm(0.1, "audition pad held")
        press("SELECT_ENC", 0.5)
        inp.pad(17, 6, 0)
        rig.tm(0.5, "audition pad released")
        editor = rig.ui_name()
        actions_before = emu.u32(first_action)
        inp.pad(2, 6, 100)
        rig.tm(0.1, "grid pad")
        inp.pad(2, 6, 0)
        rig.tm(0.5, "a note")
        action = emu.u32(first_action)
        press("BACK", 1.0)
        res["1"] = dict(entered=entered, editor=editor, action=hex(action), ui=rig.ui_name(),
                        ok=entered and editor == "soundEditor" and action != 0 and action != actions_before
                        and rig.ui_name() == "instrumentClipView")
        print("1", json.dumps(res["1"]), flush=True)
        press("SONG", 1.0)
        entered_b = song.enter(inp, 1) and song.current() == padb
        press("BACK", 1.5)  # undo
        res["2"] = dict(entered_padb=entered_b, ui=rig.ui_name(), current_is_pada=song.current() == pada,
                        ok=entered_b and rig.ui_name() == "instrumentClipView" and song.current() == pada)
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "undo_note_row_editor.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
