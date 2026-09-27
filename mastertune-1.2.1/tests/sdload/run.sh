#!/bin/bash
# A big SD card, many files and big samples on the real firmware in the emulator (sdload_emu.py, images from
# make_bigsd.py): mounting and the first save on a full 32 GB card, loading a big project, the idle CPU as the CPU
# monitor shows it with the firmware's own task manager, streaming while the card takes time, and the song browser
# in a SONGS folder of 1,200 songs. One emulator at a time; the images are sparse (a 32 GB card takes ~20-60 MB on
# disk) and deleted after each run.
#
# Usage: ./run.sh <firmware tree | deluge.elf> [out dir] [scenario ...]
#   Scenarios (default all, in this order):
#     s1      small card and 32 GB card with 20 GB of other files, FSInfo valid / invalid / stale: boot, the default
#             song, then SONGS/SAVETEST.XML saved (the first free cluster is searched from FSInfo's hint); the cards
#             with FSInfo invalid / stale booted a second time (s1-*-again: boot wrote FSInfo back)
#     s2      the big project (64 synths, 14 kits: 170 rows with their own 0.5 MB samples, 30 audio tracks with 14 MB
#             samples, the 200 spread over a folder of 3,000 long-named files) on a full 32 GB card, contiguous; then
#             10 s idle with the CPU monitor, 5 s more with the playback routine's target interval as deluge.cpp:584
#             means it (16/44100. s, not 0), and the lines of RAM the audio routine touches
#     s2frag  the same with the 200 samples fragmented (8-cluster runs), loading only
#     s3ref   the idle reference: the same 64 synths and 4 kits without the 200 samples, on the small card, 10 s idle,
#             5 s with the target interval fixed
#     s4      streaming: 16 audio tracks of long stereo samples (2.8 MB/s) + a synth, a kit and a loop, 4 s played
#             with the card instant / typical (1 ms per command, 12 MB/s) / slow (3 ms, 6 MB/s); the card's waits
#             yield to the task manager as the firmware's (USE_TASK_MANAGER); then the lines of SDRAM the audio
#             routine touches while it plays (0.02 s more)
#     s4ipc   s4 with the typical card and the CPU at 0.5 instructions per cycle (the emulator counts 1: no caches)
#     s2play  the big project of s2 played 4 s with the typical card, the CPU at 1 and at 0.5 instructions per cycle
#             (far beyond the CPU: it is for the load and the idle CPU, not a song to play)
#     s5      the song browser: SONGS with 1,200 songs (with previews), the select encoder turned through all of
#             them, 20 ms of the task manager after each step
#   IDLE_S (default 10), FIXED_S (5), PLAY_S (4), STEPS (1250), SETTLE_MS (20) change the durations.
# Results: <out>/<scenario>.json and the log (<out>/log.txt). Card profiles for the estimates, in sdload_emu.py:
#   typical 1 ms per command + 12 MB/s, fast 0.25 ms + 25 MB/s (the CPU at 400 MHz, 1 instruction per cycle; the
#   driver's own work per command not counted, a lower bound).
# Needs: python3 with unicorn 2 and numpy, a C compiler (blockcount.c), the tree's toolchain (nm, objdump, gdb).
# About 25 minutes for all.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -d "$1" ]; then
	ELF=$(cd "$1" && pwd)/build/Release/deluge.elf
else
	ELF=$(realpath "$1")
fi
OUT=$(realpath -m "${2:-$(mktemp -d)}")
shift 2 || shift $#
SCENARIOS=${*:-s1 s2 s2frag s3ref s4 s4ipc s2play s5}
mkdir -p "$OUT"
UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
cc -O2 -shared -fPIC -I"$UC/include" "$HERE/../song/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" \
	-l:libunicorn.so.2 -Wl,-rpath,"$UC/lib"
cd "$OUT"

emu() { # image, name, sdload_emu.py arguments; with KEEP=1 the image stays for another run
	img=$1 name=$2
	shift 2
	echo "== $name"
	python3 "$HERE/sdload_emu.py" "$ELF" "$img" "$OUT" "$@" --name "$name" --build "$OUT" | grep -v "^  \.\.\."
	[ -n "${KEEP:-}" ] || rm -f "${img:?}" "${img:?}.json"
	echo
}
image() { # name, make_bigsd.py arguments
	python3 "$HERE/make_bigsd.py" "$OUT/$1.img" "${@:2}"
}

for s in $SCENARIOS; do
	case $s in
	s1)
		image small small && emu "$OUT/small.img" s1-small load --save
		for f in valid invalid stale; do
			image "s1-$f" s1 --fsinfo "$f"
			if [ $f = valid ]; then
				emu "$OUT/s1-$f.img" "s1-$f" load --save
			else
				KEEP=1 emu "$OUT/s1-$f.img" "s1-$f" load --save
				emu "$OUT/s1-$f.img" "s1-$f-again" load
			fi
		done ;;
	s2)
		image s2 s2 && emu "$OUT/s2.img" s2 load --idle "${IDLE_S:-10}" --idle-fixed "${FIXED_S:-5}" --lines ;;
	s2frag)
		image s2frag s2 --fragment 8 && emu "$OUT/s2frag.img" s2frag load ;;
	s3ref)
		image s3ref bigidle && emu "$OUT/s3ref.img" s3ref load --idle "${IDLE_S:-10}" --idle-fixed "${FIXED_S:-5}" \
			--lines ;;
	s4)
		for m in instant typical slow; do
			image "s4-$m" s4
			case $m in
			instant) lat=() ;;
			typical) lat=(--sd-latency 1000,42.67) ;;
			slow) lat=(--sd-latency 3000,85.33) ;;
			esac
			emu "$OUT/s4-$m.img" "s4-$m" play --seconds "${PLAY_S:-4}" "${lat[@]}" --lines
		done ;;
	s4ipc)
		image s4ipc s4 && emu "$OUT/s4ipc.img" s4-typical-ipc0.5 play --seconds "${PLAY_S:-4}" --sd-latency 1000,42.67 \
			--ipc 0.5 ;;
	s2play)
		image s2play s2
		KEEP=1 emu "$OUT/s2play.img" s2play-typical play --seconds "${PLAY_S:-4}" --sd-latency 1000,42.67
		emu "$OUT/s2play.img" s2play-typical-ipc0.5 play --seconds "${PLAY_S:-4}" --sd-latency 1000,42.67 --ipc 0.5 ;;
	s5)
		image s5 s5 && emu "$OUT/s5.img" s5 browse --steps "${STEPS:-1250}" --settle-ms "${SETTLE_MS:-20}" ;;
	*)
		echo "unknown scenario $s"; exit 2 ;;
	esac
done 2>&1 | tee -a "$OUT/log.txt"
echo "results in $OUT"
