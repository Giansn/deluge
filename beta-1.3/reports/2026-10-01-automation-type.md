# Seed 101: a clip made CV keeps its synth parameter in the automation view (patch 0102), 01.10.2026

The fuzz campaign's seed 101 (`--mode deep --7seg`, 62a516c2 with 0001-0009 and 0101) froze with E411 at input 751, CLIP in song view. E411 came from `ModelStackWithThreeMainThings::getPatchedAutoParamFromId()`.

## Finding

The replay, with inputs 735-751 traced and a backtrace at the freeze:

| Input | What | Traced |
|---|---|---|
| 743 | `pad 4,7 held + CV` in song view | `View::changeOutputType(CV)` → `InstrumentClip::changeOutputType()`: a clip that was left in the automation view with a patched parameter selected becomes a CV clip |
| 751 | `CLIP` in song view | `SessionView::transitionToViewForClip()` → `AutomationView::renderMainPads()` → `getModelStackWithParamForClip()` → `MelodicInstrument::getModelStackWithParam()` with the kind PATCHED → `getPatchedAutoParamFromId()`: E411 |

The cause:
- **What the clip remembers:** its automation parameter (`lastSelectedParamKind`, `lastSelectedParamID`) and the type it was chosen in (`lastSelectedOutputType`).
- **Where it is checked:** `AutomationView::initializeView()` (`src/deluge/gui/views/automation_view.cpp:436-441`, called from `opened()`, `:399`) resets the selection if the type has changed.
- **What runs first:** the transitions into a clip that was left in the automation view render that view into the animation store before it opens, with the old selection. These are `SessionView::transitionToViewForClip()` (`session_view.cpp:2768-2779`), `SessionView::gridTransitionToViewForClip()` (`:4588-4606`) and `ArrangerView::transitionToClipView()` (`arranger_view.cpp:1838-1856`).
- **CV:** a CV clip looks the patched parameter up in its own params (`melodic_instrument.cpp:748`). They have no patched set, so the beta freezes with E411 (`param_manager.h:128`). A release build has no check there and reads through a null `ParamCollection`.
- **MIDI:** a MIDI clip takes any parameter ID for a CC (`midi_instrument.cpp:1347-1361`). It doesn't freeze, but the pre-render showed CC 24 for the LPF frequency until `opened()` reset it.

A user gets there with ordinary inputs: a synth clip, the automation view, a parameter selected (e.g. LPF frequency), back to song view, the clip held + CV, then CLIP. This is a real bug, not a harness artifact.

## Fix: patch 0102

`AutomationView::resetSelectionIfOutputTypeChanged()` now holds `initializeView()`'s check. `initializeView()` calls it, and so does each of the three transitions, before its pre-render. The patch adds 21 lines and removes 9, in four files.

## Tests

`tests/repro/automation_type_emu.py`, now in `run.sh`:

| Check | 0001-0009 + 0101 | + 0102 |
|---|---|---|
| (1) PADA: LPF frequency selected, made MIDI, CLIP | opens; `opened()` resets the selection | opens, selection reset |
| (2) PADB: LPF frequency selected, CLIP with no type change | the selection stays | the same |
| (3) PADB made CV, CLIP | E411 | opens, selection reset |
| Result | FAIL (3) | PASS |

Seed 101 replayed to input 800 on the fixed build: inputs 740-750 and their views are the same as before. Input 751 (`CLIP held + MOD1`) opens the automation view, and up to 800 there is no crash, freeze or hang.

`tests/repro/run.sh` on the whole series (0001-0010, 0101, 0102; the same tree as applying the patches to 62a516c2): 9 of 9 PASS (save_4917, songswap_e455, kitrow, save_name, oom_robust, cluster_cache, abandon_load, savekitrow, automation_type). With 0011 and 0012, which came in meanwhile: automation_type, export_abort_hang and export_abort_file PASS.

## Status

Runs: seed 101 replayed twice (to 751 traced, to 800 on the fix), automation_type_emu.py before and after, run.sh twice (without and with 0010). Inputs: about 1,550 replayed. Problems: 1 found (a clip's automation parameter outlives its type: E411 in a clip made CV), 1 fixed (0102). Campaign so far: seeds 100-104 (deep and all, OLED and 7seg), 4,300 inputs, no other problem.
