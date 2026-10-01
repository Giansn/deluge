#!/bin/bash
# Sets up the mastertune firmware build and the emulator tests in a fresh container (Claude Code on the web or any
# Linux x86-64): the community firmware's source at release_1_2_1, mastertune's patches on it, the dbt toolchain v16,
# a Release build, a check that it is bit for bit the released .bin, and blockcount.so for the emulator tests.
# Idempotent: what is already there is kept (an existing tree is checked, not patched again).
#
# Usage: mastertune-1.2.1/tools/setup_firmware.sh [VERSION]      (default v19.0.3; one of the l2d releases below)
# Environment (defaults):
#   WORK=/home/user/work            where everything goes (outside the repo)
#   UPSTREAM=$WORK/DelugeFirmware-121   the clone of SynthstromAudible/DelugeFirmware (blob-less, about 270 MB)
#   TREE=$WORK/mastertune-<VERSION>     the firmware tree (a git worktree of UPSTREAM at release_1_2_1 + the patches)
#   TOOLCHAIN=$WORK/dbt               holds toolchain/v16/linux-x86_64 (arm-none-eabi-gcc for the Cortex-A9 only,
#                                     cmake, ninja: about 600 MB of the 440 MB download)
#   NO_BUILD=1                        stop after the tree and the toolchain
# Disk: about 1.3 GB in all (clone, tree, toolchain, build). Network: github.com (the clone and the toolchain).
# Afterwards the tests run as SETUP.md says, e.g.:
#   python3 mastertune-1.2.1/tests/songchange/songchange_emu.py $TREE/build/Release/deluge.elf \
#       --tools $TOOLCHAIN/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi- --build $WORK/blockcount \
#       --scenario countin --out /tmp/out
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
MT=$REPO/mastertune-1.2.1
VERSION=${1:-v19.0.3}
WORK=${WORK:-/home/user/work}
UPSTREAM=${UPSTREAM:-$WORK/DelugeFirmware-121}
TREE=${TREE:-$WORK/mastertune-$VERSION}
TOOLCHAIN=${TOOLCHAIN:-$WORK/dbt}
URL=https://github.com/SynthstromAudible/DelugeFirmware
TC_URL=https://github.com/SynthstromAudible/dbt-toolchain/releases/download/v16/dbt-toolchain-16-linux-x86_64.tar.gz

# The l2d releases: version, the last patch, the source tree the patches give (git write-tree), as in the README
case "$VERSION" in
  v19.0.3) LAST=0122 TREE_ID=520ba46244c6356e3cf9002d7a7e2ab096372f2c ;;
  v19.0.2) LAST=0121 TREE_ID= ;;
  v19.0.1) LAST=0120 TREE_ID= ;;
  v19.0) LAST=0119 TREE_ID= ;;
  v18.4) LAST=0118 TREE_ID= ;;
  *) echo "unknown version $VERSION (v19.0.3, v19.0.2, v19.0.1, v19.0, v18.4)"; exit 2 ;;
esac
BIN=$(ls "$MT"/deluge-1.2.1-mastertune-"$VERSION"-l2d-*.bin 2>/dev/null | head -1)
[ -n "$BIN" ] || { echo "no released .bin for $VERSION in $MT"; exit 2; }
HASH=$(basename "$BIN" .bin | sed 's/.*-//')  # the commit hash the release was built from (in its file name)
say() { printf '\n== %s\n' "$*"; }

say "Python packages for the tests"
python3 -c 'import unicorn, numpy' 2>/dev/null || python3 -m pip install --quiet -r "$MT/tools/requirements-tests.txt"

say "The community firmware's source: $UPSTREAM"
if [ ! -d "$UPSTREAM/.git" ]; then
  mkdir -p "$(dirname "$UPSTREAM")"
  GIT_LFS_SKIP_SMUDGE=1 git clone --filter=blob:none --no-checkout "$URL" "$UPSTREAM"
  git -C "$UPSTREAM" config gc.auto 0  # no background repack (it needs the disk twice over)
fi
git -C "$UPSTREAM" rev-parse -q --verify release_1_2_1 > /dev/null || git -C "$UPSTREAM" fetch origin tag release_1_2_1

say "The firmware tree: $TREE (release_1_2_1 + patches 0001-$LAST + l2test 0001-0003)"
if [ ! -d "$TREE" ]; then
  git -C "$UPSTREAM" worktree add -q --detach "$TREE" release_1_2_1
  patches=$(ls "$MT"/patches/0*.patch | awk -v last="$LAST" '{n=$0; sub(/.*\//, "", n); if (substr(n, 1, 4) <= last) print}')
  git -C "$TREE" -c user.name=mastertune -c user.email=mastertune@localhost am -q --committer-date-is-author-date \
    $patches "$MT"/l2test/0*.patch
fi
tree=$(git -C "$TREE" rev-parse 'HEAD^{tree}')
if [ -n "$TREE_ID" ] && [ "$tree" != "$TREE_ID" ]; then
  echo "the tree is $tree, the release's is $TREE_ID: not the source of $VERSION"; exit 1
fi
[ -n "$TREE_ID" ] && echo "source tree $tree, the same as the release" || echo "source tree $tree"

say "The toolchain: $TOOLCHAIN/toolchain/v16/linux-x86_64"
TC=$TOOLCHAIN/toolchain/v16/linux-x86_64
if [ ! -x "$TC/arm-none-eabi-gcc/bin/arm-none-eabi-gcc" ]; then
  mkdir -p "$TC"
  # Only what the build needs: the compiler with the Cortex-A9 libraries (thumb/v7-a+simd), cmake and ninja
  curl -sSL "$TC_URL" | tar -xz -C "$TC" --strip-components=1 --wildcards \
    '*/arm-none-eabi-gcc/*' '*/cmake/*' '*/ninja-build/*' \
    --exclude='*/thumb/v6*' --exclude='*/thumb/v7-m*' --exclude='*/thumb/v7e*' --exclude='*/thumb/v7-r*' \
    --exclude='*/thumb/v7ve*' --exclude='*/thumb/v8*' --exclude='*/thumb/v7+*' --exclude='*/thumb/nofp*' \
    --exclude='*/lib/arm/*' --exclude='*/share/doc/*'
fi
"$TC/arm-none-eabi-gcc/bin/arm-none-eabi-gcc" --version | head -1
# Where the tests look for it by default (tests/song/run.sh with a tree, the ELF's ../../toolchain/v16)
[ -e "$TREE/toolchain/v16" ] || ln -s "$TOOLCHAIN/toolchain/v16" "$TREE/toolchain/v16"
[ "${NO_BUILD:-}" = 1 ] && exit 0

say "argon (a library the build takes from GitHub): $WORK/argon at v0.1.0"
# lib/CMakeLists.txt downloads its release archive, which the cloud session's proxy refuses (403); a git clone of the
# same tag (caa7cd80) works, and FETCHCONTENT_SOURCE_DIR_ARGON points the build at it
if [ ! -d "$WORK/argon/.git" ]; then
  git clone -q --depth 1 --branch v0.1.0 https://github.com/stellar-aria/argon "$WORK/argon"
fi
[ "$(git -C "$WORK/argon" rev-parse HEAD)" = caa7cd8056351d4fea353c1e572fa62e04a49971 ] || { echo "argon is not v0.1.0"; exit 1; }

say "Release build ($VERSION, commit hash $HASH as in the release)"
export PATH=$TC/cmake/bin:$TC/ninja-build/bin:$TC/ninja-build:$PATH DBT_TOOLCHAIN_PATH=$TOOLCHAIN DELUGE_FW_ROOT=$TREE
if [ ! -f "$TREE/build/build-Release.ninja" ]; then
  cmake -B "$TREE/build" -S "$TREE" -G "Ninja Multi-Config" -DCMAKE_CROSS_CONFIGS:STRING=all \
    "-DCMAKE_DEFAULT_CONFIGS=Debug;Release" -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=TRUE \
    -DRELEASE_TYPE:STRING="mastertune-$VERSION-l2d" -DFETCHCONTENT_SOURCE_DIR_ARGON:PATH="$WORK/argon" \
    > "$TREE/build-configure.log" || { tail -20 "$TREE/build-configure.log"; exit 1; }
fi
# The release has its own commit's hash built in (the crash display); git am made other hashes, so set it
python3 - "$TREE/build/src/deluge/version/version.cmake" "$HASH" <<'PY'
import re, sys
p, h = sys.argv[1], sys.argv[2]
s = open(p).read()
s = re.sub(r'execute_process\(\s*COMMAND \S+ rev-parse --short HEAD.*?\)\n', f'set(GIT_COMMIT_SHORT "{h}")\n', s,
           flags=re.S)
open(p, "w").write(s)
PY
cmake --build "$TREE/build" --config Release > "$TREE/build-release.log" 2>&1 || { tail -20 "$TREE/build-release.log"; exit 1; }
built=$(sha256sum "$TREE/build/Release/deluge.bin" | cut -c1-64)
released=$(sha256sum "$BIN" | cut -c1-64)
echo "built    $built"
echo "released $released  $(basename "$BIN")"
if [ "$built" != "$released" ]; then
  echo "not bit for bit the release (the first build after a configure has differed once: run this script again)"
  exit 1
fi
echo "bit for bit the released $VERSION"

say "blockcount.so (the emulator's instruction counter): $WORK/blockcount"
mkdir -p "$WORK/blockcount"
if [ ! -f "$WORK/blockcount/blockcount.so" ]; then
  UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
  cc -O2 -shared -fPIC -I"$UC/include" "$MT/tests/song/blockcount.c" -o "$WORK/blockcount/blockcount.so" \
    -L"$UC/lib" -l:libunicorn.so.2 -Wl,-rpath,"$UC/lib"
fi

say "Ready"
cat <<EOF
ELF:   $TREE/build/Release/deluge.elf
Tools: $TC/arm-none-eabi-gcc/bin/arm-none-eabi-
Tests: export BLOCKCOUNT_DIR=$WORK/blockcount; see mastertune-1.2.1/SETUP.md
EOF
