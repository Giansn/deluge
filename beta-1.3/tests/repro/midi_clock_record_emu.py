#!/usr/bin/env python3
"""Recording a parameter just after external clock ticks that arrived together, on the v1.3 beta (62a516c2): E427.
On an external clock, PlaybackHandler measures the time per tick from the input ticks' arrival times: a moving average
over the ticks counted since the start. Ticks that arrive together measure no time (the firmware's own comment: Logic,
and the Deluge itself, send several clocks at once after a continue, for a finer start position; the messages of one USB
packet share one arrival time, the packet's). After a continue followed by such clocks, the average is 0 until the next
tick. A parameter recorded meanwhile (the clip armed, RECORD on) divides by it:
AutoParam::setCurrentValueInResponseToUserInput()'s ticks to clear, SAMPLES_TO_CLEAR_AFTER_RECORD / 0, which the ARM
runtime returns as 0xFFFFFFFF, is -1, and homogenizeRegion() freezes with E427. The fuzzer's MIDI round 2 (seed 1331,
--mode deep, --midi 0.35, on the fifth delivered build) found it with a parameter from the input MIDI channel
(MelodicInstrument::processParamFromInputMIDIChannel()) recorded at position 155 for -1 ticks; a gold knob does the
same.

One boot of the real firmware in the emulator (OLED), make_card's song, MIDI follow on channel 1 (fuzz_ui.MidiIn: the
bytes through the MIDI UART, the UART's timing capture cleared: the bytes read in one go count at one time, as the
messages of one USB packet do). In session view RECORD held and the first Clip's status pad pressed (the Clip armed for
recording); the Clip entered, RECORD on; a MIDI continue, six clocks and a pitch bend in one go (playback on the
external clock, recording, the bend recorded while only the bunched ticks are counted); a gold knob turned (recorded
too); then 32 clocks at 120 BPM, a pitch bend, a stop. Checked: the Clip armed, playback on the external clock with
recording, a time per tick other than 0 after the bunched clocks, the time per tick measured from the clocks that
followed (120 BPM: about 230 samples per internal tick), the messages parsed, and no freeze, crash or access outside the
mapped memory.

Usage: midi_clock_record_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot, Inputs and Song; puts the rig on the path)
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402

PLAYBACK_CLOCK_EXTERNAL_ACTIVE = 2  # playback_handler.h
MIDI_RX_TIMING_BUFFER_SIZE = 32  # drivers/uart: the MIDI UART's timing capture, one uint32_t per byte
CLOCK_S = 60 / (120 * 24)  # MIDI clock at 120 BPM
TICK_120_BPM = 44100 * 60 / (120 * 96)  # samples per internal tick at 120 BPM: 229.7


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rig, inp = kr.boot(a, "boot")
    emu = rig.emu
    sym = emu.sym
    song = kr.Song(rig)
    midi = fuzz_ui.MidiIn(emu)
    emu.uc.ctl_flush_tb()
    state_off, recording_off, tick_time_off, armed_off = su.gdb_ints(emu, [
        "list Song::Song", "print (int)&((PlaybackHandler*)0)->playbackState",
        "print (int)&((PlaybackHandler*)0)->recording",
        "print (int)&((PlaybackHandler*)0)->timePerInternalTickMovingAverage",
        "print (int)&((Clip*)0)->armedForRecording"])
    handler = sym["playbackHandler"]

    def button(name, what, hold=0.05, wait=0.2):
        inp.button(name, True)
        rig.tm(hold, f"{what} on")
        inp.button(name, False)
        rig.tm(wait, f"{what} off")
    res = {}
    try:
        clip = song.clips()[0]
        y = 0 - song.scroll()
        inp.button("RECORD", True)
        rig.tm(0.6, "RECORD held (record arming)")
        inp.pad(16, y, 100)
        rig.tm(0.05, f"the first Clip's status pad 16,{y} on")
        inp.pad(16, y, 0)
        rig.tm(0.2, "status pad off")
        inp.button("RECORD", False)
        rig.tm(0.3, "RECORD released")
        res["armed"] = emu.u8(clip + armed_off)
        res["entered"] = song.enter(inp, 0)
        if not emu.u8(handler + recording_off):
            button("RECORD", "RECORD")
        emu.uc.mem_write(sym["midiRxTimingBuffer"], bytes(4 * MIDI_RX_TIMING_BUFFER_SIZE))
        midi.put([0xFB] + [0xF8] * 6 + [0xE0, 0x00, 0x40])  # continue, six clocks, a pitch bend on channel 1
        rig.tm(0.3, "MIDI continue, six clocks and a pitch bend together")
        res["external_clock"] = bool(emu.u8(handler + state_off) & PLAYBACK_CLOCK_EXTERNAL_ACTIVE)
        res["recording"] = emu.u8(handler + recording_off)
        res["tick_after_bunched_clocks"] = emu.u32(handler + tick_time_off)
        inp.turn("mod0", 3)
        rig.tm(0.2, "gold knob +3, recorded")
        for i in range(32):
            midi.put([0xF8])
            rig.tm(CLOCK_S, f"clock {i + 1}")
        res["tick_after_clocks"] = emu.u32(handler + tick_time_off)
        midi.put([0xE0, 0x00, 0x60])
        rig.tm(0.1, "pitch bend, recorded")
        midi.put([0xFC])
        rig.tm(0.3, "MIDI stop")
        res["messages"] = midi.messages
        res["ui_after"] = rig.ui_name()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["invalid"] = [(hex(k[0]), k[1], k[2]) for k in rig.invalid]
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    ok = (res.get("armed") and res.get("entered") and res.get("external_clock") and res.get("recording")
          and res.get("tick_after_bunched_clocks", 0) > 0
          and abs(res.get("tick_after_clocks", 0) - TICK_120_BPM) < 0.1 * TICK_120_BPM
          and res.get("messages", 0) >= 40 and not res["invalid"] and not res["problems"])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
