#!/usr/bin/env python3
"""Relocation-aware diff of two Deluge firmware images.

ref.bin/ref.elf: clean upstream build (same commit, same toolchain, same path).
fork.bin:        the image under test (no symbols).

1. Anchor the images with unique 24-byte windows, keep a monotonic chain (LIS)
   -> piecewise offset delta ref->fork, which reveals insertions/deletions.
2. For every FUNC/OBJECT symbol of the ref ELF, compare its bytes with the fork
   at the anchored position, tolerating pure relocation effects:
   - 32-bit words that are addresses and map exactly via the delta function
   - Thumb-2 BL/BLX/B.W whose targets map exactly
   - MOVW/MOVT differing only in the immediate (address halves)
   Everything else counts as a real change.
3. Report changed symbols, insertions, and string differences as JSON.
"""
import bisect, collections, json, re, subprocess, sys

ref_bin, fork_bin, elf, tc, out_json = sys.argv[1:6]
A = open(ref_bin, "rb").read()
B = open(fork_bin, "rb").read()

# ---------------------------------------------------------------- sections
secs, noload = [], []
for ln in subprocess.run([tc + "objdump", "-h", "-w", elf], capture_output=True, text=True).stdout.splitlines():
    p = ln.split()
    if len(p) >= 7 and p[0].isdigit():
        name, size, vma, lma = p[1], int(p[2], 16), int(p[3], 16), int(p[4], 16)
        if not size or vma == 0:
            continue
        if "LOAD" in ln:
            secs.append(dict(name=name, size=size, vma=vma, lma=lma))
        elif "ALLOC" in ln:
            noload.append(dict(name=name, size=size, vma=vma))
secs.sort(key=lambda s: s["lma"])
BASE = secs[0]["lma"]
FORK_BASE = int.from_bytes(B[0x20:0x24], "little")  # program_code_start in header
BASE_SHIFT = FORK_BASE - int.from_bytes(A[0x20:0x24], "little")


def vma_to_off(a):
    for s in secs:
        if s["vma"] <= a < s["vma"] + s["size"]:
            return s["lma"] - BASE + (a - s["vma"]), s
    return None, None


# ---------------------------------------------------------------- anchors
W = 24
index = {}
for i in range(0, len(B) - W, 2):
    k = B[i:i + W]
    index[k] = -1 if k in index else i
pairs = []
for i in range(0, len(A) - W, 8):
    k = A[i:i + W]
    if len(set(k)) < 10:
        continue
    j = index.get(k)
    if j is not None and j >= 0:
        pairs.append((i, j))
# longest increasing subsequence on fork offsets
tails, tails_idx, prev = [], [], [-1] * len(pairs)
for n, (_, j) in enumerate(pairs):
    k = bisect.bisect_left(tails, j)
    if k == len(tails):
        tails.append(j); tails_idx.append(n)
    else:
        tails[k] = j; tails_idx[k] = n
    prev[n] = tails_idx[k - 1] if k else -1
chain, n = [], tails_idx[-1]
while n >= 0:
    chain.append(pairs[n]); n = prev[n]
chain.reverse()
anc_ref = [a for a, _ in chain]
anc_d = [b - a for a, b in chain]


def delta_at(off):
    """delta of the nearest anchor at or before off (fallback: first anchor)."""
    k = bisect.bisect_right(anc_ref, off) - 1
    return anc_d[max(k, 0)]


def deltas_around(s, e):
    k0 = bisect.bisect_right(anc_ref, s) - 1
    k1 = bisect.bisect_left(anc_ref, e)
    ds = set(anc_d[max(k0, 0):min(k1 + 1, len(anc_d))])
    return ds


# delta changes = insertions / deletions
changes = []
for k in range(1, len(chain)):
    if anc_d[k] != anc_d[k - 1]:
        changes.append(dict(ref_from=anc_ref[k - 1], ref_to=anc_ref[k] + W,
                            d_before=anc_d[k - 1], d_after=anc_d[k]))

SDRAM0 = next(s for s in secs if s["vma"] < 0x20000000)["lma"] - BASE


SYM_STARTS, SYM_LIST = [], []


def sym_delta(off):
    k = bisect.bisect_right(SYM_STARTS, off) - 1
    if k >= 0:
        s = SYM_LIST[k]
        if s["off"] <= off < s["off"] + s["size"] and s.get("real") == 0:
            return s["delta"]
    return None


def map_addr(w):
    """Map a ref address to the fork address; None if not a loadable address."""
    t = w & 1 if (w & 1) else 0
    a = w - t
    off, s = vma_to_off(a)
    if off is None:
        return None
    d = sym_delta(off)
    if d is None:
        d = delta_at(off)
    if s["vma"] < 0x20000000:  # SDRAM: VMA origin is fixed, only in-SDRAM growth counts
        d -= delta_at(SDRAM0)
    else:                      # RAM: image start itself moved (bigger .bss before .reset)
        d += BASE_SHIFT
    return a + d + t


def in_noload(w):
    for s in noload:
        if s["vma"] <= w < s["vma"] + s["size"] + 0x4000:
            return s["name"]
    return None


def thumb_bl_target(hw1, hw2, pc):
    if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0x8000 and ((hw1 >> 6) & 0xF) < 0xE:  # B<cond>.W T3
        S = (hw1 >> 10) & 1; imm6 = hw1 & 0x3F
        J1, J2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1; imm11 = hw2 & 0x7FF
        imm = (S << 20) | (J2 << 19) | (J1 << 18) | (imm6 << 12) | (imm11 << 1)
        if S: imm -= 1 << 21
        return pc + 4 + imm
    if (hw1 & 0xF800) != 0xF000 or (hw2 & 0xD000) not in (0xD000, 0xC000, 0x9000):
        return None
    S = (hw1 >> 10) & 1
    imm10 = hw1 & 0x3FF
    J1, J2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
    imm11 = hw2 & 0x7FF
    I1, I2 = 1 - (J1 ^ S), 1 - (J2 ^ S)
    imm = (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1)
    if S:
        imm -= 1 << 25
    t = pc + 4 + imm
    if (hw2 & 0xD000) == 0xC000:  # BLX -> ARM target, align
        t &= ~3
    return t


def thumb16_branch_target(hw, pc):
    if (hw & 0xF800) == 0xE000:                      # B T2
        imm = (hw & 0x7FF) << 1
        if imm & 0x800: imm -= 0x1000
        return pc + 4 + imm
    if (hw & 0xF000) == 0xD000 and ((hw >> 8) & 0xF) < 0xE:   # B<cond> T1
        imm = (hw & 0xFF) << 1
        if imm & 0x100: imm -= 0x200
        return pc + 4 + imm
    if (hw & 0xF500) == 0xB100:                      # CBZ/CBNZ
        imm = (((hw >> 9) & 1) << 6) | (((hw >> 3) & 0x1F) << 1)
        return pc + 4 + imm
    return None


def is_movwt(hw1, hw2):
    return (hw1 & 0xFB70) in (0xF240, 0xF2C0) and (hw2 & 0x8000) == 0


def compare(s_off, size, d, addr0):
    """Return (real_diffs, reloc_diffs) comparing ref[s_off:+size] vs fork[s_off+d:]."""
    r = A[s_off:s_off + size]
    f = B[s_off + d:s_off + d + size]
    if len(f) < size:
        return size, 0
    if r == f:
        return 0, 0
    real = reloc = 0
    i = 0
    while i < size:
        if r[i] == f[i]:
            i += 1
            continue
        # align to halfword
        h = i & ~1
        # Thumb-2 32-bit instruction starting at h-2 or h?
        handled = False
        for st in (h - 2, h):
            if st < 0 or st + 4 > size:
                continue
            r1, r2 = int.from_bytes(r[st:st + 2], "little"), int.from_bytes(r[st + 2:st + 4], "little")
            f1, f2 = int.from_bytes(f[st:st + 2], "little"), int.from_bytes(f[st + 2:st + 4], "little")
            pc_r = addr0 + st
            fork_pc = pc_r + (d + BASE_SHIFT if pc_r >= 0x20000000 else d - delta_at(SDRAM0))
            tr = thumb_bl_target(r1, r2, pc_r)
            tf = thumb_bl_target(f1, f2, fork_pc)
            if tr is not None and tf is not None:
                m = map_addr(tr | 1)
                if m is not None and ((m & ~1) == (tf & ~1) or (m & ~3) == (tf & ~3)):
                    reloc += 1
                else:
                    real += 1
                i = st + 4
                handled = True
                break
            if is_movwt(r1, r2) and is_movwt(f1, f2) and (r1 & 0xFBF0) == (f1 & 0xFBF0) and (r2 & 0x0F00) == (f2 & 0x0F00):
                reloc += 1
                i = st + 4
                handled = True
                break
        if not handled and h + 2 <= size:
            # 16-bit PC-relative branches: B (T2), B<cond> (T1), CBZ/CBNZ
            r1 = int.from_bytes(r[h:h + 2], "little"); f1 = int.from_bytes(f[h:h + 2], "little")
            pc_r = addr0 + h
            fork_pc = pc_r + (d + BASE_SHIFT if pc_r >= 0x20000000 else d - delta_at(SDRAM0))
            tr = thumb16_branch_target(r1, pc_r)
            tf = thumb16_branch_target(f1, fork_pc)
            if tr is not None and tf is not None and (r1 & 0xFF00 if r1 < 0xE000 else r1 & 0xF800) == (f1 & 0xFF00 if f1 < 0xE000 else f1 & 0xF800):
                m = map_addr(tr | 1)
                if m is not None and (m & ~1) == tf:
                    reloc += 1; i = h + 2; continue
                real += 1; i = h + 2; continue
        if handled:
            continue
        # 32-bit word (absolute address) at 4-aligned position
        wpos = (addr0 + i) & ~3
        st = wpos - addr0
        if 0 <= st and st + 4 <= size:
            wr = int.from_bytes(r[st:st + 4], "little")
            wf = int.from_bytes(f[st:st + 4], "little")
            m = map_addr(wr)
            if m is not None and m == wf:
                reloc += 1; i = st + 4; continue
            if (wr & 0x0E000000) == 0x0A000000 and (wr & 0xFF000000) == (wf & 0xFF000000) and (wr >> 28) != 0xF:
                pc_r = addr0 + st
                fork_pc = pc_r + (d + BASE_SHIFT if pc_r >= 0x20000000 else d - delta_at(SDRAM0))
                def armt(w, pc):
                    imm = (w & 0xFFFFFF) << 2
                    if imm & 0x2000000: imm -= 0x4000000
                    return pc + 8 + imm
                m2 = map_addr(armt(wr, pc_r))
                if m2 is not None and (m2 & ~1) == armt(wf, fork_pc):
                    reloc += 1; i = st + 4; continue
            nr, nf = in_noload(wr), in_noload(wf)
            if nr and nr == nf and 0 <= wf - wr <= 0x2000:
                reloc += 1; i = st + 4; continue
        real += 1
        i = (i | 1) + 1
    return real, reloc


# ---------------------------------------------------------------- symbols
syms = []
for ln in subprocess.run([tc + "readelf", "-sW", elf], capture_output=True, text=True).stdout.splitlines():
    p = ln.split(None, 7)
    if len(p) == 8 and p[0].endswith(":") and p[3] in ("FUNC", "OBJECT"):
        try:
            a, sz = int(p[1], 16), int(p[2])
        except ValueError:
            continue
        if sz == 0:
            continue
        a &= ~1 if p[3] == "FUNC" else ~0
        off, s = vma_to_off(a)
        if off is None:
            continue
        syms.append(dict(name=p[7], kind=p[3], addr=a, size=sz, off=off, sec=s["name"]))
# dedupe aliases
seen, uniq = set(), []
for s in sorted(syms, key=lambda s: (s["off"], -s["size"])):
    key = (s["off"], s["size"])
    if key in seen:
        continue
    seen.add(key); uniq.append(s)
syms = uniq

def run_pass():
  out = []
  for s in syms:
    cands = deltas_around(s["off"], s["off"] + s["size"]) or {delta_at(s["off"])}
    if "delta" in s:
        cands.add(s["delta"])
    best = None
    for d in sorted(cands):
        real, reloc = compare(s["off"], s["size"], d, s["addr"])
        if best is None or real < best[0]:
            best = (real, reloc, d)
    s.update(real=best[0], reloc=best[1], delta=best[2], deltas=sorted(cands))
    out.append(s)
  return out

SYM_LIST = sorted(syms, key=lambda s: s["off"])
SYM_STARTS = [s["off"] for s in SYM_LIST]
for p in range(3):
    results = run_pass()
    print("pass", p, "changed", sum(1 for s in results if s["real"] > 0))

changed = [s for s in results if s["real"] > 0]

# demangle names of changed symbols
if changed:
    dm = subprocess.run([tc + "c++filt"], input="\n".join(s["name"] for s in changed),
                        capture_output=True, text=True).stdout.splitlines()
    for s, n in zip(changed, dm):
        s["demangled"] = n

# ---------------------------------------------------------------- strings
def strset(buf):
    return collections.Counter(m.group().decode() for m in re.finditer(rb"[\x20-\x7e]{4,}(?=\x00)", buf))

sa, sb = strset(A), strset(B)
only_fork = sorted(set(sb) - set(sa))
only_ref = sorted(set(sa) - set(sb))

json.dump(dict(
    ref_size=len(A), fork_size=len(B), ref_base=BASE, fork_base=FORK_BASE,
    anchors=len(chain), changes=changes,
    symbols_checked=len(results),
    identical=sum(1 for s in results if s["real"] == 0 and s["reloc"] == 0),
    reloc_only=sum(1 for s in results if s["real"] == 0 and s["reloc"] > 0),
    changed=changed, strings_only_fork=only_fork, strings_only_ref=only_ref,
), open(out_json, "w"), indent=1)
print(f"anchors {len(chain)}, delta changes {len(changes)}, symbols {len(results)}, changed {len(changed)}")
