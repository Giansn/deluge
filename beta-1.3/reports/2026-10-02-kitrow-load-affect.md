# Seed 186: a kit row's sample browser with no Sound when Affect Entire is on (patch 0114), 02.10.2026

Stop run 7's seed 186 (`--mode deep`, OLED, 34 patches) crashed at input 291, between inputs, after `pad 17,2 held + LOAD` in a kit clip: an invalid instruction at 0xc0000fc, reached by a virtual call through `soundEditor.currentSound` in `SampleBrowser::claimCurrentFile()`. A replay crashed at the same input.

## Finding

Replays traced function entries and read the sound editor's fields at each one:

| Input | What | Traced |
|---|---|---|
| 261 | AFFECT held + MIDI | the kit clip's `affectEntire` 0 → 1 |
| 264 | pad 12,0 (a note) | `SoundEditor::potentialShortcutPadAction()`: `setupKitGlobalFXMenu` 0 → 1 |
| 290 | pad 17,2 held + LOAD | `enterDrumCreator()`: a new drum; `setup()` hands the Kit to the file selector's permission check; `currentSound` null; the browser opens, the LOAD release turns auto-load on |
| 291 | select +1 | `previewIfPossible()` → `claimCurrentFile()` → a call through null |

The cause:
- **The flag:** v1.3's `potentialShortcutPadAction()` (`src/deluge/gui/ui/sound_editor.cpp:1452`) sets `setupKitGlobalFXMenu = shouldEditKitAffectEntire()` on every grid pad press while the sound editor is closed, before it checks for a shortcut. A note pad isn't one; the flag stays set until `exitCompletely()` (`:957`).
- **The setup:** audition pad + LOAD in a kit (`instrument_clip_view.cpp:474-498`) runs `enterDrumCreator()`. It makes a new SoundDrum for the row, selects it, then calls `soundEditor.setup(clip, &file0SelectorMenu, 0)` (`:5526`). With the flag set, `setup()` takes the kit's own FX (`sound_editor.cpp:1965`): no Sound, and `FileSelector::checkPermissionToBeginSession()` reads the Kit as a Sound (`file_selector.cpp:74`). That read passed here, so `currentSound` became null; `enterDrumCreator()` ignores the result ("Can't fail") and opens the browser (`:5535`).
- **The crash:** choosing a sample runs `claimCurrentFile()`, either with SELECT (`sample_browser.cpp:397`) or with a select turn while auto-load is on (`:593`). Its first step for an instrument is `soundEditor.currentSound->killAllVoices()` (`:838`). On the Deluge, address 0 is empty memory, so the virtual call jumps to whatever it reads there.

A user gets there with ordinary inputs:
1. In a kit clip with Affect Entire on, tap a note.
2. Hold a row's audition pad and press LOAD.
3. Pick a sample.

A real bug, not a harness artifact. Had the permission check failed, the browser would have opened on the last edited Sound, which can be freed: the previous browser's exit deletes its unused drum and leaves `currentSound` pointing at it (`:273`).

Seen on the way, not changed: the release of the LOAD press that opens the browser turns auto-load on (`:483`), so the first select turn already loads into the row.

**1.2.1:** not affected, as far as read: its `potentialShortcutPadAction()` doesn't set the flag, which is set only right before the sound editor opens.

## Fix: patch 0114

`enterDrumCreator()` clears `setupKitGlobalFXMenu` before `setup()`: the sound editor and the browser work on the new drum. One statement and a comment (5 lines added, 1 file). Grid shortcuts with Affect Entire on still open the kit's FX.

## Tests

`tests/repro/kitrow_load_affect_emu.py`, now in `run.sh`, uses the card's kit clip:

| Check | 36 patches, without 0114 | + 0114 |
|---|---|---|
| (1) Affect Entire on, pad 3,2 (a note), audition pad 17,1 + LOAD | browser open, `currentSound` null | browser on the new drum's Sound |
| (2) select +1, then SELECT | crash: a call through null in `claimCurrentFile()` | the sample kept, the sound editor on the row |
| Result | FAIL (1, 2, problems) | PASS |

`tests/repro/run.sh` on the whole series (`patches/series`: 0001-0024 with 0101-0103 and 0105-0112, then 0113 and 0114; 37 patches, applied with `git am` to 62a516c2): 33 of 33 PASS.

## Status

Runs: seed 186 replayed four times (the crash, function entries, the sound editor's fields at each entry, where the flag was set), `kitrow_load_affect_emu.py` before and after, `run.sh`. Problems: 1 (a kit row's sample browser with no Sound), fixed by 0114. Left as it is: the LOAD release that turns auto-load on.
