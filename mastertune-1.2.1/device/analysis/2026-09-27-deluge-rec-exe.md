# DelugeRec.exe with the checked state, 27.09.2026

**Result:** The .exe is rebuilt with the checked state, tested on Windows itself and replaced in the release. The link stays the same: https://github.com/Giansn/deluge/releases/download/deluge-rec/DelugeRec.exe

- **Source:** `tools/deluge_rec.py`, `tools/deluge_rec.ico` and `tests/deluge_rec` unchanged from the development branch (`2234215`, "reviewed (7 fixes) …"), on `geraet-ergebnisse` (now `device-results`) as `e0bc3a7`. The icon was the same already.
- **Tests here:**
  - `python3 tests/deluge_rec/test_deluge_rec.py`: 25 tests, all ok (5.8 s, Python 3.11, without audio and display).
  - Plus `--selftest 6` under Xvfb (Python 3.12): ok, a 4.4 s recording.
- **Workflow "DelugeRec for Windows", run 2** (https://github.com/Giansn/deluge/actions/runs/36343037010): all steps green, 2 min 15 s.
  - **Build:** PyInstaller, one file, without a console, Python 3.12.10.
  - **Self-test of the .exe on Windows:** PortAudio V19.7.0 loaded (the runner has no audio devices). The recording `USB00001.WAV` has 2 channels, 24 bit, 44,100 Hz and 193,599 frames: ok. Self-test ok, exit 0.
  - **File:** `DelugeRec.exe` with 25,075,574 bytes, SHA-256 `c1645dfd37ed4b60e738afd0b62c624c75a9ac9c88dba09d54db99bd7fbf9e3c`. It is in the release `deluge-rec` (replaced at 19:07 UTC) and as the artifact `DelugeRec-windows` of the run.
- **Note:** The tag `deluge-rec` still points to the first build (`6dff8ff`); the file in the release comes from `e0bc3a7`. The run shows which commit it was built from. Suggestion: the workflow writes the commit into the release note from now on.
- **Not checked:** with the real Deluge at the PC.

## v3: version numbers and icon

- **Numbering:** `VERSION` in `deluge_rec.py` is a whole number as with the firmware (v16, v17). Every change to the program counts up by one and gets a line under "Versions". Retroactively: v1 first build (`6dff8ff`), v2 checked state (`e0bc3a7`), v3 monitor, English, version number, icon (`b20307b`).
- **Where it's visible:** in the window title, at the start on the display ("USB REC V3"), with `--version`, in the self-test and in the file properties of the .exe (file version 3.0.0.0).
- **Releases:**
  - The .exe is always called `DelugeRec-vN.exe`, in the build, in the artifact and in both releases.
  - Every version gets a release of its own, `deluge-rec-vN`. `deluge-rec` (https://github.com/Giansn/deluge/releases/tag/deluge-rec) always has the newest as its only file. The old direct link to `DelugeRec.exe` no longer works, because the file name changes with every version.
  - A published version is never replaced. If `deluge_rec.py` or the icon change without a new number, the build stops. Otherwise it only builds and checks.
  - The tag `deluge-rec` stays on the first build. The tag `deluge-rec-vN` shows the exact state.
- **Icon:** the Deluge logo (25 squares in seven stripes) in the colours of the level meter, green, yellow, red from the bottom, plus the red recording dot at the top right. `tools/deluge_rec_icon.py` draws it.
- **Run 4** (https://github.com/Giansn/deluge/actions/runs/36346822155): all steps green, 1 min 24 s.
  - 32 tests on Windows ok.
  - Self-test of the .exe: "version: v3", the recording with 2 channels, 24 bit, 44,100 Hz and 194,040 frames ok.
  - `DelugeRec.exe` with 25,081,413 bytes, SHA-256 `7fa4ecffbbb4f69b1e13bc4f52e43b5aa13f9c872db8f8be80fb4af7bf03a898`, in the release `deluge-rec-v3` (https://github.com/Giansn/deluge/releases/tag/deluge-rec-v3). The previous link gives the same file.
- **Run 5** (https://github.com/Giansn/deluge/actions/runs/36347216300): all steps green. The build is now called `DelugeRec-v3.exe`. v3 was published already and unchanged, so there was no new release. `deluge-rec` got the published file of v3 (same SHA-256) and lost the old `DelugeRec.exe`.

## v4: VOL, boxes, Deluge proportions, monitor without crackle

- **Occasion:** The pads stood in the red area although the Deluge was set very quiet. At the start it sometimes crackled in the headphones.
- **Level:** The Deluge's VOLUME knob is analog and sits after the converter. So the USB signal never followed it, as with resampling (`usbAudioPushFrames` gets `outputBufferForResampling`).
  - **VOL:** a new vertical slider above THRESH, from 0 dB (bit-exact) to −30 dB, with arrow up/down, mouse wheel, dragging, double-click = 0 dB. It acts on the recording, the pads, ARM and the monitor. It glides changes over one block.
  - **Clipping:** What the Deluge clips itself, VOL can't save. The last pad therefore blinks after the input signal.
- **Crackle:** The monitor started without a fade. It now fades in and out over 10 ms, also across block borders, and jumps ahead with a crossfade.
  - Its output opens and closes with the Deluge's input. Before, PortAudio restarted under the open output at every reconnect.
- **Surface:** a box around every button, around VOL and around THRESH ("OUT FOLDER" read like one word), the window in the Deluge's aspect ratio (305 × 208 mm).
- **Check:**
  - Two checkers, each finding checked once more. Four bugs confirmed and fixed: no fade-out at block borders, a too short crossfade with small blocks, a jump at the start with a saved VOL, a test depending on the blink clock.
  - 39 tests. A stream simulation with real threads, clock deviation up to 20% and jitter in the clock of the blocks shows no jump bigger than the sine itself. The old v3 jumped there by 0.44.
- **Run 6** (https://github.com/Giansn/deluge/actions/runs/36349256737): all steps green. `DelugeRec-v4.exe` with 25,087,902 bytes, SHA-256 `a1c6174d868f2934606e7da6b59dcdb07553138c2d9e3eea930a89ee50cb2cb2`, in the release `deluge-rec-v4` and in `deluge-rec`.
- **Not checked:** with the real Deluge at the PC.

## v5: file names with song, date and firmware

- **Name:** "Song name date time firmware.WAV", for example `Rescue 3 2026-09-27 21-30-05 v17.WAV`. Never overwritten, otherwise " (2)", after 4 GB " part 2".
- **In the file:** a RIFF INFO list with title = song, date, program (DelugeRec v5) and a comment with the time, the full firmware and VOL.
- **Where song and firmware come from:** The Deluge has to report them. There is a firmware patch for it, see `2026-09-27-songinfo-task.md`.
  - DelugeRec only listens on USB MIDI port 3 (python-rtmidi). Port 1 stays free for the DAW; nothing is sent.
  - Without the patch the file is named only after date and time.
- **Check:** one checker over app and patch. Five bugs confirmed and fixed:
  - umlauts in CP437
  - port 3 wasn't retried when it was taken
  - a possible hang of rtmidi when closing
  - the header `F0 7D 12`
  - `--list` without a MIDI system
  - 46 tests.
- **Run 7** (https://github.com/Giansn/deluge/actions/runs/36354450337): all steps green.
  - Self-test on Windows: `midi: rtmidi 5.0.0, 0 inputs`, the recording was called `2026-09-27 22-13-15.WAV`.
  - `DelugeRec-v5.exe` with 25,395,835 bytes, SHA-256 `0c267315499470f0f67bea0ac39f72c00e1bfa4ca65e2850ea7492965d47768d`.
- **Not checked:** with the real Deluge. Song name and firmware first need a firmware with the patch.
