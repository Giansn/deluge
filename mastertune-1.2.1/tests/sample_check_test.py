#!/usr/bin/env python3
"""Tests tools/sample_check.py on a made-up card: SAMPLES/Kick/ with one file per problem the checker looks for, and a
kit that uses some of them.
- good.wav (a 55 Hz kick, 44.1 kHz mono 16 bit, decaying to silence): OK, pitch about 55 Hz, A1
- late.wav (10 ms of silence first): WARN start; the kit's zone ends beyond the file: WARN
- cut.wav (stops at full level): WARN end; no kit uses it: NOTE
- clip.wav (driven into full scale): WARN clipped; the kit names it in lower case: found (FAT)
- wide.wav (48 kHz stereo with two equal channels, tuning tag 432 Hz): NOTE twice; the folder's tags differ: Other
- ext.wav (WAVE_FORMAT_EXTENSIBLE): ERROR, so exit status 1
- good_copy.wav (good.wav again): NOTE the same audio
- ._good.wav (a Mac's file): Other
Usage: sample_check_test.py   (Python 3 with numpy)
"""
import math
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "sample_check.py")


def wav(path, x, rate=44100, extensible=False, mtun=None):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    ch = x.shape[1]
    data = np.clip(np.round(x * 32768), -32768, 32767).astype("<i2").tobytes()
    if extensible:
        fmt = struct.pack("<HHIIHHHHIH14s", 0xFFFE, ch, rate, rate * 2 * ch, 2 * ch, 16, 22, 16, 0, 1, b"\0" * 14)
    else:
        fmt = struct.pack("<HHIIHH", 1, ch, rate, rate * 2 * ch, 2 * ch, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if mtun is not None:
        body += b"mtun" + struct.pack("<Ii", 4, mtun)
    body += b"data" + struct.pack("<I", len(data)) + data
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", len(body)) + body)


def kick(rate=44100, seconds=0.6, f=55.0):
    t = np.arange(int(rate * seconds)) / rate
    return 0.8 * np.sin(2 * math.pi * f * t) * np.exp(-t / 0.06)


def main():
    fails = []

    def check(cond, what):
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            fails.append(what)

    with tempfile.TemporaryDirectory() as card:
        d = os.path.join(card, "SAMPLES", "Kick")
        os.makedirs(d)
        os.makedirs(os.path.join(card, "KITS"))
        k = kick()
        wav(os.path.join(d, "good.wav"), k)
        wav(os.path.join(d, "good_copy.wav"), k)
        wav(os.path.join(d, "late.wav"), np.concatenate([np.zeros(441), k]))
        t = np.arange(22050) / 44100
        wav(os.path.join(d, "cut.wav"), 0.5 * np.sin(2 * math.pi * 60 * t))
        wav(os.path.join(d, "clip.wav"), np.clip(3 * k, -1, 1))
        wide = kick(48000)
        wav(os.path.join(d, "wide.wav"), np.stack([wide, wide], axis=1), rate=48000, mtun=4320)
        wav(os.path.join(d, "ext.wav"), k, extensible=True)
        with open(os.path.join(d, "._good.wav"), "wb") as fh:
            fh.write(b"\0" * 64)
        with open(os.path.join(card, "KITS", "KIT000.XML"), "w") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<kit firmwareVersion="c1.2.1">\n<soundSources>\n'
                     '<sound name="KICK"><osc1 type="sample" loopMode="0" fileName="SAMPLES/Kick/good.wav">'
                     '<zone startSamplePos="0" endSamplePos="26460" /></osc1></sound>\n'
                     '<sound name="LATE"><osc1 type="sample" loopMode="0" fileName="SAMPLES/Kick/late.wav">'
                     '<zone startSamplePos="0" endSamplePos="99999" /></osc1></sound>\n'
                     '<sound name="CLIP"><osc1 type="sample" loopMode="0" fileName="samples/kick/CLIP.WAV" />'
                     '</sound>\n</soundSources>\n</kit>\n')
        out = os.path.join(card, "report.md")
        p = subprocess.run([sys.executable, TOOL, d, "--card", card, "-o", out], capture_output=True, text=True)
        check(p.returncode == 1, f"exit status 1 (an ERROR): {p.returncode} {p.stderr.strip()[-300:]}")
        rep = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
        rows = {line.split("`")[1]: line for line in rep.splitlines() if line.startswith("| `")}

        def row(name):
            return rows.get(f"SAMPLES/Kick/{name}", "")

        check("| OK |" in row("good.wav") and "A1" in row("good.wav") and "55." in row("good.wav"),
              "good.wav OK, about 55 Hz, A1: " + row("good.wav"))
        check("| WARN |" in row("late.wav") and "ms before the sound starts" in rep, "late.wav WARN start")
        check("doesn't fit the file's" in rep and "KIT000.XML (sound LATE)" in rep, "late.wav zone too long")
        check("| WARN |" in row("cut.wav") and "cut off, not faded" in rep, "cut.wav WARN end")
        check("`SAMPLES/Kick/cut.wav`: **not used**" in rep, "cut.wav not used")
        check("| WARN |" in row("clip.wav") and "clipped:" in rep, "clip.wav WARN clipped")
        check("sound CLIP" in rep, "clip.wav found under its lower-case path")
        check("| NOTE |" in row("wide.wav") and "two equal channels" in rep and "48000 Hz" in rep
              and "| 432 |" in row("wide.wav"), "wide.wav NOTE rate and equal channels, tag 432")
        check("different tuning tags" in rep, "different tags in the folder")
        check("| ERROR |" in row("ext.wav") and "WAVE_FORMAT_EXTENSIBLE" in rep, "ext.wav ERROR")
        check("| NOTE |" in row("good_copy.wav") and "the same audio as SAMPLES/Kick/good.wav" in rep,
              "good_copy.wav the same audio")
        check("._good.wav: a computer's hidden file" in rep, "._good.wav named")
        check("._good.wav`" not in rep, "._good.wav not analysed")
        if fails:
            print(rep)
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
