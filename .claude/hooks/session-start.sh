#!/bin/bash
# Session start (Claude Code on the web only): the Python packages the emulator tests need. Light and idempotent; the
# firmware itself (upstream clone, patches, toolchain, build: about 1.3 GB) is set up on demand with
# mastertune-1.2.1/tools/setup_firmware.sh (see mastertune-1.2.1/SETUP.md).
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

REQ="$CLAUDE_PROJECT_DIR/mastertune-1.2.1/tools/requirements-tests.txt"
if ! python3 -c 'import unicorn, numpy, scipy, soundfile, PIL, pymupdf, pyflakes; assert unicorn.__version__.startswith("2.")' 2>/dev/null; then
  python3 -m pip install --quiet --disable-pip-version-check -r "$REQ"
fi

# Where tools/setup_firmware.sh puts the firmware tree and blockcount.so, if it ran in this container before
WORK=/home/user/work
if [ -f "$WORK/blockcount/blockcount.so" ] && [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export BLOCKCOUNT_DIR=$WORK/blockcount" >> "$CLAUDE_ENV_FILE"
fi
echo "mastertune: test packages ready. Firmware build and emulator tests: mastertune-1.2.1/tools/setup_firmware.sh (SETUP.md)"
