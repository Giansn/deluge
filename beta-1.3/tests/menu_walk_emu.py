#!/usr/bin/env python3
"""A systematic walk through every menu of the v1.3 beta (cause 2 in NIGHTLY.md: the rewrite of the sound editor and
of the menus, horizontal menus; #4928, #4946, #4947, #4949, #4948, #4956), on the real firmware in the emulator.

For each context the menu is opened as a hand opens it, then walked with the hardware's inputs:
  synth       the synth clip PADA, SELECT                 kit-affect  the kit clip, affect-entire on, SELECT
  kit-row     the kit clip, affect-entire off, row 0 auditioned, SELECT
  midi, cv    PADB turned into a MIDI clip, then a CV clip (clip pad held + MIDI / CV in session view), SELECT
  audio       the audio clip, SELECT                      song        session view, SELECT (the song menu)
  arranger    arranger view, SELECT                       settings    SHIFT + SELECT
The walk, depth first: in a vertical menu the select encoder turned through every item (until the first comes back),
SELECT pressed on each; a submenu entered is walked the same way, a value item gets the select encoder (+2, -1), the
horizontal encoder (+1, -1), a gold knob pressed and turned, then BACK. In a horizontal menu (OLED, SETTINGS >
COMMUNITY FEATURES > HORIZONTAL MENUS on) every page (CROSS) and every column (SYNTH, KIT, MIDI, CV: once to select the
field, its value turned as above, once more for the field's action, which enters a submenu), and SHIFT + CROSS /
SCALE (the chained menus). Every fifth value item is left another way, in turn: SONG, CLIP, a long BACK, a grid pad
(exitCompletely()); the walk then opens the context again and goes back to where it was. With --shortcuts, also
SHIFT + every shortcut pad (x 0..15, y 0..7) in each clip context, the item exercised and left in the same rotation.
Recorded: crashes (emulator errors, writes through a null pointer, faults), freezeWithError() (E codes), hangs (an
input not back within its limit), wild accesses outside RAM and the peripherals, error popups. After a problem the
Deluge boots again and the walk goes on with the next context.

Usage: menu_walk_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR [--7seg] [--hmenus on|off]
                        [--contexts synth,kit-affect,...] [--shortcuts] [--max-depth N] [--max-inputs N]
Results: <out>/walk.json; last line PASS (no problem) or FAIL (the problems); exit 0/1."""
import argparse
import collections
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # beta-1.3/tests
import rig13  # noqa: E402,F401  (the paths of mastertune's rig)
import fuzz_ui  # noqa: E402
import song_emu as se  # noqa: E402
import stress_ui_emu as su  # noqa: E402

CONTEXTS = ["song", "arranger", "synth", "kit-affect", "kit-row", "audio", "midi", "cv", "settings"]
COLUMN_BUTTONS = ["SYNTH", "KIT", "MIDI", "CV"]
EXITS = ["SONG", "CLIP", "long BACK", "pad"]
SKIP = ["tartStemExport", "StemExportMenu"]  # Starts an offline stem export: minutes inside one press
_GDB = {}


class Lost(Exception):
    """The walk can't get back to where it was."""


class Walker:
    def __init__(self, rig, inp, oled, hmenus, log, max_depth=6, max_inputs=10**9):
        self.rig, self.inp, self.oled, self.log = rig, inp, oled, log
        self.emu = emu = rig.emu
        sym = emu.sym
        self.max_depth, self.max_inputs = max_depth, max_inputs
        self.inputs = 0
        self.deadline = None
        key = emu.elf
        if key not in _GDB:
            _GDB[key] = se.gdb_values(emu, [
                "(int)&((SoundEditor*)0)->navigationDepth", "(int)&((SoundEditor*)0)->menuItemNavigationRecord",
                "(int)&soundEditorRootMenu.items - (int)&soundEditorRootMenu",
                "(int)&soundEditorRootMenu.current_item_ - (int)&soundEditorRootMenu",
                "(int)&randomizerMenu.paging.visiblePageItems - (int)&randomizerMenu",
                "(int)&randomizerMenu.paging.totalPages - (int)&randomizerMenu",
                "sizeof(RuntimeFeatureSetting)", "(int)&((RuntimeFeatureSetting*)0)->value",
                "(int)&runtimeFeatureSettings.settings", "(int)HorizontalMenus",
                "(int)&((InstrumentClip*)0)->affectEntire", "(int)&((Song*)0)->sessionClips",
                "(int)&((Song*)0)->songViewYScroll", "(int)&((Clip*)0)->output", "(int)&((Output*)0)->type",
                "(int)&((Clip*)0)->type", "(int)OutputType::KIT", "(int)OutputType::SYNTH",
                "(int)OutputType::MIDI_OUT", "(int)OutputType::CV", "(int)OutputType::AUDIO"])
        (self.depth_off, self.rec_off, self.items_off, self.cur_off, self.page_items_off, self.pages_off,
         feat_size, feat_value, feat_base, hm_index, self.affect_off, self.clips_off, self.yscroll_off,
         self.output_off, self.otype_off, self.ctype_off, *types) = _GDB[key]
        self.types = dict(zip(("KIT", "SYNTH", "MIDI", "CV", "AUDIO"), types))
        self.hmenus_at = feat_base + hm_index * feat_size + feat_value
        self.editor = sym["soundEditor"]
        # The class of a menu item from its vtable's functions (identical code folding merges the trivial virtuals,
        # so no slot of isSubmenu() can be trusted): a Submenu has Submenu's or HorizontalMenu's functions in it
        self.hstyle = sym["_ZNK6deluge3gui9menu_item14HorizontalMenu14renderingStyleEv"]
        self.vt_cache = {}
        self.debounce = next(v[0] for k, v in sym.by_name.items() if k.startswith("_ZZN6deluge3gui9menu_item14HorizontalMenu12buttonAction") and k.endswith("last_navigation_buttons_press_time"))
        import subprocess
        self.data = []
        for line in subprocess.run([emu.tool_prefix + "nm", "-S", "-C", "--defined-only", emu.elf], capture_output=True,
                                   text=True).stdout.splitlines():
            p = line.split(maxsplit=3)
            if len(p) == 4 and p[2] in "bBdD":
                self.data.append((int(p[0], 16), int(p[1], 16), p[3].replace("deluge::gui::menu_item::", "")))
        self.data.sort()
        self.starts = [d[0] for d in self.data]
        self.set_hmenus(hmenus)
        self.visited = set()
        self.leaves = 0
        self.events = []  # Where the walk went somewhere unexpected (not problems)
        self.trace = collections.deque(maxlen=60)
        self.items_seen = set()

    # --- state
    def name(self, p):
        import bisect
        i = bisect.bisect_right(self.starts, p) - 1
        if i >= 0 and self.data[i][0] <= p < self.data[i][0] + max(self.data[i][1], 1):
            a, s, n = self.data[i]
            return n if p == a else f"{n}+{p - a:#x}"
        return f"{p:#x}"

    def set_hmenus(self, on):
        if on is not None:
            self.emu.uc.mem_write(self.hmenus_at, struct.pack("<I", 1 if on else 0))

    def hmenus(self):
        return self.emu.u32(self.hmenus_at)

    def vtable(self, item):
        vt = self.emu.u32(item)
        if vt not in self.vt_cache:
            names = []
            for i in range(90):
                f = self.emu.u32(vt + 4 * i)
                if not 0x20020000 <= f < 0x201F0000:
                    break
                names.append(self.emu.sym.name_at(f & ~1))
            self.vt_cache[vt] = names
        return self.vt_cache[vt]

    def is_submenu(self, item):
        return any("menu_item::Submenu::" in n or "menu_item::HorizontalMenu::" in n for n in self.vtable(item))

    def horizontal(self, item):
        if not any("HorizontalMenu::renderingStyle" in n for n in self.vtable(item)):
            return False
        return self.rig.guard("renderingStyle()", lambda: self.emu.call(self.hstyle, item), 1) & 0xFF == 1

    def ui(self):
        ui = self.rig.ui_name()
        return self.name(int(ui, 16)) if ui.startswith("0x") else ui

    def where(self):
        ui = self.ui()
        d = self.emu.u8(self.editor + self.depth_off)
        item = self.emu.u32(self.editor + self.rec_off + 4 * d) if d < 16 else 0
        return ui, d, item

    def kids(self, m):
        b, e = self.emu.u32(m + self.items_off), self.emu.u32(m + self.items_off + 4)
        return [self.emu.u32(p) for p in range(b, e, 4)] if 0 <= e - b < 400 else []

    def cur(self, m):
        b, e = self.emu.u32(m + self.items_off), self.emu.u32(m + self.items_off + 4)
        p = self.emu.u32(m + self.cur_off)
        return self.emu.u32(p) if b <= p < e else None

    def pages(self, m):
        return self.emu.u8(m + self.pages_off)

    def page_items(self, m):
        p, n = self.emu.u32(m + self.page_items_off), self.emu.u32(m + self.page_items_off + 4)
        return [self.emu.u32(p + 4 * i) for i in range(min(n, 8))]

    # --- inputs (each followed by a little of the task manager: the UI and OLED tasks render)
    def _count(self, d):
        if os.environ.get("VERBOSE"):
            ui, dep, item = self.where()
            self.log(f"    {self.inputs} {d:24s} @ {ui} d{dep} {self.name(item) if ui == 'soundEditor' else ''}")
        self.inputs += 1
        self.trace.append(f"{self.emu.seconds():.2f}s {d}")
        if self.inputs >= self.max_inputs or (self.deadline and time.time() > self.deadline):
            raise Budget()

    def press(self, b, hold=0.02, after=0.03, limit_s=60):
        self._count(b)
        if b in COLUMN_BUTTONS or b in ("CROSS", "SCALE"):
            self.emu.uc.mem_write(self.debounce, bytes(8))  # As if 0.14 s had passed (the buttons' debounce)
        self.inp.button(b, True, limit_s=limit_s)
        self.rig.tm(hold, f"{b} held")
        self.inp.button(b, False, limit_s=limit_s)
        self.rig.tm(after, f"after {b}")
        self.null.check(b)

    def press_with(self, held, b, after=0.04):
        self._count(f"{held}+{b}")
        self.emu.uc.mem_write(self.debounce, bytes(8))
        self.inp.button(held, True)
        self.rig.tm(0.03, f"{held} held")
        self.inp.button(b, True, limit_s=60)
        self.rig.tm(0.03, f"{held}+{b}")
        self.inp.button(b, False)
        self.inp.button(held, False)
        self.rig.tm(after, f"after {held}+{b}")
        self.null.check(f"{held}+{b}")

    def long_back(self):
        self._count("long BACK")
        self.inp.button("BACK", True)
        self.rig.tm(0.8, "BACK held")
        self.inp.button("BACK", False)
        self.rig.tm(0.2, "after long BACK")
        self.null.check("long BACK")

    def turn(self, enc, n, after=0.03):
        self._count(f"turn {enc} {n:+d}")
        self.inp.turn(enc, n)
        self.rig.tm(after, f"turn {enc}")
        self.null.check(f"turn {enc} {n:+d}")

    def pad(self, x, y, shift=False, hold=0.04, after=0.08):
        self._count(("SHIFT+" if shift else "") + f"pad {x},{y}")
        if shift:
            self.inp.button("SHIFT", True)
        self.inp.pad(x, y, 100)
        self.rig.tm(hold, "pad held")
        self.inp.pad(x, y, 0)
        if shift:
            self.inp.button("SHIFT", False)
        self.rig.tm(after, "after pad")
        self.null.check(f"pad {x},{y}")

    # --- song and views
    def song(self):
        return self.rig.song()

    def clips(self):
        emu, rig = self.emu, self.rig
        arr = self.song() + self.clips_off
        _, _, memory, count, msize, mstart, esize = rig.heap_offsets
        mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
        return [emu.u32(mem + ((first + i) % max(size, 1)) * es) for i in range(n)]

    def otype(self, clip):
        return self.emu.u8(self.emu.u32(clip + self.output_off) + self.otype_off)

    def clip_index(self, kind):
        return next((i for i, c in enumerate(self.clips()) if self.otype(c) == self.types[kind]), None)

    def root(self):
        return self.rig.ui_name(root=True)

    def to_session(self):
        for _ in range(8):
            ui, root = self.rig.ui_name(), self.root()
            if ui == root == "sessionView" and self.rig.mode() == 0:
                return
            if ui != root:
                self.press("BACK", after=0.15)
            elif root == "arrangerView":
                self.press("SONG", after=0.4)
            else:
                self.press("SONG", after=0.4)
        raise Lost(f"no way back to session view: {self.rig.ui_name()} / {self.root()}")

    def scroll_to(self, index, y=6):
        for _ in range(20):
            s = struct.unpack("<i", self.emu.uc.mem_read(self.song() + self.yscroll_off, 4))[0]
            if index - s == y:
                return
            self.turn("scrollY", 1 if index - s > y else -1)
        raise Lost(f"can't scroll to clip {index}")

    def enter_clip(self, index):
        self.to_session()
        self.scroll_to(index)
        self.pad(0, 6, after=0.5)
        if self.root() not in ("instrumentClipView", "audioClipView", "automationView", "keyboardScreen"):
            raise Lost(f"clip {index} not entered: {self.root()}")

    def convert(self, index, button):
        """Session view: the clip's pad held, a type button pressed (SYNTH / KIT / MIDI / CV)."""
        self.to_session()
        self.scroll_to(index)
        self._count(f"pad 0,6 held + {button}")
        self.inp.pad(0, 6, 100)
        self.rig.tm(0.1, "clip pad held")
        self.inp.button(button, True)
        self.rig.tm(0.05, f"+ {button}")
        self.inp.button(button, False)
        self.inp.pad(0, 6, 0)
        self.rig.tm(0.3, "type changed")

    def open_context(self, ctx, select=True):
        """The view of the context; with select, its menu opened (True if the sound editor is the current UI)."""
        if ctx in ("synth", "kit-affect", "kit-row", "audio", "midi", "cv"):
            kind = {"synth": "SYNTH", "kit-affect": "KIT", "kit-row": "KIT", "audio": "AUDIO", "midi": "MIDI",
                    "cv": "CV"}[ctx]
            i = self.clip_index(kind)
            if i is None and ctx in ("midi", "cv"):
                synths = [j for j, c in enumerate(self.clips()) if self.otype(c) in (self.types["SYNTH"],
                                                                                     self.types["MIDI"])]
                self.convert(synths[-1], ctx.upper())
                i = self.clip_index(kind)
            if i is None:
                raise Lost(f"no {kind} clip")
            self.enter_clip(i)
            if ctx.startswith("kit"):
                want = 1 if ctx == "kit-affect" else 0
                clip = self.clips()[i]
                if self.emu.u8(clip + self.affect_off) != want:
                    self.press("AFFECT", after=0.2)
                if self.emu.u8(clip + self.affect_off) != want:
                    raise Lost("affect-entire didn't change")
                if ctx == "kit-row":
                    self.pad(17, 0, after=0.2)  # Audition row 0: the row selected
        elif ctx in ("song", "settings"):
            self.to_session()
        elif ctx == "arranger":
            self.to_session()
            self.press("SONG", after=0.5)
            if self.root() != "arrangerView":
                raise Lost(f"arranger view not reached: {self.root()}")
        if not select:
            return True
        if ctx == "settings":
            self.press_with("SHIFT", "SELECT_ENC", after=0.3)
        else:
            self.press("SELECT_ENC", after=0.3)
        return self.rig.ui_name() == "soundEditor"

    # --- the walk
    def at(self, path):
        ui, d, item = self.where()
        return ui == "soundEditor" and d == path[-1][0] and item == path[-1][1]

    def leave_editor(self):
        for _ in range(12):
            if self.rig.ui_name() != "soundEditor" and self.rig.ui_name() == self.root():
                return
            self.press("BACK", after=0.1)
        raise Lost(f"menu not left: {self.where()}")

    def restore(self, ctx, path):
        """Back to path: BACK out of whatever opened (a browser, a popup UI), or the context opened again and the
        path replayed (each item chosen as the select encoder or a column button would choose it)."""
        for _ in range(3):
            if self.at(path):
                return
            ui, d, item = self.where()
            if ui == "soundEditor" and d > path[-1][0]:
                self.press("BACK", after=0.1)
            elif ui not in ("soundEditor", self.root()):
                self.press("BACK", after=0.2)
            else:
                break
        if self.at(path):
            return
        self.events.append(dict(ctx=ctx, event="reopen", path=[self.name(p) for _, p, _ in path], where=str(self.where())))
        try:
            if self.rig.ui_name() == "soundEditor":
                self.leave_editor()
            if path[0][2] is None:
                ok = self.open_context(ctx)
            else:
                self.open_context(ctx, select=False)
                _, x, y = path[0][2]
                self.pad(x, y, shift=True, after=0.2)
                ok = self.rig.ui_name() == "soundEditor"
        except Lost as ex:
            raise Lost(f"reopen: {ex}") from None
        if not ok or not self.at(path[:1]):
            raise Lost(f"reopened at {self.where()}, not {self.name(path[0][1])}")
        for (d, m, _), (d2, child, how) in zip(path, path[1:]):
            kid = how[-1] if how else None
            kids = self.kids(m)
            if kid in kids:  # Chosen as the walk chose it (the iterator set as the encoder or a button sets it)
                b = self.emu.u32(m + self.items_off)
                self.emu.uc.mem_write(m + self.cur_off, struct.pack("<I", b + 4 * kids.index(kid)))
            self.press("SELECT_ENC", after=0.1)
            if not self.at([(d2, child, how)]):
                raise Lost(f"replay: {self.name(child)} not entered, at {self.where()}")

    def exercise_value(self, label):
        """A value item (or a horizontal menu's field): its value, the horizontal encoder, a gold knob."""
        self.turn("select", 2)
        self.turn("select", -1)
        self.turn("scrollX", 1)
        self.press("MOD_ENC1")
        self.turn("mod0", 1)

    def other_exit(self, ctx, path, k):
        how = EXITS[k % len(EXITS)]
        if how == "long BACK":
            self.long_back()
        elif how == "pad":
            self.pad(15, 0, after=0.15)
        else:
            self.press(how, after=0.4)
        self.events.append(dict(ctx=ctx, event=f"left with {how}", at=self.name(path[-1][1]),
                                now=str(self.where()), root=self.root()))
        # On: whatever that left open closed again (e.g. CLIP from a song menu), then back to the walk
        self.restore(ctx, path)

    def walk(self, ctx, path):
        """path: [(depth, menu item, how it was entered)], the walk at its last item."""
        d, m, _ = path[-1]
        self.visited.add(m)
        self.items_seen.add(self.name(m))
        if not self.is_submenu(m):
            return self.leaf(ctx, path)
        if len(path) > self.max_depth:
            return
        if "recorder" in self.name(m).lower():  # RECORD AUDIO: ~25 s of host time a recording, once per run
            if getattr(self, "recorded", False):
                return
            self.recorded = True
        if self.horizontal(m):
            return self.walk_horizontal(ctx, path)
        # To the first item (a submenu remembers where it was; most don't wrap around): one turn back over them all
        n = len(self.kids(m))
        if n > 1 and self.cur(m) != self.kids(m)[0]:
            self.turn("select", -n)
            if not self.at(path):
                self.restore(ctx, path)
        seen = []
        for _ in range(60):
            c = self.cur(m)
            if c is None or c in seen:
                break
            seen.append(c)
            self.items_seen.add(self.name(c))
            if not any(s in self.name(c) for s in SKIP):
                self.press("SELECT_ENC", after=0.1)
                self.after_enter(ctx, path, c, ("sel", c))
            self.turn("select", 1)
            if not self.at(path):
                self.events.append(dict(ctx=ctx, event="moved by select +1", at=self.name(m), now=str(self.where())))
                self.restore(ctx, path)

    def after_enter(self, ctx, path, child, how):
        """After SELECT (or a column button) on child: walk what opened, come back."""
        ui, d, item = self.where()
        if ui == "soundEditor" and d == path[-1][0] + 1:
            if item not in self.visited:
                self.walk(ctx, path + [(d, item, how)])
            else:
                self.leaf(ctx, path + [(d, item, how)], quick=True)
            if self.rig.ui_name() == "soundEditor" and self.where()[1] > path[-1][0]:
                self.press("BACK", after=0.1)
        if not self.at(path):
            ui2 = self.where()
            if ui2[0] != "soundEditor" or ui2[1] != path[-1][0]:
                self.events.append(dict(ctx=ctx, event="enter went elsewhere", child=self.name(child),
                                        now=str(ui2), root=self.root()))
            self.restore(ctx, path)

    def leaf(self, ctx, path, quick=False):
        d, m, _ = path[-1]
        if not quick:
            self.exercise_value(self.name(m))
            self.leaves += 1
            if self.leaves % 5 == 0:
                self.other_exit(ctx, path, self.leaves // 5)
        else:
            self.turn("select", 1)

    def walk_horizontal(self, ctx, path):
        d, m, _ = path[-1]
        seen = set()
        pages = max(1, self.pages(m))
        for page in range(min(pages, 8)):
            for col in range(4):
                prev = self.cur(m)
                self.press(COLUMN_BUTTONS[col], after=0.04)  # The debounce: 0.14 s between these buttons
                if not self.at(path):  # It was the current field already: its action
                    self.after_enter(ctx, path, prev, ("col", col, prev))
                    continue
                c = self.cur(m)
                if c is None or c in seen:
                    continue
                seen.add(c)
                self.items_seen.add(self.name(c))
                if any(s in self.name(c) for s in SKIP):
                    continue
                if not self.is_submenu(c):
                    self.exercise_value(self.name(c))
                    if not self.at(path):
                        self.restore(ctx, path)
                    self.leaves += 1
                    if self.leaves % 5 == 0:  # Left another way from this field (SONG, CLIP, long BACK, a pad)
                        self.other_exit(ctx, path, self.leaves // 5)
                        self.press(COLUMN_BUTTONS[col], after=0.04)  # The field chosen again
                        if not self.at(path):
                            self.restore(ctx, path)
                            continue
                # Pressed again: the field's action (a submenu entered, a toggle toggled, a session begun)
                self.press(COLUMN_BUTTONS[col], after=0.04)
                if not self.at(path):
                    self.after_enter(ctx, path, c, ("col", col, c))
            self.press("CROSS", after=0.04)
            if not self.at(path):
                self.restore(ctx, path)
        # The chained menus (SHIFT + CROSS / SCALE): there and back
        self.press_with("SHIFT", "CROSS", after=0.04)
        moved = self.where()
        self.press_with("SHIFT", "SCALE", after=0.04)
        if not self.at(path):
            self.events.append(dict(ctx=ctx, event="chain there and back", at=self.name(m), via=self.name(moved[2]),
                                    now=str(self.where())))
            self.restore(ctx, path)

    def walk_context(self, ctx):
        self.visited = set()
        if not self.open_context(ctx):
            raise Lost(f"{ctx}: the menu didn't open ({self.rig.ui_name()})")
        ui, d, item = self.where()
        self.log(f"[{ctx}] opened at depth {d}: {self.name(item)}, horizontal {self.horizontal(item)}")
        self.walk(ctx, [(d, item, None)])
        self.leave_editor()

    def shortcuts(self, ctx):
        """SHIFT + every shortcut pad in the context's view: the item it opens exercised, left in the rotation."""
        self.open_context(ctx, select=False)
        k = 0
        for y in range(8):
            for x in range(16):
                self.pad(x, y, shift=True, after=0.15)
                ui, d, item = self.where()
                if ui != "soundEditor":
                    if ui != self.root():
                        self.press("BACK", after=0.2)
                    continue
                self.items_seen.add(self.name(item))
                path = [(d, item, ("pad", x, y))]
                if self.horizontal(item):
                    c = self.cur(item)
                    if c is not None and not self.is_submenu(c):
                        self.exercise_value(self.name(c))
                elif not self.is_submenu(item):
                    self.exercise_value(self.name(item))
                else:
                    self.turn("select", 1)
                k += 1
                how = (["BACK"] + EXITS)[k % (len(EXITS) + 1)]
                if how == "BACK":
                    self.leave_editor()
                elif how == "long BACK":
                    self.long_back()
                elif how == "pad":
                    self.pad(15, 0, after=0.15)
                else:
                    self.press(how, after=0.4)
                if self.rig.ui_name() == "soundEditor":
                    self.leave_editor()
                if self.rig.ui_name() != self.root() or not self._in_context_view(ctx):
                    self.events.append(dict(ctx=ctx, event=f"shortcut {x},{y} left with {how}",
                                            item=self.name(item), now=self.root()))
                    self.open_context(ctx, select=False)
                del path

    def _in_context_view(self, ctx):
        root = self.root()
        if ctx in ("song", "settings"):
            return root == "sessionView"
        if ctx == "arranger":
            return root == "arrangerView"
        return root in ("instrumentClipView", "audioClipView")


class Budget(Exception):
    pass


def run(a, contexts, res, log):
    """Boots, walks the contexts in turn; after a problem boots again and goes on with the next context."""
    todo = list(contexts)
    boots = 0
    while todo and boots < len(contexts) + 2:
        boots += 1
        out = os.path.join(a.out, f"boot{boots}")
        os.makedirs(out, exist_ok=True)
        image = os.path.join(out, "sd.img")
        if os.path.exists(image):
            os.remove(image)
        fuzz_ui.make_card(image, 2, os.path.join(a.out, "card-template.img"))
        rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, not a.seven)
        inp = fuzz_ui.Inputs(rig, "v13")
        w = Walker(rig, inp, not a.seven, None if a.hmenus == "default" else a.hmenus == "on", log,
                   a.max_depth, a.max_inputs)
        w.null = fuzz_ui.NullPage(rig.emu)
        fuzz_ui.ModalRecorder(rig.emu)
        rig.emu.uc.ctl_flush_tb()
        hm = w.hmenus()
        while todo:
            ctx = todo.pop(0)
            t0, n0 = time.time(), w.inputs
            w.deadline = t0 + a.context_minutes * 60 if a.context_minutes else None
            entry = dict(ctx=ctx, boot=boots, hmenus=hm, display="OLED" if not a.seven else "7SEG")
            try:
                if not a.no_walk:
                    w.walk_context(ctx)
                if a.shortcuts and ctx not in ("settings",):
                    w.shortcuts(ctx)
                entry["result"] = "done"
            except Lost as ex:
                entry["result"] = f"lost: {ex}"
            except Budget:
                entry["result"] = "budget"
            except su.Stop:
                p = rig.problems[-1] if rig.problems else {}
                try:
                    import emu13
                    p["backtrace"] = emu13.backtrace(rig.emu)
                except Exception:  # noqa: BLE001
                    pass
                entry["result"] = "PROBLEM"
                entry["problem"] = p
                entry["last_inputs"] = list(w.trace)
            if ctx == "settings":
                w.set_hmenus(hm)  # The walk may have switched horizontal menus over
            entry.update(inputs=w.inputs - n0, host_s=round(time.time() - t0), leaves=w.leaves,
                         items=len(w.items_seen))
            log(f"[{ctx}] {entry['result']}: {entry['inputs']} inputs, {entry['host_s']} s, {len(w.items_seen)} items"
                f" seen so far" + (f" -- {entry.get('problem')}" if entry["result"] == "PROBLEM" else ""))
            res["contexts"].append(entry)
            res["events"] += w.events
            w.events = []
            if entry["result"] == "PROBLEM":
                break
        res["null_writes"] += [dict(boot=boots, **x) for x in w.null.writes[:20]]
        res["invalid"] += [dict(boot=boots, page=hex(k[0]), at=k[1], during=k[2], n=c)
                           for k, c in rig.invalid.most_common(10)]
        res["error_popups"] += [dict(boot=boots, popup=p) for p in rig.error_popups()
                                if not (p[2] == "displayError" and p[3] == "Error 10")][:10]
        res["items_seen"] = sorted(set(res.get("items_seen", [])) | w.items_seen)
        json.dump(res, open(os.path.join(a.out, "walk.json"), "w"), indent=1, default=str)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--7seg", dest="seven", action="store_true")
    ap.add_argument("--hmenus", choices=("on", "off", "default"), default="default")
    ap.add_argument("--contexts", default=",".join(CONTEXTS))
    ap.add_argument("--shortcuts", action="store_true")
    ap.add_argument("--no-walk", action="store_true", help="with --shortcuts: the shortcuts only")
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--max-inputs", type=int, default=10**9, help="per boot")
    ap.add_argument("--context-minutes", type=float, default=40, help="host time per context (0: no limit)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    res = dict(elf=a.elf, display="7SEG" if a.seven else "OLED", hmenus=a.hmenus, contexts=[], events=[],
               null_writes=[], invalid=[], error_popups=[])
    t0 = time.time()

    def log(s):
        print(f"{time.time() - t0:6.0f}s {s}", flush=True)
    run(a, a.contexts.split(","), res, log)
    res["host_s"] = round(time.time() - t0)
    json.dump(res, open(os.path.join(a.out, "walk.json"), "w"), indent=1, default=str)
    problems = [c for c in res["contexts"] if c["result"] == "PROBLEM"]
    for c in res["contexts"]:
        print(f"  {c['ctx']}: {c['result']} ({c['inputs']} inputs, {c['host_s']} s)"
              + (f" {c['problem'].get('kind')} {c['problem'].get('detail')} during {c['problem'].get('what')}"
                 if c["result"] == "PROBLEM" else ""))
    print(f"  items seen {len(res['items_seen'])}, null writes {len(res['null_writes'])}, wild accesses "
          f"{len(res['invalid'])}, error popups {len(res['error_popups'])}")
    bad = problems or res["null_writes"] or res["invalid"]
    print("PASS: no crash, freeze, hang, null write or wild access" if not bad else
          "FAIL: " + "; ".join([f"{c['ctx']}: {c['problem'].get('kind')} {c['problem'].get('detail')}"
                                for c in problems] + [f"{len(res['null_writes'])} null writes"] * bool(
              res["null_writes"]) + [f"{len(res['invalid'])} wild accesses"] * bool(res["invalid"])))
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
