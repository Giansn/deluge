#!/usr/bin/env python3
"""Deluge sampling profiler, the computer's side (mastertune-v15-prof).

The firmware (Settings > CPU monitor > Profile) sends over USB MIDI, port 3, as SysEx:
- samples: 1000 a second, each the address the CPU was at and what it was doing (task, audio routine, output)
- the names of the tasks and of the song's outputs, every 2 s
- each output's exact render time, once a second
This tool records them and names the functions from the firmware's symbols.

  deluge_profiler.py symbols <deluge.elf> [-o symbols.json]      the functions of a build (needs arm-none-eabi-nm)
  deluge_profiler.py record [-p PORT] [-s SECONDS] [-o rec.jsonl] record from the Deluge (pip install mido python-rtmidi)
  deluge_profiler.py report rec.jsonl --symbols symbols.json|deluge.elf [--top N] [--csv out.csv]
  deluge_profiler.py ports                                         list the MIDI inputs

Recording: plug the Deluge in over USB, Settings > CPU monitor > Profile, start this, play (or don't), and it stops
after SECONDS. The report shows where the time goes: by function (with self time and the task it ran in), by task,
by output (a track's render time, measured exactly on the device), and how much of it is the audio routine.

The same is in tools/profiler.html for a browser (Chrome or Edge) without installing anything."""
import argparse
import bisect
import collections
import json
import os
import shutil
import subprocess
import sys
import time

HEADER = bytes([0xF0, 0x00, 0x21, 0x7B, 0x01])
SAMPLES, NAMES, OUTPUT_TIMES = 0x11, 0x12, 0x13
TICKS_PER_SECOND = 33_330_000
SAMPLE_RATE = 1000
NO_TASK, NO_OUTPUT = 31, 255


# --- decoding (the formats: src/deluge/processing/engines/profiler_core.h)

def bits(data, pos, n):
    value = 0
    for i in range(n):
        value |= (data[pos + i] & 0x7F) << (7 * i)
    return value, pos + n


def decode(msg):
    """One SysEx message (bytes, F0 to F7) -> dict, or None if it isn't the profiler's."""
    msg = bytes(msg)
    if len(msg) < 8 or msg[:5] != HEADER or msg[-1] != 0xF7:
        return None
    cmd, pos = msg[5], 6
    if cmd == SAMPLES:
        seq, pos = bits(msg, pos, 2)
        dropped, pos = bits(msg, pos, 3)
        count, pos = msg[pos], pos + 1
        samples = []
        for _ in range(count):
            address, pos = bits(msg, pos, 5)
            context, pos = bits(msg, pos, 3)
            samples.append(dict(address=address & 0xFFFFFFFF, task=context & 31, audio=bool(context & 0x20),
                                other_mode=bool(context & 0x40), output=(context >> 8) & 0xFF,
                                weight=max(1, (context >> 16) & 31)))
        if pos != len(msg) - 1:
            raise ValueError(f"samples message: {len(msg) - 1 - pos} bytes left over")
        return dict(kind="samples", seq=seq, dropped=dropped, samples=samples)
    if cmd == NAMES:
        which, count, pos = msg[pos], msg[pos + 1], pos + 2
        names = {}
        for _ in range(count):
            index, pos = bits(msg, pos, 2)
            end = msg.index(0, pos)
            names[index] = msg[pos:end].decode("ascii", "replace")
            pos = end + 1
        return dict(kind="names", which="tasks" if which == 0 else "outputs", names=names)
    if cmd == OUTPUT_TIMES:
        number, pos = msg[pos], pos + 1
        window, pos = bits(msg, pos, 5)
        count, pos = msg[pos], pos + 1
        ticks = {}
        for _ in range(count):
            index, pos = bits(msg, pos, 2)
            t, pos = bits(msg, pos, 5)
            ticks[index] = t
        return dict(kind="output_times", number=number, window=window, ticks=ticks)
    return None


# --- symbols

def find_nm(elf=None):
    for name in ("arm-none-eabi-nm",):
        if shutil.which(name):
            return name
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [os.path.join(here, "..", ".."), os.getcwd()]
    if elf:  # A firmware tree's build/Release/deluge.elf: its toolchain
        roots.insert(0, os.path.join(os.path.dirname(os.path.abspath(elf)), "..", ".."))
    for root in roots:
        candidate = os.path.join(root, "toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-nm")
        if os.path.exists(candidate):
            return candidate
    raise SystemExit("arm-none-eabi-nm not found: put the toolchain's bin on PATH, or use a symbols .json")


def symbols_from_elf(elf, nm=None):
    """The functions of a firmware build: [(start, size, name)], sorted by start."""
    out = subprocess.run([nm or find_nm(elf), "-C", "-S", "--defined-only", "-n", elf], capture_output=True, text=True,
                         check=True).stdout
    functions = []
    for line in out.splitlines():
        parts = line.split(" ", 3)
        if len(parts) == 4 and parts[2] in "tTwW":
            start, size = int(parts[0], 16) & ~1, int(parts[1], 16)
            if size:
                functions.append((start, size, parts[3]))
    functions.sort()
    return functions


def load_symbols(path):
    if path.endswith(".json"):
        with open(path) as f:
            data = json.load(f)
        return [tuple(x) for x in data["functions"]]
    return symbols_from_elf(path)


class Namer:
    def __init__(self, functions):
        self.functions = functions
        self.starts = [f[0] for f in functions]

    def __call__(self, address):
        i = bisect.bisect_right(self.starts, address) - 1
        if i >= 0:
            start, size, name = self.functions[i]
            if address < start + size:
                return name
        return f"? {address:#010x}"


# --- recording

def open_input(port_name=None):
    try:
        import mido
    except ImportError:
        raise SystemExit("needs mido and python-rtmidi: pip install mido python-rtmidi")
    names = mido.get_input_names()
    if port_name is None:
        # The Deluge's third USB MIDI port (the CPU monitor's and the profiler's)
        deluge = [n for n in names if "deluge" in n.lower()]
        third = [n for n in deluge if n.rstrip().endswith("3") or "port 3" in n.lower()]
        candidates = third or deluge
        if not candidates:
            raise SystemExit(f"no Deluge among the MIDI inputs: {names}")
        port_name = candidates[0]
    return mido.open_input(port_name), port_name


def record(args):
    port, name = open_input(args.port)
    print(f"recording from {name} for {args.seconds} s -> {args.out} (Settings > CPU monitor > Profile on the Deluge)")
    counts = collections.Counter()
    end = time.time() + args.seconds
    with open(args.out, "w") as f:
        while time.time() < end:
            for msg in port.iter_pending():
                if msg.type != "sysex":
                    continue
                raw = bytes([0xF0, *msg.data, 0xF7])
                d = decode(raw)
                if d is None:
                    continue
                counts[d["kind"]] += 1
                f.write(json.dumps({"t": time.time(), "hex": raw.hex()}) + "\n")
            time.sleep(0.005)
    print(f"got {counts['samples']} sample messages, {counts['output_times']} output times, {counts['names']} names")
    if not counts["samples"]:
        print("nothing came: is Profile on, and is it port 3 of the Deluge? (deluge_profiler.py ports)")


# --- report

def analyse(messages, namer):
    """messages: decoded dicts in order -> the numbers of the report."""
    task_names, output_names = {}, {}
    by_function, by_task, by_output = collections.Counter(), collections.Counter(), collections.Counter()
    function_task = collections.defaultdict(collections.Counter)
    audio = total = 0
    dropped = lost_messages = 0
    last_seq = None
    output_ticks = collections.Counter()
    for m in messages:
        if m["kind"] == "names":
            (task_names if m["which"] == "tasks" else output_names).update(m["names"])
        elif m["kind"] == "output_times":
            output_ticks.update(m["ticks"])
        elif m["kind"] == "samples":
            if last_seq is not None:
                lost_messages += (m["seq"] - last_seq - 1) % 16384
            last_seq = m["seq"]
            dropped += m["dropped"]
            for s in m["samples"]:
                w = s["weight"]
                name = namer(s["address"])
                total += w
                by_function[name] += w
                by_task[s["task"]] += w
                function_task[name][s["task"]] += w
                if s["audio"]:
                    audio += w
                if s["output"] != NO_OUTPUT:
                    by_output[s["output"]] += w
    # The output times' windows: a window with many outputs takes several messages, each with the window's number and
    # length, which counts once
    window_ticks, previous = 0, None
    for m in messages:
        if m["kind"] == "output_times":
            if m["number"] != previous:
                window_ticks += m["window"]
            previous = m["number"]
    return dict(total=total, audio=audio, dropped=dropped, lost_messages=lost_messages, by_function=by_function,
                by_task=by_task, by_output=by_output, function_task=function_task, task_names=task_names,
                output_names=output_names, output_ticks=output_ticks, window_ticks=window_ticks)


def read_recording(path):
    messages = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = bytes.fromhex(json.loads(line)["hex"])
            d = decode(raw)
            if d:
                messages.append(d)
    return messages


def report(args):
    namer = Namer(load_symbols(args.symbols))
    r = analyse(read_recording(args.recording), namer)
    total = r["total"]
    if not total:
        raise SystemExit("no samples in the recording")
    pct = lambda n: 100.0 * n / total  # noqa: E731
    print(f"{total} ms sampled ({total / SAMPLE_RATE:.1f} s), {r['dropped']} samples dropped, "
          f"{r['lost_messages']} messages lost")
    print(f"audio routine: {pct(r['audio']):.1f} % of the time")
    task = lambda i: r["task_names"].get(i, "(scheduler)" if i == NO_TASK else f"task {i}")  # noqa: E731
    print("\nby task:")
    for t, n in r["by_task"].most_common():
        print(f"  {pct(n):5.1f} %  {task(t)}")
    if r["window_ticks"]:
        print("\nby output (render time measured on the device, share of the time):")
        for o, t in sorted(r["output_ticks"].items(), key=lambda x: -x[1])[:args.top]:
            print(f"  {100.0 * t / r['window_ticks']:5.1f} %  {r['output_names'].get(o, f'output {o}')}")
    print(f"\nby function (top {args.top}; 'Song::renderAudio' is time spent inside the outputs' renders, see above):")
    for name, n in r["by_function"].most_common(args.top):
        tasks = ", ".join(f"{task(t)} {100.0 * c / n:.0f}%" for t, c in r["function_task"][name].most_common(2))
        print(f"  {pct(n):5.1f} %  {name[:90]}  [{tasks}]")
    if args.csv:
        with open(args.csv, "w") as f:
            f.write("kind;name;percent\n")
            for t, n in r["by_task"].most_common():
                f.write(f"task;{task(t)};{pct(n):.2f}\n")
            for o, t in r["output_ticks"].most_common():
                f.write(f"output;{r['output_names'].get(o, o)};{100.0 * t / max(r['window_ticks'], 1):.2f}\n")
            for name, n in r["by_function"].most_common():
                f.write(f"function;{name};{pct(n):.2f}\n")
        print(f"-> {args.csv}")


def symbols(args):
    functions = symbols_from_elf(args.elf)
    out = args.out or os.path.splitext(args.elf)[0] + ".symbols.json"
    with open(out, "w") as f:
        json.dump({"elf": os.path.basename(args.elf), "functions": functions}, f, separators=(",", ":"))
    print(f"{len(functions)} functions -> {out}")


def ports(_args):
    try:
        import mido
    except ImportError:
        raise SystemExit("needs mido and python-rtmidi: pip install mido python-rtmidi")
    for n in mido.get_input_names():
        print(n)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("symbols")
    p.add_argument("elf")
    p.add_argument("-o", "--out")
    p.set_defaults(func=symbols)
    p = sub.add_parser("record")
    p.add_argument("-p", "--port")
    p.add_argument("-s", "--seconds", type=float, default=30)
    p.add_argument("-o", "--out", default="profile.jsonl")
    p.set_defaults(func=record)
    p = sub.add_parser("report")
    p.add_argument("recording")
    p.add_argument("--symbols", required=True, help="symbols .json (deluge_profiler.py symbols) or the build's .elf")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--csv")
    p.set_defaults(func=report)
    p = sub.add_parser("ports")
    p.set_defaults(func=ports)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
