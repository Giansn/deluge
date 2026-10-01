#!/usr/bin/env python3
"""Which clusters of a sample a sound holds in RAM (claims a "reason" for), on the v1.3 beta. Upstream #4952 (30
September 2026) meant to hold short samples whole, but (a) it compared the duration in samples, shifted by the
cluster size's magnitude, so samples up to 4-6 times too long counted as short, (b) claimClusterReasonsForMarker()
set its count to 2 whatever it was asked, so "whole" meant two more clusters, and (c) a "short" sample's loop start
was then not claimed at all: a sample up to ~7 s (mono) with a loop point reloaded its loop start from the card at
every pass.

The card is make_sd.py's song with two kit samples replaced by test tones:
  BELL.WAV  131,072 samples mono (9 clusters with the header): held whole (all of them claimed)
  TOM.WAV   300,000 samples mono (19 clusters), loop from sample 200,000: its loop start's cluster claimed
SampleHolder::claimClusterReasonsForMarker() is hooked to find the holders; after the boot every holder's claimed
clusters (clustersForStart, clustersForLoopStart) are mapped to the sample's cluster indices.

Usage: cluster_cache_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Prints PASS or FAIL as its last line; exit status 0 or 1."""
import argparse
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..")))  # beta-1.3/tests
import fuzz_ui as fz  # noqa: E402  (puts mastertune's tests/song and tests/stress/ui on the path)
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R0  # noqa: E402

BELL_SAMPLES, TOM_SAMPLES, TOM_LOOP = 131072, 300000, 200000


def card(image):
    files, lengths = make_sd.samples()
    x = np.arange(BELL_SAMPLES) / make_sd.SR
    files["SAMPLES/BELL.WAV"] = make_sd.wav(0.3 * np.sin(2 * np.pi * 523.25 * x))
    lengths["BELL"] = BELL_SAMPLES
    x = np.arange(TOM_SAMPLES) / make_sd.SR
    files["SAMPLES/TOM.WAV"] = make_sd.wav(0.3 * np.sin(2 * np.pi * 110 * x))
    lengths["TOM"] = TOM_SAMPLES
    xml = make_sd.song_xml(lengths, 1, 2, False, False, False)
    # TOM's zone loops from TOM_LOOP to the end
    xml = xml.replace(f'<zone startSamplePos="0" endSamplePos="{TOM_SAMPLES}" />',
                      f'<zone startSamplePos="0" endSamplePos="{TOM_SAMPLES}" startLoopPos="{TOM_LOOP}" '
                      f'endLoopPos="{TOM_SAMPLES}" />')
    assert f'startLoopPos="{TOM_LOOP}"' in xml, "TOM's zone not found in the song"
    files["SONGS/DEFAULT.XML"] = xml.encode()
    fat32.build(image, files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    card(image)

    holders = set()
    rig = fz.boot("v13", a.elf, a.tools, a.build, image, True)
    emu = rig.emu
    sym = emu.sym
    # The startup song is loaded by now; load it again with the hook on, so every holder claims again
    emu.intercept(sym.find("_ZN12SampleHolder28claimClusterReasonsForMarker"),
                  lambda e: holders.add(e.uc.reg_read(UC_ARM_REG_R0)) and None)
    emu.uc.ctl_flush_tb()
    rig.change_song("DEFAULT", 0.5)

    off = dict(zip(["audioFile", "forStart", "forLoop", "clusters", "scSize", "scCluster", "arrElem", "arrMem",
                    "arrNum", "arrSize", "arrStart", "dataStart"],
                   se.gdb_values(emu, ["(int)&((SampleHolder*)0)->audioFile",
                                       "(int)&((SampleHolder*)0)->clustersForStart",
                                       "(int)&((SampleHolderForVoice*)0)->clustersForLoopStart",
                                       "(int)&((Sample*)0)->clusters", "sizeof(SampleCluster)",
                                       "(int)&((SampleCluster*)0)->cluster",
                                       "(int)&((ResizeableArray*)0)->elementSize",
                                       "(int)&((ResizeableArray*)0)->memory",
                                       "(int)&((ResizeableArray*)0)->numElements",
                                       "(int)&((ResizeableArray*)0)->memorySize",
                                       "(int)&((ResizeableArray*)0)->memoryStart",
                                       "(int)&((Sample*)0)->audioDataStartPosBytes"])))
    first_fn = sym.find("_ZN6Sample33getFirstClusterIndexWithAudioDataEv")
    nodata_fn = sym.find("_ZN6Sample35getFirstClusterIndexWithNoAudioDataEv")

    def u32(addr):
        return struct.unpack("<I", bytes(emu.uc.mem_read(addr, 4)))[0]

    results = {}
    data_start = 0
    for h in sorted(holders):
        sample = u32(h + off["audioFile"])
        if not sample:
            continue
        first = emu.call(first_fn, sample)
        nodata = emu.call(nodata_fn, sample)
        arr = sample + off["clusters"]
        mem, num, size, start = (u32(arr + off[k]) for k in ("arrMem", "arrNum", "arrSize", "arrStart"))
        elem = u32(arr + off["arrElem"])
        index_of = {}
        for i in range(num):
            j = (start + i) % size if size else i
            c = u32(mem + j * elem + off["scCluster"])
            if c:
                index_of[c] = i
        claimed = [u32(h + off["forStart"] + 4 * k) for k in range(2)]
        claimed += [u32(h + off["forLoop"] + 4 * k) for k in range(8)]
        idx = sorted({index_of[c] for c in claimed if c in index_of})
        results.setdefault(nodata - first, []).append(idx)
        if nodata - first == 19:
            data_start = u32(sample + off["dataStart"])
    print("holders by the sample's number of clusters with audio:", results)

    # BELL's data spans 9 clusters with the WAV header before it; TOM's 19
    bell_n = next((n for n in results if 4 < n <= 10), None)
    bell = results.get(bell_n, [])
    tom = results.get(19, [])
    loop_cluster = None
    if tom:
        loop_cluster = (data_start + 2 * TOM_LOOP) >> 15  # the loop start's byte: 2 bytes per mono sample
    ok_bell = bool(bell) and all(set(range(bell_n)) <= set(i) for i in bell)
    ok_tom = bool(tom) and all(loop_cluster in i for i in tom)
    print(f"BELL ({bell_n} clusters): claimed {bell}: held whole {'yes' if ok_bell else 'NO'}")
    print(f"TOM (19 clusters, loop start in cluster {loop_cluster}): claimed {tom}: loop start held "
          f"{'yes' if ok_tom else 'NO'}")
    print("PASS" if ok_bell and ok_tom else "FAIL")
    sys.exit(0 if ok_bell and ok_tom else 1)


if __name__ == "__main__":
    main()
