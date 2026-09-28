# Profiler measurement version (v15-prof)

**v15 with a profiler**, otherwise unchanged. It shows on the device what the Deluge spends its computing time on: per track, per task and per function, with the real caches, which the emulator doesn't know. Meant for the questions "87% CPU although nothing plays" and "why are voices cut".

| File | What |
|---|---|
| `deluge-1.2.1-mastertune-v15-prof-d8ff0108.bin` | v15 + profiler |
| `deluge-1.2.1-mastertune-v15-l2i-prof-76fd3515.bin` | v15 with L2 cache for code (as `l2test/`) + profiler |
| `*.symbols.json` | the function names of exactly this firmware, one per file |

SHA-256: v15-prof `76e608b3…5b3ef1e2`, v15-l2i-prof `65141b9e…4c854404`. Source: v15 plus `prof/patches/0001-…` and `0002-…`, for l2i also the patches from `l2test/`.

## Measuring

1. Flash one of the two firmware files, like any update.
2. Connect the Deluge to the computer by USB.
3. Open `tools/profiler.html` in **Chrome or Edge** (nothing to install), click **Connect**, then **Load symbols** and choose the `.symbols.json` that belongs to the flashed firmware.
4. On the Deluge: **Settings → CPU monitor → Profile**. Top left shows the usual line, and it sends the measurements to the computer.
5. Load the big song.
   - **Play nothing** for about 30 s.
   - Then **play** for about 30 s, ideally the passage where QL or VC appears.
   - With **Reset** between the two parts you get separate numbers.
6. Read the tables, or **Save recording** and send me the `.jsonl` file, and I'll evaluate it.

With Claude Code on the computer the Deluge is connected to, it also works without a browser:

```sh
pip install mido python-rtmidi
python3 tools/deluge_profiler.py record -s 60 -o profile.jsonl
python3 tools/deluge_profiler.py report profile.jsonl --symbols prof/deluge-1.2.1-mastertune-v15-prof-d8ff0108.symbols.json
```

## What the numbers show

- **By track:** the computing time of every track, measured exactly on the device (a timer around every track), as a share of the time. So you see which track costs how much, even when it is silent.
- **By task:** audio routine, loading from the card, display, scheduler and so on.
- **By function:** 1000 times a second the Deluge looks where in the code it is.
  - While a track computes, the interrupts are disabled. That time shows up collected as `Song::renderAudio`; which track it was is under "By track".
  - Which functions work inside a track shows the profile in the emulator (`tests/song`, `tests/sdload`).
- **Audio routine:** its share of the time. When idle it fills the free time with very small blocks (see below): busy waiting, not overload.

## What the emulator already shows (and the measurement on the device should confirm)

- **The 87% when idle is not overload.**
  - A calculation error in the task scheduling (`16 / 44100` as an integer gives 0) makes the audio routine run again about every 12 µs, with 4–8 samples each time.
  - Every pass goes through all tracks. Silent kits and audio tracks set up their whole effect chain before they notice that nothing sounds. That costs about 550 instructions per track, every 12 µs.
  - In the emulator that is 90% busy waiting at only about 12% real load.
  - On the device there's more: every pass reads about 99 KB of data, three times what fits into the L1 cache. That's why the L2 cache helps so clearly.
- **Voices cut needlessly (VC) while samples stream.**
  - When the Deluge waits for the card, it computes the audio inside the loading task.
  - The culling then judges the load by the mean duration of the loading task, including the wait for the card, and cuts voices although the audio buffer is hardly behind.
  - In the emulator with a slow card: 17 such cuts, the buffer at most 6 samples behind.
- I fix both in the next version. With this measurement version you can compare before and after on the device.

## Checked

- **Build:** without new warnings. Two complete rebuilds each give the same SHA-256.
- **PC** (`tests/profiler/run.sh`):
  - The firmware's ring buffer and encoding: 1175 checks.
  - The decoders of the browser page and of the Python script read the same messages exactly: 47 and 48 checks.
- **Emulator** (`tests/profiler/profiler_emu.py`, the real firmware, the timer interrupt as the CPU takes it): 21 checks, all passed with both delivered builds.
  - No message lost. The samples' weights add up to the time (4365 of 4400 ms, the rest still in the buffer).
  - The audio routine's share agrees with the CPU monitor: idle 60.4 to 59.8%, playing 97.4 to 99.2%.
  - Reverb, drone and compressor agree with the counted instructions.
  - The track times agree with the samples: while playing 89.9 to 87.3%.
- **Not tested on the device.**
  - The emulator only models the timer interrupt (OS timer 1, unused so far).
  - If no data comes, or the Deluge crashes when switching Profile on, please go back to v15 and tell me.
  - As long as Profile is off, the profiler doesn't run.

## Cost

With Profile on: about 0.04% CPU for the samples, two time measurements per track and audio block, about 8 KB/s over USB MIDI (port 3). The buffer always leaves 1 KB free, so notes and clock don't have to wait.
