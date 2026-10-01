#!/usr/bin/env python3
"""#4917 on the v1.3 beta (62a516c2): a song with an audio clip and a named CV instrument, saved by the firmware's own
save path (openUI(&saveSongUI), the name typed, SaveSongUI::performSave(true): createXMLFile(), Song::writeToFile(),
closeFileAfterWriting()) while it plays, then loaded back with the song browser (LoadSongUI, the select encoder pressed:
loads while playing, arms, swaps).

Two defects in the beta's save:
  1. AudioClip::writeDataToFile() calls Clip::writeDataToFile() twice: colourOffset, isArmedForRecording, isPlaying,
     isSoloing, length and section twice in every <audioClip>.
  2. Instrument::writeDataToFile() writes a CV instrument's channel only when it has no name: a named CV instrument on
     channel 1 is saved without it, loads on channel 0, and its clip (cvChannel="1") finds no instrument there:
     InstrumentClip::claimOutput() returns FILE_CORRUPTED and the song doesn't load.

The card: make_sd.py's song (2 synths, the kit, the audio track LOOP) with a CV instrument named CVBASS on channel 1 and
a CV clip of two notes, as SONGS/DEFAULT.XML (the startup song). Before the save the audio clip's section is set to 5
(the card has 0, the default), so a value lost on the way shows.

Checks: no duplicate attribute in <audioClip>; the saved <cvChannel> has channel="1"; the song loads back (the swap went
through, its name SAVETEST) with the audio clip's length / section / isPlaying and the CV instrument's name and channel
as before the save; no crash, freeze, hang or error popup.

Usage: save_4917_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS (both defects absent) or FAIL; exit 0/1."""
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
import song_emu as se  # noqa: E402
import stress_ui_emu as su  # noqa: E402

CV_NAME, CV_CHANNEL = "CVBASS", 1
SAVE_NAME = "SAVETEST"


def song_with_cv(xml):
    """make_sd's song with a named CV instrument (as the firmware writes one: Instrument::writeDataToFile(), then
    CVInstrument's cv2Source) and a playing CV clip of two notes on it."""
    instrument = (f'\t\t<cvChannel\n\t\t\tchannel="{CV_CHANNEL}"\n\t\t\tpresetName="{CV_NAME}"\n'
                  '\t\t\tdefaultVelocity="64"\n\t\t\tcv2Source="0" />\n')
    end = "\t</instruments>\n"
    assert xml.count(end) == 1
    xml = xml.replace(end, instrument + end)
    arp = re.search(r"\t\t\t<arpeggiator\n.*?/>\n", xml, re.S).group(0)
    clip = ('\t\t<instrumentClip\n\t\t\tinKeyMode="0"\n\t\t\tyScroll="40"\n'
            f'\t\t\tcvChannel="{CV_CHANNEL}"\n\t\t\tisPlaying="1"\n\t\t\tisSoloing="0"\n\t\t\tisArmedForRecording="0"\n'
            '\t\t\tlength="1536"\n\t\t\tcolourOffset="0"\n\t\t\tsection="0">\n' + arp +
            '\t\t\t<noteRows>\n'
            '\t\t\t\t<noteRow y="48" noteData="0x00000000000001805A14" />\n'
            '\t\t\t\t<noteRow y="55" noteData="0x00000300000001805A14" />\n'
            '\t\t\t</noteRows>\n\t\t</instrumentClip>\n')
    at = xml.index("\t\t<audioClip\n")
    return xml[:at] + clip + xml[at:]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    files, lengths = make_sd.samples()
    xml0 = song_with_cv(make_sd.song_xml(lengths, 1, 2, False, False, False))
    files["SONGS/DEFAULT.XML"] = xml0.encode()
    image = os.path.join(a.out, "sd.img")
    fat32.build(image, files)
    res = dict(elf=os.path.basename(a.elf))
    rig = None
    try:
        rig = rig13.Rig13(a.elf, a.tools, a.build, image, sd_latency="1000,42.67", oled=True)
        emu = rig.emu
        sym = emu.sym
        # Rig13 doesn't hook the song swap itself (stress_ui_emu.Rig does): change_song() needs them
        emu.intercept(sym.find("_ZN15PlaybackHandler10doSongSwapEb"), lambda e: rig.swaps.append(e.now()) and None)
        emu.intercept(sym.find("_ZN7Session14armForSongSwapEv"), lambda e: rig.arms.append(e.now()) and None)
        emu.uc.ctl_flush_tb()
        rig.load_startup_song()

        (clips_off, first_out, next_off, otype_off, oname_off, chan_off, type_off, section_off, active_off,
         length_off, audio_type, cv_type) = su.gdb_ints(emu, [
            "list Song::Song", "print (int)&((Song*)0)->sessionClips", "print (int)&((Song*)0)->firstOutput",
            "print (int)&((Output*)0)->next", "print (int)&((Output*)0)->type", "print (int)&((Output*)0)->name",
            "print (int)&((NonAudioInstrument*)0)->channel", "print (int)&((Clip*)0)->type",
            "print (int)&((Clip*)0)->section", "print (int)&((Clip*)0)->activeIfNoSolo",
            "print (int)&((Clip*)0)->loopLength", "print (int)ClipType::AUDIO", "print (int)OutputType::CV"])
        _, _, memory, count, msize, mstart, esize = rig.heap_offsets

        def audio_clips():
            arr = rig.song() + clips_off
            mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
            clips = [emu.u32(mem + ((first + i) % max(size, 1)) * es) for i in range(n)]
            return [c for c in clips if emu.u8(c + type_off) == audio_type]

        def fields(c):
            return dict(length=emu.u32(c + length_off), section=emu.u8(c + section_off),
                        isPlaying=emu.u8(c + active_off))

        def cv_outputs():
            out, found, guard = emu.u32(rig.song() + first_out), [], 0
            while out and guard < 64:
                if emu.u8(out + otype_off) == cv_type:
                    found.append(dict(name=rig.string(out + oname_off), channel=emu.u32(out + chan_off)))
                out, guard = emu.u32(out + next_off), guard + 1
            return found

        res["cv_loaded"] = cv_outputs()
        if res["cv_loaded"] != [dict(name=CV_NAME, channel=CV_CHANNEL)]:
            raise SystemExit(f"the card's CV instrument didn't load as written: {res['cv_loaded']}")
        clip, = audio_clips()
        emu.uc.mem_write(clip + section_off, bytes([5]))
        res["before_save"] = dict(audio=fields(clip), cv=cv_outputs())
        rig.play()
        rig.tm(1.0, "playing")

        # The save, as the user does it: the save-song UI opened, the name typed, saved (mayOverwrite)
        ok, _ = rig.action("open the save-song UI", rig.a["_Z6openUIP2UI"], sym["saveSongUI"], limit_s=20)
        rig.wait_mode_none()
        res["save_ui_opened"] = bool(ok & 0xFF)
        emu.uc.mem_write(se.STOP + 0x200, SAVE_NAME.encode() + b"\0")
        emu.call(sym["_ZN6String3setEPKcl"], rig.v["_ZN8QwertyUI11enteredTextE"], se.STOP + 0x200, 0xFFFFFFFF)
        saved, _ = rig.action("SaveSongUI::performSave", sym.find("_ZN10SaveSongUI11performSaveEb"), sym["saveSongUI"],
                              1, limit_s=30)
        res["saved"] = bool(saved & 0xFF)
        rig.tm(0.5, "after the save")
        xml = fat32.read_file(emu.sd_path, f"SONGS/{SAVE_NAME}.XML")
        open(os.path.join(a.out, f"{SAVE_NAME}.XML"), "wb").write(xml)
        res["saved_bytes"] = len(xml)
        tag = re.search(rb"<audioClip\b[^>]*>", xml).group(0).decode()
        names = re.findall(r"\s([A-Za-z_]\w*)=", tag)
        res["audioClip_dupes"] = sorted({n for n in names if names.count(n) > 1})
        instruments = re.search(rb"<instruments>(.*?)</instruments>", xml, re.S).group(1).decode()
        cv_tags = re.findall(r"<cvChannel\b[^>]*>", instruments)
        res["cv_saved"] = [dict(re.findall(r'\s(\w+)="([^"]*)"', t)) for t in cv_tags]
        res["cv_channel_saved"] = [t.get("channel") for t in res["cv_saved"]] == [str(CV_CHANNEL)]

        # Loaded back with the song browser
        back = rig.change_song(SAVE_NAME, 0.5)
        res["load_back"] = dict(ok=back["ok"], loaded=back["loaded"], playing=back["playing"], ui=back["ui"])
        res["after_load"] = dict(audio=[fields(c) for c in audio_clips()], cv=cv_outputs())
        res["survived"] = back["ok"] and res["after_load"] == dict(audio=[res["before_save"]["audio"]],
                                                                   cv=res["before_save"]["cv"])
    except (su.Stop, SystemExit) as ex:
        res["stopped"] = str(ex)
    if rig:
        res["problems"] = rig.problems
        res["error_popups"] = rig.error_popups()
        res["console"] = rig.console[-10:]
    json.dump(res, open(os.path.join(a.out, "save_4917.json"), "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))
    ok = (res.get("saved") is True and res.get("audioClip_dupes") == [] and res.get("cv_channel_saved") is True
          and res.get("survived") is True and not res.get("stopped") and not res.get("problems")
          and not res.get("error_popups"))
    print(f"duplicate attributes in <audioClip>: {res.get('audioClip_dupes')}; CV channel saved: "
          f"{res.get('cv_channel_saved')} ({res.get('cv_saved')}); loaded back with the same values: "
          f"{res.get('survived')} ({(res.get('load_back') or {}).get('loaded')!r})")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
