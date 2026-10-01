# Song file checker for the SD card, 01.10.2026

`tools/song_check.py`: pure Python 3, no packages, runs on Windows. Usage: `python song_check.py <card folder> [-v]`.

## What it checks

It walks every `*.XML` under `SONGS/`, `SYNTHS/` and `KITS/`, skipping a Mac's `._` files. It prints one line per file (OK, NOTE, WARN or ERROR, the firmware that saved it, the sample count and what was found), then a summary. With `-v` each finding is listed under its file. Exit status 1 means an error.

| Check | How, and why | Level |
|---|---|---|
| Well-formed XML | expat, after taking out the duplicates and bare `&` below. Line numbers are kept, so a file cut short or garbled is found. | ERROR |
| Duplicate attributes | Per element. `AudioClip::writeDataToFile()` calls `Clip::writeDataToFile()` twice: `model/clip/audio_clip.cpp:1085-1086` (mastertune), `:1093-1094` (beta 62a516c2), upstream #4917. Values that differ are named. | WARN |
| Bare `&` | The Deluge writes attribute values unescaped (`storage/storage_manager.cpp:732-746`) and reads them without decoding entities. The checker therefore notes a bare `&` and looks the path up as written. A tool that rewrites `&` as `&amp;` breaks the path for the Deluge. | NOTE |
| Samples | Every `fileName` and `filePath`, attribute or element. They are looked up the way `AudioFileManager::getAudioFileFromFilename()` does: from the card root, case-insensitively (FAT), names in code page 437 (`fatfs/ffconf.h:87,136`). If not found there, in the folder named after the song or preset (`<folder>/<name>/`, `audio_file_manager.cpp:448-466`): first the path after `SAMPLES/` with `/` → `_` (`:469-491`), then the file name alone (`:700-716`). | missing: ERROR; found only in the song's folder: NOTE |
| CV instruments | A song's `<cvChannel>` without `channel`. `Instrument::writeDataToFile()` writes the channel only for unnamed CV instruments (`model/instrument/instrument.cpp:91-98` mastertune, `:86-92` beta). A named one loads on channel 0 (`non_audio_instrument.h:59`), and its clips (`cvChannel="N"`) find no instrument there. Neither firmware lets the UI name a CV track (`arranger_view.cpp:977` mastertune, `:999` beta), so this only arises from edited files or other forks. | WARN |
| Firmware | `firmwareVersion` is shown. `earliestCompatibleFirmware` is compared with `--firmware` (default `c1.2.1`): the Deluge refuses a file that needs a newer firmware (`storage_manager.cpp:1804-1810`). Official versions sort below community ones, as in `FirmwareVersion`. | WARN |

## Results

- **make_sd.py's song** (`tests/song`): OK, its 10 samples found.
- **The same song saved by mastertune v19.0 in the emulator** (`song_emu.py --write-back`):
  - WARN: the `<audioClip>` carries `colourOffset, isArmedForRecording, isPlaying, isSoloing, length, section` twice, with equal values.
  - Nothing else: well-formed once the duplicates are removed, all samples found, `firmwareVersion="c1.2.1"`.
- **The user's songs in `device/card/`:**
  - *New Sitar Grii 10* and *New Sitar Grii 10 zones*: the same #4917 duplicates, from their audio track. 3 of 139 samples are missing: `SAMPLES/PsyPack/hihat.wav`, `hihatlong.wav`, `hihatshort.wav` (the Hihat kit rows). `samples-new-sitar-grii-10.csv` lists them as not in the backup, so they were probably missing on the card already and those rows play silent.
  - *Rescue*: its 8 samples are on the user's card (`samples-rescue.csv`) but not in the repository's copy, so ERROR here is expected.

## Tests

`tests/song_check_test.py` builds a card from make_sd.py's song and samples. It covers 14 checks, all passing:
- the duplicates (equal and differing values);
- a file cut short;
- a missing sample, and a lower-case path that is found;
- both fallback folders;
- a named CV instrument with a clip on channel 1;
- a bare `&` with its sample found;
- `earliestCompatibleFirmware="c1.3.0"` against c1.2.1 and c1.3.0;
- a `._` file skipped and a synth preset checked;
- the command line's lines, summary and exit status.

## For the development session

- **#4917:** one line, `audio_clip.cpp:1086`.
- **CV channel:** for a named CV instrument, write the channel too (`instrument.cpp:94`: `else if` → `if`). Harmless today, since the UI can't name CV tracks.
