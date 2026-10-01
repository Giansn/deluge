#!/usr/bin/env python3
"""SETTINGS > COMMUNITY FEATURES > HORIZONTAL MENUS switched off at run time (the beta notes suggest it against the menu
crashes), then a field button (SYNTH, KIT, MIDI, CV) pressed in a menu that was horizontal before: no crash.

Why it can crash: HorizontalMenu::buttonAction() takes SYNTH / KIT / MIDI / CV (and SCALE / CROSS) whatever the menu's
rendering style, and handleInstrumentButtonPress() picks the field from paging.visiblePageItems. That span is only
prepared by renderOLED() while the menu renders horizontally, and it points into one static vector that every
horizontal menu's preparePaging() refills. Once horizontal menus are off, a menu's paging still holds its last span, now
showing the items of whichever horizontal menu rendered last: the item found there isn't one of this menu's
(std::ranges::find() returns items.end()), and current_item_ = end() is dereferenced (*current_item_ ==
previous, initializeItem(*current_item_): a virtual call through the word after the vector).

The test, on the real firmware in the emulator (OLED, horizontal menus on as by default): the synth clip PADA's sound
editor, MASTER opened (horizontal: its paging prepared) and left, LFO 1 opened (the shared vector now holds LFO 1's
fields) and left; SHIFT + SELECT (settings), COMMUNITY FEATURES > HORIZONTAL MENUS turned off, settings left (saved);
the sound editor again, MASTER opened (a vertical list now), SYNTH, KIT, MIDI, CV pressed. PASS: no crash, freeze,
hang, write through a null pointer or wild access, the menu still MASTER with a current item inside its own list.
On beta-fix2 (and 62a516c2): see the last line.

Usage: hmenu_off_buttons_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..")))  # beta-1.3/tests: rig13, fuzz_ui, menu_walk_emu
import rig13  # noqa: E402,F401
import fuzz_ui  # noqa: E402
import menu_walk_emu as mw  # noqa: E402
import stress_ui_emu as su  # noqa: E402


def choose(w, menu, target, what):
    """The select encoder turned until target is the menu's current item (in a vertical menu; most don't wrap
    around, so towards it)."""
    kids = w.kids(menu)
    for _ in range(len(kids) + 2):
        c = w.cur(menu)
        if c == target:
            return
        if target not in kids or c not in kids:
            break
        w.turn("select", 1 if kids.index(target) > kids.index(c) else -1)
    raise mw.Lost(f"{what}: {w.name(target)} not reached (at {w.name(w.cur(menu) or 0)})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    if os.path.exists(image):
        os.remove(image)
    fuzz_ui.make_card(image, 2, os.path.join(a.out, "card-template.img"))
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    inp = fuzz_ui.Inputs(rig, "v13")
    w = mw.Walker(rig, inp, True, None, print)
    w.null = fuzz_ui.NullPage(rig.emu)
    emu, sym = rig.emu, rig.emu.sym
    emu.uc.ctl_flush_tb()
    item = lambda n: sym[n]  # noqa: E731  (the menu items are global objects)
    res = dict(elf=a.elf, hmenus_at_boot=w.hmenus())
    try:
        # Two horizontal menus rendered: MASTER, then LFO 1
        if not w.open_context("synth"):
            raise mw.Lost("the sound editor didn't open")
        root = w.where()[2]
        for name in ("soundMasterMenu", "lfo1Menu"):
            choose(w, root, item(name), "sound editor")
            w.press("SELECT_ENC", after=0.1)
            res[f"{name}_horizontal"] = w.horizontal(w.where()[2])
            w.press("BACK", after=0.1)
        w.leave_editor()
        # SETTINGS > COMMUNITY FEATURES > HORIZONTAL MENUS: off
        w.open_context("settings")
        settings = w.where()[2]
        choose(w, settings, item("runtimeFeatureSettingsMenu"), "settings")
        w.press("SELECT_ENC", after=0.1)
        features = w.where()[2]
        choose(w, features, item("deluge::gui::menu_item::runtime_feature::menuHorizontalMenus"), "community features")
        w.press("SELECT_ENC", after=0.1)
        for _ in range(3):
            if not w.hmenus():
                break
            w.turn("select", 1)
        res["hmenus_after_setting"] = w.hmenus()
        w.leave_editor()
        # MASTER again (vertical now): the field buttons
        if not w.open_context("synth"):
            raise mw.Lost("the sound editor didn't open again")
        root = w.where()[2]
        choose(w, root, item("soundMasterMenu"), "sound editor")
        w.press("SELECT_ENC", after=0.1)
        master = w.where()[2]
        res["menu"] = w.name(master)
        res["horizontal_now"] = w.horizontal(master)
        for b in mw.COLUMN_BUTTONS:
            w.press(b, after=0.1)
        ui, d, now = w.where()
        cur = w.cur(now) if ui == "soundEditor" else None
        res.update(ui_after=ui, menu_after=w.name(now), current_after=w.name(cur or 0))
        res["ok"] = (res["hmenus_at_boot"] == 1 and res["hmenus_after_setting"] == 0 and not res["horizontal_now"]
                     and ui == "soundEditor" and now == master and cur is not None)
    except su.Stop:
        res["stopped"] = rig.problems[-1] if rig.problems else "stop"
        res["last_inputs"] = list(w.trace)[-8:]
        res["ok"] = False
    except mw.Lost as ex:
        res["lost"] = str(ex)
        res["ok"] = False
    res["problems"] = rig.problems
    res["null_writes"] = w.null.writes
    res["invalid"] = [dict(page=hex(k[0]), at=k[1], during=k[2], n=c) for k, c in rig.invalid.most_common(10)]
    json.dump(res, open(os.path.join(a.out, "hmenu_off_buttons.json"), "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))
    ok = res["ok"] and not rig.problems and not res["null_writes"] and not res["invalid"]
    print("PASS: the field buttons in a menu that was horizontal are harmless once horizontal menus are off" if ok
          else "FAIL: " + (f"{rig.problems[-1].get('kind')} {rig.problems[-1].get('detail')} during "
                           f"{rig.problems[-1].get('what')}" if rig.problems else
                           res.get("lost") or f"null writes {res['null_writes'][:2]}, wild {res['invalid'][:2]}, "
                           f"menu after {res.get('menu_after')}, current {res.get('current_after')}"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
