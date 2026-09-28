# Performance version v12-perf

| File | Version (Settings → Firmware version) | SHA-256 |
|---|---|---|
| `deluge-1.2.1-mastertune-v12-perf-ef5caee8.bin` | `1.2.1-mastertune-v12-perf-ef5caee8` | `d08b09bcfca73b42b03c3fc22daa1a7fb1f2d86db51e2c9129fccf919ce4060c` |

**v12-perf is v12 with faster filters and oscillators and the CPU monitor of the measurement version.** Filters and oscillators compute bit for bit the same as in v12, only with fewer instructions. Sound, handling and songs stay as in v12. The CPU monitor is off after switching on (Settings → CPU monitor, see `diag/README.md`).

**Not tested on the device.** Everything is measured and proven in the Cortex-A9 emulator with the firmware's machine code.

## Gain

Full-load test (`tests/song`): the real firmware with a song of 17 tracks that all play at the same time, 4 bars. The instructions are counted per block of 128 samples.

| | v12 | v12-perf | Change |
|---|---|---|---|
| **Demand** (without CPU protection, all 45 voices on average) | 1,370,014 = 118% | 1,095,537 = 94% | **−20%** |
| Filters (LP/HP ladder) | 534,726 | 338,014 | −37% |
| Oscillators | 396,102 | 318,208 | −20% |
| all other areas | unchanged | unchanged | |
| **As on the device** (CPU protection modelled): voices on average | 20.7 | **28.5** | **+38%** |
| As on the device: pads keep of 4 chord notes | 1.5–2.1 | about 3 | |

The Deluge holds the load at about 60% with its CPU protection. The faster firmware therefore mainly brings **more voices that may sound at the same time**. With this extreme test song the quality reduction (direness) still stays at the maximum, because it already sets in from about 40–49% load. In normal songs with fewer voices the saving lowers how often and how much the Deluge saves on quality.

**Measured one by one** (benchmarks, per block):
- Filters: LP24 mono 10,830 → 7,414, HP 7,748 → 3,427, SVF 7,869 → 5,717, LP24 stereo 17,991 → 12,162.
- Oscillators: saw 1,578 → 1,112, triangle 1,662 → 606, analog square with PW 2,569 → 1,416.

## What changed

1. **Filters** (`0001`, `dsp/filter`):
   - State and coefficients stay in registers during a block.
   - Fixed decisions are made once per block instead of at every sample.
   - Terms that contribute nothing at morph 0 are dropped.
   - The feedback runs over combined multiply-add instructions.
   - Stereo runs in one pass per channel; the noise follows exactly.
   - With parallel routing, NEON adds.
2. **Oscillators** (`0002`, `render_wave.h`, `vector_rendering_function.h`, `voice.cpp`):
   - Interpolation weights and table reads with NEON.
   - Low saw/square and triangle 4 samples at a time.
   - Divisions for sync and PW over the floating-point unit, with an exact correction.
3. **CPU monitor** (`0003`): as in the measurement version `diag/`.

## Why the sound stays the same

Every change was proven by an implementer and recomputed by an independent checker:
- **Checksums** of all outputs in the emulator, v12 against v12-perf, all identical:
  - fixed cases
  - random cases: filters 2,048 × 64 blocks, oscillators 30,000 cases
  - the checkers' own attack cases: about 6,000 and 24,000, with extreme values, resets, blocks of 1–5 samples, mono and stereo
  - `getTableNumber` for all 4.3 billion possible inputs
- **In the whole song:** All 650,108 oscillator calls and all checked filter calls give exactly the same output for the same input. Voices per track and all other areas are unchanged.
- **Reasoning per change:** rounding, overflow, aliasing and every special path. Example: `smmlar(x, y, 0) = x`.
- **Build:** no new warnings. Two complete rebuilds give the same SHA-256. `git am` of all patches (`patches/0001–0012`, then `perf/0001–0003`) on `release_1_2_1` gives exactly this state.

## Limits and open points

- **The emulator counts instructions, not cycles.** How much is left on the device shows the comparison with the CPU monitor (below).
- **The filter code is 17.8 KB larger** (specialised loops). In the emulator that's invisible. On the device it can load the instruction cache more. If the measurement shows that, rare paths get smaller.
- **The song output depends on the firmware's memory layout,** already in v12. The whole WAV file is therefore no proof, but the comparison per call is. The cause is being investigated.
- **Not included:**
  - the wavetable loop, volume/reverb send per track, the effort per block and voice (to follow)
  - the compressor fix (not bit-exact)
  - the L2 cache

## Comparing on the device

1. Copy the test song from `diag/loadtest-card.zip` onto the card (see `diag/README.md`).
2. Flash the **measurement version** `diag/deluge-1.2.1-mastertune-v12-diag-d0d03dcc.bin`:
   - load the song `MT_LOADTEST`, Settings → CPU monitor → On
   - connect `tools/cpu_monitor.html`, Play, let it run for about a minute, save the CSV
3. Flash **v12-perf** and repeat the same.
4. Upload both CSV files here.

To expect:
- more voices (V) at a similar CPU display
- with the test song about as many cuts per second (14 in the emulator for both), but more voices sounding at the same time
- with your own, normal songs a lower CPU display and less often direness above 0

You can go back to v12 at any time with the v12 file.
