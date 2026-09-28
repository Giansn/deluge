#!/usr/bin/env python3
"""The table of a run.sh run: per scenario the new build against the old (<out>/new/<scenario>.json, <out>/old/...):
problems (crash, fault, hang, failed song change), the DMA's worst gap and underruns (events / stale samples), the heap
(internal RAM and SDRAM free: first -> last, the least, slope per cycle) and per scenario what else matters (the song
changes' load and wait, the saves' round trip, the settings steps, the browser's actions).

Usage: report.py <out dir>
"""
import json
import os
import sys


def load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def gaps(g):
    if not g:
        return "-"
    return f"gap {g.get('max_gap')}, underruns {g.get('underruns', '?')} ({g.get('underrun_samples')} smp)"


def heap(h):
    out = []
    for k, name in (("internal_free", "int"), ("sdram_free", "SDRAM")):
        v = (h or {}).get(k)
        if v:
            out.append(f"{name} {v['first'] / 1024:.0f}->{v['last'] / 1024:.0f} KB (min {v['min'] / 1024:.0f}, "
                       f"{v['slope_per_cycle']:+.0f} B/cycle)")
    return "; ".join(out) or "-"


def problems(r):
    p = r.get("problems") or []
    inv = r.get("invalid_accesses") or []
    kinds = {}
    for x in p:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    s = ", ".join(f"{k} x{v}" for k, v in kinds.items()) or "none"
    if inv:
        s += f", invalid accesses {sum(x['count'] for x in inv)}"
    if r.get("error_popups"):
        s += f", error popups {len(r['error_popups'])}"
    return s


def line(label, r):
    if not r:
        return f"  {label}: no result"
    s = r.get("summary") or {}
    extra = ""
    if r["scenario"] == "songchange":
        extra = (f"; {s.get('ok')}/{s.get('changes')} changes ok, load max {s.get('load_ms_max')} ms; during changes "
                 f"{gaps(s.get('gap_change'))}; playing after {gaps(s.get('gap_after'))}")
        g = s.get("heap")
    elif r["scenario"] == "bigcard":
        acts = s.get("actions") or {}
        extra = (f"; {gaps(s.get('gap'))}; folder reads {s.get('folder_reads')}; turn median "
                 f"{(acts.get('turn') or {}).get('median', 0) / 1e3:,.0f}k max {(acts.get('turn') or {}).get('max', 0) / 1e6:,.1f}M"
                 f" instr, open {(acts.get('open') or {}).get('max', 0) / 1e6:,.0f}M")
        g = s.get("heap")
    elif r["scenario"] == "save":
        extra = (f"; {s.get('saves')} saves, {s.get('distinct_files')} distinct, max {s.get('save_ms_max')} ms; "
                 f"{gaps(s.get('gap'))}; round trip {'same' if s.get('round_trip_same') else str(s.get('round_trip_changed_lines')) + ' lines differ'}")
        g = s.get("heap")
    elif r["scenario"] == "drone":
        extra = f"; {s.get('ok')}/{s.get('cycles')} cycles ok; {gaps(s.get('gap'))}"
        g = s.get("heap")
    else:
        g = None
    return f"  {label}: problems {problems(r)}{extra}; heap {heap(g)}; {r.get('emulated_s')} s emulated"


def main():
    out = sys.argv[1]
    for scenario in ("songchange", "bigcard", "save", "drone"):
        new, old = (load(os.path.join(out, v, scenario + ".json")) for v in ("new", "old"))
        if not new and not old:
            continue
        print(f"== {scenario}")
        print(line("new", new))
        print(line("old", old))
        for label, r in (("new", new), ("old", old)):
            if r and r["scenario"] == "save" and r.get("round_trip") and not r["round_trip"]["same"]:
                print(f"  {label} round trip diff: " + " | ".join(x for x in r["round_trip"]["diff"]
                                                               if x[:1] in "+-" and x[:3] not in ("+++", "---")))
    st = load(os.path.join(out, "new", "settings.json"))
    if st:
        print("== settings (the new build's card, then the old build on it)")
        for s in st.get("steps", []):
            f = s.get("file") or {}
            keys = {k: f.get(k) for k in ("outputLimiter", "filterCrossingGuard", "oledContrast")}
            print(f"  {s['step']}: file {keys}; menus {s.get('menus', s.get('menus_before'))}; flags {s.get('flags')}; "
                  f"problems {len(s.get('problems', []))}, error popups {len(s.get('error_popups', []))}"
                  + (f"; dropped when it saved {s['dropped']}" if s.get("dropped") else "")
                  + (f"; its settings as without the file: {s['same_as_without_the_file']}"
                     if "same_as_without_the_file" in s else ""))


if __name__ == "__main__":
    main()
