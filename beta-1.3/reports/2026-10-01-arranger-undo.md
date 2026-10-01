# Seed 109: undo in the arranger's automation view enters a Clip that isn't there (patch 0106), 01.10.2026

The fuzz campaign's seed 109 (`--mode deep --7seg`, 62a516c2 with 0001-0009 and 0101) froze with E369 (no timeline counter) between inputs 202 and 203. It came from `ModelStackWithNoteRow::getLoopLength()`, in a replay from `InstrumentClipView::graphicsRoutine()`.

## Finding

The replay watched writes to `automationView.onArrangerView` and traced `changeRootUI()`:

| Input | What | Traced |
|---|---|---|
| 198 | `RECORD held + SONG` | `changeRootUI(arrangerView)`: the arranger, `onArrangerView` 0 |
| 201 | `SHIFT held + CLIP` | `ArrangerView::buttonAction()` sets `onArrangerView` to 1 and opens the automation view (`arranger_view.cpp:225`) |
| 203 | `SHIFT+BACK` (redo; undo takes the same path) | `ActionLogger::revert()` → `changeRootUI(instrumentClipView)`, then E369 in its `graphicsRoutine()` |

The cause is in `ActionLogger::revert()` (`src/deluge/model/action/action_logger.cpp`):
- **The choice:** an action from another view, undone while the automation view is open, counts as `EXIT_AUTOMATION_VIEW` (`:433`).
- **The jump:** that branch goes into the current Clip's view: the instrument clip view if `getCurrentClip()->type` is INSTRUMENT (`:625-628`). It doesn't ask whether this is the arranger's automation view (`onArrangerView`), which belongs to no Clip.
- **No Clip:** after a song load there is often no current Clip at all. `getCurrentClip()->type` read through null (0 is INSTRUMENT), and the instrument clip view opened without a Clip.
- **The writes and the freeze:** `InstrumentClipView::openedInBackground()` (`instrument_clip_view.cpp:139`) wrote through the null Clip. The fuzzer keeps the null page writable, so seed 109's null writes include these. Then `graphicsRoutine()` (`:7170`) set up its model stack with no Clip: E369. A release build dereferences null instead.

A user gets there with ordinary inputs: anything undoable in the arranger (e.g. a tempo change), then CLIP, then BACK. This is a real bug, not a harness artifact.

## Fix: patch 0106

From the arranger's automation view, `EXIT_AUTOMATION_VIEW` goes back to the arranger. The patch adds 5 lines.

The dev session's 0013 fixes a neighbour in the same view: the clip-type buttons in `AutomationView::buttonAction()`. 0106 covers undo and redo, which go through `ActionLogger::revert()` instead.

## Tests

`tests/repro/arranger_undo_emu.py`, now in `run.sh`:

| Check | up to 0105 | + 0106 |
|---|---|---|
| (1) SONG (arranger), tempo +4, CLIP, BACK (undo) | crash: a write through the null Clip in `InstrumentClipView::openedInBackground()` | the arranger again, the tempo undone |
| Result | FAIL (1) | PASS |

Seed 109 was replayed to input 240 on the whole series. Inputs 195-202 and their views are the same as before. Input 203 goes back to the arranger, and up to 240 there is no crash, freeze or hang.

One replay on an earlier build of the same tree, which differed only in its version string, crashed elsewhere at input 192. There `pad 17,5 held + MIDI` in the sound editor over performance view reached `ArrangerView::handleAuditionPadAction()` → `endAudition()` with a garbage `Output*`. That depends on the memory layout and is a separate bug, still open.

`tests/repro/run.sh` on the whole series as `nightly` has it (0001-0016, 0101-0103, 0105, 0106, applied with `git am` to 62a516c2): 18 of 18 PASS.

## Status

Runs: seed 109 replayed six times (traced, with a write watch, to 240 on the fix, three for the crash at 192), arranger_undo_emu.py before and after, run.sh three times. Inputs: about 1,250 replayed. Problems: 2 found (undo into a Clip that isn't there, fixed by 0106; an arranger audition pad on a garbage `Output*`, open).
