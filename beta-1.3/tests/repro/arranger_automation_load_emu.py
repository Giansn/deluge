#!/usr/bin/env python3
"""The arranger's automation view ends with the arranger: a song load or a clip view resets it (patch 0111). On the
v1.3 beta (62a516c2) automationView.onArrangerView was set when CLIP opened the arranger's automation view, and only
ArrangerView::opened() and two transitions into a Clip's automation view reset it. A song loaded from the arranger's
automation view starts in song view or a Clip's view and kept the flag. CLIP in a Clip then opened the arranger's
automation view instead of the Clip's, with the arranger's rows (arrangerView.outputsOnScreen) of the old song, whose
Outputs were freed: an audition pad there began auditioning a freed Output (the fuzzer's seed 127, --mode deep --7seg,
input 565: a jump to 0x0c000014 from View::setActiveModControllableTimelineCounter()).

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track):
  (1) SONG (the arranger), CLIP (its automation view), SONG001 loaded: the automation view's arranger flag is off
  (2) PADA entered, CLIP: the automation view opens for PADA (not the arranger's); audition pads 17,0 to 17,7 pressed
      and released there
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0110: (1) the flag stays on (the arranger's rows still the old song's), (2) CLIP
opens the arranger's automation view.

Usage: arranger_automation_load_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot(), Inputs and Song)

se, su = kr.se, kr.su
ROWS = 8  # kDisplayHeight


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
    first_off, next_off, rows_addr, on_arranger = su.gdb_ints(emu, [
        "list Song::Song",  # (a context in which gdb finds the types)
        "print (int)&((Song*)0)->firstOutput", "print (int)&((Output*)0)->next",
        "print (int)&arrangerView.outputsOnScreen", "print (int)&automationView.onArrangerView"])
    song = kr.Song(rig)

    def outputs():
        found, o = [], emu.u32(rig.song() + first_off)
        while o and len(found) < 64:
            found.append(o)
            o = emu.u32(o + next_off)
        return found

    def rows():
        return [emu.u32(rows_addr + 4 * y) for y in range(ROWS)]

    def press(name, seconds=0.5):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)

    res = {}
    try:
        # (1) a song load from the arranger's automation view
        press("SONG", 1.0)
        press("CLIP", 1.0)
        before = dict(ui=rig.ui_name(), flag=emu.u8(on_arranger))
        res["change"] = rig.change_song("SONG001", 1.0)
        mine = outputs()
        stale = [hex(r) for r in rows() if r and r not in mine]
        after = dict(ui=rig.ui_name(), flag=emu.u8(on_arranger), stale_rows=stale)
        res["1"] = dict(before=before, after=after,
                        ok=before == dict(ui="automationView", flag=1) and after["flag"] == 0)
        print("1", json.dumps(res["1"]), flush=True)
        # (2) CLIP in a Clip
        entered = song.enter(inp, 0)
        press("CLIP", 1.0)
        opened = dict(ui=rig.ui_name(), flag=emu.u8(on_arranger), current=hex(song.current()))
        for y in range(ROWS):
            inp.pad(17, y, 100)
            rig.tm(0.1, f"audition pad 17,{y}")
            inp.pad(17, y, 0)
            rig.tm(0.2, f"audition pad 17,{y} released")
        res["2"] = dict(entered=entered, opened=opened, ui_after=rig.ui_name(),
                        ok=entered and opened["ui"] == "automationView" and opened["flag"] == 0)
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "arranger_automation_load.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
