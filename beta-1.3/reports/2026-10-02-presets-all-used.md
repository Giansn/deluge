# Seed 226: E058 and i009, preset navigation when every preset is in the song (patch 0117), 02.10.2026

Stop run 9's seed 226 (`--mode deep`, OLED, 39 patches) froze with E058 at input 783, between inputs, in the keyboard screen. A select encoder turn had changed the clip's preset. Afterwards `Song::ensureAllInstrumentsHaveAClipOrBackedUpParamManager()` found a synth with no Clip and no backed-up ParamManager. A replay froze at the same input. The second boot's E242 has its own report (0116).

## Finding

A replay checked, after every input, that each synth and kit in the song's list has a Clip or a backed-up ParamManager. Up to input 782 none was missing, and the firmware's own check before the change (E057) passed. So the state broke inside the preset change. Traced: `View::navigateThroughPresetsForInstrumentClip()` → `LoadInstrumentPresetUI::doPresetNavigation()` (asking for an unused preset) → `InstrumentClip::changeInstrument()` to the synth PINGPONG ROLL → E058 for PINGPONG ROLL (the loop's register at the freeze).

The cause:
- **The requirement:** the clip has an instance in the arranger, so `Song::shouldOldOutputBeReplaced()` (`src/deluge/model/song/song.cpp:4779`) asks for a preset not in the song (`INSTRUMENT_UNUSED`), to replace the whole instrument.
- **The fallback:** the card has five synth presets, by then all in the song. `doPresetNavigation()` (`load_instrument_preset_ui.cpp:1566-1570`) skips presets in the song. After going round its list twice, a guard against an endless loop, it returned one in the song anyway: PINGPONG ROLL, kept with no Clip.
- **The clip:** being in the song already, it got no whole-instrument replacement but `changeInstrument()` for the one clip (`view.cpp:2603-2613`), whose comment assumes no instance in the arranger. The clip, an arrangement-only one, went to PINGPONG ROLL; its instances stayed with the old synth. `getClipWithOutput()` finds such clips through the output's instances, so PINGPONG ROLL had no Clip, and its backed-up ParamManager had gone to the clip: E058.
- **The arranger:** `Song::navigateThroughPresetsForInstrument()` (arranger view, the song grid) hands the result to `replaceInstrument()`, which freezes with i009 for an instrument already in the song (`song.cpp:3416`).

A user gets there with ordinary inputs: every preset of a folder in use in the song, then a preset change in the arranger or on a clip with an arranger instance. This is a real bug, not a harness artifact. E058 is a beta-only check; i009 freezes a release build too.

**1.2.1:** the same fallback (`load_instrument_preset_ui.cpp:1527-1528`) and the same i009 check. Not run on 1.2.1; a candidate for mastertune.

## Fix: patch 0117

When the list has gone round twice without a preset that isn't in the song, `doPresetNavigation()` gets out without a result, as when the list is empty. Both callers then change nothing. The patch changes 3 lines and adds 9, in 1 file.

## Tests

`tests/repro/presets_all_used_emu.py`, now in `run.sh`:

| Check | 41 patches, without 0117 | + 0117 |
|---|---|---|
| (1) song view: new clips until no unused preset is left (Error 16), every clip stopped | holds | holds |
| (2) arranger: a synth row's audition pad held, select +1 | i009 in `replaceInstrument()` | the row keeps its synth |
| Result | FAIL (2, problems) | PASS |

The clips are stopped so that the browser's list keeps the song's synths: it drops those with a clip playing in song view. In the seed it held one with no Clip.

`tests/repro/run.sh` on the whole series with 0117 (`patches/series`: 0001-0026 with 0101-0103 and 0105-0116, then 0117; 42 patches, applied with `git am` to 62a516c2): 38 of 38 PASS. Seed 226 replayed with 0117: all 1,100 inputs, no problem.

## Status

Runs: seed 226 replayed five times (the freeze; a check of every synth and kit after each input; the preset change traced; the registers at the freeze; with 0117 through input 1,100), `presets_all_used_emu.py` on both builds, `run.sh`. Problems: 1 (preset navigation returning a preset in use: E058 in the seed, i009 in the arranger), fixed by 0117.
