# Seed 176: undo of an edit in the arranger's automation view goes into a Clip that isn't there (patch 0113), 02.10.2026

Stop run 6's seed 176 (`--mode deep`, OLED, 31 patches) froze with E369 (no timeline counter) at input 354, `BACK held` in arranger view. It came from `ModelStackWithNoteRow::getLoopLength()`, called from `InstrumentClipView::renderMainPads()`. A replay froze at the same input.

## Finding

The replay traced Actions, `ActionLogger::revert()` and `changeRootUI()`:

| Input | What | Traced |
|---|---|---|
| 344-350 | in the arranger's automation view: a knob turn, then automation recorded while playing | Actions (`getNewAction()`), each recording `view = automationView` |
| 353 | CLIP | back to the arranger |
| 355 | BACK | `revert()` → `changeRootUI(&instrumentClipView)`, then E369 in its rendering |

The cause is in `ActionLogger::revert()` (`src/deluge/model/action/action_logger.cpp:396-418`):
- **The record:** an Action records the view it was made in (`getCurrentUI()`, `:195`). The arranger's automation view is `automationView`, the same view as a Clip's automation view.
- **The choice:** `automationView` is a ClipMinder. Undone from arranger view, its Action counts as "back into a Clip view" (`ARRANGEMENT_TO_CLIP_MINDER`, `:417`). Redone from song view, it counts as `SESSION_TO_CLIP_MINDER` (`:409`).
- **The jump:** that branch enters the current Clip's view (`:638-650`). After a song load there is no current Clip. The instrument clip view opened without one: a write through null in `openedInBackground()`, then E369 in its rendering. A release build dereferences null.

This is the same mistake 0106 fixed for undo *inside* the arranger's automation view (`EXIT_AUTOMATION_VIEW`), here on the way back from the arranger and from song view. A user gets there with ordinary inputs: load a song, then SONG, CLIP, a knob turn, CLIP, BACK. This is a real bug, not a harness artifact.

## Fix: patch 0113

An Action also records whether its view was the arranger's automation view (`viewIsArrangerAutomation`). Such Actions count as the arranger's:
- They never count as a Clip view's.
- From song view they go to the arranger.
- From arranger view they go back into the arranger's automation view (`ENTER_AUTOMATION_VIEW`, which now sets `onArrangerView` from the Action).

The patch changes 3 lines and adds 9, in 2 files.

## Tests

`tests/repro/arranger_automation_redo_emu.py`, now in `run.sh`. The startup song is loaded, so there is no current Clip:

| Check | up to 0112 | + 0113 |
|---|---|---|
| (1) SONG, CLIP, pad 8,7 (LPF frequency), mod1 −2, CLIP, BACK (undo) | crash: a write through null in `InstrumentClipView::openedInBackground()` | the arranger's automation view again |
| (2) song view, SHIFT+BACK (redo) | (not reached) | the arranger |
| Result | FAIL (1, 2) | PASS |

`tests/repro/run.sh` on the whole series as `nightly` had it with 0113 (0001-0022, 0101-0103, 0105-0113, 34 patches, applied with `git am` to 62a516c2): 30 of 30 PASS.

## Status

Runs: seed 176 replayed four times (the freeze, a backtrace, Actions and `revert()` traced, and with 0113 through input 400), `arranger_automation_redo_emu.py` before and after, `run.sh`. Problems: 1 (undo or redo of an arranger automation edit into a Clip's view), fixed by 0113.
