#!/bin/sh
# Host test for the cluster loading queue (v9): most urgent first, first come first served among equal priorities,
# lowest-priority detection. Runs the firmware's own ClusterPriorityQueue and OrderedResizeableArray code, built for
# 32-bit x86 (the container code assumes 32-bit pointers), with UndefinedBehaviorSanitizer.
# Needs a 32-bit capable g++ (Debian/Ubuntu: apt install g++-multilib).
# Usage: ./run.sh /path/to/DelugeFirmware (checked out at mastertune-v9)
set -e
FW=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
B=$(mktemp -d)
trap 'rm -rf "$B"' EXIT
D="$FW/src"
g++ -m32 -std=gnu++23 -O1 -g -fsanitize=undefined -w -I "$HERE/stubs" -I "$D/deluge" -I "$D" -include host_shim.h \
    -o "$B/queue_test" "$HERE/queue_test.cpp" "$HERE/link_stubs.cpp" "$D/deluge/storage/cluster/cluster_priority_queue.cpp" \
    "$D/deluge/util/container/array/ordered_resizeable_array.cpp" "$D/deluge/util/container/array/resizeable_array.cpp"
"$B/queue_test" | tail -1
