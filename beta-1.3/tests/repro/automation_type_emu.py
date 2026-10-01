#!/usr/bin/env python3
"""A clip whose type changed since the automation view showed it opens without the old parameter (patch 0102). On the
v1.3 beta (62a516c2) the transitions into a clip that was left in the automation view (session view in rows and in
grid, arranger view) pre-render that view before AutomationView::opened() checks the clip's type: a synth clip with a
patched parameter selected there (e.g. LPF frequency), made a CV clip in song view, had that parameter looked up in its
CV params: E411 in ModelStackWithThreeMainThings::getPatchedAutoParamFromId() (the fuzzer's seed 101, --mode deep
--7seg, input 751: CLIP in song view). In a release build it reads through a null ParamCollection. A MIDI clip takes
any parameter ID for a CC (MIDIInstrument::getModelStackWithParam()): its pre-render showed CC 24 instead of nothing.

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track):
  (1) PADA: clip view, CLIP (the automation view), pad 8,7 selects LPF frequency (patched), SONG; PADA's pad held +
      MIDI; CLIP: the automation view opens, PADA is a MIDI clip and has no parameter selected
  (2) PADB: the same with LPF frequency selected, SONG, CLIP without a type change: the selection stays
  (3) PADB: SONG, PADB's pad held + CV, CLIP: the automation view opens, PADB is a CV clip, no parameter selected
Throughout: no crash, freeze or hang.
On 62a516c2 with 0001-0009 and 0101: (3) freezes with E411; (1) and (2) hold (opened() resets (1) afterwards).

Usage: automation_type_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
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
    kind_off, id_off, output_off, otype_off, patched, none, midi, cv, lpf = se.gdb_values(emu, [
        "(int)&((Clip*)0)->lastSelectedParamKind", "(int)&((Clip*)0)->lastSelectedParamID",
        "(int)&((Clip*)0)->output", "(int)&((Output*)0)->type", "(int)'deluge::modulation::params::Kind::PATCHED'",
        "(int)'deluge::modulation::params::Kind::NONE'", "(int)OutputType::MIDI_OUT", "(int)OutputType::CV",
        "(int)'deluge::modulation::params::LOCAL_LPF_FREQ'"])  # (gdb finds the namespace's names only quoted)
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

    def change_type(button):
        """In song view: the current clip's pad (y = 6, where Song.enter() put it) held + button."""
        inp.pad(0, 6, 100)
        rig.tm(0.05, "clip pad held")
        press(button, 0.3)
        inp.pad(0, 6, 0)
        rig.tm(0.5, f"clip pad held + {button}")

    res = {}
    try:
        # (1) synth to MIDI
        before = select_lpf(0)
        press("SONG", 1.0)
        change_type("MIDI")
        press("CLIP", 1.5)
        after = state(song.clips()[0])
        res["1"] = dict(before=before, after=after,
                        ok=before["entered"] and before["kind"] == patched and before["param"] == lpf
                        and after["type"] == midi and after["kind"] == none and after["ui"] == "automationView")
        print("1", json.dumps(res["1"]), flush=True)
        press("SONG", 1.0)
        # (2) no type change: the selection stays
        before = select_lpf(1)
        press("SONG", 1.0)
        press("CLIP", 1.5)
        after = state(song.clips()[1])
        res["2"] = dict(before=before, after=after,
                        ok=before["entered"] and before["kind"] == patched and after["kind"] == patched
                        and after["param"] == lpf and after["ui"] == "automationView")
        print("2", json.dumps(res["2"]), flush=True)
        # (3) synth to CV
        press("SONG", 1.0)
        change_type("CV")
        press("CLIP", 1.5)
        after = state(song.clips()[1])
        res["3"] = dict(after=after,
                        ok=after["type"] == cv and after["kind"] == none and after["ui"] == "automationView")
        print("3", json.dumps(res["3"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "automation_type.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2", "3") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
