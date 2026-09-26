#!/usr/bin/env python3
"""Compares the machine code of two firmware builds function by function (e.g. v12 and v12-diag).

Addresses are masked (branch targets keep their symbol, literal-pool words that point into the image become ADDR),
so a function only counts as changed if its instructions or constants changed. With LTO, object files can't be
compared, so this works on the linked ELFs.

Data objects (tables, constants) are compared byte for byte, except words that are pointers into the image in both.

Usage: compare_elf.py base.elf new.elf [toolchain-bin-dir] [old=new ...]
  old=new: a function that was renamed, e.g. "AudioEngine::routine()=AudioEngine::routineUnmeasured()"
Prints what changed, exits 0.
"""
import os
import re
import struct
import subprocess
import sys

BIN = sys.argv[3] if len(sys.argv) > 3 else ""
OBJDUMP = os.path.join(BIN, "arm-none-eabi-objdump") if BIN else "arm-none-eabi-objdump"
RENAMED = dict(a.split("=", 1) for a in sys.argv[4:])
IMAGE_RANGES = [(0x20000000, 0x20400000), (0x0C000000, 0x10000000)]

func_re = re.compile(r"^([0-9a-f]{8}) <(.+)>:$")
insn_re = re.compile(r"^\s*[0-9a-f]+:\s+((?:[0-9a-f]{2,8} ?)+)\s+(.*)$")
addr_sym_re = re.compile(r"\b[0-9a-f]{6,8} (<[^>]+>)")
comment_re = re.compile(r";\s*\(?[0-9a-f]+ (<[^>]+>)\)?")
word_re = re.compile(r"^\.word\s+0x([0-9a-f]+)")


def in_image(value):
    return any(lo <= value < hi for lo, hi in IMAGE_RANGES)


movt_re = re.compile(r"^(movt(?:[a-z]{2})?)\s+(\w+), #(\d+)")
movw_re = re.compile(r"^(movw(?:[a-z]{2})?)\s+(\w+), #(\d+)")


def mask_addresses(body):
    """movw/movt pairs that build an address inside the image: the address is layout, not code"""
    for i, text in enumerate(body):
        m = movt_re.match(text)
        if not m or not in_image(int(m.group(3)) << 16):
            continue
        reg = m.group(2)
        body[i] = f"{m.group(1)} {reg}, #ADDRHI"
        for j in range(i - 1, max(i - 12, -1), -1):
            w = movw_re.match(body[j])
            if w and w.group(2) == reg:
                body[j] = f"{w.group(1)} {reg}, #ADDRLO"
                break
    return body


def functions(elf):
    out = subprocess.run([OBJDUMP, "-d", "--no-show-raw-insn", "-C", elf], capture_output=True, text=True,
                         check=True).stdout
    funcs = {}
    name = None
    body = []
    for line in out.splitlines():
        m = func_re.match(line)
        if m:
            if name:
                funcs[name] = body
            name = m.group(2)
            body = []
            continue
        if name is None or not line.strip() or line.startswith("Disassembly"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        text = "\t".join(parts[1:]).strip()
        w = word_re.match(text)
        if w and in_image(int(w.group(1), 16)):
            text = ".word ADDR"
        text = comment_re.sub(r"; \1", text)
        text = addr_sym_re.sub(r"\1", text)
        text = text.replace("<" + name + "+", "<SELF+").replace("<" + name + ">", "<SELF>")
        text = re.sub(r"\s*@ 0x[0-9a-f]+$", "", text)
        body.append(re.sub(r"\s+", " ", text))
    if name:
        funcs[name] = body
    return {n: mask_addresses(b) for n, b in funcs.items()}


def shape(body):
    """The instruction sequence without any immediate, offset or target: what is left when only the layout moved
    (offsets from section anchors and the pc, branch widths, alignment nops)"""
    out = []
    for text in body:
        if text.startswith(("nop", ".word", ".short", ".byte")):
            continue
        text = re.sub(r"<[^>]*>", "<T>", text)
        text = re.sub(r"#-?(0x)?[0-9a-fA-F]+|#ADDR\w*", "#I", text)
        text = re.sub(r"^(\w+)\.[nw]\b", r"\1", text)
        text = re.sub(r"\s*@.*$", "", text)
        text = re.sub(r"\s*;.*$", "", text)
        out.append(text)
    return out


def is_code(name, body):
    """Data in the code section (tables, vtables, strings) is disassembled too; skip it"""
    return not re.match(r"^(vtable for |typeinfo |CSWTCH|_ZL|_ZZ|_ZN|__tcf_)", name) and not any(
        t.startswith(".word") or "UNDEFINED" in t for t in body[:4])


symbol_re = re.compile(r"^([0-9a-f]{8}) (.{7}) (\S+)\s+([0-9a-f]{8}) (.+)$")


def sections(elf):
    """(address, size, file offset) of the loaded sections with contents"""
    data = open(elf, "rb").read()
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum = struct.unpack_from("<HH", data, 0x2E)
    out = []
    for i in range(shnum):
        _, stype, flags, addr, offset, size = struct.unpack_from("<IIIIII", data, shoff + i * shentsize)
        if stype == 1 and flags & 2 and size:  # PROGBITS, ALLOC
            out.append((addr, size, offset))
    return data, out


def data_objects(elf):
    data, secs = sections(elf)
    out = subprocess.run([OBJDUMP, "-t", "-C", elf], capture_output=True, text=True, check=True).stdout
    objects = {}
    for line in out.splitlines():
        m = symbol_re.match(line)
        if not m or "O" not in m.group(2):
            continue
        addr, size, name = int(m.group(1), 16), int(m.group(4), 16), m.group(5).replace(".hidden ", "")
        for saddr, ssize, soff in secs:
            if saddr <= addr and addr + size <= saddr + ssize:
                objects[name] = (addr, data[soff + addr - saddr:soff + addr - saddr + size])
                break
    return objects


def same_but_pointers(a, b):
    (addr_a, bytes_a), (addr_b, bytes_b) = a, b
    if len(bytes_a) != len(bytes_b):
        return False
    if bytes_a == bytes_b:
        return True
    if (addr_a - addr_b) % 4:
        return False
    for i in range(0, len(bytes_a), 4):
        wa, wb = bytes_a[i:i + 4], bytes_b[i:i + 4]
        if wa == wb:
            continue
        if len(wa) < 4 or (addr_a + i) % 4:
            return False
        va, vb = struct.unpack("<I", wa)[0], struct.unpack("<I", wb)[0]
        if not (in_image(va) and in_image(vb)):
            return False
    return True


def main():
    base = functions(sys.argv[1])
    new = functions(sys.argv[2])
    for old_name, new_name in RENAMED.items():
        if old_name in base and new_name in new:
            base[new_name] = base.pop(old_name)
    base_data = data_objects(sys.argv[1])
    new_data = data_objects(sys.argv[2])
    data_names = set(base_data) | set(new_data)
    base = {n: b for n, b in base.items() if n not in data_names}
    new = {n: b for n, b in new.items() if n not in data_names}
    common = [n for n in base if n in new and is_code(n, base[n])]
    same = [n for n in common if base[n] == new[n]]
    layout = [n for n in common if base[n] != new[n] and shape(base[n]) == shape(new[n])]
    changed = sorted(n for n in common if shape(base[n]) != shape(new[n]))
    removed = sorted(n for n in base if n not in new)
    added = sorted(n for n in new if n not in base)
    print(f"code functions: {len(same)} identical, {len(layout)} only moved data/branch offsets, {len(changed)} with "
          f"different instructions, {len(added)} added, {len(removed)} removed")
    for title, names in (("changed", changed), ("added", added), ("removed", removed)):
        for n in names:
            size = len(new.get(n, base.get(n, [])))
            print(f"  {title:8s} {n}  ({size} insns)")

    common = [n for n in base_data if n in new_data]
    identical = [n for n in common if base_data[n][1] == new_data[n][1]]
    pointers = [n for n in common if n not in identical and same_but_pointers(base_data[n], new_data[n])]
    differ = sorted(n for n in common if n not in identical and n not in pointers)
    print(f"data objects: {len(identical)} identical, {len(pointers)} differ only in pointers, {len(differ)} differ, "
          f"{len([n for n in new_data if n not in base_data])} added, "
          f"{len([n for n in base_data if n not in new_data])} removed")
    for n in differ:
        print(f"  differs  {n}  ({len(new_data[n][1])} bytes)")


if __name__ == "__main__":
    main()
