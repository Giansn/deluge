#!/usr/bin/env python3
"""The CPU monitor's messages of cpu_stats_test.cpp (the firmware's encoder) decoded by tools/deluge_profiler.py, field
by field, with the same rejects as decode_test.js; then deluge_profiler.py live on them through a stand-in MIDI port:
one line per message, every field shown as sent.
Usage: decode_test.py cases.json"""
import contextlib
import io
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import deluge_profiler as dp  # noqa: E402


def main():
    cases = json.load(open(sys.argv[1]))
    failures = []
    fail = failures.append
    for i, c in enumerate(cases):
        d = dp.decode_cpu_stats(bytes(c["bytes"]))
        if d is None:
            fail(f"case {i} not decoded")
            continue
        for key, want in c["expected"].items():
            if d.get(key) != want:
                fail(f"case {i} {key}: {d.get(key)} != {want}")
        if len(d) != len(c["expected"]):
            fail(f"case {i}: field count {len(d)} != {len(c['expected'])}")
        if dp.decode_cpu_stats(bytes([0xF0, 0x7D, *c["bytes"][5:]])) != d:
            fail(f"case {i}: short header")
        if dp.decode(bytes(c["bytes"])) is not None:
            fail(f"case {i}: taken for a profiler message")
    good = cases[2]["bytes"]
    rejects = {
        "other command": [0x04 if i == 5 else b for i, b in enumerate(good)],
        "other manufacturer": [0x7C if i == 3 else b for i, b in enumerate(good)],
        "truncated": good[:30] + [0xF7],
        "shorter than format 1": good[:6 + 35] + [0xF7],
        "no F7": good[:-1],
        "high bit in data": [0x80 if i == 20 else b for i, b in enumerate(good)],
        "version 0": [0 if i == 6 else b for i, b in enumerate(good)],
        "note on": [0x90, 60, 100],
    }
    for name, b in rejects.items():
        if dp.decode_cpu_stats(bytes(b)) is not None:
            fail(f"accepted {name}")
    v1 = good[:6] + [1] + good[7:6 + 36] + [0xF7]
    d1 = dp.decode_cpu_stats(bytes(v1))
    if not d1 or d1["version"] != 1 or d1["sdCardAvgUs"] is not None or d1["samples"] != cases[2]["expected"]["samples"]:
        fail("format 1 message")
    if not dp.decode_cpu_stats(bytes(good[:-1] + [1, 2, 3, 0xF7])):
        fail("longer message rejected")

    # live: the messages through a stand-in port, then it stops (-s)
    pending = [types.SimpleNamespace(type="sysex", data=c["bytes"][1:-1]) for c in cases]
    pending.insert(1, types.SimpleNamespace(type="note_on", data=None))

    class Port:
        def iter_pending(self):
            while pending:
                yield pending.pop(0)
    dp.open_input = lambda name=None: (Port(), "Deluge MIDI 3 (stand-in)")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        dp.live(types.SimpleNamespace(port=None, seconds=0.2, out=None, symbols=None, every=5, top=4))
    lines = [line for line in out.getvalue().splitlines() if "CPU " in line and "voices" in line]
    if len(lines) != len(cases):
        fail(f"live: {len(lines)} lines for {len(cases)} messages")
    for c, line in zip(cases, lines):
        e = c["expected"]
        want = [f"CPU {e['dspAvgPermille'] / 10:5.1f} %", f"peak {e['dspPeakPermille'] / 10:5.1f}",
                f"voices {e['voicesNow']:3}", f"cut {e['culled']}", f"gap {e['maxGapUs'] / 1000:.1f} ms"]
        missing = [w for w in want if w not in line]
        if missing:
            fail(f"live line {line!r} lacks {missing}")

    for f in failures[:10]:
        print("FAIL " + f)
    if failures:
        print(f"{len(failures)} FAILED")
        sys.exit(1)
    print(f"deluge_profiler.py CPU monitor decoder: {len(cases)} messages ok, rejects ok, live {len(lines)} lines ok")


if __name__ == "__main__":
    main()
