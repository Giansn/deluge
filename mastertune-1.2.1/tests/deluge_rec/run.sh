#!/bin/sh
# Tests for tools/deluge_rec.py (DELUGE USB REC) without audio hardware and without a display: sounddevice and tkinter
# are stubs. Needs python3 with numpy.   Usage: ./run.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
exec python3 -B test_deluge_rec.py "$@"
