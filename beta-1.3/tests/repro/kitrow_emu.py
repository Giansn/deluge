#!/usr/bin/env python3
"""Upstream #4518 and #4951 on the v1.3 beta (62a516c2): a synth preset loaded into a kit row, and a long BACK in a
browser. Three checks, on two boots of the real firmware in the emulator (OLED):

Boot 1, the long BACK (#4518, family B). Buttons::buttonAction() arms the BACK_MENU_EXIT timer (400 ms) on every BACK
press; when it fires, Browser::exitUI() calls exitAction(). A press at a browser's root folder has already called
exitAction() itself (Browser::backButtonAction()), so a BACK held for a second runs it a second time if the browser is
still the current UI by then:
  (3a) session view, LOAD (the song browser, its scroll-in), BACK held 1 s: LoadSongUI::exitAction() calls counted
  (3b) the synth clip PADA, LOAD + SYNTH (the synth preset browser), three presets previewed, BACK held 1 s:
       LoadInstrumentPresetUI::exitAction() calls counted
Each must run exactly once, and the browser must be closed afterwards. On 62a516c2 both already do (the song
browser's scroll-out takes 8 x 28 ms, the synth browser closes at once: neither is current when the timer fires), so
these two are a regression guard for the BACK_MENU_EXIT change, not a reproduction.

Boot 2, the kit row (#4518 family A, #4951). Session view, the kit clip entered, AFFECT (row mode), audition pad 17,0
held + SYNTH (the synth browser for that row), the select encoder on until two presets have been loaded into the row
with the browser still open (LoadInstrumentPresetUI::performLoadSynthToKit()), then:
  (1) view.activeModControllableModelStack.modControllable must point at the row's new drum, not into a block freed
      during the swap (MemoryRegion::dealloc() hooked) or into the allocator's free space
  (2) the select encoder pressed (the preset kept, the browser closed), SHIFT + the LPF frequency shortcut (the sound
      editor on that row), LEARN held and the upper gold knob turned: Sound::learnKnob() walks the Song's backed-up
      ParamManagers of the drum (ensureInaccessibleParamPresetValuesWithoutKnobsAreZero()); an entry emptied by the load
      freezes there with E412. No freeze, crash or hang allowed.
On 62a516c2: (1) the view still points at the first freed drum after both loads, (2) E412 in
ParamManager::getPatchCableSet(). Encoder turns wake v1.3's self-blocking encoder task (see Inputs below).

Usage: kitrow_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS (all checks hold) or FAIL (which ones didn't); exit 0/1."""
import argparse
import collections
import json
import os
import struct
import sys

RIG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # beta-1.3/tests
sys.path.insert(0, RIG)
import rig13  # noqa: E402,F401  (sets up the paths of mastertune's rig)
import fuzz_ui  # noqa: E402
import song_emu as se  # noqa: E402
import stress_ui_emu as su  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R1  # noqa: E402

EXIT_ACTIONS = ("_ZN10LoadSongUI10exitActionEv", "_ZN22LoadInstrumentPresetUI10exitActionEv",
                "_ZN13SampleBrowser10exitActionEv", "_ZN7Browser10exitActionEv")


class Inputs(fuzz_ui.Inputs):
    """fuzz_ui.Inputs, with the encoders' interrupt completed: v1.3's encoder task blocks itself
    (interpretEncodersTask(): blockTask(EncoderTaskID)) until the encoder IRQ unblocks it after applyEdges(), so a turn
    written into the encoder alone is never actioned. Here unblockTask(EncoderTaskID) follows, as in the IRQ."""

    def __init__(self, rig, firmware):
        super().__init__(rig, firmware)
        sym = rig.emu.sym
        self.unblock = sym["unblockTask"]
        self.task_id = sym["_ZN6deluge3hid8encoders13EncoderTaskIDE"]

    def turn(self, name, n):
        super().turn(name, n)
        task_id = struct.unpack("<b", self.rig.emu.uc.mem_read(self.task_id, 1))[0]
        self.rig.action(f"turn {name} {n:+d}", self.unblock, task_id & 0xFF)


def boot(a, label):
    out = os.path.join(a.out, label)
    os.makedirs(out, exist_ok=True)
    image = os.path.join(out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    fuzz_ui.make_card(image, 2, os.path.join(a.out, "card-template.img"))
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    sym = rig.emu.sym
    rig.ui_names[sym["loadInstrumentPresetUI"]] = "loadInstrumentPresetUI"
    rig.calls = collections.Counter()
    for name in EXIT_ACTIONS:
        rig.emu.intercept(sym.find(name), lambda e, n=name: rig.calls.update([n]) and None)
    rig.emu.uc.ctl_flush_tb()
    return rig, Inputs(rig, "v13")


class Song:
    """The song's session clips (make_card's song with 2 synths: PADA, PADB, the kit, the audio track) and the
    session view's scroll: clip i sits on y = i - songViewYScroll."""

    def __init__(self, rig):
        self.rig, emu = rig, rig.emu
        (self.clips_off, self.current_off, self.yscroll_off, self.output_off, self.otype_off,
         self.kit_type) = su.gdb_ints(emu, [
             "list Song::Song", "print (int)&((Song*)0)->sessionClips", "print (int)&((Song*)0)->currentClip",
             "print (int)&((Song*)0)->songViewYScroll", "print (int)&((Clip*)0)->output",
             "print (int)&((Output*)0)->type", "print (int)OutputType::KIT"])

    def clips(self):
        emu, rig = self.rig.emu, self.rig
        arr = rig.song() + self.clips_off
        _, _, memory, count, msize, mstart, esize = rig.heap_offsets
        mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
        return [emu.u32(mem + ((first + i) % max(size, 1)) * es) for i in range(n)]

    def is_kit(self, clip):
        emu = self.rig.emu
        return emu.u8(emu.u32(clip + self.output_off) + self.otype_off) == self.kit_type

    def scroll(self):
        return struct.unpack("<i", self.rig.emu.uc.mem_read(self.rig.song() + self.yscroll_off, 4))[0]

    def current(self):
        return self.rig.emu.u32(self.rig.song() + self.current_off)

    def enter(self, inp, index):
        """Scrolls until clip index is on y=6, presses its pad: the clip view."""
        rig = self.rig
        for _ in range(16):
            if index - self.scroll() == 6:
                break
            inp.turn("scrollY", 1 if index - self.scroll() > 6 else -1)
            rig.tm(0.05, "scrollY")
        print(f"enter clip {index}: scroll {self.scroll()}, clips {len(self.clips())}", flush=True)
        inp.pad(0, 6, 100)
        rig.tm(0.05, "clip pad held")
        inp.pad(0, 6, 0)
        rig.tm(1.0, "enter clip")
        return self.current() == self.clips()[index]


def long_back(rig, inp, name, seconds=1.0):
    before = rig.calls[name]
    inp.button("BACK", True)
    rig.tm(seconds, "BACK held")
    inp.button("BACK", False)
    rig.tm(1.0, "after BACK")
    return rig.calls[name] - before


def boot1(a, res):
    """(3a), (3b): the long BACK in the song browser and in the synth browser."""
    rig, inp = boot(a, "boot1")
    try:
        song = Song(rig)
        # (3a) the song browser
        inp.button("LOAD", True)
        rig.tm(0.05, "LOAD held")
        inp.button("LOAD", False)
        rig.tm(0.5, "song browser opens")
        rig.wait_mode_none()
        opened = rig.ui_name()
        n = long_back(rig, inp, "_ZN10LoadSongUI10exitActionEv")
        res["3a"] = dict(opened=opened, exit_actions=n, ui_after=rig.ui_name(), ok=opened == "loadSongUI" and n == 1
                         and rig.ui_name() == "sessionView")
        print("3a", json.dumps(res["3a"]), flush=True)
        # (3b) the synth browser (LOAD held + SYNTH) on the synth clip PADA (clip 0), a preset previewed first
        entered = song.enter(inp, 0)
        inp.button("LOAD", True)
        rig.tm(0.05, "LOAD held")
        inp.button("SYNTH", True)
        rig.tm(0.05, "LOAD + SYNTH")
        inp.button("SYNTH", False)
        inp.button("LOAD", False)
        rig.tm(1.0, "synth browser opens")
        opened = rig.ui_name()
        for _ in range(3):
            inp.turn("select", 1)
            rig.tm(0.7, "select +1 (preview)")
        n = long_back(rig, inp, "_ZN22LoadInstrumentPresetUI10exitActionEv")
        res["3b"] = dict(entered=entered, opened=opened, exit_actions=n, ui_after=rig.ui_name(),
                         ok=opened == "loadInstrumentPresetUI" and n == 1 and rig.ui_name() == "instrumentClipView")
        print("3b", json.dumps(res["3b"]), flush=True)
    except su.Stop:
        res["boot1_stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("boot 1 STOPPED", res["boot1_stopped"], flush=True)
    res["boot1_problems"] = rig.problems
    res["boot1_error_popups"] = rig.error_popups()


def boot2(a, res):
    """(1), (2): the synth presets into the kit row, then LEARN + a gold knob on it."""
    rig, inp = boot(a, "boot2")
    emu, sym = rig.emu, rig.emu.sym
    mc_off, drum_off = se.gdb_values(emu, [
        "(int)&((View*)0)->activeModControllableModelStack.modControllable",
        "(int)&loadInstrumentPresetUI.soundDrumToReplace - (int)&loadInstrumentPresetUI"])
    view, browser = sym["view"], sym["loadInstrumentPresetUI"]
    mc = lambda: emu.u32(view + mc_off)  # noqa: E731
    freed, swaps = [], []

    def on_dealloc(e):
        p = e.uc.reg_read(UC_ARM_REG_R1)
        freed.append((p, e.u32(p - 4) & 0x3FFFFFFF))
    emu.intercept(sym["_ZN12MemoryRegion7deallocEPv"], on_dealloc)
    emu.intercept(sym.find("_ZN22LoadInstrumentPresetUI21performLoadSynthToKit"),
                  lambda e: swaps.append(dict(old_drum=emu.u32(browser + drum_off), freed_from=len(freed))) and None)
    learns, mod_actions = [], []
    emu.intercept(sym.find("_ZN5Sound9learnKnobE"), lambda e: learns.append(rig.action_name) and None)
    emu.intercept(sym.find("_ZN11SoundEditor16modEncoderActionE"),
                  lambda e: mod_actions.append((rig.action_name, rig.mode())) and None)
    emu.uc.ctl_flush_tb()

    def empty_spaces():
        region_size, empty, memory, count, msize, mstart, esize = rig.heap_offsets
        base = sym["_ZZN22GeneralMemoryAllocator3getEvE22generalMemoryAllocator"]
        out = []
        for r in range(3):
            arr = base + r * region_size + empty
            mem, n, size, first, es = (emu.u32(arr + o) for o in (memory, count, msize, mstart, esize))
            for i in range(n):
                rec = mem + ((first + i) % max(size, 1)) * es
                out.append((emu.u32(rec + 4), emu.u32(rec)))  # (address, length)
        return out

    def state(label):
        p, sw, new_drum = mc(), swaps[-1] if swaps else {}, emu.u32(browser + drum_off)
        recent = freed[sw.get("freed_from", 0):]
        r = dict(label=label, ui=rig.ui_name(), swaps=len(swaps), file=rig.browser_name(), mod_controllable=hex(p),
                 old_drum=hex(sw.get("old_drum", 0)), new_drum=hex(new_drum), points_at_new_drum=p == new_drum,
                 mc_in_block_freed_in_swap=any(f <= p < f + n for f, n in recent),
                 mc_in_free_space_now=any(addr <= p < addr + length for addr, length in empty_spaces()))
        print(json.dumps(r), flush=True)
        return r

    # The LPF frequency shortcut pad (modulation/params/param.h: patchedParamShortcuts[8][7] = LOCAL_LPF_FREQ)
    lpf_pad = (8, 7)
    try:
        song = Song(rig)
        kit_i = next(i for i, c in enumerate(song.clips()) if song.is_kit(c))
        print("clips", [("KIT" if song.is_kit(c) else "other") for c in song.clips()], "kit", kit_i, flush=True)
        res["kit_entered"] = song.enter(inp, kit_i)
        inp.button("AFFECT", True)
        rig.tm(0.05, "AFFECT held")
        inp.button("AFFECT", False)
        rig.tm(0.2, "row mode")
        inp.pad(17, 0, 100)
        rig.tm(0.1, "audition pad held")
        inp.button("SYNTH", True)
        rig.tm(0.1, "SYNTH held")
        inp.button("SYNTH", False)
        rig.tm(0.5, "browser open")
        inp.pad(17, 0, 0)
        rig.tm(0.5, "browser open, pad released")
        res["browser"] = rig.ui_name()
        # The browser lists the song's own synths first (PADA, PADB: in memory only, FILE_NOT_SAVED, the old drum
        # stays), then the preset files: the select encoder on until two of them have been loaded into the row
        ok_swaps = []
        for i in range(8):
            inp.turn("select", 1)
            rig.tm(1.0, "select +1")
            r = state(f"select +1 #{i + 1}")
            if r["new_drum"] != r["old_drum"]:
                ok_swaps.append(r)
                if len(ok_swaps) == 2:
                    break
        res["1"] = dict(swaps=ok_swaps, ok=len(ok_swaps) == 2 and all(
            s["points_at_new_drum"] and not s["mc_in_block_freed_in_swap"] and not s["mc_in_free_space_now"]
            for s in ok_swaps))
        print("1", json.dumps(dict(ok=res["1"]["ok"])), flush=True)
        # (2) keep the preset, the sound editor on the row, LEARN + the upper gold knob
        inp.button("SELECT_ENC", True)
        rig.tm(0.05, "select pressed")
        inp.button("SELECT_ENC", False)
        rig.tm(1.0, "preset kept")
        after_select = rig.ui_name()
        inp.button("SHIFT", True)
        inp.pad(*lpf_pad, 100)
        rig.tm(0.05, "LPF shortcut")
        inp.pad(*lpf_pad, 0)
        inp.button("SHIFT", False)
        rig.tm(0.5, "sound editor")
        editor = rig.ui_name()
        inp.button("LEARN", True)
        rig.tm(0.1, "LEARN held")
        learn_mode = rig.mode()
        for _ in range(2):
            inp.turn("mod1", 2)
            rig.tm(0.3, "LEARN held, gold knob turned")
        inp.button("LEARN", False)
        rig.tm(0.3, "LEARN released")
        res["2"] = dict(ui_after_select=after_select, lpf_pad=lpf_pad, editor=editor, learn_mode=learn_mode,
                        mod_actions=mod_actions, learn_knob_calls=len(learns),
                        ok=editor == "soundEditor" and len(learns) >= 1)
        print("2", json.dumps(res["2"]), flush=True)
    except su.Stop:
        res["boot2_stopped"] = rig.problems[-1] if rig.problems else "stop"
        print("boot 2 STOPPED", res["boot2_stopped"], flush=True)
    res["boot2_problems"] = rig.problems
    res["boot2_error_popups"] = rig.error_popups()
    res["learn_knob_calls"] = learns


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", choices=("1", "2"), help=argparse.SUPPRESS)  # One boot only (debugging)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    res = dict(elf=a.elf, boot1_problems=[], boot2_problems=[])
    if a.only != "2":
        boot1(a, res)
    if a.only != "1":
        boot2(a, res)
    json.dump(res, open(os.path.join(a.out, "kitrow.json"), "w"), indent=1, default=str)
    checks = {"(3a) long BACK, song browser": res.get("3a", {}).get("ok") and not res["boot1_problems"],
              "(3b) long BACK, synth browser": res.get("3b", {}).get("ok") and not res["boot1_problems"],
              "(1) view on the new drum": res.get("1", {}).get("ok"),
              "(2) LEARN + gold knob": res.get("2", {}).get("ok") and not res["boot2_problems"]}
    for k, v in checks.items():
        print(f"  {k}: {'ok' if v else 'FAILED'}")
    for p in res["boot1_problems"] + res["boot2_problems"]:
        print(f"  problem: {p}")
    failed = [k for k, v in checks.items() if not v]
    print("PASS: all four checks hold" if not failed else "FAIL: " + "; ".join(failed))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
