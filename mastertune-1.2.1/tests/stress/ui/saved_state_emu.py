#!/usr/bin/env python3
"""What a build keeps when it saves (mastertune v18.3), on the real firmware in the emulator (stress_ui_emu.py's rig):

1. unknown: a card whose CommunityFeatures.XML has two settings the build doesn't know (futureSettingA 7,
   futureSettingB 3, as a newer firmware leaves them), the build booted on it, Settings opened and left with nothing
   changed (SoundEditor::exitCompletely() saves the file), the file read back: both must be there under their own
   names and values. v18.2 and older wrote them back under whatever was in the freed memory their name pointed into
   ("SONGS"): RuntimeFeatureSettings kept a view of the String each name was read into.
2. syncLevel: a song played, saved, loaded back and saved again (stress_ui_emu.py's save scenario, one save): the
   reverb sidechain's syncLevel must be the same in both files. v18.2 and older wrote it as the internal value and
   read it as a file value, one step lower at every save and load. Other differences of the round trip are printed,
   not checked (roomSize moves by float rounding, as in 1.2.1).

Usage: BLOCKCOUNT_DIR=... saved_state_emu.py <deluge.elf> <out dir> [--tools PREFIX]
Exit status 0 when both hold.
"""
import argparse
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stress_ui_emu as su  # noqa: E402

UNKNOWN = {"futureSettingA": 7, "futureSettingB": 3}


def check_unknown(elf, tools, build, out):
    sd = os.path.join(out, "unknown.img")
    files, lengths = su.make_sd.samples()
    files["SONGS/DEFAULT.XML"] = su.make_sd.song_xml(lengths, 1, 2).encode()
    settings = "".join(f'\t<setting name="{k}" value="{v}"></setting>\n' for k, v in UNKNOWN.items())
    files["CommunityFeatures.XML"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<runtimeFeatureSettings\n\tfirmwareVersion="c1.3.0"\n'
        '\tearliestCompatibleFirmware="4.1.3">\n' + settings + '</runtimeFeatureSettings>\n').encode()
    su.fat32.build(sd, files)
    try:
        rig = su.Rig(elf, tools, build, sd, "instant", "unknown")
        su.set_menus(rig, [], 0)
        text, values = su.community_file(sd)
        kept = {k: (int(values[k]) if values.get(k) is not None else None) for k in UNKNOWN}
        ok = kept == UNKNOWN and not rig.problems and not rig.invalid
        names = sorted(set(re.findall(r'name="([^"]*)"', text or "")))
        print(f"unknown: {'ok  ' if ok else 'FAIL'} kept by name {kept} (want {UNKNOWN}); the file's names: {names}; "
              f"problems {len(rig.problems)}, invalid accesses {len(rig.invalid)}")
        return ok
    finally:
        os.remove(sd)


def check_sync_level(elf, tools, build, out):
    image = os.path.join(out, "save.img")
    su.build_songchange_card(image, 5, "DEFAULT", 1)
    try:
        rig = su.Rig(elf, tools, build, image, "1000,42.67")
        res = {}
        su.run_save(types.SimpleNamespace(saves=1), rig, res, out)
        diff = res["round_trip"]["diff"]
        sync = [d for d in diff if d[:1] in "+-" and d[:3] not in ("+++", "---") and "syncLevel" in d]
        others = [d for d in diff if d[:1] in "+-" and d[:3] not in ("+++", "---") and "syncLevel" not in d]
        ok = not sync and res["round_trip"]["load"]["ok"] and not rig.problems
        print(f"syncLevel: {'ok  ' if ok else 'FAIL'} saved, loaded back and saved again: "
              + ("the same syncLevel everywhere" if not sync else "differs: " + " | ".join(s.strip() for s in sync))
              + f"; other lines that differ (not checked): {len(others)}"
              + (" (" + " | ".join(o.strip() for o in others[:4]) + ")" if others else ""))
        return ok
    finally:
        os.remove(image)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("out")
    ap.add_argument("--tools")
    a = ap.parse_args()
    elf = os.path.abspath(a.elf)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    tools = a.tools or os.path.join(os.path.dirname(elf),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    build = os.environ.get("BLOCKCOUNT_DIR", HERE)
    ok = check_unknown(elf, tools, build, out)
    ok = check_sync_level(elf, tools, build, out) and ok
    print("saved state:", "all ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
