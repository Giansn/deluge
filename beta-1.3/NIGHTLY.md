# The community firmware's nightly (v1.3 beta): why it crashes, and what it means for mastertune

As of 1 October 2026. Upstream: `SynthstromAudible/DelugeFirmware`, branch `main`. Sources: the git history, GitHub's
issue and pull request pages, the code and the documentation at today's build, and the firmware itself in the
emulator. The investigation's tools are in `beta-1.3/tests/` and its raw data in the session's scratchpad (summarised
here).

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
   memory; a double free would be a crash. Neither has been observed yet. An audit of the 11 callers
   (`device/analysis/2026-10-01-v13-4920-double-free.md` on `device-results`) found the new free loop dead at every one
   of them: each destination is fresh, emptied just before, or guarded. The fix's effective change is the destructor
   of the removed backup (`song.cpp:3942`). So this cause is unlikely to crash today; a latent trap remains for a
   future caller (a kit row whose cloning failed for lack of RAM keeps a stale expression offset).

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

### What the beta's own documentation says

The beta's `README.md` is a pointer to delugecommunity.com. That site is built from `website/` in the same repository,
and its pages at `62a516c2` were read for this section (paths below are under `website/src/content/docs/`).
- **No list of known issues.** The download page has only the general caveats quoted above (`downloads.mdx:53,55`)
  and the advice to photograph a crash (`downloads.mdx:79`). The c1.3.0 changelog (`changelogs/CHANGELOG.mdx:33-463`)
  lists features; the rewrites of the scheduler and of memory ownership are not in it.
- **The new menus can be switched off:** `SETTINGS > COMMUNITY FEATURES > HORIZONTAL MENUS`
  (`features/community_features.mdx:40-42`). It is on by default. Whether this avoids the menu crashes of cause 2 is
  not proven: the sound editor's rewrite underneath stays.
- **Audio export has documented problems** (`features/audio_export.mdx`). On heavy arrangements, offline rendering
  "fills up memory" and leaves files with 5 seconds of audio; the advice is to turn offline rendering off (191-201).
  The tail of one export can bleed into the next, unsolved (225-227). Memory pressure during offline rendering fits the
  repeated-export crashes (#4639, #4471, #3309), but no issue says so.
- **What the project tests.** The developer guidelines say that CI checks compiling and formatting, "and in the future
  possibly unit testing", and that authors test their changes "on a best effort basis" by hand
  (`development/deluge/guidelines.md:50-55`). The repository is further along than the page: CI runs unit tests on
  x86 and an ARM spec under QEMU (`.github/workflows/tests.yml`). They test isolated parts: the memory manager,
  scheduler timing, LFO, scales, fixed-point maths, `memmove`. Nothing runs the whole firmware or a sequence of button
  presses, and the documentation names no emulator. Object lifetimes across the UI (cause 1) and the menus (cause 2)
  are therefore tested only by hand and by the beta's users. This is the main reason the regressions reach the beta.
- **The release process on paper and in practice.** `docs/GOVERNANCE.md:16-25` still describes a `nightly` tag and a
  release every three months, each prepared on a release-candidate branch that takes only bug fixes. In practice the
  last release is 1.2.1 (May 2025) and the nightly tag stopped in July 2026. The signal that 1.3 is near is a
  `release/1.3` branch (the naming of `release/1.2`, `features/community_features.mdx:18`).
- **The docs are partly out of date.** The getting-started page still names toolchain v10 with GCC 13.2.1
  (`development/deluge/getting_started.md:22,49`); `main` needs v22 (section 1).
- **Switching between the beta and 1.2.1 (or mastertune).** 1.3 moves `MIDIDevices.XML`, `MIDIFollow.XML`,
  `PerformanceView.XML` and `CommunityFeatures.XML` into a `SETTINGS/` folder. A firmware before 1.3 finds them only
  if they are moved back to the card's root (`changelogs/CHANGELOG.mdx:452-456`). MIDI Follow settings left the flash
  memory; old `MIDIFollow.XML` files are to be deleted when updating (`CHANGELOG.mdx:55-56`).
- **Contributing upstream.** `docs/CONTRIBUTING.md:15-18`: "do not use an agent to open PRs or comment on them", "do
  not use an LLM to write PR descriptions". mastertune's fixes can go upstream only through a person who writes the
  pull request themselves.

## 3. In the emulator

`beta-1.3/tests/` adapts mastertune's emulator rig (`mastertune-1.2.1/tests/song`, `tests/stress/ui`) to v1.3
(`emu13.py`, `rig13.py`):

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
| `47b1d92b` #4920 | ParamManager leaks (in mastertune at `song.cpp:3863-3867`) | cleanly; no double free (audited) |
| `dd911e86` #4615 | automation region edit through a dangling pointer | cleanly |
| `e851b323` #4645 | dangling `outputRecordingFrom` after deleting a track | one hunk by hand |
| `9c3f9a70` #4671 | SampleRecorder freed inside the card routine | partly by hand |
| `9cd09fb7` #4769 | external clock dies while the card routine runs | partly by hand |
| `e2edb15f`, `e95d7dd9`, `2280df7a`, `35dd7e71` | small guards (1-6 lines) | cleanly |

**Taken over in mastertune v19.0.4** (1 October 2026): `9c3f9a70` (the recorder freed twice, as a guard; the export
crashes #3309, #4471, #4639 reproduced on mastertune), the effective part of `47b1d92b` (#4920), the #4917 fix, a fix
for #4518's case A (a synth loaded into a kit row), and a named CV track's channel.

**#4917 is in mastertune too.** The emulator shows it. mastertune v19.0.3 saved the test song, and its `<audioClip>`
carries six attributes twice: `colourOffset`, `isArmedForRecording`, `isPlaying`, `isSoloing`, `length` and
`section`. The Deluge reads such files without trouble, but strict XML readers (tools, editors) refuse them. The fix
is one line (`audio_clip.cpp:1086`).

## 5. Results of the emulator runs

Three runs on 1 October 2026, the same random inputs (seed 1, then 1001) for both firmwares. Between inputs the
firmware's own task manager ran 20-150 ms, so an hour of the host is about 4-5 minutes of the Deluge.

| Run | Firmware | Inputs (boots) | Problems |
|---|---|---|---|
| all inputs, OLED, 60 min | v1.3 beta `62a516c2` | 1,731 (2) | **1 freeze: `SM01`** at input 907 |
| all inputs, OLED, 60 min | mastertune v19.0.3 | 1,892 (2) | none (one false hang, see below) |
| the song browser, 7-segment, 45 min | v1.3 beta | 2,397 (1) | none |

- **v1.3: `SM01` on SHIFT + SCALE, and mastertune has the same code.** The freeze is in
  `ScaleMapper::computeChangeFrom()` (`scale_mapper.cpp:14-15`): the notes of all scale-mode clips must lie in the
  current scale. `Song::setScaleNotes()` checks only that they fit in the new scale's size (`song.cpp:3051`), then
  calls the mapper. How the rule broke, from an exact replay of the run (it freezes at the same step):
  - recording armed and playing, a clip in keyboard view in scale mode;
  - pads outside the scale played: the firmware adds those notes to the scale (`instrument_clip.cpp:1104`);
  - BACK (undo) set the scale back to the one saved in the action (`action_logger.cpp:521`), but the out-of-scale
    note (D#) stayed in the clip. From then on the scale didn't contain all the clip's notes;
  - SHIFT + SCALE (next scale: `keyboard_screen.cpp:438` → `cycleThroughScales()` → `setScaleNotes()`) froze.

  A short sequence (record out-of-scale notes once, undo, SHIFT + SCALE) didn't reproduce it: there the undo removed
  the notes too. Recording over several loops before the undo is the likely difference; not pinned down yet. The check
  came with the scale mapper (`8b8a67f3`, #2363, August 2024), so 1.2.1 has it: mastertune's code is the same
  (`scale_mapper.cpp:14-15`, `song.cpp:2983-3000`, `action_logger.cpp:512`). No upstream issue or fix. A fix: in
  `setScaleNotes()`, refuse the change (or widen the source scale by the clip's notes) when the notes aren't a subset
  of the current scale, instead of freezing.
- **mastertune: a false hang, found and fixed in the fuzzer.** SHIFT + pad 0,4 in a synth clip opens the sound
  editor's RECORD AUDIO, which stays in its own loop (`AudioRecorder::process()`) and reads the buttons from the PIC
  itself. The fuzzer's inputs are calls, so nothing ended the recording and the pad press never returned. The fuzzer
  now ends such a recording after 1.5 s as BACK would; checked on v19.0.3: without that the same hang, with it the
  recording ends, the file is finished and the clip view returns. 1.2.1 and v1.3 have the same loop.
- **The song browser on the 7-segment display** (the area of #4846) held up for 2,397 inputs.
- **Writes through a null pointer:** 18 in each firmware, all from the mod buttons (`View::modButtonAction()` writes
  through a null mod-knob-mode pointer for some outputs). Harmless on the device (section 3); the same code in both.
- **Memory:** no trend within a run. Free SDRAM stayed between 1.75 and 1.93 MB on v1.3 and between 7.95 and 8.30 MB on
  mastertune, with the same song. v1.3 keeps about 6 MB more allocated; why (caches, the new effects, the menus'
  tables) is not measured yet.
- **Error popups:** one `Error 18` (file not found) on mastertune, from a browser on the test card. Expected.

What this does and does not show: an hour of random input found one crash in the beta and none in mastertune. That
fits the issue tracker (section 2), but such a run reaches few of the deep paths: it does not hold a button while
pressing another, so it never exported stems, never loaded a synth into a kit row, and never learned a knob. The
helper session's reviews of those paths (section 2) found faults that mastertune shares; v19.0.4 fixes them.

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
- **Advice:** look again once c1.3.0 has a release-candidate branch (`release/1.3`) or a release tag. Until then,
  take single fixes over (section 4).

What `main` has that mastertune doesn't:
- sounds that send MIDI notes (#3313, task 25, a medium port)
- LFO 3/4 and envelopes 3/4
- stutter per clip with reverse
- new mod FX
- looper/sampler outputs
- audio export of single kit rows and a mixdown (offline rendering itself is in 1.2.1 already)
- 24 sections
- MIDI Thru per device (1.2.1 has the global setting)

What mastertune has that `main` doesn't: master tune (`main` only edits master transpose in decimal cents now,
`CHANGELOG.mdx:91`), USB audio out, the tuner and Scan, the delay, filter and EQ
quality work, the drone, the DJ tools, the countdowns, and 20-25 % less CPU than 1.2.1. For the open plans (USB audio
in, master EQ, line-in tuner, DJ decks/crossfader/isolator), `main` has nothing to take over.

## 7. Stabilising the beta: our patches

Since 1 October 2026 the beta is stabilised here, in our repository only (`beta-1.3/patches`, applied to `62a516c2` by
`tools/setup_beta.sh`). Each patch has an emulator test in `tests/repro` that fails on `62a516c2` and passes with the
patches (`tests/repro/run.sh`); 0007 and 0008 are covered by the fuzzer seeds that found them. Numbers 0001-0099 are
this session's, 0101 and up the helper session's (`reports/`).

| Patch | Fault on 62a516c2 | Found by | Test |
|---|---|---|---|
| 0001 | SM01: undo leaves a recorded note outside the scale, SHIFT + SCALE freezes | fuzz seed 1 | `sm01_replay` (LONG=1) |
| 0002 | Duplicate `<audioClip>` attributes on save; a named CV track loses its channel (#4917, #4938) | issue review | `save_4917` |
| 0003 | E455: a song change while playing, the old song with an arrangement | issue review | `songswap_e455` |
| 0004 | A synth into a kit row: the view on a freed drum, E412 on knob learn (#4518, #4951) | issue review | `kitrow` |
| 0005 | SAVE proposes an existing song's name and overwrites it (#4825) | issue review | `save_name` |
| 0006 | Freezes out of external RAM (TERM), on a stale cluster ("invalid"), on OLED handshake hiccups | freeze audit | `oom_robust` |
| 0007 | A transpose note during a song change (no root UI) | fuzz seed 11 | fuzzer |
| 0008 | E411: a kit row saved whose sound has no params | fuzz seed 1011 | fuzzer |
| 0009 | #4952's cluster caching: wrong units, count forced to 2, loop start skipped, reverse ignored | code review | `cluster_cache` |
| 0010 | M000: a synth preset that fails to load is freed from the middle of its block | fuzz seed 2041 | `abandon_load` |
| 0011 | A track export or mixdown hangs when a stem's recording is aborted (RAM short) (#4471, #4639) | export probe | `export_abort_hang` |
| 0012 | An aborted stem stays on the card as a broken file whose header says 5 s | export probe | `export_abort_file` |
| 0013 | E369: CV, MIDI, KIT, SYNTH or SCALE in the arranger's automation view (load, SONG, CLIP, CV) | fuzz seed 1071 | `arranger_automation` |
| 0014 | RECORD AUDIO with threshold recording on: BACK never ends the recorder (since #4678) | menu walker | `recorder_threshold` |
| 0015 | Crash on SYNTH/KIT/MIDI/CV in a menu once horizontal menus are switched off at run time | menu walker | `hmenu_off_buttons` |
| 0016 | S004: a fast horizontal-encoder turn puts a browser's text cursor outside the text (since #4529) | fuzz seed 71 | `qwerty_cursor` |
| 0017 | Memory corruption: a MIDI clip with MPE output and the arp on writes 2 KB past its member channels on every note-off (the crash in `Patcher::performPatching()`) | fuzz seed 3001 | `mpe_arp_noteoff` |
| 0101 | E411: audition pad + SAVE opened the kit-row save for a non-kit row | fuzz seed 1011 | `savekitrow` |
| 0102 | E411: a clip made CV kept its synth parameter in the automation view | fuzz seed 101 | `automation_type` |
| 0103 | Null writes: the MOD buttons in a CV clip wrote through a null `getModKnobMode()` (the fuzzer's "null writes") | write hook on the null page | `modknob_cv` |
| 0105 | E445: a Song in reused memory "played reversed" when song automation was recorded (PR #4445's fields uninitialized) | fuzz seed 108 | `song_reversed` |
| 0106 | E369: undo or redo in the arranger's automation view went into a Clip that isn't there | fuzz seed 109 | `arranger_undo` |

0104 (the S004 of fuzz seed 106) was the same fix as 0016, found independently, and is withdrawn.

How the faults were found:
- **Fuzzer rounds** (`tests/fuzz_ui.py`, 50-60 minutes each, OLED and 7-segment, modes `all` and `deep` with held-button
  combinations). Every round runs on the build with all patches so far; each problem is replayed exactly (same seed)
  with a backtrace, then reduced to a short test. Round 4 (0001-0012): three problems in about 2,750 inputs (0013,
  0016, 0017). Round 5 (0001-0016, 0101, 0102; deep seed 81 and all seed 5001, OLED): none in 3,219 inputs, no wild
  access, no error popup, free SDRAM steady (0017's fault was still in that build; the inputs didn't reach it).
  Round 6 (the same build, 7-segment, deep seed 91 and all seed 7001): none in about 2,100 inputs, cut off by a
  restart of the container. Round 7 (all 22 patches; deep seed 211 OLED, all seed 8001 7-segment): none in about
  3,100 inputs (46 of 60 minutes, again cut off by a restart; the counts are from the logs, which report every 100
  inputs and every problem at once).
- **The whole series together**: `tests/repro/run.sh` on the build with all 22 patches: all 19 tests pass (not counting the
  35-minute SM01 replay, run on its own).
- **Probes** for the areas the issue tracker names: `tests/menu_walk_emu.py` (every menu of nine contexts, horizontal
  menus on and off: about 900 items, clean with 0014) and `tests/repro/export_repeat_emu.py` (16-28 stem exports in a row
  with song changes and injected RAM failures: no leak, clean with 0011 and 0012).
- **Fuzzer artefacts fixed on the way**: the modal audio recorder (ended like BACK), a song load while playing (a quick
  LOAD tap), v1.3's encoder task (each turn now wakes it), the SSI's receive DMA (its position now moves with the
  transmit DMA: without it a recorder recording an input froze with bbbb, fuzz seed 114).

What mastertune 1.2.1 shares: the code of 0001 (SM01) and 0010 (the M000 free, from #588 in 2023) is the same in 1.2.1,
so both are candidates for v19.0.5; v19.0.4 already has its own fixes for the faults behind 0002 and 0004 (section 4).
Came with 1.3, so not in 1.2.1: 0006's `operator new` (#4598), 0009 (#4952), 0014 (#4678), 0016 (#4529). 1.2.1 has 0103's null write (`View::modButtonAction()`) and the older form of 0105's fault (it casts the Song to a
`Clip` for the direction). Not checked against 1.2.1 yet: the rest.
