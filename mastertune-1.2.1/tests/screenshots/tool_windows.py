#!/usr/bin/env python3
"""Screenshots of the PC tools' windows for the README, each alone on an Xvfb screen, cropped to the window:
DelugeRec recording its test signal (--demo, no Deluge needed), DelugeTuner and DelugeBaseline (English) having read
their own demo card (demo_card(), as in their self-tests).

Usage: tool_windows.py <out dir> [--python python3.12] [--wait-scale 1]
Needs Xvfb, a Python with tkinter, numpy, sounddevice, soxr and pylibrb for the tools (--python) and Pillow with XCB for this
one. The tools run with HOME in a temporary folder, so their settings files and the take don't touch the real ones.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "..", "tools"))
DISPLAY = ":57"
# The tool's main() with its App remembered and, 1.5 s after the window opened, an action on it (what a click does)
DRIVER = r"""
import os, sys, tkinter as tk
from pathlib import Path
tools, module, action = sys.argv[1:4]
sys.argv = [module + ".py"] + sys.argv[4:]
sys.path.insert(0, tools)
m = __import__(module)
apps = []
class App(m.App):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        apps.append(self)
m.App = App
mainloop = tk.Misc.mainloop
def run(self, n=0):
    self.after(1500, lambda: exec(action, {"app": apps[0], "m": m, "Path": Path, "home": Path(os.environ["HOME"])}))
    mainloop(self, n)
tk.Misc.mainloop = run
sys.exit(m.main())
"""
# name, module, its arguments, the action, seconds until the screenshot
SHOTS = [
    ("deluge_rec", "deluge_rec", ["--demo", "--out", "{home}/takes"], 'app.press("rec")', 6),
    ("deluge_tuner", "deluge_tuner", ["--lang", "en"],
     'app.card = m.card_root(str(m.demo_card(home / "Deluge card"))); app.load_card(); app.go()', 8),
    ("deluge_baseline", "deluge_baseline", ["--lang", "en"],
     'app.card = str(m.demo_card(home / "Deluge card")); app.load_songs(); app.go()', 6),
]


def grab_window():
    """The screen without the black root window around the (undecorated) tool window."""
    im = ImageGrab.grab(xdisplay=DISPLAY)
    bbox = im.convert("L").point(lambda v: 255 if v else 0).getbbox()
    return im.crop(bbox) if bbox else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--python", default="python3.12")
    ap.add_argument("--wait-scale", type=float, default=1.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    home = os.path.join(tempfile.gettempdir(), "mastertune")  # A fixed name: the tools show the card's path
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(home)
    xvfb = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "1920x1200x24", "-br", "-nolisten", "tcp"],
                            stderr=subprocess.DEVNULL)
    ok = True
    try:
        time.sleep(1.5)
        for name, module, argv, action, wait in SHOTS:
            env = dict(os.environ, DISPLAY=DISPLAY, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"))
            log = open(os.path.join(a.out, name + ".log"), "w")
            p = subprocess.Popen([a.python, "-c", DRIVER, TOOLS, module, action] + [x.format(home=home) for x in argv],
                                 env=env, cwd=home, stdout=log, stderr=subprocess.STDOUT)
            time.sleep(wait * a.wait_scale)
            alive = p.poll() is None
            im = grab_window() if alive else None
            if im:
                path = os.path.join(a.out, name + ".png")
                im.save(path, optimize=True)
                print(f"{name}: {im.size[0]}x{im.size[1]} -> {path}")
            else:
                ok = False
                print(f"{name}: FAILED ({'no window' if alive else f'exited with {p.returncode}'}; see {log.name})")
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
            log.close()
    finally:
        xvfb.terminate()
        xvfb.wait(5)
        shutil.rmtree(home, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
