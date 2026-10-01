#!/bin/sh
# The beta fixes' tests (beta-1.3/patches), each on the real firmware in the emulator. Each fails on the original
# beta 62a516c2 and passes with the patches:
#   save_4917_emu.py      0002: a saved song has no duplicate <audioClip> attributes, a named CV track its channel
#   songswap_e455_emu.py  0003: a song change while playing, the old song with an arrangement: no E455
#   kitrow_emu.py         0004: a synth into a kit row: the view not on the freed drum, LEARN + knob no E412
#   save_name_emu.py      0005: SAVE proposes no existing name and overwrites nothing
#   oom_robust_emu.py     0006: out of external RAM, a stale cluster, OLED handshake hiccups: no freeze
#   cluster_cache_emu.py  0009: a short sample held in RAM whole, a longer one's loop start held
#   abandon_load_emu.py   0010: a synth preset that fails to load is freed from its block start (no M000)
#   export_abort_hang_emu.py  0011: a track export / mixdown whose stem recording is aborted (RAM short) ends
#   export_abort_file_emu.py  0012: an aborted stem leaves no broken 5-second file on the card
#   (export_repeat_emu.py: the probe both use; 16+ exports in a row with song changes)
#   arranger_automation_emu.py  0013: CV, MIDI, KIT, SYNTH, SCALE in the arranger's automation view: no E369
#   recorder_threshold_emu.py   0014: RECORD AUDIO with threshold recording on: BACK ends the recorder
#   hmenu_off_buttons_emu.py    0015: SYNTH/KIT/MIDI/CV in a menu once horizontal menus are off: no crash
#   qwerty_cursor_emu.py        0016: a fast horizontal-encoder turn keeps a browser's text cursor in the text (S004)
#   savekitrow_emu.py     0101: audition pad + SAVE opens the kit-row save only for a kit's sound row
#   automation_type_emu.py 0102: a clip made CV or MIDI in song view opens its automation view without the synth's
#                         parameter (CV: no E411)
#   (0007, the transpose note during a song change, and 0008, a kit row saved without params: the fuzzer's
#   seeds 11 and 1011 in --mode deep, see beta-1.3/README.md)
#   sm01_replay_emu.py    0001: the 908-input run that froze with SM01 (about 35 minutes; LONG=1 to include it)
# Usage: ./run.sh <deluge.elf> <toolchain v22 prefix> <blockcount dir> [out dir]
# Exit status: the number of failed tests.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ELF=$1 TOOLS=$2 BUILD=$3 OUT=${4:-/tmp/beta-repro}
mkdir -p "$OUT"
cd /tmp || exit 1
tests="save_4917 songswap_e455 kitrow save_name oom_robust cluster_cache abandon_load export_abort_hang export_abort_file arranger_automation recorder_threshold hmenu_off_buttons qwerty_cursor savekitrow automation_type"
[ "${LONG:-0}" = 1 ] && tests="$tests sm01_replay"
fails=0
for t in $tests; do
  python3 "$HERE/${t}_emu.py" "$ELF" --tools "$TOOLS" --build "$BUILD" --out "$OUT/$t" > "$OUT/$t.log" 2>&1 \
    || fails=$((fails + 1))
  echo "== $t: $(tail -1 "$OUT/$t.log")"
done
exit $fails
