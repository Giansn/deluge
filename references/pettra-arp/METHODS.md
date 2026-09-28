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

## Separating the synths with a neural network: MDX-Net (`separate.py`)

- **The model:** MDX-Net from KUIELab (Music Demixing Challenge 2021), its "other" model as the UVR project ships it (`kuielab_a_other.onnx`, 30 MB, on GitHub). A U-Net takes the complex stereo spectrogram as 4 channels (left and right, real and imaginary: [1, 4, 2048 bins, 512 frames]) and returns the target's spectrogram directly.
- **The code around it** follows KUIELab's `ConvTDFNet.stft()/istft()` and `demix()`: n_fft 8192, hop 1024, the first 2048 bins (up to 11 kHz), chunks of 512 frames with n_fft/2 of trim on both sides, the output times 1.035. It runs with onnxruntime on the CPU, no PyTorch: 16 s for both excerpts.
- **Result:** a synth stem without kick and hats, about 20 dB below the mix (the kick and bass carry most of the energy). The bass's fundamental goes to the bass stem, but its partials above ~200 Hz stay (see "Reading the samples").

**On the stem** the plucks and their figures show clearly, which the kick hid in the mix. What looked like fast runs on it, though, is mostly the bass (next section): a tracker on the stem counted gaps of 12–18 ms (22 % of all) and, at 6:39.57, a "buzz at 15 ms" of about 17 hits.

## Reading the samples (`samples.py`): the fast hits are the bass

Instead of a spectrogram, the samples themselves: a 3 kHz high-pass, the peak per 0.5 ms, and an attack wherever it rises 8 dB over the 3 ms before.
- **What it finds:** at 6:39.4–6:39.7 hits every **13.7 ms** before the beat, with a second train that drifts from 8.4 to 7.7 ms behind the first; from the beat on every **7.2 ms** for 100 ms, then 7.7, 8.5, 10.8 ms. It looks like a buzz of the arp.
- **The partials say otherwise:** at 200–2000 Hz the stem's strongest peaks sit on one harmonic series (h3 … h19, within 1–2 %): **73.1 Hz (D2)** before the beat, **69.1 Hz (C#2)** from the beat on. In the early excerpt (0:56) it is **109.3 Hz (A2)**, where the hits come every 9.1 ms.
- **The periods match:** D2 13.7 ms, C#2 14.5 ms (7.2 ms: two edges per period), A2 9.1 ms. The drifting second train is a second, detuned oscillator. So these "hits" are the waveform edges of a bright, detuned saw bass. MDX-Net moves its fundamental to the bass stem, but its partials above ~200 Hz stay in "other", and every edge rises like an attack.
- **The trackers count them too:** the one in `compare.py` (an 11.6 ms STFT, peaks at least 11 ms apart) is shorter than these periods. Its "buzz at 15 ms" at 6:39.57 and the song's 22 % below are the bass.
- **Removing the edges** doesn't work well enough:
  - An RMS over 2.3 ms with a morphological opening over 3.5 ms (min filter, then max filter) keeps a pluck that rings for tens of ms and drops a lone spike. On a render it finds the known gaps; on the song, where the bass is bright, its edges survive (trains of 9 and 13.5–16 ms again).
  - Separating by pitch needs a window of several bass periods (30–45 ms), and that merges gaps shorter than about 20–25 ms.
- **So:** in this mix the arp's gaps under about 25 ms can't be measured with these methods. What holds:
  - the ladder of ×0.67 for 25–180 ms (method 3, whose 46 ms window sees the bass as a steady spectrum);
  - the figures in `ANALYSIS.md`.
- The buzz of PETTRA BALL (15 ms) is therefore set by ear. So is its shape: the hits at the start and the end as clear as the buzz, and an end slower than the start.

## Analysis by synthesis (`compare.py`)

The same tracker on the stem and on renders of the real firmware in the emulator. It was meant to compare the share of gaps in a buzz (12–18 ms):
- the song 22 %;
- `PETTRA ARP.wav` 9 %, `PETTRA PINGPONG BALL B.wav` 8 %, the first `PETTRA BALL.wav` 29 %.

The song's share is its bass, though (see above), and the renders have no bass, so the numbers say nothing about the arp. The histogram distance (Jensen–Shannon) is no good measure either: a render sits on exact rungs, the song spreads.

## Other methods (web research, 2026-09-28)

- **Separation:** Demucs v4 (waveform and spectrogram U-Nets joined by a transformer) is the best, but its weights are on HuggingFace, which this container can't reach; `demucs-onnx` on PyPI needs only onnxruntime once the weights are copied in by hand. Open-Unmix needs PyTorch (weights on zenodo). Classical: NMF with pitched templates (`libnmfd`), KAM.
- **Transcription:** Onsets and Frames (piano, TensorFlow; its rule "a note starts only at an onset" is worth reusing), MT3 (JAX), YourMT3 (PyTorch): not here.
- **Onsets:** madmom's CNN and RNN onset detectors (runs here; 100 frames per second blurs the shortest bounces), its complex-domain flux.
- **Pitch:** CREPE (monophonic; ONNX weights exist on GitHub as `onnxcrepe`), PESTO (PyTorch); both on a separated stem.
- **Also:** synctoolbox's pitch-onset features (onset and loudness per MIDI pitch), mid/side masks (kick and bass cancel in L−R).
