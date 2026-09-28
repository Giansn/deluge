# Baseline master for all songs, 27.09.2026

Firmware: mastertune v17-l2d (`b3385d83`, the state on the device). Code from `release_1_2_1` with `patches/0001–0074` and `l2test/0001–0003`. Songs from `device/card/SONGS`. The measurement ran in the emulator with exactly this firmware.

## In short

- **There is no blessed baseline.** Neither Synthstrom nor the developers of the community firmware give values for song, tracks or master compressor.
  - Only one target is official: on the Deluge's VU meter, not above the 3rd pad from the top, i.e. at most −4.5 (`docs/community_features.md`, section 4.1.8).
  - How the developers laid out the level shows in the defaults: song 35, kit 35, synth and kit row 40.
- **From 40 on, no track loses quality by itself.**
  - Every volume control is a pure multiplication in 32 bits, with over 30 dB of headroom above full scale.
  - Only the sum at the output can distort: at 0 dBFS the Deluge clips hard, the same for headphones, line out, USB and resample.
  - You hear it first on the track you're turning up, because its peaks get clipped.
- **Measured on your song:** "New Sitar Grii 10" with all 7 clips.

  | Kits | Peak |
  |---|---|
  | as saved | −6.1 dBFS |
  | at 35 | −1.5 dBFS |
  | at 40 | +0.5 dBFS, it clips |

  So the clipping starts exactly at 40.

## Why from 40 on

- **The chain:**
  1. voice and effects of the track
  2. volume of the track, then the sum in the kit
  3. kit volume, then the sum in the song
  4. song volume and master effects
  5. output

  None of these volumes saturates. Between a track's control and the output there is no distorting stage switched on in your songs either:
  - no SATURATION
  - no compressor
  - no bitcrush
  - the kit and song filters open or without resonance
  - flanger and phaser compute linearly
  - reverb and digital delay clip only far above 0 dBFS
  - The only analog delay (kit 3L3Ctr0) has no feedback.

  Deliberately distorting stages get stronger the louder the signal before them is: SATURATION, analog delay, filters with a lot of resonance, compressor. In your songs none of them acts after a track's control.
- **The only limit is the output** (`doSomeOutputting`, `lshiftAndSaturate<8>`): hard at 0 dBFS.
  - Just before it sits a soft saturation (tanh) in the master compressor. It always runs, but at 0 dBFS it pushes down only 0.17 dB, below that almost nothing.
  - Recomputed with the firmware's table `tanH2d`.
- **The measurement** (emulator, almost 7 s, all 7 clips; `2026-09-27-baseline-levels.json`):

| Setting | Peak | Samples above 0 dBFS |
|---|---|---|
| as saved: kits 21–27, rows mostly 40 | −6.1 dBFS | 0 |
| all kits at 35 (default) | −1.5 dBFS | 0 |
| all kits at 40 | **+0.5 dBFS** | **30** |
| all rows and synths from 40 to 50 | −5.1 dBFS | 0 |

- **The peak comes from the kit 3L3Ctr0 (kicks and basses).**
  - With the kits it rises by 4.6 and 6.7 dB, exactly like the control of 3L3Ctr0: 27 → 35 is +4.7 dB, 27 → 40 is +6.8 dB.
  - The rows from 40 to 50 raise it by only 1 dB. So it comes from rows that aren't at 40, probably from the four kicks and basses there at 50.

## What a control does in dB

All volume controls (song, kit, kit row, synth, 0–50) follow the same parabola: from a to b the level changes by 40·log10(b/a) dB.

| Control | 20 | 25 | 27 | 30 | 35 | 40 | 45 | 50 |
|---|---|---|---|---|---|---|---|---|
| against 40 | −12.0 | −8.2 | −6.8 | −5.0 | −2.3 | 0 | +2.0 | +3.9 |
| song and kit, against 35 (default) | −9.9 | −6.0 | −4.7 | −2.9 | −0.2 | +2.1 | +4.2 | +6.0 |

- Synth and kit row have 0 dB at 25. So their default 40 already raises by 8.2 dB.
- Song and kit have 0 dB at 35.4, their default. A kit also has a fixed +1.9 dB, as compensation for the filter resonance.

## The baseline

| What | Value | Why | Source |
|---|---|---|---|
| Song volume (song view, AFFECT ENTIRE, LEVEL; or SONG MENU > MASTER > VOLUME) | 35 to start, then down until the peak lies at −6 dBFS | It acts before the output: everything gets quieter, the balance of the tracks stays. In 32 bits turning down costs no quality. | default; measurement |
| Kit (clip view with AFFECT ENTIRE, or in song view hold the clip pad) | at most 35 | Your kits are at 21–27 | default |
| Synth, kit row | 40 as the upper limit for loud things like kick and bass, 45–50 only for quiet samples | From 40 to 50 is +3.9 dB | default |
| Master compressor | off (threshold 0, as in your songs) | At 0 it doesn't regulate and raises nothing. Switched on it makes up its reduction with make-up gain, so it creates no headroom. For recordings: mastering on the PC. | code; the docs only give ranges |
| Peak | at most −6 dBFS | In DelugeRec the pad −3 stays dark. On the Deluge's VU meter at most the 3rd pad from the top. The headroom covers passages that weren't measured. | docs 4.1.8 (−4.5); measurement |
| VOLUME knob on the Deluge | any | analog after the converter, doesn't act on USB and resample | code |
| VOL in DelugeRec | 0 dB | bit-exact. Turning it down doesn't repair clipping that already happens in the Deluge. | DelugeRec |
| Startup song | DEFAULTS > STARTUP SONG > TEMPLATE, save the song volume in it | At power-up the Deluge loads `SONGS/DEFAULT.XML` as a song without a name (`deluge.cpp`). | docs; code |

**For your songs:**
- **"New Sitar Grii 10":**
  - The song volume can stay at 35 as long as the kits are around 25 (−6.1 dBFS).
  - With the kits at the default 35 the song needs 27: −1.5 − 4.7 = −6.2 dBFS.
- **"Rescue":**
  - One synth at 34.8, not measured.
  - In the master the low-pass's resonance is at maximum. As long as the low-pass is fully open, the filter doesn't run. If you close it, it works with full resonance.

## Checking all songs and setting them to the baseline: DelugeBaseline

**DelugeBaseline-vN.exe** (release `deluge-baseline` on GitHub, built by `.github/workflows/deluge-baseline-windows.yml` from `tools/deluge_baseline.py` and `tools/baseline_check.py`) has two functions, LEVELS and NORM, each in the mode READ (only shows) or APPLY (shows, writes on the second START):

- **Read levels:** only reads. The report of `baseline_check.py`, see below.
- **Apply levels:**
  - song volume above 35 to 35, master compressor off
  - kits and audio tracks above 35 to 35
  - synths and kit rows down far enough that they act at most like 40 with a fully driven sample
  - Automation keeps its shape; all values drop by the same amount.
  - SATURATION, the tracks' compressors, analog delay and filters stay: that's sound, not level.
- **NORM (normalize samples):** Read shows what it would change, apply does it.
  - Raises every sample under `SAMPLES/` whose peak lies below the target (default −1 dBFS) up to the target, never above. No peak is cut, nothing clips.
  - Samples at the target or above stay. Format, bit depth and all chunks stay; only the audio changes.
  - The ranges of a multisample get a common gain, so that their balance stays.
  - **Compensate** (default on): Every oscillator that plays a raised sample is turned down by exactly as much (osc A/B volume), in every clip of every song and in the kits and synths of `KITS/` and `SYNTHS/`. The voice gets the same signal as before, still before filters and effects.
  - Never touched: wavetables and audio clips.
  - With compensation, samples that can't be compensated safely stay too: oscillator level not saved or with a cable, FM, a format from before 2017, or a copy in the song's own folder (`SONGS/<Song>/`, from "Collect media"). The firmware looks there first as soon as a file of the song is missing, and may then play the copy.
  - A clip belongs to its instrument by name and folder, as in the firmware: two synths "Bass" in different folders stay apart.
- **Restore:** plays back a backup; the old state comes back.
- **Writing:** always only after a preview. To tick: directly onto the SD card (the old files go to `BASELINE-BACKUP/<date time function>/`) or into a copy folder (only the changed files, in the card's layout).
- **Window:** in DelugeRec's look, a little bigger for the text. German or English, with the switch SPRACHE / LANGUAGE.
  - OLED with a pixel font, one pad per song: green in order, orange with notes, red unreadable.
  - FUNCTION: LEVELS (L) or NORM (N). MODE: READ (R) or APPLY (A). START (Enter) runs it. With APPLY the OLED first shows what would change, and the pads of the songs concerned blink. START again writes, Esc cancels.
  - WRITE TO: tick SD CARD DIRECTLY or COPY FOLDER. NORMALIZE: tick COMPENSATE; the gold knob TARGET chooses the samples' target: 0, −0.3, −1, −3 or −6 dBFS.
  - CARD (C) chooses the card, REPORT (T) opens the whole report as text, RESTORE (B) shows the backups. Mouse wheel and arrow keys scroll through the list.

Without a window, e.g. for the local session: `py mastertune-1.2.1/tools/deluge_baseline.py [--lang en] check|levels|normalize E:\ [--yes]`. Without `--yes` it only shows what it would do.

**Checked in the emulator** (v17-l2d, "New Sitar Grii 10", all 7 clips, the same 302,400 samples as above):

| Card | Peak | RMS | Difference to the original |
|---|---|---|---|
| Original | −6.147 dBFS | −25.785 dBFS | |
| normalized and compensated: 73 samples raised by 0.2 to 23.2 dB, 59 oscillators compensated | −6.146 dBFS | −25.784 dBFS | at most 1 LSB (16 bit), −103 dBFS on average |
| levels applied | −6.147 dBFS | −25.785 dBFS | none |

- **Normalizing with compensation:** The song sounds the same.
- **Levels:** change nothing audible here. The too loud rows in 3L3Ctr0 have no notes in the clip and only sound when you play them live. The peak comes from rows at 40, i.e. within the baseline. The report adds "without notes".

### What "check" reports (`baseline_check.py`)

```
py mastertune-1.2.1/tools/baseline_check.py E:\ --out baseline-card.md
```

- **It reports:**
  - song, kit or audio track above 35
  - the master compressor, if it's on
  - synths and kit rows above 40, with "without notes" if the row has no notes in any clip
  - stages after the controls that distort more with the level: SATURATION, compressor, analog delay with feedback, low-pass with drive, active filters with resonance from 25
- **Quiet samples:** A row above 40 is allowed if its sample is quiet enough. For this the script reads the sample's peak in the played range and the oscillator's level. "Acts like" is the control a fully driven sample would need for the same level: control times 10^(peak/40), times osc level divided by 50. A sample at −4.6 dBFS at 50 acts like 38.4 and is in order.
- **Without this allowance:** If the voice also plays an oscillator, noise or FM, the control alone counts.
- **Levels in dB as on the Deluge from mastertune v18 on:** Every level is shown as the control value and in dB, for example "40.0 (+8.16 dB)". The formula is the firmware's (`volume_steps.cpp`, patch 0101): 40·log10((p + 2³¹) / 2³¹), for kit, audio track and song 6.02 dB less. Examples:
  - song 35 = −0.18 dB, the default 35.4 = 0.00 dB, synth 40 = +8.16 dB.
  - At the top: synth 50 = +12.04 dB, kit and song 50 = +6.02 dB.
- **Between the detents:** v18 turns in 0.5 dB steps, and it saves as before. The values therefore lie between the old positions 0–50.
  - Report and window show them to 0.1 and the dB to 0.01. Nothing is ever rounded to a whole position.
  - The limits apply in dB: whatever lies less than 0.005 dB above counts as at the limit.
  - So the report now also reports narrow overshoots, like a row at 45 with a sample at −2 dBFS: +0.05 dB above 40. The former tolerance of 0.4 positions swallowed such cases.
- **On the card copy:**
  - "New Sitar Grii 10": five rows in the kit 3L3Ctr0 (kicks and basses, acting like 40.8 to 49.9, all without notes) and the row "hihatlong" at 50, whose sample is missing here. One of the rows is at 49.6 (+11.90 dB); the old report showed 50.
  - "Rescue": in order, the loudest row is at 34.8 (+5.73 dB)
- **Limit:** It doesn't say whether a song clips. Only measuring shows that.

## The Deluge's VU meter

- **It doesn't show peaks.** It shows the mean, and its dB are compressed: `(ln(mean) − 16.7) × 4`. One displayed dB is about 2.2 real dB (`view.cpp`, `envelope_follower.cpp`).
- **It can already clip while the meter shows yellow.** The docs call the 2nd pad from the top "soft clipping", but the firmware clips hard (see above).
- **For the baseline, DelugeRec counts:** it measures every single peak.
- **Switching on:** AFFECT ENTIRE on, choose LEVEL/PAN, then press LEVEL/PAN again (docs 4.1.8).

## Backed and not backed

- **Official** (Synthstrom's repo: `docs/community_features.md`, `CHANGELOG.md`):
  - the target −4.5 on the VU meter
  - release 1.1: the compressor was changed "to reduce clipping". Songs from 1.0 may need a new song volume.
- **The firmware's defaults:** song 35, kit 35, synth and row 40, velocity 64, master compressor off.
- **Community, only seen as search hits:** The forum was blocked from here; I couldn't check the authors.
  - Set the song volume low in the template and get the level at the output or on the mixer.
  - From velocity 100 on it clips with the default values.
- **Not found:**
  - values for tracks, master compressor, sends and panorama
  - how many tracks work without clipping

## Rebuilding the measurement

```
python3 baseline_levels.py <deluge.elf> ../card <out> --song-dir <dev>/mastertune-1.2.1/tests/song \
    --build <folder with blockcount.so>
```

- **Duration and setup:** A run takes about 2 min. The script uses `sitar_emu.py`: 440 Hz, all kits, 1 bar of lead-in, then 302,400 samples.
- **Peak:** measured at the output before the clip, so it can lie above 0 dBFS.
- **Limit:** Only this range is measured; other passages in the song can be louder.
