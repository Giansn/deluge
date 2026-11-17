#!/usr/bin/env python3
"""Master tune ported from mastertune 1.2.1 (features/0003) in the v1.3 build, on the v1.3 rig (rig13.py: the firmware's
own task manager, the audio DMA in real time) with a card of its own: the song of mastertune's retune test
(mastertune-1.2.1/tests/retune/retune_emu.py: every part a tone of its own frequency, so each can be measured in the mix)
and a few parts more, and CommunityFeatures.XML with masterTune 4320 (432.0 Hz).

The parts and their tones (at 440 Hz): the kit's sample rows KICK (800 Hz, a 48 kHz file), SNARE (1100), LOOPROW (3400,
a loop), HAT (2700, an AIFF), TOM (400, transposed +5), STRETCH (1700, time stretch on) and MTUN (1900, a WAV with an
"mtun" chunk saying it was recorded at 432.0 Hz); the sample synths SMP (1400, played at 60 and 67) and MULTI
(a multisample, 300 and 3000); the audio clip CLIP (2400, its length kept); the synth SINE (a sine oscillator, A4);
the FM synth FM (a sine carrier at A#2 and a modulator at the same pitch: its first sideband at twice the carrier,
where an untuned modulator would put it elsewhere); a MIDI track and a CV track.

One boot:
  (1) the tuning read from the settings file: 432.0 Hz
  (2) playing, 4 bars after a 1-bar warm-up: each part's pitch in the output (the phase slope of its band) within
      1 cent of the 440 Hz tone times 432/440 (the MTUN row at its own pitch: it was recorded at 432); the FM sideband
      at twice the carrier
  (3) MIDI: the Channel Fine Tuning (RPN 1, 14 bits: 8192 - 2602 for -31.8 cents) on the MIDI track's channel before
      its first note
  (4) CV: the pitch voltage of the CV track's note as the firmware's formula gives it with the -31.8 cents
  (5) Settings > Tuning > Master tune opened while playing, the select encoder turned by +14 (1 Hz a detent): 446.0 Hz;
      the held notes retune (the parts' pitches in the 2 bars after, MTUN now 446/432 up); the CV voltage
      recalculated at once; the MIDI channel retuned; the menu left saves the settings file
  (6) stopped, a stem export rendered offline (SAVE held + RECORD in song view): the WAV files written have the "mtun"
      chunk with 4460, their audio where the "data" chunk says (a reader that knows nothing of it still reads them)
  (7) a second boot from the same card starts at 446.0 Hz
  (9) then Settings > Actions > Reset settings (RuntimeFeatureSettings::factoryReset(), as its community features
      and factory reset entries call it): back to 440.0 Hz, and the CPU monitor (features/0002, kept in the same file)
      back to off
and (8) no crash, freeze or hang.

Usage: master_tune_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
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
import rig13  # noqa: E402  (sets up the paths of mastertune's rig)
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402

sys.path.insert(0, os.path.join(rig13.TESTS, "retune"))
import fat32  # noqa: E402
import make_sd as M  # noqa: E402
import retune_emu as R  # noqa: E402
from pc_test import wav  # noqa: E402
from tone import cents, firmware_wav_view, tone_frequency  # noqa: E402
from unicorn import UC_HOOK_MEM_WRITE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2  # noqa: E402

SR = 44100
TENTHS = "_ZN10MasterTune12_GLOBAL__N_18tenthsHzE"  # MasterTune's tenthsHz (getTenthsHz() is inlined everywhere)
BAR_S = 2.0  # 120 BPM
STEP = M.STEP
D = R.D
START = 4320
TURN = 14  # select encoder detents in the menu: 1 Hz each
AFTER = START + 10 * TURN
FM_NOTE = 46  # A#2, 116.54 Hz at 440
CV_NOTE = 60

# name, the tone's frequency at 440 Hz (as played: transpose included), the tuning it was recorded at
PARTS = [("KICK", 800, 4400), ("SNARE", 1100, 4400), ("LOOPROW", 3400, 4400), ("HAT", 2700, 4400),
         ("TOM", 400 * 2 ** (5 / 12), 4400), ("STRETCH", 1700, 4400), ("MTUN", 1900, 4320),
         ("SMP C3", 1400, 4400), ("SMP G3", 1400 * 2 ** (7 / 12), 4400), ("MULTI A", 300, 4400),
         ("MULTI B", 3000, 4400), ("CLIP", 2400, 4400), ("SINE", 440, 4400),
         ("FM", 440 * 2 ** ((FM_NOTE - 69) / 12), 4400), ("FM sideband", 2 * 440 * 2 ** ((FM_NOTE - 69) / 12), 4400)]


# --- the card

def mtun_sample():
    return wav(R.sine(1900, SR, 0.5, 0.3, fade=0.002), SR, 16, mtun=4320)


def synth(name, osc1_body, note_rows, mode="subtractive", extra="", params=None):
    """retune_emu.synth() with a mode, more elements and params"""
    out, clip = R.synth(name, osc1_body, note_rows)
    if mode != "subtractive":
        out = out.replace('mode="subtractive"', f'mode="{mode}"', 1)
    out = out.replace("\t\t\t<lfo1 ", extra + "\t\t\t<lfo1 ", 1)
    for k, v in (params or {}).items():
        clip, n = R.re.subn(rf'{k}="[^"]*"', f'{k}="{v}"', clip, count=1)
        assert n == 1, k
    return out, clip


def song_xml():
    R.KIT_ROWS.append(("MTUN", "MTUN.WAV", {}, dict(startSamplePos=0, endSamplePos=22050), [(14 * STEP, STEP)]))
    xml = R.song_xml()
    whole = 16 * STEP
    sine = synth("SINE", '\t\t\t<osc1 type="sine" transpose="0" cents="0" retrigPhase="-1" />\n',
                 [("y", 69, [(0, whole, 100)], None)])
    fm = synth("FM", '\t\t\t<osc1 type="sine" transpose="0" cents="0" retrigPhase="0" />\n',
               [("y", FM_NOTE, [(0, whole, 100)], None)], mode="fm",
               extra='\t\t\t<modulator1 transpose="0" cents="0" retrigPhase="0" />\n'
                     '\t\t\t<modulator2 transpose="0" cents="0" retrigPhase="0" toModulator1="0" />\n',
               params=dict(modulator1Amount=M.knob(14)))
    midi = M.midi_track()
    cv_out = '\t\t<cvChannel channel="0" defaultVelocity="64" isArmedForRecording="0" activeModFunction="0" ' \
             'colour="0" />\n'
    cv_clip = M.instrument_clip("", "", M.BAR, [("y", CV_NOTE, [(0, whole, 100)], None)])
    cv_clip = R.re.sub(r'\s*instrumentPresetFolder=""', "", cv_clip.replace('instrumentPresetName=""',
                                                                             'cvChannel="0"'))
    parts = [sine, fm, midi, (cv_out, cv_clip)]
    xml = xml.replace("\t</instruments>\n", "".join(p[0] for p in parts) + "\t</instruments>\n", 1)
    return xml.replace("\t</sessionClips>\n", "".join(p[1] for p in parts) + "\t</sessionClips>\n", 1)


def make_card(image):
    files = R.samples()
    files[D + "MTUN.WAV"] = mtun_sample()
    files["SONGS/DEFAULT.XML"] = song_xml().encode()
    files["CommunityFeatures.XML"] = R.community_features(START)
    fat32.build(image, files)


def walk(image, path=""):
    """Every file on the card: path -> size."""
    f, root, read_chain = fat32._open(image)
    out = {}
    with f:
        def go(cluster, prefix):
            for name11, lfn, attr, c, size in fat32._entries(read_chain(cluster)):
                name = lfn or (name11[:8].decode("latin-1").rstrip() + ("." + name11[8:].decode("latin-1").rstrip()
                                                                        if name11[8:].strip() else ""))
                if name in (".", "..") or attr & 0x08:
                    continue
                if attr & 0x10:
                    go(c, prefix + name + "/")
                else:
                    out[prefix + name] = size
        go(root, "")
    return out


# --- what the firmware does

class Capture:
    """The samples the firmware writes into the SSI's transmit buffer, in the order written (the audio as rendered)."""

    def __init__(self, emu):
        cached = emu.sym["ssiTxBuffer"]
        self.l, self.r = [], []
        self.ranges = [(cached, cached + 128 * 8), (cached + rig13.se.UNCACHED_MIRROR_OFFSET,
                                                    cached + rig13.se.UNCACHED_MIRROR_OFFSET + 128 * 8)]
        for lo, hi in self.ranges:
            emu.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_write, begin=lo, end=hi - 1)

    def on_write(self, uc, access, address, size, value, _):
        base = next(lo for lo, hi in self.ranges if lo <= address < hi)
        words = [value & 0xFFFFFFFF, (value >> 32) & 0xFFFFFFFF] if size == 8 else [value & 0xFFFFFFFF]
        word = (address - base) // 4
        for w in words:
            (self.l if word % 2 == 0 else self.r).append(w - (1 << 32) if w & 0x80000000 else w)
            word += 1

    def mark(self):
        return min(len(self.l), len(self.r))

    def mono(self, a, b):
        return (np.array(self.l[a:b], np.float64) + np.array(self.r[a:b], np.float64)) / 2 ** 32


def pitches(x, tenths):
    out = {}
    for name, f440, recorded in PARTS:
        expected = f440 * tenths / recorded
        f, used = tone_frequency(x, SR, expected, edge=4410)
        out[name] = dict(expected=round(expected, 3), measured=round(f, 3), samples=used,
                         cents=round(float(cents(f, expected)), 3) if used else None)
    return out


def check_pitches(fails, label, res):
    for name, r in res.items():
        if r["cents"] is None or abs(r["cents"]) > 1.0:
            fails.append(f"{label}: {name} at {r['measured']} Hz, {r['expected']} expected ({r['cents']} cents)")


def prefixed(sym, prefix):
    """(name, address) of the one symbol starting with prefix"""
    names = [n for n in sym.by_name if n.startswith(prefix)]
    if len(names) != 1:
        raise KeyError(f"{names} start with {prefix}")
    return names[0], sym.find(prefix)


def menu_value(emu, item):
    """The value a Number menu item holds (Number::value_, its offset from the debug info)."""
    if not hasattr(menu_value, "off"):
        menu_value.off, = su.gdb_ints(emu, ["print (int)&masterTuneMenu.value_ - (int)&masterTuneMenu"])
    return struct.unpack("<i", emu.uc.mem_read(item + menu_value.off, 4))[0]


def fine_tuning(tenths):
    c = 1200 * math.log2(tenths / 4400)
    return max(0, min(16383, 8192 + math.floor(c * 8192 / 100 + 0.5)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    image = os.path.join(a.out, "sd.img")
    make_card(image)
    fails, res = [], {}
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    inp = fuzz_ui.Inputs(rig, "v13")
    emu, sym = rig.emu, rig.emu.sym
    if TENTHS not in sym.by_name:  # the build without it: the checks go on (the pitches are those at 440 Hz)
        fails.append("(1) no master tune in this build (MasterTune's tenthsHz)")

    def tenths(e=emu):
        return struct.unpack("<i", e.uc.mem_read(e.sym[TENTHS], 4))[0] if TENTHS in e.sym.by_name else None
    try:
        res["boot"] = t0 = tenths()
        if t0 != START:
            fails.append(f"(1) {t0} after boot, {START} in the settings file")
        cap = Capture(emu)
        midi = []  # (sample index, status type, channel, data1, data2)
        cv = []  # (sample index, channel, voltage)

        # MidiEngine::sendMidi(source, message, filter, sendUSB) and CVEngine::sendVoltageOut(channel, voltage): in
        # this build LTO's .constprop clones without `this` (the one MidiEngine and CVEngine), so the message is in
        # r1, the channel and the voltage in r0 and r1 (as their disassembly shows); one register later otherwise
        send_midi, send_cv = (prefixed(sym, n) for n in ("_ZN10MidiEngine8sendMidiE", "_ZN8CVEngine14sendVoltageOutE"))
        reg = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2]

        def on_midi(e, k=1 if send_midi[0].endswith(".constprop.0") else 2):
            m = e.uc.reg_read(reg[k])
            midi.append((cap.mark(), m & 0xF, (m >> 8) & 0xFF, (m >> 16) & 0xFF, m >> 24))
        emu.intercept(send_midi[1], on_midi)

        def on_cv(e, k=0 if send_cv[0].endswith(".constprop.0") else 1):
            cv.append((cap.mark(), e.uc.reg_read(reg[k]) & 0xFF, e.uc.reg_read(reg[k + 1]) & 0xFFFF))
        emu.intercept(send_cv[1], on_cv)
        emu.uc.ctl_flush_tb()
        o_vpo, o_tr, o_ce, o_pb = su.gdb_ints(emu, [
            "print (int)&cvEngine.cvChannels[0].voltsPerOctave - (int)&cvEngine",
            "print (int)&cvEngine.cvChannels[0].transpose - (int)&cvEngine",
            "print (int)&cvEngine.cvChannels[0].cents - (int)&cvEngine",
            "print (int)&cvEngine.cvChannels[0].pitchBend - (int)&cvEngine"])
        cv_base = sym["cvEngine"]

        def cv_expected(tenths):
            vpo = emu.u8(cv_base + o_vpo)
            tr, ce = struct.unpack("<bb", bytes([emu.u8(cv_base + o_tr), emu.u8(cv_base + o_ce)]))
            pb = struct.unpack("<i", emu.uc.mem_read(cv_base + o_pb, 4))[0]
            n = CV_NOTE + tr + ce * 0.01 + pb / (1 << 23) + 1200 * math.log2(tenths / 4400) * 0.01
            return round(2 ** ((n - 60) / 12) * 6552) if vpo == 0 else int((n - 24) * 5.46 * vpo + 0.5)

        rig.play()
        rig.tm(BAR_S, "warm-up bar")
        a0 = cap.mark()
        rig.tm(4 * BAR_S, "4 bars at 432")
        a1 = cap.mark()
        res["at_432"] = p432 = pitches(cap.mono(a0, a1), START)
        check_pitches(fails, "(2) at 432", p432)

        first_on = next((m for m in midi if m[1] == 0x9 and m[2] == 0), None)
        rpn = [m for m in midi if m[1] == 0xB and m[2] == 0]
        res["midi"] = dict(first_note_at=first_on[0] if first_on else None, cc=[m[3:] for m in rpn][:12])
        want = fine_tuning(START)
        seq = [(101, 0), (100, 1), (6, want >> 7), (38, want & 127), (101, 127), (100, 127)]
        before = [m[3:] for m in rpn if first_on and m[0] <= first_on[0]]
        if first_on is None:
            fails.append("(3) no note-on on the MIDI track's channel")
        elif before[:6] != seq:
            fails.append(f"(3) before the first note {before[:6]}, {seq} expected (Channel Fine Tuning {want})")
        cv0 = [v for i, ch, v in cv if ch == 0]
        res["cv"] = dict(voltages=sorted(set(cv0))[:6], expected=cv_expected(START), at_440=cv_expected(4400))
        if not cv0 or cv0[-1] != cv_expected(START):
            fails.append(f"(4) CV {cv0[-3:]}, {cv_expected(START)} expected (at 440 {cv_expected(4400)})")

        # (5) Settings > Tuning > Master tune, while playing
        editor = sym["soundEditor"]
        n_midi, n_cv = len(midi), len(cv)
        root, tuning, item = sym["settingsRootMenu"], sym["tuningSubmenu"], sym["masterTuneMenu"]
        o_cur, o_rec, o_depth = su.gdb_ints(emu, [
            "print (int)&settingsRootMenu.current_item_ - (int)&settingsRootMenu",
            "print (int)&soundEditor.menuItemNavigationRecord - (int)&soundEditor",
            "print (int)&soundEditor.navigationDepth - (int)&soundEditor"])
        rig.action("settings", sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], editor, 0, root, 0)
        rig.action("settings open", sym["_Z6openUIP2UI"], editor)
        rig.tm(0.1, "settings open")
        turns = 0
        while emu.u32(emu.u32(root + o_cur)) != tuning and turns < 30:
            inp.turn("select", 1)
            rig.tm(0.05, "select encoder")
            turns += 1
        for _ in range(2):  # Tuning, then Master tune (its first item)
            inp.button("SELECT_ENC", True)
            rig.tm(0.05, "select pressed")
            inp.button("SELECT_ENC", False)
            rig.tm(0.1, "select released")
        res["menu"] = dict(turns_to_tuning=turns, in_master_tune=emu.u32(editor + o_rec + 4 * emu.u8(editor + o_depth)) == item,
                           value_shown=menu_value(emu, item))
        if not res["menu"]["in_master_tune"] or res["menu"]["value_shown"] != START:
            fails.append(f"(5) Settings > Tuning > Master tune: {res['menu']}")
        inp.turn("select", TURN)
        rig.tm(0.1, "turned")
        res["after_turn"] = t1 = tenths()
        if t1 != AFTER:
            fails.append(f"(5) {t1} after the turn, {AFTER} expected")
        cv1 = [v for i, ch, v in cv[n_cv:] if ch == 0]
        res["cv_after_turn"] = dict(voltages=cv1[-3:], expected=cv_expected(AFTER))
        if not cv1 or cv1[-1] != cv_expected(AFTER):
            fails.append(f"(5) CV after the turn {cv1[-3:]}, {cv_expected(AFTER)} expected at once")
        rig.action("settings exit (save)", sym["_ZN11SoundEditor14exitCompletelyEv"], editor, limit_s=20)
        rig.tm(BAR_S / 2, "settle")
        b0 = cap.mark()
        rig.tm(2 * BAR_S, "2 bars at 446")
        b1 = cap.mark()
        res["at_446"] = p446 = pitches(cap.mono(b0, b1), AFTER)
        check_pitches(fails, "(5) at 446", p446)
        want1 = fine_tuning(AFTER)
        rpn1 = [m[3:] for m in midi[n_midi:] if m[1] == 0xB and m[2] == 0]
        res["midi_after_turn"] = rpn1[:6]
        if rpn1[:6] != [(101, 0), (100, 1), (6, want1 >> 7), (38, want1 & 127), (101, 127), (100, 127)]:
            fails.append(f"(5) MIDI after the turn {rpn1[:6]} (Channel Fine Tuning {want1} expected)")

        # (6) a stem export
        rig.action("PLAY (stop)", rig.a["_ZN15PlaybackHandler17playButtonPressedEl"], 0)
        rig.tm(1.0, "stopped")
        files_before = walk(image)
        o_offline, = su.gdb_ints(emu, ["list deluge_main", "print (int)&((StemExport*)0)->renderOffline"])
        emu.uc.mem_write(sym["stemExport"] + o_offline, b"\x01")
        B = fuzz_ui.B
        rig.button(B["SAVE"], True)
        rig.tm(0.05, "SAVE held")
        _, took = rig.button(B["RECORD"], True, limit_s=600)  # the export runs inside this press
        rig.button(B["RECORD"], False)
        rig.button(B["SAVE"], False)
        rig.tm(1.0, "export done")
        new = sorted(p for p in walk(image) if p not in files_before and p.upper().endswith(".WAV"))
        exports = []
        for p in new:
            data = fat32.read_file(image, p)
            v = firmware_wav_view(data)
            import soundfile
            import io
            x, rate = soundfile.read(io.BytesIO(data))
            exports.append(dict(path=p, mtun=v["mtun"], data_start=v["data_start"], frames=len(x), rate=rate,
                                peak=round(float(np.abs(x).max()), 4) if len(x) else 0))
        res["export"] = dict(press_s=round(took / 400e6, 2), files=exports)
        if not exports:
            fails.append("(6) no WAV file exported")
        for e in exports:
            if e["mtun"] != AFTER:
                fails.append(f"(6) {e['path']}: mtun {e['mtun']}, {AFTER} expected")
        if exports and not any(e["peak"] > 0.001 for e in exports):
            fails.append("(6) the exported files are silent")
        for _ in range(3):  # the "Export Done" menu
            if rig.ui_name() in rig.ui_names.values():
                break
            inp.button("BACK", True)
            rig.tm(0.05, "BACK")
            inp.button("BACK", False)
            rig.tm(0.3, "menu closed")
        res["problems"] = list(rig.problems)
    except su.Stop:
        res["problems"] = list(rig.problems)
    except (Exception, SystemExit) as e:  # the test's own error: reported, with what was measured so far
        fails.append(f"test error: {e!r}"[:300])
        res["problems"] = list(rig.problems)
    if res.get("problems"):
        fails.append(f"(8) {res['problems'][:3]}")
    # (7) a second boot from the same card
    if not any(f.startswith("(5)") and "after the turn," in f for f in fails):
        rig2 = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
        res["second_boot"] = t2 = tenths(rig2.emu)
        if t2 != AFTER:
            fails.append(f"(7) {t2} after a restart, {AFTER} expected: not saved")
        mode = rig2.emu.sym["_ZN9cpu_stats4modeE"]
        rig2.emu.uc.mem_write(mode, b"\x01")  # the CPU monitor on, as its menu or shortcut leaves it
        rig2.action("factory reset", rig2.emu.sym.find("_ZN22RuntimeFeatureSettings12factoryReset"),
                    rig2.emu.sym["runtimeFeatureSettings"], 0)
        rig2.tm(0.5, "after the reset")
        res["after_reset"] = dict(tenths=tenths(rig2.emu), cpu_monitor=rig2.emu.u8(mode))
        if res["after_reset"] != dict(tenths=4400, cpu_monitor=0):
            fails.append(f"(9) after a settings reset {res['after_reset']}, 4400 and 0 expected")
        if rig2.problems:
            fails.append(f"(8) second boot: {rig2.problems[:3]}")
    print(json.dumps(res, indent=1, default=str))
    json.dump(res, open(os.path.join(a.out, "master_tune.json"), "w"), indent=1, default=str)
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
