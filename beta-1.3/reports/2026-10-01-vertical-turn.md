# A fast vertical turn in song and arranger view breaks the limits (patch 0107), 01.10.2026

A replay of seed 109 (`--mode deep --7seg`) on one build crashed at input 192 (open item #22 of `2026-10-01-arranger-undo.md`). There `pad 17,5 held + MIDI`, in the sound editor over performance view, ended the arranger's audition on a garbage `Output*`: `ArrangerView::handleAuditionPadAction()` → `endAudition()` → a jump to 0x0c000014.

## Finding

A replay of the same inputs watched `arrangerView.yPressedEffective`. At input 189 an audition pad on row 3 set it to 7, which only happens with `arrangementYScroll` at −8, past its limit of −7.

The cause is in the vertical scroll, the same kind of regression as 0016's S004 (the encoder overhaul #4529):
- **The input:** v1.3 hands a fast turn on as several detents at once (`src/deluge/hid/encoder_input.cpp:106`).
- **The scroll:** `SessionView::verticalEncoderAction()` and `ArrangerView::verticalEncoderAction()` pass the whole turn to `verticalScrollOneSquare()` (`session_view.cpp:1464`, `arranger_view.cpp:3099`). That function checks its limits for one square and then adds the whole turn (`session_view.cpp:1517`, `arranger_view.cpp:3044`, `:3072`).
- **Song view, a Clip held:** a turn of +3 fails `direction == 1` (`session_view.cpp:1472`, `:1496`), so the check for turning down runs. `sessionClips.swapElements(newIndex, oldIndex)` (`:1514`) then swaps the held Clip with an index past the end of the list, and a null pointer takes its place.
- **Arranger view:** +30 from the bottom (−7) gives a scroll of 23 with 4 outputs. An audition pad on an empty row clamps `yPressedEffective` to the scroll's range (`arranger_view.cpp:899`) and stores the new Output at `outputsOnScreen[-19]` (`:917`), outside the 8 rows. A later `endAudition(outputsOnScreen[yPressedEffective])` (`:874`) reads whatever lies there. That is the garbage `Output*`; it depends on the memory layout, which explains a crash in only one of several replays.

A user gets there with ordinary inputs: hold a Clip in song view and turn the vertical encoder quickly to move it, or scroll the arranger quickly and press an audition pad. 1.2.1 limited every encoder to ±1, so mastertune is not affected.

## Fix: patch 0107

Both `verticalEncoderAction()`s take the detents one by one: `verticalScrollOneSquare(±1)` once per detent, so its limits hold for each step and the speed stays. If the first step has to wait for the card routine, the turn is retried as before.

## Tests

`tests/repro/vertical_turn_emu.py`, now in `run.sh`:

| Check | up to 0106 | + 0107 |
|---|---|---|
| (1) song view: the last Clip held, +3 at once | the Clips: 3 and a null pointer | the same 4 Clips |
| (2) arranger: +30 at once, then audition pad 17,0 | scroll 23; a write to `outputsOnScreen[-19]` | scroll 3; nothing outside |
| Result | FAIL (1, 2) | PASS |

`tests/repro/run.sh` on the whole series as `nightly` has it (0001-0020, 0101-0103, 0105-0112, applied with `git am` to 62a516c2): 27 of 27 PASS.

## Status

Runs: seed 109 replayed with a watch on `arrangerView.yPressedEffective`, `vertical_turn_emu.py` before and after 0107, `run.sh` on the series up to 0111. Problems: 1 (a fast vertical turn past the scroll limits: a stray Clip pointer in song view, writes outside the arranger's rows), fixed by 0107. This closes the open item of `2026-10-01-arranger-undo.md` (the crash at input 192).
