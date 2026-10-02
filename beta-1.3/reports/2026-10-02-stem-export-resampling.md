# Seed 1226: E242, a stem export started while the output was being resampled (patch 0116), 02.10.2026

Stop run 9's seed 226 (`--mode deep`, OLED, 39 patches) froze twice. This report is about the second freeze, in the fuzzer's second boot (seed 1226), at input 359: E242 in `AudioRecorder::setupRecordingToFile()`, on `RECORD` in song view. A replay froze at the same input. The first freeze (E058) has its own report.

## Finding

The replay traced the audio recorder:

| Input | What | Traced |
|---|---|---|
| 341 | SHIFT held + RECORD (song view) | `AudioRecorder::beginOutputRecording()`: the output is recorded (resampling) |
| 342-358 | other inputs | the resampling goes on |
| 359 | SAVE held + RECORD | `SessionView::buttonAction()` → `StemExport::startStemExportProcess()` → `startOutputRecordingUntilLoopEndAndSilence()` → `beginOutputRecording()` → `setupRecordingToFile()`: E242 |

The cause:
- **The check:** `setupRecordingToFile()` (`src/deluge/gui/ui/audio_recorder.cpp:137`) freezes in a beta build if a recording is already set up (`recordingSource`). A release build has no check: the new recorder replaces `recorder`, and the running one is never finished.
- **The export:** SAVE + RECORD starts a stem export in song view (`session_view.cpp:421-429`), in a kit's clip view (`instrument_clip_view.cpp:406-413`) and in arranger view (`arranger_view.cpp:262-277`). Each checks playback and song recording (`playbackHandler.recording`), not the audio recorder. The song menu's export item (`menu_item/stem_export/start.h:31-47`) checks nothing. Each stem is recorded through the audio recorder.

A user gets there with ordinary inputs: SHIFT + RECORD, then SAVE + RECORD. This is a real freeze in the beta build, and a lost recorder in a release build. It is not a harness artifact: the fuzzer's audio recorder harness (`ModalRecorder`) only ends the sound editor's RECORD AUDIO, not resampling.

**1.2.1:** the same ways in (song view, arranger view, the menu) with the same checks. Its release build has no E242 check, so the running recorder is replaced. A candidate for mastertune; not run on 1.2.1.

## Fix: patch 0116

`StemExport::startStemExportProcess()` (`stem_export.cpp:85`) refuses while the audio recorder records, with the views' "can't export stems" popup. This covers all four ways in. The resampling goes on. The patch adds 7 lines in 1 file.

## Tests

`tests/repro/stem_export_resampling_emu.py`, now in `run.sh`:

| Check | 39 patches, without 0116 | + 0116 |
|---|---|---|
| (1) SHIFT + RECORD, then SAVE + RECORD | E242 | the popup; the resampling goes on |
| (2) BACK, then a short RECORD press | (not reached) | the resampling ends |
| Result | FAIL (1, 2, problems) | PASS |

`tests/repro/run.sh` on the series with 0116 (`patches/series` up to 0025, then 0115 and 0116; 40 patches, applied with `git am` to 62a516c2): 36 of 36 PASS.

## Status

Runs: seed 1226 replayed once (the freeze, the audio recorder traced), `stem_export_resampling_emu.py` before and after, `run.sh`. Problems: 1 (E242, a stem export while resampling), fixed by 0116. Seed 226's first freeze (E058) is traced separately.
