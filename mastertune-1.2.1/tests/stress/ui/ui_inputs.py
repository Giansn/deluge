"""The Deluge's inputs as mastertune (1.2.1) takes them, for tests that drive the UI in the emulator: a pad pressed or
released (MatrixDriver::padAction()), a button (Buttons::buttonAction()), an encoder turned (its count of detents or
ticks, which the encoder task takes); a test card with make_sd.py's song, three more songs and the synth presets; the
boot. Used by tests/v1904; the beta's fuzzer (branch nightly, beta-1.3/tests/fuzz_ui.py) has the same for both
firmwares."""
import os
import struct
import subprocess

import stress_ui_emu as su

TESTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def bxy(x, y):  # hid/button.h fromXY()
    return 9 * (y + 16) + x


B = dict(SHIFT=bxy(8, 0), PLAY=bxy(8, 3), RECORD=bxy(8, 2), BACK=bxy(7, 1), LOAD=bxy(6, 1), SAVE=bxy(6, 3),
         LEARN=bxy(7, 0), TAP=bxy(7, 3), SYNC=bxy(7, 2), CROSS=bxy(6, 2), SCALE=bxy(6, 0), TRIPLETS=bxy(8, 1),
         SONG=bxy(3, 1), CLIP=bxy(3, 2), KEYBOARD=bxy(3, 3), AFFECT=bxy(3, 0), SYNTH=bxy(5, 0), KIT=bxy(5, 1),
         MIDI=bxy(5, 2), CV=bxy(5, 3), SELECT_ENC=bxy(4, 3), TEMPO_ENC=bxy(4, 1), X_ENC=bxy(0, 1), Y_ENC=bxy(0, 0),
         MOD_ENC0=bxy(0, 2), MOD_ENC1=bxy(0, 3),
         **{f"MOD{i}": bxy(x, y) for i, (x, y) in enumerate(zip((1, 1, 1, 1, 2, 2, 2, 2), (0, 1, 2, 3, 0, 1, 2, 3)))})
ENCODERS = ["scrollY", "scrollX", "tempo", "select", "mod1", "mod0"]


class Inputs:
    def __init__(self, rig):
        self.rig = rig
        sym = rig.emu.sym
        self.button_fn = sym.find("_ZN7Buttons12buttonActionEhbb")
        self.pad_fn = sym.find("_ZN12MatrixDriver9padActionElll")  # A clone without this: (x, y, velocity)
        # std::array<Encoder, 6> encoders (14 bytes each: encPos, then detentPos); SCROLL_Y, SCROLL_X, TEMPO, SELECT
        # (their full detents: detentPos), MOD_1, MOD_0 (non-detent: encPos)
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
        emu.uc.mem_write(address, struct.pack(fmt, max(-128, min(127, v))))


def make_card(image, synths, template):
    """make_sd.py's song as SONGS/DEFAULT.XML and as SONG001 to SONG003, its samples, and the synth presets of
    presets/SYNTHS in SYNTHS; built once, copied for every boot."""
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


def boot(elf, tools, build, image):
    """The firmware booted with the card, its startup song loaded (tests/stress/ui's Rig, OLED)."""
    return su.Rig(elf, tools, build, image, "1000,42.67")
