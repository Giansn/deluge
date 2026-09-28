# Device test v16, 27.09.2026

Local session on the Windows 11 PC, Deluge by USB. Firmware `deluge-1.2.1-mastertune-v16-c610417f.bin` (SHA-256 `7eed1a77…71897f9f`), tools from commit `0377a67`. The local session only listened to the Deluge (MIDI input), sent nothing and wrote nothing to the card.

## Points from GERAET.md (now DEVICE.md)

| # | Test | Result | Observation |
|---|---|---|---|
| 1 | Version | open | According to the user v16 is flashed; the display under Settings → Firmware version wasn't read. The v16 symbols give consistent function names. |
| 2 | USB audio stays on | not tested | |
| 3 | CPU monitor | partly ok | On and Profile run, Profile switched several times without a crash. Alerts not checked. |
| 4 | Drone tracks | not tested | |
| 5 | Measurement big song | ok | "New Sitar Grii 10", 30 s idle, 39 s playing, starting and stopping clips. Numbers below. |
| 6 | L2 versions | partly | l2d measured, roughly (see the addendum), no crash. l2i open. |

## Measurement "New Sitar Grii 10" (`2026-09-27-v16-big-song.jsonl`, CPU monitor on Profile)

| | Idle, 30 s | Playing, 39 s |
|---|---|---|
| CPU display mean / peak | 86.3% / 62% | 95.8% / 213.6% |
| Voices at most | 0 | 16 |
| Quality lowered (QL) | never | all 39 s, level 12–14 |
| Voices cut | 0 | 124, in 21 s |
| Longest gap of the audio routine | 0.4 ms | 4.2 ms; above 2.9 ms in 24 s |
| Audio routine / scheduler (profile) | 87.4% / 10.7% | 96.7% / 2.1% |

**Computing time per track** (measured on the device, share of the time):

| Track | Idle | Playing |
|---|---|---|
| K Guiro | 14.7% | 10.5% |
| K 3L3Ctr0 | 12.0% | 13.2% |
| K 014 CR-78 | 6.6% | 7.3% |
| K KIT1 | 5.7% | 7.2% |
| A AUDIO1 | 5.5% | 2.8% |
| K Rattle V2 | 3.1% | 3.5% |
| K Hihat | 2.9% | 7.8% |
| K Rattle | 2.8% | 4.0% |
| S 170 Sitar 2 / S Oboe / S Kbass | 0.4% each | 10.4 / 7.6 / 3.3% |
| M (2 MIDI tracks) | 0.4–0.5% | 0.1% |

**Functions:** `Song::renderAudio` (time in the tracks) 70% idle, 85% playing. `reverb::Mutable::process` 6.5 / 5.9%. When idle also the scheduler: `TaskManager::chooseBestTask` 5.7%, `getSecondsFromStart` 4.5%.

## Observations

- **Idle:** The silent kits and the audio track together take 53% of the time, the synths 0.4% each. That fits the finding from the emulator (the audio routine almost without pause, silent tracks set up their effects every time), i.e. v17.
- **The ranking when idle doesn't follow the kit-level effects alone:** Guiro (delay, feedback 14–15) costs 14.7%, Rattle with a similar delay only 2.8%. KIT1 without kit effects costs 5.7%. That would be worth checking in the emulator with a similar song.
- **Playing:** The cuts depend on the load, not on the card. 20 of the 21 seconds with cuts have a gap above 2.9 ms. In the 5 seconds with card accesses (up to 23 ms per access) there was no cut. Voices get cut when the kits 3L3Ctr0, Hihat, Guiro and 014 CR-78 play together.
- **Audible (user):** Oboe and sitar can hardly be heard while the kits play. Apparently it's their voices that get cut.
- First run with the CPU monitor on On (`2026-09-27-v16-cpumonitor.jsonl`, stopped after 75 s; the file contains about the first 60 s of it): when starting clips, cuts of up to 21 per second together with card accesses (4–7 ms) and gaps of up to 4.6 ms.

## The song (from the backup of 25.09., saved with c1.2.1)

140 BPM, song reverb Mutable (room 16), 13 tracks:

| Track | Kind | Settings that keep running without notes |
|---|---|---|
| KIT1 | kit, 1 drum (resample WAV) | delay on the drum |
| AUDIO1 | audio track | input monitoring on (right), reverb send 9 |
| Oboe, 170 Sitar 2, Kbass | synths, sample + square | – |
| Rattle V2 | kit, 19 drums | – |
| Rattle | kit, 14 drums | delay on the kit, feedback 14 |
| Hihat | kit, 14 drums | flanger on the kit |
| Guiro | kit, 8 drums | delay on the kit, feedback 15, reverb send 4 |
| 3L3Ctr0 | kit, 36 drums | phaser (feedback 9) and HPF on the kit |
| 014 CR-78 | kit, 14 drums | flanger on the kit |
| 2 × MIDI | | |

## Notes on tools and instructions

- **Windows port names:** `Deluge 0`, `MIDIIN2 (Deluge) 1`, `MIDIIN3 (Deluge) 2`. `open_input()` looks for a 3 at the end of the name and therefore takes port 1, from which nothing comes. With `-p "MIDIIN3 (Deluge) 2"` it works. Suggestion: recognise `midiin3` too.
- **`pip install mido python-rtmidi`** fails on Windows with Python 3.13 and 3.14. `python-rtmidi` 1.5.8 has Windows packages only up to 3.12, otherwise pip wants to compile. With Python 3.12 everything runs.

## Addendum: l2d (`2026-09-27-v16-l2d-big-song.jsonl`)

Firmware `l2test/deluge-1.2.1-mastertune-v16-l2d-03ccaac5.bin`, CPU monitor on Profile. Other than planned: only 2 s idle, then 68 s played, longer and with more clips than with v16. The comparison is therefore rough. The measurement with a fixed sequence follows.

| | v16 | l2d |
|---|---|---|
| Idle: CPU display | 86.3% (30 s) | 80.7% (2 s) |
| Playing: voices mean / at most | 8.6 / 16 | 14.5 / 24 |
| Voices cut | 3.2/s (124 in 39 s) | 2.5/s (173 in 68 s) |
| Seconds with a gap above 2.9 ms | 62%, longest 4.2 ms | 74%, longest 5.1 ms |
| Quality lowered | all the time | all the time (level 11–14) |
| Share Sitar / Oboe | 10.4 / 7.6% | 18.4 / 8.2% |

- In the first 19 s with l2d: 13 voices on average, no cut, a gap above 2.9 ms in only 4 s. After that, with up to 24 voices, cuts again (3.5/s) and gaps of up to 5.1 ms.
- **Audible (user):** with l2d "very much better".
- No crash, nothing unusual. Nothing was saved or recorded during the test.

## Song for the emulator (task 2)

- `card/SONGS/New Sitar Grii 10.XML`: unchanged from the backup of 25.09. According to the user the song hasn't changed since.
- `card/SAMPLES/…`: 136 of the 139 samples with their card paths, 75.6 MB. According to the user all own samples.
- `card/samples-new-sitar-grii-10.csv`: all 139 with track, size, format and length.
- Three hi-hats from `SAMPLES/PsyPack/` (kit Hihat) are missing; they aren't in the backup.
- Formats: 49× stereo 24 bit, 41× mono 16 bit, 21× stereo 16 bit, 18× stereo 32-bit float, 4× stereo 96 kHz 32 bit, 3× mono 24 bit.
- **Master tune for all measurements: 432 Hz** (the user's statement). The backups of 25./26.09. have no entry yet; they are from before.
  - At 432 Hz all samples are interpolated, the drums too: almost all of them play untransposed (3L3Ctr0: 1 of 36 transposed) and ran natively at 440 Hz.
  - Emulator table (`research/OPTIMIZATION.md`): per stereo voice and block 3,500 instructions native, 12,000 linear (at QL 14), 20,800 with sinc. That probably explains a good part of the kit load while playing.
  - A comparison measurement at 440 Hz would show what 432 Hz costs on the device.

## Files

- `2026-09-27-v16-big-song.jsonl`: the measurement above (70 s, Profile)
- `2026-09-27-v16-l2d-big-song.jsonl`: l2d, 70 s, Profile
- `2026-09-27-v16-profile-check.jsonl`: 12 s Profile while playing
- `2026-09-27-v16-cpumonitor.jsonl`: first run, CPU monitor on On
