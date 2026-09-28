#!/usr/bin/env python3
"""The filters' crossing guard (mastertune v18, Settings > Community features > Filter crossing guard): off by default,
switched on it survives a restart and reaches the filters, on the real firmware in the emulator (the harness of
../song).

The test: a card without CommunityFeatures.XML; boot and load the song. Then:
- the setting is off (the menu reads Off), and after 50 ms of audio FilterSet::crossingGuard is false
- the Settings menu opened, the item set to On (its writeCurrentValue(), as the select button does), the menu left
  (SoundEditor::exitCompletely(), which saves CommunityFeatures.XML): the file says filterCrossingGuard 1, and after an
  audio routine FilterSet::crossingGuard is true
- a second boot on the same card (the restart): the menu reads On, and after an audio routine the filters have it
- switched off again and saved: the file says 0, the filters don't have it
Usage: crossing_guard_setting_emu.py <deluge.elf> [--tools PREFIX] [--out DIR]   (BLOCKCOUNT_DIR: where blockcount.so is)
Exit status 0 when all hold."""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402

MENU_VALUE_OFFSET = 12  # Selection's value_ (as usb_audio_setting_emu.py)
MENU = "_ZN6deluge3gui9menu_item15runtime_feature23menuFilterCrossingGuardE"
WRITE = "_ZN6deluge3gui9menu_item15runtime_feature7Setting17writeCurrentValueEv"
READ = "_ZN6deluge3gui9menu_item15runtime_feature7Setting16readCurrentValueEv"
GUARD = "_ZN6deluge3dsp6filter9FilterSet13crossingGuardE"

failures = 0
checks = 0


def check(what, ok):
    global failures, checks
    checks += 1
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {what}", flush=True)


def boot(elf, sd, tools):
    emu = se.Emulator(elf, sd, tools, os.environ.get("BLOCKCOUNT_DIR", HERE), lambda s: None)
    se.setup_sd(emu)
    se.boot(emu)
    se.load_startup_song(emu)
    # The audio in real time while the task manager runs (as cpu_monitor_shortcut_emu.py): the audio routine renders
    se.RealTimeDma(emu)
    return emu


def menu_value(emu):
    menu = emu.sym[MENU]
    emu.w32(menu + MENU_VALUE_OFFSET, 0xFF)
    emu.call(emu.sym.find(READ), menu)
    return emu.u32(menu + MENU_VALUE_OFFSET)


def filters_have_it(emu):
    """50 ms of the task manager (the audio routine, which sets FilterSet::crossingGuard from the setting before the
    song renders), then the flag"""
    emu.uc.mem_write(emu.sym[GUARD], bytes([0xAA]))  # neither 0 nor 1: the routine must write it
    se.run_task_manager(emu, 0.05)
    return emu.u8(emu.sym[GUARD])


def set_in_menu(emu, value):
    """Settings opened, the item set to value, the menu left (which saves CommunityFeatures.XML)"""
    sym = emu.sym
    editor = sym["soundEditor"]
    emu.call(sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], editor, 0, sym["settingsRootMenu"], 0)
    emu.call(sym["_Z6openUIP2UI"], editor)
    menu = sym[MENU]
    emu.w32(menu + MENU_VALUE_OFFSET, value)
    emu.call(sym.find(WRITE), menu)
    emu.call(sym["_ZN11SoundEditor14exitCompletelyEv"], editor, timeout_s=10)


def saved(sd):
    try:
        xml = fat32.read_file(sd, "CommunityFeatures.XML").decode("ascii", "replace")
    except FileNotFoundError:
        return None
    m = re.search(r'name="filterCrossingGuard"\s+value="(\d+)"', xml)
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools")
    ap.add_argument("--out", default=os.getcwd())
    a = ap.parse_args()
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(a.out, exist_ok=True)
    sd = os.path.join(a.out, "crossingguard.img")
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 2).encode()
    make_sd.fat32.build(sd, files)
    try:
        emu = boot(a.elf, sd, tools)
        check("off by default: the menu reads Off", menu_value(emu) == 0)
        check("off by default: the filters don't have it (FilterSet::crossingGuard false)", filters_have_it(emu) == 0)
        set_in_menu(emu, 1)
        check(f"switched on and the menu left: CommunityFeatures.XML says filterCrossingGuard {saved(sd)}", saved(sd) == 1)
        check("switched on: the filters have it after the next audio routine", filters_have_it(emu) == 1)
        del emu  # the restart

        emu = boot(a.elf, sd, tools)
        check("after the restart the menu reads On", menu_value(emu) == 1)
        check("after the restart the filters have it", filters_have_it(emu) == 1)
        set_in_menu(emu, 0)
        check(f"switched off again: the file says {saved(sd)}, the filters don't have it",
              saved(sd) == 0 and filters_have_it(emu) == 0)
        del emu
    finally:
        os.remove(sd)
    print(f"Filter crossing guard setting ({os.path.basename(a.elf)}): {checks - failures} of {checks} ok")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
