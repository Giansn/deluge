#!/bin/sh
# Tests for tools/baseline_check.py and tools/deluge_baseline.py (DelugeBaseline): checking the songs of a card, setting
# them and the samples to the baseline master. Needs python3 with numpy; with tkinter and a display (or Windows) also
# the self-test with its window.   Usage: ./run.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
python3 -B test_baseline_check.py "$@"
exec python3 -B test_deluge_baseline.py "$@"
