#!/usr/bin/env python3
"""The audio stress test's results side by side (run.sh): a Markdown table of every <out>/<scenario>-<build>/result.json
(audio_stress_emu.py), in run.sh's order.

Usage: summary.py <out dir>

Columns: status (ok, crash, hang, freeze; + invalid memory accesses), CPU per 128 samples mean / p99 / max (%), culls
soft / force, DMA underruns (events / stale samples) and the largest gap, clicks (samples / events), peak (dBFS) and
clipped samples, heap free in the internal RAM and the SDRAM (not the stealable part) after the warm-up -> at the end,
in bytes (a leak: less at the end).
"""
import json
import os
import sys

ORDER = ["full", "full16", "drive", "automation", "storm", "clipping"]


def row(name, r):
    cpu = r.get("cpu", {})
    cu = r.get("culls", {})
    d = r.get("dma", {})
    o = r.get("output", {})
    hs, he = r.get("heap_start"), r.get("heap_end")
    status = r["status"] + (f" ({len(r['invalid_accesses'])} invalid accesses)" if r.get("invalid_accesses") else "")
    if r["status"] != "ok":
        status += f": {r.get('stopped', '')}"

    def heap(k):
        return f"{hs[k]['free']:,} -> {he[k]['free']:,}" if hs and he else "-"
    peak = o.get("peak_dbfs")
    extra = ""
    if "click_events_within_10ms_after_switch" in o:
        extra = f"; {o['click_events_within_10ms_after_switch']} after one of {o['switches']} switches"
    di = r.get("cpu_direness", {})
    cells = [name, status,
             f"{cpu.get('mean', 0):.1f} / {cpu.get('p99', 0):.1f} / {cpu.get('max', 0):.1f}",
             f"{di.get('share_at_14', 0) * 100:.0f} % ({di.get('changes_to_14', 0)})",
             f"{cu.get('soft', 0)} / {cu.get('force', 0)}",
             f"{d.get('underrun_events', 0)} / {d.get('underrun_samples', 0)}, {d.get('max_gap', 0)}",
             f"{o.get('click_samples', '-')} / {o.get('click_events', '-')}{extra}",
             f"{peak:.2f} / {o.get('clipped_samples', '-')}" if peak is not None else "- / -",
             heap("internal"), heap("external")]
    return "| " + " | ".join(cells) + " |"


def main():
    out = sys.argv[1]
    runs = []
    for d in sorted(os.listdir(out)):
        p = os.path.join(out, d, "result.json")
        if os.path.isfile(p):
            scenario = d.split("-")[0]
            runs.append((ORDER.index(scenario) if scenario in ORDER else len(ORDER), d, json.load(open(p))))
    print("| run | status | CPU % mean / p99 / max | direness 14 (changes) | culls soft / force | underruns "
          "(events / samples), max gap | clicks (samples / events) | peak dBFS / clipped | heap internal free "
          "| heap SDRAM free |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for _, d, r in sorted(runs, key=lambda x: (x[0], x[1])):
        print(row(f"{d} ({r['bars']:g} bars)", r))


if __name__ == "__main__":
    main()
