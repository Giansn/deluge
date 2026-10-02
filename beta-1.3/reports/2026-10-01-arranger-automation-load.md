# Seed 127: the arranger's automation view outlives a song load, then auditions a freed Output (patch 0111), 01.10.2026

Stop run #2's seed 127 (`--mode deep --7seg`, the series up to 0109) crashed at input 565, `pad 17,5` in the automation view: a jump to 0x0c000014 from `View::setActiveModControllableTimelineCounter()`. A replay crashed at the same input.

## Finding

The backtrace: `AutomationView::padAction()` → `ArrangerView::handleAuditionPadAction()` → `setActiveModControllableTimelineCounter(output->getActiveClip())` (`src/deluge/gui/views/arranger_view.cpp:927`): the automation view in the arranger's mode, row 5's Output garbage.

Replays traced the views and watched the flag, the rows and the Output:
- **Input 304** loaded a song. The old song was destroyed with its Outputs, among them the audio track at 0x201ff658. The new song started in the keyboard view.
- **Input 507:** CLIP in the instrument clip view opened the automation view (`instrument_clip_view.cpp:308`), which `AutomationView::opened()` set up for the *arranger*. `automationView.onArrangerView` was still on from before the load.
- **Input 565:** the arranger's row 5 (`arrangerView.outputsOnScreen`) still held 0x201ff658: the arranger hadn't been opened since the load to rebuild its rows. Auditioning it read a freed Output.

The cause is the flag's life:
- **Where it is reset:** CLIP in the arranger sets it (`arranger_view.cpp:225`). Only `ArrangerView::opened()` (`:512`) and the transitions into a Clip's automation view (`session_view.cpp:2773`, `:4593`, `arranger_view.cpp:1843`) reset it.
- **The load:** a song loaded from the arranger's automation view starts in song view or in a Clip's view (`deluge.cpp:342-376`), and the flag stays on.
- **CLIP in a Clip** then opens the arranger's automation view instead of the Clip's. Its audition and status pads use the old song's rows. A use-after-free: whether it crashes depends on the memory layout (one replay with other hooks didn't).

A user gets there with ordinary inputs: SONG, CLIP (the arranger's automation view), load a song, enter a Clip, CLIP, an audition pad. This is a real bug, not a harness artifact.

## Fix: patch 0111

`setUIForLoadedSong()` resets the flag, since a new song never starts in the arranger's automation view. So does opening song view, the instrument and audio clip views or the keyboard view, as `ArrangerView::opened()` already does. The performance view keeps it, because it returns to the arranger's automation view on purpose. The patch adds 5 resets (18 lines with comments).

## Tests

`tests/repro/arranger_automation_load_emu.py`, now in `run.sh`:

| Check | up to 0110 | + 0111 |
|---|---|---|
| (1) SONG, CLIP, SONG001 loaded | flag still on, a row of the old song (0x201ff5f8) | flag off |
| (2) PADA entered, CLIP, audition pads 17,0-17,7 | the arranger's automation view; a crash at pad 17,7 (a jump to 0 from `Sound::allowNoteTails()`) | PADA's automation view, no problem |
| Result | FAIL (1, 2) | PASS |

Seed 127 was replayed to input 700 on the series up to 0111. Input 507 (`CLIP held + MOD3` in the instrument clip view) now opens the Clip's automation view, and input 566 (`pad 17,5 held + SELECT_ENC`) goes through. Up to 700 there is no crash, freeze or hang.

Stop run #2's other finding, seed 126's two `Error 2` popups, is expected. The instrument browser for MIDI (`LOAD held + MIDI`) had no file selected on the test card, which sets `currentInstrumentLoadError` to UNSPECIFIED (`load_instrument_preset_ui.cpp:268`). An audition pad then shows that error (`:1128`).

`tests/repro/run.sh` on the whole series as `nightly` has it (0001-0020, 0101-0103, 0105-0112, applied with `git am` to 62a516c2): 27 of 27 PASS.

## Status

Runs: seed 127 replayed five times (the crash, with traces, with a write watch on the old audio track's `activeClip` from boot, with the flag and the rows watched, and to input 700 on the series up to 0111). `arranger_automation_load_emu.py` before and after 0111, `run.sh`. Problems: 1 (the arranger's automation view outlives a song load and auditions a freed Output), fixed by 0111. Seed 126's popups are expected.
