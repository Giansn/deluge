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
| 3 | **Master tune** | 0001, its hooks in 0002 and 0005 | ~650 | next |
| 4 | **Scan** (tuner, tempo, key) | 0118 | ~1,500, nearly all new | |
| 5 | **DJ** (filter, sync, nudge) | 0119 | ~280 | |
| 6 | **USB audio** | 0008, 0055, 0112 | ~1,050 | |

- The CPU monitor comes early: it is the measuring tool for the steps after it.
- Master tune before Scan, DJ and USB audio: they read it (Scan's cents at 432 Hz, the tuning in a recording).
- USB audio last: it costs CPU all the time and the emulator tests only part of it (descriptors, buffers).
- Settings and song attributes keep mastertune's names, so songs and settings move between mastertune 1.2.1 and this
  build. Stock v1.3 ignores them on loading and drops them at the next save, as stock 1.2.1 does with mastertune's.
- Menus follow v1.3's style (horizontal menus).
- The base is tested in the emulator only, like the features; a fault on the Deluge may be the base's.

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

