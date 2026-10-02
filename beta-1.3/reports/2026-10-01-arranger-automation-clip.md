# The arranger's automation view takes no type from the song's current Clip (patch 0110), 01.10.2026

The arranger's automation view (`automationView.onArrangerView`) had three faults of one kind: it used the song's current Clip, although the arranger shows none (0013, 0106, 0108). A code review searched it for more such uses; the emulator checked the findings. The fuzzer hadn't hit these yet.

## Finding

`AutomationView::padAction()` and `selectEncoderAction()` (`src/deluge/gui/views/automation_view.cpp:1674`, `:2583`) take the output type from the current Clip, also in the arranger (`:1693`, `:2592`). Everything after that steers by it:
- **No Clip (after a song load):** the type is read through null, as 0: SYNTH. The pads of the expression parameters (14,7, 15,0, 15,7) then pick a "synth" parameter in `handleParameterSelection()` and write it through null (`:1976`). They also move the song's shortcut to that pad (`:1986`), which has no song parameter. In the emulator: writes at 212 and 216; a Deluge writes into the empty CS0 area.
- **An audio Clip current:** `padAction()` ignores the sidebar for audio Clips (`:1681`), so the arranger's status (mute) and audition pads did nothing.
- **A MIDI Clip current:** the select encoder changed that Clip's CC (`selectMIDICC()`, `:2624`) instead of the song's parameter.
- **Smaller:** a MIDI Clip's type also made a top pad write 127 instead of 128 in the editor (`mod_controllable.cpp:1069`, `:1140`, `:1225`). SELECT and the cross-screen LED read the Clip (`:1659`, `:1878`), and a Clip's patch-cable selection reset its own shortcut (`:2641`).

A user gets there with ordinary inputs: load a song, or work in an audio or MIDI clip, then SONG, CLIP, and the pads or the select encoder. These are real bugs, not harness artifacts.

## Fix: patch 0110

In the arranger, `padAction()` and `selectEncoderAction()` take no Output and the type `OutputType::NONE`, which no clip-only branch matches. The audio Clip's sidebar check, SELECT's kit check, the LED and the patch-cable reset are skipped there. The editor's pad value takes NONE too. The patch changes 14 lines in 2 files.

## Tests

`tests/repro/arranger_automation_clip_emu.py`, now in `run.sh`. Each check runs in the arranger's automation overview (SONG, CLIP), with the null page checked after every input:

| Check | up to 0109 | + 0110 |
|---|---|---|
| (1) no current Clip: pads 14,7, 15,0, 15,7 | writes through null (212, 216); the song's shortcut moved to 15 | nothing through null; the song's selection stays |
| (2) the audio Clip current: status pad 16,7 | the mute stays | the mute toggles (0 → 1) |
| (3) PADA made MIDI and current: select +1 | PADA's CC −1 → 2; the song's parameter stays none | PADA's CC stays −1; the song's first parameter selected (ID 39, shortcut x 6) |
| Result | FAIL (1, 2, 3) | PASS |

Not changed, open:
- **Reads only:** the OLED shows the current Clip's output name (`InstrumentClipMinder::renderOLED()`), and the renders read the Clip (`:683`, `:974`). After a song load these read through null; no run has failed on them.
- **Fill mode:** with SYNC-SCALING set to Fill (not the default), the button can edit `instrumentClipView`'s held notes from any view (`view.cpp:331-344`). In the arranger that would need edit-pad presses left over in `instrumentClipView`. Not reproduced.

`tests/repro/run.sh` on the whole series as `nightly` has it (0001-0020, 0101-0103, 0105-0112, applied with `git am` to 62a516c2): 27 of 27 PASS.

## Status

Runs: `arranger_automation_clip_emu.py` before and after 0110, `run.sh`, and a code review of the view's uses of the current Clip. The review had 8 findings: 5 are fixed here, 3 of them checked in the emulator, and 3 are left open above. Problems: 3 (a write through null, dead status pads, select changing a MIDI Clip's CC), fixed by 0110. 1.2.1 shares the audio and MIDI cases.
