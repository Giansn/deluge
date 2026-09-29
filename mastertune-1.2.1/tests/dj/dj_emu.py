#!/usr/bin/env python3
"""The DJ tool's first step (mastertune-v19: the DJ filter, sync to the input, the nudge) on the real firmware in the
emulator, driven as the user would: tests/scan's rig (tests/stress/ui's Rig: booted with the OLED, make_sd.py's song
loaded, the firmware's own task manager), the buttons as Buttons::buttonAction() gets them, the gold knobs as
interpretEncoders() hands them to Song view (SessionView::modEncoderAction(), modEncoderButtonAction()), the tempo
encoder as the hardware leaves it (a detent in the encoder's detentPos, which interpretEncoders() reads).

The song's params are read where the firmware keeps them: currentSong's ParamManager, summaries[0] (the unpatched set),
params[id].currentValue (offsets from the ELF's debug info, the toolchain's gdb). A knob position k is the value k << 25
(+64: the maximum, off for the LPF; -64: the minimum, off for the HPF and the resonance).

Checks:
1. boot, Song view
2. the DJ filter: AFFECT ENTIRE on, the upper gold knob pressed three times: HPF, EQ, then DJ (the song's filter type 3,
   the popup "DJ"); the upper knob turned 20 detents left: the LPF's cutoff at knob 24, the HPF off, "DJ: LPF 16";
   40 right: the LPF open, the HPF's cutoff at knob -24, "DJ: HPF 16"; 20 left: both off, "DJ: OFF" (from the middle,
   where the knob is taken first); the lower knob 10 right: both resonances 10 up; pressed once more: back to LPF.
   Set apart: the LPF's cutoff at knob 32 (LPF mode), the HPF's at -32 and its resonance 20 up (HPF mode), then DJ
   again: a detent right opens the LPF by 2 only, "DJ: HPF 1"; the lower knob a detent right: each resonance 1 up,
   "Resonance: 12" (the HPF's, the one on); 20 left: the HPF closes first (16), then the LPF (4): LPF 26, "DJ: LPF 15".
   No jump anywhere; pressed once more: LPF
3. sync: the Scan view (Settings > Tuning > Scan), no tempo heard: TAP TEMPO shows "No tempo yet" and leaves the
   tempo; the analysis' BPM set to 128.00 (as it would read it): TAP TEMPO, "Sync 128.0 BPM", the song at 128.00 BPM;
   BACK
4. nudge: playing (the internal clock, no MIDI clock out), the horizontal encoder held and the tempo encoder a detent
   right: the time per timer tick 4 % shorter ("Sync nudged"); 0.3 s later exactly what it was, and the beat 10 ms
   ahead (441 samples, +-10 %) of where the ticks before it lead; a detent left: 4 % longer, then back, the beat as
   far behind; a nudge, then STOP at once: the tempo back
Throughout: no crash, fault, hang, error popup or access outside RAM and the peripherals.

Usage: dj_emu.py <deluge.elf> [--out DIR] [--tools PREFIX] [--build DIR] [--shots DIR]
--shots also saves the OLED at the moments the DJ manual shows (docs/dj-manual.html) as <DIR>/oled_dj_*.png
and .txt, and in the Scan view, for them only, the analysis' result set to 128 BPM and A minor.
Needs python3 with unicorn 2 and numpy, a C compiler (blockcount.c), the toolchain's gdb. About 2 minutes.
Exit status 0 when all checks pass.
"""
import argparse
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(TESTS, "scan"))
import scan_view_emu as sv  # noqa: E402  (its rig, menu and the other tests' modules)

ui, se, screens, make_sd, fat32 = sv.ui, sv.se, sv.screens, sv.make_sd, sv.fat32

AFFECT_ENTIRE, X_ENC, TAP_TEMPO = ui.button_xy(3, 0), ui.button_xy(0, 1), ui.button_xy(7, 3)
ENCODERS, TEMPO_ENCODER, ENCODER_SIZE = "_ZN6deluge3hid8encoders8encodersE", 2, 14  # detentPos at 1
MOD_TURN, MOD_PRESS = "_ZN11SessionView16modEncoderActionEll", "_ZN11SessionView22modEncoderButtonActionEhb"
LPF_FREQ, LPF_RES, HPF_FREQ, HPF_RES = 32, 33, 35, 36  # params::UnpatchedGlobal (checked against gdb below)

log, check = sv.log, sv.check


def s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


class DjRig(sv.ScanRig):
    def __init__(self, a, image):
        super().__init__(a, image)
        emu = self.emu
        (self.pm_off, self.tempo_off, self.filter_type_off, self.magnitude_off, self.nudging_off, self.tick_off,
         self.tick_time_off, self.summaries_off, self.params_off, self.autoparam_size, self.current_off,
         lpf, hpf) = se.gdb_values(emu, [
             "(int)&currentSong->paramManager - (int)currentSong",
             "(int)&currentSong->timePerTimerTickBig - (int)currentSong",
             "(int)&currentSong->globalEffectable.currentFilterType - (int)currentSong",
             "(int)&currentSong->insideWorldTickMagnitude - (int)currentSong",
             "(int)&playbackHandler.clockNudging - (int)&playbackHandler",
             "(int)&playbackHandler.lastTimerTickActioned - (int)&playbackHandler",
             "(int)&playbackHandler.timeLastTimerTickBig - (int)&playbackHandler",
             "(int)&((ParamManager*)0)->summaries", "(int)&((ParamSet*)0)->params", "sizeof(AutoParam)",
             "(int)&((AutoParam*)0)->currentValue", "(int)'deluge::modulation::params::UNPATCHED_LPF_FREQ'",
             "(int)'deluge::modulation::params::UNPATCHED_HPF_FREQ'"])
        if (lpf, hpf) != (LPF_FREQ, HPF_FREQ):
            raise SystemExit(f"the params' numbers changed: LPF_FREQ {lpf}, HPF_FREQ {hpf}")
        self.handler = self.sym["playbackHandler"]

    def song(self):
        return self.emu.u32(self.sym["currentSong"])

    def param(self, i):
        """The song's unpatched param i: its current value."""
        params = self.emu.u32(self.song() + self.pm_off + self.summaries_off)  # summaries[0].paramCollection
        array = self.emu.u32(params + self.params_off)  # ParamSet::params, an AutoParam*
        return s32(self.emu.u32(array + i * self.autoparam_size + self.current_off))

    def filter_type(self):
        return self.emu.u8(self.song() + self.filter_type_off)

    def tick_time(self):
        """The song's time per timer tick, 32.32 samples."""
        return struct.unpack("<Q", self.emu.uc.mem_read(self.song() + self.tempo_off, 8))[0]

    def bpm(self):
        magnitude = struct.unpack("<i", self.emu.uc.mem_read(self.song() + self.magnitude_off, 4))[0]
        return 110250 / (self.tick_time() / 2 ** 32) / 2 ** magnitude

    def nudging(self):
        return self.emu.u8(self.handler + self.nudging_off)

    def last_tick(self):
        """(the timer tick last actioned, its time in samples)."""
        tick = self.emu.u32(self.handler + self.tick_off)
        at = struct.unpack("<Q", self.emu.uc.mem_read(self.handler + self.tick_time_off, 8))[0] >> 32
        return tick, at

    def knob(self, which, offset, detents=1):
        for _ in range(detents):
            self.rig.action(f"gold knob {which} {offset:+d}", self.sym[MOD_TURN], self.sym["sessionView"], which, offset)

    def knob_press(self, which):
        for on in (1, 0):
            self.rig.action(f"gold knob {which} {'pressed' if on else 'released'}", self.sym[MOD_PRESS],
                            self.sym["sessionView"], which, on)

    def tempo_detent(self, offset):
        """A detent of the tempo encoder, as the hardware leaves it for interpretEncoders()."""
        self.emu.uc.mem_write(self.sym[ENCODERS] + ENCODER_SIZE * TEMPO_ENCODER + 1, struct.pack("<b", offset))
        self.rig.tm(0.02)

    def popups_since(self, n):
        return [p[3] for p in self.rig.popups[n:]]


SHOT = None  # --shots: a screens.Oled
PPR6, LINE_IN_DETECT_BIT = 0xFCFE3218, 1 << 6  # Port 6's pin levels; LINE_IN_DETECT is P6_6


def snap(s, name, settle_s=0.05):
    """--shots: the OLED as the firmware last sent it (with its popup), as <shots>/oled_dj_<name>.png and .txt."""
    if SHOT:
        s.rig.tm(settle_s)
        SHOT.save_oled(f"dj_{name}")


def knob_value(k):
    return (1 << 31) - 1 if k >= 64 else k << 25


def dj_filter(s):
    rig = s.rig
    log("== 2. the DJ filter")
    n = len(rig.popups)
    rig.button(AFFECT_ENTIRE, True)
    rig.button(AFFECT_ENTIRE, False)
    for _ in range(3):
        s.knob_press(1)
    popups = s.popups_since(n)
    check("the upper gold knob pressed three times: HPF, EQ, DJ; the song's filter type DJ (3)",
          popups[-3:] == ["HPF", "EQ", "DJ"] and s.filter_type() == 3, f"popups {popups}, type {s.filter_type()}")
    snap(s, "type")

    def state(what, detents, offset, lpf, hpf, popup):
        n = len(rig.popups)
        s.knob(1, offset, detents)
        got = (s.param(LPF_FREQ), s.param(HPF_FREQ))
        last = s.popups_since(n)[-1:] or [None]
        check(f"{what}: the LPF at knob {lpf}, the HPF at knob {hpf}, the popup {popup!r}",
              got == (knob_value(lpf), knob_value(hpf)) and last[0] == popup,
              f"LPF {got[0]} (knob {got[0] / 2 ** 25:.2f}), HPF {got[1]} (knob {got[1] / 2 ** 25:.2f}), popup {last[0]!r}")

    # From where the song's LPF and HPF are (dsp/dj/dj_filter.h's fromFilterKnobs()) to the middle first
    lpf0, hpf0 = (round(s.param(LPF_FREQ) / 2 ** 25), round(s.param(HPF_FREQ) / 2 ** 25))
    pos0 = max(-64, min(64, int((max(-64, min(64, lpf0)) - 64) / 2) + int((max(-64, min(64, hpf0)) + 64) / 2)))
    if pos0:
        state(f"from the song's LPF knob {lpf0} and HPF knob {hpf0} (DJ {pos0:+d}) to the middle", abs(pos0),
              -1 if pos0 > 0 else 1, 64, -64, "DJ: OFF")
    state("20 detents left", 20, -1, 24, -64, "DJ: LPF 16")
    snap(s, "lpf16")
    state("40 right", 40, 1, 64, -24, "DJ: HPF 16")
    snap(s, "hpf16")
    state("20 left, the middle", 20, -1, 64, -64, "DJ: OFF")
    snap(s, "off")
    def resonance(what, detents, want, popup=None):
        n = len(rig.popups)
        s.knob(0, 1, detents)
        got = (s.param(LPF_RES), s.param(HPF_RES))
        last = s.popups_since(n)[-1:] or [None]
        check(f"{what}: the resonances at knob {want[0]} (LPF) and {want[1]} (HPF)"
              + (f", the popup {popup!r}" if popup else ""),
              got == tuple(knob_value(w) for w in want) and (popup is None or last[0] == popup),
              f"LPF_RES {got[0] / 2 ** 25:.2f}, HPF_RES {got[1] / 2 ** 25:.2f}, popup {last[0]!r}")

    def press(what, want, filter_type):
        n = len(rig.popups)
        s.knob_press(1)
        check(what, s.popups_since(n)[-1:] == [want] and s.filter_type() == filter_type,
              f"popups {s.popups_since(n)}, type {s.filter_type()}")

    res0 = [max(-64, min(64, round(s.param(p) / 2 ** 25))) for p in (LPF_RES, HPF_RES)]
    res1 = [min(64, r + 10) for r in res0]
    resonance(f"the lower knob 10 right (from {res0[0]} and {res0[1]})", 10, res1)
    snap(s, "resonance")
    press("pressed once more: LPF", "LPF", 0)

    # Set apart as LPF and HPF mode set them (a band-pass), then DJ again: it goes on from there
    s.knob(1, -1, 32)
    press("pressed: HPF", "HPF", 1)
    s.knob(1, 1, 32)
    s.knob(0, 1, 20)
    check("set apart in LPF and HPF mode: the LPF at knob 32, the HPF at -32, its resonance 20 up",
          (s.param(LPF_FREQ), s.param(HPF_FREQ), s.param(HPF_RES)) ==
          (knob_value(32), knob_value(-32), knob_value(min(64, res1[1] + 20))),
          f"LPF {s.param(LPF_FREQ) / 2 ** 25:.2f}, HPF {s.param(HPF_FREQ) / 2 ** 25:.2f}, "
          f"HPF_RES {s.param(HPF_RES) / 2 ** 25:.2f}")
    s.knob_press(1)
    press("pressed twice: EQ, DJ", "DJ", 3)
    state("set apart, a detent right: the LPF opens by 2 only", 1, 1, 34, -32, "DJ: HPF 1")
    want = [min(64, res1[0] + 1), min(64, res1[1] + 21)]
    resonance("the lower knob a detent right: each 1 up, the HPF's shown", 1, want,
              f"Resonance: {(want[1] + 64) * 50 // 128}")
    state("20 left: the HPF closes first (16), then the LPF (4)", 20, -1, 26, -64, "DJ: LPF 15")
    press("pressed once more: LPF", "LPF", 0)


def sync(s):
    rig, emu, sym = s.rig, s.emu, s.sym
    log("== 3. sync: the Scan view, TAP TEMPO")
    if SHOT:  # A jack in the line input for the shots (its detect pin, P6_6, and the flag inputRoutine() keeps)
        ppr6 = int.from_bytes(emu.uc.mem_read(PPR6, 2), "little") | LINE_IN_DETECT_BIT
        emu.uc.mem_write(PPR6, ppr6.to_bytes(2, "little"))
        emu.uc.mem_write(sym[sv.LINE_IN], b"\x01")
    menu = screens.Settings(rig)
    menu.open()
    menu.enter("tuningSubmenu")
    submenu, item = menu.item(), sym["scanViewMenu"]
    for _ in range(4):
        if emu.u32(emu.u32(submenu + menu.cur)) == item:
            break
        menu.turn(1)
    menu.press(ui.SELECT_ENC)
    engine = s.scan()[0]
    check("the Scan view open", rig.ui_name(root=True) == "scanView" and engine, f"root {rig.ui_name(root=True)}")
    if not engine:
        return
    before = s.bpm()
    emu.uc.mem_write(engine + s.bpm_off, struct.pack("<f", 0.0))
    n = len(rig.popups)
    s.press(TAP_TEMPO, 0.02)
    check("no tempo heard: TAP TEMPO shows 'No tempo yet', the tempo stays",
          "No tempo yet" in s.popups_since(n) and abs(s.bpm() - before) < 1e-6,
          f"popups {s.popups_since(n)}, {before:.3f} -> {s.bpm():.3f} BPM")
    snap(s, "sync_none")
    emu.uc.mem_write(engine + s.bpm_off, struct.pack("<f", 128.0))
    n = len(rig.popups)
    s.press(TAP_TEMPO, 0.02)
    check("128.00 BPM heard: TAP TEMPO, 'Sync 128.0 BPM', the song at 128.00 BPM",
          "Sync 128.0 BPM" in s.popups_since(n) and abs(s.bpm() - 128.0) < 0.005,
          f"popups {s.popups_since(n)}, the song at {s.bpm():.4f} BPM")
    if SHOT:
        # The Scan view as the manual shows it: 128 BPM and A minor heard, set as the analysis would. The select
        # encoder pressed: the big line Hz, BPM, then the key. The popup up, silence meanwhile forgets the tempo: it is
        # set again once the popup is gone (with fewer than 4 s of onset values in, which it keeps) and the view drawn
        # anew. Then TAP TEMPO once more, over the key and the tempo heard

        def heard():
            emu.uc.mem_write(engine + s.onsets_off, struct.pack("<I", 0))
            emu.uc.mem_write(engine + s.bpm_off, struct.pack("<f", 128.0))
            emu.uc.mem_write(engine + s.tonic_off, struct.pack("<i", 9))
            emu.uc.mem_write(engine + s.minor_off, b"\x01")

        for name in ("hz", "bpm", "key"):
            heard()
            s.press(ui.SELECT_ENC, 0.05)
            if name != "hz":
                screens.quiet(rig)
                heard()
                rig.action("the Scan view drawn anew", sym["_ZN8ScanView13focusRegainedEv"], s.view)
                snap(s, f"scan_{name}")
                log(f"  shot scan_{name}: the analysis' tempo {s.f32(engine + s.bpm_off):g} BPM")
        heard()
        s.press(TAP_TEMPO, 0.02)
        snap(s, "sync_128")
    s.press(ui.BACK, 0.3)
    check("BACK: Song view", rig.ui_name(root=True) == "sessionView", f"root {rig.ui_name(root=True)}")


def nudge(s):
    rig = s.rig
    log("== 4. nudge: the horizontal encoder held, the tempo encoder turned")
    rig.play()
    rig.tm(0.5)
    base = s.tick_time()
    period = base / 2 ** 32
    for offset, word in ((1, "shorter"), (-1, "longer")):
        tick0, at0 = s.last_tick()
        n = len(rig.popups)
        rig.button(X_ENC, True)
        s.tempo_detent(offset)
        if offset > 0:
            snap(s, "nudged")  # While the encoder is held: releasing it takes the popup away
        rig.button(X_ENC, False)
        bent = s.tick_time()
        bend = base // 100 * 4
        expected = base - bend if offset > 0 else base + bend
        check(f"a detent {'right' if offset > 0 else 'left'}: the time per timer tick 4 % {word}, 'Sync nudged'",
              bent == expected and s.nudging() and "Sync nudged" in s.popups_since(n),
              f"{base / 2 ** 32:.4f} -> {bent / 2 ** 32:.4f} samples a tick, nudging {s.nudging()}, "
              f"popups {s.popups_since(n)}")
        rig.tm(0.3)
        tick1, at1 = s.last_tick()
        ahead = at0 + (tick1 - tick0) * period - at1  # Where the ticks before would have led, less where it is
        check(f"0.3 s later: the tempo exactly as before, the beat {abs(ahead):.0f} samples "
              f"{'ahead' if offset > 0 else 'behind'} (441 +-10 %)",
              s.tick_time() == base and not s.nudging() and abs(ahead - 441 * offset) <= 44,
              f"{s.tick_time() / 2 ** 32:.4f} samples a tick, nudging {s.nudging()}, ticks {tick0} -> {tick1}, "
              f"{ahead:+.1f} samples")
    rig.button(X_ENC, True)
    s.tempo_detent(1)
    rig.button(X_ENC, False)
    bent = s.tick_time()
    rig.action("PLAY (stop)", rig.a["_ZN15PlaybackHandler17playButtonPressedEl"], 0)
    check("a nudge, then STOP at once: the tempo back", bent != base and s.tick_time() == base and not s.nudging(),
          f"{bent / 2 ** 32:.4f} -> {s.tick_time() / 2 ** 32:.4f} samples a tick, playing {rig.playing()}")


def run(a, out):
    image = os.path.join(out, "dj.img")
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 2).encode()
    fat32.build(image, files)
    log("== 1. boot with the OLED, the song loaded")
    try:
        s = DjRig(a, image)
    except ui.Stop as e:
        check("boot and the song loaded", False, f"{e}")
        return
    rig = s.rig
    if a.shots:
        global SHOT
        os.makedirs(a.shots, exist_ok=True)
        SHOT = screens.Oled(s.emu, os.path.abspath(a.shots))
    try:
        rig.tm(0.5)
        check("Song view", rig.ui_name(root=True) == "sessionView", f"{rig.ui_name(root=True)}")
        dj_filter(s)
        sync(s)
        nudge(s)
    except ui.Stop as e:
        check("no crash, fault or hang", False, f"in {e}: {rig.problems}")
    log("== throughout")
    check("no error popup", not rig.error_popups(), f"{rig.error_popups()}")
    check("no access outside RAM and the peripherals", not rig.invalid, f"{dict(rig.invalid)}")
    check("no crash, fault or hang", not rig.problems, f"{rig.problems}")
    os.remove(image)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "dj"))
    ap.add_argument("--tools")
    ap.add_argument("--build", default=os.environ.get("BLOCKCOUNT_DIR"))
    ap.add_argument("--shots", help="also save the OLED at the DJ manual's moments in this folder")
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
    log(f"DJ (v19) of {a.elf} -> {a.out}")
    t = time.time()
    run(a, a.out)
    log(f"DJ ({os.path.basename(a.elf)}, {time.time() - t:.0f} s): {sv.checks - sv.failures} of {sv.checks} ok; "
        + ("all checks passed" if not sv.failures else f"FAILED: {sv.failures} check(s)"))
    sys.exit(1 if sv.failures else 0)


if __name__ == "__main__":
    main()
