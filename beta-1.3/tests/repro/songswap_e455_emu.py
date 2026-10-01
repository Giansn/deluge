#!/usr/bin/env python3
"""E455 on a song change while playing, when the old song has an arrangement (v1.3 beta, 62a516c2).

LoadSongUI::performLoad() with playback running arms a song swap: AudioEngine::songSwapAboutToHappen() ->
Song::deleteSoundsWhichWontSound(), which deletes (1) every Clip that isn't the active one of its Output and (2) every
Clip whose Output won't sound any more. Since #4903 (9ed7483d) Song::deleteClipObject() freezes with E455 (beta builds:
ALPHA_OR_BETA_VERSION is always 1) when the Clip still has a ClipInstance in the arrangement; the swap path empties the
Outputs' clipInstances only after both loops.

The card: make_sd.py's song (2 synths, the kit, the audio track) as SONGS/DEFAULT.XML (the startup song) with an
arrangement written into the XML, as the firmware saves it (clipInstances="0x" pos, length, clip index per instance):
  - PADA has a second session Clip (not playing): arranger PADA clip 0 at bar 0, the second clip at bar 4 -> case (1)
  - PADB's Clip isn't playing in session: arranger PADB at bar 0 -> case (2) (its Output skips rendering)
and the plain song as SONGS/TARGET.XML. Boot, PLAY (session), 1 s, the song browser to TARGET, the select encoder
pressed (loads while playing, arms, swaps at the launch event), 1 s of the new song.

Usage: songswap_e455_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR [--case both|inactive|silent]
  --case inactive: only PADA's arrangement (the first loop), silent: only PADB's (the second loop), both (default)
Last line: PASS (the swap went through, no freeze) or FAIL (E455 or another problem); exit 0/1."""
import argparse
import json
import os
import re
import sys

RIG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # beta-1.3/tests
sys.path.insert(0, RIG)
import rig13  # noqa: E402  (sets up the paths of mastertune's rig)
import fat32  # noqa: E402
import make_sd  # noqa: E402
import stress_ui_emu as su  # noqa: E402


def instances(*items):
    """clipInstances as Output::writeDataToFile() writes it: per instance pos, length, clip code (8 hex digits each)."""
    return "0x" + "".join(f"{pos:08X}{length:08X}{clip:08X}" for pos, length, clip in items)


def arrangement_xml(xml, case="both"):
    """case: "inactive" (PADA's two clips), "silent" (PADB) or "both"."""
    start = xml.index("\t<sessionClips>\n") + len("\t<sessionClips>\n")
    end = xml.index("\t</sessionClips>\n")
    clips = re.findall(r"\t\t<(?:instrumentClip|audioClip)\n.*?\t\t</(?:instrumentClip|audioClip)>\n",
                       xml[start:end], re.S)
    assert "".join(clips) == xml[start:end] and 'instrumentPresetName="PADA"' in clips[0] \
        and 'instrumentPresetName="PADB"' in clips[1], "unexpected session clips"
    length = int(re.search(r'length="(\d+)"', clips[0]).group(1))
    arrangement = []
    if case in ("inactive", "both"):
        clips.append(clips[0].replace('isPlaying="1"', 'isPlaying="0"', 1))
        arrangement.append(("PADA", [(0, length, 0), (length, length, len(clips) - 1)]))
    if case in ("silent", "both"):
        clips[1] = clips[1].replace('isPlaying="1"', 'isPlaying="0"', 1)
        arrangement.append(("PADB", [(0, length, 1)]))
    xml = xml[:start] + "".join(clips) + xml[end:]
    for name, items in arrangement:
        anchor = f'\t\t<sound\n\t\t\tpresetName="{name}"\n'
        assert xml.count(anchor) == 1, name
        xml = xml.replace(anchor, f'{anchor}\t\t\tclipInstances="{instances(*items)}"\n')
    return xml


def clip_instance_count(rig):
    """ClipInstances over all Outputs of the current song."""
    emu = rig.emu
    if not hasattr(rig, "_ci_offsets"):
        # (gdb finds Song only in the scope of one of its functions, as Rig13 does it)
        rig._ci_offsets = su.gdb_ints(emu, ["list Song::Song", "print (int)&((Song*)0)->firstOutput",
                                            "print (int)&((Output*)0)->next",
                                            "print (int)&((Output*)0)->clipInstances.numElements"])
    first, nxt, num = rig._ci_offsets
    n, out, guard = 0, emu.u32(rig.song() + first), 0
    while out and guard < 64:
        n += emu.u32(out + num)
        out, guard = emu.u32(out + nxt), guard + 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--case", choices=("both", "inactive", "silent"), default="both",
                    help="which Clips have arranger instances: PADA's second (inactive) clip and PADB's (not playing)")
    a = ap.parse_args()
    expected = dict(both=3, inactive=2, silent=1)[a.case]
    os.makedirs(a.out, exist_ok=True)
    files, lengths = make_sd.samples()
    plain = make_sd.song_xml(lengths, 1, 2, False, False, False)
    files["SONGS/DEFAULT.XML"] = arrangement_xml(plain, a.case).encode()
    files["SONGS/TARGET.XML"] = plain.encode()
    image = os.path.join(a.out, "sd.img")
    fat32.build(image, files)
    res = dict(elf=os.path.basename(a.elf))
    rig = None
    try:
        rig = rig13.Rig13(a.elf, a.tools, a.build, image, sd_latency="1000,42.67", oled=True)
        # Rig13 doesn't hook the swap itself (stress_ui_emu.Rig does): the arming and the swap (.constprop clones)
        sym = rig.emu.sym
        rig.about = []
        rig.emu.intercept(sym.find("_ZN11AudioEngine21songSwapAboutToHappenEv"),
                          lambda e: rig.about.append(e.now()) and None)
        rig.emu.intercept(sym.find("_ZN15PlaybackHandler10doSongSwapEb"), lambda e: rig.swaps.append(e.now()) and None)
        rig.emu.intercept(sym.find("_ZN7Session14armForSongSwapEv"), lambda e: rig.arms.append(e.now()) and None)
        rig.emu.uc.ctl_flush_tb()
        rig.load_startup_song()
        res["instances_loaded"] = clip_instance_count(rig)
        rig.play()
        rig.tm(1.0, "playing the arrangement song (session)")
        res["change"] = rig.change_song("TARGET", 1.0)
        res["instances_after"] = clip_instance_count(rig)
    except (su.Stop, SystemExit) as ex:
        res["stopped"] = str(ex)
    if rig:
        res["problems"] = rig.problems
        res["error_popups"] = rig.error_popups()
        res["swaps"] = len(rig.swaps)
        res["swap_armed"] = len(getattr(rig, "about", []))
    json.dump(res, open(os.path.join(a.out, "songswap_e455.json"), "w"), indent=1, default=str)
    problems = res.get("problems") or []
    for p in problems:
        print(f"problem: {p.get('kind')} {p.get('detail')} during {p.get('what')}")
    ch = res.get("change") or {}
    print(f"case {a.case}: instances loaded {res.get('instances_loaded')}, arming {res.get('swap_armed')}, "
          f"swaps {res.get('swaps')}, loaded {ch.get('loaded')!r}, "
          f"ok {ch.get('ok')}, playing {ch.get('playing')}, error popups {res.get('error_popups')}")
    if res.get("instances_loaded") != expected:
        print(f"arrangement not loaded as written (expected {expected} ClipInstances): the test proves nothing")
        print("FAIL")
        return 1
    e455 = any("E455" in str(p.get("detail")) for p in problems)
    ok = not problems and not res.get("stopped") and ch.get("ok") and ch.get("playing") \
        and not res.get("error_popups")
    if e455:
        print("E455: a Clip with arranger instances deleted while arming the song swap")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
