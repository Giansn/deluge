#!/usr/bin/env python3
"""Synth presets for the ratchet roll, saved by the firmware itself (emulated): a song with the synth is loaded, then
the same calls as SaveInstrumentPresetUI::performSave() write SYNTHS/<name>.XML (createXMLFile(), Output::writeToFile()
with the clip, closeFileAfterWriting()).

Usage: gen_presets.py <deluge.elf> <out dir>"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "../song"))  # the song harness: song_emu.py, make_sd.py, fat32.py
BUILD = os.environ.get("BLOCKCOUNT_DIR", HERE)  # where blockcount.so is (run.sh builds it)
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402

k = make_sd.knob
SAW = dict(type="saw", transpose=0, cents=0, retrigPhase=-1)
SAW_DETUNED = dict(type="saw", transpose=0, cents=12, retrigPhase=-1)

# Pettra-like voice (references/pettra-arp): two slightly detuned saws, 24 dB low-pass fairly open, snappy amp
# envelope without sustain, a little filter envelope, velocity on the volume (so Rise/Fade are heard)
PARAMS = dict(oscAVolume=k(50), oscBVolume=k(38), lpfFrequency=k(38), lpfResonance=k(6), hpfFrequency=k(0),
              reverbAmount=k(12), delayFeedback=k(0), arpeggiatorGate=k(20), volume=k(37),
              ratchetProbability=k(50), ratchetAmount=k(0), noteProbability=k(50))
ENV1 = dict(attack=k(0), decay=k(15), sustain=k(0), release=k(5))
ENV2 = dict(attack=k(0), decay=k(16), sustain=k(0), release=k(5))
CABLES = [("velocity", "volume", 35), ("envelope2", "lpfFrequency", 10), ("note", "lpfFrequency", 5)]

PRESETS = {
    # The new roll: a ping-pong ball between plates closing on the step after the ratchet
    "PINGPONG ROLL": dict(arpMode="arp", noteMode="up", octaveMode="up", numOctaves=2, syncLevel=5,
                          ratchetNotes=255, ratchetBounce=4, ratchetBounceLength=4, ratchetBounceFade=2),
    # The figure from Pettra - You Are The Seeds (0:57, 6:39): 3 even hits in an eighth, gaps x0.7
    "PETTRA PINGPONG": dict(arpMode="arp", noteMode="up", octaveMode="up", numOctaves=2, syncLevel=5,
                            ratchetNotes=3, ratchetBounce=6, ratchetBounceLength=1, ratchetBounceFade=0),
}


def build_sd(path, name, arp):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda *args, **kwargs: ""
    synth = make_sd.synth(name, SAW, SAW_DETUNED, 1, PARAMS, ENV1, ENV2, CABLES, notes_octave=1, arp=arp)
    make_sd.synths = lambda: [synth]
    files, lengths = make_sd.samples()
    files = {p: d for p, d in files.items() if not p.startswith("SAMPLES/")}
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 1).encode()
    files["SYNTHS/KEEP.TXT"] = b"x"  # the folder
    fat32.build(path, files)


def gdb_offsets(tools, elf):
    out = subprocess.run([tools + "gdb", "-batch", "-ex", "list SaveInstrumentPresetUI::performSave",
                          "-ex", "print (int)&((Song*)0)->firstOutput", "-ex", "print (int)&((Output*)0)->activeClip",
                          elf], capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (\d+)$", out, re.M)]
    assert len(values) == 2, out[-500:]
    return values


def save_preset(elf, name, arp, out_dir):
    sd = os.path.join(out_dir, "preset.img")
    build_sd(sd, name, arp)
    tools = os.path.join(os.path.dirname(os.path.abspath(elf)),
                         "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    first_output, active_clip = gdb_offsets(tools, elf)
    emu = song_emu.Emulator(elf, sd, tools, BUILD, lambda s: None)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    song_emu.load_startup_song(emu)
    song = emu.u32(emu.sym["currentSong"])
    output = emu.u32(song + first_output)
    clip = emu.u32(output + active_clip)
    assert output and clip, (hex(song), hex(output), hex(clip))
    scratch = song_emu.STOP + 0x100
    path = "SYNTHS/PRESET.XML"  # 8.3 for fat32.read_file(); the name on the card is the file's own
    begin = b'<?xml version="1.0" encoding="UTF-8"?>\n'
    end = b"\n</sound>\n"
    emu.uc.mem_write(scratch, path.encode() + b"\0")
    emu.uc.mem_write(scratch + 0x80, begin + b"\0")
    emu.uc.mem_write(scratch + 0xC0, end + b"\0")
    # createXMLFile(this, path, mayOverwrite, displayErrors): the clone with smSerializer folded in
    storage = emu.sym["storageManager"]
    error = emu.call(emu.sym.find("_ZN14StorageManager13createXMLFileEPKcR13XMLSerializerbb.constprop"), storage,
                     scratch, 1, 0)
    assert error == 0, f"createXMLFile: {error}"
    emu.call(emu.sym.find("_ZN6Output11writeToFileER14StorageManagerP4ClipP4Song"), output, storage, clip, song)
    error = emu.call(emu.sym.find("_ZN13XMLSerializer21closeFileAfterWritingEPKcS1_S1_"), emu.sym["smSerializer"],
                     scratch, scratch + 0x80, scratch + 0xC0)
    assert error == 0, f"closeFileAfterWriting: {error}"
    data = fat32.read_file(sd, path)
    out = os.path.join(out_dir, f"{name}.XML")
    open(out, "wb").write(data)
    return out


def main():
    elf, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    for name, arp in PRESETS.items():
        print(save_preset(elf, name, arp, out_dir))


if __name__ == "__main__":
    main()
