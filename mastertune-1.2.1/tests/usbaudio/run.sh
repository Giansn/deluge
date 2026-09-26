#!/bin/sh
# Host tests for USB audio (v8): the descriptors against the USB 2.0, USB Audio Class 1.0 and USB MIDI rules, and the
# ring buffer / packet sizes against clock drift, a computer that stops reading and an engine stall (with sanitizers).
# Usage: ./run.sh /path/to/DelugeFirmware (checked out at mastertune-v8)
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../arm/select.sh"
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
CC="gcc -w"
CXX="g++ -std=c++20 -O2 -fsanitize=address,undefined"
RUN=""
[ -n "$ARM" ] && CC="$ARM_CXX" && CXX="$ARM_CXX" && RUN="$ARM_RUN"
export RUN
$CC -DUSB_CFG_PMIDI_USE -I "$FW/src" -I "$FW/src/deluge" -o "$B/dump" "$HERE/dump.c" \
    "$FW/src/deluge/io/usb/usb_audio_descriptor.c" "$FW/src/deluge/io/midi/r_usb_pmidi_descriptor.c" \
    "$FW/src/RZA1/usb/r_usb_basic/src/driver/r_usb_peptable.c"
(cd "$B" && python3 "$HERE/check_desc.py" | tail -1)
$CXX -I "$FW/src/deluge" -o "$B/stream_sim" "$HERE/stream_sim.cpp"
$RUN "$B/stream_sim" | tail -1
