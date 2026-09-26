#!/usr/bin/env python3
"""Ratchet roll / bounce length in the real firmware (emulated): a song with one synth whose arp ratchets on every
step, every note-on after the arp (Sound::noteOnPostArpeggiator) logged with its window's sample time and velocity,
compared with the formula.

Usage: arp_roll_test.py <deluge.elf> [case ...]"""
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "../song"))  # the song harness: song_emu.py, make_sd.py, fat32.py
BUILD = os.environ.get("BLOCKCOUNT_DIR", HERE)  # where blockcount.so is (run.sh builds it)
import make_sd  # noqa: E402
import song_emu  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R3, UC_ARM_REG_SP  # noqa: E402

SR = 44100
STEP_8TH = SR * 60 // 120 // 2  # 11025 samples at 120 BPM
MIN_GAP = 706  # kRollMinGapSamples

CASES = {
    # name: arp attributes, sound params, bars to record
    "roll+4_len4_rise": (dict(syncLevel=5, ratchetNotes=255, ratchetBounce=4, ratchetBounceLength=4,
                              ratchetBounceFade=2), {}, 4),
    "roll-4_len4_fade": (dict(syncLevel=5, ratchetNotes=255, ratchetBounce=-4, ratchetBounceLength=4,
                              ratchetBounceFade=1), {}, 4),
    "roll0_len2": (dict(syncLevel=5, ratchetNotes=255, ratchetBounce=0, ratchetBounceLength=2,
                        ratchetBounceFade=1), {}, 2),
    "fixed3+6_len1_even": (dict(syncLevel=5, ratchetNotes=3, ratchetBounce=6, ratchetBounceLength=1,
                                ratchetBounceFade=0), {}, 2),
    "fixed4+5_len2_16th": (dict(syncLevel=6, ratchetNotes=4, ratchetBounce=5, ratchetBounceLength=2,
                                ratchetBounceFade=1), {}, 2),
    "roll+6_len1_16th": (dict(syncLevel=6, ratchetNotes=255, ratchetBounce=6, ratchetBounceLength=1,
                              ratchetBounceFade=2), {}, 2),
    "roll+4_len4_unsynced": (dict(syncLevel=0, ratchetNotes=255, ratchetBounce=4, ratchetBounceLength=4,
                                  ratchetBounceFade=0), {}, 4),
}


def build_sd(path, arp, params):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda: ""
    env = dict(attack=make_sd.knob(0), decay=make_sd.knob(10), sustain=make_sd.knob(0), release=make_sd.knob(0))
    p = dict(ratchetProbability=make_sd.knob(50), arpeggiatorGate=make_sd.knob(25), reverbAmount=make_sd.knob(0),
             delayFeedback=make_sd.knob(0))
    p.update(params)
    a = dict(arpMode="arp", noteMode="up", octaveMode="up", numOctaves=1)
    a.update(arp)
    synth = make_sd.synth("PING", make_sd.SAW, make_sd.SQUARE, 1, p, env, make_sd.PAD_ENV2,
                          [("velocity", "volume", 25)], notes_octave=1, arp=a)
    make_sd.synths = lambda: [synth]
    files, lengths = make_sd.samples()
    xml = make_sd.song_xml(lengths, 1, 1)
    files["SONGS/DEFAULT.XML"] = xml.encode()
    make_sd.fat32.build(path, files)


def run_case(elf, name, out_dir):
    arp, params, bars = CASES[name]
    sd = os.path.join(out_dir, f"{name}.img")
    build_sd(sd, arp, params)
    tools = os.path.join(os.path.dirname(os.path.abspath(elf)),
                         "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    emu = song_emu.Emulator(elf, sd, tools, BUILD, lambda s: None)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    emu.w32(emu.sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    player = song_emu.Player(emu)
    timer = emu.sym["_ZN11AudioEngine16audioSampleTimerE"]
    hits = []

    def on_note_on(e):
        sp = e.uc.reg_read(UC_ARM_REG_SP)
        velocity = struct.unpack("<i", e.uc.mem_read(sp, 4))[0]
        hits.append((e.u32(timer), e.uc.reg_read(UC_ARM_REG_R3), velocity))

    emu.intercept(emu.sym.find("_ZN5Sound21noteOnPostArpeggiator"), on_note_on)
    player.start()
    player.play(int(bars * make_sd.BAR_SAMPLES) if hasattr(make_sd, "BAR_SAMPLES") else bars * 4 * SR // 2)
    return hits


def expected_roll(span, ratio, mirrored, even_gap=None):
    if even_gap is not None:
        n = int(span // even_gap)
        return [i * even_gap for i in range(n)], [even_gap] * n
    gap, remaining, k = span * (1 - ratio), span, 0
    while gap >= MIN_GAP:
        gap *= ratio
        remaining *= ratio
        k += 1
    n = k + int(remaining // MIN_GAP)
    acc = [span * (1 - ratio ** j) if j <= k else span * (1 - ratio ** k) + (j - k) * MIN_GAP for j in range(n)]
    if mirrored:
        return [0.0] + [span - acc[n - i] for i in range(1, n)]
    return acc


def expected_finite(n, amount, span):
    ratio = 1 - 0.05 * abs(amount)
    ratio = ratio if amount > 0 else 1 / ratio
    gaps = [ratio ** i for i in range(n)]
    total = sum(gaps)
    return [sum(gaps[:i]) / total * span for i in range(n)]


def main():
    elf = sys.argv[1]
    names = sys.argv[2:] or list(CASES)
    out_dir = os.path.join(os.getcwd(), "out")
    os.makedirs(out_dir, exist_ok=True)
    results = {}
    for name in names:
        hits = run_case(elf, name, out_dir)
        results[name] = hits
        arp = CASES[name][0]
        step = STEP_8TH if arp["syncLevel"] == 5 else STEP_8TH // 2 if arp["syncLevel"] == 6 else None
        print(f"\n== {name}: {len(hits)} note-ons")
        t0 = hits[0][0] if hits else 0
        line = []
        for t, note, vel in hits[:48]:
            line.append(f"{t - t0}:{note}/{vel}")
        print("  " + " ".join(line))
        if step:
            span = step * arp["ratchetBounceLength"]
            if arp["ratchetNotes"] == 255:
                amount = arp["ratchetBounce"]
                exp = expected_roll(span, 1 - 0.05 * abs(amount), amount < 0,
                                    even_gap=max(step / 8, MIN_GAP) if amount == 0 else None)
                if amount == 0:
                    exp = exp[0]
            else:
                exp = expected_finite(arp["ratchetNotes"], arp["ratchetBounce"], span)
            # group the hits by span: a span starts at every multiple of span from the first hit
            first = [h for h in hits if (h[0] - t0) < span]
            got = [h[0] - t0 for h in first]
            print(f"  expected ({len(exp)}): " + " ".join(f"{e:.0f}" for e in exp))
            print(f"  got      ({len(got)}): " + " ".join(str(g) for g in got))
            if len(got) == len(exp):
                err = [g - e for g, e in zip(got, exp)]
                print(f"  error (samples, window-quantised, want 0 ... +128): min {min(err):.0f} max {max(err):.0f}")
            spans = sorted({(h[0] - t0) // span for h in hits})
            notes = [next(h[1] for h in hits if (h[0] - t0) // span == s) for s in spans]
            per = [sum(1 for h in hits if (h[0] - t0) // span == s) for s in spans]
            print(f"  per span: hits {per}, first notes {notes}")
    json.dump(results, open(os.path.join(out_dir, "hits.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
