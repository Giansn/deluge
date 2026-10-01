#!/usr/bin/env python3
"""The text cursor of a browser or save screen and a fast turn of the horizontal encoder, on the v1.3 beta (62a516c2):
S004. Since the encoder overhaul (#4529), horizontalEncoderAction() gets the whole turn accumulated since the last
read (+2, +3 when turned fast), not +-1. QwertyUI::horizontalEncoderAction() only checks for the cursor already at the
end (or at 0) and then adds the offset: one character before the end, +2 puts it past the end, and the next character
typed freezes in String::concatenateAtPos() with S004 (a release build writes past the string). The fuzzer found it
(seed 71, --mode deep, input 717: "turn scrollX +2" in the song browser, a few inputs later a letter pad).

One boot of the real firmware in the emulator (OLED): LOAD (the song browser, the current song's name as the text),
the horizontal encoder -1 (the cursor one before the end), then +2 in one turn, then -3 from the start of the text,
each time the cursor checked to lie within the text; then a letter typed (pad 4,4). Must not freeze or crash.

Usage: qwerty_cursor_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot and Inputs; puts the rig on the path)
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
    pos_at = emu.sym["_ZN8QwertyUI18enteredTextEditPosE"]
    text_at = emu.sym["_ZN8QwertyUI11enteredTextE"]

    def cursor():
        pos = struct.unpack("<h", bytes(emu.uc.mem_read(pos_at, 2)))[0]
        mem = emu.u32(text_at)  # String::stringMemory
        text = bytes(emu.uc.mem_read(mem, 64)).split(b"\0")[0].decode("latin-1") if mem else ""
        return pos, text
    res = {}
    try:
        inp.button("LOAD", True)
        rig.tm(0.05, "LOAD held")
        inp.button("LOAD", False)
        rig.tm(1.0, "song browser opens")
        rig.wait_mode_none()
        res["ui"] = rig.ui_name()
        res["opened"] = cursor()
        steps = []
        for n in (-1, 2, -40, -3):
            inp.turn("scrollX", n)
            rig.tm(0.3, f"scrollX {n:+d}")
            pos, text = cursor()
            steps.append((n, pos, len(text)))
        res["cursor_after_turns"] = steps
        inp.pad(4, 4, 100)
        rig.tm(0.1, "letter pad")
        inp.pad(4, 4, 0)
        rig.tm(0.5, "letter typed")
        res["typed"] = cursor()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    inside = all(0 <= pos <= length for _, pos, length in res.get("cursor_after_turns", [(0, -1, 0)]))
    ok = res.get("ui") == "loadSongUI" and inside and "typed" in res and not res["problems"]
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
