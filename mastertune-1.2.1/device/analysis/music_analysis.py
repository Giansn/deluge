#!/usr/bin/env python3
"""Music analysis with code, tried on the card's own samples (device/card/SAMPLES): tuning, root note, tempo and
beats, key, loudness. The report: 2026-09-28-music-analysis.md here in the folder.

The ground truth comes from the material itself:
- Tuning and root note: the 20 multisamples (double bass, oboe) name their note (as C3 = 60 counts it), recorded at
  A = 440 Hz. Each also as a copy at 432 Hz, the way a sampler plays it (resampled, 440/432 longer).
- Tempo and beats: 30 s loops rendered here from the card's kick, clap and hi-hat (plus a bass note or the sitar) at
  known tempos, with 4 ms of human timing.
- Key: I-vi-IV-V (major) and i-VI-iv-V (minor, harmonic V) in all 24 keys, 16 s each, rendered from the oboe
  (chords) and the double bass (roots) as a sampler plays them.

The methods: YIN, pYIN, librosa.estimate_tuning and Essentia's TuningFrequencyExtractor for the tuning;
librosa's beat tracker (dynamic programming), Essentia's RhythmExtractor2013 (multifeature) and madmom's
RNN + DBN (beats, and beats with downbeats) for the tempo; Essentia's KeyExtractor with its key profiles and
librosa's chroma with the Krumhansl-Kessler profiles for the key; EBU R128 (pyloudnorm) for the loudness.

Needs: pip install numpy soundfile soxr librosa essentia mir_eval pyloudnorm Cython
       pip install --no-build-isolation "madmom @ git+https://github.com/CPJKU/madmom.git"   (optional)
Usage: music_analysis.py [tuning] [tempo] [key] [loudness]   (all four without arguments)
"""
import glob
import os
import re
import sys
import time

import essentia
import essentia.standard as es
import librosa
import mir_eval
import numpy as np
import pyloudnorm
import soundfile as sf
import soxr

try:
    import madmom
    from madmom.features.beats import DBNBeatTrackingProcessor, RNNBeatProcessor
    from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor
except ImportError:
    madmom = None

essentia.log.warningActive = False
essentia.log.infoActive = False
HERE = os.path.dirname(os.path.abspath(__file__))
CARD = os.path.join(HERE, "..", "card", "SAMPLES")
SR = 44100
PC = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
NAMES = list(PC)
TO_432 = 1200 * np.log2(432 / 440)  # -31.8 cents
rng = np.random.default_rng(1)


def load(path):
    """Mono, 44.1 kHz."""
    y, sr = sf.read(path, dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    return soxr.resample(y, sr, SR) if sr != SR else y


def named_note(path):
    """The MIDI note in the file name, as the two libraries count it: C3 = 60 (one octave above C4 = 60)."""
    m = re.search(r"_([A-G]#?)(-?\d)_", os.path.basename(path))
    return 12 * (int(m.group(2)) + 2) + PC[m.group(1)] if m else None


def multisamples():
    return sorted(glob.glob(f"{CARD}/MultiSample/**/*.wav", recursive=True))


# ---------------------------------------------------------------- Tuning and root note
def cents(f):
    """Cents from the nearest semitone at A = 440 Hz, -50 to 50: an octave or a fifth off hardly changes it."""
    return (1200 * np.log2(f / 440.0) + 50) % 100 - 50


def apart(a, b):
    """The distance of two such values, around the circle of 100 cents."""
    d = abs(a - b) % 100
    return min(d, 100 - d)


def tuning_part():
    rows, notes = [], []
    for path in multisamples():
        y = load(path)[: 3 * SR]
        for label, sig in (("440", y), ("432", soxr.resample(y, 432, 440))):
            s22 = soxr.resample(sig, SR, 22050)
            rms = librosa.feature.rms(y=s22, frame_length=4096, hop_length=1024)[0]
            f0 = librosa.yin(s22, fmin=27.0, fmax=1500.0, sr=22050, frame_length=4096, hop_length=1024)
            n = min(len(rms), len(f0))
            yin = cents(np.median(f0[:n][rms[:n] > 0.1 * rms.max()]))  # the loud frames only
            est = 100 * librosa.estimate_tuning(y=s22, sr=22050, resolution=0.005)
            hz = es.TuningFrequencyExtractor()(sig)
            ess = 1200 * np.log2(hz[-1] / 440.0)  # its estimate over the whole file is the last one
            rows.append((os.path.basename(path), label, yin, est, ess))
            if label == "440":  # the root note: pYIN (in 10-cent steps, enough for the note)
                p, v, _ = librosa.pyin(s22, fmin=27.0, fmax=1500.0, sr=22050, frame_length=2048)
                f = p[v & ~np.isnan(p)]
                notes.append(int(round(librosa.hz_to_midi(np.median(f)))) - named_note(path) if len(f) > 3 else None)
    print(f"\nTUNING: cents from the nearest semitone at A = 440 Hz (432 Hz: {TO_432:.1f}), {len(notes)} multisamples")
    for label, truth in (("440", 0.0), ("432", TO_432)):
        sel = [r for r in rows if r[1] == label]
        for i, name in ((2, "YIN"), (3, "librosa"), (4, "Essentia")):
            v = np.array([r[i] for r in sel])
            right = np.mean([apart(x, truth) < apart(x, TO_432 - truth) for x in v])
            iqr = np.subtract(*np.percentile(v, [75, 25]))
            print(f"   {label} {name:8s}: median {np.median(v):6.1f}, IQR {iqr:5.1f}, 440 or 432 right {right:5.0%}")
    print("   more than 12 cents off:", ", ".join(f"{r[0][:24]} ({r[1]}, {name} {r[i]:.0f})" for r in rows
          for i, name in ((2, "YIN"), (3, "librosa"), (4, "Essentia"))
          if apart(r[i], 0.0 if r[1] == "440" else TO_432) > 12) or "none")
    print(f"ROOT NOTE (pYIN) minus the note in the file name, in semitones: {notes}")


# ---------------------------------------------------------------- Tempo and beats
def hit(path, seconds):
    y = load(path)[: int(seconds * SR)]
    return y * np.minimum(1, np.linspace(40, 0, len(y))) / (np.max(np.abs(y)) + 1e-9)


# A bar in 16ths: kick, clap, hi-hat, and the swing (the share of a 16th the odd 16ths come late)
PATTERNS = {
    "house": ([0, 4, 8, 12], [4, 12], [2, 6, 10, 14], 0.0),
    "break": ([0, 7, 10], [4, 12], list(range(16)), 0.0),
    "dnb": ([0, 10], [4, 12], list(range(0, 16, 2)), 0.0),
    "hiphop": ([0, 7, 8], [4, 12], list(range(0, 16, 2)), 0.0),
    "swing": ([0, 10], [4, 12], list(range(16)), 0.33),
    "ambient": ([0], [], [], 0.0),
}
CASES = [("house", 120), ("house", 128), ("house", 133), ("break", 100), ("break", 140), ("dnb", 174),
         ("hiphop", 88), ("swing", 96), ("ambient", 70)]


def render(style, bpm, sounds, seconds=30.0, t0=0.25):
    """The loop, its beats and its downbeats (the ground truth)."""
    kick, clap, hat, swing = PATTERNS[style]
    y = np.zeros(int((seconds + 1) * SR), np.float32)
    sixteenth = 60.0 / bpm / 4
    beats = np.arange(t0, seconds, 4 * sixteenth)

    def put(sig, t, gain):
        i = int((t + rng.normal(0, 0.004)) * SR)
        if 0 <= i < len(y) - len(sig):
            y[i:i + len(sig)] += gain * sig

    bar = 0
    while t0 + bar * 16 * sixteenth < seconds:
        for s in range(16):
            t = t0 + (bar * 16 + s) * sixteenth + (swing * sixteenth if s % 2 else 0)
            if s in kick:
                put(sounds["kick"], t, 0.9)
            if s in clap:
                put(sounds["clap"], t, 0.6)
            if s in hat:
                put(sounds["hat"], t, 0.25 * rng.uniform(0.6, 1.0))
        if style in ("house", "ambient"):  # something tonal each bar: a bass note, or the sitar
            tone = sounds["bass" if style == "house" else "sitar"]
            put(tone[: int(16 * sixteenth * SR)] * 0.5, t0 + bar * 16 * sixteenth, 1.0)
        bar += 1
    y = y[: int(seconds * SR)]
    return y / np.max(np.abs(y)) * 0.8, beats, beats[::4]


def trackers(y):
    """(name, tempo, beats, downbeats or None, seconds of computing)."""
    out = []
    t = time.time()
    tempo, beats = librosa.beat.beat_track(y=y, sr=SR, units="time")
    out.append(("librosa", float(np.atleast_1d(tempo)[0]), beats, None, time.time() - t))
    t = time.time()
    bpm, beats, _, _, _ = es.RhythmExtractor2013(method="multifeature")(y)
    out.append(("Essentia", bpm, np.array(beats), None, time.time() - t))
    if madmom:
        sig = madmom.audio.signal.Signal(y, sample_rate=SR, num_channels=1)
        t = time.time()
        beats = DBNBeatTrackingProcessor(fps=100)(RNNBeatProcessor()(sig))
        out.append(("madmom", 60 / np.median(np.diff(beats)), beats, None, time.time() - t))
        t = time.time()
        d = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)(RNNDownBeatProcessor()(sig))
        out.append(("madmom+db", 60 / np.median(np.diff(d[:, 0])), d[:, 0], d[d[:, 1] == 1, 0], time.time() - t))
    return out


def tempo_part():
    sounds = {"kick": hit(f"{CARD}/Kick/DUME1_Kick01.wav", 0.4),
              "hat": hit(f"{CARD}/Hat/KOD_Render_Closed_Hat.wav", 0.12),
              "clap": hit(f"{CARD}/rattle/LP24_OrgPerc_Clap_10.wav", 0.3),
              "bass": load(f"{CARD}/DUB/RADJ_Syn_Bass_Note_F#min_2.wav"),
              "sitar": load(sorted(glob.glob(f"{CARD}/Artists/Michael Bulaw/Sitar/*.wav"))[0])}
    print("\nTEMPO AND BEATS: tempo found (! wrong, ~ right but half, double or triple), beat F-measure (+-70 ms),"
          " downbeat F-measure, median beat offset")
    total = {}
    for style, bpm in CASES:
        y, ref, ref_down = render(style, bpm, sounds)
        line = []
        for name, est, beats, down, sec in trackers(y):
            a1 = abs(est - bpm) <= 0.04 * bpm
            a2 = any(abs(est - bpm * k) <= 0.04 * bpm * k for k in (1, 2, 0.5, 3, 1 / 3))
            f = mir_eval.beat.f_measure(mir_eval.beat.trim_beats(ref), mir_eval.beat.trim_beats(np.asarray(beats)))
            late = np.median([(b - ref[np.argmin(abs(ref - b))]) * 1000 for b in beats if b > 5])
            s = total.setdefault(name, {"a1": 0, "a2": 0, "f": 0.0, "sec": 0.0, "down": []})
            s["a1"] += a1
            s["a2"] += a2
            s["f"] += f
            s["sec"] += sec
            text = f"{name} {est:5.1f}{'' if a1 else ('~' if a2 else '!')} F {f:.2f}"
            if down is not None:
                s["down"].append(mir_eval.beat.f_measure(mir_eval.beat.trim_beats(ref_down),
                                                         mir_eval.beat.trim_beats(down)))
                text += f" down {s['down'][-1]:.2f}"
            line.append(text + (f" ({late:+.0f} ms)" if abs(late) > 35 else ""))
        print(f"   {style:7s} {bpm:3d}: " + " | ".join(line))
    n = len(CASES)
    for name, s in total.items():
        down = f", downbeat F {np.mean(s['down']):.2f}" if s["down"] else ""
        print(f"   {name:9s}: tempo right {s['a1']}/{n}, up to a factor 2 or 3 {s['a2']}/{n}, beat F {s['f'] / n:.2f}"
              f"{down}, {s['sec'] / n:.1f} s of computing per 30 s")


# ---------------------------------------------------------------- Key
def sampler(paths):
    """A multisample as a sampler plays it: the nearest sampled note, its pitch moved by resampling."""
    have = {named_note(p): load(p) for p in paths}

    def play(note, seconds):
        src = min(have, key=lambda k: abs(k - note))
        y = soxr.resample(have[src], 2 ** ((note - src) / 12), 1) if note != src else have[src]
        y = y[: int(seconds * SR)]
        return y * np.minimum(1, np.linspace(30, 0, len(y)))
    return play


def key_part():
    oboe = sampler(glob.glob(f"{CARD}/MultiSample/Oboe*/**/*.wav", recursive=True))
    bass = sampler(glob.glob(f"{CARD}/MultiSample/Kontrabass/*.wav"))
    progression = {"major": [(0, "maj"), (9, "min"), (5, "maj"), (7, "maj")],
                   "minor": [(0, "min"), (8, "maj"), (5, "min"), (7, "maj")]}
    triad = {"maj": [0, 4, 7], "min": [0, 3, 7]}
    kk = {"major": np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]),
          "minor": np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])}
    profiles = ["bgate", "edma", "temperley", "krumhansl", "shaath"]
    score = {p: [] for p in profiles + ["librosa KK"]}
    bar = 2.0
    for tonic in range(12):
        for mode in ("major", "minor"):
            y = np.zeros(int(8 * bar * SR) + SR, np.float32)
            for i in range(8):
                root, quality = progression[mode][i % 4]
                start = int(i * bar * SR)
                b = bass(36 + (tonic + root) % 12, bar)
                y[start:start + len(b)] += 0.5 * b
                for beat in range(4):
                    for interval in triad[quality]:
                        note = oboe(60 + (tonic + root + interval) % 12, bar / 4)
                        s = start + int(beat * bar / 4 * SR)
                        y[s:s + len(note)] += 0.25 * note
            y = (y / np.max(np.abs(y)) * 0.8).astype(np.float32)
            ref = f"{NAMES[tonic]} {mode}"
            for p in profiles:
                key, scale, _ = es.KeyExtractor(profileType=p)(y)
                score[p].append((ref, f"{key} {scale}"))
            chroma = librosa.feature.chroma_cqt(y=soxr.resample(y, SR, 22050), sr=22050).mean(axis=1)
            best = max((np.corrcoef(chroma, np.roll(prof, t))[0, 1], f"{NAMES[t]} {m}")
                       for t in range(12) for m, prof in kk.items())
            score["librosa KK"].append((ref, best[1]))
    print("\nKEY: 24 keys, MIREX weighted score (1 right, 0.5 a fifth up, 0.3 relative, 0.2 parallel), the errors")
    for name, pairs in score.items():
        weighted = np.mean([mir_eval.key.weighted_score(r, e) for r, e in pairs])
        right = sum(mir_eval.key.weighted_score(r, e) == 1 for r, e in pairs)
        wrong = [f"{r} -> {e}" for r, e in pairs if mir_eval.key.weighted_score(r, e) < 1]
        print(f"   {name:10s}: {weighted:5.1%}, {right}/24 right; " + (", ".join(wrong) or "no errors"))


# ---------------------------------------------------------------- Loudness
def loudness_part():
    print("\nLOUDNESS: EBU R128 integrated (the measure behind ReplayGain 2 and a DJ's auto gain) and the peak")
    for path in (sorted(glob.glob(os.path.join(HERE, "..", "..", "presets", "demo", "*.wav")))
                 + [f"{CARD}/RESAMPLE/Smoking/output_000.wav"]):
        y, sr = sf.read(path, dtype="float64", always_2d=True)
        lufs = pyloudnorm.Meter(sr).integrated_loudness(y)
        name = os.path.basename(os.path.dirname(path)) + "/" + os.path.basename(path)
        print(f"   {name:28s} {lufs:6.1f} LUFS, peak {20 * np.log10(np.max(np.abs(y))):5.1f} dBFS")


if __name__ == "__main__":
    for part in sys.argv[1:] or ["tuning", "tempo", "key", "loudness"]:
        globals()[f"{part}_part"]()
