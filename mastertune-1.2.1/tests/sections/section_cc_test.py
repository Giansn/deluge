#!/usr/bin/env python3
"""Section launch commands after loading a song (upstream 95b7acab), on the real firmware in the emulator (the song
harness of ../song): a song whose sections are launched by a note (channel 3), by a CC (channel 4, stored as
channel + IS_A_CC) and by an MPE zone; after loading, what Song::sections[].launchMIDICommand holds. 1.2.1 dropped the
CC one.

Usage: section_cc_test.py <deluge.elf> [--tools PREFIX]   (BLOCKCOUNT_DIR: where blockcount.so is)"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import make_sd  # noqa: E402
import song_emu  # noqa: E402

IS_A_CC = 18  # NUM_CHANNELS: 16 channels and 2 MPE zones
# section id: (midiCommandChannel as stored, midiCommandNote, what it is)
SECTIONS = {0: (2, 60, "note on channel 3"), 1: (IS_A_CC + 3, 64, "CC 64 on channel 4"),
            2: (16, 48, "note in the lower MPE zone")}


def build_sd(path):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda *args, **kwargs: ""
    env = dict(attack=make_sd.knob(0), decay=make_sd.knob(20), sustain=make_sd.knob(30), release=make_sd.knob(10))
    synth = make_sd.synth("SEC", make_sd.SAW, make_sd.SQUARE, 1, {}, env, make_sd.PAD_ENV2, [], notes_octave=1)
    make_sd.synths = lambda: [synth]
    files, lengths = make_sd.samples()
    xml = make_sd.song_xml(lengths, 1, 1)
    sections = "\t<sections>\n" + "".join(
        f'\t\t<section id="{i}" numRepeats="0" midiCommandChannel="{c}" midiCommandNote="{n}" />\n'
        for i, (c, n, _) in SECTIONS.items()) + "\t</sections>\n"
    xml = xml.replace("</song>", sections + "</song>")
    files["SONGS/DEFAULT.XML"] = xml.encode()
    make_sd.fat32.build(path, files)


def offsets(tools, elf):
    queries = ["(int)&((Song*)0)->sections", "sizeof(Section)", "(int)&((Section*)0)->launchMIDICommand",
               "(int)&((LearnedMIDI*)0)->channelOrZone", "(int)&((LearnedMIDI*)0)->noteOrCC"]
    out = subprocess.run([tools + "gdb", "-batch", "-ex", "list Song::Song"] + [x for q in queries for x in
                                                                               ("-ex", f"print {q}")] + [elf],
                         capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (\d+)$", out, re.M)]
    if len(values) != len(queries):
        raise SystemExit(f"offsets from gdb:\n{out[-600:]}")
    return values


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools")
    a = ap.parse_args()
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    sd = os.path.join(os.getcwd(), "sections.img")
    build_sd(sd)
    emu = song_emu.Emulator(a.elf, sd, tools, os.environ.get("BLOCKCOUNT_DIR", HERE), lambda s: None)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    song_emu.load_startup_song(emu)
    song = emu.u32(emu.sym["currentSong"])
    sections, size, command, channel_off, note_off = offsets(tools, a.elf)
    failed = 0
    print(f"section launch commands after loading ({a.elf}):")
    for i, (channel, note, what) in SECTIONS.items():
        base = song + sections + i * size + command
        got = (emu.u8(base + channel_off), emu.u8(base + note_off))
        ok = got == (channel, note)
        failed += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} section {i + 1}, {what}: channelOrZone {got[0]}, noteOrCC {got[1]} "
              f"(file: {channel}, {note})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
