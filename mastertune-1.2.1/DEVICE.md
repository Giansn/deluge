# Local session: device tests and live view

These instructions are for the Claude session on the computer the Deluge is connected to by USB.

**Renamed on 28.09.2026:** this file used to be `GERAET.md`, and the folder `geraet/` is now `device/` (`geraet/analyse/` → `device/analysis/`, `geraet/karte/` → `device/card/`). The results branch is now `device-results` (formerly `geraet-ergebnisse`, which will be deleted once both sessions have switched): `git fetch && git checkout -b device-results origin/device-results`, then merge the development branch into it as before. **The whole repository is in English now: write reports, notes and commit messages in English.**

**Roles:**
- **The cloud session develops:** firmware code, builds, emulator tests, releases. It alone is responsible for these.
- **The local session does only two things:** tests on the device and the live view. It changes no firmware code, builds no firmware and only flashes `.bin` files from this repo.

## Setup

```sh
git clone https://github.com/giansn/deluge && cd deluge
git checkout claude/wizardly-brahmagupta-nnrk07      # the current state is here
pip install mido python-rtmidi
python3 mastertune-1.2.1/tools/deluge_profiler.py ports   # the Deluge shows three ports, the third is the monitor port
```

Run `git pull` before any work: the cloud session keeps delivering.

**Windows:**
- **Use Python 3.12.** `python-rtmidi` has no ready-made packages for newer versions.
- **Port names:** The ports are called `Deluge 0`, `MIDIIN2 (Deluge) 1` and `MIDIIN3 (Deluge) 2`. From the commit after `0377a67` the script finds `MIDIIN3` by itself, `-p` is no longer needed.

## Tasks (as of 28.09.2026, v18.3 is out)

Done: the report on v16, l2d, the song for the emulator, the retune test on Windows, "Rescue". The tasks for v17 are dropped: v18.3 contains v17, v18 and v18.2, so everything now applies to v18.3. What it brings is in the README, in the sections "v18", "v18.2" and "v18.3".

1. **Flash v18.3:** `deluge-1.2.1-mastertune-v18.3-l2d-3581f019.bin`, the main file with L2 for code and data (v18.3 only comes this way). Settings → Firmware version shows `1.2.1-mastertune-` / `v18.3-l2d`. If it crashes: `deluge-1.2.1-mastertune-v18-124aeaa2.bin`, v18 without L2.
2. **The most important measurement: "New Sitar Grii 10" with v18.3**, like the v16 report. 432 Hz, CPU monitor on Profile, 30 s idle, then 40 s of the same passage with the same clips. `live -s 70 -o 2026-..-v18.3-l2d-big-song.jsonl --symbols deluge-1.2.1-mastertune-v18.3-l2d-3581f019.symbols.json`.
   - The emulator expects about the load of v17 (41 instead of 93% shown with v16). The new filters cost a little more: the test song needs 91.4 instead of 89.3%.
   - Note: QL, voices cut, the longest gap, and whether the oboe and the sitar can be heard.
3. **Filters and volume by ear**, each briefly, ideally with headphones:
   - **Rustle:** a synth with LPF 24 dB, cutoff low (about 10–20), resonance medium, a long note. The noise band around the note must be gone. The same with 12 dB and Drive.
   - **Hum:** In "New Sitar Grii 10", the song LPF fully closed. The hum at 32 Hz must be gone.
   - **Transitions:** Turn cutoff and resonance quickly, switch the filter mode and routing while a note sounds. No steps, no clicks.
   - **Parallel routing:** A song with routing "Parallel" is 6 dB quieter than with v17. Does that bother you?
   - **Drive:** LPF mode Drive with a lot of resonance. The bass stays instead of getting thin. New in v18.3: a song with several Drive filters no longer loses voices at the start of the bar, and high up with resonance Drive still sounds clean, without aliasing.
   - **EQ:** Turn bass and treble while music plays. No clicks.
   - **Volume:** Every detent changes 0.5 dB, the display shows dB (`-3.5`), at the very bottom `OFF`. Tracks set quiet sound clean.
   - **Sidechain:** An old song with hard pumping now sets in over at least 5 ms, a little softer. Does that bother you?
   - **Output limiter** (Settings → Community features, `LIMT`): on a song that clips. No more hard distortion. Switching it may click once.
   - **Filter crossing guard** (Settings → Community features, `CROS`): turn HPF and LPF with resonance towards each other. No more painful jump in level.
   - **After a silence (new in v18.2):** let a kit not play for a while (a pause in the clip, or muted), change its LPF, EQ or volume meanwhile, then let it play again. The first hit sets in without a tick and without a bright attack, right away with the new sound.
4. **OLED and song browser:**
   - **Brightness:** Settings → OLED brightness, levels 1–10. Every turn acts at once, the level stays after a restart. Is level 1 still readable?
   - **Groups:** "New Sitar Grii", "New Sitar Grii 2" and "… 10" appear as one group. "TR-808" or "Jam 2026-09-27" stay songs of their own.
5. **DelugeRec with song names** (the newest version in the release https://github.com/Giansn/deluge/releases/tag/deluge-rec, v7 or later):
   - USB audio on, load a song, record. With v18.3 flashed, the file must be called "Song name, date - 1.2.1 v18.3.WAV" (from DelugeRec v7; v6 shortened it to v18).
   - Load another song and record again. The new name must appear.
   - Only the device can check this: there is no USB in the emulator.
6. **Convert the library `deluge topics` to 432 Hz, anew with the version that no longer freezes the laptop** (`tools/retune_library.py`, from commit `868d85f`, so `git pull` first):
   - **What's new:**
     - Every file is converted in pieces of about 3 s. Per file this needs about 60 MB of memory instead of up to 8.5 GB, also for long recordings.
     - How many files run at the same time depends on the free memory: at most half of it. The console shows what was chosen.
     - The result is byte for byte the same as with the old version.
   - **Easiest on Windows: DelugeTuner** (`DelugeTuner-vN.exe` in the release https://github.com/Giansn/deluge/releases/tag/deluge-tuner, nothing to install). CARD: the copy of the card, OUTPUT: a folder on the computer, the gold knob on 432.0 Hz, first READ (only shows what it would do), then RETUNE. ESC stops, START with the same settings continues. REPORT opens `RETUNE_REPORT.txt`. The command line below still works.
   - **Start anew:** The frozen folder comes from the old version and can't be continued. Delete or rename it, then convert into a new, empty folder:
     `python tools/retune_library.py --card <copy of the card> --out <new empty folder>`
   - **If it stops** (crash, power, full disk): the same command with `--resume` and the same folders. Finished files stay, half-done ones are redone.
   - Files whose peaks went over 0 dBFS are written as 32-bit float, as wanted. The Deluge still limits them to 0 dBFS when it plays them.
   - Then copy onto a second card, push `RETUNE_REPORT.txt` too, and repeat task 2 with the converted card.
7. **PETTRA ARP onto the Deluge and by ear** (new on 28.09.2026; the synth for the arp of Pettra "You Are The Seeds", details in `presets/README.md`):
   - Copy `presets/SYNTHS/PETTRA ARP.XML` (and, if not there yet, `PETTRA PINGPONG.XML` and `PINGPONG ROLL.XML`) into `SYNTHS` on the card: with a card reader, or over USB with DEx (https://dex.silicak.es, Chrome or Edge, upload into `SYNTHS`).
   - On the Deluge: a new synth clip, load PETTRA ARP, tempo 138, Arpeggiator → Latch on, hold A4 C#5 E5. Let it run for 8 bars.
   - Compare with `references/pettra-arp/Pettra_arp_0m48-1m16.mp3` at 0:55–0:58 and with `presets/demo/PETTRA ARP.wav` (the emulator's render). Does the figure (3 hits into the next step, faster each time) sound like the song's? Is the pluck too bright or too dark, the figure too frequent or too rare? Does it sound closer with whole chords (Randomizer → Chord Polyphony 3, Chord Probability 100 %)?
   - Optionally record 8 bars with DelugeRec. Write a short report in English to `device/` and push it to `device-results`; no WAV needed.

### Done: "Rescue", the LPF has no effect (27.09.2026)

- **Finding** (emulator with the real XML, cross-checked): The LPF works. But the song LPF's resonance is at maximum, so from about knob 36 the filter oscillates by itself. Its tone moves down with the knob, is about 10 dB above the music and clips. At the bottom a tone below 60 Hz remains. v16 behaves the same.
- **The automation** (one node "fully open") is not the cause. In song view it only acts when recording into the arrangement.
- **The user's decision:** The firmware does not limit the resonance; self-oscillation stays possible, as in the original. v18 makes it 6 dB quieter and without the hum at the bottom.
- **On the device:** in song view, with the filter button on LPF, turn the lower gold knob (resonance) below about 35, then save. To get rid of the automation too: hold SHIFT, then press the upper gold knob ("Automation deleted"). Without SHIFT the press changes the filter type.

## Status (28.09.2026)

| File | What |
|---|---|
| `deluge-1.2.1-mastertune-v18.3-l2d-3581f019.bin` | **current version**, with L2 cache for code and data. In the README the sections "v18.3", "v18.2" and "v18". |
| `deluge-1.2.1-mastertune-v18.2-l2d-c9c65066.bin` | v18.2, Drive as in v18 |
| `deluge-1.2.1-mastertune-v18-l2d-6fa0875b.bin` | v18, with the bug after a silence |
| `deluge-1.2.1-mastertune-v18-124aeaa2.bin` | v18 without L2, to switch back |
| `deluge-1.2.1-mastertune-v17-l2d-b3385d83.bin` | v17, the version before |
| `l2test/deluge-1.2.1-mastertune-v17-l2i-e476310e.bin` | v17 with L2 for code only |
| `*.symbols.json` next to every `.bin` | function names for the profiler, matching exactly this `.bin` only |
| `hotfix/…v16-l2d-dronefix…` | replaced by v17 |

**Flashing:** the `.bin` into the root folder of the SD card, no other `.bin` next to it. Hold **SHIFT** while switching on. Then check the name under Settings → Firmware version.

## Live view

On the Deluge: **Settings → CPU monitor → On**. With **Profile** you also get tracks, tasks and functions.

```sh
cd mastertune-1.2.1
python3 tools/deluge_profiler.py live                          # one line per second, Ctrl-C ends it
python3 tools/deluge_profiler.py live -s 60 -o measurement.jsonl \
    --symbols deluge-1.2.1-mastertune-v17-l2d-b3385d83.symbols.json  # 60 s, with Profile also the most expensive functions
python3 tools/deluge_profiler.py report measurement.jsonl --symbols deluge-1.2.1-mastertune-v17-l2d-b3385d83.symbols.json
```

For Claude: run `live` with `-s` and `-o` in the background and read the output, or run `report` afterwards.

**The line:** `CPU 43.0 % (peak 71.0)  voices  24 (max  30)  QL 0 (0.0 % of the time)  cut 0  gap 3.1 ms  SD 11 loads, avg 1.7 max 9.0 ms`
- **CPU:** load of the audio windows, mean and peak of the last second.
  - **Important up to v16:** When idle it shows 85–90%. That is busy waiting, not overload (see `prof/README.md`). From v17 on it shows the real load.
- **voices:** sounding voices, now and the maximum.
- **QL:** quality lowered (direness 0–14) and for how much of the time.
- **cut:** voices cut (culling). The number that counts.
- **gap:** the longest pause between two runs of the audio routine. From about 2.9 ms on a dropout threatens.
- **SD:** loads from the card, mean and longest duration.
- **With Profile**, every 5 s: the audio routine's share, the most expensive tracks (measured on the device), tasks and, with `--symbols`, functions.

## Tests on the device (v16, partly done; for v17 see the tasks)

Note every point with ok or not ok and a short observation.

1. **Version:** Settings → Firmware version shows `1.2.1-mastertune-v16-c610417f`.
2. **USB audio stays on:**
   - Settings → Community features → USB audio → on.
   - Do **not** leave the menu, and switch the Deluge off.
   - After switching on, the tick must still be set, and the computer sees the Deluge as an audio device.
3. **CPU monitor:** switch between On, Alerts and Profile. No crash, `live` shows lines. With Profile the summary comes every 5 s.
4. **Drone tracks** (manual `docs/Drone-manual.pdf`, chapter 16):
   - In the drone view, **Shift + Kit**: a kit DRONE1 appears, its clip is at the bottom of song view.
   - Start the clip: it sounds like the drone, equally loud.
   - In clip view choose a drone row and turn Select: the Hz change, the OLED shows them.
   - **PLAY**, **REC**, turn Select: the Hz changes are played back in the next loop.
   - Save the song, load it again: everything sounds the same.
5. **Measurement with the big song** (the basis for the comparison with v17):
   - `live -s 70 -o v16-big-song.jsonl`.
   - Load the song, play nothing for 30 s, then play for 30 s, ideally the passage where QL or VC appears.
6. **L2 versions:** Repeat point 5 with l2i and with l2d. Note a crash with the version, what you did and what the display showed: frozen, restart or a message like "E…".

**If the Deluge crashes or behaves strangely:** go back to v16 (or v15 in the main folder). Note the version, what you did and what the display showed, exactly.

## Results for the cloud session

- **Where:** in `mastertune-1.2.1/device/`, named after the pattern `YYYY-MM-DD-<version>-<topic>`, in English.
  - A short report as `.md`: the points above with ok or not ok and the observations.
  - The measurements as `.jsonl`.
- **Push:** to a branch of its own, `device-results`, never to the development branch. That way the two sessions don't get in each other's way.
- **Tell:** The user tells the cloud session, which then fetches the results.
