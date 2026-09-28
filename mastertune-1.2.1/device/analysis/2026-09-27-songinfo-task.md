# Task for the development session: song and firmware for DelugeRec (SysEx 0x12), 27.09.2026

## The user's wish

DelugeRec's recordings should carry the exact song name, the date and the firmware.

## Why it needs the firmware

- **Date:** the PC knows it. DelugeRec already writes it into the name and into the file.
- **The format the user wants:** `Song name, dd.mm.yyyy - 1.2.1 v17`, for example `Rescue 3, 28.09.2026 - 1.2.1 v17.WAV` (DelugeRec v6). More recordings on the same day get " (2)", " (3)". Without a message from the Deluge, the file is called `28.09.2026 00-17-26.WAV` today, as seen by the user with v17-l2d.
- **Song name and firmware:** USB audio carries only samples; the Deluge has to report both itself. The PC must not ask, because the user wants it to send nothing to the Deluge. So the Deluge reports it on its own, as it already does with the CPU values (SysEx 0x10) on port 3.

## Protocol

- **Message:** `F0 00 21 7B 01 12 <JSON> F7` on USB MIDI port 3 (`upstreamUSBMIDIDevice_port3`), only sent, never answered.
  - JSON: `{"song":"Rescue 3","fw":"1.2.1-mastertune-v18"}`, UTF-8, 7 bytes packed into 8 as in `util/pack.c` (`pack_8bit_to_7bit`).
  - `0x12`, because `0x10` is taken by the CPU monitor and `0x11` by the profiler.
- **When:**
  - only while the computer has the audio stream open (`usbAudioIsStreaming()`, new)
  - at once when the stream starts and when the song name changes, otherwise every 2 s
  - only with 1 KB of room in the USB MIDI send buffer, as with the CPU monitor
- **Content:**
  - `song`: `currentSong->name`. Empty for a new song that was never saved. FatFs gives names in CP437 (`FF_CODE_PAGE 437`, `FF_LFN_UNICODE 0`). The patch converts them to UTF-8 with `ff_oem2uni`, at most 120 characters and 240 bytes, cut before a whole character.
  - `fw`: `kFirmwareVersionString`.

## Patch

`2026-09-27-songinfo.patch` in this folder, on v17 with `patches/0001–0074`, applies cleanly with `git am` (checked), also together with `l2test/0001–0003`.

- **Base checked:** `release_1_2_1` plus `patches/0001–0074` plus `l2test/0001–0003`, built with the hash `b3385d83`, gives byte for byte the file that runs on the device (`deluge-1.2.1-mastertune-v17-l2d-b3385d83.bin`, SHA-256 `aad4d080…5ec32c4b`). Please take it into v18 under the next free number.

- **New files:** `io/usb/usb_song_info.h` and `.cpp` (the routine), `usb_song_info_message.cpp` (the message alone, without hardware).
- **Changed files:**
  - `usb_audio`: `usbAudioIsStreaming()` returns `pipeRunning`.
  - `deluge.cpp`: the task "usb song info" every 0.25 s, right after the CPU monitor.

## Checked

- **Build:** `dbt build release` without errors, no new warnings. The routine is linked.
- **Message against DelugeRec:** The firmware's message building, compiled for the PC, with FatFs's real CP437 table (`ffunicode.c`), and the reader of DelugeRec v5 (`DelugeInfo.parse`) fit together. All 8 cases arrive exactly:
  - "Grüezi" from CP437
  - "Café ¢ 45°"
  - `"` and `\` in the name
  - an empty song
  - 200 characters, cut to 120
  - 120 × "ü" (exactly 240 bytes)
  - an umlaut beyond the 120 characters
  - a control character
  - The longest possible message has 428 bytes.
- **Review:** one checker over app and patch. Five bugs confirmed and fixed:
  - umlauts in CP437 (now converted to UTF-8)
  - port 3 never retried when it was taken
  - a possible hang of python-rtmidi when closing (now polling instead of a callback)
  - the header `F0 7D 12` after SysEx with the developer ID 0x7D (accepted)
  - `--list` without a MIDI system
  - Without finding: buffer sizes, escaping, timer overflow, `currentSong` while loading, sending without a host (`sendBufferSpace()` 0).
- **clang-format:** without finding.

## Not checked

- **On the device:** Please test with DelugeRec v6. The file name should then be `Song name, dd.mm.yyyy - 1.2.1 v18.WAV`, and the display shows "SONG …". At the user's request I built no test firmware: the firmware comes from the main session.
- **In the emulator:** USB doesn't run there.

## Notes

- **Port 3 is shared:** The CPU monitor and the profiler send there too, and `deluge_profiler.py` reads there. On Windows only one program can have a MIDI input open. So DelugeRec and `deluge_profiler.py` don't work at the same time.
- **Port 1 stays free:** DelugeRec never opens port 1, which a DAW needs.
