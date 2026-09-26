#!/usr/bin/env python3
"""Drives the host build of the SysEx file access (harness) the way DEx does and checks the results."""
import json, os, random, subprocess, sys

# RUN: how to start the harness (empty on the PC; the emulator for the Cortex-A9 build, see ../arm)
H = subprocess.Popen(os.environ.get("RUN", "").split() + ["./harness"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="latin-1", bufsize=1)
fails = []

def cmd(line):
    H.stdin.write(line + "\n"); H.stdin.flush()
    out = []
    while True:
        l = H.stdout.readline()
        if not l:
            raise RuntimeError("harness died")
        l = l.rstrip("\n")
        if l == "OK":
            return out
        out.append(l)

def check(cond, what):
    if not cond:
        fails.append(what); print("FAIL:", what)

def pack(data):  # DEx sysexPacking.pack_8bit_to_7bit
    out = []
    for n in range(0, len(data), 7):
        grp = data[n:n + 7]; msbs = 0
        for i, b in enumerate(grp):
            msbs |= ((b & 0x80) >> 7) << i
        out.append(msbs); out += [b & 0x7F for b in grp]
    return bytes(out)

def unpack(data):
    out = bytearray()
    for n in range(0, len(data), 8):
        hi = data[n]
        for i, b in enumerate(data[n + 1:n + 8]):
            out.append(b | (0x80 if hi & (1 << i) else 0))
    return bytes(out)

def replies(lines):
    msgs = []
    for l in lines:
        if l.startswith("R "):
            m = bytes.fromhex(l[2:])
            check(m[0] == 0xF0 and m[-1] == 0xF7 and all(b < 0x80 for b in m[1:-1]), "reply is clean 7-bit SysEx")
            check(len(m) <= 4096 * 3, f"reply fits the USB send buffer ({len(m)} bytes)")
            msgs.append(m)
    return msgs

def parse(m):
    body = m[7:-1]
    sep = body.find(b"\x00")
    js = body if sep < 0 else body[:sep]
    return m[5], m[6], json.loads(js.decode("ascii")), (None if sep < 0 else unpack(body[sep + 1:]))

session = {"mid": 0, "min": 1, "max": 7}
def send(obj, binary=None, dev="S", run=True, msgid=None):
    if msgid is None:
        session["mid"] = session["mid"] + 1 if session["min"] <= session["mid"] < session["max"] else session["min"]
        msgid = session["mid"]
    js = json.dumps(obj).encode("ascii")
    msg = bytes([0xF0, 0x00, 0x21, 0x7B, 0x01, 0x04, msgid]) + js
    if binary is not None:
        msg += b"\x00" + pack(binary)
    msg += b"\xF7"
    lines = cmd(f"{dev} {msg.hex()}")
    if run:
        lines += cmd("RUN")
    return [parse(m) for m in replies(lines)], msgid

# Session, as DEx starts
r, _ = send({"session": {"tag": "DEx"}}, msgid=0)
check(len(r) == 1 and "^session" in r[0][2], "session reply")
s = r[0][2]["^session"]
session.update(min=s["midMin"], max=s["midMax"], mid=s["midMin"] - 1)
check(r[0][0] == 4 and r[0][1] == 0, "session reply uses the direct header")
check(s.get("tag") == "DEx", "session reply echoes the tag (deluge-editor tells tabs apart by it)")

# A folder with long and odd names
random.seed(1)
cmd("MKDIR /SAMPLES")
names = []
for i in range(45):
    n = f"S{i:02d}_" + "x" * random.randint(5, 180) + ".WAV"
    names.append(n)
    cmd(f"PUT /SAMPLES/{n} {os.urandom(10).hex()}")
odd = "CAF\x82_DRUM.WAV"  # byte 0x82 is e-acute in code page 437
cmd("PUT /SAMPLES/" + odd + " 00")  # the pipe is latin-1, so this is the raw byte 0x82

# Directory listing, paged like DEx's listDirectoryComplete
got, offset = [], 0
while True:
    r, _ = send({"dir": {"path": "/SAMPLES", "offset": offset, "lines": 64}})
    check(len(r) == 1 and r[0][2]["^dir"]["err"] == 0, f"dir reply at offset {offset}")
    lst = r[0][2]["^dir"]["list"]
    if not lst:
        break
    got += [e["name"] for e in lst]; offset += len(lst)
check(sorted(got) == sorted(names + ["CAF\u0082_DRUM.WAV"]), f"listing complete and exact ({len(got)} entries)")
check(len(got) == len(set(got)), "no entry listed twice")

# Listing like deluge-editor: 25 lines per page, and a shorter page ends the listing. Every page but the last must be
# full, even with long names
def list_like_dedit(path):
    got, pages = [], []
    while True:
        r, _ = send({"dir": {"path": path, "offset": len(got), "lines": 25}})
        lst = r[0][2]["^dir"]["list"]
        pages.append(len(lst))
        got += [e["name"] for e in lst]
        if len(lst) < 25:
            return got, pages
got, pages = list_like_dedit("/SAMPLES")
check(sorted(got) == sorted(names + ["CAF\u0082_DRUM.WAV"]), f"deluge-editor sees the whole folder (pages {pages})")
cmd("MKDIR /LONG")
long_names = [f"L{i:02d}_" + "y" * (250 - 8) + ".WAV" for i in range(30)]  # 250 characters each
for n in long_names:
    cmd(f"PUT /LONG/{n} 00")
got, pages = list_like_dedit("/LONG")
check(sorted(got) == sorted(long_names) and pages[0] == 25, f"full pages with 250-character names (pages {pages})")

# Upload like DEx: open for write, 128-byte blocks, close
data = os.urandom(5000)
r, _ = send({"open": {"path": "/SONGS/NEW/UP.BIN", "write": 1, "date": 0x5B3A, "time": 0x6000}})
o = r[0][2]["^open"]; check(o["err"] == 0 and o["fid"] > 0, "open for write (creates missing folders)")
fid = o["fid"]
for addr in range(0, len(data), 128):
    chunk = data[addr:addr + 128]
    r, _ = send({"write": {"fid": fid, "addr": addr, "size": len(chunk)}}, binary=chunk)
    w = r[0][2]["^write"]; check(w["err"] == 0 and w["size"] == len(chunk), f"write at {addr}")
r, _ = send({"close": {"fid": fid}}); check(r[0][2]["^close"]["err"] == 0, "close")
g = cmd("GET /SONGS/NEW/UP.BIN")[0].split(" ")
check(g[1] == "0" and bytes.fromhex(g[2]) == data, "uploaded file is byte-exact")

# Bigger write blocks (as big as 1.2.1's 1024-byte SysEx input allows)
data2 = os.urandom(3000)
r, _ = send({"open": {"path": "/UP2.BIN", "write": 1}}); fid2 = r[0][2]["^open"]["fid"]
for addr in range(0, len(data2), 800):
    chunk = data2[addr:addr + 800]
    r, _ = send({"write": {"fid": fid2, "addr": addr, "size": len(chunk)}}, binary=chunk)
    check(r and r[0][2]["^write"]["size"] == len(chunk), f"800-byte write at {addr}")
send({"close": {"fid": fid2}})
check(bytes.fromhex(cmd("GET /UP2.BIN")[0].split(" ")[2]) == data2, "800-byte blocks byte-exact")

# Upload like deluge-editor: 512-byte blocks, then read back
data3 = os.urandom(2100)
r, _ = send({"open": {"path": "/SYNTHS/SYNT001.XML", "write": 1}}); fid3 = r[0][2]["^open"]["fid"]
for addr in range(0, len(data3), 512):
    chunk = data3[addr:addr + 512]
    r, _ = send({"write": {"fid": fid3, "addr": addr, "size": len(chunk)}}, binary=chunk)
    check(r and r[0][2]["^write"]["size"] == len(chunk), f"512-byte write at {addr}")
send({"close": {"fid": fid3}})
check(bytes.fromhex(cmd("GET /SYNTHS/SYNT001.XML")[0].split(" ")[2]) == data3, "512-byte blocks byte-exact")

# Download like DEx: 1024-byte reads
r, _ = send({"open": {"path": "/SONGS/NEW/UP.BIN", "write": 0}})
o = r[0][2]["^open"]; check(o["size"] == len(data), "open for read reports the size")
back = b""
for addr in range(0, o["size"], 1024):
    r, _ = send({"read": {"fid": o["fid"], "addr": addr, "size": 1024}})
    back += r[0][3]
send({"close": {"fid": o["fid"]}})
check(back == data, "downloaded file is byte-exact")

# Reading a file that isn't open answers with an error, not with data
r, _ = send({"read": {"fid": 999, "addr": 0, "size": 1024}})
check(r[0][2]["^read"]["err"] != 0 and r[0][2]["^read"]["size"] == 0, "read of unknown fid")

# mkdir, rename, copy into a missing folder, move, utime, delete
r, _ = send({"mkdir": {"path": "/KITS"}}); check(r[0][2]["^mkdir"]["err"] == 0, "mkdir")
r, _ = send({"rename": {"from": "/UP2.BIN", "to": "/KITS/K.BIN"}}); check(r[0][2]["^rename"]["err"] == 0, "rename")
r, _ = send({"copy": {"from": "/KITS/K.BIN", "to": "/A/B/C.BIN"}}); check(r[0][2]["^copy"]["err"] == 0, "copy into missing folders")
check(bytes.fromhex(cmd("GET /A/B/C.BIN")[0].split(" ")[2]) == data2, "copy is byte-exact")
r, _ = send({"move": {"from": "/A/B/C.BIN", "to": "/M/D.BIN"}}); check(r[0][2]["^move"]["err"] == 0, "move")
check(cmd("GET /A/B/C.BIN")[0].startswith("G 4"), "move removed the source")
r, _ = send({"utime": {"path": "/M/D.BIN", "date": 0x5B3A, "time": 0x6000}}); check(r[0][2]["^utime"]["err"] == 0, "utime")
t = cmd("STAT /M/D.BIN")[0].split(" "); check(t[3] == str(0x5B3A) and t[4] == str(0x6000), "utime set the date")
r, _ = send({"delete": {"path": "/M/D.BIN"}}); check(r[0][2]["^delete"]["err"] == 0, "delete")
r, _ = send({"ping": {}}); check("^ping" in r[0][2], "ping")

# Unknown commands and garbage are skipped
r, _ = send({"frobnicate": {"x": [1, {"y": "z"}]}, "ping": {}}); check(len(r) == 1 and "^ping" in r[0][2], "unknown key skipped")
lines = cmd("S " + bytes([0xF0, 0, 0x21, 0x7B, 1, 4, 9]).hex() + "7b7b7b227878f7") + cmd("RUN")
check(True, "garbage survived")

# DIN is ignored
r, _ = send({"ping": {}}, dev="D"); check(r == [], "SysEx file access ignores DIN")

# The queue holds at most 16
lines = []
for i in range(20):
    js = json.dumps({"ping": {}}).encode()
    lines += cmd("S " + (bytes([0xF0, 0, 0x21, 0x7B, 1, 4, session["min"]]) + js + b"\xF7").hex())
lines += cmd("RUN")
check(len(replies(lines)) == 16, f"queue bounded ({len(replies(lines))} replies to 20)")

# A reply goes out only whole: it waits for room in the USB send buffer, the next request waits behind it, and after
# two seconds without room it's dropped rather than sent in part
ping = lambda: cmd("S " + (bytes([0xF0, 0, 0x21, 0x7B, 1, 4, session["min"]]) + b'{"ping":{}}' + b"\xF7").hex())
ping_len = len(replies(ping() + cmd("RUN"))[0])
cmd(f"SPACE {ping_len - 1}")
lines = ping() + ping()
for i in range(50):
    lines += cmd("RUN1")
check(len(replies(lines)) == 0, "waits while the send buffer has less room than the reply")
cmd(f"SPACE {ping_len}"); lines += cmd("RUN1")  # the fake buffer doesn't fill up, so both fit one after the other
got = replies(lines)
check(len(got) == 2 and all(len(m) == ping_len for m in got), "sends once the whole reply fits, then the one behind it")
cmd("SPACE 12288")
cmd("SPACE 0")
lines = ping()
for i in range(20):
    lines += cmd("RUN1")
lines += cmd("TICK 88200") + cmd("RUN1")
cmd("SPACE 12288"); lines += cmd("RUN")
check(len(replies(lines)) == 0, "after two seconds without room the reply is dropped, not sent in part")
r, _ = send({"ping": {}}); check(len(r) == 1, "the next request works normally")

H.stdin.close(); H.wait()
print(f"{len(fails)} failures" if fails else "all checks passed")
sys.exit(1 if fails else 0)
