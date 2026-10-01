#!/usr/bin/env python3
"""A track or mixdown export hangs for good when a stem's recording is aborted (v1.3 beta, 62a516c2; upstream #4471:
offline song export of a long arrangement hangs, #4639).

When RAM runs short while a stem is recorded, SampleRecorder::createNextCluster() fails and the recording is aborted
(its file deleted); AudioRecorder::finishRecording() then clears recordingSource. A track or mixdown export plays the
arrangement and waits (StemExport::exportInstrumentStems() / exportMixdownStem(): a yield until playback has stopped),
relying on the arrangement to stop at its end. Arrangement::doTickForward() stops there only when not recording or
when resampling (recordingSource >= MIX), but the export runs with playbackHandler.recording NORMAL: with the recorder
gone, the arrangement plays on past its end for ever, and the export with it (offline: rendering silence at full speed,
the Deluge looks hung; the stem export UI mode stays on). Clip and drum exports end playback themselves (checkForLoopEnd())
and are not affected. The same happens when the recorder can't be created at all (getNewRecorder() out of RAM).

The run (export_repeat_emu.py, beside this file): the song with an arrangement (8 s), in arranger view SAVE + RECORD:
a track export whose 30th createNextCluster() call fails, then a mixdown whose 30th call fails, then a clean track
export and a clean mixdown, which must write whole stems; each export within 25 s of emulated time (one takes about
5), the state reset after each.

Usage: export_abort_hang_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR [--offline 1|0]
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
    ap.add_argument("--offline", default="1")
    a = ap.parse_args()
    sys.argv = [sys.argv[0], a.elf, "--tools", a.tools, "--build", a.build, "--out", a.out, "--songs", "DEFAULT",
                "--exports", "track,mixdown,track,mixdown", "--fail-each", "30", "--fail-in", "1,2",
                "--offline", a.offline, "--limit", "25" if a.offline == "1" else "120"]
    return export_repeat_emu.main()


if __name__ == "__main__":
    sys.exit(main())
