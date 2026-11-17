#!/usr/bin/env python3
"""The CPU monitor ported from mastertune 1.2.1 (features/0002) in the v1.3 build, with the v1.3 rig (rig13.py: the
firmware's own task manager, the audio DMA in real time) and the fuzzer's card (two synths, a kit, an audio track).

One boot, playing:
  (1) off at first: no line on the OLED, no SysEx counted, nothing measured
  (2) LEARN held + tempo encoder pressed: the mode is On, the popup says so, and the press reaches neither the view nor
      the tempo popup (the current UI is still session view)
  (3) after 3 s: the line in the OLED's top left corner reads "CPU n% nV" with a load between 1 and 99 % and a voice
      count the audio engine had in that time (the line's is the count after the last audio routine of its window,
      so it is checked against the range of AudioEngine::getNumVoices() sampled every 0.1 s); the image sent to the
      OLED has that corner drawn (pixels set there that the screen under it hasn't); one SysEx message a second
      counted (2 to 4 in 3 s)
  (4) the shortcut again: Off, the line gone from the next image
  (5) the shortcut once more: On again (the last of On and Alerts), and the community settings file is written by
      the save task: a second boot from the same card starts with the monitor On
  (7) a stem export rendered offline (playback stopped, SAVE held + RECORD in song view: the clips' stems): the
      monitor pauses for it, so the window that follows doesn't count the export as a gap of the audio routine (its
      longest gap stays under 50 ms, the export takes seconds)
and (6) no crash, freeze or hang.

Usage: cpu_monitor_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (with the failed checks); exit 0/1."""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))  # beta-1.3/tests
import rig13  # noqa: E402,F401  (sets up the paths of mastertune's rig, patches song_emu for v1.3)
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402

ANON = "_ZN9cpu_stats12_GLOBAL__N_1"


def sym_anon(sym, name):
    """A variable in cpu_stats' anonymous namespace, by its demangled name's last part."""
    for n in sym.by_name:
        if n.startswith(ANON) and n.endswith(f"{len(name)}{name}E"):
            return sym[n]
    raise KeyError(name)


def press_shortcut(rig, inp):
    inp.button("LEARN", True)
    rig.tm(0.05, "LEARN held")
    inp.button("TEMPO_ENC", True)
    rig.tm(0.05, "tempo encoder pressed")
    inp.button("TEMPO_ENC", False)
    rig.tm(0.05, "tempo encoder released")
    inp.button("LEARN", False)
    rig.tm(0.3, "LEARN released")


def corner_pixels(rig):
    """Pixels set in the OLED image last sent, in the corner the line takes (the top 9 rows, 60 columns)."""
    emu = rig.emu
    base = emu.sym["_ZN6deluge3hid7display4OLED16oledCurrentImageE"]
    image = emu.u32(base)
    top = 5  # OLED_MAIN_TOPMOST_PIXEL (RZA1/cpu_specific.h), a macro the debug info doesn't have
    count = 0
    for y in range(top, top + 9):
        row = bytes(emu.uc.mem_read(image + (y >> 3) * 128, 60))
        count += sum(1 for b in row if (b >> (y & 7)) & 1)
    return count


def state(rig):
    emu, sym = rig.emu, rig.emu.sym
    line = bytes(emu.uc.mem_read(sym_anon(sym, "line"), 32)).split(b"\0")[0].decode(errors="replace")
    return dict(mode=emu.u8(sym["_ZN9cpu_stats4modeE"]), enabled=emu.u8(sym["_ZN9cpu_stats7enabledE"]), line=line,
                sysex=emu.u32(sym_anon(sym, "sysexSeq")), ui=rig.ui_name())


def boot(a, image):
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    return rig, fuzz_ui.Inputs(rig, "v13")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    fuzz_ui.make_card(image, 2, os.path.join(a.out, "card-template.img"))
    fails, res = [], {}
    rig, inp = boot(a, image)
    try:
        res["at_boot"] = s0 = state(rig)
        inp.button("PLAY", True)
        rig.tm(0.02, "PLAY")
        inp.button("PLAY", False)
        rig.tm(1.5, "playing, monitor off")
        res["off"] = s1 = state(rig)
        if s1["mode"] != 0 or s1["line"] or s1["sysex"] != s0["sysex"]:
            fails.append(f"(1) not off at first: {s1}")
        ui_before = rig.ui_name()
        press_shortcut(rig, inp)
        res["shortcut_on"] = s2 = state(rig)
        res["popups_after_on"] = rig.popups[-2:]
        if s2["mode"] != 1 or not s2["enabled"]:
            fails.append(f"(2) the shortcut didn't switch it on: {s2}")
        if s2["ui"] != ui_before:
            fails.append(f"(2) the press reached the view: {ui_before} -> {s2['ui']}")
        sysex_before = s2["sysex"]
        get_voices = rig.emu.sym.find("_ZN11AudioEngine12getNumVoicesEv")
        sampled = []
        for i in range(30):
            rig.tm(0.1, "playing, monitor on")
            sampled.append(rig.emu.call(get_voices))
        res["on_3s"] = s3 = state(rig)
        res["voices_sampled"] = [min(sampled), max(sampled)]
        m = re.fullmatch(r"CPU (\d+)% (\d+)V( QL)?( VC)?", s3["line"])
        if not m:
            fails.append(f"(3) the line reads {s3['line']!r}")
        else:
            load, v = int(m.group(1)), int(m.group(2))
            if not 1 <= load <= 99:
                fails.append(f"(3) load {load} % out of 1 ... 99")
            if not min(sampled) - 2 <= v <= max(sampled) + 2:
                fails.append(f"(3) the line says {v} voices, the audio engine had {min(sampled)} ... {max(sampled)}")
        sent = s3["sysex"] - sysex_before
        res["sysex_in_3s"] = sent
        if not 2 <= sent <= 4:
            fails.append(f"(3) {sent} SysEx messages in 3 s (one a second)")
        res["corner_on"] = corner_on = corner_pixels(rig)
        press_shortcut(rig, inp)
        res["shortcut_off"] = s4 = state(rig)
        rig.tm(0.7, "monitor off, the next image sent")
        res["corner_off"] = corner_off = corner_pixels(rig)
        if s4["mode"] != 0 or state(rig)["line"]:
            fails.append(f"(4) the shortcut didn't switch it off: {state(rig)}")
        if corner_on < 40 or corner_on - corner_off < 30:
            fails.append(f"(3)/(4) the corner: {corner_on} pixels set with the line, {corner_off} without")
        press_shortcut(rig, inp)
        res["shortcut_on_again"] = s5 = state(rig)
        if s5["mode"] != 1:
            fails.append(f"(5) the shortcut didn't switch it back on: {s5}")
        rig.tm(1.5, "the save task")
        # (7) an offline stem export with the monitor on
        emu = rig.emu
        stem = emu.sym["stemExport"]
        o_offline, = su.gdb_ints(emu, ["list deluge_main", "print (int)&((StemExport*)0)->renderOffline"])
        # The half-second window last published: the first one after the export is published by the first audio
        # routine after it (its window began before the export) and then read here, before the next (0.5 s later)
        o_gap, o_seq = su.gdb_ints(emu, [
            "print (int)&cpu_stats::collector.half_.window.maxGapTicks - (int)&cpu_stats::collector",
            "print (int)&cpu_stats::collector.half_.seq - (int)&cpu_stats::collector"])
        collector = emu.sym["_ZN9cpu_stats9collectorE"]
        inp.button("PLAY", True)
        rig.tm(0.02, "PLAY")
        inp.button("PLAY", False)
        rig.tm(1.0, "stopped")
        emu.uc.mem_write(stem + o_offline, b"\x01")
        B = fuzz_ui.B
        rig.button(B["SAVE"], True)
        rig.tm(0.05, "SAVE held")
        _, took = rig.button(B["RECORD"], True, limit_s=300)  # the export runs inside this press
        rig.button(B["RECORD"], False)
        rig.button(B["SAVE"], False)
        seq_before = emu.u32(collector + o_seq)
        rig.tm(0.1, "after the export")
        gap_s = emu.u32(collector + o_gap) / 33.33e6
        res["export"] = dict(press_s=round(took / 400e6, 2), window_max_gap_ms=round(gap_s * 1000, 1),
                             windows_published=(emu.u32(collector + o_seq) - seq_before) // 2)
        if took / 400e6 < 0.5:
            fails.append(f"(7) the export took {took / 400e6:.2f} s: not an export")
        elif gap_s > 0.05:
            fails.append(f"(7) the window after the export has a {gap_s * 1000:.0f} ms gap (the export counted)")
        for _ in range(3):  # the "Export Done" menu
            if rig.ui_name() in rig.ui_names.values():
                break
            inp.button("BACK", True)
            rig.tm(0.05, "BACK")
            inp.button("BACK", False)
            rig.tm(0.3, "menu closed")
        res["problems"] = list(rig.problems)
    except su.Stop:
        res["problems"] = list(rig.problems)
    if res.get("problems"):
        fails.append(f"(6) {res['problems'][:3]}")
    # A second boot from the same card: the mode as the shortcut left it, read from the community settings file
    rig2, _ = boot(a, image)
    res["second_boot"] = s6 = state(rig2)
    if s6["mode"] != 1:
        fails.append(f"(5) after a restart the monitor is {s6['mode']}, not On (1): not saved")
    if rig2.problems:
        fails.append(f"(6) second boot: {rig2.problems[:3]}")
    print(json.dumps(res, indent=1, default=str))
    json.dump(res, open(os.path.join(a.out, "cpu_monitor.json"), "w"), indent=1, default=str)
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
