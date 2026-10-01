# The fuzzer's null writes: the MOD buttons in a CV clip (patch 0103), 01.10.2026

Every seed of the campaign recorded "null writes", all at address 0: 95 in seeds 100-109, between 1 and 18 per seed. The fuzzer keeps the null page writable and checks it after every input (`tests/fuzz_ui.py`, `NullPage`). So these were recorded, but not counted as problems.

## Finding

A write hook on the null page ran on seed 100 (400 inputs, 0001-0012 + 0101 + 0102). It caught 9 writes from one site, `View::modButtonAction()`+0x8c. The first came at input 104 (`MOD3 on`: 1 byte, value 3).

- **The write:** `View::modButtonAction()` (`src/deluge/gui/views/view.cpp:1554`) stores the pressed MOD button in `*modControllable->getModKnobMode()`.
- **Why it is null:** a CV instrument has no mod knob modes. `CVInstrument` doesn't override `getModKnobMode()`, and `ModControllable`'s returns nullptr (`mod_controllable.cpp:47-49`; the header says "Return NULL if different modes not supported"). So every MOD press in a CV clip wrote the button's number to address 0.
- **Two reads:** the same pointer is read at `view.cpp:1539` and in `View::potentiallyRenderVUMeter()` (`:1783`).
- **The safe way already exists:** `View::getModKnobMode()` (`:1702-1711`) handles nullptr.
- **On the Deluge:** address 0 is cacheable memory in the empty CS0 area (see `NullPage`), so the write doesn't fault. A later read through a null pointer can see the value, though. 1.2.1, and with it mastertune, has the same lines (`view.cpp:1356` and `:1371` in v19.0).

## Fix: patch 0103

The two reads use `View::getModKnobMode()`, which gives −1 when there are no modes. The write checks the pointer.

## Tests

`tests/repro/modknob_cv_emu.py`, now in `run.sh`:

| Check | up to 0102 | + 0103 |
|---|---|---|
| (1) PADA made CV in song view, entered, MOD0 to MOD7 | MOD1 to MOD7 write 1 to 7 to address 0 (MOD0 writes 0, which can't be seen) | nothing written |
| (2) control: PADB (a synth), MOD3 | the synth's mode becomes 3 | the same |
| Result | FAIL (1) | PASS |

The VU meter read was checked only by reading the code. Right after boot, song view has no active mod controllable, so in the test MOD0 can't switch the VU meter on.

Seed 106, replayed to input 430 with 0103 and 0104, recorded 0 null writes (the original run had 2).

`tests/repro/run.sh` on the whole series as `nightly` has it (0001-0016, 0101-0103, 0105, 0106, applied with `git am` to 62a516c2): 18 of 18 PASS.

## Status

Runs: a write hook on seed 100 (400 inputs), modknob_cv_emu.py before and after, run.sh three times. Problems: 1 found (the null writes, one site), 1 fixed (0103). Since 0103 the campaign has recorded no null writes (seeds 112-115: 0).
