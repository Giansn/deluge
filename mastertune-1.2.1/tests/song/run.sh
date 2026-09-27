#!/bin/sh
# The whole firmware's audio load with a song where everything plays at once, on the Deluge's Cortex-A9 code in the
# emulator (unicorn): the real deluge.elf boots, loads the song from an SD card image and renders it through
# AudioEngine::routine(). Reports instructions per block of 128 samples (CPU % at 400 MHz, 1 instruction per cycle),
# their spread over the windows, the windows shorter than 128 samples and their cost, voices (in all and per Sound),
# cpuDireness, culls, the output level and a profile by area and function.
#
# Three runs over the same bars:
#   1. demand: no culling for CPU load, cpuDireness 0 (what the song asks for; the task stats read 0)
#   2. device: as on the Deluge, routine() culls voices and sets cpuDireness by its own emulated duration (the task
#      manager's running average of the previous routine() calls, see song_emu.py)
#   3. the Digital reverb instead of Mutable, as 1., compared by total and by the reverb area
#
# Usage: ./run.sh <firmware tree | deluge.elf> [out dir] [bars to measure, default 4]
#   With a tree, it takes build/Release/deluge.elf (built with symbols) and the tree's toolchain (nm, objdump).
#   Results: <out>/result.json (all numbers, per window and per Sound too), <out>/measured.wav (what the codec gets),
#   and the same in <out>/device/ and <out>/digital/.
# Needs: python3 with unicorn 2 and numpy, a C compiler (for blockcount.c). About 2 minutes.
#
# SONG_OPTS: more make_sd.py options for all three runs, e.g. SONG_OPTS=--drone-life (the drone with life, FM and Pulse
#   tones, mastertune-v15: its cost against the default song's, in the profile's drone area)
# EMU_OPTS: more song_emu.py options for all three runs, e.g. EMU_OPTS="--init-sounds --seed 1" ./run.sh <tree> <out>
#   --init-sounds  Sets what Sound::Sound() leaves uninitialised but reads (Sound::globalLFO, ModControllableAudio::
#                  modFXLFO: phase and holdValue; timeStartedSkippingRendering{ModFX,LFO,Arp}), at the start of every
#                  Sound's constructor, as a firmware fix would (offsets from the ELF's debug info, the toolchain's gdb).
#                  Without it measured.wav depends on what the RAM held before: e.g. timeStartedSkippingRenderingLFO of
#                  the first synth holds a stale pointer into .rodata/.data, and the first stopSkippingRendering() ticks
#                  the LFO by audioSampleTimer minus that, so any change of the code or data layout (another
#                  RELEASE_TYPE, padding) changes the LFO phases and thus the audio. Use it to compare builds bit-exactly.
#   --seed N       Sets the random generator (jcong) to N after boot: Song::setupDefault() seeds it from TCNT_0, i.e.
#                  from the emulated instruction count up to there, which a patch touching boot code changes.
#   --fill WORD    Fills the internal RAM and the SDRAM with this 32-bit word before boot (.bss is cleared as by
#                  initsct). Two runs with different words differ exactly when the firmware reads RAM it never wrote.
#                  (With --init-sounds the demand run still differs by 1 LSB in ~70 samples from bar 2 on: other,
#                  minor reads of never-written heap bytes that are zero for every build here.)
# Recipe for bit-exact song comparisons of a patch: EMU_OPTS="--init-sounds --seed 1" for both ELFs, then compare
#   the demand runs' measured.wav (sha256sum); the log prints jcong after boot and the Sounds initialised.
#
# SAVE=1 ./run.sh <tree | deluge.elf> [out dir]: instead of the three runs, saving the playing song (song_emu.py
#   --save-while-playing): after the warm-up the SSI's DMA runs in real time (a sample every 1/44100 s of emulated time,
#   instructions at 400 MHz) and the firmware saves the song (createXMLFile(), Song::writeToFile(),
#   closeFileAfterWriting(), to SONGS/SAVETEST.XML) while it plays, servicing the audio only itself, as on the Deluge.
#   Four cases: the song with 3 and with 5 of its 8 synths (make_sd.py --synths; about 50 % and 70 % CPU, the full song
#   is too heavy for real time without culling), the save starting at bar 2 (the chord change: every synth starts new
#   notes, the heaviest moment) and at bar 2.5. Reports the save's emulated duration by what runs (AudioEngine::routine(),
#   cluster loading, UI timers/OLED/PIC, FatFS, the rest = generating the XML), the routine() calls and the windows they
#   render, and the gaps: how many samples the DMA has read since the output buffer was last full, before every sample
#   written (128 or more = an underrun, the buzz), so the song's output while saving is checked sample by sample.
#   Results: <out>/save-<synths>-<bar>/save_result.json (per call too), save.wav (what the firmware wrote to the codec).
#   The SD card is instant here (on the Deluge its waits yield to the task manager, which then runs the audio itself).
#   The XML written must be the same for two builds (its sha256 is printed). Needs the ELF's symbols: routine(),
#   Song::writeToFile(), the createXMLFile() clone without the XMLSerializer (smSerializer). About 2 minutes.
#   mastertune-v13 base (e13dce3d) against the save-speed branch: 3 synths at bar 2: 344 ms -> 22 ms, worst gap 80 -> 71;
#   5 synths at bar 2: 716 ms -> 66 ms, worst gap 128 (1 sample underrun) -> 97; at bar 2.5: 80 -> 10 ms and 180 -> 16 ms.
#
# MIDI=1 ./run.sh <tree | deluge.elf> [out dir]: instead of the three runs, the timing of the MIDI (and gate) output
#   against the audio (song_emu.py --midi-timing; MidiTiming in song_emu.py says what is modelled): the song with 3 synths
#   (MIDI_SYNTHS) and a MIDI track (make_sd.py --midi-track, a bass line in 16ths on channel 1; MIDI clock out is on by
#   default), the DMA in real time. The MIDI/gate output timer (MTU2 channel 2) and the DIN MIDI UART (its transmit DMA,
#   16-byte FIFO, 31250 baud) are modelled with their interrupts; for every timer run, the emulated time it fires
#   against the time the DMA reaches the audio sample at the window position the firmware scheduled it for
#   (timeWithinWindowAtWhichMIDIOrGateOccurs), both as scheduled (TGRA) and as run (the interrupt waits while
#   Song::renderAudio() masks interrupts, as on the Deluge), and for every MIDI clock byte its start on the wire against
#   its own tick's sample. Two cases: normal playback, 2 bars (MIDI_BARS) with routine() called as the task manager does
#   (every 16 samples, see play_realtime()), and saving (the song saved again and again for 1 bar, MIDI_SAVE_BARS),
#   when routine() renders 64 samples ahead (minNumSamplesToRender). Results: <out>/midi-play/ and <out>/midi-save/
#   midi_timing.json (per timer run too). About 3 minutes.
#   mastertune-v13 base (c6201e47) against the midi-timing branch (scheduleMidiGateOutISR()'s formula without the wrap),
#   timer runs as scheduled: normal playback 192, all within -1.2..+0.2 samples for both; while saving 97, 25 of them
#   (the MIDI clocks at a window position past what was due plus the DMA's movement) at -128 (2.9 ms early) -> all
#   within -1.4..+0.2. As run, both: up to 33 (normal) and 41 (saving) samples late while Song::renderAudio() has
#   interrupts masked (DISABLE_ALL_INTERRUPTS() around each output's rendering).
#
# Files: make_sd.py (the song and its samples, generated; its docstring describes the song), fat32.py (the card
# image), song_emu.py (the emulator harness; its docstring says what is real and what is modelled), blockcount.c
# (instruction counting per translated block).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -d "$1" ]; then
	FW=$(cd "$1" && pwd)
	ELF="$FW/build/Release/deluge.elf"
else
	ELF=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
	FW=$(cd "$(dirname "$ELF")/../.." && pwd)
fi
OUT=${2:-$(mktemp -d)}
BARS=${3:-4}
TOOLS="$FW/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-"
[ -f "$ELF" ] || { echo "no $ELF"; exit 2; }
mkdir -p "$OUT/device" "$OUT/digital"

UC=$(python3 -c 'import os, unicorn; print(os.path.dirname(unicorn.__file__))')
cc -O2 -shared -fPIC -I"$UC/include" "$HERE/blockcount.c" -o "$OUT/blockcount.so" -L"$UC/lib" -l:libunicorn.so.2 \
	-Wl,-rpath,"$UC/lib"

if [ -n "$MIDI" ]; then
	synths=${MIDI_SYNTHS:-3}
	for mode in play save; do
		dir="$OUT/midi-$mode"
		mkdir -p "$dir"
		python3 "$HERE/make_sd.py" "$dir/sd.img" --synths "$synths" --midi-track > /dev/null
		if [ $mode = play ]; then
			echo "== MIDI timing, normal playback: $synths synths and the MIDI track, ${MIDI_BARS:-2} bar(s) in real time"
			python3 "$HERE/song_emu.py" "$ELF" "$dir/sd.img" "$dir" --tools "$TOOLS" --build "$OUT" --warmup-bars 1 \
				--bars "${MIDI_BARS:-2}" --midi-timing --init-sounds --seed 1 $EMU_OPTS | sed -n '/^played/,$p'
		else
			echo "== MIDI timing while saving: $synths synths and the MIDI track, saved again and again for" \
				"${MIDI_SAVE_BARS:-1} bar(s)"
			python3 "$HERE/song_emu.py" "$ELF" "$dir/sd.img" "$dir" --tools "$TOOLS" --build "$OUT" --warmup-bars 1 \
				--bars "${MIDI_SAVE_BARS:-1}" --midi-timing --save-while-playing --init-sounds --seed 1 $EMU_OPTS \
				| sed -n '/^saved/,$p'
		fi
		rm -f "$dir/sd.img"
		echo
	done
	echo "results in $OUT"
	exit 0
fi

if [ -n "$SAVE" ]; then
	for synths in 3 5; do
		for bar in 1 1.5; do
			dir="$OUT/save-$synths-$bar"
			mkdir -p "$dir"
			echo "== saving while playing: $synths synths, the save starting after $bar bar(s)"
			python3 "$HERE/make_sd.py" "$dir/sd.img" --synths "$synths" > /dev/null
			python3 "$HERE/song_emu.py" "$ELF" "$dir/sd.img" "$dir" --tools "$TOOLS" --build "$OUT" --warmup-bars "$bar" \
				--save-while-playing --init-sounds --seed 1 $EMU_OPTS | sed -n '/^save while playing/,$p'
			rm -f "$dir/sd.img"
			echo
		done
	done
	echo "results in $OUT"
	exit 0
fi

run() { # out dir, song options, emulator options
	python3 "$HERE/make_sd.py" "$1/sd.img" $2 $SONG_OPTS > /dev/null
	python3 "$HERE/song_emu.py" "$ELF" "$1/sd.img" "$1" --tools "$TOOLS" --build "$OUT" --bars "$BARS" $3 $EMU_OPTS
	rm -f "$1/sd.img"
}

echo "== 1. demand: the song (reverb: Mutable, the default model), no culling for CPU load, cpuDireness 0"
run "$OUT" "--xml-out $OUT/song.xml" ""

echo
echo "== 2. device: the same, with culling and cpuDireness by the emulated duration, as on the Deluge"
run "$OUT/device" "" "--culling" | grep -Ev "^  \.\.\.|^boot|^song loaded"

echo
echo "== 3. the same song with the Digital reverb, no culling ($BARS bars, as 1.)"
run "$OUT/digital" "--reverb-model 2" "" | grep -E "^per 128|^full windows \(128\)"
python3 - "$OUT" <<'EOF'
import json, sys
m, d = (json.load(open(f"{sys.argv[1]}/{x}result.json")) for x in ("", "digital/"))
assert m["samples"] == d["samples"], "not the same bars"
rv = lambda r: r["areas"]["reverb"]["per_128"]
print(f"same {m['samples']} samples: total per 128 Mutable {m['instructions_per_128']:,.0f}, Digital "
      f"{d['instructions_per_128']:,.0f} ({d['instructions_per_128'] - m['instructions_per_128']:+,.0f}); reverb area "
      f"Mutable {rv(m):,.0f} ({rv(m) / 1161000 * 100:.1f}% CPU), Digital {rv(d):,.0f} ({rv(d) / 1161000 * 100:.1f}% CPU), "
      f"{rv(d) - rv(m):+,.0f}")
EOF
echo
echo "results in $OUT"
