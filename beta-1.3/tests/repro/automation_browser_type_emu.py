#!/usr/bin/env python3
"""A clip whose type changes in the instrument browser comes back into the automation view without the old parameter
(patch 0115). On the v1.3 beta (62a516c2) a synth clip's automation view with a patched parameter selected (e.g. LPF
frequency) opens the synth browser with LOAD + SYNTH; CV there makes the clip a CV clip and closes the browser.
AutomationView::focusRegained() then rendered the old parameter through InstrumentClipMinder::focusRegained() and
looked it up in the CV clip's params: E411 in ModelStackWithThreeMainThings::getPatchedAutoParamFromId() (the fuzzer's
seed 202, --mode deep, stop run 8, input 1022). 0102 resets such a selection when the view opens, not when it regains
focus.

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track):
  (1) PADA: clip view, CLIP (the automation view), pad 8,7 selects LPF frequency (patched), LOAD held + SYNTH (the
      synth browser), CV: back in the automation view, PADA is a CV clip and has no parameter selected
  (2) PADB: the same up to the browser, then BACK without a type change: the selection stays
  (3) PADA, now a CV clip: its automation view, SYNTH there (the type changes at once, without a browser), LPF
      frequency, LOAD held + SYNTH, CV: back in the automation view without the parameter. The view noted a Clip's
      type only when it opened, not when a parameter was selected, so after its own SYNTH it took the CV clip for
      unchanged (the seed's way)
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0114: (1) freezes with E411.

Usage: automation_browser_type_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
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
    rig.ui_names[emu.sym["automationView"]] = "automationView"
    kind_off, id_off, output_off, otype_off, patched, none, cv, synth, lpf = su.gdb_ints(emu, [
        "list Song::Song",  # (a context in which gdb finds the types; quoted: the namespace's names)
        "print (int)&((Clip*)0)->lastSelectedParamKind", "print (int)&((Clip*)0)->lastSelectedParamID",
        "print (int)&((Clip*)0)->output", "print (int)&((Output*)0)->type",
        "print (int)'deluge::modulation::params::Kind::PATCHED'", "print (int)'deluge::modulation::params::Kind::NONE'",
        "print (int)OutputType::CV", "print (int)OutputType::SYNTH",
        "print (int)'deluge::modulation::params::LOCAL_LPF_FREQ'"])
    song = kr.Song(rig)

    def state(clip):
        return dict(kind=emu.u32(clip + kind_off), param=emu.u32(clip + id_off),
                    type=emu.u8(emu.u32(clip + output_off) + otype_off), ui=rig.ui_name())

    def press(name, seconds=0.6):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)

    def select_lpf(index):
        """Enters clip index from song view, CLIP: the automation view, pad 8,7: LPF frequency. Returns its state."""
        entered = song.enter(inp, index)
        press("CLIP", 1.0)
        inp.pad(8, 7, 100)
        rig.tm(0.05, "pad 8,7")
        inp.pad(8, 7, 0)
        rig.tm(0.3, "LPF frequency selected")
        return dict(entered=entered, **state(song.clips()[index]))

    def synth_browser():
        inp.button("LOAD", True)
        rig.tm(0.05, "LOAD held")
        press("SYNTH", 0.3)
        inp.button("LOAD", False)
        rig.tm(1.0, "LOAD held + SYNTH")
        return rig.ui_name()

    res = {}
    try:
        # (1) CV in the synth browser
        before = select_lpf(0)
        browser = synth_browser()
        press("CV", 1.5)
        after = state(song.clips()[0])
        res["1"] = dict(before=before, browser=browser, after=after,
                        ok=before["entered"] and before["kind"] == patched and before["param"] == lpf
                        and before["ui"] == "automationView" and browser == "loadInstrumentPresetUI"
                        and after["type"] == cv and after["kind"] == none and after["ui"] == "automationView")
        print("1", json.dumps(res["1"]), flush=True)
        press("SONG", 1.0)
        # (2) the browser left with BACK: the selection stays
        before = select_lpf(1)
        browser = synth_browser()
        press("BACK", 1.5)
        after = state(song.clips()[1])
        res["2"] = dict(before=before, browser=browser, after=after,
                        ok=before["entered"] and before["kind"] == patched and browser == "loadInstrumentPresetUI"
                        and after["kind"] == patched and after["param"] == lpf and after["ui"] == "automationView")
        print("2", json.dumps(res["2"]), flush=True)
        # (3) PADA, a CV clip since (1): SYNTH in its automation view (the type changes at once), LPF frequency, LOAD
        # held + SYNTH, CV
        press("SONG", 1.0)
        entered = song.enter(inp, 0)
        if rig.ui_name() != "automationView":  # (a clip left in its automation view enters it again)
            press("CLIP", 1.0)
        press("SYNTH", 1.0)
        inp.pad(8, 7, 100)
        rig.tm(0.05, "pad 8,7")
        inp.pad(8, 7, 0)
        rig.tm(0.3, "LPF frequency selected")
        before = dict(entered=entered, **state(song.clips()[0]))
        browser = synth_browser()
        press("CV", 1.5)
        after = state(song.clips()[0])
        res["3"] = dict(before=before, browser=browser, after=after,
                        ok=before["entered"] and before["type"] == synth and before["kind"] == patched
                        and before["ui"] == "automationView" and browser == "loadInstrumentPresetUI"
                        and after["type"] == cv and after["kind"] == none and after["ui"] == "automationView")
        print("3", json.dumps(res["3"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "automation_browser_type.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2", "3") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
