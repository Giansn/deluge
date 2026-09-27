#!/usr/bin/env python3
"""The CPU monitor's shortcut (LEARN held, the tempo encoder pressed) and its mode across a restart (cpu-shortcut, for
mastertune-v17), on the real firmware in the emulator (the harness of ../song, as usb_audio_setting_emu.py).

The shortcut switches Settings > CPU monitor off and back to the last of On and Alerts (at first On), from Profile to
Off, with a popup; hid/buttons.cpp takes it before the view (Buttons::buttonAction() is called here as the PIC's
messages call it, inCardRoutine = sdRoutineLock). The mode is saved as the entry cpuMonitor in CommunityFeatures.XML:
at once by the shortcut (while the card is busy, by cpu_stats::routine() once it's free), by the menu when it's left
(SoundEditor::exitCompletely()), and read at boot (Profile as On).

One card without CommunityFeatures.XML, the song opening in its first synth's clip view, booted four times (the
emulated Deluge has the 7-segment display; from 5. on the OLED is swapped in, as Settings > Emulated display does):
1. boot: the monitor off; Song view (SESSION_VIEW)
2. Song view, stopped: LEARN, TEMPO pressed and let go, LEARN let go: On, its popup, the UI mode back to none, no tempo
   popup, the clock-out scale untouched; the card says cpuMonitor 1
3. the clip view (CLIP_VIEW), playing (the task manager with the DMA in real time): Off, then On; the audio goes on
   while it saves; the card says 0, then 1
4. the same press while the card is busy (sdRoutineLock): Off at once, the card unchanged while it stays busy; once
   free, cpu_stats::routine() saves it (0); the shortcut again: On
5. restart: On; the OLED swapped in, playing: the monitor's line and its SysEx on USB
6. Settings > CPU monitor > Alerts, the menu left: the card says 2; the shortcut: Off, then Alerts again; restart:
   Alerts. The menu on Profile, left: the card says 3; restart: On, the profiler not running
7. restart: On, the profiler not running; the keyboard (KEYBOARD from the clip view) and the drone view (SCALE in Song
   view): the shortcut toggles, no voice sounds, the UI mode back to none after LEARN. The task manager isn't run in
   the drone view: the graphics timer's UI::graphicsRoutine() loops there for ever (DroneView has no graphicsRoutine()
   of its own and RootUI can see the view underneath, so it tail-calls itself; v16 release too), a bug of its own
Usage: cpu_monitor_shortcut_emu.py <deluge.elf> [--tools PREFIX] [--out DIR]   (BLOCKCOUNT_DIR: where blockcount.so is)
Exit status 0 when every check passes."""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2  # noqa: E402


def button(x, y):
    return 9 * (y + 16) + x  # hid/button.h: fromCartesian()


LEARN = button(7, 0)
TEMPO_ENC = button(4, 1)
SCALE_MODE = button(6, 0)
SESSION_VIEW = button(3, 1)
CLIP_VIEW = button(3, 2)
KEYBOARD = button(3, 3)
OFF, ON, ALERTS, PROFILE = range(4)
NAMES = ["Off", "On", "Alerts", "Profile"]
MENU_VALUE_OFFSET = 12  # Selection's value_ (CpuMonitorMode::writeCurrentValue(): ldrb [r0, #12])
SYSEX_COMMAND = 0x10  # cpu_stats_core.h: kSysexCommand

failures = 0
checks = 0


def check(what, ok, detail=""):
    global failures, checks
    checks += 1
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {what}" + (f": {detail}" if detail else ""), flush=True)


class Deluge:
    """One boot of the emulated Deluge on the card, and what the checks reach in it."""

    def __init__(self, elf, sd, tools):
        self.sd = sd
        self.emu = emu = se.Emulator(elf, sd, tools, os.environ.get("BLOCKCOUNT_DIR", HERE), lambda s: None)
        se.setup_sd(emu)
        se.boot(emu)
        se.load_startup_song(emu)
        sym = self.sym = emu.sym
        self.popups = []
        for name in ("_ZN6deluge3hid7display4OLED12displayPopupEPKc",
                     "_ZN6deluge3hid7display12SevenSegment12displayPopupEPKc"):
            emu.intercept(sym.find(name), self.popup)
        self.tempo_popups = 0  # PlaybackHandler::commandDisplayTempo() calls
        emu.intercept(sym.find("_ZN15PlaybackHandler19commandDisplayTempoEv"), self.tempo_popup)
        self.sysex = []
        emu.intercept(sym.find("_ZN13MIDIDeviceUSB15sendBufferSpaceEv"), lambda e: 4096 * 3)
        emu.intercept(sym.find("_ZN13MIDIDeviceUSB9sendSysexEPKhl"), self.sent)
        emu.uc.ctl_flush_tb()
        # The audio in real time while the task manager runs: the views' transitions go by the audio's sample count
        self.dma = se.RealTimeDma(emu)
        # The clock-out scale's member, from the debug info (gdb needs a place where Song is known)
        out = subprocess.run([emu.tool_prefix + "gdb", "-batch", "-ex", "list Song::Song", "-ex",
                              "print (int)&((Song*)0)->insideWorldTickMagnitude", elf], capture_output=True,
                             text=True).stdout
        self.o_magnitude = int(re.findall(r"^\$\d+ = (\d+)$", out, re.M)[-1])
        self.oled = emu.u8(sym["_ZN6deluge3hid7display16have_oled_screenE"])

    def string(self, address):
        return bytes(self.emu.uc.mem_read(address, 64)).split(b"\0")[0].decode("ascii", "replace")

    def popup(self, emu):
        self.popups.append(self.string(emu.uc.reg_read(UC_ARM_REG_R1)))

    def tempo_popup(self, emu):
        self.tempo_popups += 1

    def sent(self, emu):
        uc = emu.uc
        data = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)))
        self.sysex.append(data)
        return 0

    # --- the firmware's state

    def mode(self):
        return self.emu.u8(self.sym["_ZN9cpu_stats4modeE"])

    def ui_mode(self):
        return self.emu.u32(self.sym["currentUIMode"])

    def root(self):
        return self.emu.call(self.sym["_Z9getRootUIv"])

    def voices(self):
        return self.emu.u32(self.sym["_ZN11AudioEngine12activeVoicesE"] + 16)  # numElements

    def clock_scale(self):
        return self.emu.u32(self.emu.u32(self.sym["currentSong"]) + self.o_magnitude)

    def samples(self):
        return self.emu.u32(self.sym["_ZN11AudioEngine16audioSampleTimerE"])

    def profiler_running(self):
        address = next(a for n, (a, _) in self.sym.by_name.items() if "profiler" in n and "running" in n)
        return self.emu.u8(address)

    def saved(self):
        """The cpuMonitor entry of CommunityFeatures.XML on the card, None if there's none"""
        try:
            xml = fat32.read_file(self.sd, "CommunityFeatures.XML").decode("ascii", "replace")
        except FileNotFoundError:
            return None
        m = re.search(r'name="cpuMonitor"\s+value="(\d+)"', xml)
        return int(m.group(1)) if m else None

    # --- what the user does

    def button(self, b, on):
        """Buttons::buttonAction() as readButtonsAndPads() calls it (inCardRoutine = sdRoutineLock)"""
        se.drain_uarts(self.emu)
        lock = self.emu.u8(self.sym["sdRoutineLock"])
        return self.emu.call(self.sym.find("_ZN7Buttons12buttonActionEhbb"), b, int(on), lock) & 0xFF

    def shortcut(self, locked=False):
        """LEARN held, the tempo encoder pressed and let go, LEARN let go; with locked, the card busy for the tempo
        encoder's press and release. The popups shown meanwhile."""
        self.popups = []
        self.button(LEARN, True)
        if locked:
            self.emu.uc.mem_write(self.sym["sdRoutineLock"], b"\x01")
        self.button(TEMPO_ENC, True)
        self.button(TEMPO_ENC, False)
        if locked:
            self.emu.uc.mem_write(self.sym["sdRoutineLock"], b"\x00")
        self.button(LEARN, False)
        return list(self.popups)

    def press(self, b, run=True):
        self.button(b, True)
        self.button(b, False)
        if run:
            self.run(0.5)  # The view's transition

    def run(self, seconds):
        se.run_task_manager(self.emu, seconds)

    def play(self):
        self.emu.call(self.sym.find("_ZN15PlaybackHandler17playButtonPressedEl"), 0)
        return self.emu.u8(self.sym["playbackHandler"] + 16)

    def settings_menu(self, value):
        """Settings > CPU monitor set to value as the menu does, then the menu left"""
        emu, sym = self.emu, self.sym
        editor = sym["soundEditor"]
        emu.call(sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], editor, 0, sym["settingsRootMenu"], 0)
        emu.call(sym["_Z6openUIP2UI"], editor)
        opened = emu.call(sym["_Z12getCurrentUIv"]) == editor
        menu = sym["cpuMonitorMenu"]
        emu.uc.mem_write(menu + MENU_VALUE_OFFSET, bytes([value]))
        emu.call(sym.find("_ZN6deluge3gui9menu_item14CpuMonitorMode17writeCurrentValueEv"), menu)
        running = self.profiler_running()
        emu.call(sym["_ZN11SoundEditor14exitCompletelyEv"], editor)
        return opened, running

    def oled_display(self):
        """The OLED in place of the 7-segment display (deluge::hid::display::swapDisplayType())"""
        self.emu.call(self.sym["_ZN6deluge3hid7display15swapDisplayTypeEv"])
        self.oled = True

    def close(self):
        self.dma.close()


def popup_for(deluge, mode):
    return f"CPU monitor: {NAMES[mode]}" if deluge.oled else {OFF: "COFF", ON: "C-ON", ALERTS: "C-AL"}[mode]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools")
    ap.add_argument("--out", default=os.getcwd())
    a = ap.parse_args()
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(a.out, exist_ok=True)
    sd = os.path.join(a.out, "cpumonitor.img")
    files, lengths = make_sd.samples()
    # The song opens in its first synth's clip view
    xml = make_sd.song_xml(lengths, 1, 2).replace("<instrumentClip", '<instrumentClip\n\t\t\tbeingEdited="1"', 1)
    files["SONGS/DEFAULT.XML"] = xml.encode()
    make_sd.fat32.build(sd, files)

    print("== 1. boot, a card without CommunityFeatures.XML", flush=True)
    d = Deluge(a.elf, sd, tools)
    check("the monitor off", d.mode() == OFF, NAMES[d.mode()])
    check("the song opens in the clip view", d.root() == d.sym["instrumentClipView"], f"root UI {d.root():#x}")
    d.press(SESSION_VIEW)
    check("Song view", d.root() == d.sym["sessionView"], f"root UI {d.root():#x}")

    print("== 2. Song view, stopped: the shortcut", flush=True)
    scale = d.clock_scale()
    popups = d.shortcut()
    check("On", d.mode() == ON, NAMES[d.mode()])
    check(f"its popup \"{popup_for(d, ON)}\"", popups == [popup_for(d, ON)], repr(popups))
    check("no tempo popup, the UI mode back to none", d.tempo_popups == 0 and d.ui_mode() == 0,
          f"{d.tempo_popups} tempo popups, UI mode {d.ui_mode():#x}")
    check("the clock-out scale untouched", d.clock_scale() == scale, f"{scale} -> {d.clock_scale()}")
    check("the card says cpuMonitor 1", d.saved() == ON, f"{d.saved()}")

    print("== 3. the clip view, playing: Off, then On", flush=True)
    d.press(CLIP_VIEW)
    check("the clip view", d.root() == d.sym["instrumentClipView"], f"root UI {d.root():#x}")
    check("playing", d.play())
    d.run(0.2)
    samples = d.samples()
    popups = d.shortcut()
    check("Off, its popup", d.mode() == OFF and popups == [popup_for(d, OFF)], f"{NAMES[d.mode()]}, {popups!r}")
    check("the card says 0", d.saved() == OFF, f"{d.saved()}")
    d.run(0.3)
    popups = d.shortcut()
    d.run(0.3)
    check("On, its popup", d.mode() == ON and popups == [popup_for(d, ON)], f"{NAMES[d.mode()]}, {popups!r}")
    check("the card says 1", d.saved() == ON, f"{d.saved()}")
    played = (d.samples() - samples) & 0xFFFFFFFF
    check("the audio went on (0.6 s)", abs(played - 0.6 * 44100) < 0.05 * 44100, f"{played} samples")
    check("no tempo popup, the UI mode back to none", d.tempo_popups == 0 and d.ui_mode() == 0,
          f"{d.tempo_popups} tempo popups, UI mode {d.ui_mode():#x}")

    print("== 4. the card busy (sdRoutineLock) during the press", flush=True)
    d.shortcut(locked=True)
    before = d.saved()
    d.emu.uc.mem_write(d.sym["sdRoutineLock"], b"\x01")
    d.run(0.2)  # cpu_stats::routine() runs, the card still busy
    busy = d.saved()
    d.emu.uc.mem_write(d.sym["sdRoutineLock"], b"\x00")
    d.run(0.2)
    check("Off at once; the card unchanged while busy, 0 once free", d.mode() == OFF and before == ON and busy == ON
          and d.saved() == OFF, f"{NAMES[d.mode()]}; {before}, {busy}, then {d.saved()}")
    d.shortcut()
    check("the shortcut again: On, saved", d.mode() == ON and d.saved() == ON, f"{NAMES[d.mode()]}, {d.saved()}")
    d.close()
    del d

    print("== 5. restart", flush=True)
    d = Deluge(a.elf, sd, tools)
    check("On", d.mode() == ON, NAMES[d.mode()])
    d.oled_display()
    d.play()
    d.run(1.6)
    text = d.string(d.sym.find("_ZN9cpu_stats12_GLOBAL__N_14lineE"))  # What cpu_stats::oledInfo() gives the OLED
    check("its line on the OLED", d.oled and text.startswith("CPU"), repr(text))
    stats = [m for m in d.sysex if len(m) > 5 and m[5] == SYSEX_COMMAND]
    check("its SysEx on USB", len(stats) >= 1, f"{len(stats)} messages")

    print("== 6. the menu: Alerts, left", flush=True)
    opened, _ = d.settings_menu(ALERTS)
    check("Settings opened, Alerts; the card says 2", opened and d.mode() == ALERTS and d.saved() == ALERTS,
          f"{NAMES[d.mode()]}, {d.saved()}")
    first = d.shortcut()
    mode_first = d.mode()
    second = d.shortcut()
    check("the shortcut: Off, then Alerts again", mode_first == OFF and d.mode() == ALERTS
          and first + second == [popup_for(d, OFF), popup_for(d, ALERTS)], f"{NAMES[mode_first]}, "
          f"{NAMES[d.mode()]}, {first + second!r}")
    check("the card says 2", d.saved() == ALERTS, f"{d.saved()}")
    d.close()
    del d
    d = Deluge(a.elf, sd, tools)
    check("restart: Alerts", d.mode() == ALERTS, NAMES[d.mode()])
    opened, running = d.settings_menu(PROFILE)
    check("the menu on Profile (the profiler running), left: the card says 3", opened and running
          and d.saved() == PROFILE, f"profiler {'on' if running else 'off'}, {d.saved()}")
    d.close()
    del d

    print("== 7. restart; the keyboard and the drone view", flush=True)
    d = Deluge(a.elf, sd, tools)
    check("On, the profiler not running", d.mode() == ON and not d.profiler_running(),
          f"{NAMES[d.mode()]}, profiler {'on' if d.profiler_running() else 'off'}")
    d.press(KEYBOARD)
    keyboard = d.root() == d.sym["keyboardScreen"]
    popups = d.shortcut()
    d.run(0.2)
    check("the keyboard: Off, no voice, the UI mode back to none", keyboard and d.mode() == OFF and d.voices() == 0
          and d.ui_mode() == 0 and d.root() == d.sym["keyboardScreen"],
          f"{'keyboard' if keyboard else 'not the keyboard'}, {NAMES[d.mode()]}, {d.voices()} voices, UI mode "
          f"{d.ui_mode():#x}, {popups!r}")
    d.press(SESSION_VIEW)
    d.press(SCALE_MODE, run=False)  # changeRootUI() at once; see above why the task manager doesn't run here
    drone = d.root() == d.sym["droneView"]
    voices = d.voices()
    popups = d.shortcut()
    check("the drone view: On, no voice, the UI mode back to none", drone and d.mode() == ON and d.voices() == voices
          and d.ui_mode() == 0 and d.root() == d.sym["droneView"],
          f"{'drone view' if drone else 'not the drone view'}, {NAMES[d.mode()]}, {voices} -> {d.voices()} voices, "
          f"UI mode {d.ui_mode():#x}, {popups!r}")
    check("the card says 1", d.saved() == ON, f"{d.saved()}")
    d.close()

    print(f"CPU monitor shortcut and restart ({os.path.basename(a.elf)}): {checks - failures} of {checks} ok",
          flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
