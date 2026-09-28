# Synth presets: the Pettra arp and the ping-pong roll

Three synth presets for the ratchet bounce of the arpeggiator, from **v13** on. The firmware saved them itself (in the emulator, with the same code as "Save" on the device), so the format is exactly right.

| File | What it does | Audio example |
|---|---|---|
| `SYNTHS/PETTRA ARP.XML` | **The arp of Pettra "You Are The Seeds"**, sound and arp: a bright, detuned saw pluck on 1/8, random notes over 2 octaves, and now and then (about once a bar) the song's figure: 3 equally loud hits into the next step, each gap ×0.7. | `demo/PETTRA ARP.wav` (138 BPM, A major held, 8 bars) |
| `SYNTHS/PETTRA PINGPONG.XML` | Only the figure, on every step: 3 equally loud hits in one eighth, each gap ×0.7. | `demo/PETTRA PINGPONG.wav` |
| `SYNTHS/PETTRA BALL.XML` | **The Pettra arp as the arp mode Ball** (needs firmware v18.4): a ping-pong ball between two plates that close in and part again, over 2 beats. The hits speed up (175, 114, 74, 48, 31, 20 ms) into a buzz at 15 ms on beats 2 and 4, loudest there, then slow down again. A5 held: the ball repeats one note, as the song's plucks mostly do. | `demo/PETTRA BALL.wav` (138 BPM, 8 bars) |
| `SYNTHS/PETTRA BALL BUZZ.XML` | The same over one beat with bounce +3: from 37 ms into the buzz and back, nearly all buzz, as at 6:39.57 in the song. | (none) |
| (no preset) | **The ping-pong ball between two plates** that close in slowly and part fast: computed (`tests/arp/pingpong.py`) and played as notes in a clip with the PETTRA ARP voice. Per 2 beats 5-6 hits: one long flight after the plates meet, then ever faster (x0.69 per hit) into the next meeting on beats 2 and 4, the last hits about 14 dB louder and brighter. The arp can't do this yet: its bounce either accelerates or slows down. | `demo/PETTRA PINGPONG BALL.wav` (138 BPM, 8 bars); `demo/PETTRA PINGPONG BALL B.wav`: x0.67 per hit and mostly one note, A5, as measured later (`references/pettra-arp/METHODS.md`) |
| `SYNTHS/PINGPONG ROLL.XML` | A ping-pong ball between two paddles that close in. Per ratchet 16 hits over 4 eighths, ever faster and louder, up to the next hit. | `demo/PINGPONG ROLL.wav` |

The audio examples are rendered with the real firmware in the emulator: PETTRA ARP at 138 BPM on A4 C#5 E5 as at 0:55 in the song, the other two at 120 BPM on A major.

## Onto the Deluge

- **With the card:** copy the `.XML` files into the `SYNTHS` folder of the SD card.
- **Over USB, without taking the card out** (v7 or later): open [dex.silicak.es](https://dex.silicak.es) in Chrome or Edge, connect, open `SYNTHS` and upload the files.
- **Download one file:** [PETTRA ARP.XML](https://github.com/Giansn/deluge/raw/claude/wizardly-brahmagupta-nnrk07/mastertune-1.2.1/presets/SYNTHS/PETTRA%20ARP.XML).

Then on the Deluge: open a synth clip and load the preset like any other; the arp settings come with it. Set the tempo to **138** (the preset stores no tempo) and hold **A, C#, E** (for example A4 C#5 E5). With **Arpeggiator → Latch** on, the arp keeps playing when you let go.

## The arp mode Ball (firmware v18.4)

**Arpeggiator → Ratchet notes → Ball** (after Roll). A ratchet becomes a ping-pong ball between two plates:
- The first half of the ratchet is a roll into its middle: each gap is the one before times r (r from the ratchet bounce, 0.95 at +1 to 0.5 at +10), until the ball buzzes at 15 ms.
- The second half is the same backwards: the plates part, the gaps grow again.
- **Bounce length** sets how many arp steps the ratchet lasts: at 1/8, 2 = one beat, 4 = two beats.
- **Negative bounce:** the plates meet at both ends of the ratchet (on the step) and are furthest apart in its middle.
- **Bounce velocity Rise:** the buzz is the loudest, the slow hits half as loud.
- Older firmware reads a Ball as 8 notes.

PETTRA BALL: like PETTRA ARP (below), but ratchet notes **Ball**, bounce **+7**, bounce length **4**, bounce velocity **Rise**, ratchet probability 100 %, note mode Up, 1 octave, gate 20, envelope 1 decay 14, sustain 0, release 4 (a snappier pluck, so the buzz's hits stay single hits). Hold one note, for example A5.

## PETTRA ARP: all settings

To set it by hand or to compare. Values as the Deluge shows them (0–50 unless marked).

| Where | Setting | Value |
|---|---|---|
| Oscillator 1 | Type, volume | Saw, 50 |
| Oscillator 2 | Type, volume, cents | Saw, 44, **+12** |
| Unison | Number | 1 (none: the voice stays narrow) |
| LPF | Mode, frequency, resonance | 24 dB, **26**, 3 |
| Envelope 1 (volume) | Attack, decay, sustain, release | 0, 20, 6, 8 |
| Envelope 2 | Attack, decay, sustain, release | 0, **9**, 0, 6 |
| Patch cables | Envelope 2 → LPF frequency | **+22** (the bright attack) |
| | Note → LPF frequency | +5 |
| | Velocity → volume | +10 (the hits stay equally loud) |
| Reverb | Amount | 10 |
| Arpeggiator | Mode | Arp |
| | Note mode, octave mode, octaves | **Random**, Up, 2 |
| | Sync, gate | **1/8**, 28 |
| | Ratchet notes, ratchet bounce | **3**, **+6** (each gap ×0.7) |
| | Bounce length, bounce velocity | 1, Even |
| | Ratchet probability | **14 %** |
| | Note probability | 100 % |

## What it is based on

From the two excerpts in `references/pettra-arp` (`ANALYSIS.md`, checked again on 28.09.2026):

- **The figure** is measured exactly: at 0:57.18 three hits from the offbeat into the next beat, gaps 102, 67, 52 ms. The preset plays 102, 70, 48 ms at 138 BPM.
- **No continuous arp stream:** the song's arp has no fixed 1/32 or 1/16 pulse, and the figure comes now and then. Hence the 14 % ratchet probability instead of 100 %.
- **The sound:** saw, 10–15 cents detuned, narrow, filter open, hardly any resonance, a bright attack. The filter envelope is fitted to the song. After a hit, 1.5–4 kHz falls by 3, 5, 7 dB at 20, 40, 60 ms and 4–9 kHz by 8, 16, 19 dB; the preset falls by 2, 3, 7 and 6, 15, 25 dB. Below −20 dB the song's pad and reverb fill in.
- **Not certain from the finished mix:**
  - whether the song plays single notes or whole chords per hit;
  - the note order between the figures;
  - a slowing figure right after the kick (0:56.52: 49, 70, 110 ms).

## Adjusting

- **The figure only where the song has it** (into the next beat): set the ratchet probability to 0 and, in the automation view, set it to 100 % only on the offbeat steps before the beats where the figure should come. A preset can't limit it to offbeats, so at 14 % some figures also start on the beat.
- **As at 6:36 in the song:** darker and louder. Turn the LPF frequency down by 4–6 and raise the volume a little. The harmony there moves in D harmonic minor (hold for example A, C#, D, E).
- **Whole chords per hit instead of single notes:** in the menu Randomizer (right after Arpeggiator), Chord Polyphony 3 and Chord Probability 100 %.
- **The slowing figure after the kick:** ratchet bounce −6. It applies to the whole preset, so use a second synth clip for it.
- **Only the figure, on every step:** `PETTRA PINGPONG.XML`, or the ratchet probability at 100 %.

## PINGPONG ROLL and PETTRA PINGPONG: settings

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

Adjusting:
- **Only a fill now and then instead of on every step:** lower the ratchet probability, or set it in the automation view only on the steps before which the fill should come.
- **Longer or shorter:** change the bounce length or the sync. At 1/16 and bounce length 8 the fill also lasts half a note.
- **Accelerate more softly or harder:** ratchet bounce +1 … +10.
- **The hit after the fill should bang:** ratchet probability below 100%. Then the landing step is a normal note with full velocity. With 100% the next fill starts right there, and Rise starts it at half velocity.
- **Sound:** two slightly detuned saws (+12 cents), a 24 dB low-pass rather open, a short envelope without sustain, velocity → volume 70%.

## Rebuilding them

`tests/arp/gen_presets.py` (PINGPONG ROLL, PETTRA PINGPONG) and `tests/arp/pettra.py` (PETTRA ARP, with its demo) save the presets with the firmware in the emulator; `tests/arp/run.sh` runs them all.
