# Re-measuring the two excerpts (Claude, 2026-09-26)

Two independent analyses (rhythm, pitch/sound) plus a cross-check of the core statement of my own. Everything is measured from the finished mix, so it comes with a confidence note.

## Confirmed (two methods agree)

- **Tempo 138 BPM** (beat 434.8 ms). The README says 137.8; that is only the grid step of the tempo estimate.
- **There is an accelerating figure.** Three or four equally loud hits whose gaps each shrink to about 0.65 to 0.75 times the previous one, and which fill exactly one eighth, up to the next beat:

  | Place in the song | Gaps | Sum |
  |---|---|---|
  | 0:57.2 | 99 → 64 → 46 ms | 209 ms |
  | 6:39.2 | 102 → 75 → 46 ms | 223 ms |
  | 6:31.1 (rhythm analysis only) | 99 → 65 → 54 ms | 218 ms |
  | 6:40.2 (rhythm analysis only) | 144 → 94 → 64 ms | 302 ms |

  An eighth lasts 217 ms at 138 BPM. The rhythm analysis and the HPSS cross-check found the gap times of the first two places independently of each other, to within a few milliseconds.
- **No continuous 1/32 arpeggio.** None of the methods finds a 54 ms periodicity. The arp plays in phrases, not continuously. The README's statements "1/32, 25% gate, random order" can't be confirmed.
- **Sound:** sawtooth, filter open (overtones up to above 3.5 kHz, falling about 5–6 dB/octave, hardly any resonance), slightly detuned (10–15 cents), rather narrow in stereo. Wider at 6:36.
- **Pumping:** The arp itself isn't pumped. Bass and pad duck after the kick; the bass has its peak on the offbeat.

## Found by one method only (uncertain)

- A **slowing** figure right after the kick at 0:56.1 and 1:03.1: 48 → 55 → 67 → 81 → 99 ms, about ×1.2 per hit.
- **Harmony at 6:36:** movement in D harmonic minor (A, B♭, C#, D, E, F, G). At 0:57 there is A major with B and G.
- **Gate rather 50% or more** instead of 25%. The pad covers a lot here.
- Whether the hits of the figure are single arp notes or whole chord stabs can't be separated safely from the mix.

## Rebuilding it on the Deluge with firmware v6 (exact)

- **Tempo:** 138.
- **Arp:** sync 1/8, ratchet notes 3, ratchet bounce +6, bounce fade off.
- **Onsets:** 0, 99 and 169 ms after the start of the eighth, i.e. gaps of 99, 70 and 49 ms. Measured were 99, 64 and 46 ms at 0:57 and 102, 75 and 46 ms at 6:39.
- **Ratchet probability:** In the automation view set it only on the eighth before the beat where the figure should come, 0 elsewhere.

## Rebuilding it on the Deluge with firmware v5 (approximate)

- **Tempo:** 138.
- **Arp:** sync 1/8, ratchet bounce +6 to +7. That gives a ratio of 0.70 or 0.65; measured is about 0.67.
- **Ratchet probability:** In the automation view set it only on the eighths before the beats where the figure should come, 0 elsewhere. In the piece the figure only comes now and then, not on every step.
- **Equally loud hits:** The bounce makes the late hits quieter; in the piece they're equally loud. Remedy: set the synth's patch cable velocity → level to 0.
- **Limit of the 1.3 ratchets:** They choose the count at random from 2, 4 or 8. In the piece there are mostly 3 hits per eighth. With 4 an extra short hit comes about 25 ms before the beat.
- **Exact without the arp:** Put the figure into the clip as notes (zoom in deep). At 0:57 the onsets lie 0, 99 and 163 ms after the start of the eighth.

# Checked again (Claude, 2026-09-28), for the preset PETTRA ARP

Both excerpts, kick grid from the kick's attack (±10 ms), constant-Q spectrograms and onset envelopes per band.

- **No continuous arp stream, confirmed.** The autocorrelation of the onsets has its peaks at the beat (r ≈ 0.7, the kick) and the eighth (r up to 0.24, offbeat hat and bass). At 1/32 (54 ms) r ≤ 0.02, at 1/16 (109 ms) r ≤ 0.06, at 3/16 (327 ms) r ≤ 0.04. A continuous arp would stand out clearly in 1.2–5 kHz.
- **Two kinds of hits:** tonal plucks (a harmonic stack, bright at the attack) at changing places, and thin clicks without pitch (percussion; dense around 6:39–6:41, a roll into the beat). Only the plucks belong to the arp.
- **The figure, confirmed:** 0:57.18 (the offbeat), 0:57.29, 0:57.35, then the beat at 0:57.40: gaps 102, 67, 52 ms, i.e. ×0.66 and ×0.78.
- **A slowing figure after the kick** at 0:56.52: hits +49, +119, +229 ms (gaps 49, 70, 110 ms). Seen once; fits the uncertain one above.
- **Brightness after a pluck** (0:56.35–0:56.64, median of four): 1.5–4 kHz −3, −5, −7 dB and 4–9 kHz −8, −16, −19 dB at 20, 40, 60 ms. The top decays fast, then the pad and reverb hold a floor around −20 dB.
- **Not separable from the mix:** single notes or chords per pluck, the note order between the figures.

The preset `mastertune-1.2.1/presets/SYNTHS/PETTRA ARP.XML` is built from this (see `mastertune-1.2.1/presets/README.md`).
