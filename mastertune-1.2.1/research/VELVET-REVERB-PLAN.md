# A velvet-noise reverb: a plan (01.10.2026)

A fourth reverb model, **Velvet**, next to Freeverb, Mutable and Digital: a dense, smooth room or hall with a natural, frequency-dependent decay and wide stereo, at no more CPU than Mutable.

## Velvet noise in three lines

- Velvet noise is a sparse sequence of +1, −1 and 0: one pulse of random sign at a random position in each period of a few samples, e.g. 2,000 pulses per second (one in 22 samples at 44.1 kHz). At such densities it is heard as smooth noise, not as clicks.
- Filtering with it needs no multiplications, only an addition or subtraction per pulse: a reverb tail made of velvet noise is cheap per pulse.
- It is used in two ways: as the tail itself, by convolution with filtered velvet noise (Välimäki et al. 2017; Välimäki & Prawda 2021), and inside a feedback delay network, as a "velvet" feedback matrix (Schlecht & Habets 2020).

## What exists already

| Model | Since | Character | Instructions per block of 128 | CPU |
|---|---|---|---|---|
| Freeverb | 1.2.1 | Schroeder/Moorer combs and allpasses, metallic | 70,200 | 6.0 % |
| Mutable | 1.2.1, v10, v14 | Mutable Instruments' loop of allpasses, ambient | 39,100 | 3.4 % |
| Digital | v10, v14 | Dattorro's plate, dense and smooth | 53,600 | 4.6 % |

Figures from `research/OPTIMIZATION.md` (1 % is about 11,600 instructions per block, or about 90 per sample).

- **Interface:** `dsp/reverb/base.hpp`: mono in, stereo out, once per song. Room size, Damping, Width, HPF, LPF and Modulation come through `Base`; `Reverb::process()` does the pre-delay for every model.
- **Memory:** the models live in one `std::variant` (`dsp/reverb/reverb.hpp`); only the active one exists. Mutable and Digital share a buffer of 32,768 floats (128 KB). A model that stays within it costs no extra RAM.
- **Song file:** `<reverb model="…">`, a number: 0 Freeverb, 1 Mutable, 2 Digital. mastertune reads a number above Digital as Mutable (since v10; `src/deluge/model/song/song.cpp:1367` in v19.0). Stock 1.2.1 plays a song saved with Digital without reverb (manual, 5.10).
- **Measures from v14** (section 7k of `research/OPTIMIZATION.md`): resonances in the tail's spectrum above the median are 20.5 dB for Mutable and 11.2 dB for Digital, both without modulation. A held tone wobbles in level and pitch when the delays are modulated.
- **Tests:** `tests/reverb` (host and ARM, 47 checks).
- **Community:** the draft SynthstromAudible/DelugeFirmware#4939 (a velvet-noise reverb) has no code yet. There is nothing to port; mastertune's model would be its own.

## Options

| | Design | Cost (estimate) | Long tails, Room 50 | Verdict |
|---|---|---|---|---|
| **A** | Convolution with filtered or interleaved velvet noise (Välimäki et al. 2017; Välimäki & Prawda 2021) | One addition per pulse and sample: 2,000 pulses/s × 4.5 s ≈ 9,000 pulses. Even vectorised (NEON, the transposed form of Belloch et al. 2024) about 6,700 instructions per sample: 75 % CPU per channel | A fixed impulse response has no "almost endless" | out for the tail |
| **B** | Feedback delay network (FDN) with a velvet feedback matrix (VFM, Schlecht & Habets 2020): 8 or 16 delay lines, the mixing matrix a cascade of Hadamard stages with short delays, so that each entry is a sparse ±1 filter | 8 lines: about 100–130 instructions per sample; each VFM stage adds about 40. With 2–3 stages that is **about 2–3 %** | Recursive: any reverb time, cost independent of it | **recommended** |
| **C** | B plus an early part of velvet noise: 64–200 pulses per channel over the first 50–80 ms, NEON in the transposed form | B + about 1–3.5 % | as B | only if B's onset sounds grainy |

**Why B:**
1. It is recursive, so every Room size down to "almost endless" costs the same.
2. The VFM makes the echo density grow very fast ("ultra-dense impulse responses at a minimal computational cost", Schlecht & Habets 2020). Digital gets its density from four input allpasses and two in the tank, at a higher cost.
3. Its outputs are less correlated than those of a plain small FDN (Schlecht, Fagerström & Välimäki 2023), which gives Width its range.
4. It fits in the existing 32,768-sample buffer and the `Base` interface. The other models stay untouched.

## Design (B)

- **Delays:** N = 8 (or 16) lines, mutually prime lengths, together with the VFM's short delays at most 32,768 samples. Ideally every delay is at least 128 samples: then a whole block can be processed stage by stage (the matrix vectorised across time, the filters across lines). The prototype shows whether the VFM sounds right with such delays.
- **Room size → T60** at mid frequencies, on Digital's scale: 0 ≈ 0.6 s, 30 ≈ 4.5 s, 45 ≈ 18 s, 50 almost endless. So switching between models keeps the time. Each line's gain is 10^(−3·m/(T60·fs)) for its length m.
- **Damping → the ratio of the high-frequency T60 to the mid T60**, through an absorption filter per line (a first-order shelf), from 1 at 0 (bright) to about 1/8 at 50 (dark).
- **Width:** 0 mono, 50 two decorrelated outputs, as on Digital.
- **HPF, LPF:** at the input, with the same coefficients as Mutable and Digital.
- **Modulation:** none at first, so a held tone stands still; v14 showed what modulation costs.
- **Level:** as loud as Mutable within 0.5 dB at the default settings, like Digital.
- **Arithmetic:** float like Mutable, saturating at the output. Gains below 1 even at Room 50.

## Steps

1. **Prototype on the PC** (Python and numpy in `research/velvet/`, scripts only). B with N, the number of VFM stages and the delay lengths as parameters; C as an option. The impulse responses of Mutable and Digital come from the firmware's reverb code built for the PC (as in `tests/reverb`), so Velvet is compared with the real models. Measures:
   - T60 per octave band from the energy decay curve, against the Room size and Damping targets
   - echo density over time
   - resonances above the median (v14's measure): at most Digital's 11.2 dB
   - correlation of left and right against Width, and the level against Mutable
   - operations per sample and memory

   Demo WAVs of the same dry material (drums, a held chord, a voice) through Mutable, Digital and Velvet go to the user in the chat; they are not committed.

   **Go or no-go:** Velvet has to sound better than Digital in a way the user hears (more natural, smoother long tails, wider) at no more than Digital's cost. Otherwise Digital stays the best model and the plan stops here.
2. **Firmware:** `src/deluge/dsp/reverb/velvet.hpp`; `Model::VELVET` (3) in `reverb.hpp`; the range check in `song.cpp` raised to it; the menu (`gui/menu_item/reverb/model.h`, "Velvet", 7-segment `VELV`, Room size named TIME as for Mutable and Digital). Then NEON and the block processing.
   - Tests in `tests/reverb` (host and ARM): decay times, level, 60 s at Room 50 and Damping 0 without growing, Width, silence after a long tail (no NaN, no slowdown from denormals), saving and loading.
   - The 47 existing checks stay unchanged, the other models bit-identical.
   - Cost per block in the emulator on the full-load song: target at most Mutable's 39,100 instructions, limit Digital's 53,600.
3. **Release:** binary and symbols, README, manual (5.10), and a DEVICE.md task for the local session: listening A/B against Mutable and Digital at Room 0, 30, 45 and 50 and with a held tone, and the CPU load on the device with the full-load song.

Effort: step 1 one session, step 2 one or two sessions, step 3 with the next release.

## Risks

- **Quality:** Digital is already dense and smooth. Without an audible gain a fourth model only adds to the menu, hence the go or no-go after step 1.
- **CPU:** the figures above are estimates. The VFM's extra delay reads cost cache misses; step 1 counts operations, step 2 measures instructions.
- **Colouration:** small FDNs ring metallically; the VFM and N = 16 are the remedies. The resonance measure decides.
- **Older firmware:** mastertune v10 to v19 plays a Velvet song with Mutable, stock 1.2.1 without reverb (as with Digital). This goes in the README.
- **Licence:** an own implementation from the papers (GPL-3.0 firmware); no third-party code.

## Decisions for the user

1. **Character:** a natural room or hall (recommended: the gap next to Mutable's ambience and Digital's plate), or a long, lush ambient space.
2. **Add or replace:** recommended as a fourth model. Replacing a model would change the sound of existing songs.
3. **CPU budget:** recommended target at most Mutable's 3.4 %, limit Digital's 4.6 %.
4. **Priority against DJ v19.1** (tracks as decks, the library tool): recommended to do step 1 alongside it (PC only, no firmware) and step 2 after the go.

## Sources

- Välimäki, V. et al. (2017). Late reverberation synthesis using filtered velvet noise. *Applied Sciences*, 7(5), 483. https://doi.org/10.3390/app7050483
- Välimäki, V., & Prawda, K. (2021). Late-reverberation synthesis using interleaved velvet-noise sequences. *IEEE/ACM TASLP*, 29, 1149–1160. https://doi.org/10.1109/taslp.2021.3060165
- Schlecht, S. J., & Habets, E. A. P. (2020). Scattering in feedback delay networks. *IEEE/ACM TASLP*, 28, 1915–1924. https://doi.org/10.1109/taslp.2020.3001395
- Schlecht, S. J., Fagerström, J., & Välimäki, V. (2023). Decorrelation in feedback delay networks. *IEEE/ACM TASLP*, 31, 3478–3487. https://doi.org/10.1109/taslp.2023.3313440
- Belloch, J. A. et al. (2024). Efficient velvet-noise convolution in multicore processors. *JAES*, 72(6), 383–393. https://doi.org/10.17743/jaes.2022.0156
