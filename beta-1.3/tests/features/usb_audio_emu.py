#!/usr/bin/env python3
"""USB audio ported from mastertune 1.2.1 (features/0006: the Deluge's output as a USB audio input on the computer,
the setting saved at once, the song's name and firmware for a recorder) in the v1.3 build, with the v1.3 rig
(rig13.py) and the fuzzer's card.

The emulator has no USB host, so nothing enumerates the Deluge and no computer opens the stream (the driver's
SET_INTERFACE, which calls usbAudioStart(), is inlined into the Renesas request code). The descriptors and the ring
against clock drift, a computer that stops reading and an engine stall are mastertune's host tests
(mastertune-1.2.1/tests/usbaudio, run on this tree by run.sh). This test covers the firmware's own part on v1.3:

  (1) boot from a card without CommunityFeatures.XML: USB audio off (usbAudioEnabled 0)
  (2) Settings > Community features > USB audio set On as the menu does (its value, then writeCurrentValue()): the
      popup "Restart to apply", and SETTINGS/CommunityFeatures.XML written at once with usbAudio 1, the menu not
      left, the startup song left as it was (empty here: the song loaded from the template was never saved)
  (3) a second boot from the same card (the restart): read before USB starts, usbAudioEnabled 1
  (4) the stream opened as usbAudioStart() opens it (the ring empty, running; the pipe running; USB MIDI's cable 3
      connected, as it is when a computer opens the stream), playing: every frame
      the audio engine sends to the codec goes into the ring, the same values in the same order (compared with the
      SSI's transmit buffer as written), none while stopped... until the ring is full (nothing reads it here)
  (5) meanwhile the song's info for the recorder: one SysEx on USB MIDI cable 3, F0 00 21 7B 01 12 <JSON packed 7
      bits in 8> F7, the JSON with the song's name and "fw" the firmware's version string
  (6) throughout: no crash, freeze, hang, error popup or access outside RAM and the peripherals
On the build without USB audio (s5) there is no USB audio setting: (2) fails.

Usage: usb_audio_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL (with the failed checks); exit 0/1."""
import argparse
import json
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))  # beta-1.3/tests
import rig13  # noqa: E402,F401
import fuzz_ui  # noqa: E402
import stress_ui_emu as su  # noqa: E402
import fat32  # noqa: E402
from master_tune_emu import Capture  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2  # noqa: E402

MENU = "_ZN6deluge3gui9menu_item15runtime_feature12menuUsbAudioE"
WRITE = "_ZN6deluge3gui9menu_item15runtime_feature15UsbAudioSetting17writeCurrentValueEv"
SEND_SYSEX = "_ZN12MIDICableUSB9sendSysexEPKhl"
RING_FRAMES = 2048


def unpack7(data):
    out = bytearray()
    for i in range(0, len(data), 8):
        group = data[i:i + 8]
        high = group[0]
        for j, b in enumerate(group[1:]):
            out.append(b | (0x80 if high & (1 << j) else 0))
    return bytes(out)


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

    def problems(rig, tag):
        if rig.problems or rig.error_popups() or rig.invalid:
            fails.append(f"(6) {tag}: {rig.problems[:3]} {rig.error_popups()[:3]} {list(rig.invalid)[:3]}")

    rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
    emu, sym = rig.emu, rig.emu.sym
    try:
        if MENU not in sym.by_name:
            fails.append("(2) no USB audio setting in this build")
            raise su.Stop("no USB audio")
        rig.tm(0.5, "settle")
        res["boot1_enabled"] = emu.u8(sym["usbAudioEnabled"])
        if res["boot1_enabled"] != 0:
            fails.append(f"(1) USB audio {res['boot1_enabled']} without a settings file")
        o_value, = su.gdb_ints(emu, ["print (int)&'deluge::gui::menu_item::runtime_feature::menuUsbAudio'.value_ - "
                                     "(int)&'deluge::gui::menu_item::runtime_feature::menuUsbAudio'"])
        emu.uc.mem_write(sym[MENU] + o_value, struct.pack("<i", 1))
        n = len(rig.popups)
        rig.action("USB audio On", sym.find(WRITE), sym[MENU], limit_s=20)
        rig.tm(0.3, "saved")
        res["popups"] = [p[3] for p in rig.popups[n:]]
        if "Restart to apply" not in res["popups"]:
            fails.append(f"(2) popups {res['popups']}")
    except su.Stop:
        pass
    problems(rig, "first boot")
    del rig
    if not fails:
        text = None
        try:
            text = fat32.read_file(image, "SETTINGS/CommunityFeatures.XML").decode(errors="replace")
        except Exception as e:  # noqa: BLE001
            res["settings_file_error"] = str(e)
        m = re.search(r'<setting name="usbAudio" value="(\d+)"', text or "")
        res["settings_file_usbAudio"] = m.group(1) if m else None
        m = re.search(r'startupSong="([^"]*)"', text or "")
        res["settings_file_startupSong"] = m.group(1) if m else None
        if res["settings_file_usbAudio"] != "1":
            fails.append(f"(2) SETTINGS/CommunityFeatures.XML: usbAudio {res['settings_file_usbAudio']!r}")
        if res["settings_file_startupSong"] not in (None, ""):
            fails.append(f"(2) the save at once set the startup song to {res['settings_file_startupSong']!r} (a song "
                         f"loaded but not saved)")

        # (3) the restart
        rig = fuzz_ui.boot("v13", a.elf, a.tools, a.build, image, True)
        emu, sym = rig.emu, rig.emu.sym
        inp = fuzz_ui.Inputs(rig, "v13")
        try:
            rig.tm(0.5, "settle")
            res["boot2_enabled"] = emu.u8(sym["usbAudioEnabled"])
            if res["boot2_enabled"] != 1:
                fails.append(f"(3) after the restart USB audio is {res['boot2_enabled']}")
            # (4) the stream opened as usbAudioStart() does: empty ring at the write index, running; the pipe running
            o_ring, o_w, o_r, o_run = su.gdb_ints(emu, [
                "print (int)&((UsbAudioStream*)0)->ring", "print (int)&((UsbAudioStream*)0)->writeIndex",
                "print (int)&((UsbAudioStream*)0)->readIndex", "print (int)&((UsbAudioStream*)0)->running"])
            stream = sym["_ZN12_GLOBAL__N_16streamE"]
            sysex = []

            def on_sysex(e):
                uc = e.uc
                n = uc.reg_read(UC_ARM_REG_R2)
                if 0 < n < 1024:
                    sysex.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), n)))
            emu.intercept(sym.find(SEND_SYSEX), on_sysex)
            emu.uc.ctl_flush_tb()
            cap = Capture(emu)
            w0 = emu.u32(stream + o_w)
            emu.uc.mem_write(stream + o_r, struct.pack("<I", w0))
            emu.uc.mem_write(stream + o_run, b"\x01")
            emu.uc.mem_write(sym["_ZN12_GLOBAL__N_111pipeRunningE"], b"\x01")
            # A computer that opens the stream is connected: USB MIDI's cable 3 has its device (the first)
            o_flags, = su.gdb_ints(emu, ["print (int)&MIDIDeviceManager::upstreamUSBMIDICable3.connectionFlags - "
                                         "(int)&MIDIDeviceManager::upstreamUSBMIDICable3"])
            cable3 = sym["_ZN17MIDIDeviceManager21upstreamUSBMIDICable3E"]
            flags_size, = su.gdb_ints(emu, ["print sizeof(MIDIDeviceManager::upstreamUSBMIDICable3.connectionFlags)"])
            emu.uc.mem_write(cable3 + o_flags, (1).to_bytes(flags_size, "little"))
            c0 = cap.mark()
            inp.button("PLAY", True)
            rig.tm(0.02, "PLAY")
            inp.button("PLAY", False)
            rig.tm(1.0, "playing, streaming")
            w1 = emu.u32(stream + o_w)
            pushed = min(w1 - w0, RING_FRAMES)
            ring = np.frombuffer(bytes(emu.uc.mem_read(stream + o_ring, RING_FRAMES * 8)), "<i4").reshape(-1, 2)
            got = np.array([ring[(w0 + i) % RING_FRAMES] for i in range(pushed)])
            sent = np.stack([np.array(cap.l[c0:], np.int64), np.array(cap.r[c0:c0 + len(cap.l) - c0], np.int64)],
                            axis=1).astype(np.int32)
            # The first frame pushed is the first one the codec got after the stream ran (the capture began a moment
            # before): found in the capture, then the whole run compared
            match = -1
            if pushed:
                for k in range(0, min(len(sent), 4096)):
                    if np.array_equal(sent[k:k + 64], got[:64]):
                        match = k
                        break
            same = match >= 0 and np.array_equal(sent[match:match + pushed], got)
            res["stream"] = dict(frames_pushed=int(w1 - w0), compared=int(pushed), found_at=match, same=bool(same),
                                 nonzero=int(np.count_nonzero(got)))
            if pushed < RING_FRAMES - 1 or not same or not np.count_nonzero(got):
                fails.append(f"(4) the ring: {res['stream']}")
            # (5) the song's info
            res["sysex"] = []
            for m in sysex:
                if m[:6] == bytes([0xF0, 0x00, 0x21, 0x7B, 0x01, 0x12]) and m[-1] == 0xF7:
                    try:
                        res["sysex"].append(json.loads(unpack7(m[6:-1]).decode()))
                    except ValueError as e:
                        res["sysex"].append(f"undecodable: {e}")
            res["song_info_told"] = emu.u8(sym["_ZN13usb_song_info12_GLOBAL__N_14toldE"])
            info = [x for x in res["sysex"] if isinstance(x, dict)]
            if not info or "song" not in info[0] or not info[0].get("fw"):
                fails.append(f"(5) the song info: {res['sysex']} ({len(sysex)} SysEx sent)")
            inp.button("PLAY", True)
            rig.tm(0.02, "STOP")
            inp.button("PLAY", False)
            rig.tm(0.3, "stopped")
        except su.Stop:
            pass
        problems(rig, "second boot")
    print(json.dumps(res, indent=1, default=str))
    json.dump(res, open(os.path.join(a.out, "usb_audio.json"), "w"), indent=1, default=str)
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
