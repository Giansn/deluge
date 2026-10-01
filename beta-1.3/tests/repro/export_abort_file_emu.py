#!/usr/bin/env python3
"""A stem whose recording is aborted (RAM ran out) can stay on the card as a broken file whose header says 5 seconds
(v1.3 beta, 62a516c2; the "only 5 seconds of audio" of the audio export docs' troubleshooting, upstream #4639).

SampleRecorder::abort() (createNextCluster() failed: no RAM for the next cluster) only marks the recorder ABORTED;
its own cardRoutine() then deletes the partial file and takes the half-recorded Sample out of audioFileManager. But
AudioRecorder::slowRoutine() finishes every recorder whose status is >= COMPLETE, ABORTED included: when that task
runs first, finishRecording() frees the recorder and the cleanup never happens. The file stays, cut short, with the
header SampleRecorder::setup() wrote ("5 seconds long initially"), and a Sample with no length stays listed in
audioFileManager under that path.

The run (export_repeat_emu.py, beside this file): the song, Configure Export with Song FX and Kit FX on, offline
rendering, the SDHC-like card; a clip export and two drum exports (the kit's clip: SAVE + RECORD), each export's 30th
and 75th createNextCluster() call failing (INSUFFICIENT_RAM). Every stem file on the card must be whole: RIFF and data
sizes match the file (an aborted stem leaves no file).

Usage: export_abort_file_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line PASS or FAIL; exit 0/1."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_repeat_emu  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.argv = [sys.argv[0], a.elf, "--tools", a.tools, "--build", a.build, "--out", a.out, "--songs", "DEFAULT",
                "--exports", "clip,drum,drum", "--song-fx", "1", "--kit-fx", "1", "--fail-each", "30,75",
                "--limit", "25"]
    return export_repeat_emu.main()


if __name__ == "__main__":
    sys.exit(main())
