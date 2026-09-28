# "New Sitar Grii 10" in the emulator, 432 and 440 Hz, 27.09.2026

Firmware: the rebuilt v16 (`7eed1a77…`, ELF with symbols, see `2026-09-27-rebuild-v16.md`). Script `sitar_emu.py` here in the folder, on the environment of `tests/song` (development branch `1238201`). All numbers are in `2026-09-27-sitar-emulator.json`.

## Setup

- **Card:** the song as `SONGS/DEFAULT.XML`, the 136 samples from `card/SAMPLES`, the tuning via `CommunityFeatures.XML`. Checked after the boot: 432.0 and 440.0 Hz.
- **Replaced:** 3 missing samples of the kit Hihat (`PsyPack/hihat`, `hihatshort`, `hihatlong`). The CSV has no details on them. The length comes from `endSamplePos` in the song (8,923, 6,988, 27,340 samples). The format is the same as for the other 11 samples of the kit (stereo, 24 bit, 44.1 kHz). Content: decaying noise.
- **Clips:** In the saved song, Hihat, Guiro, 170 Sitar 2 and Oboe play. For the comparison with the device I also switched on 3L3Ctr0, 014 CR-78 and KIT1 ("all kits").
- **Measurement:** as `run.sh` (`--init-sounds --seed 1`, without culling, i.e. the full demand of the song). 1 bar of lead-in, then 4 bars at 140 BPM (302,400 samples). The firmware measures the render time per track itself, like the profiler on the device (`profiler::timingOutputs`, `outputTicks[]`, OS timer 0 = emulated time). The areas come from `profile_by_function`.

## Computing time per track while playing

Emulator: % CPU (400 MHz, 1 instruction per cycle), in brackets the share of the audio routine. Device: share of the time according to `2026-09-27-v16-profile-check.jsonl`. The 55% belong to this measurement: 12 s, load 96%, QL all the time, 173 voices cut.

| Track | 432 Hz, all kits | 440 Hz, all kits | 432 Hz, as saved | Device |
|---|---|---|---|---|
| S 170 Sitar 2 | 16.3 (43.9) | 16.4 (44.3) | 16.3 (50.3) | 6.5 |
| K Hihat | 3.5 (9.4) | 3.5 (9.5) | 3.5 (10.7) | 11.0 |
| K Guiro | 3.0 (8.1) | 3.0 (8.0) | 3.0 (9.3) | 10.8 |
| S Oboe | 2.8 (7.4) | 2.6 (7.0) | 2.8 (8.5) | 2.1 |
| K KIT1 | 2.2 (5.8) | 2.2 (5.9) | 0.1 (0.3) | 8.5 |
| K 3L3Ctr0 | 1.6 (4.4) | 1.6 (4.4) | 0.2 (0.5) | 15.0 |
| K 014 CR-78 | 1.2 (3.4) | 1.2 (3.2) | 0.1 (0.3) | 9.9 |
| A AUDIO1 | 0.4 (1.2) | 0.4 (1.2) | 0.4 (1.3) | 2.6 |
| K Rattle / Rattle V2 | 0.1 each | 0.1 each | 0.1 each | 5.6 / 5.1 |
| S Kbass, 2 × M | 0.0 | 0.0 | 0.0 | 1.4 / 0.1 / 0.1 |
| **The five kits** | **11.6 (31.1)** | **11.5 (31.1)** | 6.9 (21.2) | **55.2** |
| Total, mean (peak) | 37.2 (53.4) | 37.0 (51.2) | 32.5 (42.3) | audio routine 97% |

At most 20 voices, no cuts because of the load. 30 voices were replaced because single sounds reached their voice limit.

## 432 against 440 Hz

**No difference worth mentioning:** 37.2 against 37.0% CPU (+0.2). Almost all samples are resampled already at 440 Hz, because the notes don't lie on the samples' root note. At 440 Hz only 14,106 instructions per 128 samples are read natively. At 432 Hz that goes away, and the resampling (`readSamplesResampled`) rises from 125,521 to 128,467 instead. So the tuning costs practically nothing in this song.

## Why the kits cost much more on the device

In the emulator every call of the audio routine renders 128 samples. v16 calls the routine much more often and then renders smaller pieces, 4–8 samples when idle (finding from the emulator, patch 0053). While playing, the pieces grow with the load. The effort per call then comes up much more often: setting up effects, patcher, envelopes, filter configuration. As a test I repeated the same measurement (432 Hz, all kits) with smaller windows:

| Window | Total % CPU | Five kits % CPU (share of the routine) | Sitar | Rattle, Rattle V2 (without notes) |
|---|---|---|---|---|
| 128 | 37.2 | 11.6 (31.1) | 16.3 (43.9) | 0.1 each |
| 32 requested, 55 on average* | 43.0 | 14.3 (33.1) | 18.2 (42.2) | 0.2 each |
| 8 | **102.7** | **42.7 (41.6)** | 36.3 (35.3) | 1.5 / 1.6 |

\* `routine()` partly adjusts the window size itself (`sampleThreshold`).

- **The same song costs 2.8 times as much with windows of 8.** The voices grow most, i.e. patcher, envelopes and LFOs (3.9 → 21.8% CPU), song and master FX with the output (1.6 → 11.0), "memory / other" (1.0 → 10.2) and the sample reading (13.1 → 22.3).
- **The five kits' share rises from 31 to 42%.** The kit Hihat alone grows from 3.5 to 15.7% CPU.
- **The remaining gap to the 55% on the device:**
  - On the device v16 cuts voices at this load (173 in 12 s). According to the user it hits Sitar and Oboe. In the emulator without culling they stay, which lowers the kits' share there.
  - The emulator doesn't model caches and SDRAM waits.
  - Which clips ran on the device is not logged.
- **Conclusion:** Most of the kit costs on the device come from the effort per call with v16's small windows, not from rendering the notes. That is exactly what v17 changes (the routine runs less often). So the same song with v17 is worth a try on the device.

## Notes

- **Sitar is the most expensive track in the emulator:** 18 samples, stereo, 32-bit float. It costs the same at 432 and 440 Hz, so it is resampled at 440 Hz too.
- **Guiro:** The row `drum-hit-guiro-mid` has 8 voices all the time, which is its maximum.
- **Rebuilding:** `python3 sitar_emu.py <deluge.elf> ../card <out> --song-dir <dev>/mastertune-1.2.1/tests/song --build <folder with blockcount.so from run.sh> --tenths 4320 --all-kits [--window 8]`. A run takes about 25 s, with `--window 8` about 80 s.
