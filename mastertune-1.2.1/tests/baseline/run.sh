#!/bin/sh
# Tests for tools/baseline_check.py (the songs of a card against the baseline master). Needs python3, nothing else.
# Usage: ./run.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
exec python3 -B test_baseline_check.py "$@"
