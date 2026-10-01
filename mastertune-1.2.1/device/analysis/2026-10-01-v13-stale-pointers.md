# Stale UI pointers in v1.3 beta 62a516c2 (cause 1 of NIGHTLY.md), 01.10.2026

This review reads the code only; nothing was run in the emulator. The test song (make_sd.py) has clips 0-7 = synths PADA PADB PADC PADD ARP PADE FM WT, 8 = KIT and 9 = LOOP (audio). It loads with `yScrollSongView=-7`, so clip i sits on row y = i - scroll.

## 1. References, and 2. where their objects are freed or replaced

**References:**
- `SoundEditor::current*`, 12 members (`gui/ui/sound_editor.h:62-74`)
- `View::activeModControllableModelStack` (`gui/views/view.h:120`)
- `Kit::selectedDrum` (`model/instrument/kit.h:149`)
- the browsers' `soundDrumToReplace` and `soundDrumToSave` (`load_instrument_preset_ui.h:101`, `save_kit_row_ui.h:47`)

The automation, keyboard and performance views hold none.

**Free and replace sites:**

| Site | What | Where |
|---|---|---|
| S1 | synth preset into a kit row | `load_instrument_preset_ui.cpp:1055-1108`; frees the old SoundDrum at `storage/storage_manager.cpp:594-600` |
| S2 | drum removed | `kit.cpp:437,453` |
| S3 | row deleted | `instrument_clip_view.cpp:591-648` |
| S4 | output type changed | `view.cpp:2645` |
| S5 | preset changed | `view.cpp:2299`, `load_instrument_preset_ui.cpp:1024` |
| S6 | undo | `action_logger.cpp:490-503`, `consequence_clip_existence.cpp:44-163` |
| S7 | song load | `load_song_ui.cpp:310-537` |

## 3. Refreshed?

| Reference | S1 | S2 | S3 | S4 | S5 | S6 | S7 |
|---|---|---|---|---|---|---|---|
| view stack | **no**, only when the browser closes (`instrument_clip_minder.cpp:326`) | yes | yes (`instrument_clip_view.cpp:648`) | yes (`view.cpp:2667`) | yes (`view.cpp:2677`) | partly (`action_logger.cpp:502`, only if !affectEntire) | late (`session_view.cpp:183`) |
| `Kit::selectedDrum` | yes (`load_instrument_preset_ui.cpp:1103`) | yes (`kit.cpp:454`) | yes | yes (`instrument_clip.cpp:3847`) | yes (new Kit) | yes (`action_logger.cpp:493`) | n/a |

## 4. Cases, with steps for fuzz_ui.py

**K, the shared prefix:**
1. `turn scrollY +9`: KIT moves to y=6.
2. `pad 0,6`: opens the KIT clip, which starts in affect-entire mode.
3. `AFFECT`: switches to row mode.
4. `pad 17,0` held + `SYNTH`: selects the bottom row (BELL) and opens the synth browser. The browser swaps the drum as it opens (`load_instrument_preset_ui.cpp:299`).

K needs a synth preset in SYNTHS/ (fuzz_ui.py's card has five, make_sd.py none) and a "pad held + button" step, which the fuzzer lacks.

**A. View stack × S1** (#4518, family A). Stale: high confidence; faults: medium.
- **Steps:** K, then `turn mod0 +3`.
- **Why:** The browser has no `modEncoderAction()`, so `UI::modEncoderAction()` (`gui/ui/ui.cpp:33`) goes to `View::modEncoderAction()`. That makes virtual calls on the freed SoundDrum (`view.cpp:989,1003`).
- **Missing re-point:** `performLoadSynthToKit()` frees the old drum but never re-points the stack (`load_instrument_preset_ui.cpp:1081-1103`). The S5 path does re-point it, at `:1033`.

**B. SoundEditor pointers never cleared** (#4518, its third point). High confidence, latent.
- **Steps:**
  1. `turn scrollY +9`.
  2. `pad 0,0` (PADC).
  3. `SHIFT+pad 8,7` (LPF frequency) sets the members.
  4. `BACK`.
- **What to check:** `exitCompletely()` clears only `currentSound` (`sound_editor.cpp:958`). Assert that the other eleven members are null; they aren't.
- **Why latent:** no reader was found while the editor is closed. A later feature that reads them would crash.

**C. #4951** (E412 on knob learn after S1). Medium confidence. This is an empty `backedUpParamManagers` entry, not a UI pointer.
- **Steps:**
  1. K.
  2. `SELECT_ENC` accepts the preset.
  3. `SHIFT+pad 9,7` (HPF frequency).
  4. `LEARN` held + `turn mod0 +1`.
- **Path:** `Sound::learnKnob()` (`sound.cpp:2846`) walks the backups (`:2863-2880`) and finds the empty (drum, clip) entry that `performLoadSynthToKit()` leaves (`load_instrument_preset_ui.cpp:1096-1100`, `note_row.cpp:3756`).

**D. From the code only, no steps:**
- **S7:** the stack dangles between `~Song` (`load_song_ui.cpp:537`) and `setUIForLoadedSong()`. That only matters if a UI task runs inside a yield there.
- **S6:** this needs a second kit clip.
- **#4938:** `InstrumentClip::allowNoteTails()` reads a kit row's ParamManager without collections (`instrument_clip.cpp:3345-3366`). That is model state, and no deterministic trigger was found.

## For mastertune

mastertune (1.2.1 plus patches) has both A and B.
- **A:** `loadSynthToDrum()` frees the old drum (`storage/storage_manager.cpp:459-463`). `performLoadSynthToKit()` (`load_instrument_preset_ui.cpp:1025-1080`) does not re-point the view: only the S5 path does, at `:1016`, and `focusRegained()` does when the browser closes (`instrument_clip_minder.cpp:328`). No browser class overrides `modEncoderAction()`.
- **B:** `SoundEditor::exitCompletely()` (`sound_editor.cpp:535-555`) clears none of the twelve members, not even `currentSound`.
- **Fix for A:** call `view.setActiveModControllableTimelineCounter()` at the end of `performLoadSynthToKit()`, as the S5 path does.

Method: an agent read the code; A's and B's key lines were then checked by hand, in the beta and in mastertune.
