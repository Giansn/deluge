#!/usr/bin/env python3
"""Analysis by synthesis: the same onset tracker on the song's synth stem (from separate.py) and on renders from the
emulator, compared.

The tracker: spectral flux of a short STFT (512 samples, 11.6 ms, hop 64 = 1.45 ms) over 1.5-10 kHz, rises only,
against the frame 2 back, minus its running median; peaks at least 11 ms apart (above the 9 ms period of a 110 Hz
bass, whose waveform edges would otherwise count). A steady tone is a steady spectrum and gives no flux; only new
energy does. But the window is shorter than a lower bass's period, so its edges still count: the D2 and C#2 bass at
6:39 (13.7 and 14.5 ms) is what this tracker shows as a buzz on the song (METHODS.md, "Reading the samples")

For each file: the gaps between onsets (11-300 ms), their share in the buzz (12-18 ms), the geometric ladder they bunch
on (the Rayleigh test of analyse.py) and the Jensen-Shannon distance of the log-gap histogram to the song's.

Usage: compare.py <song stem.wav>[,<song stem 2.wav>...] <render.wav> ...   (needs numpy, scipy, librosa, soundfile)"""
import sys

import librosa
import numpy as np
import scipy.signal as ss
import soundfile as sf

SR, HOP, N_FFT = 44100, 64, 512


def onsets(y):
    S = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP))
    f = librosa.fft_frequencies(sr=SR, n_fft=N_FFT)
    L = np.log1p(1000 * S[(f > 1500) & (f < 10000)])
    flux = np.r_[0, 0, np.maximum(L[:, 2:] - L[:, :-2], 0).sum(0)]
    flux = np.maximum(flux - ss.medfilt(flux, 31), 0)
    peaks, _ = ss.find_peaks(flux, height=np.percentile(flux, 93), distance=int(0.011 * SR / HOP))
    return peaks * HOP / SR


def mono(path):
    y, sr = sf.read(path)
    assert sr == SR
    return y.mean(1) if y.ndim > 1 else y


def stats(gaps):
    g = gaps[(gaps >= 11) & (gaps < 300)]
    h, _ = np.histogram(np.log2(g), bins=np.arange(np.log2(11), np.log2(300), 1 / 6))
    fast = g[(g > 25) & (g < 180)]
    qs = np.arange(1.30, 2.21, 0.01)
    r = [abs(np.exp(2j * np.pi * np.log(fast) / np.log(q)).mean()) for q in qs]
    return h / h.sum(), np.mean((g >= 12) & (g <= 18)), qs[int(np.argmax(r))], max(r), len(g)


def jensen_shannon(p, q):
    m = (p + q) / 2
    kl = lambda a, b: np.sum(a[a > 0] * np.log2(a[a > 0] / b[a > 0]))  # noqa: E731
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def main():
    song = np.concatenate([np.diff(onsets(mono(p))) * 1000 for p in sys.argv[1].split(",")])
    hs, buzz, q, r, n = stats(song)
    print(f"song: {n} gaps, buzz (12-18 ms) {buzz:.0%}, ladder x{1 / q:.2f} (R {r:.2f})")
    for path in sys.argv[2:]:
        h, buzz, q, r, n = stats(np.diff(onsets(mono(path))) * 1000)
        print(f"{path}: {n} gaps, buzz {buzz:.0%}, ladder x{1 / q:.2f} (R {r:.2f}), "
              f"Jensen-Shannon distance to the song {jensen_shannon(hs, h):.3f}")


if __name__ == "__main__":
    main()
