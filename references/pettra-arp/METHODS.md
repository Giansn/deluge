# Analysing the Pettra arp with code: the methods and what they found (2026-09-28)

The question: in the finished mix of Pettra "You Are The Seeds", how does the bright synth arp move? The picture to test: a ping-pong ball between two plates that close in and part again. `analyse.py` runs the methods that held up; the others are described so they can be tried again.

## What held up

**1. Separating drums from notes: HPSS** (`librosa.decompose.hpss`, Fitzgerald 2010)
- A note is a horizontal line in the spectrogram, a drum hit a vertical one.
- The code runs a median filter along time (31 frames) and one along frequency (31 bins) over the magnitude spectrogram. Each bin goes to the part whose median is larger, as a soft mask (with a margin, only clear cases).
- Here the kick and hats go to the percussive part; the plucks stay in the harmonic part, pad and bass too.

**2. Onsets: SuperFlux** (`librosa.onset.onset_strength` with `lag=2, max_size=3`, Böck & Widmer 2013)
- Spectral flux sums the rise of every band from one frame to the next.
- SuperFlux compares with the frame 2 back, after a maximum filter over 3 neighbouring bands. A pitch that wobbles by a band no longer counts as an onset, only new energy does.
- On the harmonic part, 600 Hz – 8 kHz (above bass and most of the pad): 115 and 114 plucks in the two excerpts. Onsets within 20 ms of a kick attack are dropped, because the kick's click leaks into the harmonic part.

**3. The gaps on a log axis, and a test for a geometric ladder**
- A metric grid (1/32, 1/16, 1/8) gives gaps in the ratio 2. A ball between closing plates gives a geometric series: every gap r times the one before, rungs in the ratio 1/r.
- The test (circular statistics): put every gap x on a circle at the angle 2π·log(x)/log(q). If the gaps sit on rungs g, g·q, g·q², … their angles bunch. The mean resultant length R measures how much; the Rayleigh test gives p ≈ exp(−n·R²).
- **Result:** the 94 fast gaps (25–180 ms) bunch best at **q = 1.50**: rungs **34, 51, 77, 115, 173 ms**, R = 0.34, **p = 2·10⁻⁵**. At q = 2 (a metric grid) R = 0.10, p = 0.4.
- So the arp's gaps are a geometric series with **×0.67 per hit**, not a grid. That is the ping-pong model with the plates closing in at half the ball's speed (s = 0.5).

**4. Single notes or chords: what rises at each pluck**
- The CQT (36 bins per octave, G4 upwards) 10–40 ms after the pluck, minus 1.2 × the 10–30 ms before it, folded to the 12 pitch classes. Counted: the classes that reach half of the strongest.
- **Result:** mostly one or two classes (155 of 228). The most common: **A** (30), C# + A (16), B (11), A + B (8), E + A (8). C# and E are the 5th and 3rd harmonic of an A.
- So mostly **single notes, above all A**, with B, E, C# in between; full chords seldom. The ball mostly repeats one note, like a ratchet.

## What did not work here

**Basic Pitch** (Spotify, ICASSP 2022; ONNX model, runs with onnxruntime alone)
- The code (`basic_pitch/models.py`, `note_creation.py`):
  - A CQT with 3 bins per semitone.
  - "Harmonic stacking": the CQT shifted by the harmonics ½, 1 … 7 and stacked, so every harmonic of a note lines up on its fundamental.
  - A small CNN with three outputs per frame (11.6 ms): pitch contour, note active, onset.
  - Notes: local maxima of the onset map above a threshold start a note; it runs on while the note map stays above its threshold (11 frames of tolerance). Its loudness is the mean activation.
- On this mix the onset map reaches at most 0.40 in the arp's register (mostly broadband stripes at the kicks), the note map 0.14. It was trained on guitar, piano and voice; a dense trance mix is outside what it learned. No usable notes.

**Melodia** (Essentia `PredominantPitchMelodia`, Salamon & Gómez 2012)
- The code:
  - The salience function: every spectral peak at f votes for the fundamentals f/h, h = 1 … 20, with weight 0.8^(h−1), in 10-cent bins.
  - Contours: the salience peaks followed over time.
  - Voicing and melody selection: which contours belong to the melody.
- The voicing step rejected 90–100 % of the frames (it is built for voice and solo lines). The salience function alone (`PitchSalienceFunction`) shows the pitch bars and was useful to look at, but it doesn't separate the arp from the pad.

## Not possible in this container

Source separation with neural networks (Demucs, Open-Unmix, MDX) would separate the synths from the drums much better than HPSS, but it needs PyTorch or model weights from servers the container can't reach (download.pytorch.org, HuggingFace). On a computer with PyTorch, `demucs` on the excerpts and `analyse.py` on its "other" stem would be the next step.
