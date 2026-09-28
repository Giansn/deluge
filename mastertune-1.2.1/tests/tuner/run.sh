#!/bin/sh
# Tests for tools/deluge_tuner.py (DelugeTuner: retune_library.py in a window). Needs python3 with numpy and soxr
# (pylibrb for the self-test); with tkinter and a display (or Windows) also the window and the self-test.
# retune_library.py's own tests: tests/retune/run.sh.   Usage: ./run.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
exec python3 -B test_deluge_tuner.py "$@"
