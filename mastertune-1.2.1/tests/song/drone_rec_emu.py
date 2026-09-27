#!/usr/bin/env python3
"""REC on a drone track (mastertune-v16) on the real firmware in the emulator: turning a drone row's pitch while the
song records writes its Hz lane, and the lane plays back what was turned; turned while not recording, the row's own
frequency moves, and the lane with it. Then Select's menu on the row, kit rows made drone rows, and a drone track made
from the drone.

Usage: drone_rec_emu.py <deluge.elf> <out dir> [--tools PREFIX] [--build DIR]   (run.sh's DRONE=1 runs it)

The song: one drone track (make_sd.py's drone kit, "DRONEREC"), a 2-bar clip with one 200 Hz row whose note is as
long as the clip, no lane, and above it a gate row without notes; the clip armed for recording and open in the clip
view (beingEdited), Affect Entire off, the drone row selected; and the drone with one tone, 1000 Hz (make_sd.py
--drone-track's reference). Played from the start with the internal clock (song_emu.Player: one AudioEngine::routine()
window at a time, then the playback handler's routine), and between windows, as the user would, on the clip view:
  bar 0.25   REC on (PlaybackHandler::recordButtonPressed())
  bar 0.5    the select encoder +50 clicks (InstrumentClipView::selectEncoderAction()): 1 Hz each, 250 Hz
  bar 1.25   the upper gold knob +10 (InstrumentClipView::modEncoderAction(1, 10)): 260 Hz
  bar 1.5    the select encoder -40: 220 Hz
  bar 1.9    REC off
  bar 4.1    (not recording) the select encoder +100: the row's own frequency 200 -> 300 Hz, the lane 1.5 times higher
Checked from the output (renderingBuffer, as song_emu's measured.wav; the windowed DFT's peak over 93 ms windows well
inside each stretch): while recording, each turn sounds at once (bar 0.75: 250 Hz, 1.4: 260, 1.75: 220); the next
loop (bars 2 to 4, REC off) plays the lane back: 250, 260 and 220 Hz from where they were turned; after the frequency
change, the lane where nothing was recorded (before the first turn) is the row's own 300 Hz, and the recorded part
1.5 times higher (375 and 390 Hz). (The last turn's value holds 0.2 s past REC off, then the lane is as before the
recording, as for any automation recorded with a gold knob.)
Then, stopped: Select pressed on the clip view (SoundEditor::setup(), as InstrumentClipMinder calls it) gives the
drone's tone menu for the row. Kit rows made drone rows, through the pads and buttons as the user presses them
(InstrumentClipView::padAction(), Buttons::buttonAction()): the gate row's audition pad held, Shift + Kit, and the pad
of the empty row above the kit's held (a row being added), Shift + Kit: the clip view stays (Kit alone opens the
row's sample browser), and each new drone row sounds its tone (200 Hz, a new drone row's) while its pad is held, at
the drone's level (within 1 dB). The firmware saves the song (song_emu.write_back_song()): the row's droneTone has
frequency 30000 and its noteRow a pitchBend lane whose nodes hold 250, 260 and 220 Hz as cents from 200 Hz (+386,
+454, +165), and the clip's three rows are drone rows.
Then a drone track made from the drone (the drone view's Shift + Kit, DroneView::buttonAction()): a clip with a new
kit (DRONE1) comes into Song view; launched, with the drone's tone off, it plays the tone at the drone's level (within
1 dB, against the drone's 1000 Hz measured above), and saved, the kit has 16 drone rows, the clip a note on the one
row whose tone was on, and that row is the kit's selected one (so the select encoder turns its pitch).
Results: <out>/drone_rec.wav (the output), <out>/saved.xml, <out>/saved_made.xml.
"""
import argparse
import math
import os
import re
import struct
import subprocess
import sys

import numpy as np
from unicorn.arm_const import UC_ARM_REG_R0

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402

SR = 44100
BAR = song_emu.BAR  # Samples: 2 s at 120 BPM
TICKS_PER_BAR = make_sd.BAR
BUTTON_KIT = 9 * (1 + 16) + 5  # hid/button.h: fromCartesian(kitButtonCoord {5, 1})
BUTTON_SHIFT = 9 * (0 + 16) + 8  # fromCartesian(shiftButtonCoord {8, 0})
AUDITION_X = 16 + 1  # The audition pads' column: kDisplayWidth + 1

failures = 0


def check(what, ok, detail=""):
    global failures
    failures += not ok
    print(f"  [{'ok' if ok else 'FAIL'}] {what}" + (f": {detail}" if detail else ""), flush=True)


def song():
    make_sd.DRONE_TRACKS = [("DRONEREC", 2, [(20000, [(0, 2 * TICKS_PER_BAR)], [])])]
    xml = make_sd.song_xml({}, 1, drone_track=True)
    xml = xml.replace("<instrumentClip", '<instrumentClip\n\t\t\tbeingEdited="1"\n\t\t\taffectEntire="0"', 1)
    # A gate row above the drone row, without notes: made a drone row later (rows_made_drone()). The rows from the
    # bottom of the grid (yScroll 0), so the pads' y is the row's index.
    xml = xml.replace("\t\t\t</soundSources>", '\t\t\t\t<gateOutput channel="2" />\n\t\t\t</soundSources>', 1)
    xml = xml.replace("\t\t\t</noteRows>", '\t\t\t\t<noteRow drumIndex="1" />\n\t\t\t</noteRows>', 1)
    xml = xml.replace('yScroll="40"', 'yScroll="0"', 1)
    return xml


def drain_pic(emu):
    """No transfer-end interrupt here: whatever the firmware put in the PIC's UART ring counts as sent (as
    tests/songchange does; the pads' and LEDs' updates would otherwise fill it and the firmware wait on it)"""
    item = emu.sym["uartItems"]
    w = struct.unpack("<H", emu.uc.mem_read(item, 2))[0]
    emu.uc.mem_write(item + 2, struct.pack("<HHBB", w, w, 1, 0))


def amplitude(x, hz):
    w = np.blackman(len(x))
    return 2 * abs(np.sum(x * w * np.exp(-2j * np.pi * hz * np.arange(len(x)) / SR))) / w.sum()


def member_offsets(emu, queries):
    """Offsets of members from the ELF's debug info (the toolchain's gdb), each query as (context, expression)"""
    args = []
    for context, expression in queries:
        args += ["-ex", f"list {context}", "-ex", f"print {expression}"]
    out = subprocess.run([emu.tool_prefix + "gdb", "-batch", *args, emu.elf], capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (-?\d+)$", out, re.M)]
    if len(values) != len(queries):
        raise SystemExit(f"gdb: {queries}:\n{out[-500:]}")
    return values


def menu_on_row(emu):
    """Select pressed on the clip view with the drone row selected: InstrumentClipMinder's SoundEditor::setup(clip)
    gives the drone's tone menu for that row (droneTrackToneMenu, drone::trackTone at the row's tone)"""
    sym = emu.sym
    (clip_offset,) = member_offsets(emu, [("Song::Song", "(int)&((Song*)0)->currentClip")])
    clip = emu.u32(emu.u32(sym["currentSong"]) + clip_offset)
    drain_pic(emu)
    ok = emu.call(sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], sym["soundEditor"], clip, 0, 0) & 0xFF
    drain_pic(emu)
    tone = emu.u32(sym["_ZN6deluge3gui9menu_item5drone9trackToneE"])
    frequency = emu.u32(tone + 12) if tone else None  # Tone: active, mode, timbre, byNote, note, cents, frequency
    start, size = sym.by_name["soundEditor"]
    held = struct.pack("<I", sym["droneTrackToneMenu"]) in bytes(emu.uc.mem_read(start, size))
    check("Select on the drone row: the tone menu for that row", ok and held and frequency == 30000,
          f"setup {ok}, the menu {'there' if held else 'not there'}, its tone at {frequency}")


def rows_made_drone(emu, player, drone_level):
    """Stopped, on the clip view: a row's audition pad held, Shift + Kit (InstrumentClipView::buttonAction()) makes it
    a drone row: the gate row (y 1), then a row being added (the empty row above the kit's, y 2). Each sounds its
    tone (a new drone row's 200 Hz) while its pad is held, at the drone's level; the clip view stays (Kit alone would
    open the row's sample browser)"""
    print("== kit rows made drone rows: audition pad held, Shift + Kit", flush=True)
    sym = emu.sym
    pad = sym.find("_ZN18InstrumentClipView9padActionElll")
    button = sym.find("_ZN7Buttons12buttonActionEhbb")
    clip_view = sym["instrumentClipView"]
    for y, what in ((1, "the gate row"), (2, "a row being added above the kit's")):
        drain_pic(emu)
        emu.call(pad, clip_view, AUDITION_X, y, 64)
        drain_pic(emu)
        emu.call(button, BUTTON_SHIFT, 1, 0)
        emu.call(button, BUTTON_KIT, 1, 0)
        drain_pic(emu)
        ui = emu.call(sym["_Z12getCurrentUIv"])
        emu.call(button, BUTTON_KIT, 0, 0)
        emu.call(button, BUTTON_SHIFT, 0, 0)
        drain_pic(emu)
        out = []
        total = 0
        while total < int(0.25 * BAR):  # Its pad still held
            w = player.window()
            total += w[1]
            out.append(w[4])
        left = np.concatenate(out)[:, 0][-16384:]
        hz = peak_hz(left, 150, 250)
        db = 20 * math.log10(amplitude(left, 200) / drone_level + 1e-12)
        drain_pic(emu)
        emu.call(pad, clip_view, AUDITION_X, y, 0)
        drain_pic(emu)
        check(f"{what}: Shift + Kit made it a drone row, sounding its 200 Hz at the drone's level while held",
              ui == clip_view and abs(hz - 200) < 0.3 and abs(db) < 1,
              f"{hz:.2f} Hz, {db:+.2f} dB, {'the clip view' if ui == clip_view else f'another UI {ui:#x}'}")


def made_from_the_drone(emu, player, out_dir, drone_level):
    """The drone view's Shift + Kit (DroneView::buttonAction()): a drone track of the drone, then its clip launched and
    the drone's tone off, so the 1000 Hz heard is the track's"""
    print("== a drone track made from the drone (drone view, Shift + Kit), launched, the drone's tone off", flush=True)
    sym = emu.sym
    drone_offset, active_offset = member_offsets(emu, [("Song::Song", "(int)&((Song*)0)->drone"),
                                                       ("Clip::Clip", "(int)&((Clip*)0)->activeIfNoSolo")])
    new_clips = []
    emu.intercept(sym["_ZN14InstrumentClip28setupAsNewKitClipIfNecessaryEP29ModelStackWithTimelineCounter"],
                  lambda e: new_clips.append(e.uc.reg_read(UC_ARM_REG_R0)))
    emu.uc.ctl_flush_tb()  # Code translated before the hook
    shift = sym["_ZN7Buttons21shiftCurrentlyPressedE"]
    emu.uc.mem_write(shift, b"\x01")
    drain_pic(emu)
    emu.call(sym["_ZN9DroneView12buttonActionEhbb"], sym["droneView"], BUTTON_KIT, 1, 0)
    drain_pic(emu)
    emu.uc.mem_write(shift, b"\x00")
    check("Shift + Kit made a clip with a new kit", len(new_clips) == 1, f"{len(new_clips)} clip(s)")
    if len(new_clips) != 1:
        return
    song = emu.u32(sym["currentSong"])
    emu.uc.mem_write(song + drone_offset, b"\x00")  # drone.tones[0].active
    emu.uc.mem_write(new_clips[0] + active_offset, b"\x01")  # The clip launched (activeIfNoSolo)
    player.start()
    out = []
    total = 0
    while total < int(1.5 * BAR):
        w = player.window()
        total += w[1]
        out.append(w[4])
    left = np.concatenate(out)[:, 0]
    i = int(1.0 * BAR)
    level = amplitude(left[i:i + 16384], 1000)
    db = 20 * math.log10(level / drone_level)
    check("its row plays the drone's tone at the drone's level (1000 Hz, within 1 dB)", abs(db) < 1, f"{db:+.2f} dB")
    drain_pic(emu)
    emu.call(sym.find("_ZN15PlaybackHandler11endPlaybackEv"))
    drain_pic(emu)
    song_emu.write_back_song(emu, os.path.join(out_dir, "saved_made.xml"))
    xml = open(os.path.join(out_dir, "saved_made.xml"), encoding="utf-8", errors="replace").read()
    kit = re.search(r'<kit\b[^>]*presetName="DRONE1".*?</kit>', xml, re.S)
    tones = re.findall(r"<droneTone\b", kit.group(0)) if kit else []
    clip = re.search(r'<instrumentClip\b[^>]*instrumentPresetName="DRONE1".*?</instrumentClip>', xml, re.S)
    rows = re.findall(r'noteData(?:WithLift)?="0x([0-9A-F]+)"', clip.group(0)) if clip else []
    check("saved: the kit DRONE1 with 16 drone rows, its clip with one note (the drone's one tone on)",
          len(tones) == 16 and len(rows) == 1, f"{len(tones)} rows, notes in {len(rows)} row(s)")
    selected = re.search(r"<selectedDrumIndex>(\d+)</selectedDrumIndex>", kit.group(0)) if kit else None
    with_note = re.findall(r'<noteRow\b[^>]*?noteData(?:WithLift)?="0x[0-9A-F]+"[^>]*?drumIndex="(\d+)"',
                           clip.group(0), re.S) if clip else []
    check("saved: that row is the kit's selected one", selected is not None and with_note == [selected.group(1)],
          f"selected {selected.group(1) if selected else None}, the note's row {with_note}")


def peak_hz(x, lo, hi):
    n = len(x)
    w = np.blackman(n)
    m = np.abs(np.fft.rfft(x * w))
    a, b = int(lo * n / SR), int(hi * n / SR)
    i = a + int(np.argmax(m[a:b + 1]))
    y0, y1, y2 = np.log(m[i - 1]), np.log(m[i]), np.log(m[i + 1])
    return (i + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2)) * SR / n


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
    fat32.build(sd, {"SONGS/DEFAULT.XML": song().encode()})

    emu = song_emu.Emulator(args.elf, sd, tools, args.build,
                            lambda s: print(s, flush=True) if os.environ.get("EMU_DEBUG") else None)
    sym = emu.sym
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    emu.w32(sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    print("== REC on a drone track: turning its pitch while recording, the lane played back, then its own pitch",
          flush=True)
    clip_view = sym["instrumentClipView"]
    root = emu.call(sym["_Z9getRootUIv"])
    check("the song opens in the drone kit's clip view", root == clip_view, f"root UI {root:#x}")

    player = song_emu.Player(emu)
    select = sym.find("_ZN18InstrumentClipView19selectEncoderActionEa")
    knob = sym.find("_ZN18InstrumentClipView16modEncoderActionEll")
    record = sym.find("_ZN15PlaybackHandler19recordButtonPressedEv")
    playback = sym["playbackHandler"]
    actions = [  # (bar, what, function, arguments)
        (0.25, "REC on", record, (playback,)),
        (0.5, "select +50", select, (clip_view, 50)),
        (1.25, "upper gold knob +10", knob, (clip_view, 1, 10)),
        (1.5, "select -40", select, (clip_view, -40)),
        (1.9, "REC off", record, (playback,)),
        (4.1, "select +100 (not recording)", select, (clip_view, 100)),
    ]
    player.start()
    out = []
    done = 0
    total = 0
    while total < int(5.5 * BAR):
        while done < len(actions) and total >= actions[done][0] * BAR:
            bar, what, function, call_args = actions[done]
            drain_pic(emu)
            emu.call(function, *call_args, timeout_s=5 if os.environ.get("EMU_DEBUG") else 0)
            drain_pic(emu)
            done += 1
        w = player.window()
        total += w[1]
        out.append(w[4])
    x = np.concatenate(out)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with open(os.path.join(args.out, "drone_rec.wav"), "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, 1, 2, SR, SR * 4, 4, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)
    left = x[:, 0]

    def hz_at(bar):
        i = int(bar * BAR) - 2048
        return peak_hz(left[i:i + 4096], 150, 450)

    expect = [(0.1, 200, "before any turn"), (0.75, 250, "recording: select +50"), (1.4, 260, "recording: knob +10"),
              (1.75, 220, "recording: select -40"),
              (2.75, 250, "played back"), (3.4, 260, "played back"), (3.75, 220, "played back"),
              (4.3, 300, "the lane before its first node: the row's own frequency, now 300 Hz"),
              (4.75, 375, "the lane 1.5 times higher with it"), (5.4, 390, "and the knob's node")]
    for bar, hz, what in expect:
        got = hz_at(bar)
        check(f"bar {bar}: {hz} Hz ({what})", abs(got - hz) < 0.3, f"{got:.2f} Hz")

    # Stopped first: loading while playing would wait for the swap at a launch, which the task manager would do
    drain_pic(emu)
    emu.call(sym.find("_ZN15PlaybackHandler11endPlaybackEv"))
    drain_pic(emu)
    drone_level = amplitude(left[int(0.5 * BAR):int(0.5 * BAR) + 16384], 1000)
    menu_on_row(emu)
    rows_made_drone(emu, player, drone_level)
    song_emu.write_back_song(emu, os.path.join(args.out, "saved.xml"))
    xml = open(os.path.join(args.out, "saved.xml"), encoding="utf-8", errors="replace").read()
    frequency = re.search(r'<droneTone\b[^>]*?frequency="(\d+)"', xml, re.S)
    check("saved: the row's own frequency 300 Hz", frequency and frequency.group(1) == "30000",
          frequency.group(1) if frequency else "no droneTone")
    lane = re.search(r'<expressionData\s+pitchBend="0x([0-9A-F]+)"', xml)
    cents = []
    if lane:
        data = lane.group(1)[8:]
        for i in range(0, len(data), 16):
            value = struct.unpack("<i", struct.pack("<I", int(data[i:i + 8], 16)))[0]
            pos = int(data[i + 8:i + 16], 16) & 0x7FFFFFFF
            cents.append((pos, round(value * 9600 / 2 ** 31, 1)))
    wanted = [1200 * math.log2(f / 200) for f in (250, 260, 220)]
    found = all(any(abs(c - w) < 0.5 for _, c in cents) for w in wanted)
    check("saved: the lane's nodes hold 250, 260 and 220 Hz as cents from 200 Hz",
          lane is not None and found, " ".join(f"{p}:{c:+}" for p, c in cents))
    kit = re.search(r'<kit\b[^>]*presetName="DRONEREC".*?</kit>', xml, re.S)
    sources = re.findall(r"<(droneTone|gateOutput|midiOutput|sound|sample|synth)\b", kit.group(0)) if kit else []
    clip = re.search(r'<instrumentClip\b[^>]*instrumentPresetName="DRONEREC".*?</instrumentClip>', xml, re.S)
    indices = [int(i) for i in re.findall(r'<noteRow\b[^>]*?drumIndex="(\d+)"', clip.group(0), re.S)] if clip else []
    kinds = [sources[i] if i < len(sources) else "?" for i in indices]
    check("saved: the clip's three rows are drone rows (the gate row made one, and the row added)",
          kinds == ["droneTone"] * 3, " ".join(kinds))
    made_from_the_drone(emu, player, args.out, drone_level)
    os.remove(sd)
    result = "all checks passed" if not failures else f"{failures} failed"
    print(f"REC on a drone track and one made from the drone: {result}", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
