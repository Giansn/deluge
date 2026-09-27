#!/usr/bin/env python3
"""Versions of a song grouped in the song browser (mastertune), on the real firmware in the emulator (the harness of
../songchange: tests/song's Emulator, performLoad() pausing at its yields while the task manager's work runs window by
window).

The user names versions of a song "TRACK", "TRACK 2", "TRACK 3": files whose names begin with the same word (up to the
first space, or the extension; case doesn't matter) and that sort next to each other are one row in the song browser,
the group's first version, shown by that word with an arrow. Pressing it (select encoder or LOAD) folds the group out
and loads nothing; the select encoder then goes through the versions, pressing one loads it; leaving the group or BACK
folds it in. A song on its own looks and works as before. The save browser is unchanged.

The card (SONGS/): A001..A150, BIG 1..BIG 120 (a group larger than the song browser's window of 100 file items),
DEFAULT (the startup song), MID 1..MID 30, OTHER, TRACK, TRACK 2, TRACK 3, track 4, TRACK 10, the folder TRACK DEMOS,
TRACK LIVE, TRACK!, TRACKS, Z001..Z150. Every song is a small one-synth song (a 1-bar clip); those loaded here have their own song LPF, so which
song got loaded is known from the song itself, not from its name.

What's driven, as the user would: the browser opened with openUI(&loadSongUI) (the current song's name set first: the
browser starts on it), the select encoder is LoadSongUI::selectEncoderAction(), buttons (select encoder, LOAD, BACK)
are Buttons::buttonAction(). What the OLED shows: the browser's renderOLED() called, its Canvas::drawString(),
drawGraphicMultiLine() (icons, the arrow) and invertArea() (the selection) recorded per row. The 7-segment display:
SevenSegment::setScrollingText().

Checks (OLED unless said):
 1. from OTHER, +1: TRACK, one row with an arrow; below it the folder TRACK DEMOS (TRACK 2..TRACK 10 not shown)
 2. +1: the folder; -1: TRACK (not TRACK 10)
 3. pressing TRACK: nothing loads, the browser stays, the versions shown indented, TRACK selected, no arrow
 4. +1 four times: TRACK 2, TRACK 3, track 4, TRACK 10; +1: the folder, the group folded in again
 5. -1 from the folder: TRACK; pressed, +1: TRACK 2; BACK: folded in, TRACK selected, the browser still open; BACK
    again: the browser closes (no group open: as before)
 6. TRACK pressed, +1 +1, LOAD: TRACK 3 loads (the song stopped)
 7. the browser opened on the current song TRACK 3: its group folded out, TRACK 3 selected
 8. while playing: from there -1, LOAD held and released: TRACK 2 loads at the launch
 9. OTHER, TRACK LIVE (after the folder: a group of one), TRACK!, TRACKS: rows of their own (no arrow, not indented),
    LOAD loads each at once
10. the window: from A150 +1: BIG 1 with an arrow, +1: DEFAULT, the folder read again on the way (the window moved:
    numFileItemsDeletedAtStart), -1: BIG 1; pressed, +1 119 times: BIG 2..BIG 120 in order, +1: DEFAULT, folded in
11. the browser opened on BIG 120 (the group across the window's edge): folded out, BIG 120 selected; BACK: BIG 1,
    folded in (the folder read again on the way)
12. the rows around a group of 30 (its versions' items in the window too): from OTHER -1: MID with an arrow at the top,
    OTHER and TRACK below; -1: DEFAULT, MID and OTHER below; +1 +1: OTHER at the bottom, MID with an arrow above
13. the save browser: TRACK 2, TRACK 3, track 4 each a row of its own (Browser::renderOLED(), unchanged)
14. 7-segment display: from OTHER +1: "TRACK--"; pressed: "TRACK"; +1: "TRACK 2"; BACK: "TRACK--"
15. the folder read (Browser::readFileItemsFromFolderAndMemory()) only when needed next to a group larger than the
    window (BIG): DEFAULT +1 -1 +1 -1 (MID 1, DEFAULT, ...): no read (the rows' files are all in the window);
    across the group, DEFAULT -1 (BIG 1) and back +1: at most 4 reads each on the OLED (the window reaching ahead of
    the steps over the versions: 2-3 for 120 of them, one for the rows), at most 3 on the 7-segment display; the
    rows "A149 | A150 | [BIG >]" and "BIG > | [DEFAULT] | MID >". (Before: 1 read per step next to it, 5-6 across.)
16. SHIFT+SAVE (delete) on the folded row TRACK: no delete prompt (it would delete TRACK.XML, which the row doesn't
    show, and the prompt doesn't name it), a popup instead; on TRACK 2 in the group folded out: the prompt as before;
    BACK from it: nothing deleted

Usage: song_groups_emu.py <deluge.elf> [--tools PREFIX] [--out DIR] [--build DIR] [--baseline] [--no-7seg]
  --baseline: a build without the grouping: the same steps, reported (the old browser: every file a row), not checked.
Exit status 0 when all checks hold. Needs python3 with unicorn 2 and numpy, a C compiler (blockcount.c).
"""
import argparse
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SONG_DIR = os.path.join(HERE, "..", "song")
sys.path.insert(0, SONG_DIR)
sys.path.insert(0, os.path.join(HERE, "..", "songchange"))
import fat32  # noqa: E402
import song_emu  # noqa: E402
import songchange_emu as sc  # noqa: E402
from song_emu import STOP  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3  # noqa: E402

BUTTON_SELECT = sc.button_xy(4, 3)  # selectEncButtonCoord
BUTTON_LOAD, BUTTON_BACK = sc.BUTTON_LOAD, sc.BUTTON_BACK
BUTTON_SHIFT, BUTTON_SAVE = sc.button_xy(8, 0), sc.button_xy(6, 3)  # shiftButtonCoord, saveButtonCoord
UI_MODE_NONE = 0
UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_UNARMED = 35
NAME_AT = STOP + 0x400  # A name written for String::set()

# The song LPF knob (0..50) of each song this test loads; all others FILLER
KNOBS = {"DEFAULT": 5, "OTHER": 10, "TRACK": 12, "TRACK 2": 14, "TRACK 3": 16, "track 4": 18, "TRACK 10": 20,
         "TRACK LIVE": 22, "TRACK!": 24, "TRACKS": 26, "BIG 1": 28, "BIG 120": 30}
BIG = 120
FILLER = 7
TRACKS = ["TRACK", "TRACK 2", "TRACK 3", "track 4", "TRACK 10"]


def log(s):
    print(s, flush=True)


def build_sd(path):
    cache = {}

    def song(knob):
        if knob not in cache:
            cache[knob] = sc.song_xml("SYN", 1, 1, 0, knob, 0, "selected", None).encode()
        return cache[knob]
    names = ([f"A{n:03d}" for n in range(1, 151)] + [f"BIG {n}" for n in range(1, BIG + 1)]
             + ["DEFAULT"] + [f"MID {n}" for n in range(1, 31)] + ["OTHER"] + TRACKS + ["TRACK LIVE", "TRACK!", "TRACKS"] + [f"Z{n:03d}" for n in range(1, 151)])
    files = {f"SONGS/{n}.XML": song(KNOBS.get(n, FILLER)) for n in names}
    files["SONGS/TRACK DEMOS/DEMO.XML"] = song(FILLER)
    fat32.build(path, files)
    return len(files), sum(len(d) for d in files.values())


class Browser:
    """The emulated Deluge (songchange_emu.Deluge) with the song browser's state and what it draws."""

    def __init__(self, a, sd, oled, out):
        self.d = d = sc.Deluge(a.elf, sd, a.tools, a.build, oled, out)
        emu, sym = d.emu, d.sym
        self.emu, self.sym, self.oled = emu, sym, oled
        self.ui = d.var["loadSongUI"]
        self.main = d.var["_ZN6deluge3hid7display4OLED4mainE"]
        self.string_memory, = song_emu.gdb_values(emu, ["(int)&((String*)0)->stringMemory"])
        try:
            self.group_off, = song_emu.gdb_values(emu, ["(int)&loadSongUI.openGroup - (int)&loadSongUI"])
        except SystemExit:
            self.group_off = None  # A build without the grouping
        self.v = {n: sym[n] for n in ("_ZN8QwertyUI11enteredTextE", "_ZN7Browser17fileIndexSelectedE",
                                      "_ZN7Browser26numFileItemsDeletedAtStartE", "_Z12getCurrentUIv", "saveSongUI",
                                      "_ZN6deluge3hid7display4OLED16submenuArrowIconE",
                                      "_ZN6deluge3gui12context_menu10deleteFileE")}
        own = [k for k in sym.by_name if k.startswith("_ZN10LoadSongUI10renderOLED")]
        self.render_load = sym.find("_ZN10LoadSongUI10renderOLED") if own else sym.find("_ZN7Browser10renderOLED")
        self.render_browser = sym.find("_ZN7Browser10renderOLED")
        self.reads = 0

        def on_read(e):
            self.reads += 1

        def on_graphic(e):
            if e.uc.reg_read(UC_ARM_REG_R0) == self.main:
                g, x, y = (e.uc.reg_read(r) for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
                d.events.append((d.window_index, "graphic", dict(g=g, x=x, y=y)))

        def on_invert(e):
            if e.uc.reg_read(UC_ARM_REG_R0) == self.main:
                x, w, y = (e.uc.reg_read(r) for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
                d.events.append((d.window_index, "invert", dict(x=x, w=w, y=y)))

        def on_scrolling_text(e):
            d.events.append((d.window_index, "7segScroll", dict(text=e.ram_str(e.uc.reg_read(UC_ARM_REG_R1)))))
        emu.intercept(sym.find("_ZN7Browser32readFileItemsFromFolderAndMemory"), on_read)
        emu.intercept(sym.find("_ZN6deluge3hid7display11oled_canvas6Canvas20drawGraphicMultiLine"), on_graphic)
        emu.intercept(sym.find("_ZN6deluge3hid7display11oled_canvas6Canvas10invertArea"), on_invert)
        emu.intercept(sym.find("_ZN6deluge3hid7display12SevenSegment16setScrollingText"), on_scrolling_text)

        def on_popup(e):
            d.events.append((d.window_index, "popup", dict(text=e.ram_str(e.uc.reg_read(UC_ARM_REG_R1)))))
        for f in ("_ZN6deluge3hid7display4OLED12displayPopupEPKc", "_ZN6deluge3hid7display12SevenSegment12displayPopupEPKc"):
            emu.intercept(sym.find(f), on_popup)
        emu.uc.ctl_flush_tb()

    # --- state
    def string(self, address):
        p = self.emu.u32(address + self.string_memory)
        return self.emu.ram_str(p, 128) if p else ""

    def state(self):
        return dict(sel=struct.unpack("<i", self.emu.uc.mem_read(self.v["_ZN7Browser17fileIndexSelectedE"], 4))[0],
                    name=self.string(self.v["_ZN8QwertyUI11enteredTextE"]),
                    deleted=self.emu.u32(self.v["_ZN7Browser26numFileItemsDeletedAtStartE"]),
                    group=self.string(self.ui + self.group_off) if self.group_off is not None else None)

    def name(self):
        return self.state()["name"]

    def group(self):
        return self.state()["group"]

    def current_ui(self):
        return self.d.call(self.v["_Z12getCurrentUIv"])

    def is_open(self, ui=None):
        return self.current_ui() == (ui or self.ui)

    def loaded(self):
        """The current song's own LPF knob (0..50), its name."""
        song = self.d.song()
        lpf = self.d.song_param(song, sc.UNPATCHED_LPF_FREQ)
        knob = next((k for k in range(51) if sc.knob_value(k) == lpf), None)
        return knob, self.string(song + self.d.off["song_name"])

    # --- what the OLED shows
    def frame(self, ui=None, render=None):
        """The rows the browser draws: [dict(text, indent, arrow, sel)], top to bottom."""
        d = self.d
        mark = len(d.events)
        d.call(render or self.render_load, ui or self.ui, self.main)
        rows = {}
        for _, kind, e in d.events[mark:]:
            if e.get("y", 0) < 15 or (kind == "graphic" and e["x"] >= 13 and e["x"] < 110):  # The title, glyphs
                continue
            r = rows.setdefault(e["y"], dict(text="", x=None, icon=None, arrow=False, sel=False))
            if kind == "main":
                r["text"], r["x"] = e["text"], e["x"]
            elif kind == "graphic":  # The icon, the arrow; the characters' glyphs (drawString()) left out
                if e["g"] == self.v["_ZN6deluge3hid7display4OLED16submenuArrowIconE"]:
                    r["arrow"] = True
                elif e["x"] < 13 and r["icon"] is None:
                    r["icon"] = e["x"]
            elif kind == "invert":
                r["sel"] = True
        out = []
        for y in sorted(rows):
            r = rows[y]
            out.append(dict(text=r["text"], indent=(r["x"] or 14) > 14, arrow=r["arrow"], sel=r["sel"]))
        return out

    @staticmethod
    def show(rows):
        return " | ".join(("[" if r["sel"] else "") + ("  " if r["indent"] else "") + r["text"]
                          + (" >" if r["arrow"] else "") + ("]" if r["sel"] else "") for r in rows)

    def scroll_texts(self, mark):
        return [e["text"] for _, kind, e in self.d.events[mark:] if kind == "7segScroll"]

    def popups(self, mark):
        return [e["text"] for _, kind, e in self.d.events[mark:] if kind == "popup"]

    # --- input
    def settle(self, limit=400):
        """Windows of the task manager's work until no UI mode is on (animations done) and no load is paused."""
        d = self.d
        for _ in range(limit):
            if d.mode() == UI_MODE_NONE and not d.paused:
                return True
            d.step()
        return False

    def turn(self, offset):
        if not self.is_open():
            return None
        self.d.select_encoder(offset)
        for _ in range(3):
            self.d.step()
        return self.name()

    def press(self, button):
        """Pressed and released, the UI settled: a load (the song stopped) done."""
        if not self.is_open():
            return False
        self.settle()
        self.d.button(button, True)
        self.settle(20000)
        self.d.button(button, False)
        self.settle(20000)
        return True

    def open(self, name, ui=None):
        """The browser opened with the current song's name set to name (it starts there)."""
        d = self.d
        self.close()
        self.emu.uc.mem_write(NAME_AT, name.encode() + b"\0")
        d.call("_ZN6String3setEPKcl", d.song() + d.off["song_name"], NAME_AT, -1)
        d.drain_pic()
        ok = d.call("_Z6openUIP2UI", ui or self.ui) & 0xFF
        self.settle(2000)
        return bool(ok) and self.is_open(ui)

    def shift_save(self):
        """SHIFT held, SAVE pressed and released (Browser: delete the selected file, its prompt)."""
        d = self.d
        self.settle()
        d.button(BUTTON_SHIFT, True)
        d.button(BUTTON_SAVE, True)
        d.button(BUTTON_SAVE, False)
        d.button(BUTTON_SHIFT, False)
        for _ in range(50):
            d.step()

    def back(self):
        if not self.is_open():
            return
        self.settle()
        self.d.button(BUTTON_BACK, True)
        self.d.button(BUTTON_BACK, False)
        for _ in range(600):  # The scroll out when the browser closes
            if self.d.mode() == UI_MODE_NONE:
                break
            self.d.step()

    def close(self):
        for ui in (self.ui, self.v["saveSongUI"]):
            for _ in range(4):
                if not self.is_open(ui):
                    break
                self.settle()
                self.d.button(BUTTON_BACK, True)
                self.d.button(BUTTON_BACK, False)
                for _ in range(600):
                    if not self.is_open(ui):
                        break
                    self.d.step()


class Checks:
    def __init__(self, baseline):
        self.baseline = baseline
        self.results = []

    def __call__(self, name, ok, detail=""):
        ok = bool(ok)
        self.results.append((name, ok))
        mark = ("ok  " if ok else "FAIL") if not self.baseline else ("same" if ok else "old ")
        log(f"  {mark} {name}" + (f": {detail}" if detail else ""))
        return ok


def row(rows, text):
    return next((r for r in rows if r["text"] == text), None)


def run_oled(a, sd, out, check):
    t0 = time.time()
    b = Browser(a, sd, True, out)
    d = b.d
    log(f"  booted, DEFAULT loaded ({time.time() - t0:.0f} s)")

    # 1-5: fold out, through the versions, fold in, BACK
    b.open("OTHER")
    check("browser opened on OTHER", b.name() == "OTHER", b.show(b.frame()))
    b.turn(1)
    f = b.frame()
    r = row(f, "TRACK")
    below = f[f.index(r) + 1]["text"] if r and f.index(r) + 1 < len(f) else None
    check("1. +1 from OTHER: TRACK, one row with an arrow, the folder TRACK DEMOS below it",
          b.name() == "TRACK" and r and r["sel"] and r["arrow"] and below == "TRACK DEMOS"
          and not any(row(f, t) for t in TRACKS[1:]), b.show(f))
    b.turn(1)
    at_folder = b.name()
    b.turn(-1)
    check("2. +1: the folder; -1: TRACK (the group's top, not TRACK 10)", at_folder == "TRACK DEMOS"
          and b.name() == "TRACK", f"{at_folder}, then {b.name()}: {b.show(b.frame())}")
    song0 = d.song()
    b.press(BUTTON_SELECT)
    f = b.frame()
    shown = [r for r in f if r["text"] in TRACKS]
    check("3. TRACK pressed: nothing loads, the browser stays, the versions indented, TRACK selected, no arrow",
          d.song() == song0 and b.is_open() and b.name() == "TRACK" and b.group() == "TRACK"
          and [r["text"] for r in shown] == TRACKS[:len(shown)] and len(shown) >= 2 and shown[0]["sel"]
          and all(r["indent"] and not r["arrow"] for r in shown),
          f"open group {b.group()!r}, loaded {b.loaded()}: {b.show(f)}")
    names = [b.turn(1) for _ in range(4)]
    f4 = b.frame()
    folder = b.turn(1)
    f = b.frame()
    r = row(f, "TRACK")
    check("4. +1 x4: TRACK 2, TRACK 3, track 4, TRACK 10; +1: the folder, the group folded in",
          names == TRACKS[1:] and folder == "TRACK DEMOS" and b.group() == "" and r and r["arrow"]
          and not any(row(f, t) for t in TRACKS[1:]), f"{names}, {folder}; at TRACK 10: {b.show(f4)}; then "
          f"{b.show(f)}")
    b.turn(-1)
    b.press(BUTTON_SELECT)
    b.turn(1)
    at2 = b.name()
    b.back()
    f = b.frame()
    check("5. -1, pressed, +1: TRACK 2; BACK: folded in, TRACK selected, the browser still open",
          at2 == "TRACK 2" and b.is_open() and b.name() == "TRACK" and b.group() == "" and f and row(f, "TRACK")
          and row(f, "TRACK")["arrow"] and row(f, "TRACK")["sel"], f"{at2}, then {b.name()}: {b.show(f)}")
    b.back()
    check("5. BACK again (no group open): the browser closes, as before", not b.is_open())

    # 6-8: loading a version, stopped and while playing
    b.open("OTHER")
    b.turn(1)
    b.press(BUTTON_SELECT)
    b.turn(1)
    b.turn(1)
    before = b.name()
    b.press(BUTTON_LOAD)
    knob, name = b.loaded()
    check("6. TRACK pressed, +1 +1, LOAD: TRACK 3 loads (stopped)", before == "TRACK 3" and knob == KNOBS["TRACK 3"]
          and not b.is_open(), f"selected {before!r}, loaded song: LPF knob {knob} (TRACK 3: {KNOBS['TRACK 3']}), "
          f"name {name!r}")

    d.call("_ZN15PlaybackHandler17playButtonPressedEl", 0)
    for _ in range(100):
        d.step()
    b.open(b.loaded()[1] or "TRACK 3")
    f = b.frame()
    check("7. the browser opened on the current song TRACK 3: its group folded out, TRACK 3 selected",
          b.name() == "TRACK 3" and b.group() == "TRACK" and row(f, "TRACK 3") and row(f, "TRACK 3")["sel"]
          and all(r["indent"] for r in f if r["text"] in TRACKS), b.show(f))
    b.turn(-1)
    before = b.name()
    loaded = False
    if b.is_open():
        b.settle()
        d.button(BUTTON_LOAD, True)
        for _ in range(20000):
            if d.paused and d.mode() == UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_UNARMED:
                break
            d.step()
        d.button(BUTTON_LOAD, False)
        loaded = b.settle(20000)
    knob, name = b.loaded()
    check("8. while playing: -1, LOAD held and released: TRACK 2 loads at the launch",
          before == "TRACK 2" and loaded and knob == KNOBS["TRACK 2"],
          f"selected {before!r}, loaded song: LPF knob {knob} (TRACK 2: {KNOBS['TRACK 2']}), name {name!r}")
    d.call("_ZN15PlaybackHandler17playButtonPressedEl", 0)  # Stop
    for _ in range(20):
        d.step()

    # 9: songs on their own
    for single in ("OTHER", "TRACK LIVE", "TRACK!", "TRACKS"):
        b.open(single)
        f = b.frame()
        r = row(f, single)
        b.press(BUTTON_LOAD)
        knob, name = b.loaded()
        check(f"9. {single}: a row of its own (no arrow, not indented), LOAD loads it at once",
              r and r["sel"] and not r["arrow"] and not r["indent"] and knob == KNOBS[single], f"{b.show(f)}; loaded: LPF knob {knob}, name {name!r}")

    # 10: the window of 20 file items, a group of 40
    b.open("A150")
    s0, r0 = b.state(), b.reads
    b.turn(1)
    s1, f1 = b.state(), b.frame()
    b.turn(1)
    s2, r2 = b.state(), b.reads
    b.turn(-1)
    s3 = b.state()
    r = row(f1, "BIG")
    check(f"10. from A150 +1: BIG 1, shown as BIG with an arrow (BIG 2..{BIG} not shown)", s1["name"] == "BIG 1" and r
          and r["arrow"] and r["sel"] and not any(x["text"].startswith("BIG ") for x in f1), b.show(f1))
    check("10. +1: DEFAULT, the folder read again on the way (the window moved); -1: BIG 1",
          s2["name"] == "DEFAULT" and r2 > r0 and s2["deleted"] != s1["deleted"] and s3["name"] == "BIG 1",
          f"{s1['name']} -> {s2['name']} -> {s3['name']}, folder reads {r2 - r0}, items before the window "
          f"{s0['deleted']} -> {s1['deleted']} -> {s2['deleted']} -> {s3['deleted']}")
    b.press(BUTTON_SELECT)
    names = [b.turn(1) for _ in range(BIG - 1)]
    at_end = b.state()
    out_of = b.turn(1)
    after = b.state()
    check(f"10. BIG 1 pressed, +1 x{BIG - 1}: BIG 2..BIG {BIG} in order; +1: DEFAULT, folded in",
          names == [f"BIG {n}" for n in range(2, BIG + 1)] and at_end["group"] == "BIG" and out_of == "DEFAULT"
          and after["group"] == "", f"{names[:3]}..{names[-2:]}, group {at_end['group']!r}; then {out_of!r}, group "
          f"{after['group']!r}")
    b.turn(-1)
    f = b.frame()
    check("10. -1 from DEFAULT: BIG 1 (the group's top, across the window), folded", b.name() == "BIG 1"
          and b.group() == "" and row(f, "BIG") and row(f, "BIG")["sel"] and row(f, "BIG")["arrow"], b.show(f))

    # 11: opened on a version at the other end of a group larger than the window
    last = f"BIG {BIG}"
    b.open(last)
    s, f = b.state(), b.frame()
    check(f"11. the browser opened on {last}: its group folded out, {last} selected",
          s["name"] == last and s["group"] == "BIG" and row(f, last) and row(f, last)["indent"], b.show(f))
    r0 = b.reads
    b.back()
    s, f = b.state(), b.frame()
    check("11. BACK: folded in, BIG 1 selected (the folder read again to get there), the browser open",
          b.is_open() and s["name"] == "BIG 1" and s["group"] == "" and row(f, "BIG") and row(f, "BIG")["arrow"]
          and b.reads > r0,
          f"folder reads {b.reads - r0}: {b.show(f)}")

    # 12: the rows around a group whose versions fit in the window
    b.open("OTHER")
    b.turn(-1)
    f1 = b.frame()
    b.turn(-1)
    f2 = b.frame()
    b.turn(1)
    b.turn(1)
    f3 = b.frame()
    texts = lambda f: [(r["text"], r["arrow"], r["sel"]) for r in f]  # noqa: E731
    check("12. a group of 30: from OTHER -1: MID with an arrow, OTHER and TRACK below; -1: DEFAULT, MID and OTHER "
          "below; +1 +1: OTHER, MID with an arrow above",
          texts(f1) == [("MID", True, True), ("OTHER", False, False), ("TRACK", True, False)]
          and texts(f2) == [("DEFAULT", False, True), ("MID", True, False), ("OTHER", False, False)]
          and texts(f3) == [("DEFAULT", False, False), ("MID", True, False), ("OTHER", False, True)],
          f"{b.show(f1)}; {b.show(f2)}; {b.show(f3)}")
    b.close()

    # 15: next to a group larger than the window, and across it: the folder read only when the rows need it
    b.open("DEFAULT")
    steps = []
    for o in (1, -1, 1, -1, -1, 1):
        r0 = b.reads
        b.turn(o)
        steps.append((b.name(), b.reads - r0, b.show(b.frame())))
    near = steps[:4]
    check(f"15. next to BIG ({BIG} versions, more than the window): DEFAULT +1 -1 +1 -1: MID 1, DEFAULT, MID 1, "
          "DEFAULT, the folder not read; -1: BIG 1 (across the group), +1: DEFAULT, each in at most 4 folder reads",
          [n for n, _, _ in near] == ["MID 1", "DEFAULT", "MID 1", "DEFAULT"] and all(r == 0 for _, r, _ in near)
          and steps[4][0] == "BIG 1" and steps[4][2] == "A149 | A150 | [BIG >]" and steps[4][1] <= 4
          and steps[5][0] == "DEFAULT" and steps[5][2] == "BIG > | [DEFAULT] | MID >" and steps[5][1] <= 4,
          "; ".join(f"{n} ({r} reads): {f}" for n, r, f in steps))

    # 16: SHIFT+SAVE (delete the selected file) on a folded group's row: refused, the group folded out first
    b.open("OTHER")
    b.turn(1)
    mark = len(d.events)
    b.shift_save()
    on_folded = (b.name(), b.is_open(), b.is_open(b.v["_ZN6deluge3gui12context_menu10deleteFileE"]), b.popups(mark))
    b.close()
    b.open("OTHER")
    b.turn(1)
    b.press(BUTTON_SELECT)
    b.turn(1)
    b.shift_save()
    on_version = (b.name(), b.is_open(b.v["_ZN6deluge3gui12context_menu10deleteFileE"]))
    b.back()  # The prompt closes, nothing deleted
    f = b.frame()
    check("16. SHIFT+SAVE on the folded row TRACK: no delete prompt (it would delete TRACK, not shown), a popup, the "
          "browser stays; on the version TRACK 2 (folded out): the prompt, as before; BACK: nothing deleted",
          on_folded[0] == "TRACK" and on_folded[1] and not on_folded[2] and on_folded[3]
          and on_version == ("TRACK 2", True) and b.is_open() and b.name() == "TRACK 2"
          and [r["text"] for r in f if r["text"] in TRACKS] == TRACKS[:len([r for r in f if r["text"] in TRACKS])]
          and row(f, "TRACK 2") and row(f, "TRACK 2")["sel"],
          f"folded row: selected {on_folded[0]!r}, browser open {on_folded[1]}, prompt open {on_folded[2]}, popups "
          f"{on_folded[3]}; version: selected {on_version[0]!r}, prompt open {on_version[1]}; after BACK: {b.show(f)}")
    b.close()

    # 13: the save browser lists every file
    save = b.v["saveSongUI"]
    b.open("TRACK", save)
    f = []
    for _ in range(5):
        if b.is_open(save):
            d.call(d.sym.find("_ZN7Browser19selectEncoderActionEa"), save, -1)
            f = b.frame(save, b.render_browser)
            if row(f, "TRACK 2"):
                break
    check("13. the save browser: the versions each a row of their own, no arrows",
          [r["text"] for r in f] in (TRACKS[i:i + 3] for i in range(3)) and row(f, "TRACK 2")
          and not any(r["arrow"] or r["indent"] for r in f), b.show(f))
    b.close()


def run_7seg(a, sd, out, check):
    t0 = time.time()
    b = Browser(a, sd, False, out)
    log(f"  booted (7-segment display), DEFAULT loaded ({time.time() - t0:.0f} s)")
    b.open("OTHER")
    mark = len(b.d.events)
    b.turn(1)
    t1 = b.scroll_texts(mark)
    mark = len(b.d.events)
    b.press(BUTTON_SELECT)
    t2 = b.scroll_texts(mark)
    mark = len(b.d.events)
    b.turn(1)
    t3 = b.scroll_texts(mark)
    mark = len(b.d.events)
    b.back()
    t4 = b.scroll_texts(mark)
    check('14. 7-segment: +1 from OTHER: "TRACK--"; pressed: "TRACK"; +1: "TRACK 2"; BACK: "TRACK--"',
          t1[-1:] == ["TRACK--"] and t2[-1:] == ["TRACK"] and t3[-1:] == ["TRACK 2"] and t4[-1:] == ["TRACK--"],
          f"{t1[-1:]}, {t2[-1:]}, {t3[-1:]}, {t4[-1:]}")
    b.close()

    b.open("DEFAULT")
    steps = []
    for o in (1, -1, -1, 1):
        r0, mark = b.reads, len(b.d.events)
        b.turn(o)
        steps.append((b.name(), b.reads - r0, b.scroll_texts(mark)[-1:]))
    check(f'15. 7-segment: DEFAULT +1 -1: "MID--", "DEFAULT", the folder not read; -1: "BIG--" (across {BIG} versions), '
          '+1: "DEFAULT", each in at most 3 folder reads',
          [(n, t) for n, _, t in steps] == [("MID 1", ["MID--"]), ("DEFAULT", ["DEFAULT"]), ("BIG 1", ["BIG--"]),
                                            ("DEFAULT", ["DEFAULT"])]
          and steps[0][1] == 0 and steps[1][1] == 0 and steps[2][1] <= 3 and steps[3][1] <= 3,
          "; ".join(f"{n} {t} ({r} reads)" for n, r, t in steps))
    b.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "out"))
    ap.add_argument("--tools")
    ap.add_argument("--build", help="directory with blockcount.so (built there if missing)")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--no-7seg", action="store_true")
    a = ap.parse_args()
    a.elf = os.path.abspath(a.elf)
    a.tools = a.tools or os.path.join(os.path.dirname(a.elf),
                                      "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    a.build = a.build or a.out
    os.makedirs(a.out, exist_ok=True)
    if not os.path.exists(os.path.join(a.build, "blockcount.so")):
        import unicorn
        uc = os.path.dirname(unicorn.__file__)
        subprocess.run(["cc", "-O2", "-shared", "-fPIC", f"-I{uc}/include", os.path.join(SONG_DIR, "blockcount.c"),
                        "-o", os.path.join(a.build, "blockcount.so"), f"-L{uc}/lib", "-l:libunicorn.so.2",
                        f"-Wl,-rpath,{uc}/lib"], check=True)
    sd = os.path.join(a.out, "songs.img")
    n, size = build_sd(sd)
    log(f"== song versions grouped in the song browser ({os.path.basename(a.elf)}"
        + (", baseline: reported, not checked" if a.baseline else "") + f"); card: {n} files, {size / 1e6:.1f} MB")
    check = Checks(a.baseline)
    run_oled(a, sd, a.out, check)
    if not a.no_7seg:
        run_7seg(a, sd, a.out, check)
    passed = sum(ok for _, ok in check.results)
    log(f"song groups: {passed} of {len(check.results)} checks "
        + ("as with the grouping" if a.baseline else "passed"))
    sys.exit(0 if a.baseline or passed == len(check.results) else 1)


if __name__ == "__main__":
    main()
