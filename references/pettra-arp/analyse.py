#!/usr/bin/env python3
"""The Pettra arp measured from the finished mix, with the methods that held up (see METHODS.md).

1. HPSS (librosa.decompose.hpss, median filters across time and frequency): the drums go to the percussive part,
   the plucks stay in the harmonic part.
2. SuperFlux onsets on the harmonic part, 600 Hz - 8 kHz (librosa.onset.onset_strength with lag 2 and a maximum
   filter of 3 bands, Boeck & Widmer 2013), onsets within 20 ms of a kick attack dropped (the kick leaks in).
3. The gaps between plucks on a log axis, and a Rayleigh test for a geometric ladder: for a factor q, every gap x is
   put on a circle at the angle 2 pi log(x) / log(q); if the gaps sit on rungs g, gq, gq^2 ... the angles bunch.
   A metric grid (1/32, 1/16, 1/8) bunches at q = 2, a ping-pong ball between closing plates at q = 1/r.
4. What rises at each pluck: the CQT 10-40 ms after it minus 1.2 x the 10-30 ms before it, folded to pitch classes.
5. Where each pluck lies between left and right: the rise of its attack (1.5-8 kHz, 2-25 ms after it against the
   20-2 ms before) in each channel's harmonic part; plucks without a rise are left out.

Usage: analyse.py [folder with the two MP3 excerpts]   (needs numpy, scipy, librosa, soundfile)"""
import os
import sys
from collections import Counter

import librosa
import numpy as np
import scipy.signal as ss
import soundfile as sf

SR = 44100
BEAT = 60 / 137.8125       # the song's tempo
KICK = 0.250               # the first kick attack in both excerpts (s), measured on the spectrogram
HOP = 128
PC = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FILES = ("Pettra_arp_0m48-1m16.mp3", "Pettra_arp_6m28-6m56.mp3")


def harmonic_part(y):
    D = librosa.stft(y, n_fft=2048, hop_length=HOP)
    H, _ = librosa.decompose.hpss(D, kernel_size=(31, 31), margin=(1.0, 2.0))
    return librosa.istft(H, hop_length=HOP, length=len(y))


def plucks(h):
    S = np.abs(librosa.stft(h, n_fft=2048, hop_length=HOP))
    M = librosa.feature.melspectrogram(S=S ** 2, sr=SR, n_mels=138, fmin=600, fmax=8000)
    o = librosa.onset.onset_strength(S=librosa.power_to_db(M), sr=SR, hop_length=HOP, lag=2, max_size=3)
    f = librosa.onset.onset_detect(onset_envelope=o, sr=SR, hop_length=HOP, pre_max=6, post_max=6, pre_avg=40,
                                   post_avg=40, delta=np.percentile(o, 75) * 0.5, wait=6)
    t = librosa.frames_to_time(f, sr=SR, hop_length=HOP)
    phase = ((t - KICK) / BEAT) % 1
    return t[np.minimum(phase, 1 - phase) * BEAT >= 0.020]


def ladder(gaps):
    """The factor q whose log ladder the gaps bunch on most (mean resultant length R, Rayleigh p = exp(-n R^2))"""
    def resultant(q):
        z = np.exp(2j * np.pi * np.log(gaps) / np.log(q)).mean()
        return abs(z), np.angle(z)
    qs = np.arange(1.30, 2.21, 0.01)
    rs = [resultant(q)[0] for q in qs]
    q = qs[int(np.argmax(rs))]
    r, angle = resultant(q)
    g = np.exp(angle / (2 * np.pi) * np.log(q))
    while g > 40:
        g /= q
    while g < 25:
        g *= q
    return q, r, np.exp(-len(gaps) * r * r), [g * q ** k for k in range(5)], {x: resultant(x)[0] for x in (1.5, 2.0)}


def rising_classes(h, times):
    bpo = 36
    fmin = librosa.note_to_hz("G4")
    C = np.abs(librosa.cqt(h, sr=SR, hop_length=HOP, fmin=fmin, n_bins=bpo * 2 + 18, bins_per_octave=bpo,
                           filter_scale=0.8))
    midi = librosa.hz_to_midi(fmin * 2 ** (np.arange(C.shape[0]) / bpo))
    sets = []
    for t in times:
        f = int(t * SR / HOP)
        rise = np.maximum(C[:, f + 3:f + 14].mean(1) - 1.2 * C[:, max(0, f - 11):f - 2].mean(1), 0)
        pc = np.zeros(12)
        for m, v in zip(midi, rise):
            if abs(m - round(m)) < 0.34:
                pc[int(round(m)) % 12] += v
        if pc.max() > 0:
            sets.append(" ".join(PC[i] for i in range(12) if pc[i] >= 0.5 * pc.max()))
    return sets


def stereo(hl, hr, times):
    """Left minus right of each pluck's rise in dB (clipped to +-20), for the plucks whose attack rises at all"""
    sos = ss.butter(4, [1500, 8000], "band", fs=SR, output="sos")
    bl, br = ss.sosfiltfilt(sos, hl), ss.sosfiltfilt(sos, hr)
    out = []
    for t in times:
        i = int(t * SR)
        after, before = slice(i + 88, i + 1100), slice(max(0, i - 880), i - 88)
        rise_l = (bl[after] ** 2).mean() - (bl[before] ** 2).mean()
        rise_r = (br[after] ** 2).mean() - (br[before] ** 2).mean()
        if rise_l > 0 or rise_r > 0:
            out.append(np.clip(10 * np.log10(max(rise_l, 1e-12) / max(rise_r, 1e-12)), -20, 20))
    return np.array(out)


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    gaps, sets, sides, flips = [], [], [], []
    for name in FILES:
        y, sr = sf.read(os.path.join(folder, name), always_2d=True)
        assert sr == SR
        h = harmonic_part(y.mean(1).astype(np.float32))
        t = plucks(h)
        gaps += list(np.diff(t) * 1000)
        sets += rising_classes(h, t)
        side = stereo(harmonic_part(y[:, 0].astype(np.float32)), harmonic_part(y[:, 1].astype(np.float32)), t)
        sides += list(side)
        hard = side[np.abs(side) >= 12]
        flips += list(np.sign(hard[1:]) != np.sign(hard[:-1]))
        print(f"{name}: {len(t)} plucks")
    gaps = np.array(gaps)
    fast = gaps[(gaps > 25) & (gaps < 180)]
    q, r, p, rungs, at = ladder(fast)
    print(f"fast gaps (25-180 ms): {len(fast)}; best ladder q = {q:.2f} (each gap x{1 / q:.2f} of the one after it), "
          f"R = {r:.2f}, Rayleigh p = {p:.1e}; rungs " + ", ".join(f"{x:.0f}" for x in rungs) + " ms")
    print(f"  R at q = 1.5: {at[1.5]:.2f}, at q = 2 (a metric grid): {at[2.0]:.2f}")
    sides = np.array(sides)
    print(f"left and right: {len(sides)} plucks with a rising attack; {np.mean(np.abs(sides) >= 12) * 100:.0f} % hard "
          f"left or right (12 dB or more: {np.sum(sides >= 12)} left, {np.sum(sides <= -12)} right), "
          f"{np.mean(np.abs(sides) < 3) * 100:.0f} % within 3 dB of the centre; from one hard pluck to the next the "
          f"side changes {np.mean(flips) * 100:.0f} % of the time")
    count = Counter(len(s.split()) for s in sets)
    print("pitch classes rising together per pluck:", dict(sorted(count.items())))
    print("most common:", Counter(sets).most_common(6))


if __name__ == "__main__":
    main()
