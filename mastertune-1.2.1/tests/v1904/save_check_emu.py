#!/usr/bin/env python3
"""Patch 0123, fix 2 (upstream #4917): the song saved from the emulator (stress_ui_emu.save_song(): SaveSongUI's steps)
while it plays, checked with tools/song_check.py (duplicate attributes), then loaded back (the song browser, as
stress_ui_emu's songchange) and the audio clip's length / section / isPlaying read from memory: they must survive.
The audio clip's section is set to 5 before the save (make_sd's song has 0, the default), so a lost attribute shows.

Usage: save_check_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(TESTS, "stress", "ui"))
sys.path.insert(0, os.path.join(TESTS, "song"))
import make_sd  # noqa: E402
import stress_ui_emu as su  # noqa: E402

import ui_inputs as ui  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    ui.make_card(image, 2, os.path.join(a.out, "card-template.img"))
    rig = ui.boot(a.elf, a.tools, a.build, image)
    emu = rig.emu
    clips_off, type_off, section_off, active_off, length_off, audio_type = su.gdb_ints(emu, [
        "list Song::Song", "print (int)&((Song*)0)->sessionClips", "print (int)&((Clip*)0)->type",
        "print (int)&((Clip*)0)->section", "print (int)&((Clip*)0)->activeIfNoSolo",
        "print (int)&((Clip*)0)->loopLength", "print (int)ClipType::AUDIO"])
    _, _, memory, count, msize, mstart, esize = rig.heap_offsets

    def audio_clips():
        arr = rig.song() + clips_off
        mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
        clips = [emu.u32(mem + ((first + i) % max(size, 1)) * es) for i in range(n)]
        return [c for c in clips if emu.u8(c + type_off) == audio_type]

    def fields(c):
        return dict(length=emu.u32(c + length_off), section=emu.u8(c + section_off),
                    isPlaying=emu.u8(c + active_off))

    res = dict(elf=a.elf)
    clip, = audio_clips()
    emu.uc.mem_write(clip + section_off, bytes([5]))
    res["before_save"] = fields(clip)
    try:
        rig.play()
        rig.tm(1.0, "warm-up")
        xml, info = su.save_song(rig, "SONGS/SAVETEST.XML", "save")
        res["save"] = dict(bytes=info["bytes"], sha256=info["sha256"][:16])
        card = os.path.join(a.out, "card")
        os.makedirs(os.path.join(card, "SONGS"), exist_ok=True)
        for path, data in make_sd.samples()[0].items():  # the samples, for song_check's lookups
            os.makedirs(os.path.dirname(os.path.join(card, path)), exist_ok=True)
            open(os.path.join(card, path), "wb").write(data)
        saved = os.path.join(card, "SONGS", "SAVETEST.XML")
        open(saved, "wb").write(xml)
        tag = re.search(rb"<audioClip\b[^>]*>", xml).group(0).decode()
        names = re.findall(r"\s([A-Za-z_]\w*)=", tag)
        res["audioClip_dupes"] = sorted({n for n in names if names.count(n) > 1})
        checker = os.path.join(TESTS, "..", "tools", "song_check.py")
        chk = subprocess.run([sys.executable, checker, saved, "--card", card, "-v"], capture_output=True, text=True,
                             cwd="/tmp")
        res["song_check"] = dict(exit=chk.returncode, out=chk.stdout.strip().splitlines())
        back = rig.change_song("SAVETEST", 0.5)
        res["load_back"] = dict(ok=back["ok"], loaded=back["loaded"], playing=back["playing"], ui=back["ui"])
        res["after_load"] = [fields(c) for c in audio_clips()]
        res["survived"] = res["after_load"] == [res["before_save"]]
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
    res["problems"] = rig.problems
    res["error_popups"] = rig.error_popups()
    json.dump(res, open(os.path.join(a.out, "save_check.json"), "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))
    ok = res.get("audioClip_dupes") == [] and res.get("survived") is True and not res["problems"]
    print(f"duplicate attributes in <audioClip>: {res.get('audioClip_dupes')}; loaded back with the same values: "
          f"{res.get('survived')}")
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
