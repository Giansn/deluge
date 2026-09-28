#!/bin/sh
# Silent kits and audio tracks (mastertune-v17): silent_emu.py's song (tracks that play, stop with long tails, are
# changed while silent and start again; see its docstring) on two builds, compared sample for sample. The build
# without the early return (the reference) and the one with it must give the same output and the same arpeggiator
# notes (the kit arp, a MIDI and a gate row's arps; each must step 8 times or more); the latter's result.json says how
# often each track took the early return.
#
# Usage: ./run.sh <reference tree | deluge.elf> <tree | deluge.elf> [out dir]
#   With a tree, it takes build/Release/deluge.elf and the tree's toolchain (gdb); with an ELF, TOOLS (the toolchain
#   prefix, .../arm-none-eabi-) or the toolchain two directories up from it.
# Results: <out>/ref/ and <out>/new/ (measured.wav, measured.npy, result.json, song.xml). About 1 minute.
# mastertune-v16 (f50646f1) against silent-v17 (fa20a339): the same output, sample for sample; 131,111 -> 129,489
#   instructions per 128 samples (-1.2 %); 16,044 early returns (KDLY 2,066, KMOD 1,416, KSTUT 3,808, LREV 2,932, LNEW
#   1,715, DRN 4,107). It catches what the early return must keep: without the sidechain in it, the output differs
#   from bar 7.5 on (LREV starting again, 266 samples); without the filter mode check (FilterSet::keepsModes()),
#   LREV's filter, turned off and on again while silent, misses its reset (44,098 samples).
#   With KARP and KMIDI (the arps across silence), mastertune-v16 (c610417f) against mastertune-v17: the same output
#   and the same 116 arp notes (kit arp 20, MIDI row 64, gate row 32, in the same windows); 135,565 -> 133,143
#   instructions per 128 samples (-1.8 %); 26,037 early returns (KARP 4,270 between its steps, KMIDI 5,598).
# mastertune v18 on: v16 is no reference any more (v18's filters and volume render differently). The reference is the
#   same source with the early return disabled, built once (then restore the line):
#     sed -i 's|	if (!renderedLastTime \&\& samplesOfSilentEffects >= kSilentSamplesBeforeSkippingEffects$|	if (false \&\& !renderedLastTime \&\& samplesOfSilentEffects >= kSilentSamplesBeforeSkippingEffects|' \
#       src/deluge/model/global_effectable/global_effectable_for_clip.cpp
#   v18 against that: different from bar 4 on (up to -21 dBFS at 5): the check after rendering didn't restart the
#   volume ramps as the early return does, and both left the filters' ramps, fades and the EQ from before the silence.
#   v18.1 (both restart all of them) against its own: the same output, sample for sample, the same 116 arp notes;
#   26,118 early returns.
# Needs: python3 with unicorn 2 and numpy, a C compiler (for ../song/blockcount.c).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(realpath -m "${3:-$(mktemp -d)}")
mkdir -p "$OUT"
UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
cc -O2 -shared -fPIC -I"$UC/include" "$HERE/../song/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" \
	-l:libunicorn.so.2 -Wl,-rpath,"$UC/lib"

run() { # tree or ELF, name
	if [ -d "$1" ]; then
		elf=$(cd "$1" && pwd)/build/Release/deluge.elf
		tools=$(cd "$1" && pwd)/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-
	else
		elf=$(realpath "$1")
		tools=${TOOLS:-$(dirname "$elf")/../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-}
	fi
	echo "== $2: $elf"
	python3 "$HERE/silent_emu.py" "$elf" "$OUT/$2" --tools "$tools" --build "$OUT"
}
run "$1" ref
run "$2" new

python3 - "$OUT" <<'PY'
import hashlib, json, os, sys
import numpy as np
out = sys.argv[1]
a, b = (np.load(os.path.join(out, d, "measured.npy")) for d in ("ref", "new"))
wav = [hashlib.sha256(open(os.path.join(out, d, "measured.wav"), "rb").read()).hexdigest() for d in ("ref", "new")]
r, n = (json.load(open(os.path.join(out, d, "result.json"))) for d in ("ref", "new"))
print(f"\nmeasured.wav sha256: ref {wav[0][:16]}, new {wav[1][:16]}")
print(f"instructions per 128 samples: ref {r['instructions_per_128']:,.0f}, new {n['instructions_per_128']:,.0f} "
      f"({(n['instructions_per_128'] / r['instructions_per_128'] - 1) * 100:+.1f} %)")
if n["early_return_symbol"]:
    print(f"new: early returns {n['early_returns']:,} "
          f"({', '.join(f'{k} {v:,}' for k, v in n['early_returns_by_track'].items())})")
arps = {}
for pos, kind, what in n["arp_notes"]:
    arps[kind] = arps.get(kind, 0) + 1
print("arp notes: " + ", ".join(f"{k} {v}" for k, v in arps.items()))
arp_ok = r["arp_notes"] == n["arp_notes"] and all(arps.get(k, 0) >= 8 for k in ("kit arp", "MIDI row", "gate row"))
if not arp_ok:
    print(f"[FAIL] the arpeggiators' notes: ref {len(r['arp_notes'])}, new {len(n['arp_notes'])}, "
          f"{'the same' if r['arp_notes'] == n['arp_notes'] else 'different'} (each arp must step 8 times or more)")
peak = float(np.abs(a).max())
print(f"peak {peak:.3f} of full scale" + (" (clipped: the 16 bits hide differences, measured.npy doesn't)"
                                          if peak >= 1 else ""))
if a.shape == b.shape and np.array_equal(a, b) and wav[0] == wav[1]:
    print(f"[ok] the same output, sample for sample ({len(a):,} samples)")
    if arp_ok:
        print(f"[ok] the same arpeggiator notes, in the same windows ({len(n['arp_notes'])})")
    sys.exit(0 if arp_ok else 1)
m = min(len(a), len(b))
diff = np.nonzero((a[:m] != b[:m]).any(axis=1))[0]
first = int(diff[0]) if len(diff) else m
print(f"[FAIL] the outputs differ: {len(a):,} / {len(b):,} samples, the first difference at sample {first:,} "
      f"(bar {first / 88200:.3f}), {len(diff):,} samples differ")
sys.exit(1)
PY
