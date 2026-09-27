#!/usr/bin/env python3
"""Checks what the drone-track song (make_sd.py --drone-track, mastertune-v16) played in the emulator (run.sh's
DRONE=1): drone tracks, kits of drone rows (DroneDrum), placed in clips that overlap, one row with an Hz lane.

Usage: drone_check.py <dir of the run as loaded> [<dir of the run after the save round trip>]
  Each dir has measured.wav (song_emu.py, --warmup-bars 1: it starts 1 bar into the song); the second also saved.xml
  (song_emu.py --write-back: the firmware saved the song and loaded it again before playing it).

Checked, in windows of 4096 samples (93 ms, 7-term Blackman-Harris) a quarter second away from every change, the
windowed DFT at each of the song's frequencies (make_sd.DRONE_TRACKS): each row sounds exactly when its notes say
(within 40 dB of the loudest tone when on, 60 dB below it when off), at the Hz its lane says: DRONE1's 200 Hz row a
fifth up (+702 cents, 300.01 Hz) in the clip's 2nd bar, measured to 0.02 Hz over 0.74 s; DRONE2's rows, each a note as
long as its 1-bar clip, never drop out at its loop point; the level of a drone row (1100 Hz, level 40, a new kit's
volume) is the song's drone's (1000 Hz, level 40, its volume 40) within 1 dB. After the save round trip: the XML has
the four droneTone rows and the lane (two nodes) on DRONE1's first row, and the song plays the same, sample for sample.
"""
import hashlib
import math
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_sd  # noqa: E402

SR = 44100
BAR_S = 2.0  # 120 BPM, 4/4
TICK_S = BAR_S / make_sd.BAR
WARMUP_BARS = 1
N = 4096

failures = 0


def check(what, ok, detail=""):
    global failures
    failures += not ok
    print(f"  [{'ok' if ok else 'FAIL'}] {what}" + (f": {detail}" if detail else ""))


def read_wav(path):
    data = open(path, "rb").read()
    pcm = np.frombuffer(data[44:], "<i2").reshape(-1, 2).astype(np.float64) / 32768
    return pcm


def window(n):
    a = [0.27105140069342, 0.43329793923448, 0.21812299954311, 0.06592544638803, 0.01081174209837,
         0.00077658482522, 0.00001388721735]
    t = 2 * np.pi * np.arange(n) / (n - 1)
    return sum(((-1) ** k) * a[k] * np.cos(k * t) for k in range(7))


def amplitude(x, hz):
    w = window(len(x))
    return 2 * abs(np.sum(x * w * np.exp(-2j * np.pi * hz * np.arange(len(x)) / SR))) / w.sum()


def peak_hz(x, lo, hi):
    n = len(x)
    m = np.abs(np.fft.rfft(x * window(n)))
    a, b = int(lo * n / SR), int(hi * n / SR)
    i = a + int(np.argmax(m[a:b + 1]))
    y0, y1, y2 = np.log(m[i - 1]), np.log(m[i]), np.log(m[i + 1])
    return (i + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)) * SR / n


def sounding(t):
    """Frequencies (Hz) sounding at song time t (seconds), as make_sd.DRONE_TRACKS has them, with each row's lane"""
    out = {make_sd.DRONE_REFERENCE_HZ: make_sd.DRONE_REFERENCE_HZ}
    for name, bars, rows in make_sd.DRONE_TRACKS:
        loop = bars * BAR_S
        pos = (t % loop) / TICK_S
        for frequency, notes, lane in rows:
            if not any(start <= pos < start + length for start, length in notes):
                continue
            cents = 0
            for node_pos, node_cents in lane:
                if pos >= node_pos:
                    cents = node_cents
            base = frequency / 100
            out[base] = round(base * 2 ** (cents / 1200), 2)
    return out


def all_frequencies():
    """Every frequency the song can sound (the rows' own and their lanes')"""
    fs = {make_sd.DRONE_REFERENCE_HZ}
    for _, _, rows in make_sd.DRONE_TRACKS:
        for frequency, _, lane in rows:
            fs.add(frequency / 100)
            for _, cents in lane:
                fs.add(round(frequency / 100 * 2 ** (cents / 1200), 2))
    return sorted(fs)


def check_run(d, label):
    print(f"== {label}")
    pcm = read_wav(os.path.join(d, "measured.wav"))
    x = pcm[:, 0]
    seconds = len(x) / SR
    start_s = WARMUP_BARS * BAR_S
    frequencies = all_frequencies()
    wrong = []
    windows = 0
    ratios = []
    t = 0.25
    while (t + N / 2 / SR) < seconds:
        song_t = start_s + t
        i = int(t * SR) - N // 2
        seg = x[i:i + N]
        amps = {f: amplitude(seg, f) for f in frequencies}
        loudest = max(amps.values())
        expected = set(sounding(song_t).values())
        for f in frequencies:
            on = f in expected
            db = 20 * math.log10(amps[f] / loudest + 1e-12)
            if (on and db < -40) or (not on and db > -60):
                wrong.append(f"{song_t:.2f} s: {f} Hz {'missing' if on else 'there'} ({db:.1f} dB)")
        ratios.append(20 * math.log10(amps[1100.0] / amps[make_sd.DRONE_REFERENCE_HZ]))
        windows += 1
        t += 0.5
    check(f"{windows} windows: every row sounds when its notes say, at its lane's Hz, and nothing else", not wrong,
          "; ".join(wrong[:6]))
    # The lane: DRONE1's row in its 2nd bar, measured over 0.74 s in the middle of a second without a change: 3 to 4 s
    # into the clip's loop (after the 500 Hz row's note), and its own pitch 0 to 1 s into it (before that note)
    def segments(phase, n):
        for song_t in np.arange(0, start_s + seconds, 4.0) + phase:
            i = int((song_t - start_s) * SR) - n // 2
            if i >= 0 and i + n <= len(x):
                yield x[i:i + n]
    lane_hz = [peak_hz(seg, 280, 320) for seg in segments(3.5, 32768)]
    expected_lane = sounding(3.5)[200.0]
    check(f"the Hz lane: 200 Hz a fifth up in the 2nd bar, {expected_lane} Hz",
          lane_hz and all(abs(h - expected_lane) < 0.02 for h in lane_hz),
          ", ".join(f"{h:.3f} Hz" for h in lane_hz))
    base_hz = [peak_hz(seg, 180, 220) for seg in segments(0.5, 32768)]
    check("and its own 200 Hz in the 1st bar", base_hz and all(abs(h - 200) < 0.02 for h in base_hz),
          ", ".join(f"{h:.3f} Hz" for h in base_hz))
    # DRONE2's rows are notes as long as its clip: held across its loop point (every bar), not started again (which
    # would dip: a gate closing fades 6 dB a block, and a new note glides in over about 15 ms). Their level in 23 ms
    # windows centred on each loop point against the level between them.
    dips = []
    for song_t in np.arange(0, start_s + seconds, BAR_S):
        i = int((song_t - start_s) * SR) - 512
        j = int((song_t + BAR_S / 2 - start_s) * SR) - 512
        if i < 0 or j + 1024 > len(x):
            continue
        for f in (700.0, 1100.0):
            dips.append(20 * math.log10(amplitude(x[i:i + 1024], f) / amplitude(x[j:j + 1024], f)))
    check(f"DRONE2's notes held across its loop point ({len(dips) // 2} of them)",
          dips and max(abs(d) for d in dips) < 0.5, f"levels there {min(dips):+.2f} to {max(dips):+.2f} dB")
    spread = max(ratios) - min(ratios)
    check("a drone row's level is the song's drone's (1100 Hz row against the 1000 Hz tone, within 1 dB)",
          abs(np.mean(ratios)) < 1 and spread < 0.1, f"{np.mean(ratios):+.2f} dB, spread {spread:.2f} dB")
    return hashlib.sha256(open(os.path.join(d, "measured.wav"), "rb").read()).hexdigest()


def main():
    loaded = check_run(sys.argv[1], "drone tracks: the song as written by make_sd.py")
    if len(sys.argv) > 2:
        saved = check_run(sys.argv[2], "the same after the save round trip (saved by the firmware, loaded again)")
        xml = open(os.path.join(sys.argv[2], "saved.xml"), encoding="utf-8", errors="replace").read()
        tones = re.findall(r"<droneTone\b", xml)
        check("saved: the four drone rows as droneTone", len(tones) == 4, f"{len(tones)}")
        lanes = re.findall(r'<noteRow\b[^>]*drumIndex="(\d+)"[^>]*>\s*<expressionData\s+pitchBend="0x([0-9A-F]+)"',
                           xml)
        lane_ok = len(lanes) == 1 and lanes[0][0] == "0" and len(lanes[0][1]) == 8 + 2 * 16
        check("saved: the Hz lane on DRONE1's first row, two nodes", lane_ok, str(lanes))
        check("the round trip plays the same, sample for sample", loaded == saved, f"{loaded[:16]} / {saved[:16]}")
    print(f"drone tracks: {'all checks passed' if not failures else f'{failures} failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
