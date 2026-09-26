#!/usr/bin/env python3
"""Adversarial edge cases for ratchet roll / bounce length: one synth, custom clip notes (ticks, 96 per quarter,
120 BPM), every note-on and note-off after the arp logged with its window's sample time.

Usage: edge_test.py <deluge.elf> case [case ...]"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "../song"))  # the song harness
BUILD = os.environ.get("BLOCKCOUNT_DIR", HERE)
import make_sd  # noqa: E402
import song_emu  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP  # noqa: E402

SR = 44100
TICK = SR * 60 / 120 / 96  # samples per tick
K = make_sd.knob

# name: (arp attributes, sound params, rows [(note, [(pos ticks, length ticks)])], bars)
CASES = {
    # Unsynced (sync off, transport running) + latch: the latched note replaced while a hit that crosses a step boundary
    # is sounding (2 hits over 4 steps, gate 80 %)
    "latch_unsynced_len4": (dict(syncLevel=0, latch=1, ratchetNotes=2, ratchetBounceLength=4),
                            dict(ratchetProbability=K(50), arpeggiatorGate=K(40)),
                            [(60, [(0, 2)]), (64, [(6, 2)])], 4),
    "latch_unsynced_len1": (dict(syncLevel=0, latch=1, ratchetNotes=2, ratchetBounceLength=1),
                            dict(ratchetProbability=K(50), arpeggiatorGate=K(40)),
                            [(60, [(0, 2)]), (64, [(6, 2)])], 4),
    "latch_unsynced_roll_len4": (dict(syncLevel=0, latch=1, ratchetNotes=255, ratchetBounce=4, ratchetBounceLength=4),
                                 dict(ratchetProbability=K(50), arpeggiatorGate=K(40)),
                                 [(60, [(0, 2)]), (64, [(40, 2)])], 4),
    # Same, synced (16ths) for comparison: the tick ends it
    "latch_synced_len4": (dict(syncLevel=6, latch=1, ratchetNotes=2, ratchetBounceLength=4),
                          dict(ratchetProbability=K(50), arpeggiatorGate=K(40)),
                          [(60, [(0, 2)]), (64, [(6, 2)])], 2),
    # Sequence length 4, every step a ratchet over 3 steps, 3 notes held: where does the sequence restart?
    "seqlen4_len3": (dict(syncLevel=6, ratchetNotes=2, ratchetBounceLength=3),
                     dict(ratchetProbability=K(50), sequenceLength=K(4)),
                     [(60, [(0, 384 * 4)]), (64, [(0, 384 * 4)]), (67, [(0, 384 * 4)])], 2),
    "seqlen4_len1": (dict(syncLevel=6, ratchetNotes=2, ratchetBounceLength=1),
                     dict(ratchetProbability=K(50), sequenceLength=K(4)),
                     [(60, [(0, 384 * 4)]), (64, [(0, 384 * 4)]), (67, [(0, 384 * 4)])], 2),
    # Note probability 50 %, ratchet always, 2 notes over 4 steps: a dropped note silences 4 steps
    "noteprob50_len4": (dict(syncLevel=6, ratchetNotes=2, ratchetBounceLength=4),
                        dict(ratchetProbability=K(50), noteProbability=K(25)),
                        [(60, [(0, 384 * 4)]), (64, [(0, 384 * 4)]), (67, [(0, 384 * 4)])], 4),
    # Release all keys mid-span, press again (no latch), unsynced and synced
    # Swing 75 % (swingAmount 25, 16ths): 4 hits over 2 steps vs 2 hits over 1 step
    "swing_len2": (dict(syncLevel=6, ratchetNotes=4, ratchetBounceLength=2, _swing=25),
                   dict(ratchetProbability=K(50)), [(60, [(0, 384 * 4)])], 1),
    "swing_len1": (dict(syncLevel=6, ratchetNotes=2, ratchetBounceLength=1, _swing=25),
                   dict(ratchetProbability=K(50)), [(60, [(0, 384 * 4)])], 1),
    "release_repress_unsynced": (dict(syncLevel=0, ratchetNotes=2, ratchetBounceLength=4),
                                 dict(ratchetProbability=K(50), arpeggiatorGate=K(40)),
                                 [(60, [(0, 30), (40, 60)])], 2),
    "release_repress_synced": (dict(syncLevel=6, ratchetNotes=2, ratchetBounceLength=4),
                               dict(ratchetProbability=K(50), arpeggiatorGate=K(40)),
                               [(60, [(0, 30), (48, 60)])], 2),
}


def sound_params_for(name):
    return CASES[name][1]


def build_sd(path, arp, params, rows):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda: ""
    env = dict(attack=K(0), decay=K(10), sustain=K(50), release=K(0))
    p = dict(ratchetProbability=K(50), arpeggiatorGate=K(25), reverbAmount=K(0), delayFeedback=K(0))
    p.update(params)
    a = dict(arpMode="arp", noteMode="up", octaveMode="up", numOctaves=1)
    a.update(arp)
    swing = a.pop("_swing", 0)
    clip_rows = [("y", note, [(pos, length, 90) for pos, length in notes], None) for note, notes in rows]
    make_sd.chord_rows = lambda octave, velocity=90: clip_rows
    synth = make_sd.synth("PING", make_sd.SAW, make_sd.SQUARE, 1, p, env, make_sd.PAD_ENV2,
                          [("velocity", "volume", 25)], notes_octave=0, arp=a)
    make_sd.synths = lambda: [synth]
    files, lengths = make_sd.samples()
    xml = make_sd.song_xml(lengths, 1, 1).replace('swingAmount="0"', f'swingAmount="{swing}"')
    files["SONGS/DEFAULT.XML"] = xml.encode()
    make_sd.fat32.build(path, files)


def run_case(elf, name, out_dir):
    arp, params, rows, bars = CASES[name]
    sd = os.path.join(out_dir, f"{name}.img")
    build_sd(sd, arp, params, rows)
    tools = os.path.join(os.path.dirname(os.path.abspath(elf)), "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    emu = song_emu.Emulator(elf, sd, tools, BUILD, lambda s: None)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    emu.w32(emu.sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    player = song_emu.Player(emu)
    timer = emu.sym["_ZN11AudioEngine16audioSampleTimerE"]
    events = []

    def on_note_on(e):
        sp = e.uc.reg_read(UC_ARM_REG_SP)
        velocity = struct.unpack("<i", e.uc.mem_read(sp, 4))[0]
        events.append((e.u32(timer), "on", e.uc.reg_read(UC_ARM_REG_R3), velocity))

    def on_note_off(e):
        events.append((e.u32(timer), "off", struct.unpack("<i", struct.pack("<I", e.uc.reg_read(UC_ARM_REG_R2)))[0], 0))

    emu.intercept(emu.sym.find("_ZN5Sound21noteOnPostArpeggiator"), on_note_on)
    emu.intercept(emu.sym.find("_ZN5Sound22noteOffPostArpeggiator"), on_note_off)

    def on_tick(e):
        pos = e.uc.reg_read(UC_ARM_REG_R3)
        if os.environ.get("TICKS") and pos % 24 == 0:
            events.append((e.u32(timer), "T", pos, 0))
    emu.intercept(emu.sym.find("_ZN15ArpeggiatorBase13doTickForward"), on_tick)
    player.start()
    t_start = emu.u32(timer)
    player.play(int(bars * 4 * SR // 2))
    return t_start, events


def main():
    elf = sys.argv[1]
    names = sys.argv[2:] or list(CASES)
    out_dir = os.path.join(os.getcwd(), "out")
    os.makedirs(out_dir, exist_ok=True)
    for name in names:
        t_start, events = run_case(elf, name, out_dir)
        print(f"\n== {name}: {sum(1 for e in events if e[1] == 'on')} note-ons, "
              f"{sum(1 for e in events if e[1] == 'off')} note-offs (times in samples from play; 16th = "
              f"{SR * 60 // 120 // 4})")
        print("  " + " ".join(f"{t - t_start}:{kind}{note}" + (f"/{vel}" if kind == "on" else "")
                              for t, kind, note, vel in events[:int(os.environ.get("N", 80))] if kind != "off"))


if __name__ == "__main__":
    main()
