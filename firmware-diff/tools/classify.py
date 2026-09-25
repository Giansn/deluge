#!/usr/bin/env python3
"""Classify every changed function from diff.json by comparing disassembly.

Per function: align ref vs fork instruction streams (opcodes with immediates masked).
- 'struct'  : identical instruction sequence, only [reg, #imm] offsets differ
- 'const'   : identical sequence, only other immediates differ (enum ids, constants)
- 'logic'   : instructions inserted/removed/replaced (real code change or codegen ripple)
"""
import difflib, json, os, re, subprocess, sys, tempfile, collections

S = os.environ.get("WORK", os.getcwd())
TC = os.environ.get("TC", sys.argv[1] if len(sys.argv) > 1 else "arm-none-eabi-")
d = json.load(open(f"{S}/diff.json"))
A = open(f"{S}/ref.bin", "rb").read()
B = open(f"{S}/fork.bin", "rb").read()
BASE = d["ref_base"]
SHIFT = d["fork_base"] - d["ref_base"]

# Thumb bit per symbol address from the ELF
thumb = {}
for ln in subprocess.run([TC + "readelf", "-sW", f"{S}/ref.elf"], capture_output=True, text=True).stdout.splitlines():
    p = ln.split(None, 7)
    if len(p) == 8 and p[3] == "FUNC":
        try:
            a = int(p[1], 16)
        except ValueError:
            continue
        thumb[a & ~1] = bool(a & 1)


def dis(buf, off, n, vma, is_thumb):
    with tempfile.NamedTemporaryFile(delete=False) as t:
        t.write(buf[off:off + n])
    args = [TC + "objdump", "-D", "-b", "binary", "-marm", f"--adjust-vma={vma:#x}", t.name]
    if is_thumb:
        args.insert(5, "-Mforce-thumb")
    out = subprocess.run(args, capture_output=True, text=True).stdout
    os.unlink(t.name)
    ins = []
    for l in out.splitlines():
        m = re.match(r"\s*[0-9a-f]+:\t([0-9a-f ]+)\t(.*)$", l)
        if m:
            txt = re.sub(r"\s*[;@].*$", "", m.group(2)).strip()
            ins.append(txt)
    return ins


MEM = re.compile(r"\[(\w+), #-?\d+\]")
BR = re.compile(r"^(b|bl|blx|b\w{2}|cbn?z)(\.[nw])?\s")


def mask(i):
    if BR.match(i):
        return re.sub(r"0x[0-9a-f]+", "T", i)
    if re.search(r"\[pc", i):  # literal loads: offsets can move with pool layout
        return re.sub(r"#-?\d+", "#L", i)
    return re.sub(r"#-?(0x)?[0-9a-f]+", "#I", i)


results = []
for s in d["changed"]:
    if s["kind"] != "FUNC":
        continue
    fork_off = s["off"] + s["delta"]
    fork_vma = s["addr"] + s["delta"] + (SHIFT if s["addr"] >= 0x20000000 else 0)
    t = thumb.get(s["addr"], True)
    # take a little extra on the fork side in case the function grew
    r = dis(A, s["off"], s["size"], s["addr"], t)
    f = dis(B, fork_off, s["size"] + 64, fork_vma, t)
    rm, fm = [mask(x) for x in r], [mask(x) for x in f]
    sm = difflib.SequenceMatcher(None, rm, fm, autojunk=False)
    ops = sm.get_opcodes()
    # trim trailing insert that is just the extra fork bytes
    if ops and ops[-1][0] == "insert" and ops[-1][1] == len(rm):
        ops = ops[:-1]
    struct = const = logic = 0
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for a, b in zip(r[i1:i2], f[j1:j2]):
                if a != b and not BR.match(a) and "[pc" not in a:
                    if MEM.search(a) and MEM.sub("M", a) == MEM.sub("M", b):
                        struct += 1
                    else:
                        const += 1
        else:
            logic += max(i2 - i1, j2 - j1)
    kind = "logic" if logic else ("struct" if struct and not const else ("const" if const and not struct else ("struct+const" if struct else "reloc")))
    s.update(cls=kind, n_struct=struct, n_const=const, n_logic=logic, n_ins=len(r))
    results.append(s)

cnt = collections.Counter(s["cls"] for s in results)
print(cnt)
json.dump(results, open(f"{S}/classified.json", "w"), indent=1)
