# Optimization: collected findings

As of 2026-09-26, firmware v12 (`mastertune-v12`, commit `f89b478c`). This document is extended as running measurements finish (sections 6 and 8).
Raw data from the measurement and review agents: `research/raw/*.json`. Benchmarks: `tests/bench/<area>/run.sh`.

## 1. Method

**Measurement**
- The real firmware code runs in the emulator (unicorn 2.1.4) as Cortex-A9 code. It is built with the firmware's flags: Thumb-2, NEON hard-float, `-O2`, `-funsafe-math-optimizations`, `-fno-inline-functions`. Only LTO is missing.
- The emulator counts the executed instructions per block of 128 samples (2.9 ms), averaged over about 10 blocks after warm-up. `ARM_PROFILE=1` gives the instructions per function.
- Scale: **% CPU = instructions per block / 1,161,000** (400 MHz, 1 instruction per clock cycle).
- Limits:
  - Cache, pipeline and dual issue are missing. The numbers are therefore good for comparisons (before/after, part against part). As an absolute load they are rough.
  - Memory effects (SDRAM, L2 cache) are not visible.

**Quality rule: bit-identical**
An optimization counts as safe if the output stays the same bit for bit. This is proven in four stages:
1. Checksums before/after, always ARM against ARM. On the PC the fallback functions for `*_rounded` in `fixedpoint.h` round differently.
2. Many inputs, random ones too, and edge cases:
   - odd block sizes (render windows are cut at ticks)
   - parameter changes in the middle of a sound
   - silence, full scale and deliberate overflows
3. The whole song in the full-load test gives the same output before and after (section 6).
4. A mathematical argument, checked by a reviewer.

Pitfalls:
- Because of fast math, a rearranged floating-point calculation can give different bits.
- NEON accesses with an alignment hint abort on the hardware when the alignment is wrong, possibly not in the emulator.

What is not bit-identical is measured as for reverb and delay: deviation in dB, spectrum, freedom from clicks. It is marked in the README as a sound change and shipped separately.

**Already done, earlier packages**
- v3 (package A): NEON shift in the interpolation buffer, compiler flags, UI scheduler, encoders.
- v4 (package B):
  - skip silent chains
  - voice culling from the community
  - output in one pass
  - `memset`
- v9: streaming fixes (priority of the load queue, more read-ahead, pin counter).
- v11: delay with cubic interpolation. Modulated, it costs 3.5% instead of 5.0% in v10, because mostly only one buffer runs now.

**Why saving CPU also means sound quality:** Under load the firmware raises `AudioEngine::cpuDireness` and then saves on quality:
- Oscillators take coarser wavetables (`voice.cpp`, `tableNumber < cpuDireness + 6`).
- Pitched samples are interpolated linearly instead of with sinc (`sample_controls.cpp`).
- In the extreme case voices are switched off (culling).

Every saving therefore lowers the chance that full songs lose quality this way.

## 2. Costs at a glance

Instructions per block of 128 samples, per instance or voice, counted in the emulator.

| Component | Instructions | CPU | Note |
|---|---|---|---|
| Oscillator saw/square/sine (table) | 1,500–1,600 | 0.13% | per oscillator and voice |
| Oscillator square with PW | 2,600 | 0.23% | |
| Oscillator saw unison 4 / 8 | 6,300 / 12,500 | 0.54 / 1.08% | |
| Filter LP24 mono / stereo | 10,800 / 21,600 | 0.93 / 1.86% | per voice; from about 10% resonance the tanh path |
| Filter LP24 with drive, oversampling | 27,000 | 2.32% | high cutoff and resonance |
| Filter LP24 + HPF stereo | 35,800 | 3.09% | |
| SVF mono | 7,900 | 0.68% | |
| Sample native stereo | 3,500 | 0.30% | without pitch change |
| Sample linear stereo (+7 semitones) | 12,000 | 1.03% | |
| Sample sinc stereo (+7 semitones) | 20,800 | 1.79% | |
| Timestretch sinc stereo | 40,300 | 3.47% | two read heads |
| DX7 modern (NEON) / MkI | 8,700 / 26,600 | 0.75 / 2.29% | per voice |
| Chorus / flanger | 8,900 / 9,100 | 0.77 / 0.78% | per track |
| Phaser | 19,900 | 1.72% | |
| EQ bass + treble | 7,200 | 0.62% | |
| SRR 1/4 + bitcrush | 5,400 | 0.46% | |
| Compressor (song) | 14,900 | 1.28% | |
| Delay static / modulated / with filters | 9,200 / 40,100 / 44,400 | 0.8 / 3.5 / 3.8% | v11 |
| Delay analog mode, modulated, with filters | 97,000 | 8.4% | of which impulse response 42,800 (3.7%) |
| Reverb Freeverb / Mutable / Digital | 70,200 / 39,100 / 53,600 | 6.0 / 3.4 / 4.6% | once per song |
| Drone 1 sine tone / 1 binaural | 4,100 / 6,300 | 0.35 / 0.54% | |
| Drone 16 binaural with harmonics | 79,500 | 6.85% | |

**In context:** Costs per voice (oscillators, filters, sample reading) multiply with the number of voices and therefore determine the load in full songs. Example: 30 voices with an LP24 filter alone cost about 28% (mono) to 56% (stereo) CPU on this scale. The song effects occur only once. The full-load test (section 6) shows the real distribution in a full song.

## 3. Findings and proposals by area

According to the measurement agents, all proposals below are possible **bit-identically**. This is an estimate and must be proven during implementation. Savings apply per block of 128 samples.

### 3.1 Filters (`dsp/filter`)
- Hot loops:
  - `LpLadderFilter::doFilter`/`doFilterStereo`: about 84 instructions per sample for LP24, over 99% of the cost.
  - `HpLadderFilter::doFilter`: about 60 per sample, 89 with 2D tanh.
  - `SVFilter::doFilter`: about 61 per sample.
- Main cause: per sample, about 20 values are reloaded and 5–7 are stored. The compiler cannot rule out aliasing with the output buffer.

| Proposal | Location | Saving | Risk |
|---|---|---|---|
| State, coefficients and `jcong` in local variables; specialize `sampleIncrement==1` | lpladder/hpladder/svf `doFilter*` | LP24 about 1,000 mono, 2,000 stereo; HP/SVF 500–700 | low–medium (registers) |
| Saturation branch (`morph>0 \|\| resonance>510M`) out of the loop | `lpladder.h` `scaleInput` | 400–600 mono | low |
| Feedback sums with `smmlar` instead of `smmulr`+`add` | lpladder `do24dB`/`do12dB`/`doDrive`, hpladder | 384 / 250 / 128 | low (rounding identical) |
| HP ladder: `temp_fc` at `morph==0`, resonance branches out of the loop, `hpfLastWorkingValue` only at the end of the block | `hpladder.cpp` | about 1,300 mono (17%) | low |
| SVF: skip `smmlar` when `c_band`/`c_high` is 0; `band_mode` out of the loop | `svf.cpp` | about 640 mono | low |
| NEON addition for parallel routing | `filter_set.cpp` | 800 mono, 1,600 stereo | low |

Total: LP24 15–20%, HP about 20%, SVF about 12%. With 30 voices with a filter that is **about 4–5% CPU**.
Not recommended: L and R in NEON lanes. `vqrdmulh` rounds differently, so the result would not be bit-identical.

### 3.2 Oscillators (`render_wave.h`, `vector_rendering_function.h`, `voice.cpp`)
- Hot loops:
  - `renderWave`: 1,371 per call (87%), 42 instructions per 4 samples.
  - `renderPulseWave`: 2,468.
  - Triangle below about 711 Hz as a scalar loop.
  - Divisions in the PW and sync setup (`__udivmoddi4` 626).

| Proposal | Location | Saving | Risk |
|---|---|---|---|
| Interpolation weights by NEON from the phase vector, drop the `{0}` inits, `applyAmplitude` as a template | `vector_rendering_function.h`, `render_wave.h` | `renderWave` about 290 per call (−21%), pulse about 500; unison 8 about 2,300 | low |
| Triangle loop in NEON (`smmlar` exactly as `vmull`+`vrshrn`+`vadd`) | `voice.cpp` `renderOsc` | about 1,100 per call | low–medium |
| Vectorize crude saw/square (below about 72 Hz) | `voice.cpp` | saw about 430, square about 850 | low |
| PW/sync divisions via VFP double with correction | `voice.cpp` | about 200, analog square PW +550 | medium |
| `getTableNumber` via `clz` | `voice.cpp` | about 30 | low |

- Not measured:
  - wavetable oscillator (needs a sample, clusters and the FFT)
  - pan loop for stereo unison, estimated at about 1,300 per part. It could also be done bit-identically with NEON.

### 3.3 Effects (`mod_controllable_audio.cpp`, `rms_feedback.cpp`, `impulse_response_processor.h`)

| Proposal | Location | Saving | Risk |
|---|---|---|---|
| **Analog delay impulse response with NEON:** `vqrdmulhq_s32` with halved coefficients. All 26 coefficients are even, so the result is identical to `smmulr`. | `impulse_response_processor.h` `process` | 42,800 → about 6,000–8,000, **about −3% CPU per analog delay** | low |
| Phaser: allpass state in registers, L/R separate, 6 stages unrolled | `processFX` (PHASER) | 6,000–7,500 | low |
| Compressor: `calcRMS` into the render loop, state local, blend without `memcpy` | `rms_feedback.cpp` | about 2,000 (+400 with blend) | low |
| EQ: states local, loop specialized for bass/treble | `processFX`/`doEQ` | about 2,000 | low |
| Mod FX loop per type (template), LFO and index local | `processFX` (Chorus/Flanger) | 1,500–2,500 | low |
| SRR state local, bitcrush with NEON `vand` | `processSRRAndBitcrushing` | SRR 1,000–1,500, bitcrush 450 | low |

Not measured: Grain.

### 3.4 Samples, timestretch, DX7 (`sample_low_level_reader.cpp`, `interpolate.h`, `dsp/dx`)
- Hot loops:
  - `readSamplesResampled`: sinc stereo 13,278 per block.
  - `interpolate()`: one function call per sample, 58 instructions stereo.
  - Linear: `jumpForwardLinear` and `interpolateLinear` also as a call per sample.
  - MkI engine: about 30 instructions per sample and operator, with branches.

| Proposal | Location | Saving | Risk |
|---|---|---|---|
| Sinc loop: `interpolate()` inline, `oscPos` and amplitude local, template by channels, condensing and cache writing | `readSamplesResampled` + `interpolate.h` | 3,000–4,500 per sinc voice (15–20%), double with timestretch | low–medium |
| Interpolation buffer for the whole block in NEON registers (`vext`) | `readSamplesResampled`, `shift_buffer.h` | 1,500–2,000 stereo | medium |
| Stereo reduction with `vpadd` | `interpolate.h` | about 650 | low |
| Linear loop inline, state local | `readSamplesResampled` (else branch) | 5,000–6,000 (about 45%) | low |
| MkI engine: `sinLog` without branches, sign via XOR mask | `EngineMkI.cpp` | 5,000–6,000 per MkI voice (20%) | low |
| Allow `neon_fm_kernel` with n=128, stack reloads in `compute_fb` | `fm_core.cpp`, assembler | about 550 | medium, little benefit |

Not measured: `considerUpcomingWindow`/clusters and `TimeStretcher::hopEnd`.

### 3.5 Drone (v12)
- Profile:
  - Outer loop in `Drone::render`: 1,169 instructions per block
  - `memset`: 222
  - `renderVoice`: about 20 instructions per sample
- Candidates:
  - NEON for the float → int conversion and the mixing
  - no `memset` of the mix buffer (the first voice writes instead of adding)
- The drone computes in float with fast math. Bit identity is therefore only certain if the order of the operations stays unchanged. Otherwise it is measured.

## 4. Bugs found

- **Compressor:** `calcRMS` uses `hpfL` for both channels, and `hpfR` stays unused (`rms_feedback.cpp`, 1.2.1). The fix is not bit-identical and therefore comes separately as a sound change.
- The findings from the v12 review are fixed and described in the README under v12. Raw data: `raw/review-v12-drone.json`.

## 5. SD card, driver, memory

**How the Deluge reads (1.2.1).** Checked by an agent and a reviewer, raw data in `raw/sd-driver.json`.
- **Bus:** 4 bit, high speed via CMD6, P1φ 66.67 MHz / 2 = **33.3 MHz**. That is 16.7 MB/s gross and about 15.5 MB/s net.
  - No headroom: P1φ is fixed, `/1` would give 66.7 MHz and be above the standard, and UHS (1.8 V, CMD11) is not available.
  - Cards without high speed run at 16.7 MHz.
- **Streaming bypasses FatFs:** The sector addresses of the clusters are precomputed (`sdAddress`). FASTSEEK, exFAT and `FF_FS_TINY` play no role in playback.
- **Per cluster** (size = the card's cluster, usually 32 KB):
  - Sequence: `CMD13`, `CMD18` with automatic `CMD12`, DMA with 64-byte bursts, then `CMD13` again.
  - Estimated 2.2–2.6 ms. Of this, the transfer takes about 2.0 ms, the card's access time 0.1–0.5 ms with peaks of 10–100 ms, the commands about 50 µs.
  - That gives about 13 MB/s, or around 70 stereo 16-bit voices with favorable accesses.
  - According to Rohan's comment in `diskio.c`, a bad card occasionally takes 200 times longer than a good one.
- **Waiting:** `USE_TASK_MANAGER` is active. While waiting for a command and the DMA, the driver yields to `TaskManager::yield`. All tasks run there, including UI and MIDI.
  - Hardware queue with depth 1.
  - The pause between the end of the DMA and the next command depends on how long the currently running task takes, e.g. OLED rendering. That is the real lever for latency.
- **Writing** (recording): 32 KB via `CMD25`, without pre-erase (ACMD23). The audio keeps running. The main loop typically stalls for 3–10 ms, during the card's garbage collection up to 250–500 ms.

**Effect of a faster card**
- No effect on the DSP load.
- Little effect on drums and short samples, because they stay in SDRAM.
- Medium effect on many long samples and audio tracks: here the access time counts.
- Largest effect on recordings: write peaks lead to dropouts.
- Recommendation:
  - a brand-name card with Class 10 / U1 or better, plus A1; V30 if you record a lot.
  - A2 and UHS bring nothing.
  - FAT32 is mandatory (no exFAT), and 32 GB SDHC is the simplest case.

**Driver options**

| Option | Gain | Verdict |
|---|---|---|
| Clock above 33.3 MHz, UHS | none possible | hardware limit |
| Drop `CMD13` before and after reading | 10–20 µs per cluster (<1%) | no, the status check is lost |
| Several clusters with one `CMD18` | unproven: buffers not contiguous, queue by priority | no |
| Asynchronous reading by interrupt | main loop free for 2–3 ms per cluster | no, high risk (reentrancy, FatFs) |
| ACMD23 before writing | 0–20% write speed, depends on the card | not for now (reviewer: risk overestimated) |
| Larger write blocks when recording (128 KB) | fewer busy phases | maybe later |
| **Allow only short tasks during SD waits** | lower latency when streaming | worth checking, measure first |
| **Measurement build:** `REPORT_LOAD_TIME` with a lower threshold, `REPORT_AWAY_TIME` | real latencies instead of estimates | yes, only with hardware |

**From the community (main since 1.2.1)**
- **L2 cache:**
  - 1.2.1 uses only L1 (32 KB code, 32 KB data). The 128 KB L2 stays off (`resetprg.c` only calls `R_CACHE_L1Init()`).
  - Upstream in two stages:
    - `c44d2476` (12/2024): L2 for code only. Data is locked out by lockdown (`REG9_D_LOCKDOWN0 = 0xFFFFFFFF`), so DMA is not affected. 19 lines.
    - `a2f8bc51` (04/2026): data too, enabled only at the end of the boot, plus prefetch (`REG1_AUX_CONTROL |= 0x30000000`). SD reading invalidates L1 and L2 before the DMA, and the FatFS buffers are aligned to 32 bytes.
    - `260ac76c` (a day later): also write back L2 before writing to the card and before the OLED DMA.
    - `c0586341`, `5d3d093e`: chainloader (loading firmware via USB SysEx). The bug is already in 1.2.1 (L1), independent of L2.
  - DMA paths in v13, checked in the code:
    - SD reading and writing (`sd_read.c`, `sd_write.c`) and the OLED (`oled_low_level.c`, `oled.cpp`) need L2 maintenance, exactly at the places from upstream.
    - Audio (SSI) and UART (MIDI, PIC) run over the uncached mirror address: not affected.
    - USB runs without DMA (`USB_CFG_DMA` off): not affected. SD over USB (v7) and the faster saving (v13) write through FatFS, so through `sd_write.c`.
  - **Probably the biggest CPU gain,** above all for data in SDRAM: delay and mod FX buffers (`allocLowSpeed`), wavetables and samples (`allocStealable`). Upstream gives no number, and the emulator cannot measure it.
  - Risk: low for code only. With data, medium: a forgotten path gives rare errors, in the worst case wrong bytes in files on the card.
  - Plan: first a test version with code L2 only, measured on the device (CPU monitor from v13, test song `diag/loadtest-card.zip`). Data only after that, with all follow-up fixes.
- **MIDI/clock during `routineForSD()`** (`9cd09fb7`):
  - With the task manager, `routineForSD()` only calls `AudioEngine::routine()`, not `playbackHandler.routine()`. The external clock therefore stalls while `routineForSD()` runs: song loading (`load_song_ui.cpp`), USB, flash.
  - Own check: 1.2.1 is built the same way (`USE_TASK_MANAGER` set, `#ifndef USE_TASK_MANAGER` before `playbackHandler.routine()` in `routine_()`).
  - The research agent considered the change not transferable, and the reviewer did not check this. Confirm it again in the code before porting.
  - Small, low risk.
- `FF_FS_TINY 0`: only slightly faster when loading files (XML), not when streaming.
- Upstream has no speed-up in the driver itself.

## 6. Full-load test with a many-track song

`tests/song/run.sh <firmware tree|deluge.elf> [out] [bars]`, run time about 1 minute. Raw data: `raw/song-load-run-1.json`.

**Setup**
- The **unchanged v12 `deluge.elf`** runs in the emulator.
- Sequence: `resetprg`/`deluge_main` up to `registerTasks`, then `setupStartupSong` (loads `SONGS/DEFAULT.XML` from a FAT32 image with 32 KB clusters), `playButtonPressed`, then `AudioEngine::routine()` per window.
- Only the hardware is emulated: timers, DMA, SPI, flash. The SDHI initialization is skipped.
- The whole routine is counted, including ticks and output. The result is deterministic: two runs are identical.

**Song:** 120 BPM, everything at once.
- 8 synths, all with 4-note chords, Saw+Square and LP24:
  - Unison 4 + HPF + LFO + Chorus
  - Unison 4 + Phaser + Delay
  - HPF + Flanger + Compressor
  - Bitcrush + SRR
  - Arp 16ths + Delay
  - Unison 4 + HPF
  - FM (DX7, algorithm 5)
  - Wavetable
- Kit with 8 tracks in sixteenths. The kick drives the sidechain, and two tracks are pitched.
- Audio track: a loop at 100 BPM, timestretched to 120 BPM.
- Reverb Mutable, 4 binaural drone tones.
- Output: peak −1.5 dBFS, RMS −18 dBFS, nothing clips, no NaN.

**Result, run 1** (recalculated by the reviewer)
- **Mean 962,920 instructions per window of 128 samples = 83% CPU. Peak 1,303,588 = 112%,** at chord changes.
- Voices: mean 29.7, peak 36.
- The cost grows about linearly: **around 126,000 fixed instructions per window plus 28,000 per voice** (r = 0.98).
- Windows cut short at clock ticks (10 samples) still cost about 200,000. So the overhead per window and voice is high.
- Variant with 1.6 s release: 58 voices, mean 108%, peak 151%.
- Digital reverb instead of Mutable: 54,000 instead of 40,000 instructions per window.

| Area | Share | Instructions per window |
|---|---|---|
| Filters (LP/HP ladder) | 35.1% | 337,500 |
| Oscillators incl. wavetable | 26.3% | 253,000 |
| Track FX (mod FX, bitcrush, volume/pan/reverb send) | 10.6% | 101,700 |
| Voices, patcher, envelopes, LFOs | 9.3% | 89,700 |
| Reverb (Mutable) | 4.1% | 39,900 |
| Sidechain, compressors | 3.2% | 31,200 |
| Sample reading, interpolation, timestretch | 2.2% | 21,300 |
| Delay | 2.1% | 20,600 |
| Drone | 2.1% | 20,500 |
| FM (DX7) | 2.1% | 20,000 |
| Memory, other | 1.4% | 13,600 |
| Song/master FX, output | 1.2% | 11,500 |
| Playback, sequencer | 0.3% | 2,500 |

Top functions:

| Function | Instructions | Share |
|---|---|---|
| `LpLadderFilter::doFilter` | 252,600 | 26% |
| `Voice::renderOsc` | 178,200 | 18.5% |
| `HpLadderFilter::doFilter` | 78,100 | |
| `Sound::render` | 75,900 | |
| `WaveTable::doRenderingLoop` | 56,700 | |
| `ModControllableAudio::processReverbSendAndVolume` | 56,700 | 5.9% |
| `reverb::Mutable::process` | 39,800 | |
| `ModControllableAudio::processFX` | 38,000 | |
| `RMSFeedbackCompressor::render` | 24,300 | |
| `Delay::process` | 19,800 | |

**Cross-check against the single benchmarks** (reviewer): the values match.
- LP ladder: 11,200 per voice in the song, 10,800 in the bench.
- HP ladder: 7,200 against 7,700.
- Oscillators: 179,000 extrapolated, 178,000 measured.

**Run 2: correction run** (raw data `raw/song-load-run-2.json`). Chords and arp now play the whole bar. This is the sustained load.

| | Demand without CPU protection | As on the device (culling and direness emulated) |
|---|---|---|
| Instructions per 128 samples (mean) | 1,366,716 = **118%** | 701,452 = **60%** |
| Peak | 2,143,439 = 185% | 1,714,556 = 148% (chord change) |
| Percentiles 5/50/95/99 | – | 57 / 59 / 68 / 86% |
| Voices mean / max | 45.2 / 66 | 20.7 / 52 |
| Voices switched off | – | **14.2 per second** (94 soft, 20 force in 4 bars) |
| `cpuDireness` | – | **at 14 in 100% of the windows** (maximum) |

- With CPU protection the pads keep only 1.5–2.1 voices. **About half of the chord notes are switched off.**
- Direness 14 also lowers the cost per voice, e.g. because the oversampling in the LP ladder is dropped.
- Shares under sustained load: filters 39%, oscillators 28.9%, voices/patcher/envelopes 9.5%, track FX 7.8%, reverb 2.9%, FM 2.7%, sidechain/compressors 2.3%, sample reading 1.6%, delay 1.5%, drone 1.5%.
- Top functions: `LpLadderFilter::doFilter` 403,108, `Voice::renderOsc` 279,575, `HpLadderFilter::doFilter` 121,254, `Sound::render` 110,416, `WaveTable::doRenderingLoop` 88,073, `processReverbSendAndVolume` 59,412.
- **Voices per track** without CPU protection (mean / max / share of the time):
  - Pads and wavetable 5.6 / 8 / 100%
  - Arp 2.3 / 3
  - FM 4.5 / 8
  - Kit 0.1–1.0
- **Short windows:** 64 of 2,816 windows have only 10 samples because of clock ticks. They cost 2.3 times as much per sample. Roughly, one call of `routine()` costs about 154,000 plus 9,500 per sample. If `routine()` were called every 64 instead of every 128 samples, the demand would rise from 118% to about 131%.
- **Reverb** over the same samples: Mutable 39,909, Digital 54,343 instructions per window (+14,434, +1.2% CPU). Everything else is the same.

**Thresholds of the CPU protection** (`audio_engine.cpp`, the same in 1.2.1 and upstream main): the basis is the render time of the last call, measured in samples.

| Render time (samples) | Share of a 128-sample window | Consequence |
|---|---|---|
| from 50 | 39% | `cpuDireness` 1: first quality reduction |
| from 63 | 49% | Direness 14 (maximum): coarsest oscillator tables, linear instead of sinc interpolation, no filter oversampling |
| from 80 | 62.5% | Soft culling: fade voices out quickly |
| from 112 | 87.5% | Hard culling |

**Conclusion:** The Deluge already saves on quality from about 40% load and on voices from about 60%. Every saving therefore counts twice: it brings more voices and better quality. Only the measurement version on the device will show whether the thresholds are too cautious. It shows the direness live. Changing the thresholds would not be bit-identical and would need tests on the device against dropouts.

**Run 1, found by the reviewer, fixed in run 2:**
1. In the last eighth of every bar the synths are silent. In the steady state the load is **about 89%**, not 83%.
2. **Culling on the device:** The soft cull starts when a window needs more than 62.5% of its time, the hard cull from 87.5%. At around 89% a real Deluge would **constantly switch off voices** with this song and raise `cpuDireness`. In the emulator this did not happen, because the run-time measurement returned 0. The correction run emulates it.
3. Voices per track are added.
4. Repeat the reverb comparison over the same bars.

According to the reviewer's counts, all 16 sounds play, 6 pads play with 4 voices 90% of the time, and all 8 kit tracks are active.

## 7. Prioritization (after the full-load test)

Basis: share in the full song times the estimated saving from the benchmarks. All items are planned as bit-identical.

| # | Target | Share in the song | Expected saving of the total | Basis |
|---|---|---|---|---|
| 1 | **Filters** (LP/HP ladder, SVF): state in registers, branches out of the loop, `smmlar` chains | 35% | **about 6–7%** of the total load | 3.1: LP 15–20%, HP about 20% |
| 2 | **Oscillators:** NEON weights in `renderWave`/`renderPulseWave`, triangle and crude in NEON | 26% (of which `renderOsc` 18.5%) | about 3–4% | 3.2: −21% `renderWave` |
| 3 | **`processReverbSendAndVolume`** (newly found, not yet benchmarked) | 5.9% | open, probably a lot (scalar loop per sample) | song profile |
| 4 | **Overhead per window and voice** (`Sound::render`, patcher, envelopes) | 9.3% + 126,000 fixed | open | song profile: 10-sample windows cost about 200,000 |
| 5 | Wavetable loop | 5.9% | open | not yet benchmarked |
| 6 | Mod FX (phaser, chorus), compressor, EQ | together about 6% | about 1–2% | 3.3 |
| 7 | Analog delay impulse response NEON | not active in the song | about 3% per analog delay | 3.3 |
| 8 | Sample reading (sinc/linear) | 2.2% in this song | small here; large in sample-heavy songs | 3.4 |
| 9 | Drone NEON | 2.1% | small | 3.5 |

**Bottom line:**
- Filters and oscillators together make up over 60% of the load and are the clear first step.
- Places 1–2 together save about 10% of the total load. That lowers this song's demand from about 89% to about 80%, so less culling and reduced quality less often.
- Places 3–5 need benchmarks first.

**Separately, with measurement on the device:**
- L2 cache
- MIDI/clock fix in `routineForSD`
- Compressor fix (`hpfR`)

**Procedure per optimization:**
1. An agent implements it, with bit-identical proof as in section 1 and a counted saving.
2. A reviewer checks it.
3. Finally the whole song before/after: the same output (`measured.wav`), fewer instructions.

## 7a. Implementation: filters and oscillators (bit-identical, reviewed)

Raw data: `raw/perf-filters-oscillators.json`. Patches: `perf/`. Branches `perf-filters` (`1de85ade`) and `perf-oscillators` (`a63fc069`), combined on `mastertune-v12-perf`.

| | Benchmark (instructions per block) | Whole song, demand per 128 samples |
|---|---|---|
| Filters | LP24 mono 10,830 → 7,414; HP 7,748 → 3,427; SVF 7,869 → 5,717; LP24 stereo 17,991 → 12,162 | 1,370,014 → 1,173,302 (**−14%**); LP `doFilter` 403k → 271k, HP 121k → 57k |
| Oscillators | saw 1,578 → 1,112; triangle 1,662 → 606; analog square PW 2,569 → 1,416 | 1,370,014 → 1,292,117 (**−6%**); `renderOsc` 280k → 202k |

**Filter changes:**
- Keep state, coefficients and `jcong` local.
- Decide fixed branches once per block.
- Drop the terms that vanish at morph 0.
- Write `hpfLastWorkingValue` only at the end of the block.
- Feedback via `smmlar`/`smmla`.
- Stereo in one pass per channel. The noise jumps ahead by an exact LCG double step, secured with `static_assert`.
- NEON addition for parallel routing.

**Oscillator changes:**
- NEON interpolation weights from the phase vector, table reads with `vld2.16`, `applyAmplitude` outside the loop.
- Crude saw/square and triangle with 4 lanes.
- Sync and PW divisions via `vdiv.f64`: exact at 32 bits, with a correction step at 64 bits.
- `getTableNumber` via `clz`, checked for all 2^32 inputs.

**Proof:**
- Benchmarks ARM against ARM identical in all fixed and random cases: filters 2,048 × 64 blocks, oscillators 30,000 cases, plus a good 6,000 and 24,000 of the reviewers' own cases respectively. Deliberately inserted bugs are detected.
- In the song, all 650,108 `renderOsc` calls and all checked filter calls are identical.
- No new warnings, no dangerous NEON alignment hints.

**Open findings from the reviewers:**
- **The song output depends on the memory layout.** Even unchanged v12 code with a different version name gives a different `measured.wav`. Comparing the whole WAV is therefore no proof, but a control build with the same layout is. The cause is being investigated (workflow `layout-dependence`): uninitialized memory or an order by address.
- **Filter code +17.8 KB** (templates inline). The stack of `doFilterStereo` grows from 104 to 528 B, and the internal heap gets smaller by the same amount.
  - Invisible in the emulator. On the device possibly more I-cache misses (L1 32 KB).
  - Possible: mark rare paths with `[[gnu::cold]]`/`noinline`. Decide only after the measurement on the device.
- **Two hangs, already in 1.2.1:** analog square with PW at `phaseIncrement` 1 and sync at `resetterPhaseIncrement` 1 (`samplesIncludingNextCrossoverSample` overflows to 0). Hardly reachable in practice, because such low frequencies are needed. Optionally, limit the increments from below.
- The test song does not cover triangle below 711 Hz, analog square with PW, osc sync and ring mod. These paths are only proven by benchmark.

## 7b. Performance version v12-perf and the next round

- **v12-perf** (`perf/`, SHA `d08b09bc…`, branch `mastertune-v12-perf` = v12 + filters + oscillators + CPU monitor):
  - Demand in the full-load test 1,095,537 instead of 1,370,014 (−20%).
  - As on the device: 28.5 instead of 20.7 voices on average (+38%), about as many voices switched off (14/s), direness constantly 14.
  - Two complete rebuilds are identical. The patch series is checked.
- **Reference for the next round:** `/home/user/work/baseline-v12-perf`, song result in `song/`, `measured.wav` SHA `c825dab9…`.
- **Next round, track effects** (workflow `perf-trackfx`, running):
  - `processReverbSendAndVolume` 59,412
  - `processFX` 38,643
  - Compressor 30,725
  - SRR/bitcrush 5,397
  - Proof by benchmark and by comparing every call in the song.
- **After that, the voice path:** `Sound::render` 110,543, overhead per block and voice, `renderBasicSource` 28,168.
  - This only works with proof in the song, so it waits for the result of the layout investigation.
- **Later:** wavetable loop 88,073, FM 36,614.

## 7c. Bug found: LFOs start with a random phase (1.2.1), fixed in v13

Raw data: `raw/layout-dependence.json`.
- **Cause:** `LFO` (`modulation/lfo.h`) leaves `phase` and `holdValue` uninitialized. `Sound::Sound()` does not set the three timestamps `timeStartedSkippingRendering{ModFX,LFO,Arp}`.
- **Effect:** On the first note, the LFO, the mod FX LFO and the arp advance by "timer minus old value". Free-running LFOs and mod FX therefore start at a random point after every load. A random-walk LFO can start with an offset of up to about ten times its range, which decays only slowly.
- Upstream (main) has the same code in these places.
- **Fix in v13:**
  - `phase = 0`, `holdValue = 0`
  - timestamps in the constructor as in `startSkippingRendering()`
  - `whichNoteCurrentlyOnPostArp = 0`
- **Proof in the emulator:** the same song with two different RAM fills before the start (`--fill 0xA5A5A5A5` / `0x5A5A5A5A`).
  - v12-perf: 352,580 of 352,896 samples differ, −16.7 dB, up to 3,749 LSB.
  - v13: 30 samples by 1 LSB (−113 dB). The rest comes from small heap blocks that are never written during loading (FFT configuration, wavetable bands, patch cables) and is inaudible.
- **Another uninitialized value, found in the track FX review:** `modFXLFOWaveType` with GRAIN in `processFX`. It is fixed along with the rest in v13.
- **Comparison recipe for future patches on a v12 base:** `EMU_OPTS="--init-sounds --seed 1"` (see `tests/song/run.sh`). From v13 on, `--init-sounds` is no longer needed.

## 7d. Track effects (bit-identical, reviewed)

Raw data: `raw/perf-trackfx.json`. Patch `perf/0004`.

| Change | Before | After |
|---|---|---|
| `processReverbSendAndVolume` with NEON | 59,412 | 13,977 |
| Phaser/chorus/flanger each with its own loop | 38,643 | 24,040 |
| EQ specialized | 7,210 | 4,021 (bench) |
| SRR local, bitcrush NEON | 5,397 | 5,014 |

- **Song:** demand 1,095,537 → 1,035,101 (−5.5%). As on the device: 28.5 → 31.9 voices.
- **Proof:**
  - 22 checksums with 4 × 4,000 random cases
  - all 165,312 calls in the song identical (with `--init-sounds --seed 1`)
  - the reviewer with their own edge cases and a deliberately inserted mutation
- **Compressor not changed:** the floating-point calculation chose different instructions depending on the context, so the result would not have been bit-identical.

## 7e. Saving during playback (v13)

Raw data: `raw/save-speed.json`. Commit `c6201e47` (on v13).
- **Problem in v12:** The crackle fix from v11 served the audio at every written character while saving. That was clean but slow: `routine()` ran about every 5 samples and rendered windows of around 24 samples.
- **Fix:** `routineWhileAccessingFile()` serves audio and UI only when at least 8 samples are due. Then `routine()` renders at least 64 samples ahead (`minNumSamplesToRender`, only during the file access).
- **Measured in the emulator,** saving during playback:

| Case | Before | After | Biggest gap after |
|---|---|---|---|
| 3 synths (about 50% CPU), from bar 2 | 344 ms | 21.8 ms | 71 samples |
| 5 synths (about 70% CPU), from bar 2 | 716 ms | 65.7 ms | 97 samples, no underrun |
| mid-bar, 3 / 5 synths | 80 / 180 ms | 10.3 / 16.0 ms | 56 / 66 samples |

- The written XML is identical, and normal playback stays bit-identical. The SD card's wait time is not modeled in the emulator.

## 7f. MIDI and gate timing (v13)

Raw data: `raw/midi-timing.json`. Commits `c89b7b35` and `a8e8af32` (on v13). Measurement: `tests/song`, `MIDI=1 ./run.sh`.
- **Bug 1 (newly visible through 7e):** `scheduleMidiGateOutISR()` computed the wait time modulo 128. If the event lay late in the window rendered ahead, MIDI (clock, notes) or gate went out a whole buffer, i.e. 2.9 ms, too early. When saving, this affected 25 of 97 timer runs. New: `s = 128 + t − due − (movement & 127)`, +128 on underrun, 1 for positions already played, limited to 5605 samples (16-bit TGRA).
- **Bug 2 (old, already in 1.2.1):** `TCNT_2` was never set to 0 before the timer started. After the compare match it keeps counting until the ISR stops it, with interrupts disabled up to 451 counts. The next run therefore came up to 38 samples too early, or after a 16-bit overflow around 127 ms too late. Now `TCNT` is set to 0 before every start.
- **Result:** timers as planned against the moment the DMA reaches the window sample: 192 runs during playback, 97 when saving, all between −1.4 and +0.2 samples.
- **Open, both old:**
  - `Song::renderAudio()` disables the interrupts per output. The timer interrupt therefore runs up to 40 samples (0.9 ms) later.
  - Notes go out about 2.3–2.6 ms before the sound, because `doMIDIClockOutTick()` sends the buffer immediately when a clock falls on the same tick. For external gear with its own latency this is rather favorable. A change would have to be agreed with the user.
- **Measurement model** in `song_emu.py`: timer (MTU2 channel 2) with its counter, MIDI UART with DMA and a 16-byte FIFO at 31,250 baud, interrupts with disabled periods, playback in real time like the task manager.

## 7g. Delay without pitch jump (v14)

Raw data: `raw/delay-v14.json`. Commits `cd7f09ef`, `9ec940a2` (branch `delay-v14`).
- **Cause of the bending:** On a time change the buffer turns at a different rate. What is already in it plays back faster or slower, the pitch shifts by the rate ratio (25% shorter: +386 cents), and the feedback carries it on.
- **Fade (new, default):** The new time runs in a fresh buffer, the input crossfades over in 23 ms, and the old buffer plays out its echoes with the original time and pitch. Pitch ≤ 0.008 cents, artifacts ≤ −85 dB. Tape matches the previous behavior.
- **Also fixed:**
  - click on the first echo after a pause
  - hard-cut end of the echoes
  - aliasing above 2 s (buffer up to 4 s)
  - Below 3% feedback the delay threw its buffer away on every round (1.2.1).
- **Ping-pong** works, but only has an effect with stereo output (headphones or R output). Through the speaker the Deluge is mono.
- CPU: +0.8% at rest, +1.1% per delay during a change.

## 7h. Pad flicker at low brightness (v13)

Raw data: `raw/pad-dim.json`. Commits `29a22d25`, `9b861a5c` (on v13). Test: `tests/pads/run.sh`.
- **Cause:** The PIC dims over time: each scan step is on for the "refresh time" and dark for the "dimmer interval". 1.2.1 keeps the sum at 23 down to 40%. Below that the refresh time stays at 8, and the dark part grows by 1.2 per level. The scan gets slower: 5.2 times at 4%, 6.8 times at 0%. Refresh values below 10 also give wrong colors (upstream Discussion #1869). There is no fix upstream.
- **Solution (Community Features → Flicker-free dimming, default On):** The PIC dims only down to 43.5% (refresh 10, period 23). `PIC::send()` scales the rest in all pad, sidebar and gold knob values. Light per level as in 1.2.1 (±1%). Off sends byte for byte the same as 1.2.1.
- **Trade-off:** button LEDs, the 7-segment display and the PIC's own blink colors (fast cursor, menu shortcuts) only dim down to 43.5%.
- **To check on the device:** the model refresh/(refresh + dimmer), the linear PWM and whether the values 1–3 light steadily.

## 7i. Overclocking

- The Deluge runs at 13.33 MHz × 30 (PLL) = **400 MHz**, the rated frequency of the RZ/A1L (`peripheral_init_basic.c`: `FRQCR = 0x1035`). Bus 133 MHz, peripherals 66.7 / 33.3 MHz.
- Software cannot go higher: the PLL factor is fixed, and `FRQCR` only selects dividers.
- Only a different crystal would speed everything up, outside the specification and with consequences for SDRAM timing, MIDI baud rate, SD, timers and heat. Gain at most about 10%.
- Software brings more: the optimizations (−25% demand from v12 to v13) and the unused L2 cache (128 KB, switched on upstream).

## 7j. L2 cache: test versions (for v14)

Folder `l2test/`, tests `tests/l2`. Commits on v14: `d29b4fd5` and `198e9822` (code only), `d0791052` (data too). A first draft on v13 (`1ac6d827`, `67a80eb8`) was replaced after the review.
- **Code only:** L2 switched on after L1, all ways locked for data (`REG9_D_LOCKDOWN0`).
  - Before every DMA transfer (SD read and write, OLED), L1 and L2 are written back and invalidated for the buffer, and again after reading.
  - The reason (reviewer): RAM is executable (`TTB_PARA_NORMAL_CACHE` without XN). A speculative instruction fetch can thus pull a line of a buffer into L2, and data accesses hit it despite the lockdown.
  - FatFS buffers on their own cache lines.
- **Data too:** plus prefetch for data and code. Data is enabled at the end of startup, with interrupts disabled, after all ways have been written back and invalidated (`CLEAN_INV_WAY`, sync).
  - Interrupts off, because on the L2C-310 an operation by address during an operation by way returns an error.
  - Write back instead of only invalidating, because lines from speculative instruction fetches may hold changed data by then.
- **Reviewer, older bugs** (fixed in v14):
  - `v7_dma_inv_range()` lost a write next to the buffer (Linux fix from 2014).
  - The SysEx file buffers from v7 shared cache lines with the allocator.
- **All DMA paths in v13:**
  - SD card and OLED need the maintenance.
  - Audio (SSI) and UART (MIDI, PIC) run over the uncached mirror address.
  - USB runs without DMA.
  - The sample clusters have Rohan's padding (`dummy[32]` before, 32 bytes after). Their DMA area therefore shares no cache line with fields that are written during loading.
- **Chainloader** (firmware via USB SysEx) is not included in release builds (`ENABLE_SYSEX_LOAD` off). For builds with it: L1 maintenance (upstream `c0586341`), plus write back, invalidate and switch off L2.
- **Emulator:** checked the startup sequence, the maintenance before the OLED DMA (25 of 25 lines, then sync) and the maintenance at odd addresses. The full-load song is bit-identical to v14 in both versions.
- **Open:** the gain. Only measurable on the device (CPU monitor, `MT_LOADTEST`).

## 7k. Reverb: wobble and muddiness (v14)

Raw data: `raw/reverb-v14.json`. Commit `7c1ffede`, tests `tests/reverb/modulation_test.cpp`.
- **Cause:** The modulated delays (Mutable: smear in the first diffuser and two tank delays; Digital: the two tank allpasses) make a held tone fluctuate in the reverb:
  - Mutable by 16.4 dB and 5 cents
  - Digital by 15.5 dB and 19 cents
  - At the 1.2.1 speed (16 times slower; v10 sped it up to the original) it is 8.4 dB and 5.6 cents.
  - Without modulation 0.1 dB and 0.3 cents.
- **Depth against wobble** (held 220 Hz tone, 5–95%):

| Depth | Mutable dB | Mutable cents | Digital dB | Digital cents |
|---|---|---|---|---|
| 0 | 0.1 | 0.3 | 0.1 | 0.3 |
| 0.1 | 0.7 | 0.3 | 1.7 | 0.4 |
| 0.2 | 1.4 | 0.5 | 3.5 | 0.8 |
| 0.4 | 6.1 | 1.1 | 8.0 | 2.7 |
| 1 | 16.4 | 5.0 | 15.5 | 19.4 |

- **Resonances** (peaks in the spectrum of the reverb tail above the median): Mutable 20.5 dB without modulation against 17.3 dB with full modulation, Digital 11.2 against 12.3 dB. So the modulation only smooths a little on the Mutable model, and only by 3 dB.
- **Reverb tail without modulation:** on the Mutable model 5% longer broadband, about 23% longer at 4 kHz (Damping 14). Unchanged on the Digital model.
- **Implementation:** menu Modulation 0–50 (default 0, 50 = v10–v13) and Pre-delay 0–100 ms (17.6 KB RAM).
  - At 50 the full-load song with Mutable is bit-identical to v13. With Digital, 176 of 705,792 samples differ by 1 LSB (rounding of the offsets).

## 7l. Idle load of large songs (for v15)

**Starting point:** On the device the CPU monitor showed 87% for a large project, although nothing was playing.

**Measurement in the emulator** (v15, stopped, never played):
- Song: the 8 synths of the full-load song eight times (64 synths) and the kit four times, drone with 4 tones on.
- Result: 8.6% with windows of 128 samples, 13.5% with windows of 16–24 samples. The latter is the normal case when stopped, because the engine then computes every 11 to 16 samples.
- Per call the 64 silent synths together cost around 2,000 instructions (`SoundInstrument::renderOutput`, about 31 per synth), the 4 kits 600 (`Kit::renderOutput`). So silent tracks are already skipped early.
- The rest comes from the song reverb (Mutable, 27–41%), the drone (14–19%), the master compressor (8–12%) and the fixed work per call (`Song::renderAudio`, `doSomeOutputting`).

**Conclusions:**
- The number of tracks does not explain 87%. Possible causes in the real song:
  - sounds that never go silent (delay with high feedback, LFO on the volume, latch arp)
  - many drone tones
  - audio tracks with input monitoring
  - cache misses, which the emulator does not model
- Next step: load the user's song (`SONGS/<Name>.XML`) in the emulator and break it down by function.
- Possible levers, if the master effects are the cause:
  - skip the reverb and the master compressor in silence (reverb input and tail below the threshold of hearing)
  - compute in larger blocks when stopped, which spreads the fixed work per call over more samples

## 7m. Idling and false culls (v17)

**Causes**, found with the emulator (`tests/sdload`) and the profiler:
- **Scheduling:** The interval of the playback routine is `16 / 44100`, which is 0 as an integer. The audio routine therefore runs about every 12 µs and computes 4–8 samples each time. Every pass goes through all tracks.
- **Silent kits and audio tracks** set up their whole effects chain before they checked for silence: around 550 instructions per track and pass.
- **False culls when streaming:** When the Deluge waits for the card, it computes the audio in the loading task. `setDireness` then judged the load by the mean duration of this task, card time included.
  - With a slow card there were 17–25 culls, although the DMA was at most 9 samples behind.

**Fix in v17** (patches 0056–0060):
- The interval is now 0.36 ms, as intended.
- Direness and culling follow the measured time of the audio routine itself.
- **Minimum window:** Under light load (below 50%, direness 0, no file access) it only computes once 32 samples are due.
- Silent tracks check for silence first. It is the same condition as before, only before the setup, and it stays bit-identical.
- The CPU monitor does not count passes without computing as busy.

**A/B in the emulator** (`tests/sdload`). The device estimate assumes 1 instruction per clock cycle at 400 MHz plus 60 ns per SDRAM line:

| Case | Calls/s | Samples/render | Monitor | Device (estimate) | Culls/QL | Reserve |
|---|---|---|---|---|---|---|
| Idle v16 | 7132 | 6.2 | 90.2% | 182% | 0/0 | 109 |
| Idle without minimum window | 3569 | 12.4 | 29.8% | 64% | 0/0 | 106 |
| Idle v17 | 2943 (735 computing) | 60 | 11.7% | 19% | 0/0 | 79 |
| Streaming v16 | 5308 | 8.4 | 92.7% | 98% | 37/4 | 83 |
| Streaming without minimum window | 3996 | 11.1 | 94.5% | 98.5% | 0/0 | 75 |
| Streaming v17 | 2592 (733 computing) | 60 | 38.6% | 40% | 0/0 | 42 |

- **Decision:** The minimum window stays.
  - Without it a typical streaming project would stay at around 95% load.
  - **Its price:** Live notes vary by up to 1.4 ms (the same on average), and the reserve is smaller. There was no underrun.
- **Silent tracks:** Per silent track the work drops from 550 to 210 instructions. In the large song that is −42% instructions and −26% SDRAM lines per pass.
- **After the reviewer:**
  - **Reserve:** The minimum window starts earlier by the expected render time. The biggest gap thus drops from 81 to 65 samples, at the same load.
  - **External clock:** A clock byte that is read late ticks immediately instead of 128 samples too late.
  - **`bypassCulling`:** now only applies to its own render.
- **Threshold 65% instead of 50%** (helper session, "New Sitar Grii 10", `device/analysis/2026-09-27-sitar-v17.md`):
  - At 0.8 instructions per clock cycle the window load was just above 50%. v17 then fell back to blocks of 12 and ended up at 87% instead of 52%.
  - With 65% the minimum window stays on. The reserve drops by 2 samples (71 instead of 73).

## 7n. First measurement on the device (v16, "New Sitar Grii 10")

The local session made the measurement with the profiler (`device/2026-09-27-v16-*` on the branch `device-results`). The song has 13 tracks: 7 kits with 1–36 drums, 3 synths, an audio track with monitoring and 2 MIDI tracks, plus reverb Mutable.

- **Stopped:**
  - 86% on the display. The audio routine runs 87% of the time.
  - The silent kits and the audio track need 53% together, the synths 0.4% each.
  - This confirms 7m on the device.
- **Playing:**
  - Only 16–18 voices sound, yet the load is 96%. QL is constantly at 12–14.
  - In 12 s, 173 voices were cut. The longest gap was 4.7 ms, and the buffer lasts 2.9 ms.
  - The kits carry the load: 3L3Ctr0 15% (36 drums, phaser), Hihat 11% (flanger), Guiro 11% (delay), CR-78 10% (flanger), KIT1 8.5%. The reverb needs 6%, the three synths together 10%.
  - Mainly the held synth voices are cut, which is why Oboe and Sitar can hardly be heard.
- **Conclusions** (corrected after the analysis of the song in the emulator, `device/analysis/2026-09-27-sitar-*.md`):
  - **432 against 440 Hz:** hardly any difference, 37.2 against 37.0% CPU. This song's samples play transposed anyway, so they are resampled at 440 Hz too. The first guess, that 432 Hz makes the kits expensive, does not hold for this song.
  - **The small processing blocks of v16 are the driver:**
    - With blocks of 8 samples the same song costs 103% instead of 37% at 128 samples. The kits grow the most, Hihat from 3.5 to 15.7%.
    - With the real scheduling: v16 93% on the monitor (device estimated 117%), v17 41% (47%). The five kits drop from 38 to 14% of the time.
  - **Open:**
    - When stopped, Guiro costs 14.7%, Rattle with a similar delay only 2.8%.
    - v17 does not skip the mod FX and delay tails of silent tracks.
- **Next steps:**
  - break the song down by function in the emulator, because on the device the profiler cannot see into the tracks
  - skip the mod FX and delay tails of silent tracks once they have decayed
  - make the kits' effects chains cheaper while playing
  - measure the L2 versions with the same song

## 8. Open

- [x] Full-load test run 1 entered, prioritization adjusted.
- [x] Correction run entered (run 2).
- [ ] Measurement version on the device with `MT_LOADTEST`: calibrate the emulator, check the direness and culling thresholds.
- [ ] Benchmarks for `processReverbSendAndVolume`, the overhead per window in `Sound::render` and the wavetable loop.
- [ ] Measure the wavetable oscillator, Grain, `hopEnd` and the stereo unison pan loop, if the full-load test shows them to be relevant.
- [x] The song "New Sitar Grii 10" in the emulator: where does the time in the kits go (7n)? Into the small processing blocks of v16, see 7n.
- [ ] Skip silent tracks with a mod FX or delay tail once the tail has decayed.
- [ ] Check the threshold of the minimum window (65% load since v17) on the device.
- [ ] MIDI/clock fix in `routineForSD()` (`9cd09fb7`): confirm in the code that it transfers.
- [x] MIDI/gate timer: 2.9 ms too early and leftover timer count fixed (7f).
- [x] Notes 2.5 ms before the sound (7f): stays as it is (the user's decision).
- [x] L2 cache: follow-up fixes collected and the DMA paths of v13 checked (section 5).
- [x] **Measurement version** built (`diag/`, SHA `8c94cf68…`). The test song for the SD card is `diag/loadtest-card.zip`.
  - CPU load per block (mean/peak), voices, `cpuDireness`, culling, SD latency per cluster.
  - Display on the OLED, via SysEx over USB only, plus a Web MIDI page `tools/cpu_monitor.html` with CSV export.
  - Goals:
    - calibrate the emulator against the hardware, with the same test song as in the full-load test
    - a real baseline for the L2 cache and optimizations
