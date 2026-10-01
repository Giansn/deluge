#!/usr/bin/env python3
"""Upstream #4825 (regression from #4656): SAVE proposes the name of a song that already exists, and confirming it
overwrites that song. Browser::fileItems is a window of at most FILE_ITEMS_MAX_NUM_ELEMENTS (20) entries around the
song being saved, and the default-name derivation (nextDefaultName() via BrowserFileListView::contains()) tested its
candidates only against that window, so a family member outside it read as free.

The card: SONGS/DEFAULT.XML (the startup template), "TEST 1" .. "TEST 25" (the reporter's numbering style), and
SONG010 with its letter variations SONG010A .. SONG010P (the letter path: 7SEG slot names). 43 songs, more than 20.
Two cases, each as the user does it: the song browser opened on the song, the select encoder pressed (it loads),
SAVE pressed and released (SaveSongUI opens with its proposed name), then the select encoder pressed to save; if the
"overwrite?" context menu comes up the user confirms it as well (they believed the name was new).
  numeric: load TEST 11, SAVE   -> the proposal must not exist on the card (the bug: "TEST 2"; fixed: "TEST 26")
  letters: load SONG010, SAVE   -> the same (the bug: a letter past the window, e.g. "SONG010K"; fixed: "SONG010Q")
After each save every original song must be byte-identical, and the proposed name must be on the card.

Usage: save_name_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Prints PASS (bug absent) or FAIL (bug present) as its last line, exit 0/1."""
import argparse
import json
import os
import sys

BETA_TESTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, BETA_TESTS)
import fuzz_ui  # noqa: E402  (puts mastertune's tests/song and tests/stress/ui on the path)
import rig13  # noqa: E402

import fat32  # noqa: E402
import make_sd  # noqa: E402
import stress_ui_emu as su  # noqa: E402

NUMERIC = [f"TEST {i}" for i in range(1, 26)]
LETTERS = ["SONG010"] + [f"SONG010{chr(c)}" for c in range(ord("A"), ord("P") + 1)]
CASES = [("numeric", "TEST 11"), ("letters", "SONG010")]
SELECT_ENC = fuzz_ui.B["SELECT_ENC"]


def build_card(image):
    files, lengths = make_sd.samples()
    xml = make_sd.song_xml(lengths, 1, 2, False, False, False).encode()
    songs = {"SONGS/DEFAULT.XML": xml}
    for name in NUMERIC + LETTERS:
        songs[f"SONGS/{name}.XML"] = xml
    files.update(songs)
    fat32.build(image, files)
    return songs


def card_songs(image):
    """The names in SONGS/ as the firmware sees them: the long name, else the 8.3 one ("SONG010HXML" -> "SONG010H.XML")."""
    return {(lfn or f"{name11[:8].rstrip()}.{name11[8:].rstrip()}").upper()
            for name11, lfn in fat32.list_dir(image, "SONGS")}


def run_case(rig, inp, image, originals, label, name):
    r = dict(case=label, song=name)
    rig.open_browser(start=name)
    r["browser_on"] = rig.browser_name()
    rig.button(SELECT_ENC, True, limit_s=40)
    rig.button(SELECT_ENC, False)
    rig.tm(0.5, "after the load")
    r["loaded"] = rig.song_name()
    r["ui_after_load"] = rig.ui_name()
    if r["loaded"].upper() != name.upper():
        r["error"] = "song not loaded"
        return r
    inp.button("SAVE", True)
    inp.button("SAVE", False)
    rig.tm(0.3, "save UI open")
    r["ui_save"] = rig.ui_name()
    proposed = rig.browser_name()
    r["proposed"] = proposed
    r["proposed_exists"] = f"{proposed}.XML".upper() in card_songs(image)
    # The user accepts the proposed name (select encoder), and the overwrite question too if it comes
    rig.button(SELECT_ENC, True, limit_s=40)
    rig.button(SELECT_ENC, False)
    rig.tm(0.3, "after the save press")
    r["overwrite_asked"] = rig.ui_name() == "overwriteFile"
    if r["overwrite_asked"]:
        rig.button(SELECT_ENC, True, limit_s=40)
        rig.button(SELECT_ENC, False)
    rig.tm(1.0, "after the save")
    r["ui_after_save"] = rig.ui_name()
    r["overwritten"] = sorted(path for path, data in originals.items() if fat32.read_file(image, path) != data)
    try:
        r["saved_bytes"] = len(fat32.read_file(image, f"SONGS/{proposed}.XML"))
    except FileNotFoundError:
        r["saved_bytes"] = None
    r["ok"] = (not r["proposed_exists"] and not r["overwrite_asked"] and not r["overwritten"]
               and bool(r["saved_bytes"]))
    return r


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
    originals = build_card(image)
    res = dict(elf=a.elf, cases=[])
    rig = None
    try:
        rig = rig13.Rig13(a.elf, a.tools, a.build, image, oled=True)
        for n, sym in (("overwriteFile", "_ZN6deluge3gui12context_menu13overwriteFileE"),):
            if sym in rig.emu.sym.by_name:
                rig.ui_names[rig.emu.sym[sym]] = n
        rig.load_startup_song()
        inp = fuzz_ui.Inputs(rig, "v13")
        for label, name in CASES:
            res["cases"].append(run_case(rig, inp, image, originals, label, name))
            # A later case compares against what is on the card now, so an earlier case's damage counts once
            originals = {p: fat32.read_file(image, p) for p in originals}
    except (su.Stop, SystemExit) as ex:
        res["stopped"] = str(ex)
    if rig:
        res["problems"] = rig.problems
        res["error_popups"] = rig.error_popups()
    json.dump(res, open(os.path.join(a.out, "save_name.json"), "w"), indent=1, default=str)
    for c in res["cases"]:
        print(f"{c['case']}: loaded {c.get('loaded')!r}, SAVE proposed {c.get('proposed')!r} "
              f"(already on the card: {c.get('proposed_exists')}), overwrite asked: {c.get('overwrite_asked')}, "
              f"songs overwritten: {c.get('overwritten')}, saved: {c.get('saved_bytes')} bytes"
              + (f", error: {c['error']}" if c.get("error") else ""))
    if res.get("stopped") or res.get("problems"):
        print(f"stopped: {res.get('stopped')}; problems: {res.get('problems')}")
    ok = (len(res["cases"]) == len(CASES) and all(c.get("ok") for c in res["cases"]) and not res.get("problems")
          and not res.get("stopped"))
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
