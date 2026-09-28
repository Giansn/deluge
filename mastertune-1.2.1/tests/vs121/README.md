# mastertune v18.3 against stock 1.2.1: CPU load

How much lighter mastertune v18.3-l2d is than the unchanged community firmware 1.2.1, measured the same way for both in the emulator. Measured on 28.09.2026.

## Builds

- **Stock 1.2.1:** tag `release_1_2_1` (`c23bc2fe`), built unchanged (`./dbt configure -DRELEASE_TYPE:STRING=stock-121`, then `ninja`).
- **mastertune v18.3-l2d:** `deluge-1.2.1-mastertune-v18.3-l2d-3581f019.bin`, the release build.
- Both at 440 Hz, both with `--init-sounds --seed 1`, the same songs and cards.

## Two measures

- **A. DSP work per window:** `tests/song/run.sh`, the real firmware renders the song in fixed windows of 128 samples. It counts the instructions per window. The device run adds the CPU protection (culling) as on the Deluge.
- **B. With the task manager:** `tasks_emu.py` (here), the firmware's own task manager runs, with the audio DMA in emulated real time. It measures the share of the time spent in the audio routine. This is where 1.2.1's scheduling bug shows: the audio task's interval is `16 / 44100` as an integer, i.e. 0, so the audio routine runs about every 12 µs on 4–12 samples. The CPU monitor can't be used for this: 1.2.1 has none, and mastertune v17 changed how it counts.

The emulator counts instructions; 1 instruction per cycle at 400 MHz unless an IPC is given. Lower IPC values (0.8, 0.7) only scale the time, as a rough stand-in for cache misses and SDRAM waits.

## Results

| Song | Measure | 1.2.1 | v18.3 | Change |
|---|---|---|---|---|
| Full-load song (17 tracks, `tests/song`) | instructions per 128 samples | 1,346,221 | 1,040,334 | **−22.7%** |
| same, device run | voices on average (voices cut in 4 bars) | 20.4 (380) | 32.0 (113) | +57% voices |
| 3-synth song (`tests/song`) | instructions per 128 samples | 700,575 | 529,128 | **−24.5%** |
| "New Sitar Grii 10" (a real song with many kits and effect chains, `device/card`) | instructions per 128 samples | 593,459 | 442,346 | **−25.5%** |
| same, task manager, IPC 1.0 | share of the time in the audio routine | 94.7% | 43.8% | −54% |
| same, IPC 0.8 | same | 97.1% | 54.8% | −44% |
| same, IPC 0.7 | same | 97.3% | 97.5% | ±0 (quality lowered in 100% → 32% of the renders) |
| 3-synth song, task manager, IPC 1.0 | same | 97.1% | 92.1–94.8% (2 runs) | −2 to −5% |

## What it means

- **About 20–25% less DSP work per sample** on the same songs, the same in all three songs.
- **At moderate load the audio routine's time can roughly halve** (the Sitar song: −44 to −54%), because the tiny blocks of 1.2.1 cost a fixed overhead per track. Near full load this disappears: both builds then use nearly all the time, and they differ only in how often the quality is lowered and voices are cut.
- **More voices before cutting:** in the full-load song v18.3 keeps 32 voices on average instead of 20.4. Part of this comes from the lighter DSP, part from the gentler culling taken over from the current community firmware in v4 (fewer voices, half of them faded instead of cut).
- **"Half the CPU load in heavy songs" is not right** as a general claim. It holds only at moderate load.

## Caveats

- **No memory or cache model.** The L2 cache (on in v18.3-l2d, never on in 1.2.1) is not captured at all. On the device it should add to the gain, but that is not measured here.
- **The IPC on the device is unknown.** For the Sitar song the tipping point lies between 0.7 and 0.8.
- **The audio is not bit-identical:** v18.3 changes filters and saturation. Voices and RMS match.
- **1.2.1's time share is elastic:** its blocks of about 10 samples fill any idle time, so its share says little at low load.
- **The minimum window of v17 is often off:** it only applies at cpuDireness 0. In the 3-synth song every chord change raises the direness to 14, and it takes about 1.75 s to come down, so the window is off about 90% of the time. A candidate for a later version.
- **Left out:** a streaming song (`tests/sdload`), an estimate with SDRAM rows, 432 Hz, UI and MIDI load. Runs were not repeated, except the 3-synth task-manager run (spread 92–95%).
