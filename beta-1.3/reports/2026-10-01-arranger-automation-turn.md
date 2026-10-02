# Seed 125: the horizontal encoder in the arranger's automation view goes through a null Clip (patch 0108), 01.10.2026

The stop run's seed 125 (`--mode deep --7seg`, the series up to 0106) crashed between inputs 1111 and 1112: an invalid instruction at 0x0c000004, called from `ClipView::horizontalEncoderAction()`. A replay on the series crashed at the same input; so did one with 0107.

## Finding

Inputs 1107 to 1111 opened the arranger's automation view (`automationView.onArrangerView`), selected a parameter there (the automation editor) and turned the horizontal encoder. The song had been loaded before and no Clip had been entered since, so it had no current Clip.

The cause is in `AutomationView::horizontalEncoderAction()` (`src/deluge/gui/views/automation_view.cpp:2216`):
- **The hand-over:** a turn the editor doesn't take itself (no button held, SHIFT, the horizontal encoder's button for zoom) goes to `ClipView::horizontalEncoderAction()` (`:2311`), also in the arranger, which shows no Clip.
- **ClipView's cases:** they all go through `getCurrentClip()`. A plain turn first asks the Clip whether it can scroll, `currentlyScrollableAndZoomable()`, a virtual call (`clip_view.cpp:259`). SHIFT edits the Clip's length (`:156`, `:176`). The check just before, for SHIFT + X_ENC on an audio clip, reads the Clip's type as well (`automation_view.cpp:2303`).
- **No Clip:** after a song load the current Clip is null. The virtual call reads its vtable through null and jumps to whatever is there: the crash. A Deluge reads the empty CS0 area there, so it jumps to garbage as well.
- **A Clip:** with a current Clip, SHIFT + a turn changes the length of a Clip the arranger doesn't show (and adds an undo step for it).

A user gets there with ordinary inputs: load a song, SONG, CLIP, a parameter's pad, then turn the horizontal encoder. This is a real bug, not a harness artifact. The neighbours in the same view are already fixed: the type buttons (the dev session's 0013), undo (0106).

## Fix: patch 0108

In the arranger the turn goes straight to `ClipNavigationTimelineView::horizontalEncoderAction()`, which scrolls and zooms the arranger: the part of ClipView's handling that applies there. The audio clip check is skipped in the arranger. The patch changes 1 line and adds 8, 3 of them a comment.

## Tests

`tests/repro/arranger_automation_turn_emu.py`, now in `run.sh`. Each check runs in the arranger's automation editor (SONG, CLIP, pad 8,7: LPF frequency):

| Check | up to 0107 | + 0108 |
|---|---|---|
| (1) PADA entered before; 3 × SHIFT + scrollX +1; X_ENC + scrollX +1 | PADA's length 1536 → 2112; the arranger zooms | PADA stays 1536; the arranger zooms (192 → 96), the Clips' zoom stays |
| (2) SONG001 loaded (no current Clip); scrollX +1, SHIFT + scrollX +1, X_ENC + scrollX +1, LEARN + X_ENC, SHIFT + LEARN + X_ENC | crash at the first turn: a jump to 0 from `ClipView::horizontalEncoderAction()` | the arranger zooms (192 → 96), no problem |
| Result | FAIL (1, 2) | PASS |

Seed 125 was replayed to input 1250 on the series up to 0109. Inputs 1107-1111 open the arranger's automation editor as before, and input 1112 (`turn scrollX +3`) scrolls it. Up to 1250 there is no crash, freeze or hang.

`tests/repro/run.sh` on the whole series as `nightly` has it (0001-0016, 0101-0103, 0105-0111, applied with `git am` to 62a516c2): 23 of 23 PASS.

## Status

Runs: seed 125 replayed three times (the series, with 0107, and to input 1250 with 0108 and 0109), `arranger_automation_turn_emu.py` before and after 0108, `run.sh`. Problems: 1 (a jump through the null current Clip in the arranger's automation view), fixed by 0108. 1.2.1 has the same code (`automation_view.cpp`, `clip_view.cpp`), so it is a candidate for mastertune v19.0.5.
