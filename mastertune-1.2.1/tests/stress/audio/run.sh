#!/bin/sh
# Audio stress test of two builds (e.g. mastertune v18 against v17) on the real firmware in the emulator: each scenario
# plays the same song on both in real time for many bars (audio_stress_emu.py; its docstring says what is modelled and
# measured) and summary.py puts the results side by side.
#
# Usage: ./run.sh <new: firmware tree | deluge.elf> <old: firmware tree | deluge.elf> [out dir] [scenario ...]
#   With a tree, it takes build/Release/deluge.elf and the tree's toolchain (nm, objdump, gdb).
# Scenarios (default: all, in this order):
#   full        make_sd.py's default song (8 synths, kit, audio track, reverb, sidechain, drone), BARS_LONG bars
#   full16      the same with 16 synths (stress_sd.py --song synths16), BARS_LONG bars
#   drive       every LPF in the drive mode (24dBDrive), BARS bars: the new build's CPU guard switches the drive
#               ladder's 2x oversampling off at cpuDireness 14 (FilterSet::setConfig()), crossfaded; how often that
#               happens, and whether the output has clicks or the DMA underruns
#   automation  a node every 16th note on every filter frequency, resonance and morph, EQ bass and treble, volume and
#               pan (stress_sd.py --song automation), and the song's own params jumping as often (--song-storm), BARS
#   storm       the default song with every filter's modes and route switched every 2 to 6 windows (--mode-storm),
#               BARS bars: the new build with the output limiter and the filter crossing guard both on (CommunityFea-
#               tures.XML, "on") and both off ("off"); the old build with the same card as "on" (it has neither)
#   clipping    the song pushed into clipping (every volume at the knob's maximum) and the mode storm, BARS_CLIP bars,
#               new on / new off / old: with the limiter on no sample may clip
# BARS_LONG (default 64), BARS (32), BARS_CLIP (8), WARMUP (1): bars; the measurement starts after the warm-up.
# Results: <out>/<scenario>-<build>/result.json, stress.wav, log; <out>/summary.md. Run one at a time (one emulator
# process); about 15 s per bar at the default song's load (the full list at the default bars: about 3 hours).
# Needs: python3 with unicorn 2 and numpy, a C compiler (../../song/blockcount.c, unless BLOCKCOUNT_DIR has
# blockcount.so).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SONG="$HERE/../../song"
elf_of() {
	if [ -d "$1" ]; then echo "$(cd "$1" && pwd)/build/Release/deluge.elf"; else echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; fi
}
tools_of() {
	if [ -d "$1" ]; then echo "$(cd "$1" && pwd)/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-"
	else echo "$(cd "$(dirname "$1")/../.." && pwd)/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-"; fi
}
[ $# -ge 2 ] || { sed -n '2,30p' "$0"; exit 2; }
NEW=$(elf_of "$1"); NEW_TOOLS=$(tools_of "$1")
OLD=$(elf_of "$2"); OLD_TOOLS=$(tools_of "$2")
OUT=${3:-$(mktemp -d)}
shift $(( $# < 3 ? $# : 3 ))
SCENARIOS=${*:-full full16 drive automation storm clipping}
for f in "$NEW" "$OLD"; do [ -f "$f" ] || { echo "no $f"; exit 2; }; done
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
BARS_LONG=${BARS_LONG:-64}
BARS=${BARS:-32}
BARS_CLIP=${BARS_CLIP:-8}
WARMUP=${WARMUP:-1}

if [ -z "$BLOCKCOUNT_DIR" ] || [ ! -f "$BLOCKCOUNT_DIR/blockcount.so" ]; then
	UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
	cc -O2 -shared -fPIC -I"$UC/include" "$SONG/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" -l:libunicorn.so.2 \
		-Wl,-rpath,"$UC/lib"
	BLOCKCOUNT_DIR=$OUT
fi
export BLOCKCOUNT_DIR

# case <name> <build: new|old> <label> <bars> <stress_sd.py options> -- <audio_stress_emu.py options>
case_run() {
	name=$1 build=$2 label=$3 bars=$4
	shift 4
	sd_opts=""
	while [ $# -gt 0 ] && [ "$1" != "--" ]; do sd_opts="$sd_opts $1"; shift; done
	[ "$1" = "--" ] && shift
	dir="$OUT/$name-$label"
	mkdir -p "$dir"
	if [ "$build" = new ]; then elf=$NEW tools=$NEW_TOOLS; else elf=$OLD tools=$OLD_TOOLS; fi
	echo "== $name, $label ($(basename "$(dirname "$(dirname "$(dirname "$elf")")")")): $bars bars;$sd_opts $*"
	python3 "$HERE/stress_sd.py" "$dir/sd.img" $sd_opts --xml-out "$dir/song.xml" > /dev/null
	status=0
	python3 "$HERE/audio_stress_emu.py" "$elf" "$dir/sd.img" "$dir" --tools "$tools" --bars "$bars" \
		--warmup-bars "$WARMUP" "$@" > "$dir/log" 2>&1 || status=$?
	sed -n '/^warm-up/,$p' "$dir/log"
	[ $status -eq 0 ] || echo "   exit $status"
	rm -f "$dir/sd.img"
	echo
}

for s in $SCENARIOS; do
	case $s in
	full)
		case_run full new new "$BARS_LONG" --song base
		case_run full old old "$BARS_LONG" --song base ;;
	full16)
		case_run full16 new new "$BARS_LONG" --song synths16
		case_run full16 old old "$BARS_LONG" --song synths16 ;;
	drive)
		case_run drive new new "$BARS" --song drive
		case_run drive old old "$BARS" --song drive ;;
	automation)
		case_run automation new new "$BARS" --song automation -- --song-storm
		case_run automation old old "$BARS" --song automation -- --song-storm ;;
	storm)
		case_run storm new new-on "$BARS" --song base --limiter --guard -- --mode-storm
		case_run storm new new-off "$BARS" --song base -- --mode-storm
		case_run storm old old "$BARS" --song base --limiter --guard -- --mode-storm ;;
	clipping)
		case_run clipping new new-on "$BARS_CLIP" --song clipping --limiter --guard -- --mode-storm
		case_run clipping new new-off "$BARS_CLIP" --song clipping -- --mode-storm
		case_run clipping old old "$BARS_CLIP" --song clipping --limiter --guard -- --mode-storm ;;
	*) echo "unknown scenario $s"; exit 2 ;;
	esac
done
python3 "$HERE/summary.py" "$OUT" | tee "$OUT/summary.md"
echo "results in $OUT"
