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
    # live: all the messages through a stand-in MIDI port, a summary at once (--every 0): the profile's line with the
    # audio routine's share, the tracks by render time (their names from the names messages) and the tasks
    import contextlib
    import io
    import types
    order = cases["names"] + cases["samples"] + cases["output_times"]
    pending = [types.SimpleNamespace(type="sysex", data=list(bytes.fromhex(c["hex"]))[1:-1]) for c in order]

    class Port:
        def iter_pending(self):
            while pending:
                yield pending.pop(0)
    deluge_profiler.open_input = lambda name=None: (Port(), "stand-in")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        deluge_profiler.live(types.SimpleNamespace(port=None, seconds=0.2, out=None, symbols=None, every=0, top=3))
    text = out.getvalue()
    output_names = {}
    for c in cases["names"]:
        if c["which"] == "outputs":
            output_names.update({int(k): v for k, v in c["names"].items()})
    busiest = max(total, key=total.get)
    want = ["profile: audio routine", "tracks ", f"{output_names.get(busiest, f'output {busiest}')} "
            f"{100.0 * total[busiest] / 33330000:.1f} %", "tasks "]
    checks += 1
    if os.environ.get("SHOW_LIVE"):
        print(text)
    if any(w not in text for w in want):
        failures += 1
        print(f"FAIL live summary lacks {[w for w in want if w not in text]}:\n{text}")
    # The Deluge's third port among the MIDI inputs, by the names Windows, macOS and Linux give it
    ports = {
        "MIDIIN3 (Deluge) 2": ["Deluge 0", "MIDIIN2 (Deluge) 1", "MIDIIN3 (Deluge) 2"],
        "Deluge Port 3": ["Deluge Port 1", "Deluge Port 2", "Deluge Port 3"],
        "Deluge:Deluge MIDI 3 32:2": ["Midi Through:Midi Through Port-0 14:0", "Deluge:Deluge MIDI 1 32:0",
                                      "Deluge:Deluge MIDI 2 32:1", "Deluge:Deluge MIDI 3 32:2"],
    }
    for want, names in ports.items():
        checks += 1
        got = deluge_profiler.third_deluge_ports(names)
        if not got or got[0] != want:
            failures += 1
            print(f"FAIL third port of {names}: {got}")
    print(f"deluge_profiler.py decoder: {checks} checks, {failures} failed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
