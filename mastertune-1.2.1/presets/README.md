# Presets: ping-pong arp

Two synth presets for the ratchet bounce, from **v13** on. The firmware saved both itself (in the emulator, with the same code as "Save" on the device), so the format is exactly right.

| File | What it does | Audio example |
|---|---|---|
| `SYNTHS/PINGPONG ROLL.XML` | **New:** a ping-pong ball between two paddles that close in. Per ratchet 16 hits over 4 eighths, ever faster and louder, up to the next hit. | `demo/PINGPONG ROLL.wav` |
| `SYNTHS/PETTRA PINGPONG.XML` | The figure from Pettra "You Are The Seeds" (0:57, 6:39): 3 equally loud hits in one eighth, each gap ×0.7, up to the next hit. | `demo/PETTRA PINGPONG.wav` |

The audio examples are rendered with the real firmware in the emulator, on a held A major chord at 120 BPM.

## Onto the card

Copy the two `.XML` files into the `SYNTHS` folder of the SD card. On the Deluge, open a synth clip and load the preset like any other. The arp settings come with it.

## Settings

Everything under **Menu → Arpeggiator**:

| Setting | PINGPONG ROLL | PETTRA PINGPONG | What it does |
|---|---|---|---|
| Sync | 1/8 | 1/8 | Length of an arp step |
| Ratchet notes | **Roll** | 3 | Roll = as many hits as it takes until the paddles close |
| Ratchet bounce | +4 | +6 | Acceleration: every gap is r × the previous one, r = 0.95 at +1 to 0.5 at +10. Negative: slowing down. 0: even. |
| **Bounce length** (new) | 4 | 1 | Over how many arp steps a ratchet lasts |
| **Bounce velocity** (new, formerly "Bounce fade") | Rise | Even | Even = equally loud, Fade = quieter (ball), Rise = louder (ping-pong) |
| Ratchet probability | 100% | 100% | How often a step ratchets |

The maths behind Roll:
- A span S has L arp steps. Hit j lies at **S·(1 − rʲ)**. The series converges exactly on the end of the span; there the paddles meet on the next hit of the grid.
- As soon as a gap falls below 16 ms, the hits continue at a 16 ms pace ("trrrr").
- Example PINGPONG ROLL at 120 BPM: gaps of 200, 160, 128, 102, 82 … 21, 17 ms, then 16 ms.

## Adjusting

- **Only a fill now and then instead of on every step:**
  - Lower the ratchet probability.
  - Or, in the automation view, set it only on the steps before which the fill should come, 0 elsewhere. In Pettra the figure only comes now and then.
- **Longer or shorter:** change the bounce length or the sync. At 1/16 and bounce length 8 the fill also lasts half a note.
- **Accelerate more softly or harder:** ratchet bounce +1 … +10.
- **The hit after the fill should bang:** ratchet probability below 100%. Then the landing step is a normal note with full velocity. With 100% the next fill starts right there, and Rise starts it at half velocity.
- **Sound:**
  - two slightly detuned saws (+12 cents), a 24 dB low-pass rather open
  - a short envelope without sustain, velocity → volume 70%
  - For Pettra as at 6:36: the filter lower (darker) and a little louder.
- **Tempo:** The presets store no tempo. Pettra runs at 138 BPM.
