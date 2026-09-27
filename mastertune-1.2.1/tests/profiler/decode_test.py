#!/usr/bin/env python3
"""Decodes the profiler test's messages (profiler_test.cpp writes them with what they hold) with tools/deluge_profiler.py:
it must read exactly that. Usage: decode_test.py cases.json"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import deluge_profiler  # noqa: E402


def main():
    cases = json.load(open(sys.argv[1]))
    failures = checks = 0
    for c in cases["samples"]:
        d = deluge_profiler.decode(bytes.fromhex(c["hex"]))
        got = [[s["address"], s["task"], int(s["audio"]), int(s["other_mode"]), s["output"], s["weight"]]
               for s in d["samples"]]
        checks += 1
        if d["kind"] != "samples" or d["seq"] != c["seq"] or d["dropped"] != c["dropped"] or got != c["samples"]:
            failures += 1
            print(f"FAIL samples {c['hex'][:40]}...: {d}")
    for c in cases["names"]:
        d = deluge_profiler.decode(bytes.fromhex(c["hex"]))
        checks += 1
        if d["kind"] != "names" or d["which"] != c["which"] or {str(k): v for k, v in d["names"].items()} != c["names"]:
            failures += 1
            print(f"FAIL names: {d} != {c['names']}")
    total = {}
    for c in cases["output_times"]:
        d = deluge_profiler.decode(bytes.fromhex(c["hex"]))
        checks += 1
        if d["kind"] != "output_times" or d["number"] != c["number"] or d["window"] != c["window"] or \
                {str(k): v for k, v in d["ticks"].items()} != c["ticks"]:
            failures += 1
            print(f"FAIL output times: {d}")
        total.update(d["ticks"])
    # The report counts a window once over its messages
    msgs = [deluge_profiler.decode(bytes.fromhex(c["hex"])) for c in cases["output_times"]]
    r = deluge_profiler.analyse(msgs, lambda a: "x")
    checks += 1
    if r["window_ticks"] != 33330000 or dict(r["output_ticks"]) != total:
        failures += 1
        print(f"FAIL analyse: window {r['window_ticks']}, {len(r['output_ticks'])} outputs")
    checks += 1
    if deluge_profiler.decode(bytes([0xF0, 0x7E, 0x00, 0x06, 0x01, 0xF7])) is not None:
        failures += 1
        print("FAIL: another SysEx isn't the profiler's")
    print(f"deluge_profiler.py decoder: {checks} checks, {failures} failed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
