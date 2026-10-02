# Seed 122: a new track in the grid layout is a false hang (the fuzzer delivers the pad's release now), 01.10.2026

The stop run's seed 122 (`--mode deep`, OLED, the series up to 0106) hung at input 736, `pad 8,4 100`: the press was not back after 10 s of the Deluge's time. Replays on the series and with 0107 hung the same way.

## Finding

The replay's backtrace: `MatrixDriver::padAction()` → `SessionView::gridHandlePads()` → `SessionView::gridCreateClip()` → `TaskManager::yield()`. The task manager kept running, and the UI mode was 29, `UI_MODE_CREATING_CLIP`.

The firmware waits here on purpose:
- **The grid layout:** song view showed its grid, and pad 8,4 lay in the empty column right of the last track. Such a press starts a new track (`src/deluge/gui/views/session_view.cpp:3736`).
- **The wait:** `gridCreateClip()` waits, inside the press, until a type button or the pad's release ends `UI_MODE_CREATING_CLIP`: `yield([]() { return currentUIMode != UI_MODE_CREATING_CLIP; })` (`:3740`).
- **On the Deluge:** `yield()` keeps the other tasks running (`src/OSLikeStuff/task_scheduler/task_scheduler.cpp:311-318`), and so does "buttons and pads" (`deluge.cpp:567`). That task reads the release from the PIC and hands it to `matrixDriver.padAction()` (`deluge.cpp:289`). The release path calls `exitTrackCreation()` (`session_view.cpp:4087`), the wait ends, and the new track gets its clip.
- **In the fuzzer:** the inputs are calls, not PIC messages. The release would come only after the press returned, so the press never returned.

This is a harness artifact, not a firmware bug. It is the same kind as the audio recorder's own loop (ModalRecorder) and a song load while LOAD is held (QuickLoadTap). 1.2.1 has the same wait (mastertune's `session_view.cpp`), so its fuzz runs could hit it too.

## Fix (harness)

`tests/fuzz_ui.py` has a new `GridTrackCreation`. Once `UI_MODE_CREATING_CLIP` has lasted 0.5 s inside a pad's press, the next run of the "buttons and pads" task (`readButtonsAndPadsOnce()`) delivers that pad's release instead: `MatrixDriver::padAction(x, y, 0)`, as `readButtonsAndPads()` would. The fuzzer's own release of that pad afterwards is left out, because the PIC sends only one. The run's `fuzz.json` counts these releases (`track_releases`).

## Check

Seed 122 was replayed to input 800 on the series up to 0106, with the fuzzer's fix and `gridCreateClip()` and `exitTrackCreation()` traced:

| Input | What | Traced |
|---|---|---|
| 736 | `pad 8,4` (held) + `turn mod1 -1` | `gridCreateClip(…, nullptr, …)`: a new track; 0.50 s later `exitTrackCreation()` from the release |
| 737-800 | song view and clip views | no problem |

Before the fix, the same replay hung at 736.

## Status

Runs: seed 122 replayed four times (the series, with 0107, with a backtrace, with the fix to 800). Problems: 1 (a hang at input 736), a harness artifact, fixed in `tests/fuzz_ui.py`. The 2-hour stop run #2 runs with the fix.
