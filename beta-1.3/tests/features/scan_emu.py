#!/usr/bin/env python3
"""Scan ported from mastertune 1.2.1 (features/0004: Settings > Tuning > Scan, a tuner, tempo meter and key finder on
the audio input) in the v1.3 build, with the v1.3 rig (rig13.py: the firmware's own task manager, the audio DMA in
real time) and the fuzzer's card, driven as the user would (the select encoder turned and pressed through v1.3's
encoder task, the buttons through Buttons::buttonAction()). mastertune's own test (mastertune-1.2.1/tests/scan/
scan_view_emu.py) with the same tone and chord.

The input: nothing in the emulator fills the SSI's receive ring (ssiRxBuffer: 2048 stereo frames of Q31), but the
firmware reads it round and round as it outputs. So the test writes a tone whose period divides the ring (40 periods
in 2048 frames: 861.328 Hz, 3 harmonics, peak 0.5), which reads as one steady tone wherever the firmware reads. With
the master tune at 440 Hz that is A4 (MIDI 81 in the Deluge's names) -37.1 cents; at 432 Hz A4 -5.4 cents. What the
OLED shows is taken from Canvas::drawString() on OLED::main per ScanView::renderOLED() call (title, big line, small
line).

  (1) boot: Song view; the tone into the input ring
  (2) Settings > Tuning > Scan (the select encoder turned to it and pressed): the root UI is scanView, the analysis
      allocated (AudioEngine::scan is the view's)
  (3) 1.5 s at 440.0 Hz: the title "Scan <input>", the big line "A4 -37.1" (+-0.3 cents), the small line
      "861.33 Hz  -- BPM" (+-0.05 Hz)
  (4) SHIFT + select encoder pressed: Settings over the Scan view; Tuning > Master tune turned to 432.0 Hz, BACK out:
      back in the Scan view at 4320 tenths; 1.5 s: the title with 432, "A4 -5.4"
  (5) the select encoder pressed: Hz big ("861.33 Hz"); again: "-- BPM" (a steady tone has no beat; the tempo
      analysis ran); again: the key, "--" (a held note is no key); then a C major chord in the ring and the select
      encoder turned (the input Left, the analysis anew): within 10 s "Scan Left 432", "C major",
      "Camelot 8B  -- BPM"
  (6) BACK: Song view again, AudioEngine::scan and the view's scan_ null, the analysis freed (delugeDealloc());
      again with Settings opened over the view and left with SONG (v1.3 goes straight to Song view): the same
  (7) throughout: no crash, freeze, hang, error popup or access outside RAM and the peripherals
On the build without Scan (s3) there is no Settings > Tuning > Scan: (2) fails.

Usage: scan_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (with the failed checks); exit 0/1."""
import argparse
import json
import math
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))  # beta-1.3/tests
import rig13  # noqa: E402,F401  (sets up the paths of mastertune's rig, patches song_emu for v1.3)
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP  # noqa: E402

RING_FRAMES, PERIODS, PEAK = 2048, 40, 0.5
TONE_HZ = 44100 * PERIODS / RING_FRAMES  # 861.328125
CENTS_TOLERANCE, HZ_TOLERANCE = 0.3, 0.05
CHORD_PERIODS = (6, 12, 15, 18)  # C3, C4, E4, G4 (just: 4:5:6), 129.2 to 387.6 Hz
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
TENTHS_HZ = "_ZN10MasterTune12_GLOBAL__N_18tenthsHzE"
DRAW_STRING = "_ZN6deluge3hid7display11oled_canvas6Canvas10drawStringESt17basic_string_view"
OLED_MAIN = "_ZN6deluge3hid7display4OLED4mainE"
SHOW = {0: "NOTE", 1: "HZ", 2: "BPM", 3: "KEY"}


def expected(tenths_hz):
    semitones = 69 + 12 * math.log2(TONE_HZ / (tenths_hz / 10))
    note = round(semitones)
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 2}", (semitones - note) * 100


def ring_bytes(x):
    return np.repeat((np.round(x * (1 << 23)).astype(np.int64) << 8).astype("<i4"), 2).tobytes()


def tone():
    phase = 2 * np.pi * PERIODS * np.arange(RING_FRAMES) / RING_FRAMES
    x = np.sin(phase) + 0.5 * np.sin(2 * phase + 0.3) + 0.25 * np.sin(3 * phase + 0.7)
    return ring_bytes(x * PEAK / np.max(np.abs(x)))


def chord():
    x = np.zeros(RING_FRAMES)
    for periods in CHORD_PERIODS:
        phase = 2 * np.pi * periods * np.arange(RING_FRAMES) / RING_FRAMES
        x += np.sin(phase) + 0.5 * np.sin(2 * phase + 0.3) + 0.25 * np.sin(3 * phase + 0.7)
    return ring_bytes(x * PEAK / np.max(np.abs(x)))


class Screen:
    """Per ScanView::renderOLED() call, the strings drawn on OLED::main (Canvas::drawString(): the canvas in r0, the
    string_view in r1 (length) and r2, x in r3, y on the stack)."""

    def __init__(self, emu):
        self.emu = emu
        self.main = emu.sym[OLED_MAIN]
        self.frames = []
        emu.intercept(emu.sym.find("_ZN8ScanView10renderOLED"), self.on_render)
        emu.intercept(emu.sym.find(DRAW_STRING), self.on_draw)

    def on_render(self, e):
        self.frames.append([])

    def on_draw(self, e):
        uc = e.uc
        if uc.reg_read(UC_ARM_REG_R0) != self.main or not self.frames:
            return
        n, p = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)
        text = bytes(uc.mem_read(p, n)).decode(errors="replace") if 0 < n < 100 else ""
        y = struct.unpack("<i", uc.mem_read(uc.reg_read(UC_ARM_REG_SP), 4))[0]
        self.frames[-1].append((y, text))

    def last(self, since):
        """(title, big, small) of the last frame with three lines drawn after frame index since, or None."""
        frames = [f for f in self.frames[since:] if len(f) == 3]
        return tuple(t for _, t in frames[-1]) if frames else None


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
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    inp = fuzz_ui.Inputs(rig, "v13")
    emu, sym = rig.emu, rig.emu.sym

    def tm(s, what="run"):
        rig.tm(s, what)

    def press(name, settle=0.15):
        inp.button(name, True)
        tm(0.03, f"{name} pressed")
        inp.button(name, False)
        tm(settle, f"{name} released")

    def popups_since(n):
        return [p[3] for p in rig.popups[n:]]

    try:
        if "scanView" not in sym.by_name or "scanViewMenu" not in sym.by_name:
            fails.append("(2) no Settings > Tuning > Scan in this build")
            raise su.Stop("no Scan")
        rig.ui_names[sym["scanView"]] = "scanView"
        view, engine_sym = sym["scanView"], sym["_ZN11AudioEngine4scanE"]
        offs = su.gdb_ints(emu, [
            "print (int)&scanView.scan_ - (int)&scanView", "print (int)&scanView.show_ - (int)&scanView",
            "print (int)&AudioEngine::scan->written_ - (int)AudioEngine::scan", "print (int)&AudioEngine::scan->bpm_ - (int)AudioEngine::scan",
            "print (int)&AudioEngine::scan->keyTonic_ - (int)AudioEngine::scan", "print (int)&AudioEngine::scan->keyMinor_ - (int)AudioEngine::scan",
            "print (int)&settingsRootMenu.current_item_ - (int)&settingsRootMenu",
            "print (int)&soundEditor.menuItemNavigationRecord - (int)&soundEditor",
            "print (int)&soundEditor.navigationDepth - (int)&soundEditor"])
        o_scan, o_show, o_written, o_bpm, o_tonic, o_minor, o_cur, o_rec, o_depth = offs
        screen = Screen(emu)
        counts = dict(tempo=0)
        freed = []
        emu.intercept(sym.find("_ZN6deluge3dsp4Scan13tempoEstimateEv"),
                      lambda e: counts.__setitem__("tempo", counts["tempo"] + 1))
        emu.intercept(sym["delugeDealloc"], lambda e: freed.append(e.uc.reg_read(UC_ARM_REG_R0)) and None)
        emu.uc.ctl_flush_tb()
        editor = sym["soundEditor"]

        def scan_ptrs():
            return emu.u32(engine_sym), emu.u32(view + o_scan)

        def menu_item():
            return emu.u32(editor + o_rec + 4 * emu.u8(editor + o_depth))

        def turn_to(submenu, item, limit=30):
            for direction in (1, -1):
                for _ in range(limit):
                    if emu.u32(emu.u32(submenu + o_cur)) == item:
                        return True
                    inp.turn("select", direction)
                    tm(0.05, "select encoder")
            return emu.u32(emu.u32(submenu + o_cur)) == item

        def title(tenths):
            line, mic = emu.u8(sym["_ZN11AudioEngine15lineInPluggedInE"]), emu.u8(sym["_ZN11AudioEngine12micPluggedInE"])
            name = "Line" if line else "Mic in" if mic else "Mic"
            return f"Scan {name}" + ("" if tenths == 4400 else f" {tenths // 10}")

        def check_note(tag, lines, tenths):
            name, cents = expected(tenths)
            m = re.fullmatch(r"(\S+) ([+ -]\d+\.\d)", lines[1]) if lines else None
            ok = bool(m) and m.group(1) == name and abs(float(m.group(2).replace(" ", "")) - cents) <= CENTS_TOLERANCE
            if not ok:
                fails.append(f"({tag}) the big line {lines and lines[1]!r}, {name} {cents:+.1f} expected")

        def hz_in(text, pattern):
            m = re.fullmatch(pattern, text or "")
            return bool(m) and abs(float(m.group(1)) - TONE_HZ) <= HZ_TOLERANCE

        # (1)
        tm(0.5, "settle")
        res["boot_ui"] = rig.ui_name(root=True)
        if res["boot_ui"] != "sessionView":
            fails.append(f"(1) booted into {res['boot_ui']}, not Song view")
        ring, size = sym.by_name["ssiRxBuffer"]
        data = tone()
        if size != len(data):
            fails.append(f"(1) ssiRxBuffer is {size} bytes, {len(data)} expected")
        emu.uc.mem_write(ring, data)

        # (2) Settings > Tuning > Scan
        rig.action("settings", sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], editor, 0, sym["settingsRootMenu"], 0)
        rig.action("settings open", sym["_Z6openUIP2UI"], editor)
        tm(0.2, "settings open")
        ok = turn_to(sym["settingsRootMenu"], sym["tuningSubmenu"])
        press("SELECT_ENC")
        ok = ok and menu_item() == sym["tuningSubmenu"] and turn_to(sym["tuningSubmenu"], sym["scanViewMenu"], 4)
        res["menu_to_scan"] = ok
        if not ok:
            fails.append("(2) Settings > Tuning > Scan not found")
            raise su.Stop("menu")
        frames0 = len(screen.frames)
        press("SELECT_ENC", 0.2)
        engine, own = scan_ptrs()
        res["after_open"] = dict(root=rig.ui_name(root=True), current=rig.ui_name(), engine=hex(engine), own=hex(own))
        if rig.ui_name(root=True) != "scanView" or rig.ui_name() != "scanView":
            fails.append(f"(2) not in the Scan view: {res['after_open']}")
            raise su.Stop("not open")
        if not engine or engine != own:
            fails.append(f"(2) the analysis isn't the audio engine's: {res['after_open']}")

        # (3) at 440
        tm(1.5, "Scan at 440")
        lines = screen.last(frames0)
        res["at_440"] = lines
        if not lines or lines[0] != title(4400):
            fails.append(f"(3) title {lines and lines[0]!r}, {title(4400)!r} expected")
        check_note("3", lines, 4400)
        if not hz_in(lines and lines[2], r"([\d.]+) Hz  \S+ BPM"):
            fails.append(f"(3) small line {lines and lines[2]!r}")

        # (4) SHIFT + select: Settings over the view, Master tune 432.0
        inp.button("SHIFT", True)
        tm(0.03, "SHIFT")
        press("SELECT_ENC")
        inp.button("SHIFT", False)
        tm(0.3, "settings")
        res["settings_over_scan"] = dict(current=rig.ui_name(), item=menu_item() == sym["settingsRootMenu"])
        if rig.ui_name() != "soundEditor" or menu_item() != sym["settingsRootMenu"]:
            fails.append(f"(4) SHIFT + select didn't open Settings: {res['settings_over_scan']}")
            raise su.Stop("settings")
        turn_to(sym["settingsRootMenu"], sym["tuningSubmenu"])
        press("SELECT_ENC")
        turn_to(sym["tuningSubmenu"], sym["masterTuneMenu"], 4)
        press("SELECT_ENC")
        inp.turn("select", -8)
        tm(0.1, "master tune turned")
        for _ in range(3):
            if rig.ui_name() == "scanView":
                break
            press("BACK", 0.4)
        frames0 = len(screen.frames)
        tenths = emu.u32(sym.find(TENTHS_HZ))
        res["back_at_432"] = dict(current=rig.ui_name(), root=rig.ui_name(root=True), tenths=tenths)
        if rig.ui_name() != "scanView" or tenths != 4320:
            fails.append(f"(4) not back in the Scan view at 432.0: {res['back_at_432']}")
        tm(1.5, "Scan at 432")
        lines = screen.last(frames0)
        res["at_432"] = lines
        if not lines or lines[0] != title(4320):
            fails.append(f"(4) title {lines and lines[0]!r}, {title(4320)!r} expected")
        check_note("4", lines, 4320)

        # (5) Hz, BPM, key; the chord
        name, _ = expected(4320)
        small_note = rf"{re.escape(name)} [+ -]\d+\.\d"
        n, frames0 = len(rig.popups), len(screen.frames)
        press("SELECT_ENC", 0.3)
        lines = screen.last(frames0)
        res["hz"] = dict(lines=lines, popups=popups_since(n), show=SHOW.get(emu.u8(view + o_show)))
        if not (hz_in(lines and lines[1], r"([\d.]+) Hz") and res["hz"]["show"] == "HZ" and "HZ" in res["hz"]["popups"]
                and re.fullmatch(small_note + r"  \S+ BPM", lines[2] if lines else "")):
            fails.append(f"(5) Hz: {res['hz']}")
        n, frames0, tempo0 = len(rig.popups), len(screen.frames), counts["tempo"]
        press("SELECT_ENC", 0.3)
        tm(1.0, "BPM")
        lines = screen.last(frames0)
        engine = scan_ptrs()[0]
        bpm = struct.unpack("<f", emu.uc.mem_read(engine + o_bpm, 4))[0] if engine else None
        res["bpm"] = dict(lines=lines, popups=popups_since(n), bpm=bpm, tempo_estimates=counts["tempo"] - tempo0)
        if not (lines and lines[1] == "-- BPM" and bpm == 0 and "BPM" in res["bpm"]["popups"]
                and counts["tempo"] > tempo0 and hz_in(lines[2], small_note + r"  ([\d.]+) Hz")):
            fails.append(f"(5) BPM: {res['bpm']}")
        n, frames0 = len(rig.popups), len(screen.frames)
        press("SELECT_ENC", 0.3)
        tm(1.0, "key")
        lines = screen.last(frames0)
        res["key_tone"] = dict(lines=lines, popups=popups_since(n))
        if not (lines and lines[1] == "--" and "KEY" in res["key_tone"]["popups"]):
            fails.append(f"(5) key of the tone: {res['key_tone']}")
        data = chord()
        emu.uc.mem_write(ring, data)
        fed0 = emu.u32(engine + o_written)
        n = len(rig.popups)
        inp.turn("select", 1)
        tm(0.03, "select encoder +1")
        fed1 = emu.u32(engine + o_written)
        res["input_left"] = dict(popups=popups_since(n), fed_before=fed0, fed_after=fed1)
        if "Left" not in res["input_left"]["popups"] or not fed1 < min(fed0, 2000):
            fails.append(f"(5) the input Left, the analysis anew: {res['input_left']}")
        frames0 = len(screen.frames)
        waited = 0.0
        while waited < 10:  # The key finder needs a few seconds of the chord (on the Deluge about 5 s)
            tm(0.5, "chord")
            waited += 0.5
            lines = screen.last(frames0)
            if lines and lines[1] != "--":
                break
        res["chord_after_s"] = waited
        tonic = struct.unpack("<i", emu.uc.mem_read(engine + o_tonic, 4))[0]
        minor = emu.u8(engine + o_minor)
        res["chord"] = dict(lines=lines, tonic=tonic, minor=minor)
        if not (lines == ("Scan Left 432", "C major", "Camelot 8B  -- BPM") and tonic == 0 and minor == 0):
            fails.append(f"(5) the C major chord: {res['chord']}")

        # (6) BACK
        engine = scan_ptrs()[0]
        nf = len(freed)
        press("BACK", 0.3)
        now, own = scan_ptrs()
        res["back"] = dict(root=rig.ui_name(root=True), current=rig.ui_name(), engine=hex(now), own=hex(own),
                           freed=engine in freed[nf:])
        if not (rig.ui_name(root=True) == "sessionView" and now == 0 and own == 0 and engine in freed[nf:]):
            fails.append(f"(6) BACK: {res['back']}")
        tm(0.5, "Song view")

        # (6b) the Scan view again, Settings over it (SHIFT + select), then SONG: v1.3 leaves the menu straight to Song
        # view (changeRootUI()), and the analysis must stop and be freed there too
        rig.action("settings", sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], editor, 0, sym["settingsRootMenu"], 0)
        rig.action("settings open", sym["_Z6openUIP2UI"], editor)
        tm(0.2, "settings open")
        turn_to(sym["settingsRootMenu"], sym["tuningSubmenu"])
        press("SELECT_ENC")
        turn_to(sym["tuningSubmenu"], sym["scanViewMenu"], 4)
        press("SELECT_ENC", 0.3)
        engine = scan_ptrs()[0]
        inp.button("SHIFT", True)
        tm(0.03, "SHIFT")
        press("SELECT_ENC")
        inp.button("SHIFT", False)
        tm(0.3, "settings over Scan")
        nf = len(freed)
        over = rig.ui_name()
        press("SONG", 0.3)
        now, own = scan_ptrs()
        res["song_out_of_menu"] = dict(scan_open=bool(engine), menu=over, root=rig.ui_name(root=True), engine=hex(now),
                                       own=hex(own), freed=engine in freed[nf:])
        if not (engine and over == "soundEditor" and rig.ui_name(root=True) == "sessionView" and now == 0 and own == 0
                and engine in freed[nf:]):
            fails.append(f"(6b) SONG out of Settings over the Scan view: {res['song_out_of_menu']}")
        tm(0.5, "Song view")
    except su.Stop:
        pass
    res["problems"] = list(rig.problems)
    res["error_popups"] = rig.error_popups()
    res["invalid"] = {str(k): v for k, v in rig.invalid.items()}
    if rig.problems or rig.error_popups() or rig.invalid:
        fails.append(f"(7) {rig.problems[:3]} {rig.error_popups()[:3]} {list(res['invalid'])[:3]}")
    print(json.dumps(res, indent=1, default=str))
    json.dump(res, open(os.path.join(a.out, "scan.json"), "w"), indent=1, default=str)
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
