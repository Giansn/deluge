#!/usr/bin/env python3
"""Preset navigation when every preset is in the song already changes nothing (patch 0117). On the v1.3 beta (62a516c2)
LoadInstrumentPresetUI::doPresetNavigation() looks for a preset not in the song when the whole instrument is to be
replaced (Availability::INSTRUMENT_UNUSED: arranger view, song view, a Clip with an arranger instance). After going
round the list twice without one (its guard against an endless loop) it returned one in the song anyway. The arranger's
Song::navigateThroughPresetsForInstrument() then gave it to Song::replaceInstrument(), which froze with i009 (already in
the song); a Clip with an arranger instance went to it without its instances, and the check after the change froze with
E058 (the fuzzer's seed 226, --mode deep, stop run 9, input 783, from the keyboard screen).

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track; the card has five synth presets):
  (1) song view: new clips in empty rows until no unused preset is left (Error 16), then every clip stopped (its
      status pad), so that the browser's list keeps the song's synths, all in use
  (2) arranger view: a synth row's audition pad held, the select encoder turned: no freeze, the row keeps its synth
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0116: (2) freezes with i009.

Usage: presets_all_used_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
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
    rig.ui_names[emu.sym["arrangerView"]] = "arrangerView"
    on_screen, otype_off, name_off, synth, active_off = su.gdb_ints(emu, [
        "list Song::Song", "print (int)&arrangerView.outputsOnScreen", "print (int)&((Output*)0)->type",
        "print (int)&((Output*)0)->name", "print (int)OutputType::SYNTH", "print (int)&((Clip*)0)->activeIfNoSolo"])
    song = kr.Song(rig)

    def press(name, seconds=0.6):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)

    def name_of(output):
        p = emu.u32(output + name_off)
        return bytes(emu.uc.mem_read(p, 24)).split(b"\0")[0].decode("latin1") if p else ""

    res = {}
    try:
        # (1) new synth clips until no unused preset is left
        created, error16 = 0, False
        for _ in range(8):
            row = len(song.clips())
            for _ in range(8):
                if 0 <= row - song.scroll() <= 7:
                    break
                inp.turn("scrollY", 1)
                rig.tm(0.1, "scrollY")
            before = len(rig.popups)
            inp.pad(0, row - song.scroll(), 100)
            rig.tm(0.1, "empty row pad")
            inp.pad(0, row - song.scroll(), 0)
            rig.tm(1.0, "new clip")
            if rig.ui_name() != "sessionView":
                press("SONG", 1.0)
            if any(p[3] == "Error 16" for p in rig.popups[before:]):
                error16 = True
                break
            created += len(song.clips()) > row
        # Every clip stopped (its status pad): the browser then keeps the song's synths in its list, all in use
        for i in range(len(song.clips())):
            for _ in range(8):
                if 0 <= i - song.scroll() <= 7:
                    break
                inp.turn("scrollY", 1 if i - song.scroll() > 7 else -1)
                rig.tm(0.1, "scrollY")
            if emu.u8(song.clips()[i] + active_off):
                inp.pad(16, i - song.scroll(), 100)
                rig.tm(0.05, "status pad")
                inp.pad(16, i - song.scroll(), 0)
                rig.tm(0.3, "clip stopped")
        active = sum(emu.u8(c + active_off) for c in song.clips())
        res["1"] = dict(created=created, clips=len(song.clips()), error16=error16, active=active,
                        ok=error16 and active == 0)
        print("1", json.dumps(res["1"]), flush=True)
        # (2) arranger view: a synth row's audition pad held, select turned
        press("SONG", 1.5)
        rows = [emu.u32(on_screen + 4 * y) for y in range(8)]
        y = next(y for y, o in enumerate(rows) if o and emu.u8(o + otype_off) == synth)
        before = dict(output=hex(rows[y]), name=name_of(rows[y]))
        inp.pad(17, y, 100)
        rig.tm(0.2, "audition pad held")
        inp.turn("select", 1)
        rig.tm(1.0, "select +1")
        inp.pad(17, y, 0)
        rig.tm(0.5, "audition pad released")
        after_output = emu.u32(on_screen + 4 * y)
        after = dict(output=hex(after_output), name=name_of(after_output))
        res["2"] = dict(ui=rig.ui_name(), row=y, before=before, after=after,
                        ok=rig.ui_name() == "arrangerView" and after == before)
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "presets_all_used.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
