#!/usr/bin/env python3
"""Two more tests of tools/retune_library.py:

1. Umlauts: the firmware's FAT has code page 437 and no Unicode API (src/fatfs/ffconf.h: FF_CODE_PAGE 437,
   FF_LFN_UNICODE 0), so the Deluge writes the paths in its XML files as CP437 bytes. A song that names
   "SAMPLES/Kick äöü.wav" in CP437 (a kit row with a slice and an audio clip) must find the file: positions scaled, the
   audio clip on the _ts copy (its path written in CP437 again), the report written and readable. An XML written on a
   computer in UTF-8 must still work too.
2. Memory: the plan reads only the headers. A dry run over 300 MB of WAV (sparse files) must not hold the audio.

Usage: paths_memory_test.py <work dir>   Needs: what pc_test.py needs
"""
import json
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pc_test import TOOL, check, sine, wav  # noqa: E402
import pc_test  # noqa: E402
from tone import firmware_wav_view  # noqa: E402

KICK = "SAMPLES/Kick äöü.wav"
KICK_TS = "SAMPLES/Kick äöü_ts.wav"
SNARE = "SAMPLES/Snare é.wav"
N_OLD, N_NEW = 44100, 44917  # 1 s at 44.1 kHz, x 55/54 (440 -> 432 Hz)


def run_tool(*args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "backslashreplace")


def song_xml(path, start, end):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<song firmwareVersion="c1.2.1">\n\t<instruments>\n'
            '\t\t<kit presetName="Kit ä" presetFolder="KITS">\n\t\t\t<soundSources>\n'
            '\t\t\t\t<sound name="Kick äöü" polyphonic="auto" mode="subtractive">\n'
            f'\t\t\t\t\t<osc1 type="sample" transpose="0" cents="0" loopMode="1" timeStretchEnable="0" '
            f'fileName="{path}">\n\t\t\t\t\t\t<zone startSamplePos="{start}" endSamplePos="{end}" />\n'
            '\t\t\t\t\t</osc1>\n\t\t\t\t</sound>\n\t\t\t</soundSources>\n\t\t</kit>\n'
            '\t\t<audioTrack name="Spur ü" />\n\t</instruments>\n\t<sessionClips>\n'
            f'\t\t<audioClip trackName="Spur ü" filePath="{path}" startSamplePos="0" endSamplePos="44100" '
            'pitchSpeedIndependent="1" length="384" />\n\t</sessionClips>\n</song>\n')


def kit_xml(path):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kit>\n\t<soundSources>\n\t\t<sound name="Snare é">\n'
            f'\t\t\t<osc1 type="sample" loopMode="1" fileName="{path}">\n'
            '\t\t\t\t<zone startSamplePos="4410" endSamplePos="44100" />\n'
            '\t\t\t</osc1>\n\t\t</sound>\n\t</soundSources>\n</kit>\n')


def test_umlauts(work):
    card, out = os.path.join(work, "card"), os.path.join(work, "out")
    files = {KICK: wav(sine(440, 44100, 1.0), 44100, 16), SNARE: wav(sine(900, 44100, 1.0), 44100, 16),
             "SONGS/SONG ä.XML": song_xml(KICK, 22050, 44100).encode("cp437"),  # As the Deluge writes it
             "KITS/UTF8.XML": kit_xml(SNARE).encode("utf-8")}  # Edited on a computer
    for rel, data in files.items():
        os.makedirs(os.path.dirname(os.path.join(card, rel)), exist_ok=True)
        open(os.path.join(card, rel), "wb").write(data)
    rc, text = run_tool("--card", card, "--out", out, "--tuning", "432", "--jobs", "1")
    if not check(rc == 0, f"umlauts: the tool failed (exit {rc}):\n{text[-1500:]}"):
        return
    report_txt = os.path.join(out, "RETUNE_REPORT.txt")
    if not check(os.path.isfile(report_txt), "umlauts: no RETUNE_REPORT.txt"):
        return
    txt = open(report_txt, encoding="utf-8").read()
    check("missing" not in txt and "not on the card" not in txt, "umlauts: a file counted as missing")
    check("\\udc" not in txt and "audioClip Spur ü: SAMPLES/Kick äöü.wav" in txt,
          "umlauts: the report doesn't show the names")
    rep = json.load(open(os.path.join(out, "RETUNE_REPORT.json"), encoding="utf-8"))
    check(rep["files"].get(KICK, {}).get("uses") == ["keep_length", "resample"],
          f"umlauts: {KICK} uses {rep['files'].get(KICK, {}).get('uses')}, expected both (slice and audio clip)")
    check(rep["files"].get(SNARE, {}).get("uses") == ["resample"], f"umlauts: {SNARE} not found from UTF-8")
    # The song: still CP437, the slice scaled, the audio clip on the _ts copy (length kept: positions stay)
    song = open(os.path.join(out, "SONGS/SONG ä.XML"), "rb").read()
    want = song_xml(KICK, 22458, N_NEW).replace(f'filePath="{KICK}"', f'filePath="{KICK_TS}"').encode("cp437")
    check(song == want, f"umlauts: the song XML isn't as expected:\n{song.decode('cp437')}")
    kit = open(os.path.join(out, "KITS/UTF8.XML"), "rb").read()
    check(kit == kit_xml(SNARE).replace('"4410"', '"4492"').replace('"44100"', f'"{N_NEW}"').encode(),
          f"umlauts: the UTF-8 kit isn't as expected:\n{kit.decode('utf-8', 'replace')}")
    for rel, frames in ((KICK, N_NEW), (KICK_TS, N_OLD), (SNARE, N_NEW)):
        p = os.path.join(out, rel)
        if check(os.path.isfile(p), f"umlauts: {rel} not written"):
            v = firmware_wav_view(open(p, "rb").read())
            check(v["mtun"] == 4320 and v["data_length"] == frames * 2,
                  f"umlauts: {rel}: mtun {v['mtun']}, {v['data_length'] // 2} samples, expected 4320, {frames}")
    print(f"umlauts: {'ok' if not pc_test.failures else 'FAILED'}")


def test_memory(work, n_files=30, mb=10):
    card = os.path.join(work, "bigcard")
    os.makedirs(os.path.join(card, "SAMPLES"))
    size = mb << 20
    fmt = struct.pack("<HHIIHH", 1, 2, 44100, 44100 * 4, 4, 16)
    head = b"RIFF" + struct.pack("<I", 4 + 24 + 8 + size) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + \
        b"data" + struct.pack("<I", size)
    for i in range(n_files):
        with open(os.path.join(card, "SAMPLES", f"BIG{i:02d}.WAV"), "wb") as fh:
            fh.write(head)
            fh.truncate(len(head) + size)  # Sparse: reads as silence, takes no space
    small = os.path.join(work, "smallcard", "SAMPLES")
    os.makedirs(small)
    open(os.path.join(small, "ONE.WAV"), "wb").write(wav(sine(440, 44100, 0.1), 44100, 16))
    failures_before = pc_test.failures

    def dry_run(folder):
        """(exit status, output, peak RSS in MB) of a dry run in its own process."""
        proc = subprocess.Popen([sys.executable, TOOL, "--card", folder, "--dry-run", "--tuning", "432"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        text = proc.stdout.read().decode("utf-8", "replace")
        _, status, usage = os.wait4(proc.pid, 0)
        return status, text, usage.ru_maxrss / 1024  # kB on Linux

    _, _, base_mb = dry_run(os.path.dirname(small))
    status, text, rss_mb = dry_run(card)
    total_mb = n_files * mb
    print(f"memory: dry run over {total_mb} MB of WAV: peak RSS {rss_mb:.0f} MB ({base_mb:.0f} MB for one small file)")
    check(status == 0, f"memory: the dry run failed:\n{text[-1500:]}")
    check(text.count(f"{size // 4} samples") == n_files, "memory: not every file planned with its length")
    check(rss_mb - base_mb < total_mb / 4, f"memory: peak RSS {rss_mb:.0f} MB for {total_mb} MB of WAV, "
                                           f"{base_mb:.0f} MB for one file (the audio is held)")
    print(f"memory: {'ok' if pc_test.failures == failures_before else 'FAILED'}")


def main():
    work = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "retune-paths-memory")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    test_umlauts(os.path.join(work, "umlauts"))
    test_memory(os.path.join(work, "memory"))
    print("paths_memory_test:", "all ok" if not pc_test.failures else f"{pc_test.failures} FAILURES")
    sys.exit(1 if pc_test.failures else 0)


if __name__ == "__main__":
    main()
