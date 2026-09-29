#!/usr/bin/env python3
"""A net of resonators that the audio runs through as a stream, and that notes each time a pluck's peak touches it.

The net: for every note of the arp's register (B4 to E6), a resonator on its fundamental and on its 2nd and 3rd
harmonic. Each is a gammatone filter, the model of a place on the cochlea: four complex one-pole stages in a row,
y[n] = (1 - r) x[n] + r e^(i w) y[n-1] with r = e^(-pi 60 / fs), 26 Hz wide together. It rings along with energy near
its frequency; 70 Hz away it lets through 32 dB less, 110 Hz away 46 dB less. That matters here: the song's bass is a
bright saw whose partials lie every 69 to 110 Hz up into the arp's register, and a one-pole resonator lets so many of
its neighbours through that it ticks with the bass's period. A note's reading is the sum of its three resonators'
energies.

A touch: the reading of a note rises 8 dB or more over its lowest value of the last 40 ms to a peak (the highest
within 6 ms either side), and at that moment it is the loudest note or within 6 dB of it (the broadband click of an
attack stirs every resonator a little; only the notes that sound count), no more than 20 dB below the loudest
reading of the last second and 40 dB below the loudest so far, and it falls 4 dB within 60 ms after the peak, as a
pluck does (a pad's chord, which also rises when the sidechain lets go, stays). Its time is where the amplitude climbed fastest between that low and the peak: that comes the same
delay after the onset for every pluck, whatever tail of the pluck before it the rise starts from, so the gaps between
touches are free of the resonators' own delay. Touches of
different notes within 8 ms are one pluck (the note with the strongest peak). A steady tone (the pad, a held bass
note, whose partials may fall into the net) raises the floor but touches nothing; only new energy does.

The audio runs in blocks of 1024 samples (23 ms) as it would arrive live, the filters keep their state from block to
block, and a touch is reported as soon as the 60 ms after its peak have arrived. --live plays it at the speed of the
music and prints the touches as they come.

Usage: net.py <wav> [from s] [to s] [--live]   (needs numpy, scipy, soundfile)"""
import sys
import time

import numpy as np
import scipy.signal as ss
import soundfile as sf
from scipy.ndimage import maximum_filter1d, minimum_filter1d

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTES = range(71, 89)          # B4 .. E6, MIDI numbers (A4 = 69)
HARMONICS = (1, 2, 3)
ORDER = 4                      # four one-pole stages in a row: a gammatone filter, as the ear's
BANDWIDTH = 60.0               # Hz per stage; 26 Hz for the four (-3 dB)
RISE_DB, FLOOR_MS, PEAK_MS, MERGE_MS, BLOCK = 8.0, 40, 6, 8, 1024
DOMINANT_DB = 6.0             # a touch counts only for a note within this of the loudest note at that moment
LOCAL_DB = 20.0               # ... and within this of the loudest reading of the last second
DECAY_DB, DECAY_MS = 4.0, 60  # a pluck falls this much within this after its peak (a pad chord stays)


def name(midi):
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


class Net:
    def __init__(self, sr):
        self.sr = sr
        self.freqs = np.array([440 * 2 ** ((n - 69) / 12) * h for n in NOTES for h in HARMONICS])
        r = np.exp(-np.pi * BANDWIDTH / sr)
        self.poles = r * np.exp(2j * np.pi * self.freqs / sr)
        self.gain = (1 - r) ** ORDER
        self.denominators = [np.poly([p] * ORDER) for p in self.poles]
        self.state = np.zeros((len(self.freqs), ORDER), complex)
        self.floor_n, self.peak_n = int(FLOOR_MS * sr / 1000), int(PEAK_MS * sr / 1000)
        self.decay_n = int(DECAY_MS * sr / 1000)
        self.history = np.zeros((len(NOTES), 0))   # the readings (dB) of the last floor + peak window
        self.amp = np.zeros((len(NOTES), 0))       # the same as amplitudes
        self.start = 0                             # the sample index of history[:, 0]
        self.reported = -1
        self.ref = None
        self.recent = []

    def feed(self, x):
        """One block of mono samples; returns the touches whose peaks are now confirmed: (time s, note, peak dB)"""
        energy = np.empty((len(self.freqs), len(x)))
        for k, a in enumerate(self.denominators):
            y, self.state[k] = ss.lfilter([self.gain], a, x, zi=self.state[k])
            energy[k] = np.abs(y) ** 2
        total = energy.reshape(len(NOTES), len(HARMONICS), -1).sum(1)
        reading = 10 * np.log10(total + 1e-20)
        self.history = np.concatenate([self.history, reading], 1)
        self.amp = np.concatenate([self.amp, np.sqrt(total)], 1)
        if self.ref is None or reading.max() > self.ref:
            self.ref = reading.max()
        # the loudest reading of the last second, block by block: a touch must come within LOCAL_DB of it
        self.recent = (self.recent + [reading.max()])[-int(self.sr / BLOCK + 1):]
        local = max(self.recent)
        touches = []
        h, f, p, d = self.history, self.floor_n, self.peak_n, self.decay_n
        # peaks we can decide on now: at least f samples of past and max(p, d) of future in the history
        lo = max(f, self.reported - self.start + 1)
        hi = h.shape[1] - max(p, d)
        if hi > lo:
            peak = maximum_filter1d(h, 2 * p + 1, axis=1)[:, lo:hi]
            floor = minimum_filter1d(h, f, axis=1, origin=(f - 1) // 2)[:, lo - 1:hi - 1]   # min of [i - f, i)
            after = minimum_filter1d(h, d + 1, axis=1, origin=-(d // 2))[:, lo:hi]        # min of [i, i + d]
            col = h[:, lo:hi]
            loudest = col.max(0)
            is_peak = ((col >= peak) & (col - floor >= RISE_DB) & (col > self.ref - 40)
                       & (col >= loudest - DOMINANT_DB) & (col > local - LOCAL_DB) & (after <= col - DECAY_DB))
            for n, k in zip(*np.nonzero(is_peak)):
                i = lo + k
                # the rise from the lowest point before the peak: where the amplitude climbs fastest (the same delay
                # after the onset for every pluck, whatever tail of the one before it the rise starts from)
                low = i - f + int(np.argmin(h[n, i - f:i]))
                slope = np.diff(self.amp[n, low:i + 1])
                j = int(np.argmax(slope))
                frac = 0.0
                if 0 < j < len(slope) - 1:   # a parabola through the steepest three
                    a, b, c = slope[j - 1:j + 2]
                    frac = 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c != 0 else 0.0
                # the note: the loudest reading at the peak (a note sharing a harmonic with it may be the one that
                # rose clearest)
                touches.append(((self.start + low + j + 0.5 + frac) / self.sr, NOTES[int(np.argmax(h[:, i]))],
                                h[:, i].max()))
            self.reported = self.start + hi - 1
        # keep only what the next block needs
        keep = f + max(p, d) + p + 1
        if h.shape[1] > keep:
            self.start += h.shape[1] - keep
            self.history = h[:, -keep:]
            self.amp = self.amp[:, -keep:]
        return touches


def merge(touches, merge_ms=MERGE_MS):
    """Touches of different notes within merge_ms: one pluck, the note with the strongest peak"""
    out = []
    for t, note, db in sorted(touches):
        if out and t - out[-1][0] < merge_ms / 1000:
            if db > out[-1][2]:
                out[-1] = (out[-1][0], note, db)
            continue
        out.append((t, note, db))
    return out


def run(path, t0=0.0, t1=None, live=False, echo=None):
    x, sr = sf.read(path, always_2d=True)
    x = x.mean(1)[int(t0 * sr):int(t1 * sr) if t1 else None]
    net = Net(sr)
    touches = []
    wall = time.time()
    for s in range(0, len(x), BLOCK):
        new = net.feed(x[s:s + BLOCK])
        new = [(t + t0, n, db) for t, n, db in new]
        touches += new
        if live:
            for t, n, db in new:
                print(f"  touch {t:8.4f} s  {name(n):4s} {db - net.ref:6.1f} dB", flush=True)
            ahead = (s + BLOCK) / sr - (time.time() - wall)
            if ahead > 0:
                time.sleep(ahead)
    return merge(touches)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    live = "--live" in sys.argv
    path = args[0]
    t0 = float(args[1]) if len(args) > 1 else 0.0
    t1 = float(args[2]) if len(args) > 2 else None
    hits = run(path, t0, t1, live)
    prev = None
    for t, n, db in hits:
        gap = f"{(t - prev) * 1000:6.1f}" if prev is not None else "     -"
        print(f"{t:8.4f} s  gap {gap} ms  {name(n):4s}")
        prev = t


if __name__ == "__main__":
    main()
