#!/usr/bin/env python3
"""Arp freeze (features/0007: the community's open PR SynthstromAudible/DelugeFirmware#5208, ported; what the arp plays
written into the clip as notes, the arp switched off, one undo) in the v1.3 build, with the v1.3 rig (rig13.py).

Each case is a song of mastertune's arp tests (mastertune-1.2.1/tests/arp/arp_roll_test.py: one synth, a chord held
for the whole clip, the arp on it) with the ratchet probability at 100 %, so that nothing is left to chance. Every
note-on after the arp (Sound::noteOnPostArpeggiator) is logged with the audio sample timer and its velocity:

  (1) the song plays a bar with the arp: the notes it plays
  (2) stopped, the clip current (as entering it makes it), the sound editor on the arp's FREEZE item, SELECT: the
      confirmation; SELECT again: "Arp frozen", the clip's arp off
  (3) the song plays the same bar again, now from the clip's notes: the same notes in the same order, the same
      velocities, each within 400 samples (9 ms) of where the arp played it (a tick is 230 samples at 120 BPM: a hit
      goes to its nearest tick, and either play starts a note at the start of its audio window, up to 128 samples
      late)
  (4) undo (BACK in Song view): the arp on again; playing, the first second as in (1)
  (5) throughout: no crash, freeze, hang, error popup or access outside RAM and the peripherals
Cases: a plain arp (Up, 16ths, no ratchet), a reversed clip, mastertune's ratchets: a fixed 4 with bounce, a roll and
a ball (their hits come closer than a tick near the end, which the freeze renders at subticks), and mastertune's latch
with the only note on the 2nd quarter (compared with the arp's second loop, where the latched note plays from the bar's
start; the frozen clip must too).
On the build without arp freeze (s6) there is no FREEZE item: (2) fails.

Usage: arp_freeze_emu.py <deluge.elf> [case ...] --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (with the failed checks); exit 0/1."""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))  # beta-1.3/tests
import rig13  # noqa: E402,F401
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402

sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "mastertune-1.2.1", "tests", "arp")))
import arp_roll_test as art  # noqa: E402
import make_sd  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R3, UC_ARM_REG_SP  # noqa: E402

K = make_sd.knob
TOLERANCE = 400  # samples
BAR_S = 2.0  # make_sd's songs: 120 BPM
CASES = {
    # name: (arp attributes, sound params, reversed clip)
    "plain_up_16th": (dict(syncLevel=6), dict(ratchetProbability=K(0)), False),
    "plain_reversed": (dict(syncLevel=6), dict(ratchetProbability=K(0)), True),
    "fixed4+5_len2_16th": (art.CASES["fixed4+5_len2_16th"][0], dict(ratchetProbability=K(50)), False),
    "roll+4_len4_rise": (art.CASES["roll+4_len4_rise"][0], dict(ratchetProbability=K(50)), False),
    "ball+3_len2_rise": (art.CASES["ball+3_len2_rise"][0], dict(ratchetProbability=K(50)), False),
    # mastertune's latch, the only note on the 2nd quarter: from the second loop on the latched note plays from the
    # bar's start, and so must the frozen clip
    "latch_offbeat": (None, None, False),
}
BAR_SAMPLES = 88200


def build_card(path, arp, params, reverse):
    if arp is None:  # latch_offbeat
        from arp_extras_emu import build_latch_card
        build_latch_card(path, 1, [(60, [(96, 2)])])
        return
    art.build_sd(path, arp, params)
    if reverse:  # The clip played backwards: written into the song file as the firmware writes it
        import fat32
        xml = fat32.read_file(path, "SONGS/DEFAULT.XML").decode()
        xml = xml.replace("<instrumentClip", '<instrumentClip\n\t\t\tsequenceDirection="reverse"', 1)
        files, _ = make_sd.samples()
        files["SONGS/DEFAULT.XML"] = xml.encode()
        fat32.build(path, files)


def compare(live, frozen):
    """(ok, detail): the same notes and velocities in order, each within TOLERANCE samples (times from the first)."""
    if not live or not frozen:
        return False, f"{len(live)} live, {len(frozen)} frozen"
    l0, f0 = live[0][0], frozen[0][0]
    worst, mismatch = 0, None
    for i, (a, b) in enumerate(zip(live, frozen)):
        if (a[1], a[2]) != (b[1], b[2]):
            mismatch = mismatch or f"#{i}: live note {a[1]} vel {a[2]}, frozen note {b[1]} vel {b[2]}"
        worst = max(worst, abs((b[0] - f0) - (a[0] - l0)))
    ok = len(live) == len(frozen) and mismatch is None and worst <= TOLERANCE
    return ok, dict(live=len(live), frozen=len(frozen), worst_samples=worst, mismatch=mismatch)


def run_case(a, name, fails, report):
    arp, params, reverse = CASES[name]
    image = os.path.join(a.out, f"{name}.img")
    build_card(image, arp, params, reverse)
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    emu, sym = rig.emu, rig.emu.sym
    inp = fuzz_ui.Inputs(rig, "v13")
    timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
    hits = []

    def on_note_on(e):
        velocity = struct.unpack("<i", e.uc.mem_read(e.uc.reg_read(UC_ARM_REG_SP), 4))[0]
        hits.append((e.u32(timer), e.uc.reg_read(UC_ARM_REG_R3), velocity))
    emu.intercept(sym.find("_ZN5Sound21noteOnPostArpeggiator"), on_note_on)
    emu.uc.ctl_flush_tb()
    r = report[name] = {}
    starts = []

    def loop(played, which):
        """The hits of loop `which` (0 the first) of a play, with their times from that loop's start."""
        t0 = starts[-1] + which * BAR_SAMPLES
        return [(t - t0, n, v) for t, n, v in played if 0 <= t - t0 < BAR_SAMPLES - 2000]

    def play(seconds, label):
        n = len(hits)
        starts.append(emu.u32(timer))
        inp.button("PLAY", True)
        rig.tm(0.02, f"{label}: PLAY")
        inp.button("PLAY", False)
        rig.tm(seconds, label)
        inp.button("PLAY", True)
        rig.tm(0.02, f"{label}: STOP")
        inp.button("PLAY", False)
        rig.tm(0.3, f"{label}: stopped")
        return hits[n:]

    def press(name_, settle=0.2):
        inp.button(name_, True)
        rig.tm(0.03, f"{name_} pressed")
        inp.button(name_, False)
        rig.tm(settle, f"{name_} released")

    try:
        if "arp_freeze_menu" not in sym.by_name:
            fails.append(f"{name}: (2) no FREEZE item in this build")
            raise su.Stop("no freeze")
        o_cur, o_first, o_mode = su.gdb_ints(emu, [
            "print (int)&currentSong->currentClip - (int)currentSong",
            "print (int)&currentSong->firstOutput - (int)currentSong",
            "list InstrumentClip::InstrumentClip", "print (int)&((InstrumentClip*)0)->arpSettings.mode"])
        song = emu.u32(sym["currentSong"])
        clip, _ = rig.action("the synth's clip", sym["_ZN4Song17getClipWithOutputEP6OutputbP4Clip"], song,
                             emu.u32(song + o_first), 0, 0)
        if arp is None:  # latch: the second loop, the arp as it goes on looping
            live = play(2 * BAR_S, "live arp, two loops")
            live = loop(live, 1)
        else:
            live = play(BAR_S, "live arp")
            live = [h for h in live if h[0] - live[0][0] < BAR_S * 44100 - 2000] if live else live
        r["live_notes"] = len(live)
        # (2) FREEZE, as the user gets there: the clip current, the sound editor on the item, SELECT twice
        emu.uc.mem_write(song + o_cur, struct.pack("<I", clip))
        editor = sym["soundEditor"]
        rig.action("sound editor", sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], editor, clip,
                   sym["arp_freeze_menu"], 0)
        rig.action("open", sym["_Z6openUIP2UI"], editor)
        rig.tm(0.2, "editor open")
        n = len(rig.popups)
        press("SELECT_ENC", 0.3)
        r["confirmation"] = rig.ui_name()
        press("SELECT_ENC", 0.5)
        r["popups"] = [p[3] for p in rig.popups[n:]]
        r["arp_mode_after"] = emu.u32(clip + o_mode)
        if "Arp frozen" not in r["popups"] or r["arp_mode_after"] != 0:
            fails.append(f"{name}: (2) {r['popups']}, arp mode {r['arp_mode_after']}")
            raise su.Stop("not frozen")
        for _ in range(2):
            if rig.ui_name() == "sessionView":
                break
            press("BACK", 0.3)
        # (3) the frozen clip
        frozen = play(BAR_S, "frozen clip")
        if arp is None:
            frozen = loop(frozen, 0)
        else:
            frozen = [h for h in frozen if h[0] - frozen[0][0] < BAR_S * 44100 - 2000] if frozen else frozen
        ok, detail = compare(live, frozen)
        r["frozen"] = detail
        if not ok:
            fails.append(f"{name}: (3) {detail}")
        # (4) undo
        r["ui_before_undo"] = rig.ui_name()
        press("BACK", 0.3)  # Undo, in Song view
        r["arp_mode_undone"] = emu.u32(clip + o_mode)
        if arp is None:
            again = loop(play(2 * BAR_S, "after undo, two loops"), 1)
            first = live
        else:
            again = play(1.0, "after undo")
            first = [h for h in live if h[0] - live[0][0] < 1.0 * 44100 - 4000]
            again = again[:len(first)]
        ok2, detail2 = compare(first, again)
        r["after_undo"] = detail2
        if r["arp_mode_undone"] == 0 or not ok2:
            fails.append(f"{name}: (4) undo: arp mode {r['arp_mode_undone']}, {detail2}")
    except su.Stop:
        pass
    r["problems"] = list(rig.problems)
    if rig.problems or rig.error_popups() or rig.invalid:
        fails.append(f"{name}: (5) {rig.problems[:3]} {rig.error_popups()[:3]} {list(rig.invalid)[:3]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("cases", nargs="*", default=list(CASES))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    fails, report = [], {}
    for name in a.cases:
        run_case(a, name, fails, report)
        print(name, json.dumps(report[name], default=str), flush=True)
    json.dump(report, open(os.path.join(a.out, "arp_freeze.json"), "w"), indent=1, default=str)
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
