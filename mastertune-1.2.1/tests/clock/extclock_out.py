#!/usr/bin/env python3
"""Gate and MIDI clock outputs while the Deluge follows an external MIDI clock (upstream 7d9beeef), on the real
firmware in the emulator (the harness of ../songchange): MIDI clock output and the gate clock (gate 4, clock mode) on,
MIDI start, then a MIDI clock every 7 audio windows (123 BPM) for a few bars. The ticks each output has done
(PlaybackHandler::lastMIDIClockOutTickDone, lastTriggerClockOutTickDone: robust against LTO inlining the functions
that count them) are read at every bar line. Without the fix both stop after a short burst; with it they keep the
incoming clock's rate, bar after bar.

Usage: extclock_out.py <deluge.elf> [--bars N] [--internal] [--tools PREFIX] [--build DIR] [--out DIR]
  --internal: the Deluge's own clock instead (PLAY), for comparing builds: ticks per 672 windows are printed.
Exit status 0 when both outputs run at a steady rate in every bar after the first."""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "songchange"))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import songchange_emu as sc  # noqa: E402

EVERY = 7  # windows (of 128 samples) per incoming MIDI clock: 44100 * 60 / (24 * 7 * 128) = 123.05 BPM
CLOCKS_PER_BAR = 96  # 24 per quarter note
GATE_SPECIAL = 2  # GateType::SPECIAL: the gate is the clock output


def offsets(tools, elf):
    queries = [("list PlaybackHandler::PlaybackHandler",
                [("midi_out_clock", "(int)&((PlaybackHandler*)0)->midiOutClockEnabled"),
                 ("analog_in", "(int)&((PlaybackHandler*)0)->usingAnalogClockInput"),
                 ("continue_from", "(int)&((PlaybackHandler*)0)->posToNextContinuePlaybackFrom"),
                 ("midi_done", "(int)&((PlaybackHandler*)0)->lastMIDIClockOutTickDone"),
                 ("gate_done", "(int)&((PlaybackHandler*)0)->lastTriggerClockOutTickDone")]),
               ("list CVEngine::CVEngine", [("clock_gate_mode", "(int)&((CVEngine*)0)->gateChannels[3].mode")])]
    args, names = [], []
    for context, qs in queries:
        args += ["-ex", context]
        for name, expression in qs:
            args += ["-ex", f"print {expression}"]
            names.append(name)
    out = subprocess.run([tools + "gdb", "-batch", *args, elf], capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (\d+)$", out, re.M)]
    if len(values) != len(names):
        raise SystemExit(f"offsets from gdb: {len(values)} of {len(names)}:\n{out[-800:]}")
    return dict(zip(names, values))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--bars", type=int, default=4)
    ap.add_argument("--internal", action="store_true")
    ap.add_argument("--tools")
    ap.add_argument("--build", help="directory with blockcount.so (built there if missing)")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "out"))
    a = ap.parse_args()
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(a.out, exist_ok=True)
    build = a.build or a.out
    if not os.path.exists(os.path.join(build, "blockcount.so")):
        import unicorn
        uc = os.path.dirname(unicorn.__file__)
        subprocess.run(["cc", "-O2", "-shared", "-fPIC", f"-I{uc}/include", os.path.join(HERE, "..", "song", "blockcount.c"),
                        "-o", os.path.join(build, "blockcount.so"), f"-L{uc}/lib", "-l:libunicorn.so.2",
                        f"-Wl,-rpath,{uc}/lib"], check=True)
    sd = os.path.join(a.out, "sd.img")
    sc.build_sd(sd)
    d = sc.Deluge(a.elf, sd, tools, build, True, a.out)
    off = offsets(tools, a.elf)
    ph, cv = d.var["playbackHandler"], d.sym["cvEngine"]
    d.emu.uc.mem_write(ph + off["midi_out_clock"], b"\x01")
    d.emu.uc.mem_write(cv + off["clock_gate_mode"], bytes([GATE_SPECIAL]))

    start = d.window_index
    clocks = []
    if a.internal:
        d.call("_ZN15PlaybackHandler17playButtonPressedEl", 0)
    else:
        # MIDI start as MidiEngine::midiMessageReceived() does it (see ../songchange, start_playback()), then the
        # clocks
        d.emu.uc.mem_write(ph + off["analog_in"], b"\0")
        d.emu.w32(ph + off["continue_from"], 0)
        d.call("_ZN15PlaybackHandler31setupPlaybackUsingExternalClockEbb", 0, 0)

        def clock(dd):
            if (dd.window_index - start) % EVERY == 0:
                dd.call("_ZN15PlaybackHandler9inputTickEbm", 0, 0)
                clocks.append(dd.window_index)
        d.before_window = clock
    bar_windows = CLOCKS_PER_BAR * EVERY
    done = {"midi": [], "gate": []}
    for b in range(a.bars + 1):
        done["midi"].append(d.i64(ph + off["midi_done"]))
        done["gate"].append(d.i64(ph + off["gate_done"]))
        if b < a.bars:
            for _ in range(bar_windows):
                d.step()

    def per_bar(windows):
        return [sum(1 for w in windows if start + b * bar_windows <= w < start + (b + 1) * bar_windows)
                for b in range(a.bars)]

    def diffs(values):
        return [values[b + 1] - values[b] for b in range(a.bars)]
    rows = {"MIDI clock in": per_bar(clocks), "MIDI clock out": diffs(done["midi"]),
            "gate clock out": diffs(done["gate"])}
    if a.internal:
        print(f"internal clock, {a.bars} x 672 windows ({a.elf}): ticks per 672 windows")
        for name in ("MIDI clock out", "gate clock out"):
            print(f"  {name:16} {rows[name]}")
        sys.exit(0)
    print(f"external MIDI clock, 123 BPM, {a.bars} bars ({os.path.basename(a.elf)}): ticks per bar")
    for name, counts in rows.items():
        print(f"  {name:16} {counts}")
    ok = True
    for name in ("MIDI clock out", "gate clock out"):
        later = rows[name][1:]
        steady = bool(later) and min(later) > 0 and max(later) - min(later) <= max(2, max(later) // 24)
        print(f"  {'ok  ' if steady else 'FAIL'} {name}: steady in every bar after the first")
        ok &= steady
    midi_later = rows["MIDI clock out"][1:]
    one_to_one = bool(midi_later) and all(abs(m - CLOCKS_PER_BAR) <= 1 for m in midi_later)
    print(f"  {'ok  ' if one_to_one else 'FAIL'} MIDI clock out: {CLOCKS_PER_BAR} per bar, as many as come in")
    ok &= one_to_one
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
