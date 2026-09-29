#!/usr/bin/env python3
"""Song change on the real firmware in the emulator (tests/song's Emulator): the countdown while a loaded song is
armed to start, and the gold knobs and mod buttons on the playing song's master FX until the song changes.

Usage: songchange_emu.py <deluge.elf> [--scenario NAME ...] [--out DIR] [--tools PREFIX] [--build DIR] [--baseline]
  Scenarios (default all): oled, 7seg (the 7-segment display), swing (song A with quarter swing 25; song B with affect
  entire off), extclock (following an external MIDI clock, 123 BPM), stop (PLAY pressed while armed), launch (no song
  change: in Song view song A's clips armed to stop after 2 loops, the launch countdown). --baseline: the
  ELF is a build without the change (1.2.1, mastertune-v13): nothing is checked, it's only reported, for comparison.
  EMU_DEBUG=1: where the firmware is every 5 s of host time, and every yield.

What runs: the firmware boots (song_emu.boot()), loads song A (SONGS/DEFAULT.XML, opened in the clip view of its
synth) and plays it. Then, as the user would: the song browser is opened (openUI(&loadSongUI), with song B,
SONGS/SONGB.XML, selected), LOAD is pressed and held (Buttons::buttonAction(): LoadSongUI::buttonAction() ->
performLoad()), turned gold knobs are UI::modEncoderAction() on the current UI (what interpretEncoders() calls), mod
buttons, LOAD and BACK are Buttons::buttonAction(), the select encoder is LoadSongUI::selectEncoderAction(). The task
manager isn't running (its list is emptied, as in song_emu.Player); what it would run while performLoad() yields runs
here, one window of 128 samples at a time: AudioEngine::routine(), the playback handler's routine (the ticks, the
launch event, the song swap), the cluster loading, UITimerManager::routine() (the graphics routine: the countdown;
the knob LEDs, popups' timeouts) and doAnyPendingUIRendering() (OLED sending). performLoad()'s yields
(TaskManager::yield()) are where it pauses: the CPU context is saved, everything else runs on the stack below the
paused frames (as the tasks run inside the yield on the Deluge), and performLoad() resumes once the yield's own
condition (the lambda it was given) holds, checked between the tasks as the task manager does. Other yields return at
once. The external clock: PlaybackHandler::setupPlaybackUsingExternalClock() (what a MIDI start does), then
inputTick() every 7 windows.

Songs (make_sd.py's synth, 120 BPM, 4/4, 96 ticks per quarter note): A: one synth ("SYNA", a 4-bar clip of chords),
song mod section 1 (LPF), song LPF knob 40, affect entire off, the synth's own mod section 0; B: one synth ("SYNB",
2-bar clip), song mod section 3 (delay), song LPF knob 25, delay feedback knob 15, affect entire on (off in swing).

Recorded: every string the OLED gets (Canvas::drawString() on the main and popup canvas), popups, the 7-segment
texts (SevenSegment::setText(), displayPopup()); per window while armed the swung tick (getActualSwungTickCount()),
the launch event and the repeats; the OLED image at checkpoints (out/<scenario>/oled_*.png and .txt).

Checks (not with --baseline):
- countdown: in every window while armed, what the display shows (the title row on the OLED, the number on the
  7-segment display, blinking only while it counts loops) is what remains until the launch event: loops while repeats
  remain, then bars, then beats (the swung ticks remaining, rounded up), at most one graphics routine period (15 ms)
  late at a change; the changes and their ticks relative to the swap are printed; the select encoder's repeats show at
  once, also back and forth in the last loop; BACK changes nothing
- the swap happens at the launch event (or at once when stopped), the countdown is gone after it
- a countdown has priority (mastertune-v19.0.2, gui/ui/countdown.h): on the OLED it is drawn on the title row over
  everything else (the canvas overlayImage), a popup that would reach into it moved below it: while armed, the upper
  gold knob pressed (the filter type's popup, "HPF") and the DJ filter turned ("DJ: LPF ..."), the OLED's title row
  stays exactly as it was and the popup shows under it. On the 7-segment display the knobs' popups don't take the
  countdown's place (the filter type still changes). Scenario launch: the launch countdown in Song view on the title
  row, counted as the community firmware's box in the middle of the image counted it (the bars until the launch, in
  the last bar the beats), no box any more; a gold knob's value popup under it; the CPU monitor's line at the bottom
  while the countdown takes the top; after the launch the countdown is gone
- knobs: from LOAD on, the gold knob changes song A's LPF (the playing song's master FX, though A's affect entire is
  off), not song B's and not A's synth (whose clip view A was in); the mod button changes A's section; mod LEDs and
  knob indicators show A's section and values. At the swap the knobs are on nothing (the view's model stack holds no
  pointer into A, which is deleted then), a knob turned right then changes nothing, knob movement held back in the
  encoder doesn't reach B, a mod button held through the swap leaves no popup; after it B has its own saved LPF,
  section, and its song view's knobs as saved (on its master FX with affect entire on, on nothing with it off)
Needs python3 with unicorn 2 and numpy, a C compiler (blockcount.c, built into --build if missing).
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
import time
import types
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SONG_DIR = os.path.join(HERE, "..", "song")
sys.path.insert(0, SONG_DIR)
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402
from song_emu import PROGRAM_STACK_TOP, STOP, Emulator  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_D0, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,  # noqa: E402
                               UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP)

# --- firmware constants (definitions_cxx.hpp, hid/button.h, hid/led/indicator_leds.h, modulation/params/param.h)
UI_MODE_NONE = 0
UI_MODE_LOADING_SONG_ESSENTIAL_SAMPLES = 34
UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_UNARMED = 35
UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_ARMED = 36
UI_MODE_LOADING_SONG_NEW_SONG_PLAYING = 37


def button_xy(x, y):  # hid/button.h fromXY(): 9 * (y + kDisplayHeight * 2) + x
    return 9 * (y + 16) + x


BUTTON_LOAD = button_xy(6, 1)
BUTTON_BACK = button_xy(7, 1)
BUTTON_AFFECT_ENTIRE = button_xy(3, 0)
MOD_BUTTON = [button_xy(x, y) for x, y in zip((1, 1, 1, 1, 2, 2, 2, 2), (0, 1, 2, 3, 0, 1, 2, 3))]
MOD_LED = [x + 9 * y for x, y in zip((1, 1, 1, 1, 2, 2, 2, 2), (0, 1, 2, 3, 0, 1, 2, 3))]  # indicator_leds fromXY()
UNPATCHED_DELAY_AMOUNT, UNPATCHED_LPF_FREQ = 29, 32
ENCODER_MOD_1, ENCODER_MOD_0 = 4, 5  # EncoderName; std::array<Encoder, 6>, sizeof(Encoder) = 14, encPos first
BAR_TICKS, BEAT_TICKS = 384, 96  # Song::getBarLength(), getQuarterNoteLength() at inputTickMagnitude 2
SAMPLES_PER_GRAPHICS = 15 * 44  # GRAPHICS_ROUTINE every 15 ms
KNOB_A_LPF, KNOB_B_LPF, KNOB_B_DELAY = 40, 25, 15


def log(s):
    print(s, flush=True)


# --- the songs

def song_xml(name, clip_bars, song_mod, synth_mod, lpf_knob, delay_fb_knob, clip_attr, swing, affect_entire=0):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda *args, **kwargs: ""
    synth = make_sd.synth(name, make_sd.SAW, make_sd.SQUARE, 1, {}, make_sd.PAD_ENV1, make_sd.PAD_ENV2,
                          [("velocity", "volume", 25)])
    make_sd.synths = lambda: [synth]
    xml = make_sd.song_xml({}, 1, 1)
    xml = xml.replace('\n\tactiveModFunction="1"', f'\n\tactiveModFunction="{song_mod}"', 1)
    xml = xml.replace('\n\t\t\tactiveModFunction="1"', f'\n\t\t\tactiveModFunction="{synth_mod}"', 1)
    xml = xml.replace('\n\taffectEntire="0"', f'\n\taffectEntire="{affect_entire}"', 1)
    if swing:
        amount, interval = swing
        xml = xml.replace('\n\tswingAmount="0"', f'\n\tswingAmount="{amount}"', 1)
        xml = xml.replace('\n\tswingInterval="6"', f'\n\tswingInterval="{interval}"', 1)
    song_params = re.search(r"<songParams.*?</songParams>", xml, re.S)
    block = song_params.group(0)
    block = re.sub(r'<lpf frequency="0x[0-9A-F]+"', f'<lpf frequency="{make_sd.knob(lpf_knob)}"', block)
    block = re.sub(r'<delay rate="(0x[0-9A-F]+)" feedback="0x[0-9A-F]+"',
                   lambda m: f'<delay rate="{m.group(1)}" feedback="{make_sd.knob(delay_fb_knob)}"', block)
    xml = xml[:song_params.start()] + block + xml[song_params.end():]
    xml = re.sub(r'(<instrumentClip\b[^>]*?)\n\t\t\tlength="\d+"',
                 lambda m: f'{m.group(1)}\n\t\t\tlength="{clip_bars * BAR_TICKS}"\n\t\t\t{clip_attr}="1"', xml,
                 count=1)
    assert f'{clip_attr}="1"' in xml, "clip attribute not placed"
    if clip_bars < 4:  # keep only the notes inside the clip
        def cut(m):
            data = m.group(1)[2:]
            notes = [data[i:i + 20] for i in range(0, len(data), 20)]
            keep = [n for n in notes if int(n[0:8], 16) < clip_bars * BAR_TICKS]
            return f'noteData="0x{"".join(keep)}"'
        xml = re.sub(r'noteData="(0x[0-9A-F]*)"', cut, xml)
    return xml


def build_sd(path, swing=None, b_affect_entire=1):
    files = {
        "SONGS/DEFAULT.XML": song_xml("SYNA", 4, 1, 0, KNOB_A_LPF, 0, "beingEdited", swing).encode(),
        "SONGS/SONGB.XML": song_xml("SYNB", 2, 3, 0, KNOB_B_LPF, KNOB_B_DELAY, "selected", None,
                                    b_affect_entire).encode(),
    }
    fat32.build(path, files)


def knob_value(k):
    v = int(make_sd.knob(k), 16)
    return v - (1 << 32) if v >= 1 << 31 else v


# --- the emulated Deluge with performLoad() pausing at its yields

# Offsets of the members this test reads, from the ELF's debug info (they move when a struct before them grows)
OFFSET_QUERIES = [
    ("list Song::Song", [("song_name", "(int)&((Song*)0)->name"),
                         ("song_global_effectable", "(int)&((Song*)0)->globalEffectable"),
                         ("song_param_manager", "(int)&((Song*)0)->paramManager"),
                         ("mod_knob_mode", "(int)&((GlobalEffectableForSong*)0)->modKnobMode"),
                         ("filter_type", "(int)&((GlobalEffectable*)0)->currentFilterType"),
                         # Quoted: gdb doesn't find the namespace deluge::hid::display (a variable is named display)
                         ("seven_popup_active", "(int)&(('deluge::hid::display::SevenSegment'*)0)->popupActive")]),
    ("list PlaybackHandler::PlaybackHandler", [("last_swung_tick", "(int)&((PlaybackHandler*)0)->lastSwungTickActioned")]),
    ("list View::View", [("view_model_stack", "(int)&((View*)0)->activeModControllableModelStack")]),
]


def member_offsets(tools, elf):
    args, names = [], []
    for context, queries in OFFSET_QUERIES:
        args += ["-ex", context]
        for name, expression in queries:
            args += ["-ex", f"print {expression}"]
            names.append(name)
    out = subprocess.run([tools + "gdb", "-batch", *args, elf], capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (\d+)$", out, re.M)]
    if len(values) != len(names):
        raise SystemExit(f"member offsets from gdb: {len(values)} of {len(names)}:\n{out[-800:]}")
    return dict(zip(names, values))


class Deluge:
    def __init__(self, elf, sd, tools, build, oled, out_dir):
        self.out_dir = out_dir
        self.emu = emu = Emulator(elf, sd, tools, build, log if os.environ.get("EMU_DEBUG") else lambda s: None)
        emu.stack_top = PROGRAM_STACK_TOP
        emu.call = types.MethodType(Deluge._call, emu)
        sym = emu.sym
        song_emu.setup_sd(emu)
        song_emu.boot(emu)
        if oled:
            emu.call(sym["_ZN6deluge3hid7display15swapDisplayTypeEv"])
        self.oled = oled
        emu.w32(sym["jcong"], 1)
        song_emu.load_startup_song(emu)
        self.sym = sym
        self.off = member_offsets(tools, elf)
        self.a = {n: sym.find(n) for n in (
            "_ZN11AudioEngine7routineEv", "_ZZ13registerTasksvENUlvE0_4_FUNEv", "_ZZ13registerTasksvENUlvE1_4_FUNEv",
            "_ZN14UITimerManager7routineEv", "_Z23doAnyPendingUIRenderingv", "_Z6openUIP2UI",
            "_ZN7Buttons12buttonActionEhbb", "_ZN2UI16modEncoderActionEll", "_ZN10LoadSongUI19selectEncoderActionEa",
            "_ZN6String3setEPKcl", "_ZN15PlaybackHandler23getActualSwungTickCountEPm",
            "_ZN15PlaybackHandler17playButtonPressedEl", "_ZN15PlaybackHandler9inputTickEbm",
            "_ZN15PlaybackHandler31setupPlaybackUsingExternalClockEbb")}
        self.var = {n: sym[n] for n in ("currentUIMode", "currentSong", "preLoadedSong", "loadSongUI", "view", "session",
                                        "playbackHandler", "uiTimerManager", "actionLogger", "uartItems",
                                        "_ZN11AudioEngine16audioSampleTimerE", "_ZN6deluge3hid8encoders8encodersE",
                                        "_ZN14indicator_leds19knobIndicatorLevelsE", "_ZN14indicator_leds9ledStatesE",
                                        "_ZN6deluge3hid7display4OLED4mainE", "_ZN6deluge3hid7display4OLED5popupE",
                                        "_ZN6deluge3hid7display4OLED16oledCurrentImageE", "numUIsOpen",
                                        "_ZN6deluge3hid7display14oledPopupWidthE", "sessionView", "display",
                                        "_ZN6deluge3hid7display4OLED12needsSendingE")}
        # mastertune-v19.0.2: the canvas the countdown is drawn on, over everything (0 in builds without it)
        self.overlay = sym.by_name.get("_ZN6deluge3hid7displayL12overlayImageE", (0, 0))[0]
        # The task list emptied, the task manager's tasks are called from here (song_emu.Player)
        start, size = sym.by_name["taskManager"]
        emu.uc.mem_write(start, bytes(size))
        self.paused = None
        self.pauses = []
        self.nested_yields = 0
        yield_at = sym.find("_ZN11TaskManager5yieldEPFbvEd") & ~1
        emu.uc.hook_add(UC_HOOK_CODE, self._on_yield, begin=yield_at, end=yield_at)
        self.load_fn = [f for f in sym.functions if f[2].startswith("LoadSongUI::performLoad(")]
        self.events = []  # (window, kind, data)
        self.window_index = 0
        self._hook_display()
        self.swaps = []

        def on_swap(e):
            self.swaps.append(dict(tick=self.last_swung_tick(), timer=self.timer(), window=self.window_index,
                                   launch=self.launch_tick(), repeats=self.repeats()))
        emu.intercept(sym.find("_ZN15PlaybackHandler10doSongSwapEb"), on_swap)
        # Code already run (boot, the startup song) is translated without these hooks: retranslate
        emu.uc.ctl_flush_tb()
        self.song_a = self.u32(self.var["currentSong"])

    # calls on the stack below whatever is paused (performLoad()'s frames), as the tasks run inside the yield
    @staticmethod
    def _call(emu, address, *args, timeout_s=0):
        uc = emu.uc
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            uc.reg_write(reg, value & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_SP, emu.stack_top)
        uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        emu.run(address | 1, STOP, timeout_s)
        return uc.reg_read(UC_ARM_REG_R0)

    def call(self, name_or_address, *args):
        if isinstance(name_or_address, str) and name_or_address not in self.a:
            self.a[name_or_address] = self.sym.find(name_or_address)
        address = self.a.get(name_or_address, name_or_address)
        return self.emu.call(address, *args, timeout_s=5 if os.environ.get("EMU_DEBUG") else 0)

    def _on_yield(self, uc, address, size, _):
        try:
            self._yield(uc)
        except Exception as ex:  # unicorn would swallow it
            log(f"yield hook: {ex!r}")
            raise

    def _yield(self, uc):
        lr = uc.reg_read(UC_ARM_REG_LR)
        if os.environ.get("EMU_DEBUG"):
            log(f"  yield from {self.sym.name_at(lr)}, paused {bool(self.paused)}")
        until = uc.reg_read(UC_ARM_REG_R0)
        timeout = struct.unpack("<d", struct.pack("<Q", uc.reg_read(UC_ARM_REG_D0)))[0]
        from_load = any(s <= (lr & ~1) < e for s, e, _ in self.load_fn)
        uc.reg_write(UC_ARM_REG_R0, 1)
        uc.reg_write(UC_ARM_REG_PC, lr)
        if from_load and self.paused is None:
            sp = uc.reg_read(UC_ARM_REG_SP)
            self.paused = dict(until=until, timeout=timeout, since=self.timer(), sp=sp, resume=lr,
                               ctx=uc.context_save())
            self.pauses.append((self.window_index, until, timeout))
            self.emu.stack_top = (sp - 1024) & ~7
            self.emu.stop()
        else:
            self.nested_yields += 1

    def resume(self):
        p, self.paused = self.paused, None
        self.emu.uc.context_restore(p["ctx"])
        self.emu.stack_top = PROGRAM_STACK_TOP
        self.emu.run(p["resume"] | 1, STOP, 0)
        # Back here when performLoad() paused again (self.paused) or the whole call returned to STOP

    def maybe_resume(self):
        """What the yield loop checks after each task: its condition (or its timeout)."""
        while self.paused:
            p = self.paused
            done = self.call(p["until"]) & 0xFF
            if not done and p["timeout"] > 0 and (self.timer() - p["since"]) / 44100 >= p["timeout"]:
                done = True
            if not done:
                return False
            if self.on_resume:
                self.on_resume(self)
            self.resume()
        return True

    on_resume = None

    # --- memory
    def u32(self, a):
        return self.emu.u32(a)

    def i32(self, a):
        return struct.unpack("<i", self.emu.uc.mem_read(a, 4))[0]

    def i64(self, a):
        return struct.unpack("<q", self.emu.uc.mem_read(a, 8))[0]

    def timer(self):
        return self.u32(self.var["_ZN11AudioEngine16audioSampleTimerE"])

    def mode(self):
        return self.u32(self.var["currentUIMode"])

    def song(self):
        return self.u32(self.var["currentSong"])

    def preloaded(self):
        return self.u32(self.var["preLoadedSong"])

    def repeats(self):
        return struct.unpack("<h", self.emu.uc.mem_read(self.var["session"] + 24, 2))[0]

    def launch_tick(self):
        return self.i64(self.var["session"] + 16)

    def loop_length(self):
        return self.i32(self.var["session"] + 28)

    def swung_tick(self):
        self.call("_ZN15PlaybackHandler23getActualSwungTickCountEPm", 0)
        lo, hi = self.emu.uc.reg_read(UC_ARM_REG_R0), self.emu.uc.reg_read(UC_ARM_REG_R1)
        return struct.unpack("<q", struct.pack("<II", lo, hi))[0]

    def last_swung_tick(self):
        return self.i64(self.var["playbackHandler"] + self.off["last_swung_tick"])

    def song_param(self, song, param_id):
        """AutoParam::currentValue of the song's unpatched param (Song::paramManager at +4, summaries[0]
        .paramCollection, ParamSet::params (a pointer) at +16, sizeof(AutoParam) 64, currentValue at +52)."""
        collection = self.u32(song + self.off["song_param_manager"] + 4)
        return self.i32(self.u32(collection + 16) + 64 * param_id + 52)

    def song_mod_section(self, song):
        return self.emu.u8(song + self.off["song_global_effectable"] + self.off["mod_knob_mode"])

    def knobs_on(self):
        """view.activeModControllableModelStack (view + 20): song, timelineCounter, modControllable, paramManager."""
        v = self.var["view"] + self.off["view_model_stack"]
        return dict(song=self.u32(v), timeline=self.u32(v + 4), mod=self.u32(v + 16), params=self.u32(v + 20))

    def knob_levels(self):
        a = self.var["_ZN14indicator_leds19knobIndicatorLevelsE"]
        return self.emu.u8(a), self.emu.u8(a + 1)

    def mod_leds(self):
        a = self.var["_ZN14indicator_leds9ledStatesE"]
        return [i for i, led in enumerate(MOD_LED) if self.emu.u8(a + led)]

    def undo_empty(self):
        return self.u32(self.var["actionLogger"]) == 0 and self.u32(self.var["actionLogger"] + 4) == 0

    def enc_pos(self, which):
        return struct.unpack("<b", self.emu.uc.mem_read(self.var["_ZN6deluge3hid8encoders8encodersE"] + 14 * which,
                                                         1))[0]

    def set_enc_pos(self, which, value):
        self.emu.uc.mem_write(self.var["_ZN6deluge3hid8encoders8encodersE"] + 14 * which, struct.pack("<b", value))

    # --- input, as the Deluge's main loop hands it on
    def button(self, b, on):
        self.drain_pic()
        return self.call("_ZN7Buttons12buttonActionEhbb", b, 1 if on else 0, 0)

    def mod_encoder(self, which, offset):
        """What interpretEncoders() does for a turned gold knob: getCurrentUI()->modEncoderAction()."""
        self.drain_pic()
        return self.call("_ZN2UI16modEncoderActionEll", self.var["loadSongUI"], which, offset)

    def select_encoder(self, offset):
        self.drain_pic()
        self.call("_ZN10LoadSongUI19selectEncoderActionEa", self.var["loadSongUI"], offset)

    def sent(self):
        """Since v19.0.2 the countdown is drawn as the image is sent to the OLED (OLED::sendMainImage(), at the end of
        each pass of the UI rendering), over it, not into a screen's image: one window for that. Before, at once."""
        if self.overlay:
            self.step()

    def knob_press(self, which, ui=None):
        """A gold knob pressed and released: getCurrentUI()->modEncoderButtonAction()."""
        for on in (1, 0):
            self.drain_pic()
            self.call("_ZN2UI22modEncoderButtonActionEhb", ui or self.var["loadSongUI"], which, on)

    def filter_type(self, song):
        return self.emu.u8(song + self.off["song_global_effectable"] + self.off["filter_type"])

    def seven_popup(self):
        """Whether the 7-segment display shows a popup (SevenSegment::popupActive)."""
        return self.emu.u8(self.u32(self.var["display"]) + self.off["seven_popup_active"])

    def popup_width(self):
        return self.i32(self.var["_ZN6deluge3hid7display14oledPopupWidthE"])

    def mark_oled_changed(self):
        self.emu.uc.mem_write(self.var["_ZN6deluge3hid7display4OLED12needsSendingE"], b"\x01")

    def drain_pic(self):
        """No transfer-end interrupt here: whatever the firmware put in the PIC's UART ring counts as sent."""
        item = self.var["uartItems"]
        w = struct.unpack("<H", self.emu.uc.mem_read(item, 2))[0]
        self.emu.uc.mem_write(item + 2, struct.pack("<HHBB", w, w, 1, 0))

    # --- one window of 128 samples and the tasks after it, checking the paused yield's condition between tasks
    before_window = None

    def step(self):
        if self.before_window:
            self.before_window(self)
        self.drain_pic()
        emu = self.emu
        emu.dma_free = 127
        self.call("_ZN11AudioEngine7routineEv")
        emu.dma_free = 0
        self.maybe_resume()
        self.call("_ZZ13registerTasksvENUlvE0_4_FUNEv")  # playbackHandler.routine()
        self.maybe_resume()
        self.call("_ZZ13registerTasksvENUlvE1_4_FUNEv")  # audioFileManager.loadAnyEnqueuedClusters(128, false)
        self.maybe_resume()
        self.drain_pic()
        self.call("_ZN14UITimerManager7routineEv", self.var["uiTimerManager"])
        self.drain_pic()
        self.call("_Z23doAnyPendingUIRenderingv")
        self.maybe_resume()
        self.window_index += 1

    # --- what's displayed
    def _hook_display(self):
        emu, sym = self.emu, self.sym
        main, popup = self.var["_ZN6deluge3hid7display4OLED4mainE"], self.var["_ZN6deluge3hid7display4OLED5popupE"]

        def string_view(e):
            n, p = e.uc.reg_read(UC_ARM_REG_R1), e.uc.reg_read(UC_ARM_REG_R2)
            return bytes(e.uc.mem_read(p, n)).decode(errors="replace") if 0 < n < 200 else ""

        def on_draw(e):
            canvas = e.uc.reg_read(UC_ARM_REG_R0)
            if canvas in (main, popup) or (self.overlay and canvas == self.overlay):
                sp = e.uc.reg_read(UC_ARM_REG_SP)
                x = struct.unpack("<i", struct.pack("<I", e.uc.reg_read(UC_ARM_REG_R3)))[0]
                y = struct.unpack("<i", e.uc.mem_read(sp, 4))[0]
                height = struct.unpack("<i", e.uc.mem_read(sp + 8, 4))[0]
                kind = "main" if canvas == main else "popup" if canvas == popup else "overlay"
                self.events.append((self.window_index, kind, dict(text=string_view(e), x=x, y=y, h=height)))

        def on_popup_text(e):
            p = e.uc.reg_read(UC_ARM_REG_R0)
            self.events.append((self.window_index, "popupText", dict(text=e.ram_str(p))))

        def on_remove_popup(e):
            self.events.append((self.window_index, "removePopup", {}))

        def on_7seg_text(e):
            sp = e.uc.reg_read(UC_ARM_REG_SP)
            dot, blink = e.u8(sp), e.u8(sp + 4)  # setText(text, alignRight, drawDot, doBlink, ...)
            self.events.append((self.window_index, "7seg", dict(text=string_view(e), blink=blink, dot=dot)))

        def on_7seg_popup(e):
            p = e.uc.reg_read(UC_ARM_REG_R1)
            self.events.append((self.window_index, "7segPopup", dict(text=e.ram_str(p))))

        def ram_str(p, n=64):
            b = bytes(emu.uc.mem_read(p, n))
            return b.split(b"\0")[0].decode(errors="replace")
        emu.ram_str = ram_str
        emu.intercept(sym.find("_ZN6deluge3hid7display11oled_canvas6Canvas10drawString"), on_draw)
        emu.intercept(sym["_ZN6deluge3hid7display4OLED9popupTextEPKcb9PopupType"], on_popup_text)
        emu.intercept(sym.find("_ZN6deluge3hid7display4OLED11removePopupEv"), on_remove_popup)
        emu.intercept(sym.find("_ZN6deluge3hid7display12SevenSegment7setText"), on_7seg_text)
        # Its entry (since v19.0.2 GCC splits it: the check for held popups, then displayPopup(...).part.0)
        seven_popup = "_ZN6deluge3hid7display12SevenSegment12displayPopupEPKcabhl9PopupType"
        emu.intercept(sym[seven_popup] if seven_popup in sym.by_name else
                      sym.find("_ZN6deluge3hid7display12SevenSegment12displayPopup"), on_7seg_popup)

    def oled_image(self):
        """What the OLED got last (OLED::oledCurrentImage: main, or main with the popup)."""
        p = self.u32(self.var["_ZN6deluge3hid7display4OLED16oledCurrentImageE"]) or \
            self.var["_ZN6deluge3hid7display4OLED4mainE"]
        data = bytes(self.emu.uc.mem_read(p, 6 * 128))
        return [[(data[(y >> 3) * 128 + x] >> (y & 7)) & 1 for x in range(128)] for y in range(48)]

    def save_oled(self, name):
        if not self.oled:
            return
        img = self.oled_image()
        rows = img[5:]  # OLED_MAIN_TOPMOST_PIXEL: the top 5 rows aren't visible
        with open(os.path.join(self.out_dir, f"oled_{name}.txt"), "w") as f:
            f.write("\n".join("".join("#" if v else "." for v in row) for row in rows) + "\n")
        scale = 3
        raw = b"".join(b"\0" + bytes(v * 255 for v in row for _ in range(scale)) for row in rows for _ in range(scale))

        def chunk(kind, data):
            c = kind + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 128 * scale, len(rows) * scale, 8, 0, 0, 0,
                                                                  0))
        png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
        open(os.path.join(self.out_dir, f"oled_{name}.png"), "wb").write(png)


# --- the scenarios

def expected_countdown(launch, repeats, tick):
    if not launch:
        return None
    if repeats > 1:
        return ("loops", repeats)
    remaining = launch - tick
    if remaining > BAR_TICKS:
        return ("bars", -(-remaining // BAR_TICKS))
    return ("beats", max(-(-remaining // BEAT_TICKS), 1))


def expected_song_view(launch, repeats, loop, tick):
    """Song view's countdown (SessionView::displayLoopsRemainingPopup(), community firmware; since v19.0.2 on the
    OLED's title row): the bars until the launch, in the last bar the beats, from the sixteenths remaining."""
    if not launch:
        return None
    sixteenths = max(round((launch - tick) / 24) + (repeats - 1) * loop // 24, 1)  # v19.0.2: at least 1 until then
    return ("bars", (sixteenths - 1) // 16 + 1) if sixteenths > 16 else ("beats", (sixteenths - 1) // 4 + 1)


LABELS = {"Loops remaining": "loops", "Bars remaining": "bars", "Beats remaining": "beats"}


class Scenario:
    def __init__(self, name, args, oled=True, swing=None, ext_clock=None, stop=False, b_affect_entire=1, launch=False):
        self.name, self.args = name, args
        self.launch = launch
        self.b_affect_entire = b_affect_entire
        self.oled, self.swing, self.ext_clock, self.stop = oled, swing, ext_clock, stop
        self.out = os.path.join(args.out, name)
        os.makedirs(self.out, exist_ok=True)
        self.checks = []
        self.trace = []  # per window while pending: (window, timer, tick, mode, repeats, launch, shown)
        self.baseline = args.baseline

    def check(self, what, ok, detail=""):
        self.checks.append(dict(check=what, ok=bool(ok), detail=str(detail)))
        if not self.baseline:
            log(f"  [{'ok' if ok else 'FAIL'}] {what}" + (f": {detail}" if detail else ""))
        else:
            log(f"  [1.2.1] {what}: {detail}")

    def shown(self, d, since=0):
        """What the display shows now, from the events: OLED title row (label, number) or 7-segment text."""
        if self.oled:
            label = number = None
            for w, kind, e in d.events[since:]:
                # The title row: title/label at y 6 or 8, number at 6; on the overlay since v19.0.2
                if kind in ("main", "overlay") and 5 <= e["y"] <= 9:
                    t = e["text"]
                    if t in LABELS or t.startswith("Song will begin"):
                        label, number = t, None
                    elif re.fullmatch(r"\d+", t) and label in LABELS:
                        number = int(t)
                    else:
                        label, number = t, None
            if label in LABELS and number is not None:
                return (LABELS[label], number)
            return label
        text = None
        for w, kind, e in d.events[since:]:
            if kind == "7seg":
                text = (e["text"].strip(), e["blink"], e["dot"])
        return text

    def matches(self, shown, expected):
        if self.oled:
            return shown == expected
        # 7-segment: the number, blinking while it counts loops, a dot on the last digit while it counts beats
        return bool(shown and expected and shown[0] == str(expected[1]) and bool(shown[1]) == (expected[0] == "loops")
                    and (shown[2] == 3) == (expected[0] == "beats"))

    def run(self):
        if self.launch:
            return self.run_launch()
        a = self.args
        log(f"== {self.name}: " + ("OLED" if self.oled else "7-segment display")
            + (f", swing {self.swing}" if self.swing else "") + (", external clock" if self.ext_clock else "")
            + (", stopped while armed" if self.stop else ""))
        sd = os.path.join(self.out, "sd.img")
        build_sd(sd, self.swing, self.b_affect_entire)
        t0 = time.time()
        d = Deluge(a.elf, sd, a.tools, a.build, self.oled, self.out)
        self.d = d
        song_a = d.song()
        log(f"  booted, song A loaded ({time.time() - t0:.1f} s)")
        self.start_playback(d)
        self.play_until(d, lambda: d.swung_tick() >= 150)
        knobs0 = d.knobs_on()
        self.synth_pm = knobs0["params"]
        self.check("before loading, in A's clip view: the knobs are on the synth, not the song",
                   knobs0["params"] != song_a + d.off["song_param_manager"],
                   f"knobs on paramManager {knobs0['params']:#x}, song A's {song_a + d.off['song_param_manager']:#x}")
        # The browser, with song B selected (as if the user had scrolled to it): LoadSongUI::opened() looks for the
        # current song's name
        name = STOP + 0x200
        d.emu.uc.mem_write(name, b"SONGB\0")
        d.call("_ZN6String3setEPKcl", song_a + d.off["song_name"], name, -1)  # Song::name
        d.drain_pic()
        ok = d.call("_Z6openUIP2UI", d.var["loadSongUI"]) & 0xFF
        self.play_until(d, lambda: d.mode() == UI_MODE_NONE, limit=2000)
        self.check("song browser open", ok and d.mode() == UI_MODE_NONE, f"openUI {ok}, mode {d.mode()}")
        d.save_oled("browser")
        synth_before = self.synth_state(d)

        # LOAD pressed and held: the new song loads while A plays on
        ev0 = len(d.events)
        d.button(BUTTON_LOAD, True)
        self.play_until(d, lambda: d.paused and d.mode() == UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_UNARMED,
                        limit=20000)
        song_b = d.preloaded()
        self.check("loaded, LOAD still held: waiting (UNARMED), song B preloaded", song_b and d.mode() == 35,
                   f"mode {d.mode()}, preLoadedSong {song_b:#x}")
        k = d.knobs_on()
        self.check("pending: gold knobs on song A's master FX (view's model stack: song A, its GlobalEffectable and "
                   "paramManager)", k["timeline"] == song_a and k["params"] == song_a + d.off["song_param_manager"]
                   and k["mod"] == song_a + d.off["song_global_effectable"],
                   f"{ {n: hex(v) for n, v in k.items()} }, song A {song_a:#x}")
        self.check("pending: mod LEDs show A's song section (1, LPF), not the synth's (0)", d.mod_leds() == [1],
                   f"mod LEDs {d.mod_leds()}")
        lvl = d.knob_levels()
        self.check("pending: knob indicators show A's LPF (resonance, frequency knob 40 -> level ~102)",
                   abs(lvl[1] - (KNOB_A_LPF * 128 // 50)) <= 2, f"levels {lvl}")
        d.save_oled("loaded_unarmed")

        a_lpf0, b_lpf0 = d.song_param(song_a, UNPATCHED_LPF_FREQ), d.song_param(song_b, UNPATCHED_LPF_FREQ)
        for _ in range(8):
            d.mod_encoder(1, -1)
            d.step()
        for _ in range(10):
            d.step()
        a_lpf1, b_lpf1 = d.song_param(song_a, UNPATCHED_LPF_FREQ), d.song_param(song_b, UNPATCHED_LPF_FREQ)
        self.check("gold knob (upper) turned 8 down while pending: song A's LPF goes down, song B's stays",
                   a_lpf1 < a_lpf0 and b_lpf1 == b_lpf0 == knob_value(KNOB_B_LPF),
                   f"A {a_lpf0:#x} -> {a_lpf1:#x}, B {b_lpf0:#x} -> {b_lpf1:#x}")
        changed = [i for i, (x, y) in enumerate(zip(synth_before, self.synth_state(d))) if x != y]
        self.check("... and the synth of song A isn't touched", not changed, f"synth params changed: {changed}")
        lvl2 = d.knob_levels()
        self.check("... the knob indicator follows (8 steps down)", lvl2[1] < lvl[1], f"{lvl} -> {lvl2}")

        d.button(MOD_BUTTON[3], True)
        d.step()
        leds3, sec3 = d.mod_leds(), d.song_mod_section(song_a)
        d.button(MOD_BUTTON[3], False)
        self.check("mod button 3 (delay) while pending: song A's section and the mod LEDs follow",
                   leds3 == [3] and sec3 == 3 and d.song_mod_section(song_b) == 3,
                   f"LEDs {leds3}, A's section {sec3}, B's own section {d.song_mod_section(song_b)}")
        d.button(MOD_BUTTON[1], True)
        d.step()
        d.button(MOD_BUTTON[1], False)
        for _ in range(3):
            d.step()

        # LOAD released: armed, the countdown starts
        mark = len(d.events)
        d.button(BUTTON_LOAD, False)
        self.check("LOAD released: armed", d.mode() == UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_ARMED,
                   f"mode {d.mode()}, launch at tick {d.launch_tick()}, loop {d.loop_length()} ticks")
        d.sent()
        first = self.shown(d, mark)
        tick = d.swung_tick()
        self.check("countdown shown at once", self.matches(first, self.expect(d, tick)) or self.baseline,
                   f"{first}")
        d.save_oled("armed")
        # One more loop, with the select encoder
        d.select_encoder(1)
        d.sent()
        self.check("select encoder +1: 2 loops", self.matches(self.shown(d, mark), self.expect(d, d.swung_tick()))
                   or self.baseline, f"{self.shown(d, mark)}, repeats {d.repeats()}")

        # Play through the countdown, recording what's shown in every window
        swap = {}

        def at_swap(dd):
            # The swap just happened (the yield's condition holds), performLoad() hasn't resumed: a knob turned
            # right now, and knob movement still in the encoder
            if dd.mode() == UI_MODE_LOADING_SONG_NEW_SONG_PLAYING and "window" not in swap:
                at = dd.swaps[-1] if dd.swaps else {}
                swap.update(window=dd.window_index, tick=at.get("tick"), timer=at.get("timer"),
                            launch=at.get("launch"), song=dd.song(), knobs=dd.knobs_on(), undo_empty=dd.undo_empty())
                b = dd.song()
                swap["b_lpf_before"] = dd.song_param(b, UNPATCHED_LPF_FREQ)
                dd.mod_encoder(1, -5)
                swap["b_lpf_after"] = dd.song_param(b, UNPATCHED_LPF_FREQ)
                dd.set_enc_pos(ENCODER_MOD_1, 3)
                swap["events_at"] = len(dd.events)
        d.on_resume = at_swap
        changed_repeats = False
        back_pressed = False
        knob_armed = None
        mark_armed = mark
        while d.mode() == UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_ARMED:
            if self.stop and d.repeats() == 1 and d.launch_tick() - d.swung_tick() < 2 * BAR_TICKS + 40:
                self.trace_window(d, mark_armed)
                stop_tick = d.swung_tick()
                d.call("_ZN15PlaybackHandler17playButtonPressedEl", 0)  # PLAY: stops, and with it the swap happens
                swap.setdefault("stopped_at", stop_tick)
                break
            d.step()
            if d.mode() != UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_ARMED:
                break
            self.trace_window(d, mark_armed)
            tick = d.swung_tick()
            ex = self.expect(d, tick)
            if not changed_repeats and ex == ("bars", 3):
                # In the last loop: one more repeat and back, the countdown must follow at once
                changed_repeats = True
                d.select_encoder(1)
                d.sent()
                s1 = self.shown(d, mark_armed)
                d.select_encoder(-1)
                d.sent()
                s2 = self.shown(d, mark_armed)
                self.check("select encoder in the last loop: +1 shows loops 2, -1 back to bars 3",
                           self.matches(s1, ("loops", 2)) and self.matches(s2, ("bars", 3)) or self.baseline,
                           f"{s1}, then {s2}")
                a0 = d.song_param(song_a, UNPATCHED_LPF_FREQ)
                d.mod_encoder(1, -3)
                knob_armed = (a0, d.song_param(song_a, UNPATCHED_LPF_FREQ), d.song_param(song_b, UNPATCHED_LPF_FREQ))
            elif changed_repeats and ex == ("bars", 3) and "priority" not in swap and not self.stop \
                    and not self.baseline and (d.overlay or not self.oled):
                swap["priority"] = True
                self.countdown_priority(d, song_a)
            if not back_pressed and ex == ("bars", 2):
                back_pressed = True
                before, n = self.shown(d, mark_armed), len(d.events)
                d.button(BUTTON_BACK, True)
                d.button(BUTTON_BACK, False)
                for _ in range(8):
                    d.step()
                self.check("BACK while armed: nothing happens, still armed, the countdown goes on",
                           d.mode() == UI_MODE_LOADING_SONG_UNESSENTIAL_SAMPLES_ARMED and
                           not [k for w, k, e in d.events[n:] if k in ("popupText", "7segPopup")] and
                           self.matches(self.shown(d, mark_armed), self.expect(d, d.swung_tick())) or self.baseline,
                           f"mode {d.mode()}, shown {before} -> {self.shown(d, mark_armed)}")
                d.save_oled("armed_bars2")
            if ex == ("beats", 3) and "beats3" not in swap:
                swap["beats3"] = True
                d.save_oled("armed_beats3")
            if self.oled and not self.stop and ex == ("beats", 1) and "held" not in swap:
                # A mod button held through the swap (its popup up), released once the new song plays
                swap["held"] = True
                d.button(MOD_BUTTON[1], True)
                swap["popup_held"] = d.i32(d.var["_ZN6deluge3hid7display14oledPopupWidthE"])
        # performLoad() finishes (the song swap is done, the old song deleted, the new song's UI set up)
        self.play_until(d, lambda: not d.paused and d.mode() == UI_MODE_NONE, limit=20000)
        if knob_armed:
            self.check("gold knob turned while armed (countdown running): A's LPF changes, B's not",
                       knob_armed[1] < knob_armed[0] and knob_armed[2] == knob_value(KNOB_B_LPF),
                       f"A {knob_armed[0]:#x} -> {knob_armed[1]:#x}, B {knob_armed[2]:#x}")
        if swap.get("held"):
            d.button(MOD_BUTTON[1], False)
            for _ in range(5):
                d.step()
            popup = d.i32(d.var["_ZN6deluge3hid7display14oledPopupWidthE"])
            self.check("a mod button held through the swap: its popup goes when it's released",
                       swap["popup_held"] and not popup or self.baseline,
                       f"popup width while held {swap['popup_held']}, after release {popup}")
        self.after_swap(d, swap, song_a, song_b)
        for _ in range(200):
            d.step()
        d.save_oled("after")
        self.report_countdown(d, swap)
        json.dump(dict(checks=self.checks, trace=self.trace, swap={k: v for k, v in swap.items()
                                                                  if isinstance(v, (int, bool, str))},
                       events=[(w, k, e) for w, k, e in d.events[ev0:]]),
                  open(os.path.join(self.out, "result.json"), "w"), indent=1)
        return all(c["ok"] for c in self.checks)

    def start_playback(self, d):
        if not self.ext_clock:
            d.call("_ZN15PlaybackHandler17playButtonPressedEl", 0)
            return
        # MIDI start (what MidiEngine::midiMessageReceived() does for it, PlaybackHandler::startMessageReceived()
        # inlined: usingAnalogClockInput and posToNextContinuePlaybackFrom 0, setupPlaybackUsingExternalClock(false)),
        # then a MIDI clock every ext_clock windows (inputTick(false, 0): the tick's time is now)
        ph = d.var["playbackHandler"]
        d.emu.uc.mem_write(ph + 17, b"\0")
        d.w32 = d.emu.w32
        d.emu.w32(ph + 28, 0)
        d.call("_ZN15PlaybackHandler31setupPlaybackUsingExternalClockEbb", 0, 0)
        every = self.ext_clock

        def clock(dd):
            if dd.window_index % every == 0:
                dd.call("_ZN15PlaybackHandler9inputTickEbm", 0, 0)
                dd.midi_clocks = getattr(dd, "midi_clocks", 0) + 1
        d.before_window = clock
        log(f"  external clock: a MIDI clock every {every} windows ({every * 128} samples, "
            f"{44100 * 60 / (24 * every * 128):.2f} BPM)")

    def play_until(self, d, cond, limit=100000):
        for _ in range(limit):
            if cond():
                return True
            d.step()
        raise SystemExit(f"{self.name}: condition not reached (mode {d.mode()}, paused {bool(d.paused)})")

    def quiet(self, d, limit=1000):
        """The windows until no popup is up (OLED or 7-segment)."""
        for _ in range(limit):
            if not (d.popup_width() if self.oled else d.seven_popup()):
                return
            d.step()

    def popup_below(self, d, what, action, ui=None, want=None):
        """mastertune-v19.0.2: what action pops up goes below the countdown, whose title row stays as it was."""
        for _ in range(12):  # The countdown as it is now: a change shows at the next graphics routine (15 ms)
            d.step()
        before = d.oled_image()
        n = len(d.events)
        action()
        for _ in range(3):
            d.step()
        after = d.oled_image()
        popups = [e["text"] for w, k, e in d.events[n:] if k == "popupText"]
        top = all(before[y] == after[y] for y in range(5, 18))
        below = [y for y in range(18, 48) if before[y] != after[y]]
        shown = (not want or any(p.startswith(want) for p in popups))
        self.check(f"{what}: its popup ({', '.join(popups) or 'none'}) below the countdown, the title row as it was",
                   d.popup_width() and shown and top and below and min(below) >= 18,
                   f"popup width {d.popup_width()}, title row rows 5-17 {'unchanged' if top else 'CHANGED'}, "
                   f"rows changed below: {below[:1]}..{below[-1:]}")
        return before

    def countdown_priority(self, d, song_a):
        """While armed: popups don't cover the countdown (OLED: below it; 7-segment: not shown)."""
        f0 = d.filter_type(song_a)
        if self.oled:
            self.popup_below(d, "the upper gold knob pressed while armed (the filter type)", lambda: d.knob_press(1),
                             want="HPF")
            d.save_oled("armed_popup")
            # On to DJ (EQ, DJ) and the DJ filter a detent to the left, as a DJ takes the old song out
            d.knob_press(1)
            d.knob_press(1)
            self.popup_below(d, "the DJ filter turned while armed", lambda: d.mod_encoder(1, -1), want="DJ: LPF")
            d.save_oled("armed_dj_popup")
            d.mod_encoder(1, 1)
        else:
            self.quiet(d)
            d.knob_press(1)
            d.step()
            self.check("7-segment: the upper gold knob pressed while armed changes the filter type, but its popup "
                       "doesn't take the countdown's place", d.filter_type(song_a) != f0 and not d.seven_popup(),
                       f"filter type {f0} -> {d.filter_type(song_a)}, popup up {d.seven_popup()}")
            d.knob_press(1)
            d.knob_press(1)
        d.knob_press(1)  # Round to LPF again
        self.check("... the filter type round to LPF again", d.filter_type(song_a) == f0,
                   f"{f0} -> {d.filter_type(song_a)}")

    def run_launch(self):
        """Scenario launch (mastertune-v19.0.2): no song change. In Song view, song A's clips armed to stop after 2
        loops: the launch countdown on the OLED's title row over everything, a gold knob's popup below it, the CPU
        monitor at the bottom, and gone after the launch."""
        a = self.args
        log(f"== {self.name}: Song view, song A's clips armed to stop after 2 loops")
        sd = os.path.join(self.out, "sd.img")
        build_sd(sd)
        d = Deluge(a.elf, sd, a.tools, a.build, True, self.out)
        self.d = d
        self.start_playback(d)
        self.play_until(d, lambda: d.swung_tick() >= 150)
        sv = d.var["sessionView"]
        d.drain_pic()
        d.call("_Z12changeRootUIP2UI", sv)
        for _ in range(20):
            d.step()
        # Song A is saved with affect entire off: in Song view its knobs are then on nothing. AFFECT ENTIRE pressed
        d.button(BUTTON_AFFECT_ENTIRE, True)
        d.button(BUTTON_AFFECT_ENTIRE, False)
        for _ in range(5):
            d.step()
        mark = len(d.events)
        d.call("_ZN7Session17armAllClipsToStopEl", d.var["session"], 2)
        for _ in range(12):
            d.step()
        shown = self.shown(d, mark)
        due = expected_song_view(d.launch_tick(), d.repeats(), d.loop_length(), d.swung_tick())
        box = [e["text"] for w, k, e in d.events[mark:] if k == "main" and "emaining" in e["text"]]
        self.check("clips armed to stop after 2 loops: the countdown on the title row, the bars until the launch "
                   "(not a box in the middle)", shown == due and due[0] == "bars" and due[1] > 4 and not box,
                   f"shown {shown}, due {due}, main drew {box[:2]}")
        d.save_oled("launch_countdown")
        before = self.popup_below(d, "a gold knob turned in Song view during the countdown (the song's LPF)",
                                  lambda: d.call("_ZN11SessionView16modEncoderActionEll", sv, 1, -1))
        d.save_oled("launch_popup")
        # The CPU monitor's line (cpu_stats::oledInfo()) at the bottom while the countdown takes the top
        self.quiet(d)
        line = d.sym.find("_ZN9cpu_stats12_GLOBAL__N_14lineE")
        enabled = d.sym["_ZN9cpu_stats7enabledE"]
        plain = d.oled_image()
        d.emu.uc.mem_write(line, b"CPU 50% 12V\0")
        d.emu.uc.mem_write(enabled, b"\x01")
        d.mark_oled_changed()
        for _ in range(2):
            d.step()
        mon = d.oled_image()
        top = all(plain[y] == mon[y] for y in range(5, 18))
        bottom = [y for y in range(36, 48) if plain[y] != mon[y]]
        self.check("the CPU monitor on during the countdown: its line at the bottom, the title row as it was",
                   top and bottom and all(plain[y] == mon[y] for y in range(18, 36)),
                   f"title row {'unchanged' if top else 'CHANGED'}, rows changed at the bottom {bottom}")
        d.save_oled("launch_cpu_monitor")
        d.emu.uc.mem_write(enabled, b"\x00")
        d.mark_oled_changed()
        # Through the countdown: loops, bars, beats, each as it is due; then the launch, and the countdown gone
        seen, rows = [], []
        while d.launch_tick():
            d.step()
            if not d.launch_tick():
                break
            s = self.shown(d, mark)
            rows.append((d.swung_tick(), s, expected_song_view(d.launch_tick(), d.repeats(), d.loop_length(),
                                                                d.swung_tick())))
            if not seen or seen[-1] != s:
                seen.append(s)
            self.trace.append((d.window_index, d.timer(), d.swung_tick(), 0, d.repeats(), d.launch_tick(), s))
        # A change shows at the next graphics routine (every 15 ms, 5 windows) and the OLED's sending after it; the
        # sixteenths are rounded from the swung ticks with their fraction, so a change may also come a window early
        due = [r[2] for r in rows]
        bad = [(tick, ex, s) for i, (tick, s, ex) in enumerate(rows) if s not in due[max(0, i - 8):i + 2]]
        n = len(d.events)
        for _ in range(20):
            d.step()
        drawn = [e["text"] for w, k, e in d.events[n:] if k == "overlay"]
        self.check(f"the countdown in Song view as due in every window ({len(seen)} changes: {seen[:3]} .. "
                   f"{seen[-2:]})", not bad and all(s in seen for s in (("bars", 8), ("bars", 2), ("beats", 4),
                                                                        ("beats", 1))),
                   f"wrong: {bad[:4]}")
        self.check("after the launch the countdown is gone (nothing drawn over the image)", not drawn, f"{drawn[:4]}")
        d.save_oled("launch_after")
        json.dump(dict(checks=self.checks, trace=self.trace, events=[(w, k, e) for w, k, e in d.events[mark:]]),
                  open(os.path.join(self.out, "result.json"), "w"), indent=1)
        return all(c["ok"] for c in self.checks)

    def synth_state(self, d):
        """currentValue of the first 40 params of the synth's unpatched and patched ParamSets (its ParamManager's
        summaries[0] and [1], ParamCollectionSummary 20 bytes)."""
        vals = []
        for i in range(2):
            coll = d.u32(self.synth_pm + 4 + 20 * i)
            vals += [d.i32(d.u32(coll + 16) + 64 * p + 52) for p in range(40)] if coll else []
        return vals

    def expect(self, d, tick):
        return expected_countdown(d.launch_tick(), d.repeats(), tick)

    def trace_window(self, d, mark):
        tick = d.swung_tick()
        self.trace.append((d.window_index, d.timer(), tick, d.mode(), d.repeats(), d.launch_tick(),
                           self.shown(d, mark)))

    def after_swap(self, d, swap, song_a, song_b):
        if self.stop:
            self.check("PLAY (stop) while armed: the song changes right away", swap.get("song") == song_b,
                       f"stopped at tick {swap.get('stopped_at')}, launch was {self.trace[-1][5] if self.trace else '?'}")
        else:
            last = self.trace[-1] if self.trace else None
            self.check("the song changes at the launch event", last and swap.get("song") == song_b and
                       swap.get("tick") == last[5], f"swap at tick {swap.get('tick')} (old song), launch event "
                       f"{last and last[5]}, {len(d.swaps)} swap(s)")
        k = swap.get("knobs", {})
        self.check("at the swap: the knobs let go of the old song (view's model stack: nothing)",
                   k.get("mod") == 0 and k.get("params") == 0 and k.get("timeline") == 0 or self.baseline,
                   {n: hex(v) for n, v in k.items()})
        self.check("at the swap: undo history empty (nothing in it points at the old song)",
                   swap.get("undo_empty") or self.baseline, swap.get("undo_empty"))
        self.check("a knob turned right at the swap changes nothing, not the new song's LPF",
                   swap.get("b_lpf_before") == swap.get("b_lpf_after") == knob_value(KNOB_B_LPF) or self.baseline,
                   f"B {swap.get('b_lpf_before', 0):#x} -> {swap.get('b_lpf_after', 0):#x}")
        b = d.song()
        self.check("after: song B plays, with its own saved LPF (knob 25) and mod section (3, delay)",
                   b == song_b and d.song_param(b, UNPATCHED_LPF_FREQ) == knob_value(KNOB_B_LPF) and
                   d.song_mod_section(b) == 3, f"song {b:#x}, LPF {d.song_param(b, UNPATCHED_LPF_FREQ):#x}, "
                   f"section {d.song_mod_section(b)}")
        self.check("after: knob movement held back at the swap was dropped (encoder MOD_1 encPos 0)",
                   d.enc_pos(ENCODER_MOD_1) == 0 or self.baseline, f"encPos {d.enc_pos(ENCODER_MOD_1)}")
        k2 = d.knobs_on()
        lvl = d.knob_levels()
        if self.b_affect_entire:
            self.check("after: the knobs are on song B (its song view, affect entire on as saved), mod LEDs show B's "
                       "section 3", k2["timeline"] == b and k2["params"] == b + 4 and d.mod_leds() == [3],
                       f"{ {n: hex(v) for n, v in k2.items()} }, LEDs {d.mod_leds()}, levels {lvl}")
            self.check("after: knob indicators show B's delay (feedback knob 15 -> level ~38)",
                       abs(lvl[0] - KNOB_B_DELAY * 128 // 50) <= 2, f"levels {lvl}")
        else:
            self.check("after: song B's song view with affect entire off, as saved: the knobs on nothing, mod LEDs off",
                       k2["mod"] == 0 and k2["params"] == 0 and d.mod_leds() == [],
                       f"{ {n: hex(v) for n, v in k2.items()} }, LEDs {d.mod_leds()}, levels {lvl}")
        after_events = d.events[swap.get("events_at", len(d.events)):]
        drawn = [e["text"] for w, kind, e in after_events if kind in ("main", "overlay") and 5 <= e["y"] <= 9]
        countdown_after = [t for t in drawn if t in LABELS]
        # Since v19.0.2 the countdown is drawn over the title (OLED::sendMainImage()), not into it: no title to bring
        # back. Before, LoadSongUI::removeCountdown() drew "Load song" again
        title_back = "Load song" in drawn or bool(d.overlay) or not self.oled
        self.check("after the swap no countdown is drawn" + ("" if d.overlay else "; the title comes back"),
                   not countdown_after and title_back or self.baseline, f"title row drawn after the swap: {drawn[:6]}")

    def report_countdown(self, d, swap):
        """The changes of what's shown, with their tick relative to the swap, and the check against the expected
        countdown in every window (a change may show up one graphics routine period, 15 ms, late)."""
        swap_tick = swap.get("tick")
        changes, prev = [], object()
        bad, late = [], 0
        expected = [expected_countdown(launch, repeats, tick) for (w, timer, tick, mode, repeats, launch, shown)
                    in self.trace]
        for i, (w, timer, tick, mode, repeats, launch, shown) in enumerate(self.trace):
            got = shown
            if got != prev:
                changes.append((tick, timer, got))
                prev = got
            if not self.matches(got, expected[i]):
                # A change due in this window shows at the next graphics routine (every 15 ms, 5 windows)
                if any(self.matches(got, expected[j]) for j in range(max(0, i - 6), i)):
                    late += 1
                else:
                    bad.append((tick, expected[i], got))
        log(f"  countdown as shown ({'OLED title row' if self.oled else '7-segment'}), tick relative to the swap:")
        for tick, timer, got in changes:
            rel = (tick - swap_tick) if swap_tick is not None else tick
            rel_t = (timer - swap["timer"]) / 44100 if "timer" in swap else 0
            log(f"    {rel:+6d} ticks ({rel / BAR_TICKS:+6.2f} bars, {rel_t:+7.3f} s)  {got}")
        if self.baseline:
            texts = [e["text"] for w, k, e in d.events if k in ("popupText", "7segPopup", "7seg")]
            seen = [t for i, t in enumerate(texts) if i == 0 or t != texts[i - 1]]
            log(f"  1.2.1: popups / 7-segment texts from LOAD on: {seen[:40]}")
        if not self.oled:
            blinks = [(e["text"].strip(), e["blink"], e["dot"]) for w, k, e in d.events if k == "7seg"]
            log(f"  7-segment texts while armed (text, blink, dot): {blinks[-16:]}")
        self.check(f"countdown right in all {len(self.trace)} windows while armed (a change up to one graphics "
                   f"period late in {late})", not bad or self.baseline, f"wrong: {bad[:5]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--scenario", action="append")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "out"))
    ap.add_argument("--tools")
    ap.add_argument("--build", help="directory with blockcount.so (built there if missing)")
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args()
    args.elf = os.path.abspath(args.elf)
    args.tools = args.tools or os.path.join(os.path.dirname(args.elf),
                                            "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    args.build = args.build or args.out
    os.makedirs(args.out, exist_ok=True)
    if not os.path.exists(os.path.join(args.build, "blockcount.so")):
        import unicorn
        uc = os.path.dirname(unicorn.__file__)
        subprocess.run(["cc", "-O2", "-shared", "-fPIC", f"-I{uc}/include", os.path.join(SONG_DIR, "blockcount.c"),
                        "-o", os.path.join(args.build, "blockcount.so"), f"-L{uc}/lib", "-l:libunicorn.so.2",
                        f"-Wl,-rpath,{uc}/lib"], check=True)
    scenarios = {
        "oled": dict(oled=True),
        "7seg": dict(oled=False),
        "swing": dict(oled=True, swing=(25, 4), b_affect_entire=0),
        "stop": dict(oled=True, stop=True),
        "extclock": dict(oled=True, ext_clock=7),
        "launch": dict(oled=True, launch=True),
    }
    names = args.scenario or ["oled", "7seg", "swing", "extclock", "stop", "launch"]
    results = {}
    for n in names:
        results[n] = Scenario(n, args, **scenarios[n]).run()
    log("")
    for n, ok in results.items():
        log(f"{n}: {'all checks passed' if ok else 'FAILED'}" if not args.baseline else f"{n}: reported")
    sys.exit(0 if all(results.values()) or args.baseline else 1)


if __name__ == "__main__":
    main()
