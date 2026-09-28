# "Rescue": the song's LPF has no effect (urgent task, 27.09.2026)

## Files

- `card/SONGS/Rescue.XML`: unchanged from the card SD DELUGE, the same as in the backup of 26.09. Saved with `c1.2.0`. One track: `Xylophon`.
- `card/samples-rescue.csv`: the 8 samples with track, size, format and length. All present. The song isn't silent without them, so no samples.

## The user's notes

1. **Which LPF:** the LPF of the whole song view (song master), not that of a track.
2. **What "has no effect" means:** not described more closely (sound unchanged, value fixed, or it jumps back?).
3. **Versions:** "all versions". There are Rescue and Rescue 2–6, all with the same finding below. How it was with v16-l2d is not noted separately.

## Finding in the XML

- **The song LPF's frequency is automated:** `songParams/lpf frequency="0x7FFFFFFF7FFFFFFF80000000"` (24 hex digits instead of 8). Probably a single node at the start with the value fully open (`0x7FFFFFFF`), which opens the LPF again and again during playback. Please check in the emulator.
- **Resonance at maximum:** `resonance="0x7FFFFFFF"`. `lpfMode` 24dB, `filterRoute` H2L, `affectEntire` 1, `currentFilterType` lpf.
- **In the whole library** (`deluge topics`, 578 songs with a song LPF) the song LPF is automated in 34 songs. Exactly this value is in 10 songs: Rescue, Rescue 2–6 (saved with `c1.2.1`) and **Didge Base V2 3–6**. Rescue grew out of Didge Base V2.
  - Didge Base V2 up to V2 4 had been saved by a 1.3 build (notes only in the 1.3 format, repaired for 1.2 on 26.09.).
  - So possibly: a value from 1.3 that 1.2.1 reads as automation.
  - Other forms occur, e.g. `Didge Base V2 2`: `0x7FFFFFFF7FFFFFFFFFFFFFFF7E000000`, `Didge Base`: `0x7FFFFFFF7E00000000004AE47C000000`, `Pseuyy 28Mixed 14–17`: `0x7FFFFFFF720000000000BCB470000000`.
- Rescue 2–6 are in `deluge topics` (the same content as on the card DELUGEBACKU, only the sample paths adjusted). I'll push them too if wanted.
