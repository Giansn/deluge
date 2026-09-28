#!/usr/bin/env python3
"""README screenshots: the real OLED of the firmware in the emulator, driven as the user would, each screen saved as
tests/songchange's save_oled() writes it: <out>/oled_<name>.png (3x) and .txt ('#' lit, '.' dark), without the top 5
rows the OLED doesn't show. Nothing is modelled for the pictures: what's saved is OLED::oledCurrentImage, the image the
firmware last sent to the display (with its popup or the CPU monitor's line when it has one).

tests/stress/ui's Rig (the task manager runs; the CPU monitor off, as after a power-up). The card (build_card()):
DEFAULT, make_sd.py's song with 2 synths, its kit, audio track and drone (loaded at boot, in Song view), and the same
song as "New Sitar Grii" at 120.00 BPM in A3 minor (make_sd's 229.75 samples a tick show as 119.97, its root note 0 as
C-2; the Song view shows the root note with its octave):
  drone             SCALE in Song view: the drone view, its first tone in Hz
  volume_db         Song view, AFFECT ENTIRE, the first mod button (volume/pan), the upper gold knob -1: the popup
                    in dB
  master_tune       Settings > Tuning > Master tune (Hz), the select encoder turned -8 (1 Hz a step): 432.0
  firmware_version  Settings > Firmware version
  cpu_monitor       Settings > CPU monitor: On, the menu left; New Sitar Grii loaded (the song browser, as
                    Rig.change_song()), playing: the monitor's line in Song view
tests/browser's card and song browser (songchange_emu.Deluge):
  song_browser      NEW/New Sitar Grii pressed (its versions folded out), -1 +1: New Drum Idea (a group folded in)
                    above it
Each screen is taken with no popup up (the task manager runs until it's gone), but volume_db's.

Usage: oled_screens_emu.py <deluge.elf> [--out DIR] [--tools PREFIX] [--build DIR] [--keep-image]
  --build: where blockcount.so is (default $BLOCKCOUNT_DIR, else the out dir; built there if missing).
Needs python3 with unicorn 2 and numpy, a C compiler (blockcount.c), the toolchain's gdb. About 2 minutes.
"""
import argparse
import os
import re
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.normpath(os.path.join(HERE, ".."))
for d in ("song", "songchange", "browser", "stress/ui"):
    sys.path.insert(0, os.path.join(TESTS, d))
import fat32  # noqa: E402
import song_emu as se  # noqa: E402
import song_groups_emu as sg  # noqa: E402
import songchange_emu as sc  # noqa: E402
import stress_ui_emu as ui  # noqa: E402

OLED_MAIN, OLED_CURRENT = "_ZN6deluge3hid7display4OLED4mainE", "_ZN6deluge3hid7display4OLED16oledCurrentImageE"
POPUP_WIDTH = "_ZN6deluge3hid7display14oledPopupWidthE"
CPU_LINE = "_ZN9cpu_stats12_GLOBAL__N_14lineE"  # What cpu_stats::oledInfo() gives the OLED
AFFECT_ENTIRE = sc.button_xy(3, 0)  # affectEntireButtonCoord
SONG = "New Sitar Grii"  # cpu_monitor's song


def log(s):
    print(s, flush=True)


class Oled:
    """songchange_emu.Deluge's oled_image() and save_oled() on the Rig's emulator."""
    oled_image, save_oled = sc.Deluge.oled_image, sc.Deluge.save_oled
    oled = True

    def __init__(self, emu, out_dir):
        self.emu, self.out_dir = emu, out_dir
        self.var = {n: emu.sym[n] for n in (OLED_MAIN, OLED_CURRENT)}

    def u32(self, a):
        return self.emu.u32(a)


class Settings:
    """The sound editor on Settings, opened as stress_ui_emu.set_menus() opens it; the select encoder
    (SoundEditor::selectEncoderAction()), its button and BACK (Buttons::buttonAction()) as the user turns and presses
    them."""

    def __init__(self, rig):
        self.rig, self.emu = rig, rig.emu
        sym = self.sym = rig.emu.sym
        self.editor = sym["soundEditor"]
        self.cur, self.depth, self.record = se.gdb_values(rig.emu, [
            "(int)&settingsRootMenu.current_item_ - (int)&settingsRootMenu",
            "(int)&soundEditor.navigationDepth - (int)&soundEditor",
            "(int)&soundEditor.menuItemNavigationRecord - (int)&soundEditor"])

    def open(self):
        rig, sym = self.rig, self.sym
        rig.action("settings setup", sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], self.editor, 0,
                   sym["settingsRootMenu"], 0)
        rig.action("settings open", sym["_Z6openUIP2UI"], self.editor)
        rig.tm(0.3)

    def item(self):
        """The menu item shown: menuItemNavigationRecord[navigationDepth]."""
        return self.emu.u32(self.editor + self.record + 4 * self.emu.u8(self.editor + self.depth))

    def turn(self, offset):
        self.rig.action(f"select encoder {offset:+d}", self.sym.find("_ZN11SoundEditor19selectEncoderActionEa"),
                        self.editor, offset)
        self.rig.tm(0.05)

    def press(self, b):
        self.rig.button(b, True)
        self.rig.button(b, False)
        self.rig.tm(0.3)

    def enter(self, name):
        """In the submenu shown, the select encoder turned to the item, pressed: the item shown."""
        submenu, item = self.item(), self.sym[name]
        selected = lambda: self.emu.u32(self.emu.u32(submenu + self.cur))  # noqa: E731 (Submenu::current_item_)
        for direction in (1, -1):
            for _ in range(20):
                if selected() == item:
                    break
                self.turn(direction)
        if selected() != item:
            raise SystemExit(f"{name} not found in the menu")
        self.press(ui.SELECT_ENC)
        if self.item() != item:
            raise SystemExit(f"{name} not entered")


def quiet(rig, limit_s=5):
    """The task manager until no popup is up."""
    waited = 0.0
    while rig.emu.u32(rig.emu.sym[POPUP_WIDTH]) and waited < limit_s:
        rig.tm(0.1)
        waited += 0.1
    if rig.emu.u32(rig.emu.sym[POPUP_WIDTH]):
        raise SystemExit(f"a popup still up after {limit_s} s: {rig.popups[-1:]}")


def build_card(path):
    """SONGS/DEFAULT.XML (the song of stress_ui_emu.heavy_songs() with 2 synths) and the same as SONG: 120.00 BPM
    (44100 * 60 / (120 * 96) = 229.6875 samples a tick: 229 and .6875 * 2^32 as a signed 32-bit fraction), root note
    69 (A3; the song is in minor)."""
    files, songs = ui.heavy_songs(2)
    files["SONGS/DEFAULT.XML"] = songs["DEFAULT"].encode()
    xml, n = re.subn(r'\n\ttimerTickFraction="-?\d+"\n\trootNote="\d+"',
                     '\n\ttimerTickFraction="-1342177280"\n\trootNote="69"', songs["DEFAULT"], count=1)
    if n != 1 or '\n\ttimePerTimerTick="229"' not in xml:
        raise SystemExit("no tempo or root note in make_sd's song")
    files[f"SONGS/{SONG}.XML"] = xml.encode()
    fat32.build(path, files)


def rig_screens(a, out):
    image = os.path.join(out, "screens.img")
    build_card(image)
    rig = ui.Rig(a.elf, a.tools, a.build, image, "instant")
    emu, sym = rig.emu, rig.emu.sym
    shot = Oled(emu, out)
    rig.action("CPU monitor off", sym.find("_ZN9cpu_stats7setModeEh"), 0)  # The Rig's CpuStats switched it on
    rig.tm(0.5)
    log(f"booted: {rig.song_name()!r} in {rig.ui_name(root=True)}")

    def save(name, detail):
        shot.save_oled(name)
        log(f"  {name}: {detail} -> oled_{name}.png, .txt")

    # The drone view: SCALE in Song view (as stress_ui_emu's drone scenario)
    rig.button(ui.SCALE, True)
    rig.button(ui.SCALE, False)
    rig.tm(0.3)
    quiet(rig)
    save("drone", f"root UI {rig.ui_name(root=True)}")
    rig.button(ui.BACK, True)
    rig.button(ui.BACK, False)
    rig.tm(0.5)

    # The song's volume knob: AFFECT ENTIRE (the gold knobs on the song's master FX), the first mod button
    # (volume/pan), the upper gold knob down a step (getCurrentUI()->modEncoderAction(), as interpretEncoders() calls)
    for b in (AFFECT_ENTIRE, sc.MOD_BUTTON[0]):
        rig.button(b, True)
        rig.button(b, False)
        rig.tm(0.1)
    quiet(rig)
    n = len(rig.popups)
    rig.action("upper gold knob -1", sym.find("_ZN11SessionView16modEncoderActionEll"), rig.v["sessionView"], 1, -1)
    rig.tm(0.1)
    popups = [p[3] for p in rig.popups[n:]]
    if not any("dB" in p for p in popups) or not emu.u32(sym[POPUP_WIDTH]):
        raise SystemExit(f"no popup in dB: {popups}")
    save("volume_db", f"the popup {popups[-1]!r}")
    quiet(rig)

    # Settings > Tuning > Master tune (Hz), 8 clicks down from 440.0
    s = Settings(rig)
    s.open()
    s.enter("tuningSubmenu")
    s.enter("masterTuneMenu")
    for _ in range(8):
        s.turn(-1)
    rig.tm(0.3)
    quiet(rig)
    value = emu.u32(sym["masterTuneMenu"] + ui.MENU_VALUE_OFFSET)
    if value != 4320:
        raise SystemExit(f"master tune {value / 10} Hz, not 432.0")
    save("master_tune", f"the menu's value {value / 10} Hz")
    s.press(ui.BACK)
    s.press(ui.BACK)

    # Settings > Firmware version
    s.enter("firmwareVersionMenu")
    rig.tm(0.3)
    quiet(rig)
    save("firmware_version", "Settings > Firmware version")
    s.press(ui.BACK)

    # Settings > CPU monitor: On; the menu left (BACK twice); SONG loaded (the song browser, the select encoder
    # pressed on it; stopped: at once), playing
    s.enter("cpuMonitorMenu")
    s.turn(1)
    mode = emu.u8(sym["_ZN9cpu_stats4modeE"])
    s.press(ui.BACK)
    s.press(ui.BACK)
    loaded = rig.change_song(SONG, 0.5)
    if not loaded["ok"] or loaded["ui"] != "sessionView":
        raise SystemExit(f"{SONG} not loaded in Song view: {loaded}")
    rig.play()
    rig.tm(2.0)
    quiet(rig)
    line = bytes(emu.uc.mem_read(sym.find(CPU_LINE), 64)).split(b"\0")[0].decode(errors="replace")
    if mode != 1 or not line.startswith("CPU"):
        raise SystemExit(f"CPU monitor mode {mode}, its line {line!r}")
    save("cpu_monitor", f"the line {line!r}, the song {rig.song_name()!r} in {rig.ui_name(root=True)}")
    if rig.problems or rig.error_popups():
        raise SystemExit(f"problems {rig.problems}, error popups {rig.error_popups()}")
    if not a.keep_image:
        os.remove(image)


def browser_screen(a, out):
    image = os.path.join(out, "songs.img")
    sg.build_sd(image)
    b = sg.Browser(types.SimpleNamespace(elf=a.elf, tools=a.tools, build=a.build), image, True, out)
    if not b.open("New Sitar Grii", folder=sg.NEW):
        raise SystemExit("the song browser didn't open on NEW/New Sitar Grii")
    b.press(sg.BUTTON_SELECT)  # Folds the group out
    b.turn(-1)  # New Drum Idea (a group folded in) scrolls in above
    b.turn(1)
    b.settle()
    for _ in range(400):  # The image sent, no popup
        b.d.step()
        if not b.d.u32(b.d.var[POPUP_WIDTH]):
            break
    st = b.state()
    if st["name"] != "New Sitar Grii" or st["groups"] != ["New Sitar Grii"]:
        raise SystemExit(f"not on New Sitar Grii with its group folded out: {st}")
    b.d.save_oled("song_browser")  # (Before frame(), which draws into OLED::main again)
    log(f"  song_browser: at {st['name']!r}, folded out {st['groups']}; rows {b.show(b.frame())} -> "
        f"oled_song_browser.png, .txt")
    if not a.keep_image:
        os.remove(image)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "screens"))
    ap.add_argument("--tools")
    ap.add_argument("--build", default=os.environ.get("BLOCKCOUNT_DIR"))
    ap.add_argument("--keep-image", action="store_true", help="keep the card images in the out dir")
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
    log(f"OLED screens of {a.elf} -> {a.out}")
    rig_screens(a, a.out)
    browser_screen(a, a.out)


if __name__ == "__main__":
    main()
