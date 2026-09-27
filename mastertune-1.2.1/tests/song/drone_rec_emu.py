#!/usr/bin/env python3
"""REC on a drone track (mastertune-v16) on the real firmware in the emulator: turning a drone row's pitch while the
song records writes its Hz lane, and the lane plays back what was turned; turned while not recording, the row's own
frequency moves, and the lane with it.

Usage: drone_rec_emu.py <deluge.elf> <out dir> [--tools PREFIX] [--build DIR]   (run.sh's DRONE=1 runs it)

The song: one drone track (make_sd.py's drone kit, "DRONEREC"), a 2-bar clip with one 200 Hz row whose note is as
long as the clip, no lane, the clip armed for recording and open in the clip view (beingEdited), Affect Entire off, the
row selected. Played from the start with the internal clock (song_emu.Player: one AudioEngine::routine() window at a
time, then the playback handler's routine), and between windows, as the user would, on the clip view:
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
recording, as for any automation recorded with a gold knob.) Then, stopped,
the firmware saves the song (song_emu.write_back_song()): the row's droneTone has frequency 30000 and its noteRow a
pitchBend lane whose nodes hold 250, 260 and 220 Hz as cents from 200 Hz (+386, +454, +165).
Results: <out>/drone_rec.wav (the output), <out>/saved.xml.
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
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402

SR = 44100
BAR = song_emu.BAR  # Samples: 2 s at 120 BPM
TICKS_PER_BAR = make_sd.BAR

failures = 0


def check(what, ok, detail=""):
    global failures
    failures += not ok
    print(f"  [{'ok' if ok else 'FAIL'}] {what}" + (f": {detail}" if detail else ""), flush=True)


def song():
    make_sd.DRONE_TRACKS = [("DRONEREC", 2, [(20000, [(0, 2 * TICKS_PER_BAR)], [])])]
    make_sd.drone_reference = lambda: ""
    xml = make_sd.song_xml({}, 1, drone_track=True)
    xml = xml.replace("<instrumentClip", '<instrumentClip\n\t\t\tbeingEdited="1"\n\t\t\taffectEntire="0"', 1)
    return xml


def drain_pic(emu):
    """No transfer-end interrupt here: whatever the firmware put in the PIC's UART ring counts as sent (as
    tests/songchange does; the pads' and LEDs' updates would otherwise fill it and the firmware wait on it)"""
    item = emu.sym["uartItems"]
    w = struct.unpack("<H", emu.uc.mem_read(item, 2))[0]
    emu.uc.mem_write(item + 2, struct.pack("<HHBB", w, w, 1, 0))


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
    os.remove(sd)
    print(f"REC on a drone track: {'all checks passed' if not failures else f'{failures} failed'}", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
