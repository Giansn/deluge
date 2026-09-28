#!/usr/bin/env python3
"""Tests for tools/deluge_tuner.py (DelugeTuner: retune_library.py in a window).

- Where the new card goes (destination()): the folder chosen if it is empty or not there, else "Deluge 432 Hz" in it,
  numbered if taken, beside it if it is a card itself (the new card made before); a stopped run of the same card with
  the same settings continues, another card's or one with other settings doesn't; never in the card, a card at a
  drive's root (E:\\, /) too, as retune_library's own check of --out.
- The card: its root found from the card or from its SAMPLES folder; the pads as a bar of the categories; the list
  of a run in German and English.
- The window (tkinter and a display): READ writes nothing; RETUNE writes the same new card as retune_library's
  command line; stopped after a file (ESC), START continues and the card is the same as one made in one go; another
  card doesn't continue that run; not enough space; closed while it runs, it stops first; the tuning knob (whole Hz,
  0.1 Hz, the limits, typed); the settings saved; English.
- The language: the computer's to start with (Windows: its display language; macOS, Linux: the locale), a language
  chosen in the window wins over it the next time.
- The self-test of the .exe (needs soxr and pylibrb).

Usage: python3 test_deluge_tuner.py   Needs numpy and soxr (pylibrb for the self-test; tkinter and a display for
the window, else those are skipped). retune_library.py's own tests: tests/retune/run.sh.
"""
import argparse
import json
import multiprocessing
import ntpath
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "..", "tools")
sys.path.insert(0, TOOLS)
import deluge_tuner as dt  # noqa: E402
import retune_library as rl  # noqa: E402

try:
    import tkinter  # noqa: F401
    HAS_TK = bool(os.environ.get("DISPLAY")) or sys.platform.startswith("win")
except ImportError:
    HAS_TK = False
HAS_SOXR = rl.soxr is not None


def setUpModule():
    multiprocessing.set_start_method("spawn", force=True)  # As the window does: no fork of a process with Tk in it


def progress_file(folder, tenths=4320, rate=44100, no_float=False):
    """A run stopped in folder: its progress file (the header), a converted file."""
    folder = Path(folder)
    (folder / "SAMPLES").mkdir(parents=True, exist_ok=True)
    (folder / "SAMPLES" / "A.WAV").write_bytes(b"RIFF")
    header = rl.progress_header(argparse.Namespace(tenths=tenths, rate=rate, no_float=no_float))
    (folder / rl.PROGRESS).write_text(json.dumps(header) + "\n", encoding="utf-8")


class Destination(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.card = str(self.base / "card")
        (self.base / "card" / "SAMPLES").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def dest(self, chosen, runs=None, tenths=4320, rate=44100, no_float=False, card=None):
        return dt.destination(str(chosen), card or self.card, tenths, rate, no_float, runs or {})

    def test_empty_or_new(self):
        self.assertEqual(self.dest(self.base / "new"), (str(self.base / "new"), False))
        (self.base / "empty").mkdir()
        self.assertEqual(self.dest(self.base / "empty"), (str(self.base / "empty"), False))

    def test_a_new_folder_in_it(self):
        music = self.base / "music"
        music.mkdir()
        (music / "song.mp3").write_bytes(b"x")
        self.assertEqual(self.dest(music), (str(music / "Deluge 432 Hz"), False))
        (music / "Deluge 432 Hz").mkdir()
        (music / "Deluge 432 Hz" / "x").write_bytes(b"x")
        self.assertEqual(self.dest(music), (str(music / "Deluge 432 Hz (2)"), False))
        self.assertEqual(self.dest(music, tenths=4153), (str(music / "Deluge 415.3 Hz"), False))
        # The folder with the card in it: a new folder beside the card, never the card
        self.assertEqual(self.dest(self.base), (str(self.base / "Deluge 432 Hz"), False))

    def test_beside_a_card(self):
        made = self.base / "made"  # The new card made before: the next one beside it, not in it
        (made / "SONGS").mkdir(parents=True)
        (made / dt.REPORT).write_text("report")
        self.assertEqual(self.dest(made), (str(self.base / "Deluge 432 Hz"), False))

    def test_continues_only_its_own_run(self):
        run = self.base / "run"
        progress_file(run)
        runs = {str(run): self.card}
        self.assertEqual(self.dest(run, runs), (str(run), True))
        other = self.base / "other"
        (other / "SAMPLES").mkdir(parents=True)
        # Not recorded, another card, other settings: a new folder, beside the run (it holds SAMPLES)
        for kw in (dict(runs={}), dict(runs=runs, card=str(other)), dict(runs=runs, rate=0),
                   dict(runs=runs, no_float=True), dict(runs=runs, tenths=4400)):
            folder, resume = self.dest(run, **kw)
            self.assertFalse(resume, kw)
            self.assertNotEqual(folder, str(run), kw)
        progress_file(self.base / "Deluge 432 Hz")  # A stopped run in the new folder continues there
        self.assertEqual(self.dest(self.base, {str(self.base / "Deluge 432 Hz"): self.card}),
                         (str(self.base / "Deluge 432 Hz"), True))

    def test_never_in_the_card(self):
        for chosen in (self.card, os.path.join(self.card, "SAMPLES"), os.path.join(self.card, "new")):
            with self.assertRaises(ValueError):
                self.dest(chosen)

    def test_card_at_a_drives_root(self):
        self.assertTrue(dt.inside("/tmp/x", "/"))  # / ends with its separator: not //
        with mock.patch.object(dt.os, "path", ntpath):
            self.assertTrue(dt.inside("E:\\new", "E:\\"))
            self.assertTrue(dt.inside("e:\\NEW\\deeper", "E:\\"))
            self.assertFalse(dt.inside("F:\\new", "E:\\"))
            self.assertFalse(dt.inside("E:\\cards2", "E:\\cards"))
        # retune_library's check of --out: in a card at the root of its drive (C:\\ or /) refused
        args = rl.parser().parse_args(["--card", self.base.anchor, "--out", str(self.base / "x")])
        self.assertEqual(rl.check_args(args), "--out must be outside the card's folder (and not contain it)")
        args = rl.parser().parse_args(["--card", self.card, "--out", str(self.base / "card2")])
        self.assertIsNone(rl.check_args(args))


class SystemLanguage(unittest.TestCase):
    """The window's language to start with: German if the computer speaks German, else English."""

    def on_windows(self, langid):
        import ctypes
        import types

        def ui_language():
            if isinstance(langid, Exception):
                raise langid
            return langid
        windll = types.SimpleNamespace(kernel32=types.SimpleNamespace(GetUserDefaultUILanguage=ui_language))
        with mock.patch.object(dt.sys, "platform", "win32"), mock.patch.object(ctypes, "windll", windll, create=True):
            return dt.system_language()

    def on_unix(self, env, code=None):
        import locale
        with mock.patch.object(dt.sys, "platform", "linux"), mock.patch.dict(dt.os.environ, env, clear=True), \
                mock.patch.object(locale, "getlocale", lambda *a: (code, "UTF-8")):
            return dt.system_language()

    def test_windows(self):
        for langid, lang in ((0x0807, "de"), (0x0407, "de"), (0x0C07, "de"), (0x0409, "en"), (0x0809, "en"),
                             (0x040C, "en"), (OSError("no API"), "en")):
            self.assertEqual(self.on_windows(langid), lang, hex(langid) if isinstance(langid, int) else langid)

    def test_macos_linux(self):
        self.assertEqual(self.on_unix({"LANG": "de_CH.UTF-8"}), "de")
        self.assertEqual(self.on_unix({"LANG": "en_US.UTF-8"}), "en")
        self.assertEqual(self.on_unix({"LANG": "fr_CH.UTF-8"}), "en")
        self.assertEqual(self.on_unix({"LC_ALL": "de_DE.UTF-8", "LANG": "en_US.UTF-8"}), "de")
        self.assertEqual(self.on_unix({"LC_MESSAGES": "en_GB.UTF-8", "LANG": "de_CH.UTF-8"}), "en")
        self.assertEqual(self.on_unix({"LANG": "C"}), "en")
        self.assertEqual(self.on_unix({}, "de_AT"), "de")  # Nothing set: Python's locale
        self.assertEqual(self.on_unix({}, None), "en")


class Card(unittest.TestCase):
    def test_card_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "card" / "SAMPLES").mkdir(parents=True)
            card = os.path.join(tmp, "card")
            self.assertEqual(dt.card_root(card), card)
            self.assertEqual(dt.card_root(os.path.join(card, "SAMPLES")), card)
            with self.assertRaises(ValueError):
                dt.card_root(tmp)
            with self.assertRaises(ValueError):
                dt.card_root(os.path.join(tmp, "gone"))

    def test_pads(self):
        pads = dt.pads_of({"converted": 700, "already native": 50, "left as it is": 30, "missing": 2}, 32)
        self.assertEqual(len(pads), 32)
        self.assertEqual([pads.count(k) for k in ("converted", "already native", "left as it is", "missing")],
                         [28, 2, 1, 1])
        self.assertEqual(pads, sorted(pads, key=[k for k, *_ in dt.CATEGORIES].index))
        self.assertEqual(dt.pads_of({"converted": 1}, 32), ["converted"] * 32)
        self.assertEqual(dt.pads_of({}, 32), [])

    def test_items_heard_in_tune(self):
        """Files already in the tuning by ear: their own line with the tuning, in both languages, and their pads."""
        result = dict(counts={"converted": 3, "heard in tune": 2}, xml=0, values=0, size_old=1 << 20,
                      size_new=1 << 20, as_float=[], quieter=[], warnings=[])
        with mock.patch.object(dt.rl, "pylibrb", object()):
            self.assertIn(("2 KLANGEN SCHON SO: NUR MARKIERT (432,0 HZ)", False),
                          dt.result_items(result, False, 4320, "/new"))
            with mock.patch.object(dt, "LANG", "en"):
                self.assertIn(("2 SOUND IN TUNE: TAG ONLY (432.0 HZ)", False),
                              dt.result_items(result, True, 4320, None))
        self.assertEqual(dt.pads_of(result["counts"], 5), ["converted"] * 3 + ["heard in tune"] * 2)

    def test_items(self):
        result = dict(counts={"converted": 3, "already native": 1}, xml=2, values=12, size_old=3 << 20,
                      size_new=int(3.1 * (1 << 20)), as_float=["SAMPLES/A.WAV"], quieter=[], warnings=["a warning"])
        try:
            with mock.patch.object(dt.rl, "pylibrb", object()):
                self.assertEqual(dt.result_items(result, False, 4320, "/new"), [
                    ("3 UMGESTIMMT", False), ("1 SCHON IN DER STIMMUNG (432,0 HZ)", False),
                    ("2 SONGS/KITS/SYNTHS, 12 WERTE", False), ("3,0 MB -> 3,1 MB", False),
                    ("1 ALS 32-BIT FLOAT (SPITZEN ÜBER 0 DBFS)", False), ("NEUE KARTE: /new", False),
                    ("AUF EINE LEERE SD-KARTE KOPIEREN", False), ("MASTER TUNE AM DELUGE: 432,0 HZ", False),
                    ("WARNUNGEN (1)", True), ("a warning", False)])
                dt.LANG = "en"
                self.assertEqual(dt.result_items(result, True, 4153, None)[:4], [
                    ("3 TO RETUNE", False), ("1 ALREADY IN TUNE (415.3 HZ)", False),
                    ("2 SONGS/KITS/SYNTHS, 12 VALUES", False), ("3.0 MB -> ~3.1 MB", False)])
            with mock.patch.object(dt.rl, "pylibrb", None):
                self.assertIn(("NO RUBBER BAND: AUDIO CLIPS AND STRETCH STAY, THE DELUGE TUNES THEM AS IT PLAYS",
                               False), dt.result_items(result, True, 4320, None))
        finally:
            dt.LANG = "de"
        self.assertEqual((dt.cents(4320), dt.cents(4400), dt.cents(4153)), ("-31,8 CENT", "0,0 CENT", "-100,0 CENT"))


@unittest.skipUnless(HAS_TK and HAS_SOXR, "no tkinter, display or soxr: the window can't open or convert")
class Window(unittest.TestCase):
    """The window's buttons pressed, as by the mouse or the keys."""

    def setUp(self):
        import tkinter as tk
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.card = dt.demo_card(self.base / "card")
        self.out = self.base / "out"
        self.settings = self.base / "settings.json"
        self.root = tk.Tk()
        self.app = dt.App(self.root, str(self.settings), card=str(self.card), out=str(self.out), lang="de", jobs=1)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:  # Closed by the test
            pass
        dt.LANG = "de"
        self.tmp.cleanup()

    def press(self, *keys):
        for key in keys:
            self.app.press(key)
            dt.settle(self.root, self.app)

    def cli(self, out, *more):
        """The new card as retune_library's command line makes it."""
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "retune_library.py"), "--card", str(self.card),
                            "--out", str(out), "--quiet", *more], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return dt.card_files(out)

    def test_read_writes_nothing(self):
        before = dt.card_files(self.card)
        self.press("read", "start")
        self.assertEqual(self.app.list["title"], "432,0 HZ: 5 AUDIODATEIEN")
        self.assertIn(("NUR GELESEN: UMSTIMMEN SCHREIBT DIE NEUE KARTE", False), self.app.list["items"])
        self.assertFalse(self.out.exists())
        self.assertEqual(dt.card_files(self.card), before)
        self.assertIsNone(self.app.report_path)
        self.assertIn("DRY RUN", self.app.report_text)

    def test_retune_as_the_command_line(self):
        self.press("retune", "start")
        self.assertEqual(self.app.list["title"], "NEUE KARTE 432,0 HZ")
        self.assertIn(("NEUE KARTE: " + str(self.out), False), self.app.list["items"])
        self.assertEqual(dt.card_files(self.out), self.cli(self.base / "cli"))
        self.assertEqual(self.app.report_path, str(self.out / dt.REPORT))
        self.assertEqual(json.loads(self.settings.read_text())["runs"], {})
        # START again: the folder is the new card now, the next one goes beside it
        self.assertEqual(self.app.destination(), (str(self.base / "Deluge 432 Hz"), False))

    def test_settings_of_the_run(self):
        self.app.tick_box("keep")
        self.app.tick_box("quieter")
        self.app.turn_gold(1)
        self.press("retune", "start")
        self.assertEqual(self.app.list["title"], "NEUE KARTE 433,0 HZ")
        self.assertEqual(dt.card_files(self.out),
                         self.cli(self.base / "cli", "--tuning", "433", "--rate", "0", "--no-float"))
        self.assertEqual(json.loads(self.settings.read_text()), {
            "card": str(self.card), "out": str(self.out), "lang": "de", "mode": "retune", "tuning": 4330, "rate": 0,
            "no_float": True, "runs": {}})

    def stopped_after_one_file(self):
        convert_job = rl.convert_job

        def one(job):
            result = convert_job(job)
            self.app.stop.set()  # As ESC does: the next file isn't started
            return result

        with mock.patch.object(rl, "convert_job", one):
            self.press("retune", "start")
        self.assertEqual(self.app.list["title"], "ANGEHALTEN")
        self.assertTrue((self.out / rl.PROGRESS).exists())
        self.assertFalse((self.out / "SONGS").exists())  # The songs are written last
        self.assertEqual(json.loads(self.settings.read_text())["runs"], {str(self.out): str(self.card)})

    def test_stop_and_continue(self):
        self.stopped_after_one_file()
        self.assertEqual(self.app.destination(), (str(self.out), True))
        shown = []
        self.app.oled.text = lambda x, y, s, *a, **k: shown.append(s)
        self.app.view = "home"
        self.app.draw_oled(time.monotonic() + 60, True)
        self.assertIn("START: FORTSETZEN", shown)
        del self.app.oled.text
        self.press("start")
        self.assertEqual(self.app.list["title"], "NEUE KARTE 432,0 HZ")
        self.assertFalse((self.out / rl.PROGRESS).exists())
        self.assertEqual(dt.card_files(self.out), self.cli(self.base / "cli"))

    def test_another_card_starts_anew(self):
        self.stopped_after_one_file()
        other = dt.demo_card(self.base / "other")
        (other / "SAMPLES" / "TONE.WAV").unlink()
        self.app.card = str(other)
        folder, resume = self.app.destination()
        self.assertEqual((folder, resume), (str(self.base / "Deluge 432 Hz"), False))

    def test_not_enough_space(self):
        with mock.patch.object(dt, "free_bytes", lambda folder: 1000):
            self.press("retune", "start")
        self.assertEqual(self.app.list["title"], "ZU WENIG PLATZ")
        self.assertFalse(self.out.exists())
        self.assertEqual(json.loads(self.settings.read_text())["runs"], {})

    def test_closed_while_it_runs(self):
        closed, destroy = [], self.root.destroy
        self.root.destroy = lambda: (closed.append(self.app.busy), destroy())
        self.app.press("retune")
        self.app.press("start")
        self.assertTrue(self.app.busy)
        self.app.quit()
        self.assertTrue(self.app.closing and self.app.stop.is_set())
        self.assertEqual(closed, [])  # Not before the run has stopped
        end = time.monotonic() + 60
        while time.monotonic() < end and not closed:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(closed, [None])  # Closed once it had stopped
        self.assertFalse((self.out / dt.REPORT).exists())

    def test_tuning_knob(self):
        app = self.app
        for step, fine, tenths in ((1, False, 4330), (-1, False, 4320), (1, True, 4321), (1, False, 4330),
                                   (-1, True, 4329), (-1, False, 4320), (-3, False, 4290)):
            app.turn_gold(step, fine)
            self.assertEqual(app.tenths, tenths, (step, fine))
        app.tenths = 4153
        app.turn_gold(-1)
        self.assertEqual(app.tenths, 4153)
        app.turn_gold(1)
        self.assertEqual(app.tenths, 4160)
        app.tenths = 4660
        app.turn_gold(1)
        self.assertEqual(app.tenths, 4662)
        for typed, tenths in (("432,5", 4325), ("415.3 Hz", 4153), ("500", 4153), ("abc", 4153), ("432,55", 4153),
                              ("466,2", 4662)):
            with mock.patch.object(app.simpledialog, "askstring", lambda *a, **k: typed):
                app.type_tuning()
            self.assertEqual(app.tenths, tenths, typed)
        self.assertEqual(json.loads(self.settings.read_text())["tuning"], 4662)

    def test_language(self):
        self.app.set_lang("en")
        self.assertEqual(dt.LANG, "en")
        self.assertEqual(sorted(self.app.bound), sorted("rRuUcCoOtTdDeE"))
        self.press("read", "start")
        self.assertEqual(self.app.list["title"], "432.0 HZ: 5 AUDIO FILES")
        self.app.set_lang("de")
        self.assertEqual((dt.LANG, self.app.view, self.app.list), ("de", "home", None))
        self.assertEqual(json.loads(self.settings.read_text())["lang"], "de")

    def test_computers_language_and_the_saved_one(self):
        """The computer's language to start with; a language chosen in the window wins over it the next time."""
        import tkinter as tk

        def opened(system, saved=None):
            settings = self.base / f"settings-{system}-{saved}.json"
            if saved:
                settings.write_text(json.dumps({"lang": saved}), encoding="utf-8")
            root = tk.Tk()
            try:
                with mock.patch.object(dt, "system_language", lambda: system):
                    app = dt.App(root, str(settings), card=str(self.card))
                return app.lang, dt.LANG, sorted(app.bound)
            finally:
                root.destroy()

        de_keys, en_keys = sorted("lLuUkKzZbBdDeE"), sorted("rRuUcCoOtTdDeE")
        self.assertEqual(opened("de"), ("de", "de", de_keys))  # System German
        self.assertEqual(opened("en"), ("en", "en", en_keys))  # System English
        self.assertEqual(opened("de", saved="en"), ("en", "en", en_keys))  # Chosen in the window: it wins
        self.assertEqual(opened("en", saved="de"), ("de", "de", de_keys))
        self.app.set_lang("en")  # Chosen now, on a German computer: English the next time
        root = tk.Tk()
        try:
            with mock.patch.object(dt, "system_language", lambda: "de"):
                self.assertEqual(dt.App(root, str(self.settings), card=str(self.card)).lang, "en")
        finally:
            root.destroy()

    def test_without_a_card(self):
        self.app.card = ""
        self.press("start")
        self.assertIsNone(self.app.list)
        self.assertEqual(self.app.message, "KEINE KARTE: KARTE WÄHLEN")
        self.app.card = str(self.base / "gone")
        self.press("start")
        self.assertTrue(self.app.message.startswith("KARTE NICHT DA"))


@unittest.skipUnless(HAS_TK and HAS_SOXR and rl.pylibrb is not None,
                     "no tkinter, display, soxr or pylibrb: the self-test can't run")
class SelfTest(unittest.TestCase):
    def test_selftest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(dt.selftest(tmp), Path(tmp, "selftest.txt").read_text())
            self.assertEqual(Path(tmp, "selftest.txt").read_text().splitlines(), [
                f"version: v{dt.VERSION}", "soxr: ok", "rubberband: ok", "read: ok", "convert: ok", "processes: ok",
                "ear: ok", "resume: ok", "window: ok", "english: ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
