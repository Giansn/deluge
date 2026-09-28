#!/usr/bin/env python3
"""The song's stem read sample by sample, and why its fastest "hits" are the bass (METHODS.md, "Reading the samples").

1. Attacks from the samples: a 3 kHz high-pass (causal), per 0.5 ms the peak; an attack where the peak rises 8 dB over
   the loudest of the 3 ms before (at least 5 ms after the last). Its sample: the first in that 0.5 ms above 30 % of
   the burst's peak. L-R: which channel is louder.
2. The low partials: every 0.2 s, the strongest spectral peaks at 200-2000 Hz (186 ms window) and the fundamental
   whose harmonic series they sit on (f0 55-150 Hz with the most level on its harmonics h f0 against halfway
   between them, (h + 1/2) f0).
If the attacks come every period of that fundamental (or half of it), they are the waveform edges of a low saw, not
plucks.

Usage: samples.py <stem.wav> <from s> <to s>   (the stem from separate.py; needs numpy, scipy, soundfile)"""
import sys

import numpy as np
import scipy.signal as ss
import soundfile as sf

SR = 44100
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note(f):
    m = 69 + 12 * np.log2(f / 440)
    n = int(round(m))
    return f"{NAMES[n % 12]}{n // 12 - 1}{(m - n) * 100:+.0f}c"


def attacks(x, t0, t1):
    sos = ss.butter(4, 3000, "high", fs=SR, output="sos")
    hl, hr = ss.sosfilt(sos, x[:, 0]), ss.sosfilt(sos, x[:, 1])
    h = np.abs(hl + hr) / 2
    a, b, step = int(t0 * SR), int(t1 * SR), SR // 2000
    blocks = np.array([h[i:i + step].max() for i in range(a, b, step)])
    floor = np.percentile(blocks, 99) * 0.1
    out, last = [], -99
    for k in range(6, len(blocks) - 6):
        if blocks[k] > floor and blocks[k] > 2.5 * blocks[k - 6:k].max() and k - last >= 10:
            s0 = a + k * step
            burst = h[s0:s0 + 4 * step]
            first = s0 + int(np.argmax(burst > 0.3 * burst.max()))
            lr = 20 * np.log10((np.abs(hl[s0:s0 + 4 * step]).max() + 1e-12) / (np.abs(hr[s0:s0 + 4 * step]).max() + 1e-12))
            out.append((first, 20 * np.log10(burst.max() + 1e-12), lr))
            last = k
    return out


def series(m, t):
    n = 8192
    seg = m[int(t * SR):int(t * SR) + n] * np.hanning(n)
    X = 20 * np.log10(np.abs(np.fft.rfft(seg, 4 * n)) + 1e-9)
    f = np.fft.rfftfreq(4 * n, 1 / SR)
    band = (f > 200) & (f < 2000)
    peaks, _ = ss.find_peaks(X * band, distance=20)
    top = sorted(sorted(peaks, key=lambda i: -X[i])[:8])

    def level(x):
        i = int(round(x / f[1]))
        return X[i - 4:i + 5].max()

    def score(f0):
        # on the harmonics minus halfway between them: an octave too high or too low loses half its partials
        hs = np.arange(np.ceil(200 / f0), np.floor(2000 / f0) + 1)
        return np.mean([level(h * f0) - level((h + 0.5) * f0) for h in hs])
    f0s = np.arange(55, 150, 0.1)
    best = f0s[int(np.argmax([score(x) for x in f0s]))]
    return best, [(f[i], f[i] / best) for i in top]


def main():
    path, t0, t1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    x, sr = sf.read(path, always_2d=True)
    assert sr == SR
    prev = None
    for s, db, lr in attacks(x, t0, t1):
        gap = f"{(s - prev) / SR * 1000:6.1f}" if prev is not None else "     -"
        print(f"attack {s / SR:8.4f} s  gap {gap} ms  peak {db:6.1f} dB  L-R {lr:+5.1f} dB")
        prev = s
    for t in np.arange(t0, t1, 0.2):
        f0, top = series(x.mean(1), t)
        print(f"{t:6.2f} s: partials on {f0:.1f} Hz ({note(f0)}, period {1000 / f0:.2f} ms): "
              + " ".join(f"{fr:.0f} (h{h:.2f})" for fr, h in top))


if __name__ == "__main__":
    main()
