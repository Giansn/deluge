# Seed 202: E411 when a clip's type changes in the instrument browser over its automation view (patch 0115), 02.10.2026

Stop run 8's seed 202 (`--mode deep`, OLED, 37 patches) froze with E411 at input 1022: `CV` in the synth browser, opened from a synth clip's automation view. The freeze came from `ModelStackWithThreeMainThings::getPatchedAutoParamFromId()`. A replay froze at the same input.

## Finding

Input 1021, LOAD held + SYNTH in the automation view with a patched parameter selected, opened the synth browser; at 1022 its CV made the clip a CV clip and closed it. The backtrace: `LoadInstrumentPresetUI::buttonAction()` → `closeUI()` → `AutomationView::focusRegained()` → `InstrumentClipMinder::focusRegained()` → `View::setActiveModControllableTimelineCounter()` → `setKnobIndicatorLevels()` → `AutomationView::displayAutomation()` → `getModelStackWithParamForClip()` → `getPatchedAutoParamFromId()`.

The cause:
- **The checks:** a Clip keeps the automation view's parameter and a type (`lastSelectedOutputType`). The view resets a selection whose type changed when it opens (`initializeView()`, `src/deluge/gui/views/automation_view.cpp:436`; 0102 added the pre-renders of session and arranger view); its own SYNTH, MIDI and CV buttons reset it before they change the type (`:1423-1457`).
- **Gap 1:** LOAD + SYNTH opens the browser over the view without a reset. The browser's CV (or MIDI) button changes the type itself (`load_instrument_preset_ui.cpp:390`, `:367`) and closes. `focusRegained()` (`automation_view.cpp:465`) doesn't check the type. The knob levels (`view.cpp:1408`) looked the patched parameter up in the CV clip's params, which have no patched set: E411. A MIDI clip would take the ID for a CC.
- **Gap 2:** the view noted the type only when it opened. In the seed its own SYNTH (input 1013) had made the CV clip a synth: the selection was reset, but the type noted stayed CV. LPF resonance was selected for the synth (input 1017), so CV in the browser looked like no change (traced).

A user gets there with ordinary inputs: select a synth parameter in a clip's automation view, LOAD + SYNTH, CV. This is a real freeze (FREEZE_WITH_ERROR stops a release build too), not a harness artifact.

**1.2.1:** the same `focusRegained()` without a type check, and the same browser buttons; only `initializeView()` checks the type. Not run on 1.2.1; a candidate for mastertune.

## Fix: patch 0115

- **Gap 1:** for an instrument Clip whose type changed, `AutomationView::focusRegained()` forgets the selected parameter before `InstrumentClipMinder::focusRegained()` sets the view's model stack to the new Output and renders. After that it resets the rest of the selection (`initParameterSelection()`) and notes the type. The whole reset can't come first: it refreshes the knob levels while the model stack still holds the old synth (a first version froze there with E411 in `Sound::getParamFromModEncoder()`).
- **Gap 2:** the view notes the Clip's type also when a parameter is selected (a pad, the select encoder).

A selection whose type didn't change stays. The patch adds 23 lines in 1 file.

## Tests

`tests/repro/automation_browser_type_emu.py`, now in `run.sh`:

| Check | without 0115 | gap 1 only | + 0115 |
|---|---|---|---|
| (1) PADA: automation view, LPF frequency, LOAD + SYNTH, CV | E411 | the automation view, a CV clip, no parameter | the same |
| (2) PADB: the same, then BACK instead of CV | (not reached) | the selection stays | the same |
| (3) PADA, now CV: SYNTH in its automation view, LPF frequency, LOAD + SYNTH, CV (the seed's way) | (not reached) | E411 | no parameter, no freeze |
| Result | FAIL (1, 2, 3, problems) | FAIL (3, problems) | PASS |

`tests/repro/run.sh` on the whole series (`patches/series`: 0001-0025 with 0101-0103 and 0105-0114, then 0115; 39 patches, applied with `git am` to 62a516c2): 35 of 35 PASS. Seed 202 replayed with 0115: all 1,100 inputs, no problem.

## Status

Runs: seed 202 replayed four times (the freeze; with gap 1's fix alone, which still froze; the Clip's type and noted type at each step; with 0115 through input 1,100), `automation_browser_type_emu.py` on three builds, `run.sh`. Problems: 1 (E411 after a type change under the automation view), fixed by 0115.
