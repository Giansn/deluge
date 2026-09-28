#!/usr/bin/env python3
"""The Pettra arp as a ping-pong ball between two plates that move towards each other and apart again, computed,
then played by the real firmware (emulated) with the PETTRA ARP voice: notes placed in a synth clip, the arp off.

The model: a ball flies at constant speed between two plates. G(t) is the time it needs to cross the gap at time t.
The plates are closest at the meeting points (every 2 beats, on beats 2 and 4). They close in slowly (speed s_a,
in units of the ball's speed) and part fast (s_p):
    G(t) = min(g_min + s_p * (t - previous meeting), g_min + s_a * (next meeting - t)).
The ball arrives where the plate is at that moment: after a hit at t_n the next is at t_{n+1} = t_n + G(t_{n+1}).
Solved for the gaps: while the plates close in, each gap is 1/(1+s_a) of the one before; while they part, 1/(1-s_p)
times. The song (references/pettra-arp/ANALYSIS.md) shows x0.5-0.85 into the beat (median about 0.7) and mostly a
jump of x4-7 right after the tightest hit (35 -> 154 ms): s_a = 0.45 (x0.69), s_p = 0.75 (x4). Per 2 beats that is
6 hits: one long gap after the meeting, then 5 ever faster into the next one. The hits are louder and brighter the
tighter the gap (the ball hits harder when the plates close in), the last before the meeting the loudest and longest.

Pitches: "plates" (default) alternates a low and a high A major note per hit, one per plate; "ratchet" plays A5 on
four hits of five and B5, C#6 or E5 on the others, as the song's plucks are mostly single A notes (METHODS.md).

Usage: pingpong.py <deluge.elf> <out dir> [s_a] [s_p] [g_min in ms] [plates|ratchet]
(BLOCKCOUNT_DIR: where blockcount.so is)"""
import os
import random
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "../song"))  # the song harness: song_emu.py, make_sd.py, fat32.py
BUILD = os.environ.get("BLOCKCOUNT_DIR", HERE)
import fat32  # noqa: E402
import make_sd  # noqa: E402
import pettra  # noqa: E402
import song_emu  # noqa: E402

k = make_sd.knob
BPM = pettra.BPM
BEAT = 60 / BPM
TICK = BEAT / make_sd.TICKS_PER_QUARTER
PERIOD = 2 * BEAT       # plates closest every 2 beats
FIRST_MEET = BEAT       # on beats 2 and 4
LOW = [69, 73, 76]      # one plate: A4 C#5 E5
HIGH = [81, 85, 88]     # the other: A5 C#6 E6
# The PETTRA ARP voice, arp off, with the velocity on volume and filter so that the accents are heard
PARAMS = dict(pettra.PARAMS)
CABLES = [("velocity", "volume", 35), ("envelope2", "lpfFrequency", 22), ("note", "lpfFrequency", 5),
          ("velocity", "lpfFrequency", 12)]


def gap(t, s_a, s_p, g_min):
    """G(t) and whether the plates are closing in (True) or parting (False) at t"""
    prev = FIRST_MEET + np.floor((t - FIRST_MEET) / PERIOD) * PERIOD
    parting, closing = g_min + s_p * (t - prev), g_min + s_a * (prev + PERIOD - t)
    return (closing, True) if closing <= parting else (parting, False)


def hits(duration, s_a, s_p, g_min):
    """Hit times: t_{n+1} = t_n + G(t_{n+1}), solved by bisection (x - t_n - G(x) grows with x for s < 1)."""
    t = [FIRST_MEET - PERIOD / 2]
    while t[-1] < duration:
        a, b = t[-1], t[-1] + 2.0
        for _ in range(60):
            m = (a + b) / 2
            if m - t[-1] - gap(m, s_a, s_p, g_min)[0] < 0:
                a = m
            else:
                b = m
        t.append(b)
    return np.array([x for x in t if 0 <= x < duration])


def velocities(times, s_a, s_p, g_min):
    """Tighter gap = harder hit: 50 at the widest gap to 127 at the tightest; parting x0.85 (the plate recedes)"""
    far = g_min + s_a * s_p / (s_a + s_p) * PERIOD             # where the two lines meet: the widest gap
    out = []
    for x in times:
        g, closing = gap(x, s_a, s_p, g_min)
        closeness = min(max((far - g) / (far - g_min), 0), 1)
        v = 50 + 77 * closeness ** 1.2
        out.append(v if closing else v * 0.85)
    return np.clip(np.round(out), 1, 127).astype(int)


def clip_rows(times, vels, bars, mode="plates"):
    rng = random.Random(7)
    rows = {}
    ticks = np.round(times / TICK).astype(int)
    for i, (tk, v) in enumerate(zip(ticks, vels)):
        nxt = ticks[i + 1] if i + 1 < len(ticks) else tk + 24
        length = max(1, min(int((nxt - tk) * 0.6), 22 if v < 120 else 28))   # short; the hardest hits ring longer
        if mode == "ratchet":
            pitch = 81 if rng.random() < 0.8 else rng.choice([83, 85, 76])
        else:
            pitch = rng.choice(LOW if i % 2 == 0 else HIGH)  # the ball alternates between the plates
        if tk < bars * make_sd.BAR:
            rows.setdefault(pitch, []).append((int(tk), length, int(v)))
    return [("y", p, notes, None) for p, notes in sorted(rows.items())]


def build_sd(path, rows):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda *args, **kwargs: ""
    make_sd.chord_rows = lambda octave, velocity=100: rows
    synth = make_sd.synth("PETTRA PINGPONG BALL", pettra.gen_presets.SAW, pettra.gen_presets.SAW_DETUNED, 1, PARAMS,
                          pettra.ENV1, pettra.ENV2, CABLES, notes_octave=0, arp=None)
    make_sd.synths = lambda: [synth]
    whole, fraction, tempo = pettra.tempo_attrs(BPM)
    song = make_sd.song_xml(make_sd.samples()[1], 1, 1)
    song = song.replace('timePerTimerTick="229"', f'timePerTimerTick="{whole}"')
    song = song.replace('timerTickFraction="-1073741824"', f'timerTickFraction="{fraction}"')
    song = song.replace('tempo="0x00002EE0"', f'tempo="{tempo}"')
    fat32.build(path, {"SONGS/DEFAULT.XML": song.encode(), "SYNTHS/KEEP.TXT": b"x"})


def main():
    elf, out_dir = sys.argv[1], sys.argv[2]
    s_a = float(sys.argv[3]) if len(sys.argv) > 3 else 0.45
    s_p = float(sys.argv[4]) if len(sys.argv) > 4 else 0.75
    g_min = float(sys.argv[5]) / 1000 if len(sys.argv) > 5 else 0.038
    mode = sys.argv[6] if len(sys.argv) > 6 else "plates"
    os.makedirs(out_dir, exist_ok=True)
    bars = 4
    times = hits(bars * 4 * BEAT, s_a, s_p, g_min)
    vels = velocities(times, s_a, s_p, g_min)
    rows = clip_rows(times, vels, bars, mode)
    gaps = np.diff(times) * 1000
    print(f"closing in x{1 / (1 + s_a):.2f} per hit, parting x{1 / (1 - s_p):.1f}; {len(times)} hits in {bars} bars")
    print("gaps (ms):", " ".join(f"{x:.0f}" for x in gaps[:20]))
    print("velocity :", " ".join(str(v) for v in vels[:21]))
    sd = os.path.join(out_dir, "pingpong.img")
    build_sd(sd, rows)
    tools = os.path.join(os.path.dirname(os.path.abspath(elf)),
                         "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    emu = song_emu.Emulator(elf, sd, tools, BUILD, lambda msg: None)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    emu.w32(emu.sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    player = song_emu.Player(emu)
    player.start()
    record = []
    player.play(int(2 * bars * 4 * BEAT * 44100) + 4096, record)   # the 4-bar clip twice
    x = np.concatenate([w[4] for w in record])
    peak = np.abs(x).max()
    x = x / max(peak, 1e-9) * 0.7
    path = os.path.join(out_dir, "PETTRA PINGPONG BALL.wav" if mode == "plates" else "PETTRA PINGPONG BALL B.wav")
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes((x * 32767).astype("<i2").tobytes())
    os.remove(sd)
    print(path, f"peak {20 * np.log10(max(peak, 1e-9)):.1f} dBFS")


if __name__ == "__main__":
    main()
