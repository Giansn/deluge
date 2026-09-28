# Test of `tools/retune_library.py` (27.09.2026, Windows)

State `18a6d0e`. Python 3.14 with numpy, scipy, soundfile, soxr 1.1.0, pylibrb 0.1.2, ffmpeg (imageio-ffmpeg). No sox. The emulator part (`retune_emu.py`) didn't run here; it needs the Linux toolchain.

## Tests that come with it

| Test | Result |
|---|---|
| `pc_test.py` | **all checks passed** (148 s). Read by libsndfile 16/16, scipy 16/16, ffmpeg 16/16, `wave` 15/16 (the float file, expected). |
| `paths_memory_test.py` | Umlauts ok. The memory part stops on Windows: `os.wait4` only exists on Unix (line 115). A bug of the test, not of the tool. Suggestion: on Windows use `psutil` or skip the measurement. |

## Real data: "New Sitar Grii 10" (`card/`, 136 samples)

`--card card --out … --tuning 432`: 136 converted, 3 missing (the `PsyPack` hi-hats, not in the backup either), 2 s.

- **Source folder unchanged** (checksums of all files before and after the same).
- **XML:** only numbers changed, otherwise byte for byte the same. 151 values: 7 × `startSamplePos`, 144 × `endSamplePos`.
- **Files:** all with `mtun` = 4320. The four files at 96 kHz are now 44.1 kHz; all others stay at 44.1 kHz. Formats stay: 18 × float, 62 × 16 bit, 52 × 24 bit, 4 × 32-bit PCM.
- **Length:** ×1.018425 to ×1.018671 (expected 55/54 = 1.018519, rounding with short files).
- **Pitch** (cross-correlation of the spectra, original against converted): double bass, oboe, sitar and DUB −31.78 cents each, expected −31.77.
- **Level** unchanged (oboe −38.97 dB, sitar −26.40 dB before and after).
- **Against an independent conversion** (scipy `resample_poly` 55/54, Kaiser 12): deviation −63 to −101 dB relative. For the oboe (16 bit, quiet) that is the rounding noise of the 16 bits (about −103 dBFS), for the sitar (float) the slightly different filter shape. Not audible.
- **Clipping:** In 15 of 136 files single peaks lie above 0 dBFS after the conversion and are clipped. Mostly 1–6 samples. Striking: `DUB/RADJ_Syn_Bass_Note_F#min_2.wav` 3670 samples at +0.01 dB (the original already sits at 0 dBFS), `Hat/LCD2_ClosedHH_60.wav` 4 samples at +1.36 dB. Suggestion: write such files as 32-bit float (the firmware reads it) or minimally quieter.
- A loop note: `DRUMS/Crash/CR78 Cymbal.wav`, loop of 1318 samples, period −0.53 cents after rounding.

## Dry run over the whole card (backup of 25.09., 7337 files)

`--dry-run --tuning 432`: 162 s (warm 48 s).

| | Count |
|---|---|
| converted | 5148 (23.0 → 22.5 GB) |
| XML with changed positions | 648, together 46,704 values |
| macOS companion files `._…` ("not a WAV or AIFF file") | 607, 0.1 MB, rightly so |
| missing (named in XML, not on the card) | 59: `PsyPack` 32, `Percussion` 9, `Nature SOunds` 5, `Fuego` 4, others |
| "unknown use" | 8, from the unreadable `KITS/041 Jonathan Snipes (Waterfalls).XML` ("`</sound>` closes `<modKnobs>`"): stays unchanged, its samples too |
| 64-bit float (the firmware doesn't read it) | 6 |
| empty 0-byte files ("too short") | 5 |
| wavetables / maybe wavetables | 2 / 4 |
| WAVE_FORMAT_EXTENSIBLE | 1 |

Short loops in `SAMPLES/RESAMPLE/Chrigu Jam*/output_*.wav`: 10 notes on the period after rounding.

**Memory:** peak **3.4 GB** already in the dry run (PC with 15.7 GB: works). It isn't the audio. The plan holds all 968 XML texts (295 million characters, as Python str with surrogateescape 2 bytes per character) and their parse trees at the same time. Suggestion: keep only the references and positions per XML and read the text again when writing.

## The user's task before converting his library

The user wants to convert his whole collection to 432 Hz for good, not just one song. The library is `deluge topics`. Before that he wishes:

1. **No clipped peaks:** A file whose conversion goes above 0 dBFS should be written as 32-bit float instead of clipped. The firmware reads 32-bit float. In the test this concerned 15 of 136 files, mostly 1–6 samples, at most +1.36 dB.
2. If it fits: less memory while planning (see above, 3.4 GB), and `paths_memory_test.py` on Windows too.

**Dry run over `deluge topics`** (6816 files, 149 s, nothing written):

| | Count |
|---|---|
| converted | 5608 files, 23.5 GB: 5467 resampled, 191 with the same length (Rubber Band), of them 51 `_ts` copies |
| XML with changed positions | 875 of 1171, together 52,660 values |
| missing | 31 |
| "unknown use" | 8, from `KITS/041 Jonathan Snipes (Waterfalls).XML` (unreadable, stays unchanged) |
| wavetables / maybe wavetables | 2 / 2 |
| WAVE_FORMAT_EXTENSIBLE | 1 |

No macOS companion files and no empty files. 10 loop notes in `SAMPLES/RESAMPLE/Chrigu Jam*/output_*.wav`. The 191 files with the same length would be a good case for the emulator: audio clips and samples with time-stretch after the conversion.

After the fix I'll do the conversion into a new folder. The user writes it to a second card.

## Converting `deluge topics`: the laptop froze

Run with the fixed version (`77e6d42`, `--tuning 432 --no-float`, default `--jobs` = 12 cores) from 21:24.

- Up to 5200 of 5658 jobs everything ran. Then the laptop froze (15.7 GB RAM); the user had to hard-restart it at 21:31.
- **Cause: the memory.** Still open were 464 files with 11.3 GB, among them 22 over 100 MB. The biggest: `PROJECTS/Fuego/Engeeinsle.WAV` 556 MB (about 35 min), `RESAMPLE/Simple Life 18/output_000.wav` 420 MB, `RESAMPLE/Elective Flow 24/jam nr2 elective flow.wav` 385 MB, `RESAMPLE/Idk Line Arranged 7/output_000.wav` 381 MB, more at 250–290 MB.
  - A job holds the whole file in memory, several times. Decoding 24 bit creates a uint8 array, an int32 array with 4 bytes per byte of the file, then float64. Then come the soxr output, round, clip and int64 when encoding, and the Rubber Band arrays when keeping the length.
  - That gives an estimated 5–8 GB for a file of 556 MB. 12 such jobs at the same time plus 3.4 GB of planning break any laptop.
- The target folder is incomplete (5202 files, 10.6 GB, no XML yet). It stays until the user decides.

**Please, before the next run:**
1. Convert big files piece by piece (soxr `ResampleStream`, Rubber Band block by block, encoding per block), so that a job needs at most a few hundred MB, no matter how long the file is.
2. Limit the simultaneous jobs by the free memory, not only by the cores.
3. If possible: be able to continue (`--resume`: skip finished files), so that an abort doesn't cost everything again.

Until then I leave the conversion resting. Then I'll start it with low process priority and a watchdog that stops at low free memory.

## For the device test

- A partial conversion must not go onto the card: samples that other songs use too would be converted there, but not their positions. So always convert the whole card and write it to a second card. The original stays untouched.
- Then measure "New Sitar Grii 10" at 432 Hz with the converted card (l2d, Profile) and compare with the measurement of the original.
