# Seed 260: undo into a closed sound editor (patch 0118), 02.10.2026

Stop run 11's seed 260 (`--mode deep`, OLED, 42 patches) crashed at input 461, `BACK` in a clip view: an invalid instruction at 0xc0000fc, reached from `SoundEditor::beginScreen()`+0x15. A replay crashed at the same input.

## Finding

The replay traced the sound editor, its navigation record and the undo:

| Input | What | Traced |
|---|---|---|
| 445 | audition pad 17,6 held + SELECT in a clip view | `InstrumentClipView::enterNoteRowEditor()` → `SoundEditor::setup()`: the note row editor |
| 449-450 | a knob turn, a grid pad in the note row editor | an Action recording the sound editor as its view (the undo below went to it) |
| 451 | BACK | `SoundEditor::exitCompletely()` |
| 459 | another clip's view | |
| 461 | BACK (undo) | `ActionLogger::revert()` → `changeRootUI(soundEditor)` → `SoundEditor::opened()` → `beginScreen()`: no menu item, a call through null |

The cause:
- **The record:** `ActionLogger::getNewAction()` records `getCurrentUI()` as the Action's view (`src/deluge/model/action/action_logger.cpp:195`). Only a root UI may make Actions, with two exceptions (`:97-99`): the sound editor's note editor and note row editor, which edit notes on the grid, and the pattern browser. Their Actions recorded the sound editor.
- **The undo:** undo and redo run only from a root UI (`allowedToDoReversion()`, `:875-876`). Undone from another clip, `revert()` changes clip (`CHANGE_CLIP`, `:444-445`) and makes the recorded view the root UI if it isn't the current one (`:649-650`). That made the sound editor the root UI. It had been closed long before, its navigation record had no item left, and `beginScreen()` called `beginSession()` through null.

A user gets there with ordinary inputs:
1. In a clip, hold an audition pad and press SELECT (the note row editor), then set a note.
2. Leave the editor and go to another clip.
3. Press BACK (undo).

This is a real bug, not a harness artifact. `revert()` compares the view only with root UIs (song view, arranger, keyboard, automation and clip views), so a sound editor view went to no animation but this one.

**1.2.1:** it records `getCurrentUI()` too, but makes Actions only from a root UI (or over the performance view): the note row editor's way in is new in 1.3. Not affected as far as read.

## Fix: patch 0118

The Action records `getRootUI()`, the view under the sound editor or pattern browser. That is the view to go back to; undo runs only from root UIs, so nothing else compares it. The patch changes 1 line and adds 3, in 1 file.

## Tests

`tests/repro/undo_note_row_editor_emu.py`, now in `run.sh`:

| Check | 42 patches, without 0118 | + 0118 |
|---|---|---|
| (1) PADA's clip view, audition pad 17,6 + SELECT (the note row editor), a grid pad (an Action), BACK | holds | holds |
| (2) SONG, PADB's clip view, BACK (undo) | crash in `SoundEditor::beginScreen()` | back in PADA's clip view |
| Result | FAIL (2, problems) | PASS |

`tests/repro/run.sh` on the whole series with 0118 (`patches/series`: 0001-0026 with 0101-0103 and 0105-0117, then 0118; 43 patches, applied with `git am` to 62a516c2): 39 of 39 PASS. Seed 260 replayed with 0118: all 1,000 inputs, no problem.

## Status

Runs: seed 260 replayed twice (the crash with the sound editor and the undo traced; with 0118 through input 1,000), `undo_note_row_editor_emu.py` on both builds, `run.sh`. Problems: 1 (undo into a closed sound editor), fixed by 0118.
