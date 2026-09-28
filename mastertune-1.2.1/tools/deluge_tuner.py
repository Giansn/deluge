#!/usr/bin/env python3
"""DelugeTuner: retunes the sample library of a Deluge card once to the master tune, so that its samples play natively
at that tuning (mastertune firmware): retune_library.py in a window in DelugeRec's look. Built into DelugeTuner-vN.exe
for Windows by .github/workflows/deluge-tuner-windows.yml; runs as a script anywhere.

What it does (retune_library.py's docstring has every detail): it reads a card, the SD card itself or a copy of it,
and writes a new card into a folder on the computer, to copy onto an empty SD card. The card is only read. Every
sample is resampled once from its own tuning to the one chosen (and to 44.1 kHz, unless each file keeps its rate),
with the tuning in its mtun chunk; AIFF becomes WAV; the positions in the songs, kits and synths are scaled to match;
audio clips and time-stretched samples are pitch-shifted keeping their length (Rubber Band); wavetables and everything
else are copied as they are. Peaks that resampling puts over full scale: the file as 32-bit float, or a little
quieter; never clipped.

Its window: CARD chooses the card, OUTPUT the folder for the new card (that folder if it is empty, else a new folder
in it, "Deluge 432 Hz", numbered if taken; beside it if it is a card itself), the gold knob the tuning (415.3 to
466.2 Hz: 1 Hz a step, 0.1 Hz with Shift or the arrow keys; a click on the number types it), boxes to tick the sample
rate and what happens to peaks. Two modes: READ only reads and shows what it would do, RETUNE writes the new card (the
progress on the display and the pads). ESC, or closing the window, stops it once the files being converted are done:
START with the same card, folder and settings continues (CONTINUE). REPORT opens the report. German or English: the
computer's language to start with (system_language(): Windows' display language, the locale on macOS and Linux),
then the switch in the window, which it keeps.

Usage (Windows: py instead of python3): python3 deluge_tuner.py [--lang de|en]   (the window; the command line is
retune_library.py's). Needs numpy and soxr (pip install numpy soxr), pylibrb for audio clips and time-stretched samples.

Versions:
  1  the first build: read or retune a card into a new folder, 415.3 to 466.2 Hz, stop and continue, German and English
  2  the computer's language to start with (Windows: its display language; macOS, Linux: the locale); code in English
"""
import argparse
import json
import math
import multiprocessing
import os
import queue
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retune_library as rl  # noqa: E402

VERSION = 2
START_TENTHS = 4320  # The gold knob at the first start: 432 Hz
GROWTH = 1.05  # The new card's size to the card's, about: longer files at a lower tuning (440/432 = 1.0185), headroom
REPORT = "RETUNE_REPORT.txt"
CARD_FOLDERS = ("SAMPLES", "SONGS", "KITS", "SYNTHS")
LANG = "de"  # The window's language: de or en

PANEL, PLATE, EDGE, BEZEL, LABEL, SMALL = "#0e0e10", "#18181b", "#26262b", "#050506", "#d8d8de", "#8c8c96"
BOX, BOX_EDGE = "#1d1d21", "#35353d"  # The box around each control
OLED_ON, OLED_OFF = (226, 238, 255), (7, 9, 13)
GREEN, AMBER, RED, CYAN, WHITE, BLUE = "#2fdc6e", "#ffae1c", "#ff2d2d", "#35d4e8", "#e8e8f0", "#3d8bff"
# retune_library's categories of the audio files: (key, what the list calls them read, and written, the pads' colour)
CATEGORIES = (("converted", ("WERDEN UMGESTIMMT", "TO RETUNE"), ("UMGESTIMMT", "RETUNED"), CYAN),
              ("already native", ("SCHON IN DER STIMMUNG", "ALREADY IN TUNE"),
               ("SCHON IN DER STIMMUNG", "ALREADY IN TUNE"), GREEN),
              ("left as it is", ("BLEIBEN, WIE SIE SIND", "LEFT AS THEY ARE"),
               ("BLIEBEN, WIE SIE SIND", "LEFT AS THEY WERE"), WHITE),
              ("missing", ("FEHLEN AUF DER KARTE", "MISSING ON THE CARD"),
               ("FEHLEN AUF DER KARTE", "MISSING ON THE CARD"), RED))


def t(de, en):
    """The text in the window's language."""
    return de if LANG == "de" else en


def num(x, digits=1):
    """A number as the language writes it: 432,0 or 432.0."""
    s = f"{x:.{digits}f}"
    return s.replace(".", ",") if LANG == "de" else s


def hz(tenths):
    return num(tenths / 10) + " HZ"


def cents(tenths):
    """The tuning against 440 Hz, in cents: 432 Hz is -31.8."""
    c = 1200 * math.log2(tenths / rl.DEFAULT_TENTHS)
    return ("+" if round(c, 1) > 0 else "") + num(c) + " CENT"


def songs(n):
    return f"{n} SONG" + ("" if n == 1 else "S")


def size_text(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else num(n) + " " + unit
        n /= 1024


# --------------------------------------------------------------------------------------------------------------------
# The card, and where the new one goes


def card_root(path):
    """The card's root: the folder with SAMPLES, SONGS, KITS or SYNTHS in it, or the folder above one of them.
    ValueError if it is neither."""
    path = os.path.abspath(path)
    try:
        names = {n.upper() for n in os.listdir(path) if os.path.isdir(os.path.join(path, n))}
    except OSError as e:
        raise ValueError(str(e)) from e
    if names & set(CARD_FOLDERS):
        return path
    if os.path.basename(path.rstrip("\\/")).upper() in CARD_FOLDERS:
        return os.path.dirname(path.rstrip("\\/"))
    raise ValueError(path)


def card_stats(card):
    """What the card holds: audio files under SAMPLES, songs, all files and their bytes."""
    audio = songs = files = size = 0
    for dirpath, dirnames, names in os.walk(card):
        dirnames.sort()
        top = os.path.relpath(dirpath, card).split(os.sep)[0].upper()
        for name in names:
            files += 1
            try:
                size += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
            audio += top == "SAMPLES" and name.lower().endswith(rl.AUDIO_EXTENSIONS)
            songs += top == "SONGS" and name.lower().endswith(".xml")
    return dict(audio=audio, songs=songs, files=files, bytes=size)


def folder_bytes(folder):
    total = 0
    for dirpath, _, names in os.walk(folder):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def free_bytes(folder):
    """The free space where folder is or will be (its first ancestor that is there)."""
    while not os.path.isdir(folder) and os.path.dirname(folder) != folder:
        folder = os.path.dirname(folder)
    return shutil.disk_usage(folder).free


def inside(a, b):
    """a is b or in it (paths as the file system compares them; a drive's root ends with its separator already)."""
    a, b = (os.path.join(os.path.normcase(os.path.abspath(p)), "") for p in (a, b))
    return a.startswith(b)


def looks_like_card(folder):
    return os.path.isfile(os.path.join(folder, REPORT)) or any(
        os.path.isdir(os.path.join(folder, name)) for name in CARD_FOLDERS)


def destination(chosen, card, tenths, rate, no_float, runs):
    """Where the new card goes: (folder, resume). The folder chosen if it is empty or not there yet, or if it holds a
    run of this card with these settings that was stopped (resume, recorded in runs: {folder: card} of the runs
    started and not finished). Else a new folder in it, "Deluge 432 Hz", numbered if taken (or its stopped run);
    beside it if the folder chosen is a card itself (the one made before, say). ValueError if it is in the card."""
    chosen, card = os.path.abspath(chosen), os.path.abspath(card)
    if inside(chosen, card):
        raise ValueError(t("ZIEL IST IN DER KARTE: EINEN ORDNER AUSSERHALB WÄHLEN",
                           "OUTPUT IS IN THE CARD: CHOOSE A FOLDER OUTSIDE IT"))
    header = rl.progress_header(argparse.Namespace(tenths=tenths, rate=rate, no_float=no_float))

    def state(folder):
        """False: new (empty or not there), True: a stopped run to continue, None: taken."""
        if inside(folder, card) or inside(card, folder):
            return None
        if not os.path.exists(folder):
            return False
        if not os.path.isdir(folder):
            return None
        if not os.listdir(folder):
            return False
        if runs.get(folder) == card and rl.read_progress_header(os.path.join(folder, rl.PROGRESS)) == header:
            return True
        return None

    found = state(chosen)
    if found is not None:
        return chosen, found
    base = os.path.dirname(chosen) if looks_like_card(chosen) and os.path.dirname(chosen) != chosen else chosen
    name = f"Deluge {tenths / 10:g} Hz"
    for i in range(1, 1000):
        folder = os.path.join(base, name if i == 1 else f"{name} ({i})")
        found = state(folder)
        if found is not None:
            return folder, found
    raise ValueError(t("KEIN FREIER NAME IM ZIEL", "NO FREE NAME IN THE OUTPUT"))


def pads_of(counts, n):
    """The pads as a bar of the categories (their order): n in all, by the largest remainder, one at least for each
    category there is."""
    values = [(key, counts.get(key, 0)) for key, *_ in CATEGORIES]
    total = sum(v for _, v in values)
    if not total:
        return []
    alloc = {key: max(1, int(v * n / total)) if v else 0 for key, v in values}
    rest = sorted(values, key=lambda kv: -(kv[1] * n / total - int(kv[1] * n / total)))
    while sum(alloc.values()) < n:
        for key, v in rest:
            if v and sum(alloc.values()) < n:
                alloc[key] += 1
    while sum(alloc.values()) > n:
        key = max(alloc, key=alloc.get)
        alloc[key] -= 1
    return [key for key, _ in values for _ in range(alloc[key])]


def result_items(result, dry_run, tenths, out):
    """The OLED's list of a run: (text, heading) per line."""
    items = []
    for key, read, written, _ in CATEGORIES:
        n = result["counts"].get(key, 0)
        if n:
            label = t(*(read if dry_run else written))
            items.append((f"{n} {label}" + (f" ({hz(tenths)})" if key == "already native" else ""), False))
    items.append((f"{result['xml']} SONGS/KITS/SYNTHS, {result['values']} " + t("WERTE", "VALUES"), False))
    items.append((f"{size_text(result['size_old'])} -> {'~' if dry_run else ''}{size_text(result['size_new'])}",
                  False))
    if result["as_float"]:
        items.append((t(f"{len(result['as_float'])} ALS 32-BIT FLOAT (SPITZEN ÜBER 0 DBFS)",
                        f"{len(result['as_float'])} AS 32-BIT FLOAT (PEAKS OVER 0 DBFS)"), False))
    if result["quieter"]:
        items.append((t(f"{len(result['quieter'])} ETWAS LEISER (SPITZEN ÜBER 0 DBFS)",
                        f"{len(result['quieter'])} A LITTLE QUIETER (PEAKS OVER 0 DBFS)"), False))
    if rl.pylibrb is None:
        items.append((t("OHNE RUBBER BAND: AUDIO-CLIPS UND STRETCH BLEIBEN, DER DELUGE STIMMT SIE BEIM SPIELEN",
                        "NO RUBBER BAND: AUDIO CLIPS AND STRETCH STAY, THE DELUGE TUNES THEM AS IT PLAYS"), False))
    if dry_run:
        items.append((t("NUR GELESEN: UMSTIMMEN SCHREIBT DIE NEUE KARTE", "ONLY READ: RETUNE WRITES THE NEW CARD"),
                      False))
    else:
        items += [(t("NEUE KARTE: ", "NEW CARD: ") + out, False),
                  (t("AUF EINE LEERE SD-KARTE KOPIEREN", "COPY IT ONTO AN EMPTY SD CARD"), False),
                  (t("MASTER TUNE AM DELUGE: ", "MASTER TUNE ON THE DELUGE: ") + hz(tenths), False)]
    if result["warnings"]:
        items.append((t("WARNUNGEN", "WARNINGS") + f" ({len(result['warnings'])})", True))
        items += [(w, False) for w in result["warnings"]]
    return items


# --------------------------------------------------------------------------------------------------------------------
# Self-test (for the build: converts a demo card, stops and continues, and opens and closes the window)


def wav_bytes(x, rate, bits=16, is_float=False):
    """A WAV of x (frames, channels; full scale 1.0)."""
    import numpy as np
    x = np.asarray(x, np.float64).reshape(len(x), -1)
    ch = x.shape[1]
    if is_float:
        data = x.astype("<f4").tobytes()
    else:
        s = 2 ** (bits - 1)
        y = np.clip(np.round(x * s), -s, s - 1).astype(np.int64).reshape(-1)
        if bits == 24:
            v = (y & 0xFFFFFF).astype(np.uint32)
            data = np.stack([v & 0xFF, (v >> 8) & 0xFF, v >> 16], axis=1).astype(np.uint8).tobytes()
        else:
            data = y.astype("<i2").tobytes()
    ba = ch * bits // 8
    fmt = struct.pack("<HHIIHH", 3 if is_float else 1, ch, rate, rate * ba, ba, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    if len(data) & 1:
        body += b"\0"
    return b"RIFF" + struct.pack("<I", len(body)) + body


def demo_card(root):
    """A small card: a tone and a kick in a song's kit, an audio clip (its length kept), a sample whose peaks go over
    full scale when resampled, a wavetable that stays."""
    import numpy as np
    root = Path(root)

    def sine(freq, rate, seconds, amp=0.5):
        n = int(round(seconds * rate))
        x = amp * np.sin(2 * np.pi * freq * np.arange(n) / rate)
        k = int(0.01 * rate)
        w = 0.5 - 0.5 * np.cos(np.pi * np.arange(k) / k)
        x[:k] *= w
        x[-k:] *= w[::-1]
        return x

    loud = sine(440, 44100, 0.5, 0.3)
    s = 11025
    loud[s:s + 48] = np.sqrt(2) * np.sin(np.pi / 2 * np.arange(48) + np.pi / 4)  # Full scale, +3 dB between samples
    files = {
        "SAMPLES/TONE.WAV": wav_bytes(sine(440, 44100, 1.0), 44100),
        "SAMPLES/DRUMS/KICK.WAV": wav_bytes(np.stack([sine(600, 48000, 0.5), sine(750, 48000, 0.5, 0.4)], 1), 48000,
                                            24),
        "SAMPLES/LOUD.WAV": wav_bytes(np.clip(loud, -1, 1), 44100),
        "SAMPLES/CLIP.WAV": wav_bytes(sine(450, 44100, 1.0), 44100),
        "SAMPLES/WT/TABLE.WAV": wav_bytes(np.sin(2 * np.pi * np.arange(8192) / 2048) * 0.3, 44100),
        "SONGS/DEMO.XML": (
            '<?xml version="1.0" encoding="UTF-8"?>\n<song firmwareVersion="c1.2.1">\n\t<instruments>\n'
            '\t\t<kit presetName="KIT" presetFolder="KITS">\n\t\t\t<soundSources>\n'
            '\t\t\t\t<sound name="KICK" mode="subtractive">\n'
            '\t\t\t\t\t<osc1 type="sample" loopMode="1" timeStretchEnable="0" fileName="SAMPLES/DRUMS/KICK.WAV">\n'
            '\t\t\t\t\t\t<zone startSamplePos="2400" endSamplePos="24000" />\n\t\t\t\t\t</osc1>\n'
            '\t\t\t\t</sound>\n\t\t\t\t<sound name="TONE" mode="subtractive">\n'
            '\t\t\t\t\t<osc1 type="sample" loopMode="1" timeStretchEnable="0" fileName="SAMPLES/TONE.WAV">\n'
            '\t\t\t\t\t\t<zone startSamplePos="0" endSamplePos="44100" />\n\t\t\t\t\t</osc1>\n'
            '\t\t\t\t</sound>\n\t\t\t</soundSources>\n\t\t</kit>\n'
            '\t\t<audioTrack name="LOOP" />\n\t</instruments>\n\t<sessionClips>\n'
            '\t\t<audioClip trackName="LOOP" filePath="SAMPLES/CLIP.WAV" startSamplePos="0" endSamplePos="44100" '
            'pitchSpeedIndependent="1" length="384" />\n\t</sessionClips>\n</song>\n').encode(),
        "SETTINGS/NOTES.TXT": b"not audio\n"}
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


def run_args(card, out, tenths=START_TENTHS, rate=44100, no_float=False, dry_run=False, resume=False, jobs=None):
    """retune_library's options, as its command line would have them, checked."""
    argv = ["--card", str(card), "--tuning", f"{tenths / 10:.1f}", "--rate", str(rate), "--quiet"]
    argv += ["--dry-run"] if dry_run else ["--out", str(out)]
    argv += (["--no-float"] if no_float else []) + (["--resume"] if resume else [])
    argv += ["--jobs", str(jobs)] if jobs else []
    args = rl.parser().parse_args(argv)
    error = rl.check_args(args)
    if error:
        raise ValueError(error)
    return args


def card_files(folder, skip=(REPORT, "RETUNE_REPORT.json")):
    """{path: bytes} of every file in a folder (the reports left out: they name the folder and the time)."""
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*"))
            if p.is_file() and p.name not in skip}


def settle(root, app, seconds=60.0):
    """The window's work done, its threads too (the self-test and the tests)."""
    end = time.monotonic() + seconds
    while app.working and time.monotonic() < end:
        root.update()
        time.sleep(0.01)
    root.update()
    assert not app.working, "the window's work didn't end"


def selftest(out):
    global LANG
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    lines, ok = [f"version: v{VERSION}"], True
    LANG = "de"

    def step(name, fn):
        nonlocal ok
        try:
            fn()
            lines.append(f"{name}: ok")
        except Exception as e:  # The self-test reports everything
            ok = False
            lines.append(f"{name}: FAILED {type(e).__name__}: {e}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        card = demo_card(tmp / "card")
        said = []

        def soxr():
            import numpy as np
            assert rl.soxr is not None, "soxr missing"
            y = rl.soxr.resample(np.sin(np.arange(4410) / 7.0), 44100, 43200, quality="VHQ")
            assert abs(len(y) - 4320) <= 1, len(y)

        def rubberband():
            assert rl.pylibrb is not None, "pylibrb (Rubber Band) missing"
            assert hasattr(rl.pylibrb, "RubberBandStretcher")

        def read():
            result = rl.convert(run_args(card, None, dry_run=True), echo=said.append)
            assert result["counts"] == {"converted": 4, "left as it is": 1}, result["counts"]
            assert result["report"] is None and not (card.parent / "new").exists()

        def convert():
            said.clear()
            result = rl.convert(run_args(card, tmp / "new", jobs=2), echo=said.append)
            new, info = tmp / "new", {}
            for rel in ("SAMPLES/TONE.WAV", "SAMPLES/DRUMS/KICK.WAV", "SAMPLES/LOUD.WAV", "SAMPLES/CLIP.WAV"):
                with open(new / rel, "rb") as fh:
                    info[rel] = rl.read_audio_info(fh)
            assert all(i.mtun == 4320 for i in info.values()), {r: i.mtun for r, i in info.items()}
            assert info["SAMPLES/TONE.WAV"].frames == 44917, info["SAMPLES/TONE.WAV"].frames  # 44100 x 440/432
            assert info["SAMPLES/DRUMS/KICK.WAV"].frames == 22458 and info["SAMPLES/DRUMS/KICK.WAV"].rate == 44100
            assert info["SAMPLES/LOUD.WAV"].float and info["SAMPLES/LOUD.WAV"].bits == 32  # Peaks over full scale
            assert info["SAMPLES/CLIP.WAV"].frames == 44100  # An audio clip keeps its length (Rubber Band)
            song = (new / "SONGS/DEMO.XML").read_text(encoding="utf-8")
            assert 'startSamplePos="2246" endSamplePos="22458"' in song and 'endSamplePos="44917"' in song, song
            assert (new / "SAMPLES/WT/TABLE.WAV").read_bytes() == (card / "SAMPLES/WT/TABLE.WAV").read_bytes()
            assert (new / REPORT).is_file() and not (new / rl.PROGRESS).exists()
            assert result["as_float"] == ["SAMPLES/LOUD.WAV"], result["as_float"]

        def processes():  # The frozen program starts itself as the worker processes
            assert "converting 4 files with 2 process(es) ..." in said, said

        def resume():
            count = []

            def stop():
                return len(count) >= 1

            try:
                rl.convert(run_args(card, tmp / "part", jobs=1), echo=lambda s: None,
                           step=lambda done, total, what: count.append(done) if what == "convert" and done else None,
                           stop=stop)
                raise AssertionError("it didn't stop")
            except rl.Stopped:
                pass
            assert (tmp / "part" / rl.PROGRESS).exists() and not (tmp / "part" / "SONGS").exists()
            rl.convert(run_args(card, tmp / "part", resume=True, jobs=1), echo=lambda s: None)
            assert card_files(tmp / "part") == card_files(tmp / "new"), "resumed, the new card isn't the same"

        def window():  # Its buttons pressed: read, then retuned into a folder
            import tkinter as tk
            root = tk.Tk()
            try:
                app = App(root, None, card=str(card), out=str(tmp / "window"), lang="de", jobs=2)
                app.press("read")
                app.press("start")
                settle(root, app)
                assert app.list and app.list["title"] == "432,0 HZ: 5 AUDIODATEIEN", app.list
                app.press("retune")
                app.press("start")
                settle(root, app)
                assert app.list["title"] == "NEUE KARTE 432,0 HZ", app.list
                assert card_files(tmp / "window") == card_files(tmp / "new")
            finally:
                root.destroy()

        def english():
            global LANG
            import tkinter as tk
            root = tk.Tk()
            try:
                app = App(root, None, card=str(card), lang="de", jobs=1)
                app.set_lang("en")
                app.press("read")
                app.press("start")
                settle(root, app)
                assert app.list["title"] == "432.0 HZ: 5 AUDIO FILES", app.list
                assert ("4 TO RETUNE", False) in app.list["items"], app.list["items"]
            finally:
                root.destroy()
                LANG = "de"

        for name, fn in (("soxr", soxr), ("rubberband", rubberband), ("read", read), ("convert", convert),
                         ("processes", processes), ("resume", resume), ("window", window), ("english", english)):
            step(name, fn)
    (out / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ok


# --------------------------------------------------------------------------------------------------------------------
# The window: DelugeRec's look (deluge_rec.py), as DelugeBaseline's. A panel in the Deluge's proportions, the OLED with
# its pixel font, pads (the progress, then the files by what happened to them), round buttons with their LEDs in boxes,
# boxes to tick, the gold knob for the tuning, a language switch (German, English).

# The window's icon, 64 x 64: DelugeRec's, the Deluge's rain of squares as a level meter, with a wave in the corner at
# the top right instead of its dot (deluge_rec_icon.py --tuner draws it, and deluge_tuner.ico for the .exe)
ICON_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAC1klEQVR42u2bwWsTQRTG909ouiakxjYYk5ik2W5oKgpND4InvfUoCMGD"
            "4EXBo6DgKeChp54qgngQhUJBUBAsSHNooSgohRyUggept4DXHp7zlp3p7BpJk+wks7sv8B0y2V3e99uZbyYwYxin+FQqtQZTi6nNdMQE"
            "murIrRFrbRijfthDmkwdjQ33E9beHMZ4nmlXfli5bEM+X4Js9gLMzp7XUlgb1oi1+kCgl7wxQHfv8puLxSpkMnOQSJgwNTUdCmGtWDPW"
            "LkHo9h0WrvljfhNSDZPxXiDQgwTh+L8Q3G7f5d09nc6E1rhf6EUaFt2ew0Ee81EyL0OQM6FX2otuHzXzXL7h0JQBdHjghXnMnyYTpGDs"
            "yMHnNGJyRtU8F3qUekHDcFdNTkhE+e3LvUAKxJbhLh2dBYS2RZtJyFxfhWzzLqQWL4/8PPTqAmgbfG2Pq6hBHgKW5VHQ13OZuSJYm9tw"
            "6eC3o6WvvyB3/+FIANAr/+9gDJv+4wCAb95680GYlzV3604gs4HWAHIPHgnD9f1DKK+/9HxPVmvRBZCy605354YxA7C9svFatM2/2PLc"
            "Mz1zDuy3bVj68lNcrwyAaiDYxbnR0toz0X6mUIb63veToXDztvit8PipaL/YWg83AJMF1fzzTUemL6BxNhBDgcFIliw4e+2Gp8ekl6+G"
            "G0A/VV+9F2ZrHz87mcC/F56sqc+ASQPAt764c/DP7LCw9QkSqZngAfz5tuBRvwJVX8+D0n63exKKOFzYukHJLKAjAL5WSF9ZcWAonQZ1"
            "BTC2dUDsAUQJCAEgAAQg+IXQOEOMABAAAqAfAL/sH/c8GvT3Ua8nAASAAKgFEGRB4wZCAAgAAQg+BIM2oBIIASAABGDyCyHVoUoACAAB"
            "mCwAnYEQAALgBTDUNrkwA/Bvk9N+o2TQ8m+UjP1W2Xhvlo79dnk6MEFHZujQFB2bo4OTdHSWDk/H9vj8X9h9pLKP6Q0cAAAAAElFTkSu"
            "QmCC")

# DelugeRec's 5 x 7 pixel font (7 rows of 5 pixels), with the German letters and a few signs more
FONT = {
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11100 10010 10001 10001 10001 10010 11100",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 10001 01010 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    " ": "00000 00000 00000 00000 00000 00000 00000", ".": "00000 00000 00000 00000 00000 01100 01100",
    ":": "00000 01100 01100 00000 01100 01100 00000", "-": "00000 00000 00000 11111 00000 00000 00000",
    "_": "00000 00000 00000 00000 00000 00000 11111", "/": "00000 00001 00010 00100 01000 10000 00000",
    "%": "11000 11001 00010 00100 01000 10011 00011", "+": "00000 00100 00100 11111 00100 00100 00000",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    "!": "00100 00100 00100 00100 00100 00000 00100", "?": "01110 10001 00001 00010 00100 00000 00100",
    ",": "00000 00000 00000 00000 01100 00100 01000", "'": "01100 00100 01000 00000 00000 00000 00000",
    ">": "01000 00100 00010 00001 00010 00100 01000", "<": "00010 00100 01000 10000 01000 00100 00010",
    "=": "00000 00000 11111 00000 11111 00000 00000", "*": "00000 00100 10101 01110 10101 00100 00000",
    "Ä": "01010 00000 01110 10001 11111 10001 10001", "Ö": "01010 00000 01110 10001 10001 10001 01110",
    "Ü": "01010 00000 10001 10001 10001 10001 01110", "«": "00000 00101 01010 10100 01010 00101 00000",
    "»": "00000 10100 01010 00101 01010 10100 00000", "#": "01010 01010 11111 01010 11111 01010 01010",
    "[": "01110 01000 01000 01000 01000 01000 01110", "]": "01110 00010 00010 00010 00010 00010 01110",
    "&": "01100 10010 10100 01000 10101 10010 01101", ";": "00000 01100 01100 00000 01100 00100 01000",
    '"': "01010 01010 00000 00000 00000 00000 00000", "^": "00100 01010 10001 00000 00000 00000 00000",
    "|": "00100 00100 00100 00100 00100 00100 00100", "\\": "00000 10000 01000 00100 00010 00001 00000",
    "~": "00000 00000 01000 10101 00010 00000 00000",
}
_glyphs = {}


def glyph(ch):
    """A character's 7 rows of 5 pixels, 1 where it is lit."""
    g = _glyphs.get(ch)
    if g is None:
        g = _glyphs[ch] = tuple(tuple(int(b) for b in row) for row in FONT[ch].split())
    return g


def oled_text(s):
    """Text as the OLED can show it: upper case, a letter it lacks without its accent, else '?'."""
    out = []
    for ch in str(s).upper():
        if ch not in FONT:
            ch = unicodedata.normalize("NFKD", ch)[:1].upper()
            ch = ch if ch in FONT else "?"
        out.append(ch)
    return "".join(out)


class Oled:
    """The Deluge's OLED as DelugeRec draws it: w x h pixels, each scale x scale on the screen (3 or more), with a
    visible pixel grid and scanlines."""

    def __init__(self, scale, w=128, h=48):
        self.scale, self.W, self.H = scale, w, h
        s = scale
        self.fb = [bytearray(w) for _ in range(h)]

        def pixel(colour, shade):  # One pixel of a line on the screen: its last column darker (the grid)
            return b"".join(bytes(int(c * shade * (0.8 if i == s - 1 else 1.0)) for c in colour) for i in range(s))
        # (off, on) for the lines of a pixel, and for its last line, darker (the scanline)
        self.line, self.scan = ((pixel(OLED_OFF, f), pixel(OLED_ON, f)) for f in (1.0, 0.45))
        self.header = b"P6 %d %d 255\n" % (self.W * s, self.H * s)

    def clear(self):
        for row in self.fb:
            row[:] = bytes(self.W)

    @staticmethod
    def width(s, size=1):
        return len(oled_text(s)) * 6 * size - size

    def text(self, x, y, s, size=1, invert=False):
        for ch in oled_text(s):
            for r, bits in enumerate(glyph(ch)):
                for yy in range(y + r * size, y + (r + 1) * size):
                    if not 0 <= yy < self.H:
                        continue
                    row = self.fb[yy]
                    for col, bit in enumerate(bits):
                        if bit or invert:
                            for xx in range(max(x + col * size, 0), min(x + (col + 1) * size, self.W)):
                                row[xx] = 1 - bit if invert else 1
            x += 6 * size
        return x

    def rect(self, x, y, w, h, on=True):
        x0, x1 = max(x, 0), min(x + w, self.W)
        if x1 > x0:
            fill = (b"\x01" if on else b"\x00") * (x1 - x0)
            for yy in range(max(y, 0), min(y + h, self.H)):
                self.fb[yy][x0:x1] = fill

    def frame(self):
        return b"".join(self.fb)

    def ppm(self):
        out = [self.header]
        for row in self.fb:
            out += [b"".join([self.line[v] for v in row])] * (self.scale - 1)
            out.append(b"".join([self.scan[v] for v in row]))
        return b"".join(out)


def fit(text, width):
    """A path in at most width characters: its end, where it says the most."""
    return text if len(text) <= width else ".." + text[-(width - 2):]


def settings_path():
    base = os.environ.get("APPDATA") if sys.platform.startswith("win") else None
    return (Path(base) / "DelugeTuner" if base else Path.home() / ".config" / "deluge_tuner") / "settings.json"


def system_language():
    """de if the computer speaks German, else en: the window's language until one is chosen in it. Windows: the
    language of its user interface; macOS and Linux: the locale (LC_ALL, LC_MESSAGES, LANG, the first one set, else
    Python's locale). English where it can't be read."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            return "de" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x07 else "en"  # LANG_GERMAN
        code = next((os.environ[k] for k in ("LC_ALL", "LC_MESSAGES", "LANG") if os.environ.get(k)), None)
        if code is None:
            import locale
            code = locale.getlocale()[0] or ""
        return "de" if code.lower().startswith("de") else "en"
    except Exception:
        return "en"


def find_card():
    """A Deluge card among the drives (Windows): the first with a SAMPLES or SONGS folder."""
    if not sys.platform.startswith("win"):
        return ""
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        if os.path.isdir(f"{letter}:\\SAMPLES") or os.path.isdir(f"{letter}:\\SONGS"):
            return f"{letter}:\\"
    return ""


class App:
    """The window: a card, a folder for the new card, the tuning, and a mode (READ: only read, RETUNE: write the new
    card), then START."""
    W, H = 900, 614  # The panel at scale 1, in the Deluge's (and DelugeRec's) proportions: 305 x 208 mm
    ROWS, PADS = 5, 32  # Lines of a list on the display; pads (two rows of 16)
    DISPLAY = (170, 64)  # The display's pixels: 28 characters on 5 lines and a bottom line, as DelugeBaseline's
    COLOURS = {"read": WHITE, "retune": CYAN, "start": GREEN, "report": WHITE, "card": GREEN, "out": BLUE}
    # The computer's keys, by language (shown small under each button)
    LETTERS = {"de": {"read": "L", "retune": "U", "card": "K", "out": "Z", "report": "B"},
               "en": {"read": "R", "retune": "U", "card": "C", "out": "O", "report": "T"}}
    PHASES = {"xml": ("LESE SONGS", "READING SONGS"), "audio": ("LESE SAMPLES", "READING SAMPLES"),
              "convert": ("STIMME UM", "RETUNING"), "copy": ("KOPIERE", "COPYING")}

    def __init__(self, root, settings, card=None, out=None, z=1.0, lang=None, jobs=None):
        global LANG
        import tkinter as tk
        from tkinter import filedialog, simpledialog
        self.tk, self.filedialog, self.simpledialog = tk, filedialog, simpledialog
        self.root, self.settings = root, settings
        saved = {}
        if settings:
            try:
                saved = json.loads(Path(settings).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
        self.card = card or saved.get("card") or find_card()
        self.out = out or saved.get("out", "")
        # The language: as the command line says, else as chosen in the window before, else the computer's
        self.lang = LANG = lang or (saved.get("lang") if saved.get("lang") in ("de", "en") else system_language())
        self.mode = saved.get("mode") if saved.get("mode") in ("read", "retune") else "read"
        tenths = saved.get("tuning", START_TENTHS)
        self.tenths = tenths if isinstance(tenths, int) and rl.is_valid_tuning(tenths) else START_TENTHS
        self.rate = 0 if saved.get("rate") == 0 else 44100
        self.no_float = bool(saved.get("no_float", False))
        runs = saved.get("runs") if isinstance(saved.get("runs"), dict) else {}
        self.runs = {k: v for k, v in runs.items() if isinstance(v, str) and os.path.isdir(k)}  # Started, not done
        self.jobs_n = jobs  # Processes at most (None: retune_library's default, one per core)
        scale = max(3, int(3 * z + 0.5))
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        while scale > 3 and (self.W * scale / 3 > sw * 0.98 or self.H * scale / 3 > sh - 90):
            scale -= 1
        self.oled = Oled(scale, *self.DISPLAY)
        s = scale / 3  # Everything follows the display's size
        self.Z = lambda v: int(round(v * s))  # noqa: E731
        self.boot_until = time.monotonic() + 1.4
        self.view = "home"  # home, list
        self.list = None  # {"title", "items": [(text, heading)], "sel", "since"}
        self.busy = self.running = None  # What runs (its title) and on which button, None when nothing does
        self.working = 0  # Threads not done yet, the quiet ones too
        self.phase, self.done, self.total, self.workers = "", 0, 0, 0
        self.stop = threading.Event()
        self.closing = False
        self.message, self.message_since, self.message_until = "", 0.0, 0.0
        self.stats = None  # card_stats() of the card
        self.result = None  # The last run: (result, dry_run)
        self.report_text, self.report_path = "", None
        self.dest = None  # ((card, out, tenths, rate, no_float, runs), (folder, resume) or the reason there is none)
        self.jobs = queue.Queue()
        self.drag = None
        self.bound = []  # The keys bound for the language set
        self.after_id = None  # The next tick

        root.title(f"DELUGE TUNER v{VERSION}")
        self.icon = tk.PhotoImage(data=ICON_PNG, master=root)
        root.iconphoto(True, self.icon)
        root.configure(bg=PANEL)
        root.resizable(False, False)
        self.c = tk.Canvas(root, width=self.Z(self.W), height=self.Z(self.H), bg=PANEL, highlightthickness=0)
        self.c.pack()
        self.img = tk.PhotoImage(width=self.oled.W * scale, height=self.oled.H * scale, master=root)
        self.build()
        self.bind_keys()
        for k in ("<Return>", "<KP_Enter>"):
            root.bind(k, lambda e: self.press("start"))
        root.bind("<Escape>", lambda e: self.escape())
        for k, step in (("<Up>", -1), ("<Down>", 1), ("<Prior>", -self.ROWS), ("<Next>", self.ROWS)):
            root.bind(k, lambda e, s=step: self.move(s))
        root.bind("<Home>", lambda e: self.move(-10 ** 6))
        root.bind("<End>", lambda e: self.move(10 ** 6))
        for k in ("<plus>", "<KP_Add>"):
            root.bind(k, lambda e: self.turn_gold(1))
        for k in ("<minus>", "<KP_Subtract>"):
            root.bind(k, lambda e: self.turn_gold(-1))
        root.bind("<Right>", lambda e: self.turn_gold(1, fine=True))
        root.bind("<Left>", lambda e: self.turn_gold(-1, fine=True))
        root.bind("<MouseWheel>", lambda e: self.wheel(e, 1 if e.delta > 0 else -1))
        root.bind("<Button-4>", lambda e: self.wheel(e, 1))
        root.bind("<Button-5>", lambda e: self.wheel(e, -1))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.bind("<Destroy>", self.destroyed, add="+")
        if self.card:
            self.load_card()
        self.tick()

    # --- the panel, drawn anew when the language changes

    def build(self):
        c, Z = self.c, self.Z
        c.delete("all")
        self.shown = {}  # What the LEDs, boxes, pads and OLED show: only a change is drawn
        self.leds, self.checks = {}, {}
        W, H = Z(self.W), Z(self.H)
        c.create_rectangle(Z(8), Z(8), W - Z(8), H - Z(8), fill=PLATE, outline=EDGE, width=Z(1))
        self.label(Z(26), Z(18), "DELUGE", Z(3))
        self.label(Z(26) + self.label_width("DELUGE", Z(3)) + Z(12), Z(25), "TUNER", Z(2))
        # The OLED in its bezel: a click chooses a line
        self.ox, self.oy = ox, oy = Z(40), Z(58)
        ow, oh = self.oled.W * self.oled.scale, self.oled.H * self.oled.scale
        c.create_rectangle(ox - Z(6), oy - Z(6), ox + ow + Z(6), oy + oh + Z(6), fill=BEZEL, outline=EDGE)
        screen = c.create_image(ox, oy, image=self.img, anchor="nw")
        c.tag_bind(screen, "<Button-1>", self.click_oled)
        # Pads: the progress, then the audio files by what happened to them
        self.pads = []
        for i in range(self.PADS):
            x, y = ox + (i % 16) * Z(32), Z(270) + (i // 16) * Z(28)
            pad = c.create_rectangle(x, y, x + Z(28), y + Z(24), fill=self.dim(WHITE, 0.03), outline="#0a0a0c",
                                     width=Z(1))
            c.tag_bind(pad, "<Button-1>", lambda e, i=i: self.hover_pad(i, 3.0))
            c.tag_bind(pad, "<Enter>", lambda e, i=i: self.hover_pad(i))
            self.pads.append(pad)
        # Below: the mode, go, the report; then the card and the folder for the new one
        self.button_row([(t("MODUS", "MODE"), [("read", t("LESEN", "READ")), ("retune", t("UMSTIMMEN", "RETUNE"))]),
                         ("", [("start", "START")]), ("", [("report", t("BERICHT", "REPORT"))])],
                        Z(26), Z(566), Z(338), Z(468), Z(62))
        for key, x0, x1, text in (("card", Z(26), Z(292), t("KARTE", "CARD")),
                                  ("out", Z(300), Z(566), t("ZIEL", "OUTPUT"))):
            self.box(x0, Z(484), x1, Z(596))
            self.button(key, text, x0 + Z(46), Z(522))
        self.card_area, self.out_area = (Z(26) + Z(96), Z(484)), (Z(300) + Z(96), Z(484))
        # On the right: the tuning, the samples' settings, the language
        rx0, rx1 = Z(578), Z(874)
        self.box(rx0, Z(52), rx1, Z(236))
        self.label(rx0 + Z(14), Z(62), t("STIMMUNG", "TUNING"), Z(2), SMALL)
        gx, gy = self.gold_knob = (rx0 + Z(48), Z(128))
        knob = [c.create_oval(gx - Z(26), gy - Z(26), gx + Z(26), gy + Z(26), fill="#8a6a28", outline="#4e3b14",
                              width=Z(2)),
                c.create_oval(gx - Z(20), gy - Z(20), gx + Z(20), gy + Z(20), fill="#d9b35a", outline="#f0d58c",
                              width=Z(1))]
        self.gold_line = c.create_line(gx, gy, gx, gy - Z(18), fill="#2a1e08", width=Z(3), capstyle="round")
        knob.append(self.gold_line)
        self.tuning_area = (gx + Z(40), gy - Z(22))
        c.create_rectangle(gx + Z(34), gy - Z(28), rx1 - Z(8), gy + Z(4), fill=BOX, outline="", tags="type")
        c.tag_bind("type", "<Button-1>", lambda e: self.type_tuning())
        c.tag_bind("type", "<Enter>", lambda e: c.configure(cursor="xterm"))
        c.tag_bind("type", "<Leave>", lambda e: c.configure(cursor=""))
        self.cents_area = (gx + Z(40), gy + Z(10))
        for i, line in enumerate(t(("ZIEHEN, RAD, + UND -: 1 HZ", "MIT SHIFT, PFEIL LINKS/RECHTS: 0,1",
                                    "KLICK AUF DIE ZAHL: EINTIPPEN", "MASTER TUNE AM DELUGE: GLEICH"),
                                   ("DRAG, WHEEL, + AND -: 1 HZ", "WITH SHIFT, ARROWS LEFT/RIGHT: 0.1",
                                    "CLICK THE NUMBER: TYPE IT", "MASTER TUNE ON THE DELUGE: SAME"))):
            self.label(rx0 + Z(16), Z(172) + i * Z(13), line, Z(1), SMALL)
        for item in knob:
            c.tag_bind(item, "<Button-1>", self.grab)
            c.tag_bind(item, "<B1-Motion>", self.drag_knob)
            c.tag_bind(item, "<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
            c.tag_bind(item, "<Enter>", lambda e: c.configure(cursor="sb_v_double_arrow"))
            c.tag_bind(item, "<Leave>", lambda e: c.configure(cursor=""))
        self.update_knob()
        self.box(rx0, Z(246), rx1, Z(468))
        self.label(rx0 + Z(14), Z(256), t("SAMPLERATE", "SAMPLE RATE"), Z(2), SMALL)
        self.checkbox("rate", t("44,1 KHZ (DELUGE)", "44.1 KHZ (DELUGE)"), rx0 + Z(16), Z(290), CYAN)
        self.checkbox("keep", t("WIE JEDE DATEI", "EACH FILE'S OWN"), rx0 + Z(16), Z(320), CYAN)
        self.label(rx0 + Z(14), Z(348), t("SPITZEN ÜBER 0 DBFS", "PEAKS OVER 0 DBFS"), Z(2), SMALL)
        self.checkbox("float", "32-BIT FLOAT", rx0 + Z(16), Z(382), AMBER)
        self.checkbox("quieter", t("ETWAS LEISER", "A LITTLE QUIETER"), rx0 + Z(16), Z(412), AMBER)
        self.label(rx0 + Z(44), Z(436), t("NIE ÜBERSTEUERT", "NEVER CLIPPED"), Z(1), SMALL)
        self.box(rx0, Z(484), rx1, Z(596))
        self.label(rx0 + Z(14), Z(494), "SPRACHE / LANGUAGE", Z(2), SMALL)
        # The language switch: a click on DE or EN chooses it, a click on the switch flips it
        mid, y = (rx0 + rx1) // 2, Z(548)
        self.label(mid - Z(44) - self.label_width("DE", Z(3)), y - Z(10), "DE", Z(3), tag=("lang", "lang_de"))
        self.label(mid + Z(44), y - Z(10), "EN", Z(3), tag=("lang", "lang_en"))
        c.create_rectangle(mid - Z(32), y - Z(13), mid + Z(32), y + Z(13), fill=BEZEL, outline=EDGE, width=Z(1),
                           tags=("lang", "lang_switch"))
        self.lang_knob = c.create_rectangle(0, 0, 0, 0, fill="#c8c8d0", outline="#f0f0f4", width=Z(1),
                                            tags=("lang", "lang_switch"))
        c.tag_bind("lang_de", "<Button-1>", lambda e: self.set_lang("de"))
        c.tag_bind("lang_en", "<Button-1>", lambda e: self.set_lang("en"))
        c.tag_bind("lang_switch", "<Button-1>", lambda e: self.set_lang("en" if self.lang == "de" else "de"))
        c.tag_bind("lang", "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind("lang", "<Leave>", lambda e: c.configure(cursor=""))

    def button_row(self, groups, x0, x1, top, bottom, cy):
        """Boxes of round buttons side by side over x0..x1, each with its title; cy: the buttons' centre from top."""
        Z = self.Z
        sizes = []
        for title, keys in groups:
            slots = [max(self.label_width(text, Z(2)), Z(50)) + Z(16) for _, text in keys]
            sizes.append((max(sum(slots) + Z(24), self.label_width(title, Z(2)) + Z(28) if title else 0), slots))
        gap = (x1 - x0 - sum(w for w, _ in sizes)) / max(1, len(groups) - 1)
        x = x0
        for (title, keys), (w, slots) in zip(groups, sizes):
            self.box(int(x), top, int(x + w), bottom)
            if title:
                self.label(int(x) + Z(14), top + Z(10), title, Z(2), SMALL)
            bx = x + (w - sum(slots)) / 2
            for (key, text), slot in zip(keys, slots):
                self.button(key, text, int(bx + slot / 2), top + cy)
                bx += slot
            x += w + gap

    def button(self, key, text, cx, cy):
        """A round button with its LED, its name under it and its key small below."""
        c, Z = self.c, self.Z
        colour = self.COLOURS[key]
        ring = c.create_oval(cx - Z(19), cy - Z(19), cx + Z(19), cy + Z(19), fill="#26262a", outline="#3c3c42",
                             width=Z(2))
        led = c.create_oval(cx - Z(7), cy - Z(7), cx + Z(7), cy + Z(7), fill=self.dim(colour, 0.22), outline="")
        self.label(cx - self.label_width(text, Z(2)) // 2, cy + Z(28), text, Z(2))
        letter = "ENTER" if key == "start" else self.LETTERS[self.lang][key]
        self.label(cx - self.label_width(letter, Z(1)) // 2, cy + Z(48), letter, Z(1), SMALL)
        for item in (ring, led):
            self.clickable(item, lambda e: self.press(key))
        self.leds[key] = (led, colour)

    def checkbox(self, key, text, x, y, colour):
        """A box to tick, with its name: all of it clickable."""
        c, Z = self.c, self.Z
        tag, s = "check_" + key, Z(18)
        c.create_rectangle(x - Z(6), y - Z(13), x + s + Z(16) + self.label_width(text, Z(2)), y + Z(13), fill=BOX,
                           outline="", tags=tag)
        c.create_rectangle(x, y - s // 2, x + s, y + s // 2, fill=BEZEL, outline="#6a6a74", width=Z(2), tags=tag)
        marks = [c.create_line(x + Z(5), y - s // 2 + Z(5), x + s - Z(5), y + s // 2 - Z(5), fill=colour, width=Z(3),
                               tags=tag),
                 c.create_line(x + Z(5), y + s // 2 - Z(5), x + s - Z(5), y - s // 2 + Z(5), fill=colour, width=Z(3),
                               tags=tag)]
        self.label(x + s + Z(10), y - Z(7), text, Z(2), tag=tag)
        c.tag_bind(tag, "<Button-1>", lambda e: self.tick_box(key))
        c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
        self.checks[key] = marks

    def bind_keys(self):
        for k in self.bound:
            self.root.unbind(k)
        self.bound = []
        for key, letter in self.LETTERS[self.lang].items():
            for k in (letter.lower(), letter.upper()):
                self.root.bind(k, lambda e, key=key: self.press(key))
                self.bound.append(k)
        for k, lang in (("d", "de"), ("D", "de"), ("e", "en"), ("E", "en")):  # Deutsch, English
            self.root.bind(k, lambda e, lang=lang: self.set_lang(lang))
            self.bound.append(k)

    # --- drawing helpers, as in DelugeRec

    def label(self, x, y, s, p, fill=LABEL, tag=None):
        """Lettering in the OLED's pixel font, p screen pixels per font pixel."""
        for ch in oled_text(s):
            for r, bits in enumerate(glyph(ch)):
                for col, bit in enumerate(bits):
                    if bit:
                        self.c.create_rectangle(x + col * p, y + r * p, x + (col + 1) * p, y + (r + 1) * p,
                                                fill=fill, width=0, tags=tag)
            x += 6 * p

    def box(self, x0, y0, x1, y1):
        """The box around controls on the panel."""
        self.c.create_rectangle(x0, y0, x1, y1, fill=BOX, outline=BOX_EDGE, width=self.Z(1))

    def clickable(self, item, action):
        self.c.tag_bind(item, "<Button-1>", action)
        self.c.tag_bind(item, "<Enter>", lambda e: self.c.configure(cursor="hand2"))
        self.c.tag_bind(item, "<Leave>", lambda e: self.c.configure(cursor=""))

    @staticmethod
    def label_width(s, p):
        return len(oled_text(s)) * 6 * p - p

    @staticmethod
    def dim(colour, f=0.14):
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % (int(24 + (r - 24) * f), int(24 + (g - 24) * f), int(26 + (b - 26) * f))

    def update_knob(self):
        gx, gy = self.gold_knob
        a = math.radians(-135 + 270 * (self.tenths - rl.MIN_TENTHS) / (rl.MAX_TENTHS - rl.MIN_TENTHS))
        r = self.Z(18)
        self.c.coords(self.gold_line, gx, gy, gx + r * math.sin(a), gy - r * math.cos(a))

    def say(self, text, seconds=2.0):
        """A message in the OLED's bottom line; a long one stays until it has scrolled through."""
        now = time.monotonic()
        over = max(0, Oled.width(text) - (self.oled.W - 1)) / 30
        self.message, self.message_since = text, now
        self.message_until = now + (max(seconds, over + 3.0) if over else seconds)

    def show(self, item, fill):
        if self.shown.get(item) != fill:
            self.shown[item] = fill
            self.c.itemconfigure(item, fill=fill)

    def redraw(self, tag, text, x, y, p, fill=LABEL):
        """Lettering that changes (the card, the folder, the tuning): drawn again when its text does."""
        if self.shown.get(tag) != text:
            self.shown[tag] = text
            self.c.delete(tag)
            self.label(x, y, text, p, fill, tag)
            return True
        return False

    # --- the gold knob, the wheel, the list

    def grab(self, event):
        self.drag = {"y": event.y}

    def drag_knob(self, event):
        d, step = self.drag, self.Z(12)
        while d and abs(event.y - d["y"]) >= step:
            up = event.y < d["y"]
            d["y"] += -step if up else step
            self.turn_gold(1 if up else -1, fine=bool(event.state & 1))

    def wheel(self, event, step):
        gx, gy = self.gold_knob
        if abs(event.x - gx) <= self.Z(150) and abs(event.y - gy) <= self.Z(34):  # The knob and its number
            self.turn_gold(step, fine=bool(event.state & 1))
        else:
            self.move(-step)

    def turn_gold(self, step, fine=False):
        """The tuning: 0.1 Hz a step (fine), else to the next whole Hz up or down."""
        if self.busy:
            return
        tenths = self.tenths
        for _ in range(abs(step)):
            if fine:
                tenths += 1 if step > 0 else -1
            else:
                tenths = (tenths // 10 + 1) * 10 if step > 0 else (tenths - 1) // 10 * 10
        self.set_tuning(min(rl.MAX_TENTHS, max(rl.MIN_TENTHS, tenths)))

    def set_tuning(self, tenths):
        if tenths != self.tenths:
            self.tenths = tenths
            self.update_knob()
            self.save()
        self.say(t("STIMMUNG ", "TUNING ") + hz(tenths) + ", " + cents(tenths), 1.5)

    def type_tuning(self):
        if self.busy:
            return
        text = self.simpledialog.askstring("DelugeTuner", t("Stimmung in Hz (415,3 bis 466,2):",
                                                            "Tuning in Hz (415.3 to 466.2):"),
                                           initialvalue=num(self.tenths / 10), parent=self.root)
        if text is None:
            return
        try:
            value = float(text.strip().lower().replace("hz", "").replace(",", ".").strip())
            tenths = int(round(value * 10))
            valid = abs(tenths - value * 10) < 1e-6 and rl.is_valid_tuning(tenths)
        except ValueError:
            valid = False
        if valid:
            self.set_tuning(tenths)
        else:
            self.say(t("415,3 BIS 466,2 HZ, IN SCHRITTEN VON 0,1", "415.3 TO 466.2 HZ, IN STEPS OF 0.1"), 3)

    def move(self, step):
        """Through the list on the OLED: wheel, arrow keys."""
        if self.view == "list" and self.list:
            sel = min(len(self.list["items"]) - 1, max(0, self.list["sel"] + step))
            if sel != self.list["sel"]:
                self.list["sel"], self.list["since"] = sel, time.monotonic()

    def click_oled(self, event):
        row = ((event.y - self.oy) // self.oled.scale - 11) // 9
        if self.view == "list" and self.list and 0 <= row < self.ROWS:
            top = self.list_top()
            if top + row < len(self.list["items"]):
                self.list["sel"], self.list["since"] = top + row, time.monotonic()

    def hover_pad(self, i, seconds=1.6):
        if self.busy or not self.result:
            return
        keys = pads_of(self.result[0]["counts"], self.PADS)
        if i < len(keys):
            for key, read, written, _ in CATEGORIES:
                if key == keys[i]:
                    self.say(f"{self.result[0]['counts'][key]} {t(*(read if self.result[1] else written))}", seconds)

    # --- the buttons and boxes

    def press(self, key):
        if self.busy:
            self.say(t("LÄUFT: ESC HÄLT AN", "RUNNING: ESC STOPS"))
            return
        if key in ("read", "retune"):  # What START will do
            self.mode = key
            self.view = "home"
            self.save()
        elif key == "start":
            self.go()
        elif key == "card":
            self.choose_card()
        elif key == "out":
            self.choose_out()
        elif key == "report":
            self.open_report()

    def go(self):
        """START: reads the card and shows what it would do, or writes the new card."""
        card = self.card_or_say()
        if card is None:
            return
        if rl.soxr is None:
            self.say(t("SOXR FEHLT: PIP INSTALL SOXR", "SOXR MISSING: PIP INSTALL SOXR"), 4)
            return
        dry_run = self.mode == "read"
        out, resume = None, False
        if not dry_run:
            if not self.out and not self.choose_out():
                return
            try:
                out, resume = destination(self.out, card, self.tenths, self.rate, self.no_float, self.runs)
            except ValueError as e:
                self.say(str(e), 4)
                return
            self.runs[out] = card  # Started: START continues it if it stops
            self.save()
        self.stop.clear()
        self.result, self.report_text, self.report_path = None, "", None
        options = dict(tenths=self.tenths, rate=self.rate, no_float=self.no_float)
        self.start(t("LESE KARTE", "READING THE CARD"), lambda: self.run(card, out, resume, dry_run, **options),
                   lambda result: self.finished(result, out, dry_run, options["tenths"]), "start")

    def run(self, card, out, resume, dry_run, tenths, rate, no_float):
        """In the worker thread: retune_library's conversion, its progress through the queue."""
        args = run_args(card, out, tenths, rate, no_float, dry_run, resume, self.jobs_n)
        if not dry_run:  # Room for the new card: about the card's size, and a little more
            need = int(card_stats(card)["bytes"] * GROWTH) - (folder_bytes(out) if resume else 0)
            free = free_bytes(out)
            if free < need:
                return {"space": (free, need)}

        def echo(text):
            m = re.match(r"converting \d+ files with (\d+) process", text)
            if m:
                self.jobs.put(("workers", int(m.group(1))))

        def step(done, total, what):
            self.jobs.put(("step", what, done, total))
            if what in ("xml", "audio") and self.stop.is_set():  # Reading the card: nothing written yet
                raise rl.Stopped("stopped while reading the card")

        try:
            return rl.convert(args, echo, step, self.stop.is_set)
        except rl.Stopped as e:
            return {"stopped": str(e)}

    def finished(self, result, out, dry_run, tenths):
        self.dest = None  # The folder has changed: where the next new card goes, found anew
        if "space" in result:
            free, need = result["space"]
            self.show_list(t("ZU WENIG PLATZ", "NOT ENOUGH SPACE"), [
                (t(f"FREI: {size_text(free)}, NÖTIG: ETWA {size_text(need)}",
                   f"FREE: {size_text(free)}, NEEDED: ABOUT {size_text(need)}"), False),
                (t("ZIEL: EINEN ORDNER MIT MEHR PLATZ WÄHLEN", "OUTPUT: CHOOSE A FOLDER WITH MORE SPACE"), False)])
            self.say(t("NICHTS GESCHRIEBEN", "NOTHING WRITTEN"), 3)
            self.runs.pop(out, None)
            self.save()
            return
        if "stopped" in result:
            items = [(t("NICHTS GESCHRIEBEN", "NOTHING WRITTEN"), False)] if dry_run else [
                (t("DIE FERTIGEN DATEIEN BLEIBEN", "THE FILES FINISHED STAY"), False),
                (t("START MIT GLEICHER KARTE, GLEICHEM ZIEL UND GLEICHER STIMMUNG: WEITER",
                   "START WITH THE SAME CARD, OUTPUT AND TUNING: CONTINUES"), False), (out, False)]
            self.show_list(t("ANGEHALTEN", "STOPPED"), items)
            self.say(t("ANGEHALTEN", "STOPPED"), 3)
            return
        if not dry_run:
            self.runs.pop(out, None)
            self.save()
        self.result = (result, dry_run)
        self.report_text, self.report_path = "\n".join(result["lines"]) + "\n", result["report"]
        n = sum(result["counts"].values())
        title = (f"{hz(tenths)}: {n} " + t("AUDIODATEIEN", "AUDIO FILES") if dry_run else
                 t("NEUE KARTE ", "NEW CARD ") + hz(tenths))
        self.show_list(title, result_items(result, dry_run, tenths, out))
        self.say(t("NUR GELESEN, NICHTS GESCHRIEBEN", "ONLY READ, NOTHING WRITTEN") if dry_run else
                 t("FERTIG", "DONE"), 3)

    def escape(self):
        if self.busy:
            if not self.stop.is_set():
                self.stop.set()
                self.say(t("HÄLT AN, SOBALD DIE LAUFENDEN DATEIEN FERTIG SIND",
                           "STOPS AS SOON AS THE FILES RUNNING ARE DONE"), 4)
        else:
            self.view = "home"

    def tick_box(self, key):
        if self.busy:
            return
        if key in ("rate", "keep"):
            self.rate = 44100 if key == "rate" else 0
            self.say(t("44,1 KHZ: DER DELUGE SPIELT SIE OHNE UMRECHNEN", "44.1 KHZ: THE DELUGE PLAYS THEM AS THEY ARE")
                     if self.rate else t("JEDE DATEI BEHÄLT IHRE SAMPLERATE", "EACH FILE KEEPS ITS SAMPLE RATE"), 3)
        else:
            self.no_float = key == "quieter"
            self.say(t("SPITZEN: SO VIEL LEISER, DASS NICHTS ÜBERSTEUERT", "PEAKS: JUST AS MUCH QUIETER AS NEEDED")
                     if self.no_float else t("SPITZEN: DATEI ALS 32-BIT FLOAT", "PEAKS: THE FILE AS 32-BIT FLOAT"), 3)
        self.save()

    def set_lang(self, lang):
        """The language switch: the panel and every text anew. What was shown goes (it was in the other language)."""
        global LANG
        if self.busy or lang == self.lang:
            return
        self.lang = LANG = lang
        self.list, self.result, self.view = None, None, "home"
        self.report_text, self.report_path = "", None
        self.dest = None
        self.build()
        self.bind_keys()
        self.save()
        self.say(t("DEUTSCH", "ENGLISH"))

    def choose_card(self):
        path = self.filedialog.askdirectory(title=t("Deluge-Karte (die SD-Karte oder eine Kopie davon)",
                                                    "Deluge card (the SD card or a copy of it)"),
                                            initialdir=self.card or None)
        if not path:
            return
        try:
            root = card_root(path)
        except ValueError:
            self.say(t("KEINE DELUGE-KARTE: OHNE SAMPLES UND SONGS", "NO DELUGE CARD: NO SAMPLES, NO SONGS"), 3)
            return
        self.card = root
        self.list, self.result, self.view = None, None, "home"
        self.save()
        self.load_card()
        self.say(t("KARTE ", "CARD ") + self.card, 3)

    def choose_out(self):
        path = self.filedialog.askdirectory(title=t("Ordner für die neue Karte (auf dem Computer)",
                                                    "Folder for the new card (on the computer)"),
                                            initialdir=self.out or None)
        if not path:
            return False
        self.out = os.path.abspath(path)
        self.save()
        self.say(t("ZIEL ", "OUTPUT ") + self.out, 3)
        return True

    def open_report(self):
        if not self.report_text:
            self.say(t("NOCH KEIN BERICHT: ERST START", "NO REPORT YET: START FIRST"), 3)
            return
        path = self.report_path
        if not path:  # Only read: the report to a file of its own
            path = (Path(self.settings).parent if self.settings else Path(tempfile.gettempdir())) / t(
                "Bericht.txt", "Report.txt")
        try:
            if not self.report_path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(self.report_text, encoding="utf-8-sig")
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # noqa: S606 (the report, in the computer's text viewer)
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
            self.say(t("BERICHT OFFEN", "REPORT OPEN"))
        except OSError:
            self.say(t("BERICHT: ", "REPORT: ") + str(path), 4)

    def card_or_say(self):
        """The card's root, or None with the reason on the OLED."""
        if not self.card:
            self.say(t("KEINE KARTE: KARTE WÄHLEN", "NO CARD: CHOOSE A CARD"), 3)
            return None
        try:
            return card_root(self.card)
        except ValueError:
            self.say(t("KARTE NICHT DA: ", "CARD NOT THERE: ") + self.card, 3)
            return None

    def quit(self):
        """Closing the window: a run stops first (its files kept), a second close doesn't wait."""
        if self.busy and not self.closing:
            self.closing = True
            self.stop.set()
            self.say(t("HÄLT AN, DANN SCHLIESST ES", "STOPPING, THEN IT CLOSES"), 10)
            return
        self.root.destroy()

    def destroyed(self, event):
        """The window gone: no tick after it (its command would be gone)."""
        if event.widget is self.root and self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    # --- work in a thread, its results through a queue

    def start(self, title, work, done, key, quiet=False):
        if not quiet:
            self.busy, self.running = title, key
            self.phase, self.done, self.total, self.workers = "", 0, 0, 0
        self.working += 1

        def worker():
            try:
                self.jobs.put(("done", done, work(), None, quiet))
            except BaseException as e:  # Shown on the OLED (SystemExit too: the window must not hang)
                self.jobs.put(("done", done, None, e, quiet))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while True:
            try:
                item = self.jobs.get_nowait()
            except queue.Empty:
                return
            if item[0] == "step":
                self.phase, self.done, self.total = item[1:]
                continue
            if item[0] == "workers":
                self.workers = item[1]
                continue
            _, done, result, error, quiet = item
            self.working -= 1
            if not quiet:
                self.busy = self.running = None
            if error is None:
                done(result)
            elif not quiet:
                self.show_list(t("FEHLER", "ERROR"), [(f"{type(error).__name__}: {error}", False)])
                self.say(t("FEHLER: SIEHE ANZEIGE", "ERROR: SEE THE DISPLAY"), 4)

    def load_card(self):
        """The card's files counted, quietly."""
        card = self.card
        self.stats = None
        self.start("", lambda: card_stats(card), lambda stats: setattr(self, "stats", stats)
                   if card == self.card else None, None, quiet=True)

    def show_list(self, title, items):
        self.list = {"title": title, "items": items or [("-", False)], "sel": 0, "since": time.monotonic()}
        self.view = "list"

    def save(self):
        self.dest = None  # Where the new card goes: found anew
        if not self.settings:
            return
        try:
            Path(self.settings).parent.mkdir(parents=True, exist_ok=True)
            Path(self.settings).write_text(json.dumps({
                "card": self.card, "out": self.out, "lang": self.lang, "mode": self.mode, "tuning": self.tenths,
                "rate": self.rate, "no_float": self.no_float, "runs": self.runs}), encoding="utf-8")
        except OSError:
            pass

    def destination(self):
        """(folder, resume) for the home screen, or the reason there is none (text): found when something changed."""
        key = (self.card, self.out, self.tenths, self.rate, self.no_float)
        if self.dest is None or self.dest[0] != key:
            try:
                card = card_root(self.card) if self.card else None
            except ValueError:
                card = None
            if self.card and card is None:
                found = t("KARTE NICHT DA", "CARD NOT THERE")
            elif not (self.out and card):
                found = None
            else:
                try:
                    found = destination(self.out, card, self.tenths, self.rate, self.no_float, self.runs)
                except (ValueError, OSError) as e:
                    found = str(e)
            self.dest = (key, found)
        return self.dest[1]

    # --- the loop: LEDs, boxes, pads, OLED, 25 times a second

    def list_top(self):
        items, sel = self.list["items"], self.list["sel"]
        return min(max(0, sel - 1), max(0, len(items) - self.ROWS))

    def tick(self):
        if self.closing and not self.busy:
            self.root.destroy()
            return
        now = time.monotonic()
        self.poll()
        blink = int(now * 2.5) % 2 == 0
        Z = self.Z
        lit = {"read": self.mode == "read", "retune": self.mode == "retune",
               "start": self.running == "start" and (blink or not self.stop.is_set()),
               "card": bool(self.card) or blink, "out": bool(self.out) or (self.mode == "retune" and blink),
               "report": bool(self.report_text)}
        for key, (led, colour) in self.leds.items():
            self.show(led, colour if lit[key] else self.dim(colour, 0.22))
        ticked = {"rate": self.rate == 44100, "keep": self.rate == 0, "float": not self.no_float,
                  "quieter": self.no_float}
        for key, marks in self.checks.items():
            for m in marks:
                if self.shown.get(m) != ticked[key]:
                    self.shown[m] = ticked[key]
                    self.c.itemconfigure(m, state="normal" if ticked[key] else "hidden")
        mid = (Z(578) + Z(874)) // 2
        x = mid - Z(30) if self.lang == "de" else mid + Z(2)
        if self.shown.get("lang_knob") != x:
            self.shown["lang_knob"] = x
            self.c.coords(self.lang_knob, x, Z(548) - Z(11), x + Z(28), Z(548) + Z(11))
            for tag, on in (("lang_de", self.lang == "de"), ("lang_en", self.lang == "en")):
                self.c.itemconfigure(tag, fill=LABEL if on else self.dim(LABEL, 0.35))
        # The card, the folder for the new one, the tuning: their text on the panel
        cx, cy = self.card_area
        card = self.card or t("KEINE KARTE", "NO CARD")
        self.redraw("card_name", (os.path.basename(card.rstrip("\\/")) or card)[:14], cx, cy + Z(24), Z(2))
        self.redraw("card_path", fit(card, 28), cx, cy + Z(46), Z(1), SMALL)
        st = self.stats
        self.redraw("card_stats", f"{st['audio']} SAMPLES, {songs(st['songs'])}" if st and self.card else "", cx,
                    cy + Z(60), Z(1), SMALL)
        ox, oy = self.out_area
        out = self.out or t("(NOCH KEIN ORDNER)", "(NO FOLDER YET)")
        self.redraw("out_name", (os.path.basename(out.rstrip("\\/")) or out)[:14] if self.out else "", ox, oy + Z(24),
                    Z(2))
        self.redraw("out_path", fit(out, 28), ox, oy + Z(46), Z(1), SMALL)
        dest = self.destination() if self.mode == "retune" else None
        new = ""
        if isinstance(dest, tuple):
            new = (t("WEITER IN ", "CONTINUES IN ") if dest[1] else t("NEU: ", "NEW: ")) + os.path.basename(dest[0])
        self.redraw("out_new", fit(new, 28), ox, oy + Z(60), Z(1), SMALL)
        tx, ty = self.tuning_area
        if self.redraw("tuning", hz(self.tenths), tx, ty, Z(3)):
            self.c.addtag_withtag("type", "tuning")  # A click on the number types it
        tx, ty = self.cents_area
        self.redraw("cents", cents(self.tenths) + t(" ZU 440 HZ", " TO 440 HZ"), tx, ty, Z(1), SMALL)
        # The pads: the progress while it runs, then the audio files by what happened to them
        keys = pads_of(self.result[0]["counts"], self.PADS) if self.result and not self.busy else []
        colours = {key: colour for key, _, _, colour in CATEGORIES}
        lit_pads = self.PADS * self.done / self.total if self.busy and self.total else -1
        for i, pad in enumerate(self.pads):
            if lit_pads >= 0:
                fill = CYAN if i < int(lit_pads) else self.dim(CYAN, 0.45) if i == int(lit_pads) and blink else \
                    self.dim(WHITE, 0.03)
            elif i < len(keys):
                colour = colours[keys[i]]
                fill = colour if keys[i] != "left as it is" else self.dim(colour, 0.5)
            else:
                fill = self.dim(WHITE, 0.03)
            self.show(pad, fill)
        self.draw_oled(now, blink)
        frame = self.oled.frame()
        if frame != self.shown.get("oled"):
            self.shown["oled"] = frame
            self.img.configure(data=self.oled.ppm())
        self.after_id = self.root.after(40, self.tick)

    def marquee(self, o, x, y, text, since, invert=False, width=None):
        """A line too long for the OLED scrolls, as on the Deluge: a pause, then along, a pause at its end."""
        width = width or o.W - x
        span = o.width(text) - width
        shift = 0
        if span > 0:
            s = time.monotonic() - since - 1.0
            shift = int(min(span, (s % (span / 30 + 2.0)) * 30)) if s > 0 else 0
        o.text(x - shift, y, text, invert=invert)

    def draw_oled(self, now, blink):
        o = self.oled
        o.clear()
        message = self.message if self.message and now < self.message_until else ""
        cols = o.W // 6
        if now < self.boot_until:  # At start the name and the version, as the Deluge shows its own
            o.text((o.W - o.width("DELUGE", 2)) // 2, 14, "DELUGE", 2)
            o.text((o.W - o.width(f"TUNER V{VERSION}")) // 2, 38, f"TUNER V{VERSION}")
            return
        if self.busy:
            title = t(*self.PHASES[self.phase]) if self.phase in self.PHASES else self.busy
            o.text(0, 0, title)
            o.text(o.W - o.width(hz(self.tenths)), 0, hz(self.tenths))
            o.rect(0, 9, o.W, 1)
            line = f"{self.done}/{self.total}" if self.total else t("BITTE WARTEN", "PLEASE WAIT")
            if self.workers and self.phase == "convert":
                line += f", {self.workers} " + t("PROZESSE", "PROCESSES") if self.workers > 1 else \
                    t(", 1 PROZESS", ", 1 PROCESS")
            o.text(0, 16, line[:cols])
            for x, y, w, h in ((0, 30, o.W, 1), (0, 42, o.W, 1), (0, 30, 1, 13), (o.W - 1, 30, 1, 13)):
                o.rect(x, y, w, h)
            if self.total:
                o.rect(2, 32, (o.W - 4) * self.done // self.total, 9)
            else:
                o.rect(2 + int(now * 50) % (o.W - 26), 32, 22, 9)
            if not message:
                o.rect(0, 56, o.W, 8)
                o.text(1, 57, t("HÄLT AN ...", "STOPPING ...") if self.stop.is_set() else
                       t("ESC: ANHALTEN (SPÄTER WEITER)", "ESC: STOP (CONTINUE LATER)"), invert=True)
        elif self.view == "list" and self.list:
            items = self.list["items"]
            self.marquee(o, 0, 0, self.list["title"], self.list["since"])
            o.rect(0, 9, o.W, 1)
            top = self.list_top()
            for row, (text, heading) in enumerate(items[top:top + self.ROWS]):
                y, sel = 12 + row * 9, top + row == self.list["sel"]
                line = ("» " if heading else "  ") + text
                if sel:
                    o.rect(0, y - 1, o.W - 3, 9)
                    self.marquee(o, 0, y, line, self.list["since"], invert=True, width=o.W - 3)
                else:
                    o.text(0, y, line[:cols])
            if len(items) > self.ROWS:  # Where in the list: a thin bar on the right
                h = max(3, 45 * self.ROWS // len(items))
                o.rect(o.W - 1, 11 + (45 - h) * self.list["sel"] // max(1, len(items) - 1), 1, h)
        else:
            self.draw_home(o)
        if message:
            o.rect(0, 56, o.W, 8)
            self.marquee(o, 1, 57, message, self.message_since, invert=True)

    def draw_home(self, o):
        """What START will do, from the buttons and boxes set."""
        if not self.card:
            o.text((o.W - o.width(t("KARTE?", "CARD?"), 2)) // 2, 6, t("KARTE?", "CARD?"), 2)
            o.text(1, 30, t("KNOPF KARTE: DIE SD-KARTE", "CARD BUTTON: CHOOSE THE SD"))
            o.text(1, 39, t("ODER EINE KOPIE WÄHLEN", "CARD OR A COPY OF IT"))
            return
        read = self.mode == "read"
        o.text(0, 0, t("LESEN", "READ") if read else t("UMSTIMMEN", "RETUNE"))
        o.text(o.W - o.width(hz(self.tenths)), 0, hz(self.tenths))
        o.rect(0, 9, o.W, 1)
        o.text(0, 12, t("ZEIGT, WAS ES MACHEN WÜRDE", "SHOWS WHAT IT WOULD DO") if read else
               t("SCHREIBT EINE NEUE KARTE", "WRITES A NEW CARD"))
        dest, bottom = self.destination(), t("START: LESEN", "START: READ")
        if read:
            o.text(0, 21, t("ÄNDERT NICHTS", "CHANGES NOTHING"))
        elif not self.out:
            o.text(0, 21, t("ZIEL? KNOPF ZIEL", "OUTPUT? OUTPUT BUTTON"))
            bottom = t("START: ZIEL WÄHLEN", "START: CHOOSE THE OUTPUT")
        elif isinstance(dest, tuple):  # The end of the path: the new card's folder
            o.text(0, 21, t("NACH ", "TO ") + fit(dest[0], o.W // 6 - len(t("NACH ", "TO "))))
            bottom = t("START: FORTSETZEN", "START: CONTINUE") if dest[1] else t("START: UMSTIMMEN", "START: RETUNE")
        else:
            self.marquee(o, 0, 21, str(dest), self.boot_until)
            bottom = t("KNOPF KARTE: KARTE WÄHLEN", "CARD BUTTON: CHOOSE A CARD") if dest == t(
                "KARTE NICHT DA", "CARD NOT THERE") else t("ZIEL: ANDEREN ORDNER WÄHLEN", "OUTPUT: CHOOSE ANOTHER")
        rate = t("44,1 KHZ", "44.1 KHZ") if self.rate else t("RATE BLEIBT", "RATE KEPT")
        o.text(0, 30, rate + ", " + t("SPITZEN: ", "PEAKS: ") + (t("LEISER", "QUIETER") if self.no_float else "FLOAT"))
        o.text(0, 39, t("KARTE ", "CARD ") + fit(self.card, 22))
        st = self.stats
        if st:
            o.text(0, 48, f"{st['audio']} SAMPLES, {songs(st['songs'])}, {size_text(st['bytes'])}"[:28])
        o.text(1, 57, bottom)


# --------------------------------------------------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(prog="DelugeTuner", description="Retunes a Deluge sample library once to the master "
                                                                 "tune, in a window (the command line is "
                                                                 "retune_library.py's).")
    ap.add_argument("--version", action="version", version=f"DelugeTuner v{VERSION}")
    ap.add_argument("--lang", choices=("de", "en"),
                    help="the window's language: German or English (default: the one chosen in the window before, else "
                         "the computer's language)")
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)  # The self-test's folder
    args = ap.parse_args(argv)
    # Worker processes started anew, as on Windows: not forked from the window's process (the .exe starts itself,
    # freeze_support() below)
    multiprocessing.set_start_method("spawn", force=True)
    if args.selftest:
        return 0 if selftest(args.out or ".") else 1
    if sys.platform.startswith("win"):
        try:  # Sharp text on scaled Windows displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    import tkinter as tk
    root = tk.Tk()
    App(root, settings_path(), z=min(3.0, max(1.0, root.winfo_fpixels("1i") / 96)), lang=args.lang)
    root.mainloop()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # The .exe started as a worker process: converts, then ends here
    sys.exit(main())
