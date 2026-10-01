#!/bin/bash
# Sets up the community beta (v1.3) with our fixes, for a fresh container: the community firmware's source at the
# beta 62a516c2 (cloned read-only: pushing is disabled, see CLAUDE.md), beta-1.3/patches applied on a local branch,
# the dbt toolchain v22 (GCC 14; the beta doesn't build with v16), a Release build, and blockcount.so for the emulator
# tests. Idempotent: what is already there is kept (an existing tree is not patched again).
#
# Usage: beta-1.3/tools/setup_beta.sh
# Environment (defaults):
#   WORK=/home/user/work
#   UPSTREAM=$WORK/DelugeFirmware-beta   the clone of SynthstromAudible/DelugeFirmware (blob-less, read-only)
#   TREE=$WORK/beta-fixes                the firmware tree: a worktree at 62a516c2, the patches on it
#   BRANCH=beta-fixes                    its local branch (never pushed)
#   TOOLCHAIN=$WORK/tc22                 holds toolchain/v22/linux-x86_64 (compiler, cmake, ninja: about 700 MB)
#   NO_BUILD=1                           stop after the tree and the toolchain
# Disk: about 2 GB. Afterwards: beta-1.3/tests/repro/run.sh <ELF> <tools> <blockcount dir> runs the fixes' tests.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
BETA=$REPO/beta-1.3
BASE_SHORT=62a516c2
WORK=${WORK:-/home/user/work}
UPSTREAM=${UPSTREAM:-$WORK/DelugeFirmware-beta}
TREE=${TREE:-$WORK/beta-fixes}
BRANCH=${BRANCH:-beta-fixes}
TOOLCHAIN=${TOOLCHAIN:-$WORK/tc22}
URL=https://github.com/SynthstromAudible/DelugeFirmware
TC_URL=https://github.com/SynthstromAudible/dbt-toolchain/releases/download/v22/dbt-toolchain-22-linux-x86_64.tar.gz
say() { printf '\n== %s\n' "$*"; }

say "Python packages for the tests"
python3 -c 'import unicorn, numpy' 2>/dev/null \
  || python3 -m pip install --quiet -r "$REPO/mastertune-1.2.1/tools/requirements-tests.txt"

say "The community firmware's source (read-only): $UPSTREAM"
if [ ! -d "$UPSTREAM/.git" ]; then
  mkdir -p "$(dirname "$UPSTREAM")"
  GIT_LFS_SKIP_SMUDGE=1 git clone --filter=blob:none --no-checkout "$URL" "$UPSTREAM"
  git -C "$UPSTREAM" config gc.auto 0
fi
# Nothing is ever pushed to the community repository (CLAUDE.md)
git -C "$UPSTREAM" remote set-url --push origin PUSH_DISABLED_community_repo_is_read_only
git -C "$UPSTREAM" rev-parse -q --verify "$BASE_SHORT^{commit}" > /dev/null || git -C "$UPSTREAM" fetch -q origin main --tags
BASE=$(git -C "$UPSTREAM" rev-parse "$BASE_SHORT^{commit}")

say "The tree: $TREE (beta $BASE_SHORT + beta-1.3/patches, local branch $BRANCH)"
if [ ! -d "$TREE" ]; then
  git -C "$UPSTREAM" worktree add -q --no-checkout -b "$BRANCH" "$TREE" "$BASE"
  # The website and contrib folders aren't needed; the menu generator needs docs/menus
  git -C "$TREE" sparse-checkout set --no-cone '/*' '!/website/' '!/contrib/' '!/docs/' '/docs/menus/'
  git -C "$TREE" checkout -q "$BRANCH"
  git -C "$TREE" -c user.name=beta-fixes -c user.email=beta-fixes@localhost am -q --committer-date-is-author-date \
    "$BETA"/patches/0*.patch
fi
echo "tree $(git -C "$TREE" rev-parse 'HEAD^{tree}'), $(git -C "$TREE" rev-list --count "$BASE"..HEAD) patches on $BASE_SHORT"

say "The toolchain v22: $TOOLCHAIN/toolchain/v22/linux-x86_64"
TC=$TOOLCHAIN/toolchain/v22/linux-x86_64
if [ ! -x "$TC/arm-none-eabi-gcc/bin/arm-none-eabi-gcc" ]; then
  mkdir -p "$TC"
  curl -sSL "$TC_URL" | tar -xz -C "$TC" --strip-components=1 --wildcards \
    '*/arm-none-eabi-gcc/*' '*/cmake/*' '*/ninja-build/*' \
    --exclude='*/thumb/v6*' --exclude='*/thumb/v7-m*' --exclude='*/thumb/v7e*' --exclude='*/thumb/v7-r*' \
    --exclude='*/thumb/v7ve*' --exclude='*/thumb/v8*' --exclude='*/thumb/v7+*' --exclude='*/thumb/nofp*' \
    --exclude='*/lib/arm/*' --exclude='*/share/doc/*'
fi
"$TC/arm-none-eabi-gcc/bin/arm-none-eabi-gcc" --version | head -1
mkdir -p "$TREE/toolchain"
[ -e "$TREE/toolchain/v22" ] || ln -s "$TOOLCHAIN/toolchain/v22" "$TREE/toolchain/v22"
[ "${NO_BUILD:-}" = 1 ] && exit 0

say "Release build"
export PATH=$TC/cmake/bin:$TC/ninja-build/bin:$TC/ninja-build:$PATH DBT_TOOLCHAIN_PATH=$TOOLCHAIN DELUGE_FW_ROOT=$TREE
if [ ! -f "$TREE/build/build-Release.ninja" ]; then
  cmake -B "$TREE/build" -S "$TREE" -G "Ninja Multi-Config" -DCMAKE_CROSS_CONFIGS:STRING=all \
    "-DCMAKE_DEFAULT_CONFIGS=Debug;Release" -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=TRUE -DRELEASE_TYPE:STRING=beta \
    > "$TREE/build-configure.log" || { tail -20 "$TREE/build-configure.log"; exit 1; }
fi
cmake --build "$TREE/build" --config Release > "$TREE/build-release.log" 2>&1 \
  || { tail -20 "$TREE/build-release.log"; exit 1; }
ELF=$(ls -t "$TREE"/build/Release/*.elf | head -1)

say "blockcount.so (the emulator's instruction counter): $WORK/blockcount"
mkdir -p "$WORK/blockcount"
if [ ! -f "$WORK/blockcount/blockcount.so" ]; then
  UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
  cc -O2 -shared -fPIC -I"$UC/include" "$REPO/mastertune-1.2.1/tests/song/blockcount.c" \
    -o "$WORK/blockcount/blockcount.so" -L"$UC/lib" -l:libunicorn.so.2 -Wl,-rpath,"$UC/lib"
fi

say "Ready"
cat <<EOF
ELF:   $ELF
Tools: $TC/arm-none-eabi-gcc/bin/arm-none-eabi-
Tests: beta-1.3/tests/repro/run.sh "$ELF" $TC/arm-none-eabi-gcc/bin/arm-none-eabi- $WORK/blockcount
EOF
