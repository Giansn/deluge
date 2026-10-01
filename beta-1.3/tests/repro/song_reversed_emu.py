#!/usr/bin/env python3
"""A new Song never plays "reversed" (patch 0105). PR #4445 moved currentlyPlayingReversed and sequenceDirectionMode
from Clip to TimelineCounter, so that recording song automation reads the Song's own flags instead of casting the
Song to a Clip. But nothing initializes them for the Song (Clip sets its own): a Song made in reused memory took
whatever was there. In the fuzzer's seed 108 (--mode deep, input 488, after song changes) the Song's flag held 0xED,
so recording song automation from performance view took the "playing reversed" branch of
AutoParam::homogenizeRegion() and froze with E445. That branch also needs the Song's cut point, INT32_MAX.

One boot of the real firmware in the emulator (OLED), make_card's card. At the entry of Song's constructor, the test
writes 0xED into the two fields: the memory a Song is made in after song changes, as in seed 108.
  (1) SONG001 loaded (a new Song): its currentlyPlayingReversed is false and its sequenceDirectionMode FORWARD
  (2) arranger view, RECORD + PLAY, KEYBOARD (performance view), pad 10,3 (its FX value, recorded as song
      automation, as seed 108 did): no freeze
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0104: (1) keeps 0xED, and (2) freezes with E445.

Usage: song_reversed_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot() and Inputs)
from unicorn.arm_const import UC_ARM_REG_R0  # noqa: E402

se, su = kr.se, kr.su
DIRTY = 0xED


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
    emu, sym = rig.emu, rig.emu.sym
    rev_off, dir_off, forward = su.gdb_ints(emu, [  # (gdb finds Song only in the context of its constructor)
        "list Song::Song", "print (int)&((Song*)0)->currentlyPlayingReversed",
        "print (int)&((Song*)0)->sequenceDirectionMode", "print (int)SequenceDirection::FORWARD"])
    made = []

    def dirty(e):
        this = e.uc.reg_read(UC_ARM_REG_R0)  # the Song's memory, before its constructor runs
        e.uc.mem_write(this + rev_off, bytes([DIRTY]))
        e.uc.mem_write(this + dir_off, bytes([DIRTY]))
        made.append(this)
    for ad in {v[0] for k, v in sym.by_name.items() if k.startswith(("_ZN4SongC1Ev", "_ZN4SongC2Ev")) and v[1]}:
        emu.intercept(ad, dirty)
    emu.uc.ctl_flush_tb()

    def press(name, seconds=0.5):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)

    res = {}
    try:
        res["change"] = rig.change_song("SONG001", 1.0)
        song = rig.song()
        rev, direction = emu.u8(song + rev_off), emu.u32(song + dir_off)
        res["1"] = dict(songs_made=[hex(s) for s in made], current=hex(song), reversed=rev, direction=direction,
                        ok=song in made and rev == 0 and direction == forward)
        print("1", json.dumps(res["1"]), flush=True)
        press("SONG", 1.0)  # the arranger: song automation plays (and records) with the arrangement
        arranger = rig.ui_name()
        press("RECORD")
        press("PLAY", 1.0)
        press("KEYBOARD", 1.0)
        perf = rig.ui_name()
        inp.pad(10, 3, 100)  # a performance pad: its FX value, recorded as song automation (seed 108's input 488)
        rig.tm(0.5, "performance pad held")
        inp.pad(10, 3, 0)
        rig.tm(0.5, "performance pad released")
        press("PLAY", 0.5)
        res["2"] = dict(arranger=arranger, ui=perf, ok=arranger == "arrangerView" and perf == "performanceView")
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "song_reversed.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
