#!/usr/bin/env python3
"""The arp extras ported from mastertune 1.2.1 (latch, ratchet notes, ratchet bounce, bounce length, bounce velocity,
the roll and the ball) in the v1.3 build, with the v1.3 rig (rig13.py: the firmware's own task manager, the SSI's DMA
in real time). The songs are those of mastertune's own arp tests (mastertune-1.2.1/tests/arp/arp_roll_test.py): one
synth whose arp ratchets on every step, every note-on after the arp (Sound::noteOnPostArpeggiator) logged with the
audio sample timer and its velocity.

Checked, for each ratchet case:
  (1) the number of hits in the first ratchet: as the formula of arp_roll_test.py has it (expected_roll, expected_ball,
      expected_finite: the same as mastertune's ArpeggiatorBase::getRatchetNoteStart())
  (2) where they fall: each hit within one audio window after the formula's time (the arp starts notes per rendered
      window; v1.3 renders at most 128 samples per window here), never before it
  (3) the velocity follows the gaps as the bounce velocity says: Even all the same, Fade softest at the shortest gap,
      Rise loudest there
and for latch, a note held for 2 ticks at the start of the bar and another one half a bar later:
  (4) latch on: the arp goes on after the first key is let go, and once the second key comes in only its note plays
  (5) latch off: the arp stops when the key is let go (as in v1.3)
and, the case the review of the port found (mastertune 1.2.1 has it too): latch on, the first note latched, then the
arp switched off while playing (its mode written as the Mode menu leaves it on a kit arp, which doesn't cut):
  (7) no note starts after the arp is off: the latched note isn't started as a held note no note-off would end
and in every case (6) no crash, freeze or hang.

Usage: arp_extras_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR [case ...]
Last line: PASS or FAIL (with the failed checks); exit 0/1."""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))  # beta-1.3/tests
import rig13  # noqa: E402  (sets up the paths of mastertune's rig, patches song_emu for v1.3)
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402

sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "mastertune-1.2.1", "tests", "arp")))
import arp_roll_test as art  # noqa: E402
import make_sd  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R3, UC_ARM_REG_SP  # noqa: E402

WINDOW = 128  # Samples: the most v1.3 renders per audio window in the rig
RATCHET_CASES = ["roll+4_len4_rise", "roll-4_len4_fade", "roll0_len2", "fixed3+6_len1_even", "fixed4+5_len2_16th",
                 "roll+6_len1_16th", "ball+3_len2_rise", "ball+7_len4_rise", "ball-6_len2_fade"]
K = make_sd.knob
TICKS_PER_BAR = 384
LATCH_CASES = {
    # name: (latch, rows [(note, [(pos ticks, length ticks)])])
    "latch_on": (1, [(60, [(0, 2)]), (64, [(TICKS_PER_BAR // 2, 2)])]),
    "latch_off": (0, [(60, [(0, 2)]), (64, [(TICKS_PER_BAR // 2, 2)])]),
}


def build_latch_card(path, latch, rows):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda *args, **kwargs: ""
    env = dict(attack=K(0), decay=K(10), sustain=K(50), release=K(0))
    p = dict(ratchetProbability=K(0), arpeggiatorGate=K(25), reverbAmount=K(0), delayFeedback=K(0))
    a = dict(arpMode="arp", noteMode="up", octaveMode="up", numOctaves=1, syncLevel=6, latch=latch)
    clip_rows = [("y", note, [(pos, length, 90) for pos, length in notes], None) for note, notes in rows]
    make_sd.chord_rows = lambda octave, velocity=90: clip_rows
    synth = make_sd.synth("PING", make_sd.SAW, make_sd.SQUARE, 1, p, env, make_sd.PAD_ENV2,
                          [("velocity", "volume", 25)], notes_octave=0, arp=a)
    make_sd.synths = lambda: [synth]
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 1).encode()
    make_sd.fat32.build(path, files)


def play(a, image, seconds, label, event=None):
    """Boots v1.3 with the card, plays the startup song for seconds; returns the note-ons (sample timer, note,
    velocity) and the rig's problems. event: (after s, fn(rig) -> mark) runs once on the way; the mark is returned."""
    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    emu, sym = rig.emu, rig.emu.sym
    timer = sym["_ZN11AudioEngine16audioSampleTimerE"]
    hits = []

    def on_note_on(e):
        velocity = struct.unpack("<i", e.uc.mem_read(e.uc.reg_read(UC_ARM_REG_SP), 4))[0]
        hits.append((e.u32(timer), e.uc.reg_read(UC_ARM_REG_R3), velocity))
    emu.intercept(sym.find("_ZN5Sound21noteOnPostArpeggiator"), on_note_on)
    emu.uc.ctl_flush_tb()
    inp = fuzz_ui.Inputs(rig, "v13")
    try:
        inp.button("PLAY", True)
        rig.tm(0.02, "PLAY held")
        inp.button("PLAY", False)
        if event:
            rig.tm(event[0], label)
            mark = event[1](rig)
            rig.tm(seconds - event[0], f"{label} after the event")
        else:
            rig.tm(seconds, label)
    except su.Stop:
        pass
    if event:
        return hits, list(rig.problems), rig.playing(), mark
    return hits, list(rig.problems), rig.playing()


def check_ratchet(name, hits, fails, report):
    arp = art.CASES[name][0]
    step = art.STEP_8TH if arp["syncLevel"] == 5 else art.STEP_8TH // 2
    span = step * arp["ratchetBounceLength"]
    amount = arp["ratchetBounce"]
    if arp["ratchetNotes"] == 254:
        exp = art.expected_ball(span, 1 - 0.05 * abs(amount), amount < 0, arp["ratchetBounceLength"])
    elif arp["ratchetNotes"] == 255:
        exp = art.expected_roll(span, 1 - 0.05 * abs(amount), amount < 0,
                                even_gap=max(step / 8, art.MIN_GAP) if amount == 0 else None)
        if amount == 0:
            exp = exp[0]
    else:
        exp = art.expected_finite(arp["ratchetNotes"], amount, span)
    t0 = hits[0][0] if hits else 0
    first = [h for h in hits if h[0] - t0 < span]
    got = [h[0] - t0 for h in first]
    err = [g - e for g, e in zip(got, exp)]
    r = dict(expected=len(exp), got=len(got), err_min=round(min(err)) if err else None,
             err_max=round(max(err)) if err else None, velocities=[h[2] for h in first])
    report[name] = r
    if len(got) != len(exp):
        fails.append(f"{name}: (1) {len(got)} hits in the first ratchet, the formula has {len(exp)}")
    elif min(err) < -1 or max(err) > WINDOW:
        fails.append(f"{name}: (2) hits off the formula by {min(err):.0f} ... {max(err):.0f} samples")
    # (3) the velocity against the gaps
    fade = arp["ratchetBounceFade"]
    if len(first) >= 3 and not (arp["ratchetNotes"] == 255 and amount == 0):
        starts = [e for e in exp[:len(first)]] + [span]
        gaps = [starts[i + 1] - starts[i] for i in range(len(first))]
        vel = [h[2] for h in first]
        shortest, longest = gaps.index(min(gaps)), gaps.index(max(gaps))
        ok = (len(set(vel)) == 1 if fade == 0 else vel[shortest] < vel[longest] if fade == 1
              else vel[shortest] > vel[longest])
        r["velocity_check"] = dict(fade=fade, at_shortest=vel[shortest], at_longest=vel[longest], ok=ok)
        if not ok:
            fails.append(f"{name}: (3) velocities {vel} don't follow the gaps as bounce velocity {fade} says")


def arp_off(rig):
    """The song's (only) clip's arp mode set to OFF, as a menu leaves it; returns the audio sample timer then. The
    clip is the first of Song::sessionClips (in session view no clip is the current one)."""
    emu = rig.emu
    clips_off, mode_off, off = su.gdb_ints(emu, ["list Song::Song", "print (int)&((Song*)0)->sessionClips",
                                                "print (int)&((InstrumentClip*)0)->arpSettings.mode",
                                                "print (int)ArpMode::OFF"])
    _, _, memory, count, msize, mstart, esize = rig.heap_offsets
    arr = rig.song() + clips_off
    mem, first, es = emu.u32(arr + memory), emu.u32(arr + mstart), emu.u32(arr + esize)
    clip = emu.u32(mem + first * es)
    emu.uc.mem_write(clip + mode_off, bytes([off]))
    return emu.u32(emu.sym["_ZN11AudioEngine16audioSampleTimerE"])


def check_latch(name, latch, hits, fails, report):
    half_bar = make_sd.BAR_SAMPLES // 2 if hasattr(make_sd, "BAR_SAMPLES") else art.SR * 2 // 2
    t0 = hits[0][0] if hits else 0
    before = [h for h in hits if h[0] - t0 < half_bar - art.STEP_8TH // 4]
    after = [h for h in hits if h[0] - t0 >= half_bar + art.STEP_8TH]
    report[name] = dict(hits=len(hits), notes_before=sorted({h[1] for h in before}),
                        notes_after=sorted({h[1] for h in after}), before=len(before), after=len(after))
    if latch:
        # 16ths: half a bar is 8 steps; the first key's note on every one of them, then the second key's only
        if len(before) < 6 or len({h[1] for h in before}) != 1:
            fails.append(f"{name}: (4) the first note didn't go on after its key was let go ({len(before)} hits)")
        if len(after) < 6 or len({h[1] for h in after}) != 1 or {h[1] for h in after} == {h[1] for h in before}:
            fails.append(f"{name}: (4) the second key didn't replace the latched note ({report[name]})")
    elif len(before) > 2 or len(after) > 2:
        fails.append(f"{name}: (5) the arp went on without latch ({len(before)} + {len(after)} hits)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("cases", nargs="*")
    a = ap.parse_intermixed_args()
    os.makedirs(a.out, exist_ok=True)
    names = a.cases or RATCHET_CASES + list(LATCH_CASES) + ["latch_arp_off"]
    fails, report = [], {}
    for name in names:
        image = os.path.join(a.out, f"{name}.img")
        if name == "latch_arp_off":
            build_latch_card(image, 1, [(60, [(0, 2)])])
            hits, problems, playing, t_off = play(a, image, 2.0, name, (0.8, arp_off))
            late = [h for h in hits if h[0] >= t_off]  # the next render starts a pending note: same timer value
            report[name] = dict(hits_before=len(hits) - len(late), hits_after_arp_off=len(late))
            if len(hits) - len(late) < 4:
                fails.append(f"{name}: the latched note didn't go on before the arp was switched off")
            if late:
                fails.append(f"{name}: (7) {len(late)} note(s) started after the arp was switched off: {late[:4]}")
        elif name in LATCH_CASES:
            latch, rows = LATCH_CASES[name]
            build_latch_card(image, latch, rows)
            hits, problems, playing = play(a, image, 2.6, name)
            check_latch(name, latch, hits, fails, report)
        else:
            arp, params, bars = art.CASES[name]
            art.build_sd(image, arp, params)
            hits, problems, playing = play(a, image, bars * 2 + 0.6, name)
            check_ratchet(name, hits, fails, report)
        report[name]["problems"] = problems
        report[name]["playing"] = bool(playing)
        if problems:
            fails.append(f"{name}: (6) {problems[:3]}")
        print(f"{name}: {json.dumps(report[name])}", flush=True)
    json.dump(report, open(os.path.join(a.out, "arp_extras.json"), "w"), indent=1)
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
