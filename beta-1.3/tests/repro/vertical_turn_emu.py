#!/usr/bin/env python3
"""A fast turn of the vertical encoder in song and arranger view moves one square per detent, within the limits (patch
0107). v1.3 hands a fast turn on as several detents at once (encoder_input.cpp), and SessionView's and ArrangerView's
verticalEncoderAction() passed the whole turn to verticalScrollOneSquare(), which checks its limits for one square:
  - song view: a Clip held and the encoder turned up fast swapped the Clip with an index outside sessionClips
    (direction == 1 is false for +3, so the down check ran), leaving a stray pointer in the list of Clips
  - arranger view: the scroll went past its limits (+30 from -7 gave 23, the limit is the number of Outputs - 1); an
    audition pad on an empty row then put its new Output outside outputsOnScreen[]'s 8 rows, and later ended
    auditioning on whatever was there (a garbage Output*: the crash seen in a replay of the fuzzer's seed 109)

One boot of the real firmware in the emulator (OLED), make_card's song (PADA, PADB, the kit, the audio track):
  (1) song view: the last Clip's pad held, the vertical encoder turned +3 at once: the song's Clips are the same 4,
      the scroll within its limits
  (2) arranger view: the vertical encoder turned +30 at once: the scroll within its limits; audition pad 17,0 held
      and let go: the audition pad code writes nothing outside outputsOnScreen[]
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0106: (1) a null pointer among the Clips, (2) scroll 23 and a write outside.

Usage: vertical_turn_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot(), Inputs and Song)
from unicorn import UC_HOOK_MEM_WRITE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_PC  # noqa: E402

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
    arr_scroll_off, rows_addr, pressed_addr = su.gdb_ints(emu, [
        "list Song::Song", "print (int)&((Song*)0)->arrangementYScroll", "print (int)&arrangerView.outputsOnScreen",
        "print (int)&arrangerView.yPressedEffective"])  # (yPressedEffective and yPressedActual follow the rows)
    outside = []

    def on_write(uc, access, address, size, value, user):
        name = emu.sym.name_at(uc.reg_read(UC_ARM_REG_PC))
        if "uditionPadAction" in name and not pressed_addr <= address < pressed_addr + 4:
            outside.append(((address - rows_addr) // 4, name.split("(")[0]))
    for lo, hi in ((rows_addr - 64 * 4, rows_addr), (rows_addr + ROWS * 4, rows_addr + (ROWS + 64) * 4)):
        emu.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=lo, end=hi - 1)
    emu.uc.ctl_flush_tb()
    song = kr.Song(rig)

    def arranger_scroll():
        return struct.unpack("<i", emu.uc.mem_read(rig.song() + arr_scroll_off, 4))[0]

    def press(name, seconds=0.8):
        inp.button(name, True)
        inp.button(name, False)
        rig.tm(seconds, name)
        return rig.ui_name()

    res = {}
    try:
        clips = song.clips()
        last = len(clips) - 1
        for _ in range(16):  # the last Clip onto y = 6, as Song.enter() does, without entering it
            if last - song.scroll() == 6:
                break
            inp.turn("scrollY", 1 if last - song.scroll() > 6 else -1)
            rig.tm(0.05, "scrollY")
        inp.pad(0, 6, 100)
        rig.tm(0.1, "last Clip's pad held")
        inp.turn("scrollY", 3)
        rig.tm(0.3, "turned +3 at once")
        inp.pad(0, 6, 0)
        rig.tm(0.5, "pad released")
        after = song.clips()
        scroll = song.scroll()
        res["1"] = dict(before=[hex(c) for c in clips], after=[hex(c) for c in after], scroll=scroll,
                        ok=sorted(after) == sorted(clips) and 1 - ROWS <= scroll <= len(clips) - 1)
        print("1", json.dumps(res["1"]), flush=True)
        arranger = press("SONG", 1.0)
        inp.turn("scrollY", 30)  # (the arranger opens at the bottom limit, -7: up has room to overshoot)
        rig.tm(0.5, "turned +30 at once")
        scroll = arranger_scroll()
        inp.pad(17, 0, 100)
        rig.tm(0.3, "audition pad held")
        inp.pad(17, 0, 0)
        rig.tm(0.5, "audition pad released")
        res["2"] = dict(ui=arranger, scroll=scroll, writes_outside=outside[:8],
                        ok=arranger == "arrangerView" and 1 - ROWS <= scroll <= len(clips) - 1 and not outside)
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "vertical_turn.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
