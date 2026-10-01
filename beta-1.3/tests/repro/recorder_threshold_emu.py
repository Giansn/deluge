#!/usr/bin/env python3
"""The sound editor's RECORD AUDIO with threshold recording on (song menu > THRESHOLD RECORDING > MODE, any level but
OFF) and a quiet input: BACK must end the recorder. Found by the menu walker (menu_walk_emu.py): the walk set the song's
threshold mode, then RECORD AUDIO in the synth's ACTIONS menu never returned (a hang in SampleRecorder::feedAudio()).

Why: since #4678 (55449f1b) threshold recording keeps the audio before the threshold is passed, so numSamplesCaptured is
not 0 while it waits; SampleRecorder::endSyncedRecording(), which BACK calls (AudioRecorder::endRecordingSoon()), then
no longer aborts the empty recording but waits for the rest of it (CAPTURING_DATA_WAITING_TO_STOP), and feedAudio()
resets numSamplesCaptured to the margin every cycle while it waits for the threshold: the stop never completes, the
recorder stays open, and every further BACK does nothing (it only acts while CAPTURING_DATA). On the Deluge: stuck in
the recorder until the input gets loud enough.

The test, on the real firmware in the emulator (OLED): the song menu (session view, SELECT), THRESHOLD RECORDING >
MODE turned one step (LOW), BACK out; the synth clip PADA, its sound editor, ACTIONS > RECORD AUDIO 1 chosen and SELECT
pressed (with horizontal menus twice: the recorder's menu, then its record field). The input is silence. 1.5 s into
the recording BACK's path is taken (fuzz_ui.ModalRecorder: AudioRecorder::endRecordingSoon() at each of the recorder
loop's button reads). PASS: the press returns within 20 s (emulated), the recorder closed and the sound editor current
again, no crash or freeze. On beta-fix2 and 62a516c2 the press never returns (hang).

Usage: recorder_threshold_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..")))  # beta-1.3/tests: rig13, fuzz_ui, menu_walk_emu
import rig13  # noqa: E402,F401
import fuzz_ui  # noqa: E402
import menu_walk_emu as mw  # noqa: E402
import stress_ui_emu as su  # noqa: E402


def choose(w, menu, target, what):
    """The select encoder turned until target is the menu's current item (in a vertical menu; most don't wrap
    around, so towards it)."""
    kids = w.kids(menu)
    for _ in range(len(kids) + 2):
        c = w.cur(menu)
        if c == target:
            return
        if target not in kids or c not in kids:
            break
        w.turn("select", 1 if kids.index(target) > kids.index(c) else -1)
    raise mw.Lost(f"{what}: {w.name(target)} not reached (at {w.name(w.cur(menu) or 0)})")


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
    fuzz_ui.make_card(image, 2, os.path.join(a.out, "card-template.img"))
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    inp = fuzz_ui.Inputs(rig, "v13")
    w = mw.Walker(rig, inp, True, None, print)
    w.null = fuzz_ui.NullPage(rig.emu)
    modal = fuzz_ui.ModalRecorder(rig.emu)
    emu, sym = rig.emu, rig.emu.sym
    emu.uc.ctl_flush_tb()
    mode_off, = su.gdb_ints(emu, ["list Song::Song", "print (int)&((Song*)0)->thresholdRecordingMode"])
    item = lambda n: sym[n]  # noqa: E731  (the menu items are global objects)
    res = dict(elf=a.elf)
    try:
        # The song menu: THRESHOLD RECORDING > MODE one step up (OFF -> LOW)
        if not w.open_context("song"):
            raise mw.Lost("the song menu didn't open")
        root = w.where()[2]
        choose(w, root, item("songThresholdRecordingSubmenu"), "song menu")
        w.press("SELECT_ENC", after=0.1)
        sub = w.where()[2]
        choose(w, sub, item("songThresholdRecordingModeMenu"), "threshold recording")
        w.press("SELECT_ENC", after=0.1)
        w.turn("select", 1)
        res["threshold_mode"] = emu.u8(rig.song() + mode_off)
        w.leave_editor()
        # The synth clip's sound editor: ACTIONS > the recorder menu, its record field
        if not w.open_context("synth"):
            raise mw.Lost("the sound editor didn't open")
        root = w.where()[2]
        choose(w, root, item("soundEditorRootActionsMenu"), "sound editor")
        w.press("SELECT_ENC", after=0.1)
        actions = w.where()[2]
        choose(w, actions, item("sample0RecorderMenu"), "actions")
        # SELECT on RECORD AUDIO 1: with horizontal menus its menu (the record field focused), SELECT again records;
        # without, the recorder at once. The recorder runs inside the press (SELECT acts on release) until it ends
        for _ in range(2):
            res["menu"] = w.name(w.where()[2])
            w._count("SELECT (record)")
            inp.button("SELECT_ENC", True, limit_s=20)
            inp.button("SELECT_ENC", False, limit_s=20)
            if modal.recordings:
                break
            rig.tm(0.1, "menu")
        rig.tm(0.3, "after the recording")
        res["recordings"] = modal.recordings
        res["ui_after"] = w.ui()
        res["ok"] = res["threshold_mode"] != 0 and modal.recordings == 1 and res["ui_after"] == "soundEditor"
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        res["ok"] = False
    except mw.Lost as ex:
        res["lost"] = str(ex)
        res["ok"] = False
    res["problems"] = rig.problems
    res["null_writes"] = w.null.writes
    json.dump(res, open(os.path.join(a.out, "recorder_threshold.json"), "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))
    ok = res["ok"] and not rig.problems
    print("PASS: BACK ends the recorder with threshold recording on" if ok else
          "FAIL: " + (f"{rig.problems[-1].get('kind')} {rig.problems[-1].get('detail')}" if rig.problems else
                      res.get("lost", f"recordings {res.get('recordings')}, ui {res.get('ui_after')}")))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
