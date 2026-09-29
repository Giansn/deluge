# The dull undertone at the attack of multisamples, 29.09.2026

Heard on the device with v18.3 in "New Sitar Grii 10": "a dull undertone at the attack, at piano a little, at pluck strings partly very audible, not all multisamples".

## Result

- **Not the firmware.**
  - v17 and v18.3 render the song's attacks alike. Below 300 Hz the difference at the onsets is −72 dB for the sitar and −76 dB for the oboe, relative to the track. In the whole band the two differ by −35 dB: that is v18's filters, without its analog noise.
  - The note-ons and the 30 voice-limit culls are the same in both.
  - A cull adds nothing low: with 16 voices instead of 8 there are no culls, and the share below 300 Hz stays at −2.08 dB.
  - 440 against 432 Hz makes no difference either.
- **The samples are played far below the pitch they were recorded at.** A sample pitched down moves its attack, the pluck and the breath noise, into the low register, where it sounds as a thud.
  - **Sitar** ("170 Sitar 2"): the song plays notes 41 to 50. The lowest sample is at 53, so every note is 3 to 12 semitones down, a dull register all through.
  - **Oboe:** the song plays 77 to 86, all from the D5 sample (86), up to 9 semitones down. The attack's energy below 300 Hz rises from −22 dB at the root to −12.6 dB 9 semitones down, while the sustain stays at −25 dB. The WAV file resampled on the computer gives the same (−24.5 and −13.6 dB): it is the sample, not the firmware.
- **Why the oboe plays from one sample: wrong roots.** When the multisample was made, the Deluge detected the roots of the five lower oboe samples 43 to 53 semitones too low. So their zones cover only notes 0 to 53, and the D5 sample covers everything from 54 to 87. The files have no `smpl` or `inst` chunk, so the Deluge had only its own pitch detection.
- **The double bass has the same fault.** The roots of its three lowest samples (A#0, F#0, G0) are 28 to 44 semitones too high. The song's bass notes 83 to 86 therefore play the G0 sample 8 to 11 semitones up (at 78 to 93 Hz), instead of D1 to F#1 near their own pitch.

This fits "not all multisamples": it depends on how far a note is from its sample. It also fits "pluck strings": a pluck has a strong attack.

## The zones

`multisample_zones.py` lists them for a song or preset. The roots of the oboe and the double bass against the notes in their file names (C3 = 60, as `tests/scan` confirmed for all 20 files within 7 cents):

| Sound | Zones with a wrong root | Notes the song plays |
|---|---|---|
| Oboe | A#2, D3, F3, A#3, D4: root 43–53 semitones too low; D5 then covers 54–87 | 77–86: D5, 0 to 9 semitones down |
| 170 Sitar 2 | none (21 zones, one or two semitones each, the lowest at 53) | 41–50: the sample at 53, 3 to 12 semitones down |
| Kbass | A#0, F#0, G0: root 28–44 semitones too high | 83–86: G0, 8 to 11 semitones up |

## What helps

1. **Correct zones.** Roots from the file names, and the song's bass notes 44 lower, so that they keep their pitch. The oboe's notes 77 to 80 then come from D4 (3 to 6 up), 81 to 86 from D5 (up to 5 down). The bass notes 39 to 42 come from D1 to F#1, near their own pitch.
2. **Sitar:** lower samples, or the part an octave higher.
3. **Firmware (an idea for v19):** take the root at import from the file name where it names one, or find it with the pitch detection of the Scan view (YIN), which is right on all 20 files.

## The correction

`multisample_zones.py --fix` measures each zone's file (the note in its name, the cents heard as DelugeTuner hears them) and corrects the zones that play their file more than 10 cents off. Every note of the song keeps the pitch it sounded at, to the nearest semitone. The result is `device/card/SONGS/New Sitar Grii 10 zones.XML`:

- **Oboe:** the five lower zones get their measured roots, and each note goes to the nearest sample. Notes 73 to 80 now come from D4, 81 to 87 from D5.
- **Double bass:**
  - A#0, F#0 and G0 get their measured roots.
  - E1 is re-tuned: it was 84 cents sharp, a detection error of nearly a semitone that the test above did not show.
  - The clip's notes 83, 84 and 86 move to 39, 40 and 42, played from D1, E1 and F#1 near their own pitch. Before, they sounded about 38 cents flat of D#1, E1 and F#1, out of tune against the song. In the saved song the clip does not play.
- **Sitar:** unchanged; its zones match their files within 15 cents.

In the emulator (v18.4, 432 Hz, the same 4 bars):
- The corrected song loads and plays the same 90 note-ons, and the sitar sounds bit for bit the same.
- The oboe's attack below 300 Hz, relative to the whole band:

  | Note | Before | After | Now from |
  |---|---|---|---|
  | 77 | −12.6 dB | −22.3 dB | D4, 3 up |
  | 79 | −16.4 dB | −22.8 dB | D4, 5 up |
  | 81, 82 | −19.2, −20.1 dB | the same | D5, 5 and 4 down, as before |
  | 86 | −22.4 dB | the same | D5, its own pitch |

- The thud of 77 and 79 is gone: they are now as low in the bass as a note at its own pitch. They are also 3 to 4 dB louder, as loud as the other notes.
- What remains is the sitar's low register (41 to 50 from the sample at 53). Only lower sitar samples, or the part an octave higher, help there.

## Device check

Play the oboe at note 86 and at 77, and the sitar at 53 and at 41, on v17 and on v18.x. The thud should grow with the distance from the sample and be the same on both firmwares.

## Method

- **Emulator:** the real firmware of v17 and v18.3 (both l2d builds) played the song as saved: 432 Hz, 4 bars, 30 sitar and 16 oboe onsets. The tracks were captured separately (`sitar_emu.py`'s rig).
  - Per onset, the energy of the first 80 ms below 300 Hz relative to the whole band.
  - The difference v18.3 − v17, low-passed at 300 Hz.
  - Also run with maxVoices 16 and at 440 Hz, and with the firmware's task manager.
- **Oboe sample:** the WAV file resampled on the computer, compared at the root and 9 semitones down.
- **Limitation:** the emulator models neither the caches nor the codec.
