#!/usr/bin/env python3
"""A random user on the real firmware in the emulator, to find crashes: the firmware boots (OLED or 7-segment), loads
its startup song, and then, step by step, a random input as the hardware delivers it: a pad pressed and released
(MatrixDriver::padAction()), a button (Buttons::buttonAction(): SHIFT combinations, a long BACK, the encoder buttons),
an encoder turned (its count of detents or ticks, which the firmware's encoder task takes), PLAY. Between the inputs
the firmware's own task manager runs 20-150 ms (the audio in real time, the UI, the card). The same seed gives the same
inputs on both firmwares:
  --firmware v13    the community beta (upstream main), rig13.Rig13 (the startup song loaded by its own task)
  --firmware 121    mastertune (1.2.1-based), tests/stress/ui's Rig
Recorded per run: a crash (an emulator error: a write through a null pointer, an exception), freezeWithError() (the
E/i codes; in a beta build every FREEZE_WITH_ERROR), the fault handlers, a hang (an input or a slice of the task
manager not back within its limit), wild accesses outside RAM and the peripherals, error popups, the heap now and
then. After a problem: the last inputs are kept, the Deluge boots again and the run goes on with the next seed.

Usage: fuzz_ui.py <deluge.elf> --firmware v13|121 --tools PREFIX --build DIR --out DIR [--steps N] [--seed S]
                  [--synths N] [--7seg] [--minutes M]
Results: <out>/fuzz.json (problems with their last inputs, heap samples, counts) and a summary on stdout."""
import argparse
import collections
import json
import os
import random
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(TESTS, "stress", "ui"))
sys.path.insert(0, os.path.join(TESTS, "song"))


def bxy(x, y):  # hid/button.h fromXY(), the same in 1.2.1 and v1.3
    return 9 * (y + 16) + x


B = dict(SHIFT=bxy(8, 0), PLAY=bxy(8, 3), RECORD=bxy(8, 2), BACK=bxy(7, 1), LOAD=bxy(6, 1), SAVE=bxy(6, 3),
         LEARN=bxy(7, 0), TAP=bxy(7, 3), SYNC=bxy(7, 2), CROSS=bxy(6, 2), SCALE=bxy(6, 0), TRIPLETS=bxy(8, 1),
         SONG=bxy(3, 1), CLIP=bxy(3, 2), KEYBOARD=bxy(3, 3), AFFECT=bxy(3, 0), SYNTH=bxy(5, 0), KIT=bxy(5, 1),
         MIDI=bxy(5, 2), CV=bxy(5, 3), SELECT_ENC=bxy(4, 3), TEMPO_ENC=bxy(4, 1), X_ENC=bxy(0, 1), Y_ENC=bxy(0, 0),
         MOD_ENC0=bxy(0, 2), MOD_ENC1=bxy(0, 3),
         **{f"MOD{i}": bxy(x, y) for i, (x, y) in enumerate(zip((1, 1, 1, 1, 2, 2, 2, 2), (0, 1, 2, 3, 0, 1, 2, 3)))})
# Rarely: SAVE and LOAD open their browsers, RECORD records; the rest often
BUTTON_WEIGHTS = {n: (1 if n in ("SAVE", "LOAD", "RECORD", "LEARN", "SYNC", "TAP") else 4) for n in B if n != "SHIFT"}
ENCODERS = ["scrollY", "scrollX", "tempo", "select", "mod1", "mod0"]


class Inputs:
    """The hardware's inputs as the firmware takes them, for either firmware."""

    def __init__(self, rig, firmware):
        self.rig, self.firmware = rig, firmware
        emu = rig.emu
        sym = emu.sym
        self.button_fn = sym.find("_ZN7Buttons12buttonActionEhbb")
        self.pad_fn = sym.find("_ZN12MatrixDriver9padActionElll")  # A clone without this: (x, y, velocity)
        if firmware == "v13":
            # deluge::hid::encoders::scrollY etc.: DetentedEncoder (edgeAccumulator, then the detents: an atomic int32
            # at +4), ContinuousEncoder for the gold ones (the ticks: an atomic int8 at +0)
            names = {"scrollY": "7scrollY", "scrollX": "7scrollX", "tempo": "5tempo", "select": "6select",
                     "mod1": "4mod1", "mod0": "4mod0"}
            self.enc = {k: (sym[f"_ZN6deluge3hid8encoders{v}E"] + (0 if k.startswith("mod") else 4),
                            "<b" if k.startswith("mod") else "<i") for k, v in names.items()}
        else:
            # 1.2.1: std::array<Encoder, 6> encoders (14 bytes each: encPos, then detentPos); SCROLL_Y, SCROLL_X,
            # TEMPO, SELECT (their full detents: detentPos), MOD_1, MOD_0 (non-detent: encPos)
            base = sym["_ZN6deluge3hid8encoders8encodersE"]
            self.enc = {k: (base + 14 * i + (0 if i >= 4 else 1), "<b") for i, k in enumerate(ENCODERS)}

    def button(self, name, on):
        self.rig.action(f"{name} {'on' if on else 'off'}", self.button_fn, B[name], 1 if on else 0, 0)

    def pad(self, x, y, velocity):
        self.rig.action(f"pad {x},{y} {velocity}", self.pad_fn, x, y, velocity)

    def turn(self, name, n):
        address, fmt = self.enc[name]
        emu = self.rig.emu
        size = struct.calcsize(fmt)
        v = struct.unpack(fmt, emu.uc.mem_read(address, size))[0] + n
        lo, hi = (-128, 127) if size == 1 else (-(1 << 31), (1 << 31) - 1)
        emu.uc.mem_write(address, struct.pack(fmt, max(lo, min(hi, v))))


def make_card(image, synths, template):
    """make_sd.py's song as SONGS/DEFAULT.XML and as SONG001 to SONG003 (song loads, numeric names), its samples, and
    the synth presets of presets/SYNTHS in SYNTHS (preset loads); built once, copied for every boot."""
    if not os.path.exists(template):
        import fat32
        import make_sd
        files, lengths = make_sd.samples()
        xml = make_sd.song_xml(lengths, 1, synths, False, False, False).encode()
        files["SONGS/DEFAULT.XML"] = xml
        for i in (1, 2, 3):
            files[f"SONGS/SONG{i:03d}.XML"] = xml
        presets = os.path.join(TESTS, "..", "presets", "SYNTHS")
        for name in sorted(os.listdir(presets)):
            files[f"SYNTHS/{name}"] = open(os.path.join(presets, name), "rb").read()
        fat32.build(template, files)
    subprocess.run(["cp", "--sparse=always", template, image], check=True)


class NullPage:
    """Writes through a null pointer: the Deluge's MMU maps 0x00000000 (the empty CS0 area) as normal cacheable memory,
    so such a write doesn't fault there, it just goes nowhere (ttb_init.S; the vectors are elsewhere, VBAR). The
    emulator maps that page read-only (song_emu), which would count it as a crash: here it is writable and checked
    after every input instead; what was written is recorded with the input and cleared again."""

    def __init__(self, emu, size=0x10000):
        from unicorn import UC_PROT_ALL
        self.emu, self.size = emu, size
        emu.uc.mem_protect(0, 0x100000, UC_PROT_ALL)
        self.zero = bytes(size)
        self.writes = []

    def check(self, what):
        data = bytes(self.emu.uc.mem_read(0, self.size))
        if data != self.zero:
            at = [i for i in range(self.size) if data[i]][:8]
            self.writes.append(dict(during=what, offsets=at))
            self.emu.uc.mem_write(0, self.zero)


def boot(firmware, elf, tools, build, image, oled):
    if firmware == "v13":
        import rig13
        rig = rig13.Rig13(elf, tools, build, image, sd_latency="1000,42.67", oled=oled)
        rig.load_startup_song()
    else:
        import stress_ui_emu as su
        rig = su.Rig(elf, tools, build, image, "1000,42.67")
        if not oled:
            raise SystemExit("--7seg: only with --firmware v13 for now")
    return rig


def step(rng, inp, rig, log):
    """One random input; returns its description."""
    r = rng.random()
    tm = lambda s, what: rig.tm(s, what)  # noqa: E731
    if r < 0.30:  # A pad: the grid or the two sidebar columns
        x, y = rng.randrange(18), rng.randrange(8)
        inp.pad(x, y, rng.choice((64, 100, 127)))
        tm(rng.uniform(0.02, 0.15), "pad held")
        inp.pad(x, y, 0)
        d = f"pad {x},{y}"
    elif r < 0.55:  # A button, a quarter of them with SHIFT held
        name = rng.choices(list(BUTTON_WEIGHTS), weights=list(BUTTON_WEIGHTS.values()))[0]
        shift = rng.random() < 0.25
        if shift:
            inp.button("SHIFT", True)
        inp.button(name, True)
        tm(rng.uniform(0.02, 0.1), "button held")
        inp.button(name, False)
        if shift:
            inp.button("SHIFT", False)
        d = ("SHIFT+" if shift else "") + name
    elif r < 0.62:  # SHIFT + a pad: the sound editor's shortcuts
        x, y = rng.randrange(16), rng.randrange(8)
        inp.button("SHIFT", True)
        inp.pad(x, y, 100)
        tm(0.03, "shortcut")
        inp.pad(x, y, 0)
        inp.button("SHIFT", False)
        d = f"SHIFT+pad {x},{y}"
    elif r < 0.85:  # An encoder turned
        name = rng.choice(ENCODERS)
        n = rng.choice((-3, -2, -1, -1, 1, 1, 2, 3))
        inp.turn(name, n)
        d = f"turn {name} {n:+d}"
    elif r < 0.90:  # A long BACK (exit all)
        inp.button("BACK", True)
        tm(0.8, "BACK held")
        inp.button("BACK", False)
        d = "long BACK"
    elif r < 0.95:  # A pad held while an encoder turns (e.g. a note's velocity, a clip's length)
        x, y = rng.randrange(16), rng.randrange(8)
        name = rng.choice(("scrollX", "scrollY", "select", "mod0", "mod1"))
        n = rng.choice((-2, -1, 1, 2))
        inp.pad(x, y, 100)
        tm(0.03, "pad held")
        inp.turn(name, n)
        tm(0.05, "pad held, turned")
        inp.pad(x, y, 0)
        d = f"pad {x},{y} + turn {name} {n:+d}"
    else:
        inp.button("PLAY", True)
        inp.button("PLAY", False)
        d = "PLAY"
    tm(rng.uniform(0.02, 0.15), "between inputs")
    return d


def step_browser(rng, inp, rig, log):
    """--mode browser: the song browser (LOAD) only, as in #4846 (a crash scrolling with <> on a numeric song name,
    7-segment display): the horizontal encoder (with and without SHIFT, which edits the name's number), the select
    encoder, now and then BACK and LOAD again, rarely the select encoder pressed (loads the song)."""
    tm = lambda s, what: rig.tm(s, what)  # noqa: E731
    r = rng.random()
    if r < 0.45:
        n = rng.choice((-3, -2, -1, -1, 1, 1, 2, 3))
        inp.turn("scrollX", n)
        d = f"turn scrollX {n:+d}"
    elif r < 0.60:
        n = rng.choice((-2, -1, 1, 2))
        inp.button("SHIFT", True)
        inp.turn("scrollX", n)
        tm(0.03, "SHIFT held")
        inp.button("SHIFT", False)
        d = f"SHIFT+turn scrollX {n:+d}"
    elif r < 0.85:
        n = rng.choice((-2, -1, 1, 2))
        inp.turn("select", n)
        d = f"turn select {n:+d}"
    elif r < 0.93:
        inp.button("BACK", True)
        inp.button("BACK", False)
        tm(0.2, "after BACK")
        inp.button("LOAD", True)
        inp.button("LOAD", False)
        d = "BACK, LOAD"
    elif r < 0.97:
        inp.button("X_ENC", True)
        tm(0.03, "<> pressed")
        inp.button("X_ENC", False)
        d = "<> pressed"
    else:
        inp.button("SELECT_ENC", True)
        inp.button("SELECT_ENC", False)
        tm(0.5, "loading")
        inp.button("LOAD", True)
        inp.button("LOAD", False)
        d = "SELECT (load), LOAD"
    tm(rng.uniform(0.02, 0.12), "between inputs")
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--firmware", choices=("v13", "121"), required=True)
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=1500, help="inputs in all (over every boot)")
    ap.add_argument("--minutes", type=float, default=0, help="stop after this much host time (0: no limit)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--synths", type=int, default=2)
    ap.add_argument("--7seg", dest="seven", action="store_true")
    ap.add_argument("--mode", choices=("all", "browser"), default="all")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    import stress_ui_emu as su
    t0 = time.time()
    result = dict(firmware=a.firmware, elf=os.path.basename(a.elf), seed=a.seed, steps=0, boots=0, problems=[],
                  heap=[], invalid=[], error_popups=[])
    seed = a.seed
    done = 0
    while done < a.steps and not (a.minutes and time.time() - t0 > a.minutes * 60):
        image = os.path.join(a.out, f"sd-{a.firmware}.img")
        if os.path.exists(image):
            os.remove(image)  # A fresh card each boot (a crash may leave it as it was mid-write)
        make_card(image, a.synths, os.path.join(a.out, "card-template.img"))
        rng = random.Random(seed)
        recent = collections.deque(maxlen=80)
        try:
            rig = boot(a.firmware, a.elf, a.tools, a.build, image, not a.seven)
        except (su.Stop, SystemExit) as ex:
            result["problems"].append(dict(seed=seed, step=0, kind="boot", detail=str(ex),
                                           problems=getattr(ex, "problems", None)))
            break
        result["boots"] += 1
        inp = Inputs(rig, a.firmware)
        null = NullPage(rig.emu)
        if a.mode == "browser":
            inp.button("LOAD", True)
            inp.button("LOAD", False)
            rig.tm(0.3, "browser opens")
        n = 0
        try:
            while done < a.steps and not (a.minutes and time.time() - t0 > a.minutes * 60):
                d = (step_browser if a.mode == "browser" else step)(rng, inp, rig, print)
                recent.append(f"{rig.emu.seconds():.2f}s {d}")
                null.check(d)
                n += 1
                done += 1
                if done % 100 == 0:
                    h = rig.heap()
                    result["heap"].append(dict(step=done, seed=seed, at_s=round(rig.emu.seconds(), 1), **h))
                    print(f"[{a.firmware}] {done} inputs, {time.time() - t0:.0f} s, ui {rig.ui_name()}, heap sdram "
                          f"{h['sdram_free'] // 1024} KB free", flush=True)
        except su.Stop:
            p = rig.problems[-1] if rig.problems else {}
            try:
                ui = rig.ui_name()
            except Exception:  # noqa: BLE001
                ui = "?"
            result["problems"].append(dict(seed=seed, step=n, ui=ui, **p, last_inputs=list(recent)))
            print(f"[{a.firmware}] PROBLEM after {n} inputs (seed {seed}): {p.get('kind')} {p.get('detail')}"
                  f" during {p.get('what')}", flush=True)
        result.setdefault("null_writes", []).extend(dict(seed=seed, **w) for w in null.writes[:20])
        result["invalid"] += [dict(seed=seed, page=hex(k[0]), at=k[1], during=k[2], n=c)
                              for k, c in rig.invalid.most_common(10)]
        # Error 10 (FOLDER_DOESNT_EXIST: no KITS folder on the card) left out
        result["error_popups"] += [dict(seed=seed, popup=p) for p in rig.error_popups()
                                   if not (p[2] == "displayError" and p[3] == "Error 10")][:10]
        result["steps"] = done
        seed += 1000
        json.dump(result, open(os.path.join(a.out, "fuzz.json"), "w"), indent=1, default=str)
    result["host_s"] = round(time.time() - t0)
    json.dump(result, open(os.path.join(a.out, "fuzz.json"), "w"), indent=1, default=str)
    print(json.dumps(dict(firmware=a.firmware, steps=result["steps"], boots=result["boots"],
                          problems=[(p["seed"], p["step"], p.get("kind"), p.get("detail"), p.get("what"))
                                    for p in result["problems"]],
                          invalid=len(result["invalid"]), null_writes=len(result.get("null_writes", [])),
                          error_popups=result["error_popups"][:5]),
                     indent=1, default=str))


if __name__ == "__main__":
    main()
