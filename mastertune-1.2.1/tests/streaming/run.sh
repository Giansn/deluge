#!/bin/sh
# Host test for the cluster loading queue (v9): most urgent first, first come first served among equal priorities,
# lowest-priority detection. Runs the firmware's own ClusterPriorityQueue and OrderedResizeableArray code, built for
# 32-bit x86 (the container code assumes 32-bit pointers), with UndefinedBehaviorSanitizer.
# Needs a 32-bit capable g++ (Debian/Ubuntu: apt install g++-multilib).
# Usage: ./run.sh /path/to/DelugeFirmware (checked out at mastertune-v9)
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../arm/select.sh"
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
CXX="g++ -m32 -std=gnu++23 -O1 -g -fsanitize=undefined -w"
RUN=""
[ -n "$ARM" ] && CXX="$ARM_CXX" && RUN="$ARM_RUN"
$CXX -I "$HERE/stubs" -I "$D/deluge" -I "$D" -include host_shim.h \
    -o "$B/queue_test" "$HERE/queue_test.cpp" "$HERE/link_stubs.cpp" "$D/deluge/storage/cluster/cluster_priority_queue.cpp" \
    "$D/deluge/util/container/array/ordered_resizeable_array.cpp" "$D/deluge/util/container/array/resizeable_array.cpp"
$RUN "$B/queue_test" | tail -1
