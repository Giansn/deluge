# The DJ tool: a plan (29.09.2026)

What "DJ" can mean on a Deluge, what it already has, and the steps for v19. The user decided on 29.09.2026: first with an external player (v19.0), then the Deluge as the player (v19.1); the DJ filter on the song's gold knobs in Song view; crossfader and nudge on the Deluge's own controls.

## What the Deluge can and can't do

- **One stereo output.** The headphones carry the same mix as the main output: there is no cue (pre-listening) on the Deluge itself. Cueing would need a second output (a USB audio return is not there; v8 sends only Deluge → computer).
- **One stereo input** (line in, mic in, the internal microphone). An audio track can pass it into the mix with its effects (input monitoring, `AudioOutput::echoing`): an external player can be a "deck" through the Deluge.
- **Audio clips** play long WAV files from the card, time-stretched to the song's tempo ("pitch/speed independent"). Loaded into an audio clip, a sample gets the power of two of beats nearest its length (1, 2, 4, 8 … bars); a whole track of, say, 97 bars gets a wrong length and plays out of time. The firmware reads no tempo from a file (only `smpl`/`inst`: root note, loop points).
- **CPU:** heavy songs use over 90 %. Anything that runs all the time has to be cheap. The Scan analysis (pitch, tempo, key) costs about 2 % while its view is open (v18.4).

## What exists already

| Building block | Where | Use for DJing |
|---|---|---|
| Song-level LPF and HPF | `GlobalEffectable`, gold knobs in Song view, Performance view | the DJ filter's two halves |
| Filters without rustle, glides, crossing guard | v18 | a clean sweep from low-pass through to high-pass |
| Shelving EQ bass/treble | v18 | two of three bands of an isolator |
| Stutter, delay, reverb | 1.2.1, v10, v11 | loop roll, echo out |
| Scan view: pitch, BPM and key of the input (Camelot) | v18.4 | tempo and key of an external player; tuning |
| Music analysis on the computer | `device/analysis/2026-09-28-music-analysis.md` | BPM, beats, key, loudness of a library |
| DelugeTuner, retune_library | tools | the pattern for a library tool |

## Building blocks for v19

**A. DJ filter.** One control from low-pass (left) through off (middle) to high-pass (right), on the song's master. Resonance moderate and fixed (or a second knob), the crossing guard on. Cost: next to nothing (the filters run anyway). **Decided:** on the song's gold knobs in Song view, as a mode of the filter section.

**B. Sync to the input.** In the Scan view (or the DJ view), one press sets the song's tempo to the BPM heard, with the ×2/÷2 already there. Then a nudge (hold a button: the Deluge's clock a little faster or slower for a moment) to bring the beats together by ear, as with a jog wheel. Automatic phase alignment (the beat's position from the onsets) is possible but a step further.

**C. Tracks as decks.** A computer tool analyses tracks and writes their BPM into the file (the `acid` chunk that DAWs write: tempo and number of beats) and trims them to the first downbeat; the firmware reads it and gives an audio clip the track's exact number of bars. Two audio tracks with a track each then play in sync at the song's tempo, beatmatched without work. Firmware: the `acid` chunk in `AudioFile` and the clip length in the sample browser (small). Computer: analysis with madmom or beat_this (octave errors: a BPM range to fold into), loudness to R128.

**D. Crossfader.** Between two audio tracks (decks A and B), or between the input and the song. **Decided:** on the Deluge's own controls (a gold knob; MIDI learn stays possible).

**E. Isolator EQ.** Per deck: bass, mid and treble down to −∞ (kills). Bass and treble shelves exist since v18; a mid band and deeper cuts are new. Cost per deck: a few biquads.

**F. Key.** Done in v18.4: the Scan view shows the key of the input (chroma and a key profile, Camelot notation). Next step: one press sets the song's root note and scale (major or minor) to the key heard, so that the Deluge's own parts match an external track. Typical accuracy on dense electronic music about 75 % even for the best tools, errors mostly to neighbouring keys (see the analysis).

**G. A library tool for the computer** ("DelugeDJ", like DelugeTuner): BPM, first downbeat, key and loudness per track; writes `acid`, `cue ` and `smpl`, bar-exact loops, names with BPM and key; the report lists doubtful tempos (the half/double question) for a decision.

## Steps (decided)

1. **v19.0:** A (DJ filter) and B (sync to the input, nudge), with an external player. Small, cheap, useful at once. Built (patch 0119), details below.
2. **v19.1:** C and G (tracks as decks, the library tool). The biggest gain: DJing with the Deluge's own audio clips, in sync.
3. **v19.2:** D and E (crossfader, isolator), F's next step (the song's scale from the key heard).

## v19.0 in detail

Built in v19.0 (patch 0119), tested in `tests/dj`.

- **DJ filter** (Song view, AFFECT ENTIRE on, the filter mod button): pressing the upper gold knob goes round LPF, HPF, EQ and, for the song only, **DJ**.
  - In DJ the upper knob goes from low-pass closed (left) through off (middle) to high-pass open (right). It writes the song's own LPF and HPF cutoffs: turning left closes the LPF with the HPF off, turning right opens the HPF with the LPF off. So saving, automation recording and v18's glides work as for the other knobs.
  - Where LPF and HPF were set apart before (both on, e.g. in LPF and HPF mode), the knob goes on from there without a jump: to the right the LPF opens first, then the HPF; to the left the HPF closes first, then the LPF.
  - The lower knob turns both resonances, each from where it is; the popup shows the one of the filter that is on.
  - The knob's LEDs show the position, and a popup shows e.g. `DJ: LPF 16` (7-segment `LP16`, `OFF`, `HP16`).
- **Sync** (Scan view): TAP TEMPO sets the song's tempo to the BPM shown (with the ×2/÷2), undoable, popup `Sync 128.0 BPM` (7-segment `SYNC`). Before a tempo is heard: `No tempo yet`. With an external clock nothing changes (popup `External clock`). SHIFT + TAP TEMPO: the metronome, as everywhere.
- **Nudge** (any view, as 1.2.1 nudges clocks): hold the horizontal encoder and turn the tempo encoder.
  - With the internal clock, each detent bends the tempo 4 % faster or slower for 0.25 s: 10 ms of phase at any tempo. Turning on keeps it bent; then the tempo is exactly what it was. The MIDI and trigger clock outputs follow the bend, so gear synced to the Deluge moves with it.
  - STOP ends a bend at once; a song saved during a bend saves the tempo without it.
  - **SHIFT** + horizontal encoder + tempo encoder: 1.2.1's nudge, only the MIDI clock out (a clock more or less for the gear that follows; the Deluge itself stays).
  - With an external clock, as before.

## Decisions (29.09.2026)

1. **Use:** both, in this order: with an external player first (v19.0), then the Deluge as the player (v19.1).
2. **DJ filter:** on the song's gold knobs in Song view.
3. **Crossfader and nudge:** on the Deluge's own controls.
4. **Cueing** (still open): only with a second output (a USB audio return from the computer), a large piece of work; not planned for v19.
