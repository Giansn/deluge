#!/usr/bin/env python3
"""Tests tools/song_check.py on a card made of make_sd.py's song and samples (tests/song), and variants of the song
with each problem the checker looks for:
- the song as generated: OK, its 10 samples found
- an <audioClip> with its six attributes twice, as the firmware saves it (#4917): WARN, named; one value differing
- the song cut short: ERROR, not well-formed
- a sample path that isn't on the card: ERROR, missing; a path in lower case: found (FAT is case-insensitive)
- samples only in the song's own folder (SONGS/<song>/: the path after SAMPLES/ with "_", and the file name alone):
  NOTE, found as the Deluge finds them
- a named CV instrument without a channel and a clip on CV channel 1: WARN, both named
- a bare "&" in a sample's name (the Deluge writes it so): NOTE, and the sample found under its name as written
- earliestCompatibleFirmware c1.3.0: WARN for a c1.2.1 Deluge, nothing for --firmware c1.3.0
- a Mac's "._" file next to the songs: skipped; a synth preset in SYNTHS: checked
Then the command line: one line per file, the summary, exit status 1 (there are errors).

Usage: song_check_test.py   (pure Python 3)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "song_check.py")
sys.path.insert(0, os.path.join(HERE, "song"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import make_sd  # noqa: E402
import song_check  # noqa: E402

failures = 0


def check(cond, msg, got=""):
    global failures
    print(("ok    " if cond else "FAIL  ") + msg + ("" if cond else f": {got}"))
    failures += 0 if cond else 1


def main():
    card = tempfile.mkdtemp(prefix="song_check_")
    try:
        files, lengths = make_sd.samples()
        for path, data in files.items():
            os.makedirs(os.path.join(card, os.path.dirname(path)), exist_ok=True)
            open(os.path.join(card, path), "wb").write(data)
        song = make_sd.song_xml(lengths, 1)
        os.makedirs(os.path.join(card, "SONGS"))
        os.makedirs(os.path.join(card, "SYNTHS"))

        def write(name, xml):
            path = os.path.join(card, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w", encoding="cp437", newline="").write(xml)
            return path

        def run(path, firmware="c1.2.1"):
            return song_check.check_file(path, song_check.Card(card), firmware, True)

        sev, line, _ = run(write("SONGS/PLAIN.XML", song))
        check(sev == 0 and "10 samples" in line, "the song as generated: OK, 10 samples", line)

        # The audio clip's attributes twice, as AudioClip::writeDataToFile() writes them
        m = re.search(r"<audioClip\b(.*?)>", song, re.S)
        twice = "".join(f'\n\t\t\t{n}="{v}"' for n, v in re.findall(r'\b(colourOffset|isArmedForRecording|isPlaying|'
                                                                     r'isSoloing|length|section)="([^"]*)"', m.group(1)))
        dup = song[:m.end(1)] + twice + song[m.end(1):]
        sev, line, details = run(write("SONGS/DUPLICATE.XML", dup))
        check(sev == 2 and "#4917" in line and "colourOffset" in line and len(details) == 1,
              "the audio clip's attributes twice: WARN, #4917, the element's line", line)
        differ = song[:m.end(1)] + twice.replace('section="', 'section="1', 1) + song[m.end(1):]
        sev, line, _ = run(write("SONGS/DIFFER.XML", differ))
        check(sev == 2 and "differing: section" in line, "one of them with another value: named", line)

        sev, line, _ = run(write("SONGS/CUT.XML", song[:len(song) * 2 // 3]))
        check(sev == 3 and "not well-formed" in line, "the song cut short: ERROR, not well-formed", line)

        gone = song.replace('fileName="SAMPLES/KICK.WAV"', 'fileName="SAMPLES/NOT_HERE.WAV"')
        sev, line, details = run(write("SONGS/MISSING.XML", gone))
        check(sev == 3 and "1 of 10 samples missing" in line and details == ["missing: SAMPLES/NOT_HERE.WAV"],
              "a sample not on the card: ERROR, named", line)
        lower = song.replace('fileName="SAMPLES/KICK.WAV"', 'fileName="samples/kick.wav"')
        sev, line, _ = run(write("SONGS/LOWER.XML", lower))
        check(sev == 0, "the path in lower case: found", line)

        # Only in the song's own folder, as "collect media" leaves them
        own = song.replace('fileName="SAMPLES/SNARE.WAV"', 'fileName="SAMPLES/DRUMS/SNARE.WAV"')
        own = own.replace('fileName="SAMPLES/CLAP.WAV"', 'fileName="SAMPLES/ELSEWHERE/CLAP.WAV"')
        write("SONGS/OWN/DRUMS_SNARE.WAV", "")
        write("SONGS/OWN/CLAP.WAV", "")
        sev, line, _ = run(write("SONGS/OWN.XML", own))
        check(sev == 1 and "2 samples found in SONGS/OWN/" in line, "samples in the song's own folder: NOTE", line)

        # A named CV instrument (saved without its channel) and a clip on CV channel 1
        cv = song.replace("<instruments>", '<instruments>\n\t\t<cvChannel\n\t\t\tpresetName="MOD CV"\n'
                          '\t\t\tdefaultVelocity="64"></cvChannel>', 1)
        cv = cv.replace("<sessionClips>", '<sessionClips>\n\t\t<instrumentClip\n\t\t\tcvChannel="1"\n'
                        '\t\t\tlength="96"></instrumentClip>', 1)
        sev, line, _ = run(write("SONGS/CV.XML", cv))
        check(sev == 2 and "'MOD CV' has no channel" in line and "clips on channel 1" in line,
              "a named CV instrument without a channel: WARN, its clip's channel named", line)

        # A bare "&" in a name, as the Deluge writes it: the sample is found under its name as written
        shutil.copy(os.path.join(card, "SAMPLES", "RIM.WAV"), os.path.join(card, "SAMPLES", "R&B.WAV"))
        amp = song.replace('fileName="SAMPLES/RIM.WAV"', 'fileName="SAMPLES/R&B.WAV"')
        sev, line, _ = run(write("SONGS/AMP.XML", amp))
        check(sev == 1 and "1 bare '&'" in line and "10 samples" in line, "a bare '&': NOTE, the sample found", line)

        newer = song.replace('earliestCompatibleFirmware="4.1.0-alpha"', 'earliestCompatibleFirmware="c1.3.0"')
        path = write("SONGS/NEWER.XML", newer)
        sev, line, _ = run(path)
        check(sev == 2 and "needs firmware c1.3.0" in line, "earliestCompatibleFirmware c1.3.0: WARN", line)
        sev, line, _ = run(path, "c1.3.0")
        check(sev == 0, "the same for a c1.3.0 Deluge: OK", line)

        # The command line over the whole card
        write("SONGS/._PLAIN.XML", "\0\5\26\7 not XML")
        synth = re.search(r"<sound\b.*?</sound>", song, re.S).group(0)
        write("SYNTHS/SYNT000.XML", '<?xml version="1.0" encoding="UTF-8"?>\n' + synth.replace(
            "<sound", '<sound firmwareVersion="c1.2.1" earliestCompatibleFirmware="4.1.0-alpha"', 1))
        r = subprocess.run([sys.executable, TOOL, card], capture_output=True, text=True)
        out = r.stdout.splitlines()
        files_lines = [s for s in out if s[:5].strip() in song_check.SEVERITY]
        check(len(files_lines) == 11 and not any("._" in s for s in files_lines),
              "the card: one line per file (10 songs, 1 synth), the '._' file skipped", "\n".join(out))
        check(any(s.startswith("OK     SYNTHS/SYNT000.XML") for s in out), "the synth preset checked: OK", "\n".join(out))
        check(out[-1] == "11 files: 3 OK, 2 NOTE, 4 WARN, 2 ERROR" and r.returncode == 1,
              "the summary, and exit status 1 for the errors", f"{out[-1]!r}, exit {r.returncode}")
    finally:
        shutil.rmtree(card)
    print(f"{failures} FAILURES" if failures else "all checks passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
