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
  deluge_profiler.py live [-p PORT] [-s SECONDS] [-o rec.jsonl] [--symbols S] [--every N]
                                                                   the CPU monitor live, one line a second, and with
                                                                   Profile the busiest tracks and tasks every N s
  deluge_profiler.py ports                                         list the MIDI inputs

Recording: plug the Deluge in over USB, Settings > CPU monitor > Profile, start this, play (or don't), and it stops
after SECONDS. The report shows where the time goes: by function (with self time and the task it ran in), by task,
by output (a track's render time, measured exactly on the device), and how much of it is the audio routine.

Live: Settings > CPU monitor > On (or Profile) on the Deluge, then this. Each second a line of the CPU monitor: load
(average and peak of the audio windows), voices, quality lowered (direness), voices cut (culling), SD card loads and the
longest gap between two audio routines. With Profile, every N seconds the tracks' render times and the tasks too. With
-o it saves everything as record does, for report afterwards.

The same is in tools/profiler.html and tools/cpu_monitor.html for a browser (Chrome or Edge) without installing
anything."""
import argparse
import bisect
import collections
import json
import os
import re
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


CPU_STATS = 0x10
CPU_STATS_FIELDS = (("version", 1), ("seq", 2), ("windowMs", 2), ("dspAvgPermille", 2), ("dspPeakPermille", 2),
                    ("voicesNow", 2), ("voicesMax", 2), ("direMax", 1), ("direSharePermille", 2), ("culled", 2),
                    ("sdLoads", 2), ("sdAvgUs", 4), ("sdMaxUs", 4), ("maxGapUs", 4), ("samples", 4))


def decode_cpu_stats(msg):
    """One CPU monitor SysEx message (bytes, F0 to F7) -> dict of its fields, or None if it isn't one. The same as
    decodeCpuStats() in tools/cpu_monitor.html: header F0 00 21 7B 01 10 (or F0 7D 10), fields of 7-bit groups, least
    significant first; format 2 adds the SD card time without the audio rendered meanwhile (None in format 1)."""
    msg = bytes(msg)
    if len(msg) >= 6 and msg[:5] == HEADER:
        pos = 5
    elif len(msg) >= 3 and msg[0] == 0xF0 and msg[1] == 0x7D:
        pos = 2
    else:
        return None
    if msg[pos] != CPU_STATS:
        return None
    pos += 1
    end = len(msg) - 1
    if msg[end] != 0xF7 or end - pos < 36 or msg[pos] < 1 or any(b > 0x7F for b in msg[pos:end]):
        return None
    d = {}
    for name, n in CPU_STATS_FIELDS:
        d[name], pos = bits(msg, pos, n)
    d["sdCardAvgUs"] = d["sdCardMaxUs"] = None
    if d["version"] >= 2 and end - pos >= 8:
        d["sdCardAvgUs"], pos = bits(msg, pos, 4)
        d["sdCardMaxUs"], pos = bits(msg, pos, 4)
    return d


def cpu_line(d):
    """A CPU monitor message as one line of text."""
    sd = ""
    if d["sdLoads"]:
        sd = f"  SD {d['sdLoads']} loads, avg {d['sdAvgUs'] / 1000:.1f} max {d['sdMaxUs'] / 1000:.1f} ms"
    return (f"CPU {d['dspAvgPermille'] / 10:5.1f} % (peak {d['dspPeakPermille'] / 10:5.1f})  "
            f"voices {d['voicesNow']:3} (max {d['voicesMax']:3})  "
            f"QL {d['direMax']} ({d['direSharePermille'] / 10:.1f} % of the time)  cut {d['culled']}  "
            f"gap {d['maxGapUs'] / 1000:.1f} ms{sd}")


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

def third_deluge_ports(names):
    """The MIDI input names that are the Deluge's third port, best first; all the Deluge's if none is recognisably
    the third."""
    deluge = [n for n in names if "deluge" in n.lower()]
    windows = [n for n in deluge if "midiin3" in n.lower().replace(" ", "")]
    other = [n for n in deluge if n not in windows and not n.lower().startswith("midiin")
             and re.search(r"(?:port|midi)\s*3(?!\d)|\s3$", n.strip(), re.I)]
    return windows + other or deluge


def open_input(port_name=None):
    try:
        import mido
    except ImportError:
        raise SystemExit("needs mido and python-rtmidi: pip install mido python-rtmidi")
    names = mido.get_input_names()
    if port_name is None:
        # The Deluge's third USB MIDI port (the CPU monitor's and the profiler's). Its name differs: "Deluge Port 3"
        # (macOS), "Deluge:Deluge MIDI 3 28:2" (Linux), "MIDIIN3 (Deluge) 2" (Windows, where the last digit is the
        # port's index and the first port is plain "Deluge 0")
        candidates = third_deluge_ports(names)
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
                    if decode_cpu_stats(raw) is None:
                        continue
                    d = dict(kind="cpu")
                counts[d["kind"]] += 1
                f.write(json.dumps({"t": time.time(), "hex": raw.hex()}) + "\n")
            time.sleep(0.005)
    print(f"got {counts['samples']} sample messages, {counts['output_times']} output times, {counts['names']} names, "
          f"{counts['cpu']} CPU monitor lines")
    if not counts["samples"]:
        print("nothing came: is Profile on, and is it port 3 of the Deluge? (deluge_profiler.py ports)")


def live(args):
    port, name = open_input(args.port)
    namer = Namer(load_symbols(args.symbols)) if args.symbols else (lambda a: f"0x{a:08x}")
    print(f"live from {name}" + (f" for {args.seconds} s" if args.seconds else ", Ctrl-C to stop")
          + (f" -> {args.out}" if args.out else "") + " (Settings > CPU monitor > On or Profile on the Deluge)",
          flush=True)
    out = open(args.out, "w") if args.out else None
    start = time.time()
    end = start + args.seconds if args.seconds else None
    interval, names, last_summary, last_cpu = [], [], start, start
    try:
        while end is None or time.time() < end:
            for msg in port.iter_pending():
                if msg.type != "sysex":
                    continue
                raw = bytes([0xF0, *msg.data, 0xF7])
                d = decode(raw)
                cpu = decode_cpu_stats(raw) if d is None else None
                if d is None and cpu is None:
                    continue
                if out:
                    out.write(json.dumps({"t": time.time(), "hex": raw.hex()}) + "\n")
                if cpu:
                    last_cpu = time.time()
                    print(f"{time.strftime('%H:%M:%S')}  {cpu_line(cpu)}", flush=True)
                elif d["kind"] == "names":
                    names.append(d)
                else:
                    interval.append(d)
            now = time.time()
            if now - last_summary >= args.every:
                if any(m["kind"] == "samples" for m in interval):
                    print_summary(analyse(names + interval, namer), args.top, bool(args.symbols))
                interval, last_summary = [], now
                names = names[-4:]  # The names come every 2 s: the latest are enough
            if now - last_cpu > 5:
                print("(nothing for 5 s: is the CPU monitor on, and is it port 3 of the Deluge? "
                      "deluge_profiler.py ports)", flush=True)
                last_cpu = now
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        if out:
            out.close()


def print_summary(r, top, with_functions):
    total = r["total"]
    pct = lambda n: 100.0 * n / total  # noqa: E731
    task = lambda i: r["task_names"].get(i, "(scheduler)" if i == NO_TASK else f"task {i}")  # noqa: E731
    parts = [f"  profile: audio routine {pct(r['audio']):.0f} %"]
    if r["window_ticks"]:
        parts.append("tracks " + ", ".join(
            f"{r['output_names'].get(o, f'output {o}')} {100.0 * t / r['window_ticks']:.1f} %"
            for o, t in sorted(r["output_ticks"].items(), key=lambda x: -x[1])[:top]))
    parts.append("tasks " + ", ".join(f"{task(t)} {pct(n):.0f} %" for t, n in r["by_task"].most_common(top)))
    if with_functions:
        parts.append("functions " + ", ".join(f"{n_[:40]} {pct(n):.0f} %" for n_, n in r["by_function"].most_common(top)))
    print("\n    ".join(parts), flush=True)


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


def read_recording(path, cpu=None):
    """The profiler's messages of a recording; the CPU monitor's go to the list cpu, if given."""
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
            elif cpu is not None:
                c = decode_cpu_stats(raw)
                if c:
                    cpu.append(c)
    return messages


def report(args):
    namer = Namer(load_symbols(args.symbols))
    cpu = []
    r = analyse(read_recording(args.recording, cpu), namer)
    if cpu:
        n = len(cpu)
        print(f"CPU monitor, {n} s: load {sum(c['dspAvgPermille'] for c in cpu) / n / 10:.1f} % on average, "
              f"peak {max(c['dspPeakPermille'] for c in cpu) / 10:.1f} %, voices up to {max(c['voicesMax'] for c in cpu)}, "
              f"quality lowered {sum(c['direMax'] > 0 for c in cpu)} s, voices cut {sum(c['culled'] for c in cpu)}, "
              f"longest gap {max(c['maxGapUs'] for c in cpu) / 1000:.1f} ms\n")
    total = r["total"]
    if not total:
        raise SystemExit("no profiler samples in the recording (CPU monitor on Profile?)")
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
    p = sub.add_parser("live")
    p.add_argument("-p", "--port")
    p.add_argument("-s", "--seconds", type=float, help="stop after this long (default: Ctrl-C)")
    p.add_argument("-o", "--out", help="save everything, as record does")
    p.add_argument("--symbols", help="symbols .json or .elf: the busiest functions too")
    p.add_argument("--every", type=float, default=5, help="seconds between the profile's summaries")
    p.add_argument("--top", type=int, default=4)
    p.set_defaults(func=live)
    p = sub.add_parser("ports")
    p.set_defaults(func=ports)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
