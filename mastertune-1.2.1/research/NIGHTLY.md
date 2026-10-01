# The community firmware's nightly (v1.3 beta): why it crashes, and what it means for mastertune

As of 1 October 2026. Upstream: `SynthstromAudible/DelugeFirmware`, branch `main`. Sources: the git history, GitHub's
issue and pull request pages, the code at today's build, and the firmware itself in the emulator. The investigation's
tools are in `tests/v13/` and its raw data in the session's scratchpad (summarised here).

## 1. What "nightly" is today

- The daily build of `main` is called **beta** now: the workflow "Beta Build" runs at 00:14 UTC when `main` got commits
  in the last 24 hours and moves the tag `beta`. Today: `62a516c2` (2026-10-01 02:41, "Fix/ Adjust when shortcut overlay
  is rendered (#4953)"). The tag `nightly` stopped on 2026-07-07 (`ed79215f`), when the nightly schedule was switched
  off (#4681, 2026-07-15).
- It calls itself **c1.3.0 "Derbyshire", upcoming**. There is no 1.3 release yet; c1.2.1 is still the latest release.
- Since the branch point of 1.2.1 (`cc25a0ec`, 2024-08-25): about 1,000 commits, 905 files, +62,000/-30,000 lines.
  Most of it is user interface (views 17 %, menu items 15 %, UI 12 %), then the model (14 %) and storage (7 %); the
  sound engine 3 %. Pace: 53, 72, 73 commits in June, July, August 2026, 36 in September.
- Building it needs the dbt toolchain **v22** (GCC 14.2.1): with v16's GCC 13 `src/memmove.c` doesn't compile (the NEON
  intrinsics `vld1q_u8_x4` came with GCC 14). A sparse checkout without `website/` and `contrib/` works, but the menu
  generator needs `docs/menus/`. Today's build: `deluge-v1_3_0-beta+2026_10_01-62a516c2.bin`, 1,731,052 bytes.

## 2. Why v1.3 crashes regularly

The crash reports have not slowed down: in September 2026 seven new crash or freeze reports came in (#4956, #4949,
#4947, #4928, #4938, #4902, #4888); since April about 35 were closed (about 9 of them without a fix: not reproducible
or not planned); **11 are open**, several marked release-blocker. Four causes, by how much they explain:

1. **Pointers that outlive what they point to.** When a sound, kit row or ParamManager is replaced or deleted
   (a synth preset loaded into a kit row, a row deleted, the sound editor left), pointers cached in the view and the
   sound editor (`currentModControllable`, `currentParamManager`, `view.activeModControllableModelStack`, ...) still
   point at the old object. Then a gold knob, the slicer or the sample browser reads freed memory: **E339**, **E412**,
   hard faults.
   - #4938 (open): hard fault in `InstrumentClip::allowNoteTails()` during playback. It hands a kit row's ParamManager
     without parameter collections to `Sound::allowNoteTails()`, which reads them unchecked
     (`instrument_clip.cpp:3345-3366`). There is no guard in today's beta.
   - #4518 (fix open since May): stale sound-editor and view pointers after a row or sound is replaced.
   - #4951 (fix opened yesterday): E412 on knob learn after a synth preset was loaded into a kit row.
2. **Regressions from the new menus.** The rewrite of the sound editor and menus (horizontal menus) left places
   that assume a current menu item or a current clip. Each fix tends to open the next crash: #4928 was fixed by #4946,
   which caused #4947 (leaving Settings crashed) and #4949 (leaving any parameter menu froze). #4948 fixed those, and
   #4956 (song menu without a current clip) was fixed this morning (`b67c580d`). The known cases are fixed; the class
   remains.
3. **Re-entrancy in the scheduler.** v1.3 runs everything as tasks and lets long work yield
   (`TaskManager::yield()`). Work that waits for the SD card runs other tasks inside it. This caused #4671 (a
   SampleRecorder freed inside the card routine) and #4832 (Startup fault F1 on every boot), both fixed. Repeated stem
   or song exports still crash (#4639, #4471, #3309, all open), with the same pattern suspected but not proven.
4. **Churn in memory ownership.** The fix for ParamManager leaks (#4920) was pushed directly, reverted the same day,
   and landed again on 25 September. It now frees the destination's collections in `stealParamCollectionsFrom()`,
   which has 11 callers that were not audited for shared collections. A leak raises the risk of running out of
   memory; a double free would be a crash. Neither has been observed yet.

Also open and not a crash, but it damages files: **#4917, song files with duplicate attributes**.
`AudioClip::writeDataToFile()` calls `Clip::writeDataToFile()` twice (since 2024, so in 1.2.1 too).

The community says so itself. Its download page (`website/src/content/docs/downloads.mdx`) offers the beta with:
"Note that there may still be issues in the beta [...] Be aware that in addition to new features there are large
internal changes - we believe these will make the deluge more efficient and stable in the long term, but there may be
short term issues with perceived performance due to interactions between sub systems." A crash shows as "a crazy
pattern with yellow or red in the sidebar"; they ask for a photo of the whole Deluge on the issue tracker or Discord.
Since today (#4955) the crash handler reports more.

In short: v1.3 is a beta under heavy construction. Large parts were rewritten at once (menus, sound editor, the
scheduler, memory ownership), and the crashes come from the seams between old and new code, above all from object
lifetimes. The fixes land quickly, often within days, but new regressions keep coming in at about the same rate.

## 3. In the emulator

`tests/v13/` adapts the emulator rig of `tests/song` and `tests/stress/ui` to v1.3 (`emu13.py`, `rig13.py`):

- **SD card:** v1.3 inlines `sd_read_sect()` into `disk_read_without_streaming_first()`.
- **Boot:** the stop is where `deluge_main()` starts the task manager. v1.3 calls `startClock()` earlier as well.
- **The scheduler:** `TaskManager::yield()` takes its timeout as 64-bit ticks.
- **The startup song:** loaded by v1.3's own conditional task. The rig waits until the task deletes its canary file.
- **Detection:** crashes, `freezeWithError()` (the beta freezes on every E/i code), fault handlers, hangs and wild
  memory accesses are recorded.

`fuzz_ui.py` is a random user for both firmwares:
- **Inputs:** pads, buttons with and without SHIFT, a long BACK, all six encoders, SHIFT+pad shortcuts, PLAY.
- **Timing:** 20-150 ms of the firmware's own task manager between inputs.
- **The card:** a song with synths, a kit and an audio track, three more songs (SONG001-SONG003) and five synth
  presets.
- **Writes through a null pointer:** the Deluge's MMU maps address 0 (the empty CS0 area) as normal memory
  (`ttb_init.S`), and the exception vectors sit elsewhere (VBAR). So on the device such a write does not fault; it goes
  nowhere. The fuzzer records it and goes on instead of counting it as a crash.

Results: see section 5.

## 4. What it means for mastertune

mastertune stays on 1.2.1 (recommendation of the comparison, with reasons in section 6). Of upstream's fixes, these
are worth porting, and they apply to mastertune's code (each checked against `20a9a6ef`):

| Upstream fix | What | Applies |
|---|---|---|
| `8102020a` #4805 | i028 memory corruption: the waveform view loaded the wrong cluster (1 line) | with -C1 |
| `7064d10b` #4885 | live input pitch shifter: division by zero, endless loop, wrong mask | one hunk by hand |
| `47b1d92b` #4920 | ParamManager leaks on steal/backup delete | cleanly (check the double-free risk first) |
| `dd911e86` #4615 | automation region edit through a dangling pointer | cleanly |
| `e851b323` #4645 | dangling `outputRecordingFrom` after deleting a track | one hunk by hand |
| `9c3f9a70` #4671 | SampleRecorder freed inside the card routine | partly by hand |
| `9cd09fb7` #4769 | external clock dies while the card routine runs | partly by hand |
| `e2edb15f`, `e95d7dd9`, `2280df7a`, `35dd7e71` | small guards (1-6 lines) | cleanly |

**#4917 is in mastertune too.** The emulator shows it. mastertune v19.0.3 saved the test song, and its `<audioClip>`
carries six attributes twice: `colourOffset`, `isArmedForRecording`, `isPlaying`, `isSoloing`, `length` and
`section`. The Deluge reads such files without trouble, but strict XML readers (tools, editors) refuse them. The fix
is one line (`audio_clip.cpp:1086`).

## 5. Results of the emulator runs

(filled in below as the runs finish)

## 6. Moving mastertune onto v1.3?

Not now:
- **Patches:** 118 of mastertune's 125 patches no longer apply to `main`.
- **Conflicts:** a three-way merge gives 442 conflict hunks in 154 files.
  - About 150 are easy: features mastertune took over from 1.3 itself (arp, JSON SysEx), where upstream's version
    would simply be kept.
  - About 280 are mastertune's own work: master tune, CPU monitor, USB audio, delay, reverb, filters, drone,
    countdowns, DJ, Scan, performance. They meet rewritten upstream code: `sound.cpp` +1211/-903, the menus,
    `audio_engine.cpp`, the scheduler.
- **Effort and risk:** an estimated 4-6 weeks. On top of that, mastertune's CPU savings would have to be measured
  again, the emulator tests rebuilt for 1.3, and the result would land on a beta that still crashes regularly.
- **Advice:** look again once c1.3.0 has a release tag. Until then, take single fixes over (section 4).

What `main` has that mastertune doesn't:
- sounds that send MIDI notes (#3313, task 25, a medium port)
- LFO 3/4 and envelopes 3/4
- stutter per clip with reverse
- new mod FX
- looper/sampler outputs
- offline stem export
- 24 sections
- MIDI Thru

What mastertune has that `main` doesn't: master tune, USB audio out, the tuner and Scan, the delay, filter and EQ
quality work, the drone, the DJ tools, the countdowns, and 20-25 % less CPU than 1.2.1. For the open plans (USB audio
in, master EQ, line-in tuner, DJ decks/crossfader/isolator), `main` has nothing to take over.
