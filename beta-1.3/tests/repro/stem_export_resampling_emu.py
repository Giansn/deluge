#!/usr/bin/env python3
"""No stem export while the output is being resampled (patch 0116). On the v1.3 beta (62a516c2) SHIFT + RECORD starts
recording the output (AudioRecorder::beginOutputRecording()). SAVE + RECORD then started a stem export: the views check
playback and song recording, not the audio recorder, and the export set up its own output recording, which froze with
E242 in AudioRecorder::setupRecordingToFile() (a beta build checks that no recording is set up; a release build would
replace the running recorder). The fuzzer's seed 226, second boot (1226), --mode deep, stop run 9, input 359.

One boot of the real firmware in the emulator (OLED), song view, playback stopped:
  (1) SHIFT held + RECORD (resampling starts), SAVE held + RECORD: no stem export, the "can't export" popup, and the
      resampling goes on (SAVE's release then opens the song browser, as after that popup while playing)
  (2) BACK (the browser closed), a short RECORD press: the resampling ends, no recording left
Throughout: no crash, freeze or hang.
On 62a516c2 with the patches up to 0115: (1) freezes with E242.

Usage: stem_export_resampling_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (which checks didn't hold); exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot() and Inputs)

se, su = kr.se, kr.su
CANT_EXPORT = "Turn off playback and/or recording"  # STRING_FOR_CANT_EXPORT_STEMS (OLED)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    a.elf, a.out = os.path.abspath(a.elf), os.path.abspath(a.out)
    os.makedirs(a.out, exist_ok=True)
    rig, inp = kr.boot(a, "boot")
    emu = rig.emu
    source, none = su.gdb_ints(emu, ["list AudioRecorder::setupRecordingToFile",
                                     "print (int)&audioRecorder.recordingSource",
                                     "print (int)AudioInputChannel::NONE"])
    recording = lambda: emu.u32(source)  # noqa: E731

    res = {}
    try:
        ui = rig.ui_name()
        inp.button("SHIFT", True)
        rig.tm(0.05, "SHIFT held")
        inp.button("RECORD", True)
        rig.tm(0.05, "SHIFT held + RECORD")
        inp.button("RECORD", False)
        inp.button("SHIFT", False)
        rig.tm(1.0, "resampling")
        resampling = recording()
        popups_before = len(rig.popups)
        inp.button("SAVE", True)
        rig.tm(0.05, "SAVE held")
        inp.button("RECORD", True)
        rig.tm(0.05, "SAVE held + RECORD")
        inp.button("RECORD", False)
        rig.tm(0.05, "RECORD released")
        inp.button("SAVE", False)
        rig.tm(0.5, "SAVE released")
        popups = [p[3] for p in rig.popups[popups_before:]]
        res["1"] = dict(ui=ui, resampling=resampling, popups=popups, still=recording(), after=rig.ui_name(),
                        ok=ui == "sessionView" and resampling != none and CANT_EXPORT in popups
                        and recording() == resampling)
        print("1", json.dumps(res["1"]), flush=True)
        inp.button("BACK", True)
        inp.button("BACK", False)
        rig.tm(1.0, "BACK")
        back = rig.ui_name()
        inp.button("RECORD", True)
        rig.tm(0.05, "RECORD")
        inp.button("RECORD", False)
        rig.tm(3.0, "the resampling ends")
        res["2"] = dict(after_back=back, recording=recording(), ui=rig.ui_name(),
                        ok=back == "sessionView" and recording() == none)
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("STOPPED", res["stopped"], flush=True)
    res["problems"] = rig.problems
    json.dump(res, open(os.path.join(a.out, "stem_export_resampling.json"), "w"), indent=1, default=str)
    failed = [k for k in ("1", "2") if not res.get(k, {}).get("ok")] + (["problems"] if rig.problems else [])
    print("PASS" if not failed else f"FAIL ({', '.join(failed)})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
