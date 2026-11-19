#!/usr/bin/env python3
"""The DJ tool ported from mastertune 1.2.1 (features/0005: the DJ filter on the song's gold knobs, sync to the tempo
Scan hears, the nudge that bends the internal clock) in the v1.3 build, with the v1.3 rig (rig13.py) and the fuzzer's
card. The gold knobs as Song view gets them (SessionView::modEncoderAction(), modEncoderButtonAction()), the buttons
through Buttons::buttonAction(), the encoders through v1.3's encoder task. mastertune's own test is
mastertune-1.2.1/tests/dj/dj_emu.py.

The DJ filter adds no filter of its own: it drives the song's LPF and HPF params (cutoff and resonance) as their own
knobs set them, so the filters' sound and levels stay v1.3's. (2) and (3) check that on the params and on the output.

  (1) boot: Song view
  (2) AFFECT ENTIRE (the song's knobs), the filter row (its mod button), the upper gold knob pressed three times: HPF, EQ, DJ (the song's filter type
      3, the popup "DJ"); turned 20 detents left of the middle: the LPF's cutoff at knob 24 (the value its own knob
      gives there), the HPF off, "DJ: LPF 16"; 40 right: the LPF off (open), the HPF at knob -24, "DJ: HPF 16"; 20 left:
      both off, "DJ: OFF"; the lower knob 10 right: both resonances 10 up; pressed once more: LPF again
  (3) the levels: the four params (both cutoffs, both resonances) bit for bit the same with the DJ filter at OFF as
      with the LPF opened and the HPF closed by their own knobs, and with "DJ: LPF 16" as with the LPF's own knob at
      24 (the decisive check: the filters compute their output from these params alone). Coarser, the output over the
      song's first 3 s (played from its start each time), in all and above 3 kHz: own knobs and DJ within 0.5 dB (the
      test song's free-running oscillators vary the same setting by up to 0.4 dB from run to run); the LPF at 24 takes
      at least 3 dB above 3 kHz
  (4) sync: the Scan view (Settings > Tuning > Scan), no tempo heard yet: TAP TEMPO shows "No tempo yet" and leaves
      the tempo; the analysis' BPM set to 128.0 (as it would read it): TAP TEMPO, "Sync 128.0 BPM", the song at 128.00
      BPM; BACK
  (5) the nudge, playing on the internal clock: the horizontal encoder held and the tempo encoder a detent right: the
      time per timer tick 4 % shorter, "Sync nudged"; 0.4 s later exactly what it was; a detent left: 4 % longer,
      then back; a nudge, then STOP at once: the tempo back
  (6) throughout: no crash, freeze, hang, error popup or access outside RAM and the peripherals
On the build without the DJ tool (s4) the filter types go round LPF, HPF, EQ: (2) fails.

Usage: dj_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (with the failed checks); exit 0/1."""
import argparse
import json
import math
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))  # beta-1.3/tests
import rig13  # noqa: E402,F401
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402
from master_tune_emu import Capture  # noqa: E402

LPF_FREQ, LPF_RES, HPF_FREQ, HPF_RES = 32, 33, 35, 36  # params::UnpatchedGlobal (checked against gdb)
MOD_TURN, MOD_PRESS = "_ZN11SessionView16modEncoderActionEll", "_ZN11SessionView22modEncoderButtonActionEhb"
DB_TOLERANCE = 0.5  # The test song's free-running oscillators vary the same setting by up to 0.4 dB run to run


def s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


def knob_value(k):
    return (1 << 31) - 1 if k >= 64 else k << 25


def rms_db(x):
    return 20 * math.log10(max(1e-12, float(np.sqrt(np.mean(x * x)))))


def high_db(x, above=3000.0):
    """The level of what is above `above` Hz (the part a low-pass at knob 24 takes away)."""
    spec = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / 44100)
    return 10 * math.log10(max(1e-30, float(spec[f > above].sum()) / len(x) ** 2 * 2))


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
    fails, res = [], {}
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    inp = fuzz_ui.Inputs(rig, "v13")
    emu, sym = rig.emu, rig.emu.sym

    def tm(s, what="run"):
        rig.tm(s, what)

    def press(name, settle=0.15):
        inp.button(name, True)
        tm(0.03, f"{name} pressed")
        inp.button(name, False)
        tm(settle, f"{name} released")

    def popups_since(n):
        return [p[3] for p in rig.popups[n:]]

    try:
        (pm_off, tempo_off, type_off, mode_off, mag_off, nudging_off, summaries_off, params_off, ap_size, cur_off,
         lpf, lpf_res, hpf, hpf_res) = su.gdb_ints(emu, [
             "print (int)&currentSong->paramManager - (int)currentSong",
             "print (int)&currentSong->timePerTimerTickBig - (int)currentSong",
             "print (int)&currentSong->globalEffectable.currentFilterType - (int)currentSong",
             "print (int)&currentSong->globalEffectable.modKnobMode - (int)currentSong",
             "print (int)&currentSong->insideWorldTickMagnitude - (int)currentSong",
             "print (int)&playbackHandler.clockNudging - (int)&playbackHandler",
             "print (int)&((ParamManager*)0)->summaries", "print (int)&((ParamSet*)0)->params",
             "print sizeof(AutoParam)", "print (int)&((AutoParam*)0)->currentValue",
             "print (int)'deluge::modulation::params::UNPATCHED_LPF_FREQ'",
             "print (int)'deluge::modulation::params::UNPATCHED_LPF_RES'",
             "print (int)'deluge::modulation::params::UNPATCHED_HPF_FREQ'",
             "print (int)'deluge::modulation::params::UNPATCHED_HPF_RES'"])
        if (lpf, lpf_res, hpf, hpf_res) != (LPF_FREQ, LPF_RES, HPF_FREQ, HPF_RES):
            raise SystemExit(f"the params' numbers changed: {lpf, lpf_res, hpf, hpf_res}")
        handler, session = sym["playbackHandler"], sym["sessionView"]

        def song():
            return emu.u32(sym["currentSong"])

        def param(i):
            params = emu.u32(song() + pm_off + summaries_off)
            array = emu.u32(params + params_off)
            return s32(emu.u32(array + i * ap_size + cur_off))

        def params4():
            return tuple(param(i) for i in (LPF_FREQ, HPF_FREQ, LPF_RES, HPF_RES))

        def filter_type():
            return emu.u8(song() + type_off)

        def tick_time():
            return struct.unpack("<Q", emu.uc.mem_read(song() + tempo_off, 8))[0]

        def bpm():
            magnitude = struct.unpack("<i", emu.uc.mem_read(song() + mag_off, 4))[0]
            return 110250 / (tick_time() / 2 ** 32) / 2 ** magnitude

        def knob(which, offset, detents=1):
            for _ in range(detents):
                rig.action(f"gold knob {which} {offset:+d}", sym[MOD_TURN], session, which, offset)

        def knob_press(which):
            for on in (1, 0):
                rig.action(f"gold knob {which} {'pressed' if on else 'released'}", sym[MOD_PRESS], session, which, on)

        # (1)
        tm(0.5, "settle")
        if rig.ui_name(root=True) != "sessionView":
            fails.append(f"(1) booted into {rig.ui_name(root=True)}")
            raise su.Stop("boot")

        # (2) the song's knobs (AFFECT ENTIRE), the filter row, LPF -> HPF -> EQ -> DJ
        press("AFFECT")
        press("MOD1")
        res["mod_knob_mode"] = emu.u8(song() + mode_off)
        if res["mod_knob_mode"] != 1:
            fails.append(f"(2) the filter row's button left the knob mode at {res['mod_knob_mode']}")
        # The song's own LPF open and HPF off, as the levels' reference (3) needs: by their own knobs in LPF and HPF mode
        n = len(rig.popups)
        knob(1, 1, 130)  # LPF mode: the LPF's knob to the top
        knob_press(1)    # HPF
        knob(1, -1, 130)  # the HPF's knob to the bottom
        res["own_knobs_open"] = own_open = params4()
        knob_press(1)  # EQ
        knob_press(1)  # DJ
        res["types"] = [p.upper() for p in popups_since(n)[-3:]]  # v1.3 names them "Hpf", "EQ"
        res["filter_type"] = filter_type()
        if res["types"] != ["HPF", "EQ", "DJ"] or filter_type() != 3:
            fails.append(f"(2) the filter types: {res['types']}, type {filter_type()}")
            raise su.Stop("no DJ")

        def state(tag, detents, offset, want_lpf, want_hpf, popup):
            n = len(rig.popups)
            knob(1, offset, detents)
            got = (param(LPF_FREQ), param(HPF_FREQ))
            last = (popups_since(n)[-1:] or [None])[0]
            res[tag] = dict(lpf_knob=round(got[0] / 2 ** 25, 2), hpf_knob=round(got[1] / 2 ** 25, 2), popup=last)
            if got != (knob_value(want_lpf), knob_value(want_hpf)) or last != popup:
                fails.append(f"(2) {tag}: {res[tag]}, LPF {want_lpf} HPF {want_hpf} {popup!r} expected")

        state("dj_lpf16", 20, -1, 24, -64, "DJ: LPF 16")
        state("dj_hpf16", 40, 1, 64, -24, "DJ: HPF 16")
        state("dj_off", 20, -1, 64, -64, "DJ: OFF")
        res["dj_off_params_equal_own_knobs"] = params4() == own_open
        if params4() != own_open:
            fails.append(f"(2) DJ OFF: params {params4()}, the own knobs' open/off {own_open}")
        r0 = (param(LPF_RES), param(HPF_RES))
        knob(0, 1, 10)
        r1 = (param(LPF_RES), param(HPF_RES))
        want = tuple(knob_value(min(64, round(v / 2 ** 25) + 10)) for v in r0)
        res["resonance"] = dict(before=[round(v / 2 ** 25, 2) for v in r0], after=[round(v / 2 ** 25, 2) for v in r1])
        if r1 != want:
            fails.append(f"(2) the lower knob 10 right: {res['resonance']}")
        knob(0, -1, 10)  # back
        n = len(rig.popups)
        knob_press(1)
        if [p.upper() for p in popups_since(n)[-1:]] != ["LPF"] or filter_type() != 0:
            fails.append(f"(2) pressed once more: {popups_since(n)}, type {filter_type()}")

        # (3) the levels, playing: own knobs against the DJ filter
        cap = Capture(emu)

        def level(what):
            """The output's level over the song's first 3 s, played from its start (the same music each time)."""
            press("PLAY", 0.02)
            m0 = cap.mark()
            tm(3.0, what)
            x = cap.mono(m0, cap.mark())
            press("PLAY", 0.5)  # stop
            return rms_db(x), high_db(x)

        p_own = params4()
        lv_own_open = level("LPF mode, LPF open, HPF off")
        knob_press(1)
        knob_press(1)
        knob_press(1)  # DJ
        knob(1, 1, 3)
        knob(1, -1, 3)  # DJ: OFF (from the middle, out and back)
        p_dj = params4()
        lv_dj_off = level("DJ: OFF")
        knob(1, -1, 20)  # DJ: LPF 16, the LPF at knob 24
        p_dj16 = params4()
        lv_dj16 = level("DJ: LPF 16")
        knob(1, 1, 20)  # OFF
        knob_press(1)  # LPF mode
        knob(1, -1, 40)  # the LPF's own knob: 64 -> 24
        p_own24 = params4()
        lv_own24 = level("LPF mode, the LPF at knob 24")
        knob(1, 1, 40)
        knob_press(1)
        knob_press(1)
        knob_press(1)
        knob_press(1)  # round to LPF again
        lv_own_again = level("LPF mode, LPF open, HPF off, again")  # how much the same setting varies run to run
        res["levels_db"] = {k: dict(rms=round(v[0], 2), above_3k=round(v[1], 2)) for k, v in
                            dict(own_open=lv_own_open, dj_off=lv_dj_off, own_lpf24=lv_own24, dj_lpf16=lv_dj16,
                                 own_open_again=lv_own_again).items()}
        spread = max(abs(a - b) for a, b in zip(lv_own_open, lv_own_again))
        tolerance = max(DB_TOLERANCE, spread + 0.1)
        res["levels_tolerance_db"] = round(tolerance, 2)
        res["levels_same_setting_twice_db"] = round(spread, 2)
        res["levels_params_same"] = dict(open=p_own == p_dj, lpf24=p_own24 == p_dj16)
        if p_own != p_dj or p_own24 != p_dj16:
            fails.append(f"(3) the params differ: own {p_own} / DJ {p_dj}; own 24 {p_own24} / DJ 16 {p_dj16}")
        if any(abs(a - b) > tolerance for a, b in zip(lv_own_open + lv_own24, lv_dj_off + lv_dj16)):
            fails.append(f"(3) the levels differ by more than {tolerance:.2f} dB: {res['levels_db']}")
        if lv_own_open[1] - lv_own24[1] < 3:
            fails.append(f"(3) the LPF at 24 takes less than 3 dB above 3 kHz: {res['levels_db']} (no filter?)")

        # (4) sync from the Scan view
        editor = sym["soundEditor"]
        o_cur, o_bpm = su.gdb_ints(emu, ["print (int)&settingsRootMenu.current_item_ - (int)&settingsRootMenu",
                                         "print (int)&AudioEngine::scan->bpm_ - (int)AudioEngine::scan"])
        rig.ui_names[sym["scanView"]] = "scanView"
        rig.action("settings", sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], editor, 0, sym["settingsRootMenu"], 0)
        rig.action("settings open", sym["_Z6openUIP2UI"], editor)
        tm(0.2, "settings")

        def turn_to(submenu, item):
            for direction in (1, -1):
                for _ in range(30):
                    if emu.u32(emu.u32(submenu + o_cur)) == item:
                        return True
                    inp.turn("select", direction)
                    tm(0.05, "select")
            return False

        turn_to(sym["settingsRootMenu"], sym["tuningSubmenu"])
        press("SELECT_ENC")
        turn_to(sym["tuningSubmenu"], sym["scanViewMenu"])
        press("SELECT_ENC", 0.3)
        if rig.ui_name() != "scanView":
            fails.append(f"(4) not in the Scan view: {rig.ui_name()}")
            raise su.Stop("scan")
        tick0 = tick_time()
        n = len(rig.popups)
        press("TAP", 0.2)
        res["sync_none"] = dict(popups=popups_since(n), tempo_kept=tick_time() == tick0)
        if "No tempo yet" not in popups_since(n) or tick_time() != tick0:
            fails.append(f"(4) TAP without a tempo: {res['sync_none']}")
        scan = emu.u32(sym["_ZN11AudioEngine4scanE"])
        emu.uc.mem_write(scan + o_bpm, struct.pack("<f", 128.0))
        n = len(rig.popups)
        press("TAP", 0.2)
        res["sync_128"] = dict(popups=popups_since(n), bpm=round(bpm(), 4))
        if "Sync 128.0 BPM" not in popups_since(n) or abs(bpm() - 128) > 0.005:
            fails.append(f"(4) TAP at 128: {res['sync_128']}")
        press("BACK", 0.3)

        # (5) the nudge
        press("PLAY", 0.5)
        base = tick_time()

        def nudge(direction):
            n = len(rig.popups)
            inp.button("X_ENC", True)
            tm(0.02, "horizontal encoder held")
            inp.turn("tempo", direction)
            tm(0.02, "tempo encoder")
            bent = tick_time()
            inp.button("X_ENC", False)
            return bent, emu.u8(handler + nudging_off), popups_since(n)

        for direction in (1, -1):
            bent, nudging, pops = nudge(direction)
            tm(0.4, "after the nudge")
            want = base - base // 100 * 4 if direction > 0 else base + base // 100 * 4
            res[f"nudge_{direction:+d}"] = dict(ratio=round(bent / base, 5), nudging=nudging, popups=pops,
                                               back=tick_time() == base, nudging_after=emu.u8(handler + nudging_off))
            if bent != want or not nudging or "Sync nudged" not in pops or tick_time() != base:
                fails.append(f"(5) nudge {direction:+d}: {res[f'nudge_{direction:+d}']}")
        bent, _, _ = nudge(1)
        press("PLAY", 0.02)  # STOP at once
        res["nudge_stop"] = dict(bent=bent != base, back=tick_time() == base)
        if bent == base or tick_time() != base:
            fails.append(f"(5) nudge then STOP: {res['nudge_stop']}")
        tm(0.3, "stopped")
    except su.Stop:
        pass
    res["problems"] = list(rig.problems)
    res["error_popups"] = rig.error_popups()
    res["invalid"] = {str(k): v for k, v in rig.invalid.items()}
    if rig.problems or rig.error_popups() or rig.invalid:
        fails.append(f"(6) {rig.problems[:3]} {rig.error_popups()[:3]} {list(res['invalid'])[:3]}")
    print(json.dumps(res, indent=1, default=str))
    json.dump(res, open(os.path.join(a.out, "dj.json"), "w"), indent=1, default=str)
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
