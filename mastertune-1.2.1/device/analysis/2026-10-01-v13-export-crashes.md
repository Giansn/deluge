# Export crashes #4639, #4471, #3309, 01.10.2026

Three open reports:
- **#4639** (beta 2026-07-04): stem export crashes after a few songs.
- **#4471** (1.3): an offline mixdown of a long arrangement crashes.
- **#3309** (1.2): RECORD + PLAY over the whole song stops with **M123** after about a minute.

## Cause: a SampleRecorder freed twice when a recording aborts

M123 means a block was freed twice (`memory/memory_region.cpp:214-216`). The chain in mastertune's source, which is 1.2.1's:

1. Every SD wait yields to the task manager (`deluge.cpp:981-1017`). 1.2.1's scheduler neither gates tasks by resource nor keeps a task from running again inside its own yield (`OSLikeStuff/task_scheduler.cpp:365-397`).
2. When the recorder gets no new cluster (RAM), `feedAudio()` calls `abort()` (`model/sample/sample_recorder.cpp:916-924`). The aborted branch then deletes the file, which means more SD waits (`:313-366`).
3. The task "audio recorder slow" (`deluge.cpp:621`) runs `AudioRecorder::slowRoutine()` unguarded. Status ≥ COMPLETE includes ABORTED, so it calls `finishRecording()` → `discardRecorder()`, which frees the recorder at once (`gui/ui/audio_recorder.cpp:179-186, 241-246`, `processing/engines/audio_engine.cpp:2076-2096`).
4. If that happens inside an SD wait, the block is freed a second time, in one of two ways:
   - The recorder's suspended `cardRoutine()` resumes on freed memory, and `doRecorderCardRoutines()` frees it again (`audio_engine.cpp:1967-1971`). This is upstream's analysis.
   - The destructor itself waits for the card, and the same task runs again before `recorder` is cleared. This is what the emulator showed (below).
5. Offline export can also hang: it drains the recorder in the audio routine without a null check or a bound (`audio_engine.cpp:1371-1375`). After a card error, `cardRoutine()` never advances, so the loop never ends.

It gets likelier with offline rendering (#4471: every cluster write yields), long recordings (#3309) and repeated exports (#4639: earlier stems stay cached and RAM gets tighter; plausible, not proven).

**Beta `62a516c2` has upstream's fix** `9c3f9a70` (#4671, 12 July). #4639 and #4471 were reported on builds before it.
- `slowRoutine()` returns while `sdRoutineLock || currentlyAccessingCard` (`gui/ui/audio_recorder.cpp:181-189`), and is registered as `RESOURCE_SD | RESOURCE_SD_ROUTINE` (`deluge.cpp:586-587`).
- `discardRecorder()` freezes with E251 when called from inside the SD routine (`audio_engine.cpp:1619-1622`).
- The drain is null-checked and bounded (`audio_engine.cpp:1083-1110`).

## What survives from one export to the next

In `StemExport` (`processing/stem_export/stem_export.cpp:53`, beta):
- **Reset every export:** `processStarted`, the export type and the file counter (`:101`); the counts, in `disarmAll*()`.
- **Reset every stem:** the silence timers (`:167`) and `stopRecording` (`:188`/`:211`).
- **Never reset:** the folder number and name and the WAV name. They are only names, though.
- **Restored even on cancel:** mutes and solos.

Outside it, these carry over:
- `audioRecorder.recorder` and `recordingSource` (cleared only by `finishRecording()`);
- the `firstRecorder` list;
- the RAM taken by earlier stems.

## Repro

- **Buttons:**
  - In Song view, `SAVE` held + `RECORD` exports clip stems (`session_view.cpp:391-399`), offline by default (`stem_export.cpp:70`). In arranger view it exports tracks.
  - `RECORD` held + `PLAY` is #3309's case (`hid/buttons.cpp:182-184`).
  - fuzz_ui.py has no step for one button held while another is pressed.
- **On the device:** export a long, sample-heavy song several times in a row.
- **In the emulator:** `tests/export/export_abort_emu.py` makes `SampleRecorder::createNextCluster()` return INSUFFICIENT_RAM on chosen calls.
  - **mastertune v19.0:** with an SDHC-like card (1000,42.7 µs, waits yield) and failures at calls 3 and 40, it stops with **M123 at 1.735 s**. The trace:
    1. The task runs `finishRecording()`.
    2. `~SampleRecorder()` reads the card (`move_window` → `disk_read`), and that wait yields.
    3. The same task runs again (`sdRoutineLock=1`) and destructs and frees the same recorder (`0x202f6890`) a second time.
  - **Earlier near miss:** at the first abort, `finishRecording()` already ran inside an SD wait, but did not crash.
  - **With an instant card** (no yields), both aborts are clean and the export goes on.

## mastertune

All of it is in mastertune unchanged (offline stem export, the unguarded task, the unbounded drain, arranger recording), so all three symptoms can happen there.

To fix: port `9c3f9a70`.
- With no resource gate in 1.2.1, the guard in `slowRoutine()` is the fix there; `currentlyAccessingCard` exists in 1.2.1.
- Also port the null-checked, bounded drain, and E251 in `discardRecorder()`.
- `export_abort_emu.py` should then export all stems.
