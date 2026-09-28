# Music analysis with code: tuning, root note, tempo, key, loudness, 28.09.2026

Why: groundwork for DJ features (v19 brings a DJ filter, README "Known limitations" of v18) and for the tools on the computer. The question: what can code find out about music today, how reliably, and where would it run for the Deluge.

Script `music_analysis.py` here in the folder. The material is the card's own samples (`device/card/SAMPLES`), so the ground truth is known:
- The 20 multisamples (13 double bass with vibrato, 7 oboe staccato) name their note and were recorded at 440 Hz. Each was also analysed as a copy at 432 Hz, resampled the way a sampler plays it.
- The loops and chord progressions are rendered by the script from the card's kick, clap, hi-hat, bass note, sitar, oboe and double bass, at known tempos and in known keys.

Libraries: librosa 1.0.0, Essentia 2.1-beta6-dev, madmom 0.17.dev0 (from git), mir_eval, pyloudnorm. beat_this, the newest beat tracker (ISMIR 2024), could not be tried: it needs PyTorch, and the cloud container can't reach the server of its CPU build.

## In short

- **Tuning:** 440 and 432 Hz are easy to tell apart on pitched samples.
  - YIN got all 40 files right (the 20 recordings and their 432 Hz copies), with a spread of 0.7 cents.
  - `librosa.estimate_tuning` got 39 of 40.
  - Essentia needs care. Its window runs from −35 to +65 cents, and 432 Hz (−31.8 cents) sits 3 cents from the edge, so some values wrap round to +53 or +56.
- **Root note:** pYIN finds all 20 notes. The two libraries name their files counting C3 = 60, one octave below the C4 = 60 convention, so the audio is more trustworthy than the octave in a name.
- **Tempo and beats:** On the 9 loops, madmom (neural network plus a tempo model) found every tempo up to a factor of 2 and placed the most beats right (F 0.88). It needs about 3 s of computing per 30 s of audio.
  - librosa and Essentia (signal processing only) each got 6 of 9 tempos right.
  - Their errors are the typical ones: ⅔, ¾ or ¼ of the tempo, and Essentia's beats between the kicks, on the hi-hats, even with the tempo right.
- **Key:** Tested on 24 keys with clean triads.
  - Krumhansl profiles: 24/24.
  - Essentia's default profile (bgate): 23/24.
  - The rest: 17 to 22 of 24.
  - The errors are related keys, mostly the relative key or a fourth away.
  - Real electronic music is harder: the best tools reach about 75 %.
- **Loudness:** The peak says little about loudness. Two files with the same peak are 3.1 LU apart, and the file with the highest peak is the quietest, 11 to 14 LU below the others. For a DJ's auto gain the right measure is loudness (EBU R128), not the peak.
- **For the Deluge:** tempo, beat and key analysis belongs on the computer.
  - The firmware already reads a root note and loop points from the WAV file, but no tempo.
  - Loops cut to 1, 2, 4, 8 … bars already play in sync in an audio clip.
  - The DJ filter itself needs no analysis.

## What the firmware has today

- **Root note:** `Sample::workOutMIDINote()` takes, in this order:
  - the length, for single-cycle waveforms;
  - otherwise the note from the file (the `smpl` or `inst` chunk);
  - otherwise its own FFT pitch detection (`Sample::determinePitch()`).

  In mastertune the detection counts relative to the recording's tuning (README, "Recordings are never tuned twice").
- **From WAV files** it reads the `smpl` chunk (MIDI unity note with fine tune, loop start and end) and the `inst` chunk (`storage/audio/audio_file.cpp`). It reads no tempo (no `acid` chunk).
- **Audio clips:** a loaded sample gets the power of two of beats nearest its length at the song's tempo (within a factor of √2 either way) and is time-stretched to it (`gui/ui/browser/sample_browser.cpp`, "load sample with time stretching"). So a loop cut to 1, 2, 4, 8 … bars plays in sync as long as its tempo is within √2 of the song's. A loop of 3 or 6 bars does not.
- **No tempo, beat, key or loudness analysis.**

## Tuning and root note

Cents from the nearest semitone at A = 440 Hz, median over the 20 multisamples (in brackets the interquartile range). 432 Hz is −31.8 cents.

| Method | As recorded (440) | Copy at 432 | 440 or 432 right |
|---|---|---|---|
| YIN, median f0 of the loud frames | +0.3 (0.7) | −31.4 (0.7) | 40/40 |
| `librosa.estimate_tuning` (spectral peaks) | +0.5 (5.1) | −31.5 (4.8) | 39/40 |
| Essentia `TuningFrequencyExtractor`, its last value | −0.5 (9.5) | −29.0 (32.5) | 40/40 |

- **librosa** misses at the two lowest double bass notes (F#0 and G0, 46 and 49 Hz). There it reads −12 and −32 cents at 440 Hz; G0 comes out as 432.
- **Essentia** accumulates its estimate over the frames, so its last value counts, not the median of the frames. Near its window's lower edge (−35 cents) values jump to the other side: +53 and +56 for two 432 Hz copies. They are still assigned right, because the comparison runs round the circle.
- **Limits:**
  - The estimate is known only up to a semitone: a sample 68 cents sharp reads like 432 Hz.
  - Drums and noise have no tuning.
  - A sample retuned to 432 Hz and one recorded at 432 Hz look the same.
- **Root note (pYIN):** 20 of 20, counting C3 = 60 as the libraries do. The analysis window matters: with 186 ms (4096 samples at 22.05 kHz) pYIN missed 5 of the 7 short oboe notes (0.6 to 1.0 s); with 93 ms it found all of them.

## Tempo and beats

30 s loops from the card's hits with 4 ms of human timing. Each cell gives the tempo found and the beat F-measure (a beat counts within ±70 ms). **Bold:** the tempo is wrong. *Italic:* half or double. In brackets: the median offset of the beats, where it is over 35 ms.

| Loop | librosa | Essentia | madmom | madmom with downbeats (beat F, downbeat F) |
|---|---|---|---|---|
| house 120 | 120.2, 1.00 | 120.0, 0.00 (+242 ms) | 120.0, 1.00 | 120.0, 1.00, 1.00 |
| house 128 | 129.2, 1.00 | 128.1, 0.00 (+226 ms) | 127.7, 1.00 | 127.7, 1.00, 1.00 |
| house 133 | 132.5, 1.00 | 133.0, 1.00 | 133.3, 1.00 | 133.3, 1.00, 1.00 |
| breakbeat 100 | **132.5**, 0.29 | **133.3**, 0.29 | *200.0*, 0.67 | *200.0*, 0.67, 0.69 |
| breakbeat 140 | 139.7, 0.99 | *69.9*, 0.00 (−120 ms) | 139.5, 1.00 | 139.5, 1.00, 1.00 |
| drum and bass 174 | **117.5**, 0.40 | **116.0**, 0.40 | 171.4, 1.00 | 176.5, 1.00, 1.00 |
| hip-hop 88 | 87.6, 0.95 | 88.0, 0.00 (−324 ms) | *176.5*, 0.67 | *176.5*, 0.67, 0.67 |
| swing 96 | 95.7, 0.00 (−87 ms) | 96.0, 0.00 (+298 ms) | *193.5*, 0.67 | *193.5*, 0.67, 0.00 |
| ambient 70 (kick and sitar) | **17.5**, 0.39 | 70.2, 0.97 | 69.8, 0.93 | 69.8, 0.96, 1.00 |
| **Tempo right, up to a factor 2 or 3** | 6/9, 6/9 | 6/9, 7/9 | 6/9, 9/9 | 6/9, 9/9 |
| **Beat F (downbeat F)** | 0.67 | 0.29 | 0.88 | 0.88 (0.82) |
| **Computing per 30 s** | 0.4 s | 0.5 s | 3.1 s | 3.6 s |

- **The metrical level:** at 88 to 100 BPM with 16th hi-hats, madmom counts double time. That is a valid reading, but not the one a DJ wants. DJ software folds tempos into a range the user sets, for that reason.
- **Wrong ratios:** librosa and Essentia land on ⅔ (174 → 117) and 4/3 (100 → 133). librosa's tempo model prefers about 120 BPM and read ¼ of the sparse ambient loop.
- **Off-beat:** Essentia locks onto the hi-hats between the kicks. The tempo is right, but every beat is 226 to 324 ms off. DJ software has "shift the grid by half a beat" for this.
- **Computing:** measured on one core of the cloud container, from Python, so a rough figure. Real music is harder than these loops (see "Published numbers").

## Key

24 keys: I–vi–IV–V in major and i–VI–iv–V in minor (with the major V), oboe triads over double bass roots, 16 s each. The score is MIREX's weighted one: 1 if right, 0.5 a fifth up, 0.3 relative, 0.2 parallel, otherwise 0.

| Method | Weighted | Right | Errors |
|---|---|---|---|
| librosa chroma (CQT) with the Krumhansl-Kessler profiles | 100 % | 24/24 | none |
| Essentia `KeyExtractor`, krumhansl | 100 % | 24/24 | none |
| Essentia, bgate (its default) | 95.8 % | 23/24 | G major → C major |
| Essentia, temperley | 92.9 % | 22/24 | A# minor → C# major, B minor → G major |
| Essentia, edma | 82.9 % | 19/24 | 3 relative (e.g. D# major → C minor), 2 a fourth up (G major → C major) |
| Essentia, shaath | 75.4 % | 17/24 | 3 relative, 2 a fourth up, 1 parallel (G# minor → Ab major), E minor → C major |

- **Related keys:** most errors are the relative key or a fourth. On the Camelot wheel DJs use, these are next door and mix well anyway. Two are a third apart (B minor → G major, E minor → C major), and one is the parallel key; those are not next door.
- **Register:** a first run with the chords and roots one octave lower (the file names' C3 = 60 read as C4 = 60) gave only 59 to 85 % for the same Essentia profiles; librosa stayed at 100 %. The result depends on register and voicing, which in real mixes a bass line and drums disturb.

## Loudness

EBU R128 integrated loudness (the measure behind ReplayGain 2 and DJ auto gain) and the peak:

| File | Loudness | Peak |
|---|---|---|
| `presets/demo/PETTRA PINGPONG.wav` | −12.7 LUFS | −3.1 dBFS |
| `presets/demo/PINGPONG ROLL.wav` | −15.8 LUFS | −3.1 dBFS |
| `SAMPLES/RESAMPLE/Smoking/output_000.wav` | −26.3 LUFS | −0.3 dBFS |

Same peak, 3.1 LU apart; nearly full-scale peak, 11 to 14 LU quieter. Matching levels by peak would leave these far apart.

## Published numbers on real music

From a web search. The container can't open arXiv or the ISMIR archives, so numbers marked * come from search summaries and weren't checked in the papers.

- **Tempo, best systems (Accuracy 1 / Accuracy 2):**
  - GiantSteps (electronic dance music) 90.2/97.6, Ballroom 95.1/98.7, GTZAN 78.9/91.3* ([ISMIR 2021](https://archives.ismir.net/ismir2021/paper/000085.pdf)).
  - Octave errors are the main failure. On GiantSteps, Böck 2015 had 58.9 vs 86.4* ([TISMIR](https://transactions.ismir.net/articles/10.5334/tismir.43)).
- **Beats:**
  - beat_this: GTZAN beat F 89.1, downbeat F 79.8* ([github.com/CPJKU/beat_this](https://github.com/CPJKU/beat_this)).
  - Online (real-time) trackers such as BeatNet and BEAST: about 75 to 80 beat F*.
- **Key, GiantSteps Key (604 clips of electronic dance music, weighted / right %):** Mixed In Key 8.3 75.7/69.4, a CNN 73.5/66.7, rekordbox 7.12 65.5/56.8 (third-party comparison, 2025, [github.com/a1ex90/MusicalKeyCNN](https://github.com/a1ex90/MusicalKeyCNN)).
- **Mixxx's analyzer** (open-source DJ software, from its source):
  - beats with qm-dsp (default) or SoundTouch;
  - key with qm-dsp (default) or libKeyFinder;
  - ReplayGain 2.0 with libebur128, target −18 LUFS.

## For DJ features on the Deluge

- **The DJ filter (v19)** needs no analysis. A classic DJ filter is one knob over a low-pass and a high-pass.
- **Where tempo, beat and key analysis belongs:** on the computer.
  - The Deluge's CPU (Cortex-A9, 400 MHz) already runs heavy songs at over 90 % (README, v18).
  - The neural trackers need 3 to 4 s per 30 s even on a PC core.
  - Light signal-processing methods (onset envelope and comb filter for tempo, chroma and a key profile for key, R128 for loudness) would be cheap enough in principle. On the device they would compete with the voices, and they make the errors shown above.
- **What works without new firmware:** a computer tool, like DelugeTuner, analyses the library:
  - beats with madmom or beat_this, with a tempo range to fold octave errors and a half-beat grid shift;
  - key with a Krumhansl-type profile, shown as Camelot;
  - loudness per R128.

  It hands the results over this way:
  - root note and loop points in `smpl`, which the firmware reads;
  - loops cut to 1, 2, 4, 8 … bars, which the firmware syncs by length;
  - tempo and key in the file name.
- **What needs firmware:** a tempo per sample (for example reading the `acid` chunk that DAWs write), a beat grid for whole tracks, or live analysis of the input.
- **Useful for mastertune now:** YIN tells 432 from 440 Hz to about 1 cent. A sample from another source that is already at 432 Hz has no `mtun` chunk, so it counts as 440 Hz material and would be lowered a second time. `retune_library.py` / DelugeTuner could warn about such files, or tag them with `mtun`.
