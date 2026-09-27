#!/usr/bin/env python3
"""The drone view of a song whose drone is on, after the song is opened again (the user's report on v16: "the song
with the drone on crashes when it's opened again and the drone is selected"), on the real firmware in the emulator.

Usage: drone_reopen_emu.py <deluge.elf> <out dir> [--tools PREFIX] [--build DIR]   (run.sh's DRONE=1 runs it)

The crash: DroneView has no graphicsRoutine() of its own since mastertune-v13 (its pads stopped pulsing), so the UI
timer's call every 15 ms (UITimerManager::routine(): getCurrentUI()->graphicsRoutine()) lands in UI::graphicsRoutine(),
which hands it to the root UI (canSeeViewUnderneath(): a RootUI can), the drone view itself: a tail call to itself,
for ever. The Deluge hangs 15 ms after the drone view opens (from Song view's Scale or the Song menu's Drone), whatever
song and however it was loaded; its audio stops with it. Opening the song again isn't needed, it's what the user did
first. Here every UI::graphicsRoutine() entry is counted: more than 1000 in one timer routine is the hang, reported
with where it is, and the test stops there.

What runs (tests/songchange's Deluge: the firmware boots, loads SONGS/DEFAULT.XML, and between windows of 128 samples
runs what the task manager would, the UI timer too; performLoad() pauses at its yields as on the Deluge; OLED):
Song A (DEFAULT.XML): a synth whose 1-bar clip has no notes (silent, but it plays and so arms a song change), and the
drone, its volume 40, no sidechain or reverb, with five tones:
  1   200 Hz      steady, sine                        level 41
  2   523 Hz      binaural, beat 20 Hz (513 left, 533 right), soft    level 38
  3   A4 by note  monaural, beat 20 Hz (430 and 450 Hz), organ        level 34
  4   1500 Hz     isochronic, beat 10 Hz, rich                        level 31
  10  125 Hz      steady, soft, panned                                level 38
Song B (SONGB.XML): a synth playing chords in a 1-bar clip and the drone with life, FM (saw) and a Pulse, binaural
and isochronic tone (one synced, as a triplet).
  1. As loaded at boot: the drone measured (the reference: each tone's lines, frequency and level, FFT over 0.4 s
     windows, stopped: the drone alone)
  2. Song A opened again from the song browser (stopped): the same, within 0.5 dB and 0.3 Hz
  3. Scale: the drone view, and the UI timer for 0.1 s (v16 hangs here)
  4. In it, as the user would: tone 2 selected (its audition pad), tone 1's level pad 8 (level 25: 16 dB down,
     measured) and pad 13 (back to 41), the upper gold knob +5 (205 Hz, measured) and -5, the select encoder on tone 2
     +3 and -3, the mode buttons (Kit, then Synth), tone 4 muted and on again, each knob section and its knobs there
     and back, scrolled to tones 9-16 and back, Select (the tone's menu) and Back: then the drone as at first
  5. Shift + Kit: a drone track made from the drone (v16), back in Song view, and the drone view again
  6. PLAY; song B loaded while A plays (the song change: armed, the countdown, the swap at the loop's end); the
     drone view with song B's life and FM, its knobs turned
  7. From the drone view: LOAD, song A (the view open while the song loads; the song change back to A); after the
     swap Song view, and Scale again; stopped: the drone as at first
Checks: no hang and no fault anywhere; the root UI after each step; the drone's lines as the reference after 2., 4.
and 7.; the level and pitch changes of 4. heard. Results: <out>/drone_reopen.json (the measurements).
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
import time

import numpy as np
from unicorn import UC_HOOK_CODE

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "songchange"))
import fat32  # noqa: E402
import songchange_emu as sc  # noqa: E402

SR = 44100
B = sc.button_xy
SCALE, SELECT, KIT, SHIFT, LOAD, BACK = B(6, 0), B(4, 3), B(5, 1), B(8, 0), B(6, 1), B(7, 1)
SYNTH, PLAY = B(5, 0), B(8, 3)
SEGMENT = 4 * SR // 10  # 0.4 s: whole cycles of the isochronic tone's 10 Hz beat (Hann's zeros on its sidebands)

# Song A's drone: (index, attributes, lines as (name, channel 0 left / 1 right, Hz))
TONES_A = [
    (0, dict(mode=0, timbre=0, byNote=0, frequency=20000, beat=1000, level=41),
     [("1: 200 Hz", 0, 200.0), ("1: 200 Hz", 1, 200.0)]),
    (1, dict(mode=1, timbre=1, byNote=0, frequency=52300, beat=2000, level=38),
     [("2: binaural left", 0, 513.0), ("2: binaural right", 1, 533.0)]),
    (2, dict(mode=2, timbre=2, byNote=1, note=69, cents=0, beat=2000, level=34),
     [("3: monaural low", 0, 430.0), ("3: monaural high", 1, 450.0)]),
    (3, dict(mode=3, timbre=3, byNote=0, frequency=150000, beat=1000, level=31),
     [("4: isochronic", 0, 1500.0), ("4: isochronic", 1, 1500.0)]),
    (9, dict(mode=0, timbre=1, byNote=0, frequency=12500, beat=1000, level=38, pan=-12),
     [("10: 125 Hz", 0, 125.0), ("10: 125 Hz", 1, 125.0)]),
]
TONES_B = [
    (0, dict(mode=1, timbre=4, byNote=0, frequency=22000, beat=600, level=40)),
    (1, dict(mode=3, timbre=3, byNote=1, note=57, beat=800, sync=4, triplet=1, pulseAttack=20, level=36)),
    (2, dict(mode=2, timbre=2, byNote=0, frequency=33000, beat=400, level=30, pan=10)),
]

failures = []


def check(what, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {what}" + (f": {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(what)
    return ok


# --- the songs

def drone_xml(tones, extra=""):
    out = f'\t<drone volume="40" sidechain="0" reverb="0" sidechainShape="-601295438"{extra}>\n'
    for index, attributes in tones:
        a = dict(index=index, active=1, **attributes)
        out += "\t\t<tone " + " ".join(f'{k}="{v}"' for k, v in a.items()) + " />\n"
    return out + "\t</drone>\n"


def build_sd(path):
    a = sc.song_xml("SYNA", 1, 1, 0, sc.KNOB_A_LPF, 0, "selected", None, 1)
    a = re.sub(r'noteData="0x[0-9A-F]*"', 'noteData="0x"', a)  # A's synth plays nothing: the drone alone
    a = a.replace("</song>\n", drone_xml([(i, t) for i, t, _ in TONES_A]) + "</song>\n")
    b = sc.song_xml("SYNB", 1, 3, 0, sc.KNOB_B_LPF, sc.KNOB_B_DELAY, "selected", None, 1)
    b = b.replace("</song>\n", drone_xml(TONES_B, ' life="40" lifeRate="30" fm="30" fmForm="1" pulseWidth="25"')
                  + "</song>\n")
    fat32.build(path, {"SONGS/DEFAULT.XML": a.encode(), "SONGS/SONGB.XML": b.encode()})


# --- the emulated Deluge, recording its output and watching the graphics routine

OFFSETS = [
    ("list DroneView::opened", [("selected", "(int)&((DroneView*)0)->selected_")]),
    ("list Song::Song", [("drone", "(int)&((Song*)0)->drone"),
                         ("tone_size", "sizeof(((Song*)0)->drone.tones._M_elems[0])"),
                         ("tone_level", "(int)&((Song*)0)->drone.tones._M_elems[0].level - "
                                        "(int)&((Song*)0)->drone"),
                         ("tone_frequency", "(int)&((Song*)0)->drone.tones._M_elems[0].frequency - "
                                            "(int)&((Song*)0)->drone"),
                         ("tone_mode", "(int)&((Song*)0)->drone.tones._M_elems[0].mode - (int)&((Song*)0)->drone"),
                         ("tone_active", "(int)&((Song*)0)->drone.tones._M_elems[0].active - "
                                         "(int)&((Song*)0)->drone")]),
]


class Hang(Exception):
    pass


class Deluge(sc.Deluge):
    def __init__(self, elf, sd, tools, build, out):
        super().__init__(elf, sd, tools, build, True, out)
        emu, sym = self.emu, self.sym
        args, names = [], []
        for context, queries in OFFSETS:
            args += ["-ex", context]
            for name, expression in queries:
                args += ["-ex", f"print {expression}"]
                names.append(name)
        text = subprocess.run([tools + "gdb", "-batch", *args, elf], capture_output=True, text=True).stdout
        values = [int(v) for v in re.findall(r"^\$\d+ = (\d+)$", text, re.M)]
        if len(values) != len(names):
            raise SystemExit(f"member offsets from gdb: {len(values)} of {len(names)}:\n{text[-800:]}")
        self.off.update(zip(names, values))
        self.dv = sym["droneView"]
        self.ui_names = {sym[n]: n for n in ("sessionView", "droneView", "instrumentClipView", "arrangerView",
                                             "loadSongUI", "soundEditor")}
        self.record = None
        self.rendering_buffer = sym["_ZN11AudioEngine15renderingBufferE"]
        self.master = (sym["_ZN11AudioEngine23masterVolumeAdjustmentLE"],
                       sym["_ZN11AudioEngine23masterVolumeAdjustmentRE"])
        # UI::graphicsRoutine() entries in one UITimerManager::routine() call: a few (the current UI's, the root's);
        # more than 1000 is it calling itself for ever
        self.graphics_entries = 0
        self.hang = False
        at = sym.find("_ZN2UI15graphicsRoutineEv") & ~1
        emu.uc.hook_add(UC_HOOK_CODE, self._on_graphics_routine, begin=at, end=at)
        emu.uc.ctl_flush_tb()

    def _hook_display(self):
        pass  # What the display shows isn't checked here (and a popup's text on the stack's top 64 bytes can't be read)

    def _on_graphics_routine(self, uc, address, size, _):
        self.graphics_entries += 1
        if self.graphics_entries > 1000 and not self.hang:
            self.hang = True
            self.emu.stop()

    def call(self, name_or_address, *args):
        address = self.a.get(name_or_address, name_or_address)
        if isinstance(address, str):
            address = self.sym.find(address)
        return self.emu.call(address, *args)

    def ui_name(self, address):
        return self.ui_names.get(address, hex(address))

    def root(self):
        return self.ui_name(self.call("_Z9getRootUIv"))

    def current(self):
        return self.ui_name(self.call("_Z12getCurrentUIv"))

    def step(self):
        """sc.Deluge.step(), recording the output (as song_emu.Player.window()) and stopping a hang."""
        self.drain_pic()
        emu = self.emu
        emu.dma_free = 127
        timer = self.timer()
        self.call("_ZN11AudioEngine7routineEv")
        emu.dma_free = 0
        if self.record is not None:
            samples = (self.timer() - timer) & 0xFFFFFFFF
            x = np.frombuffer(bytes(emu.uc.mem_read(self.rendering_buffer, samples * 8)), "<i4").reshape(-1, 2)
            gain = np.array([struct.unpack("<i", emu.uc.mem_read(a, 4))[0] for a in self.master]) / 2 ** 55
            self.record.append(x * gain)
        self.maybe_resume()
        self.call("_ZZ13registerTasksvENUlvE0_4_FUNEv")  # playbackHandler.routine()
        self.maybe_resume()
        self.call("_ZZ13registerTasksvENUlvE1_4_FUNEv")  # audioFileManager.loadAnyEnqueuedClusters(128, false)
        self.maybe_resume()
        self.drain_pic()
        self.graphics_entries = 0
        self.call("_ZN14UITimerManager7routineEv", self.var["uiTimerManager"])
        if self.hang:
            raise Hang(self.root())
        self.drain_pic()
        self.call("_Z23doAnyPendingUIRenderingv")
        self.maybe_resume()
        self.window_index += 1

    def steps(self, seconds):
        for _ in range(max(1, round(seconds * SR / 128))):
            self.step()

    # --- the drone view, as its pads, knobs and buttons call it
    def pad(self, x, y):
        self.drain_pic()
        self.call("_ZN9DroneView9padActionElll", self.dv, x, y, 64)
        self.call("_ZN9DroneView9padActionElll", self.dv, x, y, 0)

    def knob(self, which, offset):
        self.drain_pic()
        self.call("_ZN9DroneView16modEncoderActionEll", self.dv, which, offset)

    def press(self, b):
        self.button(b, True)
        self.button(b, False)

    def selected(self):
        return self.i32(self.dv + self.off["selected"])

    def tone(self, index, field):
        a = self.song() + self.off["drone"] + index * self.off["tone_size"] + self.off["tone_" + field]
        return self.i32(a) if field in ("level", "frequency") else self.emu.u8(a)

    def load(self, name, from_button=False):
        """The song browser on a song (as scrolled to), LOAD pressed and let go; until the new song is in and the
        browser gone (while playing: armed, the countdown to the loop's end, the swap). Whether the song was swapped
        (stopped, the new song may be where the old one was)."""
        song = self.song()
        text = sc.STOP + 0x200
        self.emu.uc.mem_write(text, name.encode() + b"\0")
        self.call("_ZN6String3setEPKcl", song + self.off["song_name"], text, -1)  # LoadSongUI looks for this name
        if from_button:
            self.press(LOAD)  # View::buttonAction(): a short press opens the browser
        else:
            self.call("_Z6openUIP2UI", self.var["loadSongUI"])
        for _ in range(3000):
            if self.mode() == sc.UI_MODE_NONE and self.current() == "loadSongUI":
                break
            self.step()
        if not check(f"song browser open on {name}", self.current() == "loadSongUI", f"current UI {self.current()}"):
            raise SystemExit(1)
        swaps = len(self.swaps)
        self.press(LOAD)
        for _ in range(20000):
            if len(self.swaps) > swaps and not self.paused and self.mode() == sc.UI_MODE_NONE:
                break
            self.step()
        self.steps(0.05)
        return len(self.swaps) > swaps  # PlaybackHandler::doSongSwap(): the new song is the current one


# --- the drone's lines in the output

def lines(x, tones):
    """Each line's frequency (the peak within 6 Hz, interpolated) and level (dB of full scale, the power within
    6 Hz), over the 0.4 s segments of x (Hann), averaged."""
    n = SEGMENT
    segments = [x[i:i + n] for i in range(0, len(x) - n + 1, n)]
    window = np.hanning(n)
    scale = 2 / window.sum()
    freqs = np.fft.rfftfreq(n, 1 / SR)
    out = {}
    for _, _, tone_lines in tones:
        for name, channel, hz in tone_lines:
            band = (freqs > hz - 6) & (freqs < hz + 6)
            powers, peaks = [], []
            for s in segments:
                spectrum = np.abs(np.fft.rfft(s[:, channel] * window)) * scale
                powers.append(np.sum(spectrum[band] ** 2) / 1.5)  # Hann's noise bandwidth: 1.5 bins
                i = np.flatnonzero(band)[np.argmax(spectrum[band])]
                a, b, c = np.log(spectrum[i - 1:i + 2] + 1e-12)
                peaks.append(freqs[i] + (a - c) / (2 * (a - 2 * b + c)) * (freqs[1] - freqs[0]))
            key = f"{name} ({'LR'[channel]})"
            out[key] = dict(hz=float(np.mean(peaks)), db=float(10 * np.log10(np.mean(powers) / 2 + 1e-20)))
    return out


def measure(d, seconds=0.8, tones=TONES_A):
    d.record = []
    while sum(len(r) for r in d.record) < seconds * SR:
        d.step()
    x = np.concatenate(d.record)
    d.record = None
    return lines(x, tones)


def same_as(what, got, ref, db_tolerance=0.5, hz_tolerance=0.3):
    worst_db = max(abs(got[k]["db"] - ref[k]["db"]) for k in ref)
    worst_hz = max(abs(got[k]["hz"] - ref[k]["hz"]) for k in ref)
    detail = ", ".join(f"{k} {got[k]['hz']:.1f} Hz {got[k]['db']:.1f} dB" for k in list(got)[::2])
    return check(f"{what}: the drone's lines as at first (level within {db_tolerance} dB, frequency within "
                 f"{hz_tolerance} Hz; worst {worst_db:.2f} dB, {worst_hz:.2f} Hz)",
                 worst_db <= db_tolerance and worst_hz <= hz_tolerance, detail)


def run(d, results):
    # 1. As loaded at boot
    d.steps(0.4)
    ref = results["reference"] = measure(d)
    expected = {f"{name} ({'LR'[ch]})": hz for _, _, ls in TONES_A for name, ch, hz in ls}
    check("as loaded: every line of the drone at its frequency (within 0.3 Hz) and heard (above -60 dBFS)",
          all(abs(ref[k]["hz"] - hz) <= 0.3 and ref[k]["db"] > -60 for k, hz in expected.items()),
          ", ".join(f"{k} {v['hz']:.2f} Hz {v['db']:.1f} dB" for k, v in ref.items()))

    # 2. Opened again from the song browser, stopped
    swapped = d.load("DEFAULT")
    check("song A opened again: swapped in, in Song view", swapped and d.root() == "sessionView", d.root())
    d.steps(0.4)
    same_as("after opening it again", results.setdefault("reopened", measure(d)), ref)

    # 3. The drone view
    d.press(SCALE)
    check("Scale: the drone view", d.root() == "droneView", d.root())
    d.steps(0.1)  # The UI timer's graphics routine, 6 times
    check("the drone view open for 0.1 s, the UI timer running: no hang", True)

    # 4. In it
    d.pad(17, 1)
    check("tone 2's audition pad selects it", d.selected() == 1, f"selected {d.selected()}")
    d.pad(7, 0)
    level = d.tone(0, "level")
    d.steps(0.3)
    low = measure(d)
    d.pad(12, 0)
    drop = ref["1: 200 Hz (L)"]["db"] - low["1: 200 Hz (L)"]["db"]
    check("tone 1's pad 8: level 25, heard 16 dB lower (within 0.5 dB)", level == 25 and abs(drop - 16) <= 0.5,
          f"level {level}, {drop:.2f} dB lower; pad 13: level {d.tone(0, 'level')}")
    d.steps(0.1)  # More than 60 ms: the next turn isn't a quick one
    d.knob(1, 5)
    frequency = d.tone(0, "frequency")
    d.steps(0.3)
    at205 = measure(d, tones=[(0, None, [("1", 0, 205.0)])])["1 (L)"]
    check("the upper gold knob +5 (pitch section): tone 1 at 205 Hz", frequency == 20500 and
          abs(at205["hz"] - 205) <= 0.3, f"frequency {frequency}, heard {at205['hz']:.2f} Hz {at205['db']:.1f} dB")
    d.steps(0.1)
    d.knob(1, -5)
    d.call("_ZN9DroneView19selectEncoderActionEa", d.dv, 3)  # Tone 1 is selected now: 1 Hz a click
    d.call("_ZN9DroneView19selectEncoderActionEa", d.dv, -3)
    d.pad(17, 1)
    d.call("_ZN9DroneView19selectEncoderActionEa", d.dv, 3)  # Tone 2: 523 -> 526 Hz
    d.steps(0.05)
    d.call("_ZN9DroneView19selectEncoderActionEa", d.dv, -3)
    d.pad(17, 0)
    d.press(KIT)  # Binaural
    mode = d.tone(0, "mode")
    d.steps(0.05)
    d.press(SYNTH)  # Steady again
    check("Kit, then Synth: tone 1 binaural, then steady again", mode == 1 and d.tone(0, "mode") == 0,
          f"modes {mode}, {d.tone(0, 'mode')}")
    d.pad(16, 3)  # Tone 4 off
    off = d.tone(3, "active")
    d.steps(0.05)
    d.pad(16, 3)  # And on
    check("tone 4's mute pad: off, and on again", off == 0 and d.tone(3, "active") == 1,
          f"active {off}, {d.tone(3, 'active')}")
    for section in range(6):
        d.press(sc.MOD_BUTTON[section])
        for which in (0, 1):
            d.knob(which, 2)
            d.steps(0.1)  # Not a quick turn back (the pitch 10 Hz a click then)
            d.knob(which, -2)
        d.step()
    d.press(sc.MOD_BUTTON[1])  # Back to pitch and beat
    d.call("_ZN9DroneView21verticalEncoderActionElb", d.dv, 8, 0)
    d.steps(0.05)
    d.call("_ZN9DroneView21verticalEncoderActionElb", d.dv, -8, 0)
    d.pad(17, 0)
    d.press(SELECT)
    menu = d.current()
    d.steps(0.1)
    d.press(BACK)
    d.steps(0.05)
    check("Select: tone 1's menu, Back: the drone view again", menu == "soundEditor" and d.current() == "droneView",
          f"{menu}, then {d.current()}")
    d.steps(0.4)
    same_as("after the pads, knobs and buttons", results.setdefault("played", measure(d)), ref)

    # 5. A drone track made from the drone (v16)
    shift = d.sym["_ZN7Buttons21shiftCurrentlyPressedE"]
    d.emu.uc.mem_write(shift, b"\x01")
    d.press(KIT)
    d.emu.uc.mem_write(shift, b"\x00")
    d.steps(0.05)
    root = d.root()
    d.press(SCALE)
    d.steps(0.1)
    check("Shift + Kit: a drone track, back in Song view; Scale: the drone view again",
          root == "sessionView" and d.root() == "droneView", f"{root}, then {d.root()}")
    d.press(BACK)

    # 6. Song B while A plays
    d.call("_ZN15PlaybackHandler17playButtonPressedEl", 0)
    d.steps(0.2)
    swapped = d.load("SONGB")
    check("song B loaded while A played (the song change)", swapped and d.root() == "sessionView", d.root())
    d.press(SCALE)
    for section in (3, 5):  # Life and its rate, FM and the pulse width
        d.press(sc.MOD_BUTTON[section])
        d.knob(1, 3)
        d.knob(0, -3)
        d.steps(0.05)
    d.pad(17, 1)
    d.knob(1, 2)
    d.steps(0.2)
    check("song B's drone view, its life and FM turned: no hang", d.root() == "droneView", d.root())

    # 7. From the drone view: LOAD, song A (the song change back)
    swapped = d.load("DEFAULT", from_button=True)
    check("song A loaded from the drone view's LOAD while B played", swapped and d.root() == "sessionView",
          d.root())
    d.press(SCALE)
    d.steps(0.1)
    d.call("_ZN15PlaybackHandler17playButtonPressedEl", 0)  # Stop
    d.steps(0.8)
    check("Scale: the drone view of song A, stopped", d.root() == "droneView", d.root())
    same_as("after the song change back to A", results.setdefault("changed_back", measure(d)), ref)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("out")
    ap.add_argument("--tools")
    ap.add_argument("--build")
    args = ap.parse_args()
    elf = os.path.abspath(args.elf)
    tools = args.tools or os.path.join(os.path.dirname(elf),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    build = args.build or os.environ.get("BLOCKCOUNT_DIR") or args.out
    os.makedirs(args.out, exist_ok=True)
    if not os.path.exists(os.path.join(build, "blockcount.so")):
        import unicorn
        uc = os.path.dirname(unicorn.__file__)
        subprocess.run(["cc", "-O2", "-shared", "-fPIC", f"-I{uc}/include", os.path.join(HERE, "blockcount.c"),
                        "-o", os.path.join(build, "blockcount.so"), f"-L{uc}/lib", "-l:libunicorn.so.2",
                        f"-Wl,-rpath,{uc}/lib"], check=True)
    print("== the drone view of a song with the drone on, the song opened again (drone_reopen_emu.py)", flush=True)
    sd = os.path.join(args.out, "sd.img")
    build_sd(sd)
    t = time.time()
    d = Deluge(elf, sd, tools, build, args.out)
    results = {}
    try:
        run(d, results)
    except Hang as hang:
        check("no hang", False, f"UI::graphicsRoutine() entered over 1000 times in one UITimerManager::routine() "
              f"call, the root UI {hang}: the graphics routine calls itself for ever and the Deluge hangs (DroneView "
              f"has no graphicsRoutine() of its own)")
    json.dump(results, open(os.path.join(args.out, "drone_reopen.json"), "w"), indent=1)
    os.remove(sd)
    print(f"  ({time.time() - t:.0f} s) " + ("all checks passed" if not failures else
                                            f"FAILED: {len(failures)} check(s)"), flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
