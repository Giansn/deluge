#!/usr/bin/env python3
"""A synth preset that fails to load, on the v1.3 beta (62a516c2): M000. StorageManager::loadInstrumentFromFile()
abandons a preset whose reading fails (a card read error, out of RAM, a broken file) and frees the new instrument
with delugeDealloc(static_cast<void*>(newInstrument)). For a SoundInstrument (class SoundInstrument : public Sound,
public MelodicInstrument) the Instrument part doesn't start the block, so the allocator is handed a pointer into the
middle of it: MemoryRegion::dealloc() freezes with M000. The fuzzer found it (seed 2041, --mode all, input 341, in
the sound editor); the code is from 2023 (#588), so 1.2.1 has it too.

One boot of the real firmware in the emulator (OLED). The synth clip PADA, LOAD + SYNTH (the synth preset browser),
SoundInstrument::readFromFile() made to fail (Error::SD_CARD, as a card read error would), the select encoder
turned three times: each preview loads a preset and abandons it. Must not freeze or crash; the failed reads must have
happened (else the check proves nothing), and the browser must close again with BACK.

Usage: abandon_load_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot, Inputs and Song; puts the rig on the path)
import stress_ui_emu as su  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rig, inp = kr.boot(a, "boot")
    emu = rig.emu
    sd_card_error, = su.gdb_ints(emu, ["print (int)Error::SD_CARD"])
    failed_reads = []

    def fail_read(e):
        failed_reads.append(1)
        return sd_card_error
    res = {}
    try:
        song = kr.Song(rig)
        res["entered"] = song.enter(inp, 0)
        inp.button("LOAD", True)
        rig.tm(0.05, "LOAD held")
        inp.button("SYNTH", True)
        rig.tm(0.05, "LOAD + SYNTH")
        inp.button("SYNTH", False)
        inp.button("LOAD", False)
        rig.tm(1.0, "synth browser opens")
        res["opened"] = rig.ui_name()
        emu.intercept(emu.sym.find("_ZN15SoundInstrument12readFromFile"), fail_read)
        emu.uc.ctl_flush_tb()
        for _ in range(3):
            inp.turn("select", 1)
            rig.tm(0.7, "select +1 (preview, the read fails)")
        res["failed_reads"] = len(failed_reads)
        kr.long_back(rig, inp, "_ZN22LoadInstrumentPresetUI10exitActionEv")
        res["ui_after"] = rig.ui_name()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["failed_reads"] = len(failed_reads)
    res["problems"] = rig.problems
    res["error_popups"] = rig.error_popups()
    for k, v in res.items():
        print(f"  {k}: {v}")
    ok = (res.get("entered") and res.get("opened") == "loadInstrumentPresetUI" and res["failed_reads"] > 0
          and not res["problems"] and res.get("ui_after") == "instrumentClipView")
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
