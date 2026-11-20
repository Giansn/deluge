# mastertune's features on v1.3

mastertune 1.2.1's features, ported one step at a time onto the community firmware **v1.3.0** with our fixes. The base
is the official release (tag `v1.3.0`, commit `156500e7c`: the same revision as the released file `deluge-c1_3_0.bin`,
whose README names it), then `patches/` (the 36 fixes, `README.md`), then `features/` in the order of
`features/series`. Each step is its own patch and its own build, so a fault found on the Deluge points to one step.

`tools/setup_beta.sh` with `FEATURES=1` builds it: the tree `$WORK/beta-features` (local branch `beta-features`).
`tests/features/run.sh <ELF> <tools prefix> <blockcount dir>` runs the features' tests; each fails on the build
without the features and passes with them.

## The plan

Ordered by what each costs against what it brings, and by what builds on what. mastertune's patches don't apply to
v1.3 as they are (`git apply --check` fails for all 20 of them): v1.3 rewrote the menus (horizontal menus), the
settings and parts of the audio engine. The conflicts are almost all in the glue (strings, menus, settings, the audio
engine's hooks); the features themselves are mostly new files.

| Step | Feature | mastertune patches | Size | Status |
|---|---|---|---|---|
| 1 | **Arp extras**: latch, ratchet notes (exactly 2 ... 8, roll, ball), ratchet bounce, bounce length and velocity | the latch and bounce part of 0005; 0006, 0021, 0024, 0117, 0120 | ~1,000 lines | done: `features/0001` |
| 2 | **CPU monitor** (the profiler left out) | 0015, 0026, 0039, 0059, 0062, 0069, 0070 (profiler 0053, 0054) | ~1,050 (+670) | done: `features/0002` |
| 3 | **Master tune** | 0001 (its MIDI drum hook as 0005 moved it) | ~650 | done: `features/0003` |
| 4 | **Scan** (tuner, tempo, key) | 0118 | ~1,500, nearly all new | done: `features/0004` |
| 5 | **DJ** (filter, sync, nudge) | 0119 | ~280 | done: `features/0005` |
| 6 | **USB audio** | 0008, 0055, 0112 | ~1,050 | done: `features/0006` |

- The CPU monitor comes early: it is the measuring tool for the steps after it.
- Master tune before Scan, DJ and USB audio: they read it (Scan's cents at 432 Hz, the tuning in a recording).
- USB audio last: it costs CPU all the time and the emulator tests only part of it (descriptors, buffers).
- Settings and song attributes keep mastertune's names, so songs and settings move between mastertune 1.2.1 and this
  build. Stock v1.3 ignores them on loading and drops them at the next save, as stock 1.2.1 does with mastertune's.
- Menus follow v1.3's style (horizontal menus).
- The filters are v1.3's: mastertune 1.2.1's own filter changes (v16 to v19: the resonance 6 dB quieter, parallel
  routing 6 dB quieter, the Drive and crossing changes) are not part of the plan. The DJ filter (step 5) only drives
  the song's LPF and HPF params, so their levels stay as in v1.3.
- The base is tested in the emulator only, like the features; a fault on the Deluge may be the base's.

## Next: steps 7 to 9 (planned 20.11.2026)

What is essential and still missing, after a look at the arp in this build, the community's open work and
mastertune's own plans (no open arp requests in the community's issues; its draft velvet reverb,
SynthstromAudible/DelugeFirmware#4939, has no code).

| Step | What | Source | Size | Status |
|---|---|---|---|---|
| 7 | **Arp freeze**: what the arp plays written into the clip as notes, the arp off, one undo | the community's open PR SynthstromAudible/DelugeFirmware#5208 (commit `9641fbb31`, 25.10.2026), ported | ~1,500 lines, nearly all new | planned |
| 8 | **More note orders**: Converge, Diverge, Up/Down without repeated ends, Thumb, Pinky | own | ~150 | planned |
| 9 | **Velvet reverb, the prototype**: a fourth reverb model, first on the computer | `mastertune-1.2.1/research/VELVET-REVERB-PLAN.md` (branch `claude/wizardly-brahmagupta-nnrk07`), its step 1 | Python only | planned |

**Step 7, arp freeze** (`features/0007`):
- What #5208 does: FREEZE at the end of the arp's Pattern menu (synth, MIDI and CV clips, the kit arp), with a
  confirmation, replaces the notes that feed the arp with the notes it plays and switches the arp off, as one undoable
  action. It runs the real arpeggiator tick by tick over the clip outside playback, on a copy of its settings
  (unsynced arps in samples at the current tempo); reversed and ping-pong clips are written out as they sound; the
  randomizer settings that would act on the frozen notes again are reset. A community feature switches it off.
- The port: the PR's new files (`model/clip/arp_freeze.*`, two consequences for the undo) as they are; the glue as in
  steps 4 to 6 (strings, menus, the community features' list) and the arpeggiator, where step 1 changed the same
  places (the PR adds `isClockActive()` and `getTicksPerPeriod()`). The PR is pinned at its commit; the author is named
  in the patch and here. Nothing is posted to the community.
- Our arp extras: a tick is 1/96 of a quarter (5.2 ms at 120 BPM). Ratchet hits fall on ticks or between them; a
  roll's hits are at least 16 ms (3 ticks at 120 BPM) apart, so each hit becomes a note of its own, rounded to its tick
  (at most half a tick, 2.6 ms at 120 BPM, off). Latch, bounce velocity and the ball come through the real arp.
- Tests (`tests/features/arp_freeze_emu.py`): a clip with a chord, the arp playing it while the song plays (the note-ons
  captured), then FREEZE: the clip's notes are the notes the arp played (pitch, velocity, start within a tick, length
  within a tick); UNDO brings the chord and the arp back. The same with a ratchet roll, a ball, a kit arp and a
  reversed clip. The menu walk visits FREEZE.

**Step 8, more note orders** (`features/0008`):
- New `ArpNoteMode` values after the existing ones (the stored numbers and names stay): **Converge** (lowest, highest,
  2nd lowest, 2nd highest, ...), **Diverge** (from the middle outwards), **Up/Down excl.** (today's Up/Down plays the
  top and the bottom note twice; this one once), **Thumb** (the lowest note between each of the others, going up:
  C E C G C B), **Pinky** (the highest note between each of the others, going up). Each goes through one octave, then
  on as the octave mode says, like Up.
- Saved as `converge`, `diverge`, `upDownExcl`, `thumb`, `pinky`; a firmware without them reads Up there.
- Menus: Note mode for synths and the kit arp; 7-segment `CONV`, `DIVG`, `UPDX`, `THMB`, `PNKY`.
- Tests: a host test of the sequences (1 to 7 notes, each octave mode), an emulator test of the notes played for a
  four-note chord, the menu walk.

**Step 9, the velvet reverb's prototype** (on the computer; no firmware yet):
- The plan's design B: a feedback delay network with a velvet feedback matrix (8 or 16 lines, about 2 to 3 % CPU
  estimated, within the 32,768-sample buffer Mutable and Digital share), Room size as Digital's reverb time, Damping
  as the ratio of high to mid decay, Width, HPF and LPF.
- In `mastertune-1.2.1/research/velvet/` (Python and numpy), compared with Mutable and Digital built for the computer
  from the firmware's own code (as `tests/reverb` builds them): reverb time per octave band, echo density, resonances
  (at most Digital's 11.2 dB), left/right correlation against Width, level within 0.5 dB of Mutable, operations per
  sample and memory. v1.3 has the same three models with the same numbers, so a Velvet would be model 3 in both.
- Demo WAVs (drums, a held chord, a voice) through Mutable, Digital and Velvet go to the user to listen to.
- Go or no-go by ear: only if Velvet sounds better than Digital at no more than its cost does the firmware follow
  (the plan's step 2, for mastertune 1.2.1 and this build).

## Step 1: the arp extras (`features/0001`)

mastertune's arpeggiator is v1.3's own, backported to 1.2.1 in mastertune's 0005, so the engine came over as a
three-way merge of `modulation/arpeggiator.{cpp,h}`: base mastertune's arp right after that backport, one side v1.3's
arp, the other mastertune's final arp. The base was first given what v1.3 added since the backport (a note's
`sampleSyncLength`, `song_firmware_version`, the "mode" attribute as v1.3 writes it), so the merge brought over only
mastertune's own changes; one conflict (`doTickForward()`, a ratchet that lasts several steps) was resolved keeping
v1.3's order (the step's `sampleSyncLengthOn` set before the note is switched on).

- **Latch** (arpeggiator menu, after Mode; not for a kit row, for the kit arp): the arp keeps playing the notes after
  the keys are let go; the next key pressed after all were let go replaces them without the arp losing time.
  Switching it off stops what's latched.
- **Ratchet** (a new horizontal menu after Pattern, for synths, kit rows, the kit arp, MIDI and CV):
  - *Ratchet notes*: Auto (2, 4 or 8 at random, as v1.3), exactly 2 ... 8, Roll (hits until the plates close, at least
    16 ms apart) or Ball (a ping-pong ball between plates that meet after the first step and part again).
  - *Ratchet bounce* -10 ... +10: each gap the one before times r; 0 is even, as in v1.3.
  - *Bounce length* 1 ... 16: how many arp steps a ratchet lasts.
  - *Bounce velocity*: Even, Fade (softer as the gaps get shorter) or Rise (louder).
- Fixes that came with them from mastertune: ratchet hits that fell behind are dropped instead of pushing the next step
  back, a new step drops what is left of the last step's ratchet, and an arp without settings plays plain notes.
- **A fault mastertune 1.2.1 has too**, found by the review of this port and fixed here: notes the latch held after
  their keys were let go stayed in the arp's note list, marked to start (every note the arp plays is), so once the arp
  was off (its mode, a preset, or a clip of the same instrument without the arp) they started as held notes that no
  note-off would ever end. On a kit arp switched off from its menu (which doesn't cut the sound there) the latched
  rows sounded for good; on a CV clip the gate stayed open. Now such a note is marked when its key is let go, and
  dropped once the arp is off.
- Saved as `latch`, `ratchetNotes`, `ratchetBounce`, `ratchetBounceLength`, `ratchetBounceFade`, as mastertune saves
  them.

**Tested** (`tests/features/arp_extras_emu.py`, the songs of mastertune's own `tests/arp/arp_roll_test.py`, on the v1.3
rig: the firmware's own task manager, the audio DMA in real time):
- **9 ratchet cases** (rolls accelerating, decelerating and even, fixed counts over one and two steps, 8ths and 16ths,
  three balls): in each the first ratchet has exactly the formula's number of hits (3 to 24), each 0 to 28 samples
  after the formula's time (within one audio window), never before; the velocity follows the gaps as Even, Fade or
  Rise say.
- **Latch**: on, the first key's note goes on for the half bar after it was let go (8 sixteenths) and the second key's
  note replaces it; off, the arp stops with the key (as v1.3).
- **Latch, then the arp off** (the arp mode written as the kit arp's Mode menu leaves it): no note starts from then on.
  Without the fix the latched note starts as a held note in the very next render.
- **On the build without the features** (the fixes alone): 10 of the 11 ratchet and latch cases fail (a ratchet has
  v1.3's 1 to 4 even hits, the latch doesn't hold); latch off passes there too, as it should.
- No crash, freeze or hang in any case.
- **The menus** (`tests/menu_walk_emu.py`, synth, kit with affect entire, kit row, MIDI and CV; again with the
  horizontal menus on): every new item visited (Latch, the Ratchet menu and its four items), each value turned with
  the select and horizontal encoders and a gold knob, left in every way the walk has; about 6,800 inputs, no crash,
  freeze, hang, null write or wild access.
- **The fixes' test of the arp** (`tests/repro/mpe_arp_noteoff_emu.py`, 0017) still passes.
- **The build** (`FEATURES=1 tools/setup_beta.sh`) gives the same file twice in the same folder (SHA-256
  `d12eee68...`); its tree is the one the tests ran on.

## Step 2: the CPU monitor (`features/0002`)

mastertune's CPU monitor without its sampling profiler. The core (`processing/engines/cpu_stats_core.{h,cpp}`: the
collector, the OLED line, the SysEx encoding) is mastertune's file unchanged; the firmware side (`cpu_stats.{h,cpp}`)
and the hooks are fitted to v1.3 (its audio routine, voice culling, cluster loading, OLED, MIDI cables, settings file
and task scheduler).

- **Settings > CPU Monitor**: Off, On, Alerts. On: one short line in the OLED's top left corner, `CPU 43% 24V`, with
  `QL` while the quality is lowered and a blinking `VC` while voices are cut; a popup on the 7-segment display; the
  numbers once a second as SysEx (command 0x10) to the computer on USB port 3 (`tools/cpu_monitor.html`,
  `tools/deluge_profiler.py` read them). Alerts: only `QL` and `VC`. v1.3's own High CPU Indicator stays as it is.
- **LEARN held + tempo encoder pressed** switches it off and back on (the last of On and Alerts) in every view; the
  view and the tempo popup don't get that press. With Shift the press clears the tempo automation, as ever.
- **Kept across a restart** as `cpuMonitor` in the community settings file (mastertune's name; its Profile is read as
  On). The shortcut's save runs in a task of its own that holds the SD card's resource, and keeps the startup song
  as it was, so a song loaded but not saved doesn't become the one "Last saved" opens.
- The line goes on top of the finished image in a canvas of its own, so no screen's image is changed; not over the
  screensaver.
- **Two faults found by the review, both only in the numbers** (no effect on the sound, the card or USB):
  - A voice the CPU guard had just stopped (OFF until it next renders) could be picked and counted again in the same
    pass: now a voice is counted only if it isn't OFF yet. (v1.3 itself picks such a voice again for its next
    kill or fast release; that is upstream's and stays as it is.)
  - While a stem export rendered offline the collector kept its window open, so the first window after the export
    counted the whole export as one gap of the audio routine, and the line stayed up, frozen, over the export.
    mastertune 1.2.1 has this too. Now the monitor pauses while an export renders offline.

**Tested:**
- **The core** (`mastertune-1.2.1/tests/cpu_stats`, host test, on this tree): the collector with a simulated timer
  (and its wrap), the OLED line and the SysEx encoding, decoded by `tools/cpu_monitor.html` and
  `tools/deluge_profiler.py`: 503 messages, all fields, rejects.
- **On the v1.3 rig** (`tests/features/cpu_monitor_emu.py`, the fuzzer's card, playing): off at first; LEARN + tempo
  encoder switches it on (the popup says so, session view stays); after 3 s the line reads e.g. `CPU 71% 21V QL`
  (load 1 to 99 %, the voices within the range the audio engine had), the image sent to the OLED has it in the
  corner (102 pixels set there, 0 once off), 3 SysEx messages in 3 s; the shortcut switches it off and on again; a
  second boot from the same card starts with it on. Then a stem export rendered offline (18 s of emulated time): the
  window after it has its longest gap under 50 ms (1.1 ms); without the fix 17,899 ms, the export itself.
- **The menus**: the walk through Settings (and the synth's menus) visits CPU Monitor; no problem.
- The double count is fixed by the review's reading of the code; the rig has no way to make the CPU guard kill a voice
  on purpose, so it isn't tested apart.

## Step 3: master tune (`features/0003`)

mastertune's master tune: `model/tuning/master_tune{.h,.cpp,_math.h}` are mastertune's files with the migration from
the flash taken out; the hooks are fitted to v1.3 (its voice, audio clip, CV engine, MIDI output, recorder, WAV reader,
sample browser, settings file and task scheduler).

- **Settings > Tuning > Master tune (Hz)**: the reference pitch of A4, 415.3 to 466.2 Hz in steps of 0.1 Hz (the select
  encoder 1 Hz a detent, the cursor moved for 0.1). Kept as `masterTune` (tenths of Hz) in the community settings file,
  as mastertune keeps it.
- **Everything that makes sound follows it**: synth and kit voices with every oscillator type (sample, wavetable, the
  basic waves, FM carriers and modulators, DX7), audio clips (pitch-shifted, their length kept), the CV outputs, and
  external MIDI by RPN 1, Channel Fine Tuning (before a note on a channel that hasn't got the current tuning; to every
  channel the song plays on when the tuning changes and when playback starts). Also v1.3's own MIDI output of a synth
  or kit row (its MIDI menu's channel), which mastertune 1.2.1 doesn't have. Held notes retune at once.
- **Recordings remember their tuning**: audio recorded on the Deluge (recording, resampling, stem export) at another
  tuning gets an `mtun` chunk (the tuning in tenths of Hz) before its data, so it is only shifted by the difference
  later and never tuned twice; other software skips the chunk. Live input used as an oscillator is left alone.
- At 440.0 Hz everything not recorded at another tuning plays as without it.
- **Three faults found by the review**, all v1.3's own interplay:
  - Settings > Actions > Reset settings (community features, or all) left the tuning, and the CPU monitor's mode
    (step 2), as they were, and leaving the menu wrote them straight back into the file: both now go back to their
    defaults.
  - A CV instrument whose CV2 carries an expression (v1.3: aftertouch, mod or velocity) would have had that voltage
    replaced by an old pitch at a tuning change: only a channel whose last voltage was a pitch is re-sent.
  - A value out of range in the file (`440`, Hz instead of tenths) gave 415.3 Hz, as in mastertune 1.2.1: now it is
    ignored (440 Hz).
- Left out: the migration from the flash bytes 198-199 of early master-tune builds. In v1.3 they are the next free
  bytes of the flash settings, and the tuning has been in the settings file since mastertune v16.

**Tested:**
- **The arithmetic and the WAV header** (mastertune's host tests on this tree: `master_tune_math_test.cpp`,
  `wav_mtun_chunk_test.py`): the ratio, the cents, RPN 1 at 415.3/440/466.2 Hz (0/8192/16383), the phase increments
  (worst error 0.001 cents), a recorded tuning; the recorder's header with and without the chunk, read by the firmware's
  chunk walk and by other readers.
- **On the v1.3 rig** (`tests/features/master_tune_emu.py`, a card of its own: the song of mastertune's retune test and
  a sine synth, an FM synth, a kit row playing a file recorded at 432 Hz, a MIDI and a CV track; the settings file at
  432.0 Hz), playing:
  - **The pitch of every part in the output** (the phase slope of its band, 4 bars): 15 tones, from a 48 kHz file, an
    AIFF, a loop, a transposed row, a time-stretched row, the sample synth, the multisample, the audio clip, the sine,
    the FM carrier and its first sideband (where an untuned modulator would put it elsewhere): each within 0.02 cents of
    432/440 of its tone at 440 Hz; the file recorded at 432 Hz at its own pitch.
  - **MIDI**: RPN 1 with 5590 (-31.8 cents) on the MIDI track's channel before its first note.
  - **CV**: the note's voltage 19483 (19656 at 440 Hz), as the firmware's formula gives it.
  - **Retuned while playing**, through Settings > Tuning > Master tune and the select encoder (+14 detents: 446.0 Hz):
    every part within 0.05 cents in the 2 bars after (the file recorded at 432 Hz now 446/432 up); the CV voltage
    re-sent at once (19784); RPN 1 with 10113 to the MIDI channel.
  - **A stem export** at 446.0 Hz: every WAV it wrote has the `mtun` chunk with 4460, its audio where the `data` chunk
    says (byte 124), read by `soundfile` as any WAV.
  - **A second boot** from the same card starts at 446.0 Hz; then **a settings reset** brings it back to 440.0 Hz and
    the CPU monitor to off.
  - No crash, freeze or hang.
  - **On the build without it** (step 2): every part 31.8 cents off (at 440 Hz), no RPN, the CV voltage that of
    440 Hz: the test fails, as it should.
- **The menus**: the walk through Settings (`tests/menu_walk_emu.py`, 963 inputs, 169 items) visits Tuning and Master tune; no
  crash, freeze, hang, null write or wild access.
- **The other features' tests** (`tests/features/run.sh`: the arp extras, the CPU monitor) pass on this build too.
- **The build** (`FEATURES=1 tools/setup_beta.sh`) gives the same file twice in the same folder (SHA-256
  `dfb86b62...`); its tree is the one the tests ran on.
- The CV2 expression case is fixed by the review's reading of the code; the rig has no CV instrument in that mode.

## Step 4: Scan (`features/0004`)

mastertune's Scan view: `dsp/scan/scan.{h,cpp}` and `gui/views/scan_view.{h,cpp}` are mastertune's files (the view
without the name override v1.3's UI doesn't have, its menu item's `isRelevant()` const as v1.3 declares it). The audio
routine feeds the analysis the input frames the recorders get; the item is the second of Settings > Tuning.

- **Settings > Tuning > Scan** (from Song view): the note with its cents against the master tune (so at 432 Hz too), its
  frequency, the tempo in BPM and the key, major or minor, with its Camelot number. Select encoder: the input (Auto,
  Left, Right, Stereo); pressed: what is big (note, Hz, BPM, key). Horizontal encoder: the BPM halved or doubled. The
  pads are the needle, the sidebar the input's level. BACK, SONG or SCALE: back to Song view, the analysis freed.
- **Three faults found by the review**, two of them v1.3's interplay:
  - v1.3 leaves a menu with SONG straight to Song view (`changeRootUI()`), also a menu opened over the Scan view
    (SHIFT + select): the view never knew, so the audio routine went on feeding the analysis and its 95 KB stayed
    allocated until Scan was opened and left again. A song loaded from the view did the same (as in mastertune 1.2.1).
    Now `changeRootUI()` and `nullifyUIs()` release it when the Scan view stops being the root UI.
  - On the 7-segment display the analysis wrote its note over a menu opened on top of the view (v1.3 keeps calling the
    view's graphics routine under the menu): it now shows only while the view is the current UI.
  - The debug builds' table of UI names had no entry for the new UI type.

**Tested:**
- **The analysis** (mastertune's host test `tests/scan`, on this tree): pitches, tempos and keys of the test material
  (every key right, 24 of 24, at 432 Hz, at 440 Hz with drums, and analysed every 116 ms), silence, noise and a held
  note without a key; 0.4 % of real time on the computer.
- **On the v1.3 rig** (`tests/features/scan_emu.py`, the fuzzer's card; a tone of 861.33 Hz in the input ring): the
  menu opens the view, the analysis is the audio engine's; at 440 Hz "A4 -37.1" and "861.32 Hz  -- BPM"; SHIFT + select,
  Master tune turned to 432.0 Hz, BACK: "A4 -5.4" with "432" in the title; Hz, BPM ("-- BPM" for a steady tone, the
  tempo analysis ran) and key ("--" for a held note) in turn; a C major chord and the input turned to Left: "C major",
  "Camelot 8B" after 4 s; BACK: Song view, the analysis freed; again with Settings over the view left by SONG: freed
  too (the fault above). No crash, freeze, hang or wild access.
- **The menus**: the walk through Settings and the song menu (`tests/menu_walk_emu.py`, about 1,400 inputs, 233 items)
  visits Tuning > Scan (which opens the view and leaves the menu) and comes back; no problem, before the review's fixes
  and on the release build.

## Step 5: the DJ tool (`features/0005`)

mastertune's DJ filter, sync and nudge: `dsp/dj/dj_filter.h` is mastertune's file; the glue merged with v1.3 (the end
of a nudge is checked in `PlaybackHandler::routine()`, as v1.3 moved the MIDI input into `midiRoutine()`).

- **DJ filter**: the song's filter type DJ, after LPF, HPF and EQ when the upper gold knob is pressed (Song view with
  AFFECT ENTIRE, the filter row); clips and sounds go round as before. **It adds no filter of its own**: the upper knob
  drives the song's own LPF and HPF cutoff params as their own knobs set them (left of the middle the low-pass closes,
  right of it the high-pass opens, in the middle both are off; where they were set apart a detent goes on from there
  without a jump), the lower knob both resonances. So the filters' sound and levels are v1.3's. Popups "DJ: LPF 16",
  "DJ: OFF", "DJ: HPF 16". Saved as `currentFilterType="dj"`; a firmware without it loads LPF there.
- **Sync** (Scan view): TAP TEMPO sets the song's tempo to the BPM shown, undoable; "No tempo yet" before a tempo is
  heard, "External clock" where one sets it.
- **Nudge**: the horizontal encoder held and the tempo encoder turned bends the internal clock for 0.25 s (4 % a
  detent, about 10 ms at any tempo), then the tempo is exactly what it was; STOP ends it at once; a song saved while
  bent saves the unbent tempo. SHIFT keeps v1.3's nudge of the MIDI clock out.

**Tested:**
- **The knob's curve** (mastertune's host test `tests/dj`, on this tree): every position, a detent at all 129, and no
  jump from any LPF and HPF knob set apart (33,282 starts).
- **On the v1.3 rig** (`tests/features/dj_emu.py`, the fuzzer's card):
  - The filter types go round LPF, HPF, EQ, DJ; 20 detents left: the LPF's cutoff exactly at knob 24, the HPF off,
    "DJ: LPF 16"; 40 right: the LPF off, the HPF at knob -24, "DJ: HPF 16"; 20 left: "DJ: OFF", both off; the lower
    knob 10 right: both resonances 10 up; pressed once more: LPF.
  - **The levels against v1.3's own filter knobs**: the four params (both cutoffs, both resonances) bit for bit the
    same with DJ at OFF as with the LPF opened and the HPF closed by their own knobs, and with "DJ: LPF 16" as with the
    LPF's own knob at 24. The filters compute their output from these params alone, so this is the decisive check.
    Coarser, the output over the song's first 3 s (played from its start each time), on the release build: with the
    LPF open -18.72 dBFS by its own knobs, -19.13 with DJ at OFF; at knob 24 -19.11 and -18.73; above 3 kHz -36.25 and
    -36.18, -49.45 and -49.12. The test song's free-running oscillators vary the same setting by up to 0.4 dB from run
    to run (the run before gave +0.18 and -0.09 dB the other way round), so the output is only compared within 0.5 dB;
    the low-pass at 24 takes 13 dB above 3 kHz, as it should.
  - Sync from the Scan view: "No tempo yet" without a tempo, the tempo kept; the analysis at 128 BPM: "Sync 128.0 BPM",
    the song at 128.0000 BPM.
  - The nudge, playing: a detent right makes the time per tick exactly 4 % shorter ("Sync nudged"), 0.4 s later it is
    what it was; left 4 % longer, then back; a nudge then STOP: back at once.
  - No crash, freeze, hang or wild access.

## Step 6: USB audio (`features/0006`)

mastertune's USB audio: `io/usb/usb_audio*`, `usb_song_info*` and the changes to the Renesas USB driver are
mastertune's (they applied unchanged); the glue is fitted to v1.3 (the community features read before USB starts, the
frames pushed from v1.3's output buffer, the song info on v1.3's USB MIDI cable 3).

- **Settings > Community features > USB audio** (off by default, read at boot): the Deluge enumerates as a USB Audio
  Class 1.0 device next to its MIDI interface, stereo, 24 bits, 44.1 kHz; the computer records exactly what goes to the
  outputs. Empty packets the computer got show "USB audio gap", at most every 10 s. Nothing during an offline stem
  export.
- The setting is saved at once ("Restart to apply"), so a restart from the menu keeps it; that save keeps the startup
  song as it was (as the CPU monitor's save), so a song loaded but not saved doesn't become the one "Last saved" opens.
  (Leaving the menu writes the file as v1.3 does.)
- While the computer has the stream open, the song's name and the firmware go to USB MIDI port 3 as SysEx 0x12 (JSON,
  packed 7 bits in 8), for DelugeRec.

**Tested** (the emulator has no USB host: nothing enumerates the Deluge or opens the stream):
- **The descriptors and the ring** (mastertune's host tests `tests/usbaudio`, on this tree): the descriptors against
  the USB 2.0, USB Audio Class 1.0 and USB MIDI rules; the ring and the packet sizes against clock drift, a computer
  that stops reading and an engine stall, with the sanitizers.
- **On the v1.3 rig** (`tests/features/usb_audio_emu.py`): off without a settings file; switched on as the menu does:
  "Restart to apply", `SETTINGS/CommunityFeatures.XML` written at once with `usbAudio` 1 and the startup song left as
  it was; a second boot reads it before USB starts (on); the stream opened as `usbAudioStart()` opens it, playing: the
  2,048 frames in the ring are the frames sent to the codec, bit for bit and in order; the song info goes out on cable 3
  as `{"song":"","fw":"1.3.0-beta-..."}` (the song from the template was never saved). No crash, freeze, hang or wild
  access.
- **The menus**: the walk through Settings visits Community features > USB audio; no problem.
- **Not tested here**: the enumeration on a computer, the recording itself and MIDI next to it. That needs the Deluge
  (mastertune 1.2.1's device tests did it for the same driver code).

## The build with all six steps

`deluge-1.3.0-mastertune-s6-b1a91c9b.bin` (`README.md`): `tests/features/run.sh` on it runs mastertune's host tests on
its tree (the CPU monitor's core, the master tune arithmetic and WAV header, Scan's analysis, the DJ knob, USB audio's
descriptors and ring) and the six emulator tests; all pass. Two runs of `FEATURES=1 tools/setup_beta.sh` (a fresh tree
each time, in the same folder) give the same file, SHA-256 `c0a02c6b...`. A rebuild of the existing build folder after
its `clean` target gives a file of the same size with about 40 KB of code laid out differently; the file to compare
with is the one from `setup_beta.sh`.
