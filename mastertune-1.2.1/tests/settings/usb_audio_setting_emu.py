#!/usr/bin/env python3
"""USB audio's setting survives a restart made right from the menu (mastertune-v16), on the real firmware in the
emulator (the harness of ../song).

The Settings menu saves its values to the card only when it's left (SoundEditor::exitCompletely()), and USB audio's
setting says "restart to apply" when it's switched on. Restarted from there, as the user did, it was off again. Now
UsbAudioSetting::writeCurrentValue() saves CommunityFeatures.XML at once.

The test: a card without CommunityFeatures.XML; boot and load the song; the menu item's value set to On and its
writeCurrentValue() called, as the select button does, the menu not left. Then:
- CommunityFeatures.XML on the card says usbAudio 1
- a second boot on the same card (the restart): the menu reads On (Setting::readCurrentValue())
Usage: usb_audio_setting_emu.py <deluge.elf> [--tools PREFIX] [--out DIR]   (BLOCKCOUNT_DIR: where blockcount.so is)
Exit status 0 when both hold."""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402

MENU_VALUE_OFFSET = 12  # Selection's value_ (UsbAudioSetting::writeCurrentValue(): ldrd r2, r1, [r0, #12])
ON = 1


def boot(elf, sd, tools):
    emu = se.Emulator(elf, sd, tools, os.environ.get("BLOCKCOUNT_DIR", HERE), lambda s: None)
    se.setup_sd(emu)
    se.boot(emu)
    se.load_startup_song(emu)
    return emu


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools")
    ap.add_argument("--out", default=os.getcwd())
    a = ap.parse_args()
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(a.out, exist_ok=True)
    sd = os.path.join(a.out, "usbaudio.img")
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 2).encode()
    make_sd.fat32.build(sd, files)

    failures = 0
    emu = boot(a.elf, sd, tools)
    menu = emu.sym["_ZN6deluge3gui9menu_item15runtime_feature12menuUsbAudioE"]
    emu.w32(menu + MENU_VALUE_OFFSET, ON)
    emu.call(emu.sym.find("_ZN6deluge3gui9menu_item15runtime_feature15UsbAudioSetting17writeCurrentValueEv"), menu)
    del emu  # The restart: nothing else happens, the menu isn't left

    try:
        xml = fat32.read_file(sd, "CommunityFeatures.XML").decode("ascii", "replace")
    except FileNotFoundError:
        xml = ""
    m = re.search(r'name="usbAudio"\s+value="(\d+)"', xml)
    saved = m and int(m.group(1)) == ON
    failures += not saved
    print(f"  {'ok  ' if saved else 'FAIL'} CommunityFeatures.XML on the card: usbAudio "
          f"{m.group(1) if m else '(not there)'}, without leaving the menu")

    emu = boot(a.elf, sd, tools)
    menu = emu.sym["_ZN6deluge3gui9menu_item15runtime_feature12menuUsbAudioE"]
    emu.w32(menu + MENU_VALUE_OFFSET, 0)
    emu.call(emu.sym.find("_ZN6deluge3gui9menu_item15runtime_feature7Setting16readCurrentValueEv"), menu)
    value = emu.u32(menu + MENU_VALUE_OFFSET)
    failures += value != ON
    print(f"  {'ok  ' if value == ON else 'FAIL'} after the restart the menu shows USB audio "
          f"{'on' if value == ON else 'off'}")
    print(f"USB audio setting across a restart ({os.path.basename(a.elf)}): {2 - failures} of 2 ok")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
