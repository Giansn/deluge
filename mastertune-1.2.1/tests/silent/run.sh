#!/bin/sh
# Silent kits and audio tracks (mastertune-v17): silent_emu.py's song (tracks that play, stop with long tails, are
# changed while silent and start again; see its docstring) on two builds, compared sample for sample. The build
# without the early return (the reference) and the one with it must give the same output; the latter's result.json
# says how often each track took the early return.
#
# Usage: ./run.sh <reference tree | deluge.elf> <tree | deluge.elf> [out dir]
#   With a tree, it takes build/Release/deluge.elf and the tree's toolchain (gdb); with an ELF, TOOLS (the toolchain
#   prefix, .../arm-none-eabi-) or the toolchain two directories up from it.
# Results: <out>/ref/ and <out>/new/ (measured.wav, measured.npy, result.json, song.xml). About 1 minute.
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
peak = float(np.abs(a).max())
print(f"peak {peak:.3f} of full scale" + (" (clipped: the 16 bits hide differences, measured.npy doesn't)"
                                          if peak >= 1 else ""))
if a.shape == b.shape and np.array_equal(a, b) and wav[0] == wav[1]:
    print(f"[ok] the same output, sample for sample ({len(a):,} samples)")
    sys.exit(0)
m = min(len(a), len(b))
diff = np.nonzero((a[:m] != b[:m]).any(axis=1))[0]
first = int(diff[0]) if len(diff) else m
print(f"[FAIL] the outputs differ: {len(a):,} / {len(b):,} samples, the first difference at sample {first:,} "
      f"(bar {first / 88200:.3f}), {len(diff):,} samples differ")
sys.exit(1)
PY
