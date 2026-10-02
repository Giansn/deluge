#!/usr/bin/env python3
"""A fast turn of the select encoder on a held clip instance in arranger view, on the v1.3 beta (62a516c2): a crash.
ArrangerView::selectEncoderAction() with a clip instance held picks the next session Clip of that Output through
Song::getNextSessionClipWithOutput(offset, ...). That steps the index by offset and stops only at exactly -1 or
exactly the number of session Clips. Since the encoder overhaul (#4529) offset is the whole turn (+-2 or more when
turned fast), so a step of 2 jumps past either end, reads Clip pointers beyond the array until some garbage happens
to match, and the arranger puts that "Clip" into the clip instance: the fuzzer's seed 13001 (--mode all, input 1125,
on the second delivered build) crashed in View::setActiveModControllableTimelineCounter() with a jump to
0x0c0000fc, after wild reads in selectEncoderAction(). The traced value: a Clip pointer in neither sessionClips nor
arrangementOnlyClips.

One boot of the real firmware in the emulator (OLED), the song with an arrangement of 4 bars (every Output one clip
instance of its session Clip, as export_repeat_emu.py writes it): SONG (arranger view), then the clip instance of the
first session Clip's Output held (pad 0 of its row) while the select encoder turns -2, then +2, then -2. Checked:
no crash, freeze or hang, no access outside the mapped memory, and the clip instance's Clip afterwards is none (a
white instance) or one of the song's Clips.

Usage: arranger_clip_select_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_repeat_emu as er  # noqa: E402  (with_arrangement(); puts the rig on the path)
import fat32  # noqa: E402
import fuzz_ui  # noqa: E402
import make_sd  # noqa: E402
import stress_ui_emu as su  # noqa: E402

LENGTH = 4 * 384


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
    files, lengths = make_sd.samples()
    xml, _ = er.with_arrangement(make_sd.song_xml(lengths, 1, 1, False, False, False), LENGTH)
    files["SONGS/DEFAULT.XML"] = xml.encode()
    fat32.build(image, files)

    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    emu = rig.emu
    inp = fuzz_ui.Inputs(rig, "v13")
    sym = emu.sym
    rig.ui_names[sym["arrangerView"]] = "arrangerView"
    ons, cis_off, sess_off, arr_off, ci_clip, clip_output = su.gdb_ints(emu, [
        "list Song::Song", "print (int)&((ArrangerView*)0)->outputsOnScreen", "print (int)&((Output*)0)->clipInstances",
        "print (int)&((Song*)0)->sessionClips", "print (int)&((Song*)0)->arrangementOnlyClips",
        "print (int)&((ClipInstance*)0)->clip", "print (int)&((Clip*)0)->output"])
    av = sym["arrangerView"]
    _, _, memory, count, msize, mstart, esize = rig.heap_offsets

    def elements(addr):
        mem, n, size, first, es = (emu.u32(addr + o) for o in (memory, count, msize, mstart, esize))
        return [mem + ((first + i) % max(size, 1)) * es for i in range(n)]

    def clips(off):
        return [emu.u32(e) for e in elements(rig.song() + off)]
    res = {}
    try:
        inp.button("SONG", True)
        rig.tm(0.05, "SONG on")
        inp.button("SONG", False)
        rig.tm(0.5, "arranger view")
        res["view"] = rig.ui_name()
        first_clip = clips(sess_off)[0]
        output = emu.u32(first_clip + clip_output)
        rows = [emu.u32(av + ons + 4 * y) for y in range(8)]
        y = rows.index(output)
        res["row"] = y
        instance = elements(output + cis_off)[0]
        inp.pad(0, y, 100)
        rig.tm(0.05, f"clip instance (pad 0,{y}) held")
        for turn in (-2, 2, -2):
            inp.turn("select", turn)
            rig.tm(0.3, f"select {turn:+d} with the clip instance held")
        inp.pad(0, y, 0)
        rig.tm(0.5, "released")
        clip = emu.u32(instance + ci_clip)
        live = set(clips(sess_off)) | set(clips(arr_off))
        res["clip_after"] = hex(clip)
        res["clip_ok"] = clip == 0 or clip in live
        res["ui_after"] = rig.ui_name()
    except (su.Stop, ValueError) as ex:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}" if p else str(ex)
    res["invalid"] = [(hex(k[0]), k[1], k[2]) for k in rig.invalid]
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    ok = (res.get("view") == "arrangerView" and res.get("clip_ok") and not res["invalid"] and not res["problems"])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
