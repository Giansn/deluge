# Seed 108: a Song made in reused memory plays "reversed" (patch 0105), 01.10.2026

The fuzz campaign's seed 108 (`--mode deep`, OLED, 62a516c2 with 0001-0009 and 0101) froze with E445 at input 488. E445 came from `AutoParam::homogenizeRegion()`. The input was a pad in performance view, with the arrangement recording.

## Finding

The replay traced `AutoParam`'s region functions from input 470, with their stack arguments, and took a backtrace at the freeze. The path:

`PerformanceView::padAction()` → `setParameterValue()` → `AutoParam::setValuePossiblyForRegion()` → `setCurrentValueInResponseToUserInput()` → `homogenizeRegion(livePos 541, length 38, …, effectiveLength 2147483647, reversed 0xED, cut 2147483647)`

- **Whose automation:** `effectiveLength` and the cut point are the Song's (`Song::getLoopLength()` and `getPosAtWhichPlaybackWillCut()` return INT32_MAX, `src/deluge/model/song/song.cpp:5765-5767` and `:5806-5808`). The automation being recorded was the song's own, from the arranger.
- **The direction:** `reversed` came from `isCurrentlyPlayingReversed()` (`auto_param.cpp:158`), which for the Song reads `TimelineCounter::currentlyPlayingReversed` (`model_stack.cpp:71`). That byte held **0xED**, not a `bool`.
- **The freeze:** the "playing reversed" branch checks `startPos < posAtWhichClipWillCut` and freezes with E445 (`auto_param.cpp:1254`).

The cause is in `src/deluge/model/timeline_counter.h:54-55`: `currentlyPlayingReversed` and `sequenceDirectionMode` have no initializer, and the constructor is `= default`. Clips set both (`clip.cpp:369`, `:1046`), but nothing sets them for the Song. The fields came with PR #4445 (ec1cb557), which fixed this very E445 in arranger automation: before it, `isCurrentlyPlayingReversed()` cast the Song to a `Clip*`. In fresh, zeroed memory the new fields read false, so the fix looked complete. A Song made in reused memory, as after song changes, keeps whatever was there. The code comment at E445 says "potentially solved by PR #4445".

1.2.1, and with it mastertune, still has the cast (`model_stack.cpp:71` in v19.0). Without E445 (beta builds only), a release build takes the reversed branch with INT32_MAX and records a wrong region.

## Fix: patch 0105

The two fields get initializers: false and `FORWARD`.

## Tests

`tests/repro/song_reversed_emu.py`, now in `run.sh`. At the entry of Song's constructor the test writes 0xED into both fields, as the memory held in seed 108:

| Check | up to 0104 | + 0105 |
|---|---|---|
| (1) SONG001 loaded: the new Song's flags | 0xED, 0xED | false, `FORWARD` |
| (2) arranger, RECORD + PLAY, KEYBOARD (performance view), pad 10,3 | E445 | no freeze |
| Result | FAIL (1, 2) | PASS |

Seed 108 was replayed to input 530 on the whole series. Inputs 480-487 and their views are the same as before. Input 488 (`pad 10,3`) goes through, and up to 530 there is no crash, freeze or hang.

`tests/repro/run.sh` on the whole series as `nightly` has it (0001-0016, 0101-0103, 0105, 0106, applied with `git am` to 62a516c2): 18 of 18 PASS.

## Status

Runs: seed 108 replayed twice (to 488 traced, to 530 on the fix), song_reversed_emu.py before and after, run.sh three times. Inputs: about 1,020 replayed. Problems: 1 found (E445: the Song's direction flags uninitialized), 1 fixed (0105). Open: 1.2.1's cast, for the mastertune side.
