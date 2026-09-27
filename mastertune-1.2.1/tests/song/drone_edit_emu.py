#!/usr/bin/env python3
"""A drone row's Hz lane when its notes are edited, and its pitch from Shift, MIDI and another clip (mastertune-v16), on
the real firmware in the emulator. A drone row's notes are its gate and its expression X is its Hz lane, which the notes
don't carry: whatever the firmware does to a row's MPE when notes are added, recorded, moved or made Euclidean must
leave the lane alone. The review of v16 found each of these rewriting or deleting lanes; this checks them fixed.

Usage: drone_edit_emu.py <deluge.elf> <out dir> [--tools PREFIX] [--build DIR]   (run.sh's DRONE=1 runs it)

The song: one drone track (make_sd.py's drone kit, "DRONEEDIT") with a 2-bar clip open in the clip view (Affect Entire
off, the rows from the bottom of the grid, so a pad's y is its row), and a second clip of the kit, not playing, with
only the kit's first row; the song's drone off. The first clip's rows (base, notes, the lane as cents at positions in
bars):
  0  200 Hz   a note over bar 1               +386 @0, +702 @0.5, +1200 @1.5
  1  400 Hz   no notes                        +500 @0, +1000 @1
  2  a gate row (channel 2), no notes
  3  2500 Hz  a note over the clip            +300 @0, +600 @1
  4  3000 Hz  a note of a quarter bar at 0    +200 @0, +400 @1
  5  4000 Hz  no notes                        +100 @0, +150 @1
  6  1000 Hz  a note to 1/16 before the end   +702 @0, +1200 @1
  7  440 Hz   no notes, no lane
Stopped, on the clip view as the user would:
  - row 3's audition pad held, the vertical encoder pressed and turned +1 (Euclidean, 2 notes): its lane stays
  - row 4's note held and the vertical encoder turned +1 (the note moves up into row 5), then back: both lanes stay
  - a pitch bend a quarter up for row 7 (Kit::receivedPitchBendForDrum(), as a learned MIDI channel gives it): the
    row's pitch goes up by a quarter of its bend range (the default 2 semitones: 50 cents), not by 2400 cents; heard
    with its audition pad held
  - its pad still held, the kit's active clip made the second clip, which has no row for it (as when that clip is
    launched while a MIDI note holds the row: the note-off would go to that clip's rows and never reach it): it stops
Playing (song_emu.Player, as drone_rec_emu.py), recording, with the pads and knobs:
  bar 0.25 REC on; 0.45 Shift + select encoder +30 on row 6 (selected: 0.1 Hz a click, 1503 Hz); 0.5-0.6 row 1's pad
  (its only note); 0.7 REC off; 1.1 REC on; 1.25-1.35 row 0's pad; 1.6 REC off
  played back (bars 2 to 4): row 1's new note at its lane's 534 Hz and row 0's at 300 Hz (a note recorded doesn't set
  the lane to the note's MPE, 0 for a pad), row 6 at 1503 Hz where Shift + select turned it and at 2000 Hz in its
  second bar (the fine step recorded, the lane kept), row 5's moved note at its own lane's 4238 Hz
  bar 6.3 row 0's pad pressed while its note plays and let go at 6.35: only selects it, the row still sounds at 6.6
  bar 3.95 row 6's pad pressed and let go after its note (selects it again); 4.25 REC on; 4.5 Shift + the upper gold
  knob: the lane deleted (as Shift and a gold knob delete automation while recording) and the row back at its own
  1000 Hz, at once and after (bars 6.5 and 7.5), rather than held at the lane's last value; 4.9 REC off
The firmware saves the song (song_emu.write_back_song()): the lanes of rows 0, 1, 3, 4 and 5 as they were, row 6's
gone, the moved note in row 5; the kit's drums with the gate row's first and the drone rows' after it (firmware before
v16 skips <droneTone>, and rows refer to their drums by index: so only the drone rows lose theirs), each row still
referring to its own drum; and the kit counts 7 drone rows (Kit::numDroneDrums, which keeps kits without any from
looking for them every block).
Results: <out>/drone_edit.wav (the output while playing), <out>/saved.xml.
"""
import argparse
import math
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drone_rec_emu as d  # noqa: E402
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402

SR = 44100
BAR = song_emu.BAR  # Samples: 2 s at 120 BPM
TB = make_sd.BAR  # Ticks
BUTTON_Y_ENC = 9 * (0 + 16) + 0  # hid/button.h: fromCartesian(yEncButtonCoord {0, 0})
MATCH_CHANNEL = 1  # MIDIMatchType::CHANNEL
CENTS_PER_VALUE = 9600 / 2 ** 31

ROWS = [  # (hundredths of a hertz or None for the gate row, notes as (start, length) in ticks, lane as (tick, cents))
    (20000, [(0, TB)], [(0, 386), (TB // 2, 702), (3 * TB // 2, 1200)]),
    (40000, [], [(0, 500), (TB, 1000)]),
    (None, [], []),
    (250000, [(0, 2 * TB)], [(0, 300), (TB, 600)]),
    (300000, [(0, TB // 4)], [(0, 200), (TB, 400)]),
    (400000, [], [(0, 100), (TB, 150)]),
    (100000, [(0, 2 * TB - TB // 16)], [(0, 702), (TB, 1200)]),
    (44000, [], []),
]

check = d.check


def song():
    make_sd.DRONE_TRACKS = [("DRONEEDIT", 2, [(hz or 10000, notes, lane) for hz, notes, lane in ROWS])]
    xml = make_sd.song_xml({}, 1, drone_track=True)
    # Row 2 a gate row: its drum a gateOutput in place of the third droneTone
    tones = [m.start() for m in re.finditer(r"\t\t\t\t<droneTone\b", xml)]
    end = xml.index("/>\n", tones[2]) + 3
    xml = xml[:tones[2]] + '\t\t\t\t<gateOutput channel="2" />\n' + xml[end:]
    xml = xml.replace("<instrumentClip", '<instrumentClip\n\t\t\tbeingEdited="1"\n\t\t\taffectEntire="0"', 1)
    xml = xml.replace('yScroll="40"', 'yScroll="0"', 1)
    # The song's drone off (make_sd.py's reference tone, 1000 Hz): only the rows sound
    xml = xml.replace('<tone index="0" active="1"', '<tone index="0" active="0"', 1)
    # The second clip of the kit, not playing, with only the kit's first row
    block = make_sd.global_params_block("kitParams", make_sd.DRONE_KIT_PARAMS, 3)
    second = make_sd.instrument_clip("DRONEEDIT", "KITS", 2 * TB, [("drumIndex", 0, [], None)], params_block=block,
                                     kit=True)
    second = second.replace('isPlaying="1"', 'isPlaying="0"', 1).replace('yScroll="40"', 'yScroll="0"', 1)
    return xml.replace("\t</sessionClips>", second + "\t</sessionClips>", 1)


def lane_of(row_xml):
    """The row's lane as [(position in bars, cents)] and the value it holds, from the saved expressionData"""
    m = re.search(r'pitchBend="0x([0-9A-F]+)"', row_xml)
    if not m:
        return None, []
    data = m.group(1)
    value = struct.unpack("<i", struct.pack("<I", int(data[:8], 16)))[0] * CENTS_PER_VALUE
    nodes = []
    for i in range(8, len(data), 16):
        v = struct.unpack("<i", struct.pack("<I", int(data[i:i + 8], 16)))[0]
        pos = int(data[i + 8:i + 16], 16) & 0x7FFFFFFF
        nodes.append((round(pos / TB, 3), round(v * CENTS_PER_VALUE, 1)))
    return value, nodes


def notes_of(row_xml):
    m = re.search(r'noteData(?:WithLift)?="0x([0-9A-F]+)"', row_xml)
    if not m:
        return []
    data = m.group(1)
    step = 22 if "noteDataWithLift" in row_xml else 20  # Position, length, velocity, (lift,) probability
    return [(int(data[i:i + 8], 16), int(data[i + 8:i + 16], 16)) for i in range(0, len(data), step)]


def same_lane(nodes, lane):
    return len(nodes) == len(lane) and all(abs(p - tick / TB) < 0.002 and abs(c - cents) < 0.5
                                           for (p, c), (tick, cents) in zip(nodes, lane))


class Rig:
    """The emulated Deluge and what the checks reach in it"""

    def __init__(self, emu):
        self.emu = emu
        sym = emu.sym
        self.clip_view = sym["instrumentClipView"]
        self.pad = sym.find("_ZN18InstrumentClipView9padActionElll")
        self.button = sym.find("_ZN7Buttons12buttonActionEhbb")
        self.vertical = sym.find("_ZN18InstrumentClipView21verticalEncoderActionElb")
        self.select = sym.find("_ZN18InstrumentClipView19selectEncoderActionEa")
        self.knob = sym.find("_ZN18InstrumentClipView16modEncoderActionEll")
        self.record = sym.find("_ZN15PlaybackHandler19recordButtonPressedEv")
        self.playback = sym["playbackHandler"]
        self.shift = sym["_ZN7Buttons21shiftCurrentlyPressedE"]
        (self.o_current_clip, self.o_output, self.o_active_clip, self.o_timeline_counter,
         self.o_pitch_offset) = d.member_offsets(emu, [
             ("Song::Song", "(int)&((Song*)0)->currentClip"),
             ("Clip::Clip", "(int)&((Clip*)0)->output"),
             ("Kit::Kit", "(int)&((Kit*)0)->activeClip"),
             ("Kit::Kit", "(int)&((ModelStackWithTimelineCounter*)0)->timelineCounter"),
             ("DroneDrum::DroneDrum", "(int)&((DroneDrum*)0)->pitchOffset"),
         ])
        try:
            (self.o_num_drone_drums,) = d.member_offsets(emu, [("Kit::Kit", "(int)&((Kit*)0)->numDroneDrums")])
        except SystemExit:
            self.o_num_drone_drums = None  # A build from before the fixes

    def call(self, function, *args):
        d.drain_pic(self.emu)
        result = self.emu.call(function, *args)
        d.drain_pic(self.emu)
        return result

    def call_with_stack(self, function, *args):
        """A call with more than four arguments: the rest on the stack (AAPCS)"""
        emu = self.emu
        sp = song_emu.PROGRAM_STACK_TOP - 0x100
        for i, value in enumerate(args[4:]):
            emu.w32(sp + 4 * i, value)
        from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                       UC_ARM_REG_SP)
        d.drain_pic(emu)
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            emu.uc.reg_write(reg, value & 0xFFFFFFFF)
        emu.uc.reg_write(UC_ARM_REG_SP, sp)
        emu.uc.reg_write(UC_ARM_REG_LR, song_emu.STOP | 1)
        emu.run(function | 1, song_emu.STOP)
        d.drain_pic(emu)

    def with_shift(self, function, *args):
        self.emu.uc.mem_write(self.shift, b"\x01")
        self.call(function, *args)
        self.emu.uc.mem_write(self.shift, b"\x00")

    def clip(self):
        return self.emu.u32(self.emu.u32(self.emu.sym["currentSong"]) + self.o_current_clip)

    def kit(self):
        return self.emu.u32(self.clip() + self.o_output)

    def drum(self, index):
        return self.emu.call(self.emu.sym["_ZN3Kit16getDrumFromIndexEl"], self.kit(), index)

    def signed(self, address):
        return struct.unpack("<i", struct.pack("<I", self.emu.u32(address)))[0]


def play(player, bars):
    out = []
    total = 0
    while total < int(bars * BAR):
        w = player.window()
        total += w[1]
        out.append(w[4])
    return np.concatenate(out)[:, 0]


def hz_of(x, lo, hi):
    return d.peak_hz(x, lo, hi)


def stopped_edits(rig, player):
    emu = rig.emu
    print("== stopped: Euclidean on row 3, row 4's note moved into row 5", flush=True)
    rig.call(rig.pad, rig.clip_view, d.AUDITION_X, 3, 64)
    rig.call(rig.button, BUTTON_Y_ENC, 1, 0)
    rig.call(rig.vertical, rig.clip_view, 1, 0)
    rig.call(rig.button, BUTTON_Y_ENC, 0, 0)
    rig.call(rig.pad, rig.clip_view, d.AUDITION_X, 3, 0)
    rig.call(rig.pad, rig.clip_view, 0, 4, 64)  # The note at step 0
    rig.call(rig.vertical, rig.clip_view, 1, 0)  # The view scrolls up a row, the note held with it: into row 5
    rig.call(rig.pad, rig.clip_view, 0, 4, 0)
    rig.call(rig.vertical, rig.clip_view, -1, 0)  # And back: pad y is row again

    print("== stopped: a pitch bend a quarter up for row 7 (a learned channel), then its pad held", flush=True)
    bend_range = emu.u8(emu.sym["_ZN12FlashStorage16defaultBendRangeE"])
    memory = song_emu.STOP + 0x800
    emu.w32(memory, emu.u32(emu.sym["currentSong"]))
    emu.w32(memory + rig.o_timeline_counter, rig.clip())
    drum = rig.drum(7)
    bend = rig.emu.sym["_ZN3Kit24receivedPitchBendForDrumEP29ModelStackWithTimelineCounterP4Drumhh13MIDIMatchTypehPb"]
    value = 8192 + 2048  # A quarter of the way up
    rig.call_with_stack(bend, rig.kit(), memory, drum, value & 0x7F, value >> 7, MATCH_CHANNEL, 0, memory + 0x40)
    cents = rig.signed(drum + rig.o_pitch_offset) * CENTS_PER_VALUE
    want = 2400 * min(bend_range, 96) / 96
    check(f"the bend within the row's bend range ({bend_range} semitones): +{want:.0f} cents",
          abs(cents - want) < 0.5, f"{cents:+.1f} cents (unscaled it would be +2400)")
    rig.call(rig.pad, rig.clip_view, d.AUDITION_X, 7, 64)
    x = play(player, 0.25)
    hz_want = 440 * 2 ** (want / 1200)
    got = hz_of(x[-4096:], 300, 2000)
    check(f"heard with its pad held: {hz_want:.1f} Hz", abs(got - hz_want) < 0.3, f"{got:.2f} Hz")
    level = d.amplitude(x[-4096:], got)

    kit = rig.kit()
    clip_a = rig.emu.u32(kit + rig.o_active_clip)
    clip_b = rig.call(rig.emu.sym["_ZN4Song17getClipWithOutputEP6OutputbP4Clip"], rig.emu.u32(rig.emu.sym["currentSong"]),
                      kit, 0, clip_a)
    if not clip_b or clip_b == clip_a:
        check("the second clip of the kit", False, f"{clip_b:#x}")
    else:
        emu.w32(kit + rig.o_active_clip, clip_b)
        x = play(player, 0.1)
        after = d.amplitude(x[-4096:], got)
        emu.w32(kit + rig.o_active_clip, clip_a)
        db = 20 * math.log10(after / level + 1e-12)
        check("the kit's active clip made one without the row, its pad still held: the row stops", db < -60,
              f"{db:+.1f} dB against before")
    rig.call(rig.pad, rig.clip_view, d.AUDITION_X, 7, 0)
    value = 8192  # The wheel back in the middle
    rig.call_with_stack(bend, rig.kit(), memory, drum, value & 0x7F, value >> 7, MATCH_CHANNEL, 0, memory + 0x40)


def recording(rig, player, out_dir):
    print("== playing: notes recorded on rows with lanes, Shift + select and Shift + knob while recording", flush=True)
    rig.call(rig.pad, rig.clip_view, d.AUDITION_X, 6, 64)  # Row 6 selected
    rig.call(rig.pad, rig.clip_view, d.AUDITION_X, 6, 0)
    record = (rig.call, rig.record, rig.playback)
    actions = [
        (0.25, record),
        (0.45, (rig.with_shift, rig.select, rig.clip_view, 30)),
        (0.5, (rig.call, rig.pad, rig.clip_view, d.AUDITION_X, 1, 64)),
        (0.6, (rig.call, rig.pad, rig.clip_view, d.AUDITION_X, 1, 0)),
        (0.7, record),
        (1.1, record),
        (1.25, (rig.call, rig.pad, rig.clip_view, d.AUDITION_X, 0, 64)),
        (1.35, (rig.call, rig.pad, rig.clip_view, d.AUDITION_X, 0, 0)),
        (1.6, record),
        # Row 6 selected again where its note has ended: pressed while its note plays, the pad's "silent" audition
        # (a note-on and at once a note-off, InstrumentClipView::startAuditioningRow()) would close its gate until
        # the next note
        (3.95, (rig.call, rig.pad, rig.clip_view, d.AUDITION_X, 6, 64)),
        (3.97, (rig.call, rig.pad, rig.clip_view, d.AUDITION_X, 6, 0)),
        (4.25, record),
        (4.5, (rig.with_shift, rig.knob, rig.clip_view, 1, 1)),
        (4.9, record),
        # Row 0's pad pressed while its note plays (bar 1 of the clip: song bars 6 to 7): only selects it, the row
        # keeps sounding (mastertune-v16: its one gate isn't opened and closed by the silent audition)
        (6.3, (rig.call, rig.pad, rig.clip_view, d.AUDITION_X, 0, 64)),
        (6.35, (rig.call, rig.pad, rig.clip_view, d.AUDITION_X, 0, 0)),
    ]
    player.start()
    out = []
    done = 0
    total = 0
    while total < int(8.0 * BAR):
        while done < len(actions) and total >= actions[done][0] * BAR:
            f, *args = actions[done][1]
            f(*args)
            done += 1
        w = player.window()
        total += w[1]
        out.append(w[4])
    x = np.concatenate(out)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with open(os.path.join(out_dir, "drone_edit.wav"), "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, 1, 2, SR, SR * 4, 4, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)
    left = x[:, 0]

    def hz_at(bar, lo, hi):
        i = int(bar * BAR) - 2048
        return hz_of(left[i:i + 4096], lo, hi)

    expect = [
        (2.55, 450, 650, 400 * 2 ** (500 / 1200), "row 1's recorded note (its only one) at its lane's pitch"),
        (3.3, 150, 340, 300.0, "row 0's recorded note at its lane's pitch (not the tone's own 200 Hz)"),
        (2.65, 1400, 1700, 1503.0, "row 6 where Shift + select turned it while recording: 0.1 Hz a click"),
        (3.5, 1400, 2200, 2000.0, "row 6's lane kept past it (not held at 1500 Hz)"),
        (2.12, 4100, 4700, 4000 * 2 ** (100 / 1200), "row 5's moved note at row 5's own lane (not row 4's)"),
        (4.75, 900, 1700, 1000.0, "row 6 at its own pitch once Shift + knob deleted its lane while recording"),
        (6.5, 900, 1700, 1000.0, "and played back without the lane"),
        (7.5, 900, 2200, 1000.0, "and in its second bar"),
        (6.6, 250, 340, 200 * 2 ** (702 / 1200), "row 0 still sounding after its pad was pressed during its note"),
    ]
    for bar, lo, hi, hz, what in expect:
        got = hz_at(bar, lo, hi)
        check(f"bar {bar}: {hz:.1f} Hz, {what}", abs(got - hz) < 0.3, f"{got:.2f} Hz")
    d.drain_pic(rig.emu)
    rig.emu.call(rig.emu.sym.find("_ZN15PlaybackHandler11endPlaybackEv"))
    d.drain_pic(rig.emu)


def saved(rig, out_dir):
    print("== saved by the firmware", flush=True)
    song_emu.write_back_song(rig.emu, os.path.join(out_dir, "saved.xml"))
    xml = open(os.path.join(out_dir, "saved.xml"), encoding="utf-8", errors="replace").read()
    kit = re.search(r'<kit\b[^>]*presetName="DRONEEDIT".*?</kit>', xml, re.S)
    sources = re.findall(r"<(droneTone|gateOutput|midiOutput|sound|sample|synth)\b([^>]*)", kit.group(0)) if kit else []
    kinds = [kind for kind, _ in sources]
    clips = re.findall(r'<instrumentClip\b[^>]*instrumentPresetName="DRONEEDIT".*?</instrumentClip>', xml, re.S)
    clip = max(clips, key=lambda c: c.count("<noteRow")) if clips else ""
    rows = re.findall(r"<noteRow\b.*?(?:/>|</noteRow>)", clip, re.S)
    indices = [int(m.group(1)) if (m := re.search(r'drumIndex="(\d+)"', r)) else -1 for r in rows]
    first_tone = kinds.index("droneTone") if "droneTone" in kinds else len(kinds)
    check("the kit's drums: the gate row's first, the drone rows' after it (older firmware skips droneTone)",
          kinds == ["gateOutput"] + ["droneTone"] * 7, " ".join(kinds))

    def frequency(i):
        if not 0 <= i < len(sources):
            return "?"
        kind, attributes = sources[i]
        m = re.search(r'frequency="(\d+)"', attributes)
        return "gate" if kind == "gateOutput" else int(m.group(1)) if m else kind

    want = [hz if hz is not None else "gate" for hz, _, _ in ROWS]
    got = [frequency(i) for i in indices]
    check("each row still refers to its own drum, in the rows' order", got == want, f"{got}")
    old = [i < first_tone for i, (hz, _, _) in zip(indices, ROWS) if hz is None]
    check("the gate row's drum comes before any drone row's (kept by firmware before v16)", old == [True],
          f"drumIndex {indices[2] if len(indices) > 2 else None}, first droneTone {first_tone}")
    if len(rows) != len(ROWS):
        check("the clip's rows", False, f"{len(rows)} rows")
        return
    lanes = {i: lane_of(rows[i]) for i in range(len(rows))}
    for i in (0, 1, 3, 4, 5):
        check(f"row {i}'s lane as it was", same_lane(lanes[i][1], ROWS[i][2]), f"{lanes[i][1]}")
    value, nodes = lanes[6]
    check("row 6: no lane, and no offset left from it", not nodes and (value is None or abs(value) < 0.01),
          f"{nodes}, value {value}")
    counts = {i: len(notes_of(rows[i])) for i in (0, 1, 3, 4, 5)}
    check("the notes: row 0 two (one recorded), row 1 one (recorded), row 3 two (Euclidean), row 4 none, row 5 one "
          "(moved there)", counts == {0: 2, 1: 1, 3: 2, 4: 0, 5: 1}, f"{counts}")
    num = rig.emu.u32(rig.kit() + rig.o_num_drone_drums) if rig.o_num_drone_drums is not None else None
    check("the kit counts 7 drone rows (Kit::numDroneDrums)", num == 7, f"{num}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("out")
    ap.add_argument("--tools")
    ap.add_argument("--build", default=HERE)
    args = ap.parse_args()
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(args.out, exist_ok=True)
    sd = os.path.join(args.out, "sd.img")
    xml = song()
    open(os.path.join(args.out, "song.xml"), "w").write(xml)
    fat32.build(sd, {"SONGS/DEFAULT.XML": xml.encode()})
    emu = song_emu.Emulator(args.elf, sd, tools, args.build,
                            lambda s: print(s, flush=True) if os.environ.get("EMU_DEBUG") else None)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    emu.w32(emu.sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    rig = Rig(emu)
    root = emu.call(emu.sym["_Z9getRootUIv"])
    check("the song opens in the drone kit's clip view", root == rig.clip_view, f"root UI {root:#x}")
    player = song_emu.Player(emu)
    stopped_edits(rig, player)
    recording(rig, player, args.out)
    saved(rig, args.out)
    os.remove(sd)
    result = "all checks passed" if not d.failures else f"{d.failures} failed"
    print(f"a drone row's lane under note edits, Shift, MIDI and another clip: {result}", flush=True)
    sys.exit(1 if d.failures else 0)


if __name__ == "__main__":
    main()
