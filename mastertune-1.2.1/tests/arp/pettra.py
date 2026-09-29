#!/usr/bin/env python3
"""PETTRA ARP: a synth preset for the arpeggiator of Pettra - "You Are The Seeds" (references/pettra-arp), saved by
the firmware itself (emulated), and a demo rendered with it: A4 C#5 E5 held, 8 bars at 138 BPM, as at 0:55.

What the two excerpts show (references/pettra-arp/ANALYSIS.md, checked again on 2026-09-28):
- No continuous arp stream: the arp's band has no 1/32 or 1/16 period (autocorrelation of the onsets r <= 0.06,
  the beat 0.7). Kick on the beat, hat on the offbeat.
- The figure: from the offbeat 3 hits into the next beat, each gap x0.7 (0:57.18: 102, 67, 52 ms), now and then.
- The voice: saw, slightly detuned (10-15 cents), narrow, a bright pluck, the filter open, hardly any resonance, the
  hits equally loud. After a hit (0:56.3-0:56.6) 1.5-4 kHz falls 3, 5, 7 dB at 20, 40, 60 ms and 4-9 kHz 8, 16, 19 dB;
  this voice: 2, 3, 7 and 6, 15, 25 dB (the song's pad and reverb fill in below -20 dB).
So: sync 1/8, random notes over 2 octaves, 3 ratchets with bounce +6 (x0.7) over one step, even velocity, and a
ratchet probability of 14 % so that the figure comes now and then (about once a bar) instead of on every step.

Usage: pettra.py <deluge.elf> <out dir>   (BLOCKCOUNT_DIR: where blockcount.so is, see run.sh)"""
import os
import re
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "../song"))  # the song harness: song_emu.py, make_sd.py, fat32.py
BUILD = os.environ.get("BLOCKCOUNT_DIR", HERE)
import fat32  # noqa: E402
import gen_presets  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402

k = make_sd.knob
NAME = "PETTRA ARP"
BPM = 138
# The voice: two saws 12 cents apart, no unison (narrow), 24 dB low-pass half open with a short filter envelope on
# top (the bright attack, fitted to the decay measured above), an amp envelope that decays without much sustain, the
# velocity hardly on the volume
PARAMS = dict(oscAVolume=k(50), oscBVolume=k(44), lpfFrequency=k(26), lpfResonance=k(3), hpfFrequency=k(0),
              reverbAmount=k(10), delayFeedback=k(0), arpeggiatorGate=k(28), volume=k(37),
              ratchetProbability=k(7), ratchetAmount=k(0), noteProbability=k(50))
ENV1 = dict(attack=k(0), decay=k(20), sustain=k(6), release=k(8))
ENV2 = dict(attack=k(0), decay=k(9), sustain=k(0), release=k(6))
CABLES = [("velocity", "volume", 10), ("envelope2", "lpfFrequency", 22), ("note", "lpfFrequency", 5)]
ARP = dict(arpMode="arp", noteMode="random", octaveMode="up", numOctaves=2, syncLevel=5,
           ratchetNotes=3, ratchetBounce=6, ratchetBounceLength=1, ratchetBounceFade=0)
A_MAJOR = [69, 73, 76]  # A4 C#5 E5: with 2 octaves the arp spans A4 to E6, the register of the passage at 0:55
# Headphones plugged in: the firmware renders in stereo only with headphones or the right line output plugged in
# (AudioEngine::renderInStereo, set from the pins in deluge.cpp's inputRoutine()), and only then does a pan per voice
# take effect. In the emulator the pins read 0, as with the speaker alone, and the player doesn't run inputRoutine(),
# so the flags are set directly, and the pin too (HEADPHONE_DETECT, port 6 pin 5: PPR6, GPIO.PPR1 at 0xFCFE3204 plus
# 4 bytes a port) in case something reads it again.
PPR6 = 0xFCFE3218
HEADPHONE_DETECT_BIT = 1 << 5


def plug_headphones(emu):
    emu.uc.mem_write(PPR6, HEADPHONE_DETECT_BIT.to_bytes(2, "little"))
    for name in ("_ZN11AudioEngine19headphonesPluggedInE", "_ZN11AudioEngine14renderInStereoE"):
        emu.uc.mem_write(emu.sym[name], b"\x01")


def use_voice():
    gen_presets.PARAMS, gen_presets.ENV1, gen_presets.ENV2, gen_presets.CABLES = PARAMS, ENV1, ENV2, CABLES
    make_sd.chord_rows = lambda octave, velocity=100: [("y", n, [(0, 4 * make_sd.BAR, velocity)], None)
                                                       for n in A_MAJOR]


def tempo_attrs(bpm):
    """timePerTimerTick as Song::setBPMInner() computes it (inputTickMagnitude 2), and the tempo param (BPM x 100)"""
    ticks = 110250 / (bpm * 4)
    fraction = int(round((ticks - int(ticks)) * 2 ** 32))
    return int(ticks), fraction - 2 ** 32 if fraction >= 2 ** 31 else fraction, f"0x{int(bpm * 100):08X}"


def build_sd(path):
    gen_presets.build_sd(path, NAME, ARP)
    whole, fraction, tempo = tempo_attrs(BPM)
    files = {"SYNTHS/KEEP.TXT": b"x"}
    song = make_sd.song_xml(make_sd.samples()[1], 1, 1)
    song = re.sub(r'timePerTimerTick="\d+"', f'timePerTimerTick="{whole}"', song)
    song = re.sub(r'timerTickFraction="-?\d+"', f'timerTickFraction="{fraction}"', song)
    song = re.sub(r'tempo="0x[0-9A-F]+"', f'tempo="{tempo}"', song)
    files["SONGS/DEFAULT.XML"] = song.encode()
    fat32.build(path, files)


def render(elf, out_dir, bars=8):
    sd = os.path.join(out_dir, "demo.img")
    build_sd(sd)
    tools = os.path.join(os.path.dirname(os.path.abspath(elf)),
                         "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    emu = song_emu.Emulator(elf, sd, tools, BUILD, lambda s: None)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    emu.w32(emu.sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    plug_headphones(emu)
    player = song_emu.Player(emu)
    player.start()
    record = []
    player.play(int(bars * 4 * 60 / BPM * 44100) + 2048, record)
    x = np.concatenate([w[4] for w in record])
    peak = np.abs(x).max()
    x = x / max(peak, 1e-9) * 0.7  # normalised for listening
    path = os.path.join(out_dir, f"{NAME}.wav")
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes((x * 32767).astype("<i2").tobytes())
    print(path, f"peak {20 * np.log10(max(peak, 1e-9)):.1f} dBFS")
    return path


def main():
    elf, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    use_voice()
    print(gen_presets.save_preset(elf, NAME, ARP, out_dir))
    render(elf, out_dir)


if __name__ == "__main__":
    main()
