#!/usr/bin/env python3
"""The horizontal encoder in the arranger's automation view only scrolls and zooms the arranger (patch 0108). On the
v1.3 beta (62a516c2) AutomationView::horizontalEncoderAction() handed every turn the editor itself didn't take to
ClipView::horizontalEncoderAction(), also in the arranger (automationView.onArrangerView), where no Clip is shown.
Its cases go through getCurrentClip(): SHIFT edits the Clip's length, and a plain turn first asks the Clip whether it
can scroll (currentlyScrollableAndZoomable(), virtual). After a song load no Clip has been entered and the song has
none: the call went through a null pointer (the fuzzer's seed 125, --mode deep --7seg, input 1112: a jump to
0x0c000004 from ClipView::horizontalEncoderAction()). With a current Clip, SHIFT changed the length of a Clip the
arranger doesn't show.

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track), each time in the automation editor of the arranger (SONG from song view, CLIP, pad 8,7: LPF frequency):
  (1) PADA entered before (the song's current Clip): SHIFT held + scrollX +1, three times: PADA's length stays;
      X_ENC held + scrollX +1: the arranger zooms in, the Clips' zoom stays
  (2) SONG001 loaded (no Clip entered, so no current Clip): scrollX +1, SHIFT held + scrollX +1, X_ENC held +
      scrollX +1: the arranger zooms in; LEARN held + X_ENC (copy the automation), SHIFT + LEARN held + X_ENC (paste)
Throughout: no crash, freeze or hang, and the UI stays the arranger's automation view.
On 62a516c2 with the patches up to 0107: (1) PADA gets longer, (2) crashes at the first turn.

Usage: arranger_automation_turn_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import struct
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
    rig.ui_names[emu.sym["automationView"]] = "automationView"
    rig.ui_names[emu.sym["arrangerView"]] = "arrangerView"
    length_off, zoom_off, on_arranger = su.gdb_ints(emu, [
        "list Song::Song",  # (a context in which gdb finds the types)
        "print (int)&((Clip*)0)->loopLength", "print (int)&((Song*)0)->xZoom",
        "print (int)&automationView.onArrangerView"])
    song = kr.Song(rig)

    def zooms():  # xZoom[NAVIGATION_CLIP], xZoom[NAVIGATION_ARRANGEMENT]
        return struct.unpack("<2I", emu.uc.mem_read(rig.song() + zoom_off, 8))

    def where():
        return rig.ui_name() == "automationView" and emu.u8(on_arranger) == 1

    def press(name, seconds=0.5):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)

    def held(name, then, seconds=0.5):
        """name held while then() runs."""
        inp.button(name, True)
        rig.tm(0.05, f"{name} held")
        then()
        inp.button(name, False)
        rig.tm(seconds, f"{name} released")

    def turn(seconds=0.5):
        inp.turn("scrollX", 1)
        rig.tm(seconds, "scrollX +1")

    def arranger_editor():
        """From song view: the arranger, its automation view, LPF frequency selected (the editor)."""
        press("SONG", 1.0)
        arranger = rig.ui_name()
        press("CLIP", 1.0)
        inp.pad(8, 7, 100)
        rig.tm(0.05, "pad 8,7")
        inp.pad(8, 7, 0)
        rig.tm(0.3, "LPF frequency selected")
        return arranger == "arrangerView" and where()

    res = {}
    try:
        # (1) a current Clip the arranger doesn't show
        entered = song.enter(inp, 0)
        pada = song.clips()[0]
        press("SONG", 1.0)  # (song view)
        editor = arranger_editor()
        length = emu.u32(pada + length_off)
        for _ in range(3):
            held("SHIFT", turn)
        lengths = (length, emu.u32(pada + length_off))
        before = zooms()
        held("X_ENC", turn)
        after = zooms()
        res["1"] = dict(entered=entered, editor=editor, current=hex(song.current()), length=lengths, zoom=(before, after),
                        ok=entered and editor and lengths[0] == lengths[1] and after[1] < before[1]
                        and after[0] == before[0] and where())
        print("1", json.dumps(res["1"]), flush=True)
        # (2) no current Clip
        res["change"] = rig.change_song("SONG001", 1.0)
        current = song.current()
        editor = arranger_editor()
        turn()
        held("SHIFT", turn)
        before = zooms()
        held("X_ENC", turn)
        after = zooms()
        held("LEARN", lambda: press("X_ENC", 0.3))
        held("SHIFT", lambda: held("LEARN", lambda: press("X_ENC", 0.3)))
        res["2"] = dict(editor=editor, current=hex(current), zoom=(before, after),
                        ok=editor and current == 0 and after[1] < before[1] and where())
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "arranger_automation_turn.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
