#!/usr/bin/env python3
"""A fast turn of the horizontal encoder with its button held zooms one step in the turn's direction, within the
limits (patch 0109). v1.3 hands a fast turn on as several detents at once (encoder_input.cpp), and
TimelineView::horizontalEncoderAction() took only -1 (zoomMagnitude = -offset) for zooming in and checked its limits
only for +-1: a fast turn to the right zoomed out instead, and a fast turn either way zoomed out past the limit, until
xZoom (uint32_t, doubled each time) overflowed and was 0.

One boot of the real firmware in the emulator (OLED), make_card's song (synths PADA and PADB, the kit, the audio
track), PADA entered (its clip view), X_ENC held while scrollX turns:
  (1) +2 at once (to the right: zoom in): the zoom gets finer (xZoom[NAVIGATION_CLIP] smaller)
  (2) -3 at once, 30 times (to the left: zoom out): the zoom never gets coarser than kMaxZoom
  (3) +3 at once, 30 times: the zoom never gets finer than 1 (0 means it overflowed)
Throughout: no crash, freeze or hang.

Usage: fast_zoom_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
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
MAX_ZOOM = 1610612736 // 16  # kMaxZoom (definitions_cxx.hpp)


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
    zoom_off, = su.gdb_ints(emu, ["list Song::Song", "print (int)&((Song*)0)->xZoom"])
    song = kr.Song(rig)

    def zoom():  # xZoom[NAVIGATION_CLIP]
        return struct.unpack("<I", emu.uc.mem_read(rig.song() + zoom_off, 4))[0]

    def turns(n, times):
        seen = []
        inp.button("X_ENC", True)
        rig.tm(0.05, "X_ENC held")
        for _ in range(times):
            inp.turn("scrollX", n)
            rig.tm(0.3, f"X_ENC held + scrollX {n:+d}")
            seen.append(zoom())
        inp.button("X_ENC", False)
        rig.tm(0.3, "X_ENC released")
        return seen

    res = {}
    try:
        entered = song.enter(inp, 0)
        ui = rig.ui_name()
        before = zoom()
        after = turns(2, 1)[0]
        res["1"] = dict(entered=entered, ui=ui, zoom=(before, after), ok=entered and after < before)
        print("1", json.dumps(res["1"]), flush=True)
        seen = turns(-3, 30)
        res["2"] = dict(zooms=seen, ok=max(seen) <= MAX_ZOOM and min(seen) > 0)
        print("2", json.dumps(res["2"]), flush=True)
        seen = turns(3, 30)
        res["3"] = dict(zooms=seen, ok=min(seen) > 0)
        print("3", json.dumps(res["3"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "fast_zoom.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2", "3") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
