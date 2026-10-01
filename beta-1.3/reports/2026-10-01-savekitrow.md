# Seed 1011: the "kit row without collections" is no kit row (patch 0101), 01.10.2026

The fuzzer's seed 1011 (`--mode deep`) froze with E411 when a kit row was saved: the row's ParamManager had no main
collections. Patch 0008 only guards the save. The question was where the row loses them.

## Finding

It never had any, because it is not a kit row.

The replay, with the save UI's inputs traced (`SaveKitRowUI::opened()` hooked):

| Input | What | Traced |
|---|---|---|
| 13 | `pad 17,y held + SAVE` in the synth clip | `SaveKitRowUI` opens with `soundDrumToSave=0xfffffad0` and the ParamManager of a melodic row (`0x2020b20c`, no collections) |
| 169 | `pad 11,2 held + CV` | `View::changeOutputType()` → `InstrumentClip::changeOutputType(CV)`: the clip becomes a CV clip |
| 170 | `pad 17,3 held + SAVE` | `SaveKitRowUI` opens with `soundDrumToSave=0xfffffad0` and `paramManagerToSave=0x1c` |

The cause is in `InstrumentClipView::buttonAction()` (`src/deluge/gui/views/instrument_clip_view.cpp:580-588`). With an audition pad held, SAVE opened the kit-row save in any clip. Three things go wrong there:
- **No row:** `getNoteRowOnScreen()` returns null for a row without notes. `&noteRow->paramManager` then gives `0x1c`.
- **No drum:** a melodic row has `drum == nullptr`, and `noteRow->drum->type` reads through it. The null page reads 0, which is `DrumType::SOUND`.
- **Garbage drum:** after that dereference, the compiler drops the null check of the downcast `(SoundDrum*)noteRow->drum`. A null `Drum*` becomes `0 − 0x530 = 0xfffffad0`.

Saving then wrote from that garbage SoundDrum and froze with E411 at the params, or now, with 0008, skips them.

The other entry, `commandSaveKitRow()` (`:2228-2238`), already checks the kit, the row and the drum.

**Not undo.** The suspected undo of a type change is not involved. The two undos of this run (inputs 165 and 167) come before the change. `InstrumentClip::changeOutputType()` clears the undo log anyway (`src/deluge/model/clip/instrument_clip.cpp:3749`).

**Not #4938.** #4938 has a real kit row: `InstrumentClip::allowNoteTails()` is reached only for rows with a drum (`instrument_clip.cpp:3345-3366`), and seed 1011 never got there. 0002's guard stays, and #4938's own origin is still open.

## Fix: patch 0101

The SAVE branch now requires a kit clip (`getCurrentOutputType() == OutputType::KIT`), a NoteRow, a drum and `DrumType::SOUND`, as `commandSaveKitRow()` does. The change is 8 lines. 0008's guard in `Sound::writeToFile()` stays.

## Tests

`tests/repro/savekitrow_emu.py`, now in `run.sh`:

| Check | 62a516c2 + 0001-0009 | + 0101 |
|---|---|---|
| (1) synth clip PADA, each audition pad + SAVE | opens 8 times: drum `0xfffffad0`, ParamManager `0x1c` or a melodic row's | never opens |
| (2) kit clip, each audition pad + SAVE | opens for the sound row with its drum and collections | the same |
| Result | FAIL (1) | PASS |

Seed 1011 was replayed to input 180 on the fixed build. The kit-row save opens neither at 13 nor at 170: input 170 stays in the clip view. Inputs 1-169 are the same as before, and there is no crash, freeze or hang.

`tests/repro/run.sh` on 0001-0009 + 0101: 7 of 7 PASS (save_4917, songswap_e455, kitrow, save_name, oom_robust, cluster_cache, savekitrow).

## Status

Runs: seed 1011 replayed 3 times (to 177, 171 and 180), savekitrow_emu.py before and after, run.sh twice. Inputs: about 530 replayed. Problems: 1 found (the kit-row save on a non-kit row, also the cause of 0008's E411), 1 fixed (0101). Open: #4938's own trigger.
