#!/usr/bin/env python3
"""The Scan view (mastertune-v18.4: Settings > Tuning > Scan, a tuner, tempo meter and key finder on the audio input)
on the real firmware in the emulator, driven as the user would: tests/stress/ui's Rig (booted with the OLED, the startup song
loaded, the firmware's own task manager, the output DMA in real time, the card instant), the menu and the buttons as
tests/screenshots/oled_screens_emu.py drives them (SoundEditor::selectEncoderAction(), Buttons::buttonAction()).

The input: in the emulator nothing fills the SSI's receive ring (ssiRxBuffer: 2048 stereo frames of Q31, the codec's
24 bits at the top) and its DMA stands still, but the firmware reads the ring round and round as it outputs
(AudioEngine::slowRoutine() keeps the read position within its window behind the DMA's, which here doesn't move: it
jumps most of a lap ahead every ~128 frames, so the analysis gets the ring ~13x faster than real time, always
contiguous). So the test writes a tone whose period divides the ring: 40 periods in 2048 frames, 44100 * 40 / 2048
= 861.328 Hz (3 harmonics, peak 0.5 of full scale, both channels, 512 frames tiled: it repeats exactly every 512),
which reads as one steady tone wherever and however far the firmware reads. With the master tune at 440 Hz that is
A4 (MIDI 81 in the Deluge's names, 880 Hz) -37.1 cents; at 432 Hz A4 -5.4 cents.

What the OLED shows is taken from Canvas::drawString() on the main canvas, per ScanView::renderOLED() call (the title,
the big line, the small line); the images the OLED got (OLED::oledCurrentImage) are saved as <out>/oled_scan_*.png
(3x, written without PIL) and .txt ('#' lit), as songchange_emu.py's save_oled() writes them.

Checks:
1. boot with the OLED, make_sd.py's song (2 synths, its kit, audio track and drone) loaded: Song view
2. the input ring filled with the tone
3. Settings > Tuning > Scan (the select encoder turned to it and pressed): the root UI is scanView, the analysis is
   allocated (AudioEngine::scan is the view's)
4. 1.5 s of the task manager: the title "Scan <input>" (Auto names the jack detected: the emulator's GPIO reads 0,
   which the firmware takes as the mic plugged in: "Scan Mic in", the channels mixed), the big line "A4 -37.1"
   (+-0.3 cents), the small one "861.33 Hz  -- BPM" (+-0.05 Hz)
5. SHIFT + select encoder (Settings, from the Scan view), Tuning > Master tune turned down to 432.0 Hz, BACK out of
   the menu: back in the Scan view, MasterTune's tenthsHz 4320; 1.5 s: the title with 432, "A4 -5.4" (+-0.3)
6. the select encoder pressed: the big line in Hz ("861.33 Hz"); again: BPM, "-- BPM" for the steady tone after the
   tempo analysis has run (Scan::tempoEstimate() called); again: the key, "--" for the tone (a held note is no key);
   then a C major chord in the ring (C3 and C4 E4 G4, whole periods: 6, 12, 15 and 18 in 2048 frames, 129.2 to 387.6
   Hz, a just major triad a little flat of 440's C, E and G) and the select encoder turned to the input Left (the
   popup "Left", the analysis anew: Scan::reset()); 1.5 s: "Scan Left 432", "C major", "Camelot 8B  -- BPM",
   Scan::keyTonic() 0; turned back to Auto, pressed again: the note (the modes go round)
7. BACK: the root UI is sessionView again, AudioEngine::scan and the view's scan_ null, the analysis freed
   (delugeDealloc() on it)
Throughout: no crash, fault, hang, error popup or access outside RAM and the peripherals (the Rig's records); the
input ring still holds the chord at the end (nothing in the emulator writes it).

Usage: scan_view_emu.py <deluge.elf> [--out DIR] [--tools PREFIX] [--build DIR] [--keep-image]
  --build: where blockcount.so is (default $BLOCKCOUNT_DIR, else the out dir; built there if missing).
Needs python3 with unicorn 2 and numpy, a C compiler (blockcount.c), the toolchain's gdb. About 1.5 minutes.
Exit status 0 when all checks pass.
"""
import argparse
import math
import os
import re
import struct
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.normpath(os.path.join(HERE, ".."))
for d in ("song", "songchange", "browser", "stress/ui", "screenshots"):
    sys.path.insert(0, os.path.join(TESTS, d))
import fat32  # noqa: E402
import make_sd  # noqa: E402
import oled_screens_emu as screens  # noqa: E402
import song_emu as se  # noqa: E402
import stress_ui_emu as ui  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP  # noqa: E402

SHIFT = ui.button_xy(8, 0)  # shiftButtonCoord
RING_FRAMES, PERIODS, PEAK = 2048, 40, 0.5  # SSI_RX_BUFFER_NUM_SAMPLES; the tone's periods in it; its peak
TONE_HZ = 44100 * PERIODS / RING_FRAMES  # 861.328125
CENTS_TOLERANCE, HZ_TOLERANCE = 0.3, 0.05
CHORD_PERIODS = (6, 12, 15, 18)  # C3, C4, E4, G4 (just intonation: 4:5:6), 129.2 to 387.6 Hz
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SCAN_ENGINE = "_ZN11AudioEngine4scanE"  # AudioEngine::scan
TENTHS_HZ = "_ZN10MasterTune12_GLOBAL__N_18tenthsHzE"  # MasterTune's tenthsHz (LTO adds a suffix)
DRAW_STRING = "_ZN6deluge3hid7display11oled_canvas6Canvas10drawStringESt17basic_string_view"
SHOW = {0: "NOTE", 1: "HZ", 2: "BPM", 3: "KEY"}  # ScanView::Show
MIC_IN, LINE_IN = "_ZN11AudioEngine12micPluggedInE", "_ZN11AudioEngine15lineInPluggedInE"

failures = 0
checks = 0


def log(s):
    print(s, flush=True)


def check(what, ok, detail=""):
    global failures, checks
    checks += 1
    failures += not ok
    log(f"  {'ok  ' if ok else 'FAIL'} {what}" + (f": {detail}" if detail else ""))
    return ok


def expected(tenths_hz):
    """(note name, cents) of the tone at that master tune: the nearest note, as ScanView::noteOf() and
    noteCodeToString() (C-2 is MIDI 0) name it."""
    semitones = 69 + 12 * math.log2(TONE_HZ / (tenths_hz / 10))
    note = round(semitones)
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 2}", (semitones - note) * 100


def tone():
    """The ring's content: 2048 stereo frames (int32, the 24 bits at the top), left = right."""
    phase = 2 * np.pi * PERIODS * np.arange(512) / RING_FRAMES  # 10 periods
    x = np.sin(phase) + 0.5 * np.sin(2 * phase + 0.3) + 0.25 * np.sin(3 * phase + 0.7)
    x *= PEAK / np.max(np.abs(x))
    frames = np.tile(np.round(x * (1 << 23)).astype(np.int64) << 8, RING_FRAMES // 512).astype("<i4")
    return np.repeat(frames, 2).tobytes()


def chord():
    """A C major chord for the ring: C3 and C4 E4 G4 as 6, 12, 15 and 18 periods in it (harmonics as tone()'s)."""
    x = np.zeros(RING_FRAMES)
    for periods in CHORD_PERIODS:
        phase = 2 * np.pi * periods * np.arange(RING_FRAMES) / RING_FRAMES
        x += np.sin(phase) + 0.5 * np.sin(2 * phase + 0.3) + 0.25 * np.sin(3 * phase + 0.7)
    x *= PEAK / np.max(np.abs(x))
    return np.repeat((np.round(x * (1 << 23)).astype(np.int64) << 8).astype("<i4"), 2).tobytes()


class Screen:
    """What ScanView::renderOLED() draws: per call, the strings drawn on OLED::main (Canvas::drawString(): the canvas
    in r0, the string_view in r1 (length) and r2, x in r3, y and the height on the stack), as songchange_emu.py
    records them."""

    def __init__(self, emu):
        self.emu = emu
        self.main = emu.sym[screens.OLED_MAIN]
        self.frames = []  # [dict(t=emulated s, lines=[(x, y, height, text)])]
        emu.intercept(emu.sym.find("_ZN8ScanView10renderOLED"), self.on_render)
        emu.intercept(emu.sym.find(DRAW_STRING), self.on_draw)

    def on_render(self, e):
        self.frames.append(dict(t=e.seconds(), lines=[]))

    def on_draw(self, e):
        uc = e.uc
        if uc.reg_read(UC_ARM_REG_R0) != self.main or not self.frames:
            return
        n, p = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)
        text = bytes(uc.mem_read(p, n)).decode(errors="replace") if 0 < n < 100 else ""
        sp = uc.reg_read(UC_ARM_REG_SP)
        y, _, height = struct.unpack("<iii", uc.mem_read(sp, 12))
        x = struct.unpack("<i", struct.pack("<I", uc.reg_read(UC_ARM_REG_R3)))[0]
        self.frames[-1]["lines"].append((x, y, height, text))

    def last(self, since):
        """(title, big, small) of the last frame drawn after frame index since, or None."""
        frames = [f for f in self.frames[since:] if len(f["lines"]) == 3]
        return tuple(line[3] for line in frames[-1]["lines"]) if frames else None


class ScanRig:
    def __init__(self, a, image):
        self.rig = rig = ui.Rig(a.elf, a.tools, a.build, image, "instant")
        self.emu = emu = rig.emu
        sym = self.sym = emu.sym
        rig.action("CPU monitor off", sym.find("_ZN9cpu_stats7setModeEh"), 0)  # The Rig's CpuStats switched it on
        rig.ui_names[sym["scanView"]] = "scanView"
        self.view = sym["scanView"]
        (self.scan_off, self.show_off, self.written_off, self.pitch_off, self.bpm_off, self.onsets_off,
         self.tonic_off, self.minor_off, self.scan_size) = se.gdb_values(emu, [
             "(int)&scanView.scan_ - (int)&scanView", "(int)&scanView.show_ - (int)&scanView",
             "(int)&AudioEngine::scan->written_ - (int)AudioEngine::scan",
             "(int)&AudioEngine::scan->pitchHz_ - (int)AudioEngine::scan",
             "(int)&AudioEngine::scan->bpm_ - (int)AudioEngine::scan",
             "(int)&AudioEngine::scan->onsetCount_ - (int)AudioEngine::scan",
             "(int)&AudioEngine::scan->keyTonic_ - (int)AudioEngine::scan",
             "(int)&AudioEngine::scan->keyMinor_ - (int)AudioEngine::scan", "sizeof(*AudioEngine::scan)"])
        self.screen = Screen(emu)
        self.counts = dict(tempo=0, pitch=0)
        self.freed = []  # delugeDealloc()'s pointers
        emu.intercept(sym.find("_ZN6deluge3dsp4Scan13tempoEstimateEv"), lambda e: self.count("tempo"))
        emu.intercept(sym.find("_ZN6deluge3dsp4Scan10pitchFrameEPKf"), lambda e: self.count("pitch"))
        emu.intercept(sym["delugeDealloc"], lambda e: self.freed.append(e.uc.reg_read(UC_ARM_REG_R0)) and None)
        emu.uc.ctl_flush_tb()

    def count(self, what):
        self.counts[what] += 1

    def f32(self, address):
        return struct.unpack("<f", self.emu.uc.mem_read(address, 4))[0]

    def scan(self):
        """AudioEngine::scan, the view's scan_"""
        return self.emu.u32(self.sym[SCAN_ENGINE]), self.emu.u32(self.view + self.scan_off)

    def analysis(self):
        s = self.scan()[0]
        if not s:
            return "no analysis"
        return (f"the analysis: {self.emu.u32(s + self.written_off):,} samples fed at 22.05 kHz, pitch "
                f"{self.f32(s + self.pitch_off):.4f} Hz, bpm {self.f32(s + self.bpm_off):g}, "
                f"{self.emu.u32(s + self.onsets_off)} onset values, {self.counts['pitch']} pitch frames, "
                f"{self.counts['tempo']} tempo estimates")

    def show(self):
        return SHOW.get(self.emu.u8(self.view + self.show_off), "?")

    def title(self, tenths_hz):
        """The title ScanView::renderOLED() should draw: the input (Auto: named after the jack detected) and the master
        tune when it isn't 440.0 Hz."""
        line, mic = self.emu.u8(self.sym[LINE_IN]), self.emu.u8(self.sym[MIC_IN])
        name = "Line" if line else "Mic in" if mic else "Mic"
        return f"Scan {name}" + ("" if tenths_hz == 4400 else f" {tenths_hz // 10}")

    def press(self, b, settle_s=0.1):
        self.rig.button(b, True)
        self.rig.button(b, False)
        self.rig.tm(settle_s)


def check_note(what, lines, tenths_hz):
    """The big line in the note mode: the note and its cents."""
    name, cents = expected(tenths_hz)
    want = f"{name} {cents:+.1f}"
    m = re.fullmatch(r"(\S+) ([+ -]\d+\.\d)", lines[1]) if lines else None
    ok = bool(m) and m.group(1) == name and abs(float(m.group(2).replace(" ", "")) - cents) <= CENTS_TOLERANCE
    exact = " exactly" if lines and lines[1] == want else ""
    return check(f"{what}: the big line {want!r}{exact} (+-{CENTS_TOLERANCE} cents)", ok,
                 f"drawn {lines[1] if lines else None!r} (cents {cents:.2f})")


def check_hz(what, text, pattern):
    """A line with the frequency in it: 861.33 Hz, +-0.05."""
    m = re.fullmatch(pattern, text or "")
    ok = bool(m) and abs(float(m.group(1)) - TONE_HZ) <= HZ_TOLERANCE
    exact = " exactly" if m and m.group(1) == f"{TONE_HZ:.2f}" else ""
    return check(f"{what}{exact} (+-{HZ_TOLERANCE} Hz)", ok, f"drawn {text!r} (the tone {TONE_HZ:.4f} Hz)")


def run(a, out):
    image = os.path.join(out, "scan.img")
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 2).encode()
    fat32.build(image, files)
    log("== 1. boot with the OLED, the song loaded")
    t0 = time.time()
    try:
        s = ScanRig(a, image)
    except ui.Stop as e:
        check("boot and the song loaded", False, f"{e}")
        return
    try:
        steps(a, out, s, t0)
    except ui.Stop as e:
        check("no crash, fault or hang", False, f"in {e}: {s.rig.problems}")
    if not a.keep_image:
        os.remove(image)


def steps(a, out, s, t0):
    rig, emu, sym, screen = s.rig, s.emu, s.sym, s.screen
    shot = screens.Oled(emu, out)
    rig.tm(0.5)
    check("Song view", rig.ui_name(root=True) == "sessionView",
          f"{rig.song_name()!r} in {rig.ui_name(root=True)} ({time.time() - t0:.0f} s)")

    log("== 2. the tone into the input ring")
    ring, size = sym.by_name["ssiRxBuffer"]
    data = tone()
    emu.uc.mem_write(ring, data)
    check(f"ssiRxBuffer is {RING_FRAMES} stereo frames of int32, filled with {TONE_HZ:.3f} Hz", size == len(data),
          f"{size} bytes")

    log("== 3. Settings > Tuning > Scan")
    menu = screens.Settings(rig)
    menu.open()
    menu.enter("tuningSubmenu")
    submenu, item = menu.item(), sym["scanViewMenu"]
    for _ in range(4):
        if emu.u32(emu.u32(submenu + menu.cur)) == item:
            break
        menu.turn(1)
    shown = emu.u32(emu.u32(submenu + menu.cur)) == item
    check("the item Scan in Tuning (from Song view)", shown)
    if not shown:
        return
    frames0 = len(screen.frames)
    menu.press(ui.SELECT_ENC)
    engine, own = s.scan()
    check("the root UI is scanView, the menu closed",
          rig.ui_name(root=True) == "scanView" and rig.ui_name() == "scanView",
          f"root {rig.ui_name(root=True)}, current {rig.ui_name()}")
    check("the analysis allocated: AudioEngine::scan is the view's", engine and engine == own,
          f"AudioEngine::scan {engine:#x}, scanView.scan_ {own:#x} ({s.scan_size} bytes)")
    if not engine:
        return

    log("== 4. 1.5 s at the master tune 440.0 Hz")
    rig.tm(1.5)
    lines = screen.last(frames0)
    log(f"  drawn: {lines}; {s.analysis()}")
    title = s.title(4400)
    check(f"the title {title!r}", lines and lines[0] == title, f"{lines and lines[0]!r}")
    check_note("440 Hz", lines, 4400)
    check_hz("440 Hz: the small line '861.33 Hz  ...'", lines and lines[2], r"([\d.]+) Hz  \S+ BPM")
    screens.quiet(rig)
    shot.save_oled("scan_note_440")

    log("== 5. SHIFT + select encoder: Settings > Tuning > Master tune 432.0 Hz, BACK to the Scan view")
    rig.button(SHIFT, True)
    rig.button(ui.SELECT_ENC, True)
    rig.button(ui.SELECT_ENC, False)
    rig.button(SHIFT, False)
    rig.tm(0.3)
    opened = rig.ui_name() == "soundEditor" and menu.item() == sym["settingsRootMenu"]
    check("Settings open over the Scan view", opened, f"current {rig.ui_name()}, root {rig.ui_name(root=True)}")
    if not opened:
        return
    menu.enter("tuningSubmenu")
    menu.enter("masterTuneMenu")
    for _ in range(8):
        menu.turn(-1)
    for _ in range(3):
        menu.press(ui.BACK)
    frames0 = len(screen.frames)
    tenths = emu.u32(sym.find(TENTHS_HZ))
    back = rig.ui_name() == "scanView" and rig.ui_name(root=True) == "scanView"
    check("back in the Scan view, the master tune 432.0 Hz", back and tenths == 4320,
          f"current {rig.ui_name()}, root {rig.ui_name(root=True)}, tenthsHz {tenths}")
    rig.tm(1.5)
    lines = screen.last(frames0)
    log(f"  drawn: {lines}; {s.analysis()}")
    title = s.title(4320)
    check(f"the title {title!r}", lines and lines[0] == title, f"{lines and lines[0]!r}")
    check_note("432 Hz", lines, 4320)
    check_hz("432 Hz: the small line '861.33 Hz  ...'", lines and lines[2], r"([\d.]+) Hz  \S+ BPM")
    screens.quiet(rig)
    shot.save_oled("scan_note_432")

    log("== 6. the select encoder pressed: Hz, then BPM")
    name, _ = expected(4320)
    small_note = rf"{re.escape(name)} [+ -]\d+\.\d"
    n = len(rig.popups)
    frames0 = len(screen.frames)
    s.press(ui.SELECT_ENC)
    screens.quiet(rig)
    lines = screen.last(frames0)
    popups = [p[3] for p in rig.popups[n:]]
    log(f"  drawn: {lines}, popups {popups}")
    check_hz("the big line in Hz: '861.33 Hz'", lines and lines[1], r"([\d.]+) Hz")
    small = lines[2] if lines else ""
    check("the small line: the note and the BPM, the popup 'HZ'",
          s.show() == "HZ" and "HZ" in popups and bool(re.fullmatch(small_note + r"  \S+ BPM", small)),
          f"show {s.show()}, small {small!r}")
    shot.save_oled("scan_hz")
    n = len(rig.popups)
    frames0 = len(screen.frames)
    tempo0 = s.counts["tempo"]
    s.press(ui.SELECT_ENC)
    rig.tm(1.0)
    screens.quiet(rig)
    lines = screen.last(frames0)
    popups = [p[3] for p in rig.popups[n:]]
    log(f"  drawn: {lines}, popups {popups}; {s.analysis()}")
    engine = s.scan()[0]
    bpm = s.f32(engine + s.bpm_off) if engine else None
    check("the big line '-- BPM' (a steady tone has no beat), the popup 'BPM'",
          lines and lines[1] == "-- BPM" and s.show() == "BPM" and "BPM" in popups and bpm == 0,
          f"big {lines and lines[1]!r}, show {s.show()}, Scan::bpm() {bpm}")
    check("the tempo analysis ran meanwhile", s.counts["tempo"] > tempo0,
          f"{s.counts['tempo'] - tempo0} tempo estimates in this step, {s.counts['tempo']} in all")
    check_hz("the small line: the note and '861.33 Hz'", lines and lines[2], small_note + r"  ([\d.]+) Hz")
    shot.save_oled("scan_bpm")

    log("== 6b. the select encoder pressed: the key, none for the tone; a C major chord")
    n = len(rig.popups)
    frames0 = len(screen.frames)
    s.press(ui.SELECT_ENC)
    rig.tm(1.0)
    screens.quiet(rig)
    lines = screen.last(frames0)
    popups = [p[3] for p in rig.popups[n:]]
    log(f"  drawn: {lines}, popups {popups}")
    check("the big line '--' (a held note is no key), the small 'Input ... dB  -- BPM', the popup 'KEY'",
          lines and lines[1] == "--" and bool(re.fullmatch(r"Input -?\d+ dB  -- BPM", lines[2])) and s.show() == "KEY"
          and "KEY" in popups, f"big {lines and lines[1]!r}, small {lines and lines[2]!r}, show {s.show()}")
    # The chord, and the input turned to Left (both channels hold it): the analysis starts anew, without the tone
    data = chord()
    emu.uc.mem_write(ring, data)
    engine = s.scan()[0]
    fed0 = emu.u32(engine + s.written_off) if engine else 0
    n = len(rig.popups)
    rig.action("select encoder +1", sym.find("_ZN8ScanView19selectEncoderActionEa"), s.view, 1)
    fed1 = emu.u32(engine + s.written_off) if engine else 0
    popups = [p[3] for p in rig.popups[n:]]
    check("the select encoder turned: the input Left, the analysis anew (Scan::reset(): nothing fed yet)",
          "Left" in popups and fed1 < fed0 and fed1 < 1000,
          f"popups {popups}, samples fed {fed0:,} before, {fed1:,} after")
    frames0 = len(screen.frames)
    rig.tm(1.5)
    screens.quiet(rig)
    lines = screen.last(frames0)
    tonic = struct.unpack("<i", emu.uc.mem_read(engine + s.tonic_off, 4))[0] if engine else None
    minor = emu.u8(engine + s.minor_off) if engine else None
    log(f"  drawn: {lines}; key tonic {tonic}, minor {minor}; {s.analysis()}")
    check("the C major chord: the title 'Scan Left 432', the big line 'C major', the small 'Camelot 8B  -- BPM', "
          "Scan::keyTonic() 0 (major)",
          lines and lines[0] == "Scan Left 432" and lines[1] == "C major" and lines[2] == "Camelot 8B  -- BPM"
          and tonic == 0 and minor == 0,
          f"{lines}, tonic {tonic}, minor {minor}")
    shot.save_oled("scan_key")
    rig.action("select encoder -1", sym.find("_ZN8ScanView19selectEncoderActionEa"), s.view, -1)  # Auto again
    n = len(rig.popups)
    s.press(ui.SELECT_ENC)
    screens.quiet(rig)
    popups = [p[3] for p in rig.popups[n:]]
    check("pressed again: the note (the modes go round), the popup 'NOTE'", s.show() == "NOTE" and "NOTE" in popups,
          f"show {s.show()}, popups {popups}")

    log("== 7. BACK: Song view")
    engine = s.scan()[0]
    freed0 = len(s.freed)
    s.press(ui.BACK, 0.3)
    now, own = s.scan()
    check("the root UI is sessionView again",
          rig.ui_name(root=True) == "sessionView" and rig.ui_name() == "sessionView",
          f"root {rig.ui_name(root=True)}, current {rig.ui_name()}")
    check("the analysis freed: AudioEngine::scan and scanView.scan_ null, delugeDealloc() on it",
          now == 0 and own == 0 and engine in s.freed[freed0:],
          f"AudioEngine::scan {now:#x}, scan_ {own:#x}, freed {[hex(p) for p in s.freed[freed0:]]} "
          f"(the analysis was {engine:#x})")

    log("== throughout")
    check("no error popup", not rig.error_popups(), f"{rig.error_popups()}")
    check("no access outside RAM and the peripherals", not rig.invalid, f"{dict(rig.invalid)}")
    check("no crash, fault or hang", not rig.problems, f"{rig.problems}")
    check("the input ring still holds the chord", bytes(emu.uc.mem_read(ring, size)) == data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "scan_view"))
    ap.add_argument("--tools")
    ap.add_argument("--build", default=os.environ.get("BLOCKCOUNT_DIR"))
    ap.add_argument("--keep-image", action="store_true", help="keep the card image in the out dir")
    a = ap.parse_args()
    a.elf, a.out = os.path.abspath(a.elf), os.path.abspath(a.out)
    a.tools = a.tools or os.path.join(os.path.dirname(a.elf),
                                      "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    a.build = os.path.abspath(a.build or a.out)
    os.makedirs(a.out, exist_ok=True)
    if not os.path.exists(os.path.join(a.build, "blockcount.so")):
        import unicorn
        uc = os.path.dirname(unicorn.__file__)
        subprocess.run(["cc", "-O2", "-shared", "-fPIC", f"-I{uc}/include", os.path.join(TESTS, "song", "blockcount.c"),
                        "-o", os.path.join(a.build, "blockcount.so"), f"-L{uc}/lib", "-l:libunicorn.so.2",
                        f"-Wl,-rpath,{uc}/lib"], check=True)
    log(f"Scan view of {a.elf} -> {a.out}")
    t = time.time()
    run(a, a.out)
    log(f"Scan view ({os.path.basename(a.elf)}, {time.time() - t:.0f} s): {checks - failures} of {checks} ok; "
        + ("all checks passed" if not failures else f"FAILED: {failures} check(s)"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
