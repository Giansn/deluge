#!/usr/bin/env python3
"""Checks the USB audio configuration descriptor against USB 2.0 ch. 9, UAC 1.0 and USB MIDI 1.0 rules."""
import subprocess, sys
out = subprocess.run(["./dump"], capture_output=True, text=True).stdout.split("\n")
get = lambda name: bytes.fromhex(next(l for l in out if l.startswith(name + " "))[len(name) + 1:].replace(" ", ""))
dev, midi, cfg = get("device"), get("midiconfig"), get("config")
pipes = [int(x, 16) for x in next(l for l in out if l.startswith("pipes ")).split()[1:]]
fails = []
def check(c, what):
    print(("ok   " if c else "FAIL ") + what)
    if not c: fails.append(what)

# Device
check(len(dev) == 18 and dev[0] == 18 and dev[1] == 1, "device descriptor 18 bytes")
check(dev[12] | dev[13] << 8 == 0x0201, "bcdDevice 2.01 with audio")
check(dev[4] == 0 and dev[5] == 0 and dev[6] == 0, "class defined per interface")

# Walk
descs, i = [], 0
while i < len(cfg):
    L = cfg[i]
    check(L >= 2 and i + L <= len(cfg), f"descriptor at {i} has a sane length ({L})")
    descs.append(cfg[i:i + L]); i += L
check(i == len(cfg), "descriptors fill the configuration exactly")
c0 = descs[0]
check(c0[1] == 2 and c0[0] == 9, "configuration header")
check(c0[2] | c0[3] << 8 == len(cfg), f"wTotalLength {c0[2] | c0[3] << 8} == {len(cfg)}")

# Group by interface
ifaces, cur = [], None
for d in descs[1:]:
    if d[1] == 4:
        check(d[0] == 9, "interface descriptor is 9 bytes")
        cur = {"desc": d, "num": d[2], "alt": d[3], "neps": d[4], "cls": (d[5], d[6]), "eps": [], "cs": []}
        ifaces.append(cur)
    elif d[1] == 5:
        cur["eps"].append(d)
    else:
        cur["cs"].append(d)
nums = sorted(set(f["num"] for f in ifaces))
check(nums == [0, 1, 2] and c0[4] == 3, f"interfaces 0,1,2 and bNumInterfaces 3 (got {nums}, {c0[4]})")
for f in ifaces:
    check(len(f["eps"]) == f["neps"], f"interface {f['num']} alt {f['alt']}: {f['neps']} endpoint(s) declared and present")
    for e in f["eps"]:
        expect = 9 if f["cls"][0] == 1 else 7  # audio class endpoints use the 9-byte form
        check(e[0] == 9, f"interface {f['num']} endpoint {e[2]:#04x} is the 9-byte audio form")
# alt settings contiguous, starting at 0
for n in nums:
    alts = [f["alt"] for f in ifaces if f["num"] == n]
    check(alts == list(range(len(alts))), f"interface {n} alternate settings {alts} run from 0")

ac = [f for f in ifaces if f["num"] == 0][0]
check(ac["cls"] == (1, 1) and ac["neps"] == 0, "interface 0 is AudioControl without endpoints")
hdr = ac["cs"][0]
check(hdr[1] == 0x24 and hdr[2] == 1 and hdr[3] | hdr[4] << 8 == 0x0100, "AC header, ADC 1.00")
check(hdr[0] == 8 + hdr[7], "AC header length matches bInCollection")
check(list(hdr[8:8 + hdr[7]]) == [1, 2], "AC header lists interfaces 1 (MIDI) and 2 (audio stream)")
check(hdr[5] | hdr[6] << 8 == sum(d[0] for d in ac["cs"]), "AC header wTotalLength covers header and units")
it = [d for d in ac["cs"] if d[2] == 2][0]; ot = [d for d in ac["cs"] if d[2] == 3][0]
check(it[0] == 12 and it[3] == 1 and it[7] == 2 and (it[8] | it[9] << 8) == 3, "input terminal 1: 2 channels, L+R")
check(ot[0] == 9 and ot[3] == 2 and (ot[4] | ot[5] << 8) == 0x0101 and ot[7] == 1, "output terminal 2: USB streaming, from 1")

ms = [f for f in ifaces if f["num"] == 1]
check(len(ms) == 1 and ms[0]["cls"] == (1, 3), "interface 1 is MIDIStreaming")
# MIDI interface identical to the Deluge's usual one apart from its number
mi = midi[9:]; start = cfg.find(mi[3:])
check(start > 0 and cfg[start - 3:start] == bytes([9, 4, 1]) and cfg[start - 1 + 1:start + len(mi) - 3] == mi[3:],
      "MIDI interface byte for byte as in MIDI-only mode (number 1 instead of 0)")

asf = [f for f in ifaces if f["num"] == 2]
check(len(asf) == 2 and asf[0]["neps"] == 0 and asf[1]["neps"] == 1 and all(f["cls"] == (1, 2) for f in asf),
      "interface 2: AudioStreaming, alt 0 without and alt 1 with one endpoint")
alt1 = asf[1]
gen = [d for d in alt1["cs"] if d[1] == 0x24 and d[2] == 1][0]
fmt = [d for d in alt1["cs"] if d[1] == 0x24 and d[2] == 2][0]
csep = [d for d in alt1["cs"] if d[1] == 0x25][0]
check(gen[0] == 7 and gen[3] == 2 and (gen[5] | gen[6] << 8) == 1, "AS general: terminal link 2, PCM")
check(fmt[0] == 8 + 3 * fmt[7] and fmt[3] == 1 and fmt[4] == 2 and fmt[5] == 3 and fmt[6] == 24,
      "format type I: 2 channels, 3-byte subframes, 24 bits")
check(fmt[7] == 1 and fmt[8] | fmt[9] << 8 | fmt[10] << 16 == 44100, "one sample rate: 44100")
ep = alt1["eps"][0]
mps = ep[4] | ep[5] << 8
check(ep[2] == 0x83 and ep[3] == 0x05 and ep[6] == 1, "endpoint 0x83, isochronous asynchronous, every frame")
check(mps >= 45 * 6 and mps <= 1023, f"wMaxPacketSize {mps} fits 45 frames and full speed")
check(csep[0] == 7 and csep[2] == 1 and csep[3] == 0, "CS endpoint: no sampling frequency control (no class requests)")

# Endpoint order and the driver's pipe table (usb_pstd_set_eptbl_index: index = endpoints of all interface
# descriptors before the matching one, plus position)
order = [e[2] for f in ifaces for e in f["eps"]]
check(order == [0x02, 0x81, 0x83], f"endpoint order {[hex(x) for x in order]}")
def eptbl_index(num, alt):
    start, res, numbers, j = 0, {}, 0, 0
    for f in ifaces:
        if f["num"] == num and f["alt"] == alt:
            numbers = f["neps"]
            for k, e in enumerate(f["eps"]):
                res[e[2]] = start + k
        else:
            start += f["neps"]
    return res
check(eptbl_index(1, 0) == {0x02: 0, 0x81: 1}, "MIDI endpoints get table entries 0 and 1")
check(eptbl_index(2, 1) == {0x83: 2}, "audio endpoint gets table entry 2")
entries = [pipes[k * 6:(k + 1) * 6] for k in range(3)]
check([e[0] for e in entries] == [2, 3, 1] and pipes[18] == 0xFFFF, "pipe table: PIPE2, PIPE3 (MIDI), PIPE1 (audio), end")
bufsize = lambda v: ((v >> 10) + 1) * 64
blocks = [(e[2] & 0xFF, (e[2] & 0xFF) + 2 * bufsize(e[2]) // 64) for e in entries]
check(bufsize(entries[2][2]) >= mps, f"PIPE1 buffer {bufsize(entries[2][2])} bytes holds a packet")
check(all(a[1] <= b[0] or b[1] <= a[0] for x, a in enumerate(blocks) for b in blocks[x + 1:]),
      f"FIFO buffer blocks don't overlap {blocks}")
check(entries[2][1] & 0x0200, "PIPE1 double buffered")
print(f"{len(fails)} failures" if fails else "all descriptor checks passed")
sys.exit(1 if fails else 0)
