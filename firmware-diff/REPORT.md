# deluge.bin compared with the original firmware 1.3

## Result

`deluge.bin` is a custom build of the Synthstrom Deluge community firmware with a single functional extension: an adjustable reference pitch ("Master Tune", A4 in Hz). All other differences in the binary are consequences of this extension.

| | |
|---|---|
| File | `deluge.bin`, 1,737,116 bytes, SHA-256 `786ae3b549b5754b733e11f37b5c40c13073bd3cbd8b11e330d7d9eb6d041309` |
| Version string | `1.3.0-dev-50813bcd-dirty` (short form `c1.3.0`) |
| Build path in the binary | `/home/g2thek/src/DelugeFirmware/…` |
| Base | `SynthstromAudible/DelugeFirmware` `main@50813bcd` of 2026-09-25 |
| `dirty` | There were local, unpublished changes at build time |

**What "original 1.3" means:** There is no finished release 1.3 upstream. There are only the tags `beta` (2026-09-24), `nightly` and `start_1_3_0`. So the comparison is on two levels:

1. **Upstream difference to the official `beta`:** The base `50813bcd` lies 3 commits after `beta`. They are small bug fixes in 3 files (+40/−16 lines):
   - `4fdafedb` don't open another menu (#4944)
   - `04c1bf3a` use underlying param to check highlights for patch cables (#4945)
   - `50813bcd` Bugfix/first last clip double (#4943)

   Not included is the newer upstream commit `47b1d92b` Fix/ param manager leaks (#4920).
2. **Local changes against exactly this base:** That is the main part of this report.

## Method

A direct byte comparison is useless. The local change makes `.bss` 64 bytes bigger, so the whole program starts 0x40 bytes later (`0x2005c3c0` instead of `0x2005c380`). Every absolute address in the image changes.

So the unchanged original was built as a reference:

- the same commit `50813bcd`
- the same official toolchain (DBT v22, `arm-none-eabi-gcc` 14.2.1 xPack)
- configuration `release`
- the same path `/home/g2thek/src/DelugeFirmware`, so that `__FILE__` strings have the same length
- the working tree "dirty" too, so that the version string is identical

The result is `v1.3.0-dev-50813bcd-dirty` with 1,733,332 bytes.

The comparison (`tools/fwdiff.py`) anchors both images by unique 24-byte windows. The anchors are joined into a monotonic chain (LIS). Then every function and data symbol of the reference is compared with its counterpart. Pure relocation effects are taken out: absolute addresses, BL/B/CBZ targets, ARM BL and MOVW/MOVT.

Proof that the reference fits: 6,029 of 7,514 symbols are identical (3,026) or only moved (3,003).

## The local change: Master Tune

**Handling**
- A new submenu **Settings → Tuning** (before "Defaults") with one entry **Master tune (Hz)**. On the 7-segment display they are called `TUNE` and `MTUN`.
- Range 415.3–466.2 Hz, i.e. ±1 semitone around 440 Hz, in steps of 0.1 Hz. Internally an `int` in tenths of Hz is stored (4153–4662). The default is 440.0 Hz.

**Computation** (a new function at `0x20078504` in the fork)
- `Hz = v · 0.1`, `ratio = Hz / 440`
- `cents = 1200 · log2(ratio)` as `float`
- `log2Q24 = lround(log2(ratio) · 2^24)`
- `ratioQ30 = lround(ratio · 2^30)`
- Then the CV channels 0 and 1 are recomputed, if a global state is set (probably a loaded song).

**Effect** (reads found)

| Place | Effect |
|---|---|
| `Voice::calculatePhaseIncrements` (2×) | phase increments of the synth voices × `ratioQ30` |
| `DxVoice::init`, `DxVoice::update` (ARM code) | DX7 engine: `log2Q24` as an offset in the log-frequency domain |
| `CVEngine::calculateVoltage` | CV outputs: + `cents` |
| `Sample::workOutMIDINote` | the root-note detection of samples divides by the master tune Hz instead of a fixed 440 |
| `SampleBrowser::loadAllSamplesInFolder` (3×) | the note ranges when loading whole sample folders, also relative to the master tune frequency |

So all four places where the original uses a fixed 440 Hz are switched (`sample.cpp`, `sample_browser.cpp` 2×, `dx7note.cpp`). No reads were found in the MIDI note output and in audio clips.

**Storage**
- The value is stored in the SPI flash settings in bytes **198–199** (int16, little endian). `FlashStorage::writeSettings` writes them together with bytes 196/197 in one 32-bit store.
- At startup (`deluge_main`) the value is read and checked. If it lies outside 4153–4662, or if the stored settings come from an older firmware version, 4400 (440.0 Hz) applies.
- **Note:** Upstream, bytes 198–199 are unused today. If a future official release uses these bytes differently, the settings may be misread when switching between this build and the official firmware.

**Texts**
- Two new l10n strings, "Tuning" and "Master tune (Hz)", were inserted right after `STRING_FOR_DEFAULTS` (new IDs 701/702).
- All later string IDs move by +2.
- Otherwise there are no new or removed texts.

**Newly linked library functions (newlib libm)**
- `log2` (544 B), `lround` (124 B) and `__log2_data` (2,192 B)
- `__log2_data` and `lround` match the toolchain's `libm.a` byte for byte, `log2` except for address references.

## Why about 1,100 functions differ

The binary is built with LTO and section anchors. New global variables therefore move the offsets of many other globals relative to their anchor. Together with the string IDs moved by +2, the immediates in many functions change without their logic changing.

| Class (1,101 functions) | Count | Meaning |
|---|---:|---|
| `reloc` | 47 | only address remains |
| `struct` | 197 | only `[reg, #offset]` moved |
| `const` | 64 | only constants (mainly string IDs) |
| `struct+const` | 204 | both |
| `logic` | 589 | different instruction sequence (codegen follow-on effects; contains the tuning places above) |

On top come 384 changed data objects (mainly vtables and menu tables). The complete list is in `changed_symbols.csv`.

**Size balance (+3,784 B)**

| Area | Growth |
|---|---:|
| `.text` | +1,208 B (of it 668 B libm, **about 540 B of new app code**) |
| `.rodata` | +2,448 B (of it 2,192 B `__log2_data`, 256 B vtable/menu lists) |
| `.data` | +16 B |
| `.sdram_data` / `.sdram_rodata` | +32 / +44 B (texts and l10n entries) |
| `.exceptions` | +12 B |
| `.bss` | +64 B |
| `.sdram_bss` | +128 B (two new menu objects) |

**Assessment:** With about 540 bytes of new app code the tuning feature is practically fully explained: the computing function, the menu class, flash reading/writing and the changes in voice, DX7, CV and sample detection. There is hardly room left for other hidden functions. Small logic changes can't be ruled out entirely without the source code, though.

## Reproduction

```sh
git clone --filter=blob:none https://github.com/SynthstromAudible/DelugeFirmware /home/g2thek/src/DelugeFirmware
cd /home/g2thek/src/DelugeFirmware
git config core.abbrev 8            # short hash with 8 characters as in the original
git checkout 50813bcd62d806e0f49c602dcbbb68166e58916d
touch LOCAL_BUILD_MARKER            # tree "dirty" -> identical version string
./dbt build release                 # downloads toolchain v22

# working folder with ref.bin/ref.elf (from build/Release) and fork.bin (= deluge.bin)
T=/path/to/this/repo/firmware-diff/tools
export WORK=$PWD/work TC=/home/g2thek/src/DelugeFirmware/toolchain/v22/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-
cd $WORK
python3 $T/fwdiff.py ref.bin fork.bin ref.elf $TC diff.json   # symbol comparison -> diff.json
python3 $T/classify.py                                         # classification -> classified.json
python3 $T/gaps.py                                             # biggest insertions
python3 $T/callers.py 20172738                                 # callers of log2 in the fork
python3 $T/annot.py 20078504 200785e4                          # new tuning function, annotated
```
