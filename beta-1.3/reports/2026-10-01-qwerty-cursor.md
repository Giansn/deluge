# Seed 106: a fast encoder turn moves the browser's text cursor out of the text (fixed by 0016), 01.10.2026

The fuzz campaign's seed 106 (`--mode deep`, OLED, 62a516c2 with 0001-0009 and 0101) froze with S004 in `String::concatenateAtPos()` at input 378, a QWERTY pad in the song browser.

## Finding

The replay, with the `String` functions traced from input 365 and a backtrace at the freeze (`MatrixDriver::padAction()` → `QwertyUI::padAction()` → `String::concatenateAtPos()`):

| Input | What | Cursor (`QwertyUI::enteredTextEditPos`) |
|---|---|---|
| 368 | `pad 6,2` (V) in the song browser | No song starts with V. `Browser::predictExtendedText()` sets the text back to the current song's name (7 characters): 0 |
| 370 | `turn scrollX -1` | at 0 already: 0 |
| 372 | `turn scrollX +1` | 1 |
| 376 | `turn scrollX -3` | 1 − 3 = **−2** |
| 378 | `pad 9,2` (M) | `concatenateAtPos(…, pos −2)`: S004 |

The cause:
- **The cursor move:** `QwertyUI::horizontalEncoderAction()` (`src/deluge/gui/ui/qwerty_ui.cpp:470-491`) adds the encoder's offset to the cursor. It checks only for exactly 0 (turning left) and exactly the end (turning right).
- **The offset:** v1.3 hands a fast turn on as one offset of several detents (`src/deluge/hid/encoder_input.cpp:102-114`, "the full accumulated delta for natural acceleration"). 1.2.1 limited every function encoder to ±1 (`hid/encoders.cpp:116-125` there: "Some functions can break if they receive bigger numbers"). This is a regression in v1.3 that any fast turn reaches, not a harness artifact. mastertune (1.2.1) is not affected.
- **The freeze:** `String::concatenateAtPos()` compares the position with `getLength()`, a `size_t` (`src/deluge/util/d_string.cpp:223-225`). −2 counts as past the end: S004. The check is not limited to beta builds. Before that, `qwerty_ui.cpp:352` read the character at the cursor, outside the text.
- **Past the end:** by the code, a turn of +9 at position 1 of 7 gives 10, and the next character freezes the same way.

All QWERTY screens end up in this function: the song, preset and sample browsers and the save and rename dialogs (OLED). `SampleBrowser` and `SlotBrowser` hand the turn on to it. On 7-segment, `SlotBrowser` clamps its own number cursor (`slot_browser.cpp:78-84`).

## Fix: patch 0016

The dev session's patch 0016 (fuzz round 4) makes the same change: the cursor is kept within the text. My 0104 was the same `std::clamp` and is withdrawn. The two were found independently, in seed 106 here and in round 4 there.

## Tests

0016's test `qwerty_cursor_emu.py` is in `run.sh`. My own check ran on the song browser with SONG001 and 0104, which makes the same change:

| Check | up to 0103 | + the clamp |
|---|---|---|
| (0) S typed: SONG001, cursor 1; turns −1 and +1 | 1, 0, 1 | the same |
| (1) a turn of −3, then S typed | S004 | cursor 0, then 1 |
| (2) a turn of +20, then 1 typed | not reached | cursor 7 (the end), no freeze |

Seed 106 was replayed to input 430 with 0103 and the clamp. Inputs 365-377 and their views are the same as before. Input 378 (`pad 9,2 held + SYNTH`) types its character, and up to 430 there is no crash, freeze or hang.

`tests/repro/run.sh` on the whole series as `nightly` has it (0001-0016, 0101-0103, 0105, 0106, applied with `git am` to 62a516c2): 18 of 18 PASS.

## Status

Runs: seed 106 replayed twice (to 378 traced, to 430 on the fix), the check before and after, run.sh three times. Inputs: about 810 replayed. Problems: 1 found (S004: the text cursor outside the text after a fast turn), fixed by 0016.
