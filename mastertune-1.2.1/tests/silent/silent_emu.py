#!/usr/bin/env python3
"""Silent kits and audio tracks (mastertune-v17, GlobalEffectableForClip::renderOutput()'s early return): a song made
for it, on the real firmware in the emulator (tests/song's harness), for comparing two builds sample for sample.

Usage: silent_emu.py <deluge.elf> <out dir> [--tools PREFIX] [--build DIR] [--bars N]   (run.sh runs it for two ELFs)

A kit or audio track whose clip renders nothing, and whose effects have given out exact silence for 4096 samples and
hold nothing that could sound or move on, skips its effects (v12 on). mastertune-v17 checks that before setting up the
effects and a buffer. This song plays tracks, stops them with long tails, changes things while they're silent and
starts them again, so any difference between the check before and the check after shows in the output:
  KICKK  a kick on every beat, the sidechain's source (sideChainSend), all the time
  KDLY   a kit: a snare and a bell in bar 0 and again in bar 5 of its 8-bar clip; delay with feedback (18 repeats, a
         bar's tail, then given up), reverb send, sidechain ducking; its LPF opened fully, automated down from bar 1.5 to 3.5 while
         silent (the filter comes on: a mode change, which resets it), the bell row's pan automated (the rows'
         automation ticks while the kit is silent); the upper gold knob (volume) turned at bar 2.75 while silent
  KMOD   a kit with chorus, its depth 0 until the automation turns it up at bar 3 (mod FX on while silent), notes in
         bars 0 and 6 of its 7-bar clip
  KSTUT  a kit, notes in bar 0 of its 4-bar clip, stuttered from bar 0.6 to 1.3 (into its silence) and again from 3.2
         to 3.6 while silent (the stutter knob pressed and released, modKnobMode 6)
  LREV   an audio track (the 2-bar loop), reverb send, delay, sidechain ducking; stopped at bar 1.4 (at once, its
         tails ring on), its LPF knob turned down at bar 2.9 while silent (a mode change), started again at bar 4.5;
         stopped at bar 5.75, its LPF knob turned up (the filter off) at 7 and down again at 7.25 while silent (off and
         on again: reset with a fade, where going by the mode at the restart would find no change), started at 7.5
  LNEW   an audio track whose clip doesn't play at first, launched at bar 2.5 (instantly, a late start)
  DRN    a drone track: a 300 Hz drone row whose note starts at bar 4 and ends at bar 6 of its 8-bar clip, ducked
  KARP   the kit arp stepping across silence: RIM and HATC (0.1 s, cut at the note's end) held from bar 0 to 3 and 5 to 7
         of its 8-bar clip, played by the kit arp in quarters; between the steps the kit is silent long enough for
         the early return, and each step must bring it back
  KMIDI  a kit of a MIDI row (channel 2, note 60, its arp over 2 octaves in 16ths, held from bar 0.5 to 2.5) and a
         gate row (gate 1, its arp in 8ths, held from bar 1 to 3) of its 4-bar clip: their own arpeggiators
         (Kit::renderNonAudioArpPostOutput()) step while the kit, with no audio at all, takes the early return
Knob and stutter as the user does them in the song view, the clip's pad held (SessionView::padAction(),
modEncoderAction(), modEncoderButtonAction()); clips started and stopped at once with Shift + the status pad. Played
from the start for --bars (default 8) bars, one AudioEngine::routine() window at a time (song_emu.Player, no culling),
--init-sounds and seed 1 as tests/song/run.sh's bit-exact recipe. With a build that has the early return, it counts
its calls by track and half bar (each calls advanceWithNothingToRender() once): where they stop and start again shows
the events above reaching the tracks. The arpeggiators' notes are logged with the window they come in (the kit arp's
steps, Kit::kitArpNoteToRow(); the MIDI and gate rows', MIDIDrum/GateDrum::noteOnPostArp()).
Results: <out>/measured.wav (what the codec gets, as song_emu's), <out>/measured.npy (the same before its 16 bits:
the render buffer times the master volume, float64), <out>/result.json (instructions per window, the
early return's calls by track where the ELF has it, RMS per half bar, the arpeggiators' notes).
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SONG = os.path.join(HERE, "..", "song")
sys.path.insert(0, SONG)
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu  # noqa: E402
from make_sd import BAR as TBAR, STEP, attrs, knob, global_params_block, instrument_clip  # noqa: E402

SR = 44100
BAR = song_emu.BAR  # Samples: 2 s at 120 BPM


def automation(initial, nodes):
    """An automated param's value as the firmware writes it: the current value, then (value, position in ticks, with
    bit 31 set where the value glides from the node before)"""
    out = "0x" + initial[2:]
    for value, pos, interpolated in nodes:
        out += value[2:] + f"{pos | (1 << 31 if interpolated else 0):08X}"
    return out


def kit_part(name, rows, bars, notes_by_row, params, extra_attrs=None, row_params=None, arp=None, loop_mode=1):
    """A kit (make_sd.kit()'s rows and settings) with some of make_sd.KIT_ROWS, its clip of `bars` bars; a note is its
    position (a 16th long) or (position, length); arp: the clip's <arpeggiator> (the kit arp); loop_mode: the samples'
    (1 play once; 0 cut at the note's end, which the kit arp needs: it passes rows without note tails by)"""
    k = dict(presetName=name, presetFolder="KITS", defaultVelocity=64, isArmedForRecording=0, activeModFunction=0,
             colour=0, lpfMode="24dB", hpfMode="HPLadder", filterRoute="H2L", modFXType="none")
    k.update(extra_attrs or {})
    out = f"\t\t<kit{attrs(k, 3)}>\n"
    out += '\t\t\t<delay pingPong="1" analog="0" syncLevel="7" syncType="0" />\n'
    out += '\t\t\t<sidechain attack="327244" release="936" syncLevel="6" syncType="0" />\n'
    out += '\t\t\t<audioCompressor attack="83886080" release="83886080" thresh="0" ratio="1073741824" ' \
           'compHPF="0" compBlend="2147483647" />\n'
    out += "\t\t\t<soundSources>\n"
    clip_rows = []
    for index, row in enumerate(rows):
        _, _, velocity, transpose = next(r for r in make_sd.KIT_ROWS if r[0] == row)
        s = dict(name=row, polyphonic="auto", voicePriority=1, mode="subtractive", lpfMode="24dB", hpfMode="HPLadder",
                 filterRoute="H2L", modFXType="none", maxVoices=8)
        if row == "KICK":
            s["sideChainSend"] = 2147483647
        out += f"\t\t\t\t<sound{attrs(s, 5)}>\n"
        osc = dict(type="sample", transpose=transpose, cents=0, loopMode=loop_mode, reversed=0, timeStretchEnable=0,
                   timeStretchAmount=0, fileName=f"SAMPLES/{row}.WAV")
        out += f"\t\t\t\t\t<osc1{attrs(osc, 6)}>\n"
        out += f'\t\t\t\t\t\t<zone startSamplePos="0" endSamplePos="{LENGTHS[row]}" />\n'
        out += "\t\t\t\t\t</osc1>\n"
        out += '\t\t\t\t\t<osc2 type="sample" transpose="0" cents="0" loopMode="0" reversed="0" ' \
               'timeStretchEnable="0" timeStretchAmount="0" />\n'
        out += '\t\t\t\t\t<lfo1 type="triangle" syncLevel="0" syncType="0" />\n'
        out += '\t\t\t\t\t<lfo2 type="triangle" syncLevel="0" syncType="0" />\n'
        out += '\t\t\t\t\t<unison num="1" detune="8" spread="0" />\n'
        out += '\t\t\t\t\t<delay pingPong="1" analog="0" syncLevel="7" syncType="0" />\n'
        out += '\t\t\t\t\t<sidechain attack="327244" release="936" syncLevel="6" syncType="0" />\n'
        out += '\t\t\t\t\t<audioCompressor attack="83886080" release="83886080" thresh="0" ratio="1073741824" ' \
               'compHPF="0" compBlend="2147483647" />\n'
        out += "\t\t\t\t</sound>\n"
        p = dict(make_sd.SOUND_PARAMS)
        p.update(oscBVolume=knob(0), lpfFrequency=knob(50), lpfResonance=knob(0), volume=make_sd.VOLUME_DRUM,
                 reverbAmount=knob(5), compressorThreshold=knob(0))
        p.update((row_params or {}).get(row, {}))
        row_env1 = dict(attack=knob(0), decay=knob(25), sustain=knob(50), release=knob(0))
        block = make_sd.sound_params_block("soundParams", p, row_env1, make_sd.PAD_ENV2, [("velocity", "volume", 25)],
                                           5)
        clip_rows.append(("drumIndex", index, [(pos, STEP, velocity) if isinstance(pos, int) else (*pos, velocity)
                                               for pos in notes_by_row[row]], block))
    out += "\t\t\t</soundSources>\n"
    out += "\t\t\t<selectedDrumIndex>0</selectedDrumIndex>\n"
    out += "\t\t</kit>\n"
    kit_params = dict(make_sd.KIT_PARAMS)
    kit_params.update(params)
    block = global_params_block("kitParams", {k: v for k, v in kit_params.items() if not k.startswith("_")}, 3)
    for tag, value in kit_params.items():  # _delay, _lpf, _hpf: the child tags' attributes
        if tag.startswith("_"):
            block = replace_child(block, tag[1:], value)
    clip = instrument_clip(name, "KITS", bars * TBAR, clip_rows, params_block=block, arp=arp, kit=True)
    clip = clip.replace("<instrumentClip", '<instrumentClip\n\t\t\taffectEntire="1"', 1)
    return out, clip


def non_audio_kit_part(name, bars, rows):
    """A kit of MIDI and gate rows only, each with its own arpeggiator: rows are (the row's tag with its attributes,
    its <arpeggiator>'s attributes, notes as (position, length))"""
    out = f"\t\t<kit{attrs(dict(presetName=name, presetFolder='KITS', defaultVelocity=64, isArmedForRecording=0, activeModFunction=0, colour=0, lpfMode='24dB', hpfMode='HPLadder', filterRoute='H2L', modFXType='none'), 3)}>\n"
    out += '\t\t\t<delay pingPong="1" analog="0" syncLevel="7" syncType="0" />\n'
    out += '\t\t\t<sidechain attack="327244" release="936" syncLevel="6" syncType="0" />\n'
    out += '\t\t\t<audioCompressor attack="83886080" release="83886080" thresh="0" ratio="1073741824" ' \
           'compHPF="0" compBlend="2147483647" />\n'
    out += "\t\t\t<soundSources>\n"
    clip_rows = []
    for index, (tag, arp, notes) in enumerate(rows):
        element = tag.split()[0]
        out += f"\t\t\t\t<{tag}>\n\t\t\t\t\t<arpeggiator{attrs(arp, 6)} />\n\t\t\t\t</{element}>\n"
        clip_rows.append(("drumIndex", index, [(pos, length, 100) for pos, length in notes], None))
    out += "\t\t\t</soundSources>\n"
    out += "\t\t\t<selectedDrumIndex>0</selectedDrumIndex>\n"
    out += "\t\t</kit>\n"
    block = global_params_block("kitParams", make_sd.KIT_PARAMS, 3)
    clip = instrument_clip(name, "KITS", bars * TBAR, clip_rows, params_block=block, kit=True)
    clip = clip.replace("<instrumentClip", '<instrumentClip\n\t\t\taffectEntire="1"', 1)
    return out, clip


def replace_child(block, tag, attributes):
    """global_params_block()'s <delay>, <lpf> or <hpf> with these attributes"""
    lines = block.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith(f"<{tag} "):
            pad = line[:len(line) - len(line.lstrip())]
            lines[i] = pad + f"<{tag} " + " ".join(f'{k}="{v}"' for k, v in attributes.items()) + " />"
    return "\n".join(lines)


def audio_part(name, playing, params, mod_function=0):
    """An audio track playing make_sd's 2-bar loop (make_sd.audio_track()), its clip playing or not"""
    a = dict(name=name, inputChannel="none", activeModFunction=mod_function, lpfMode="24dB", hpfMode="HPLadder",
             filterRoute="H2L", modFXType="none")
    out = f"\t\t<audioTrack{attrs(a, 3)}>\n"
    out += '\t\t\t<delay pingPong="1" analog="0" syncLevel="7" syncType="0" />\n'
    out += '\t\t\t<sidechain attack="327244" release="936" syncLevel="6" syncType="0" />\n'
    out += '\t\t\t<audioCompressor attack="83886080" release="83886080" thresh="0" ratio="1073741824" ' \
           'compHPF="0" compBlend="2147483647" />\n'
    out += "\t\t</audioTrack>\n"
    c = dict(trackName=name, filePath="SAMPLES/LOOP.WAV", startSamplePos=0, endSamplePos=LENGTHS["LOOP"],
             pitchSpeedIndependent=1, attack=0, priority=1, overdubsShouldCloneAudioTrack=1, isPlaying=int(playing),
             isSoloing=0, isArmedForRecording=0, length=2 * TBAR, colourOffset=0, section=0)
    clip = f"\t\t<audioClip{attrs(c, 3)}>\n"
    p = dict(make_sd.KIT_PARAMS, volume=make_sd.VOLUME_LOOP)
    p.update(params)
    block = global_params_block("params", {k: v for k, v in p.items() if not k.startswith("_")}, 3)
    for tag, value in p.items():
        if tag.startswith("_"):
            block = replace_child(block, tag[1:], value)
    clip += block
    clip += "\t\t</audioClip>\n"
    return out, clip


def drone_part():
    """make_sd.drone_tracks()' kit with one 300 Hz row, its note from bar 4 to 6 of an 8-bar clip, ducked"""
    saved = make_sd.DRONE_TRACKS, make_sd.DRONE_KIT_PARAMS
    make_sd.DRONE_TRACKS = [("DRN", 8, [(30000, [(4 * TBAR, 2 * TBAR)], [])])]
    make_sd.DRONE_KIT_PARAMS = dict(saved[1], sidechainCompressorVolume=knob(30))
    try:
        (out, clip), = make_sd.drone_tracks()
    finally:
        make_sd.DRONE_TRACKS, make_sd.DRONE_KIT_PARAMS = saved
    return out, clip.replace('isArmedForRecording="1"', 'isArmedForRecording="0"')


def song():
    steps = lambda bar, s: [bar * TBAR + x * STEP for x in s]  # noqa: E731
    open_lpf = dict(frequency=knob(50), resonance=knob(10))
    parts = [
        kit_part("KICKK", ["KICK"], 1, {"KICK": steps(0, [0, 4, 8, 12])}, dict(sidechainCompressorVolume=knob(0))),
        kit_part("KDLY", ["SNARE", "BELL"], 8,
                 {"SNARE": steps(0, [0, 6]) + steps(5, [0, 4]), "BELL": steps(0, [3]) + steps(5, [10])},
                 dict(reverbAmount=knob(25), sidechainCompressorVolume=knob(35),
                      _delay=dict(rate=knob(25), feedback=knob(17)),
                      _lpf=dict(frequency=automation(knob(50), [(knob(50), 3 * TBAR // 2, False),
                                                                (knob(22), 7 * TBAR // 2, True)]),
                                resonance=knob(10))),
                 row_params={"BELL": dict(pan=automation(knob(25), [(knob(10), 0, False),
                                                                    (knob(40), 6 * TBAR, True)]))}),
        kit_part("KMOD", ["HATO", "CLAP"], 7, {"HATO": steps(0, [2, 6, 10, 14]) + steps(6, [2, 6]),
                                               "CLAP": steps(0, [12, 14]) + steps(6, [12])},
                 dict(reverbAmount=knob(15), modFXRate=knob(30),
                      modFXDepth=automation(knob(0), [(knob(0), 0, False), (knob(30), 3 * TBAR, False)]),
                      _lpf=open_lpf),
                 extra_attrs=dict(modFXType="chorus", modFXCurrentParam="depth")),
        kit_part("KSTUT", ["RIM", "TOM"], 4, {"RIM": steps(0, [0, 3, 8, 10]), "TOM": steps(0, [5, 13, 15])},
                 dict(reverbAmount=knob(0), stutterRate=knob(30), _lpf=open_lpf),
                 extra_attrs=dict(activeModFunction=6)),
        audio_part("LREV", True, dict(reverbAmount=knob(30), sidechainCompressorVolume=knob(40),
                                      _delay=dict(rate=knob(25), feedback=knob(8)), _lpf=open_lpf), mod_function=1),
        audio_part("LNEW", False, dict(reverbAmount=knob(10), _lpf=open_lpf)),
        drone_part(),
        # The kit arp stepping across silence: RIM and HATC (0.1 s each) held for 3 bars from bar 0 and for 2 from
        # bar 5 go through the kit arp in quarters; between its steps the kit is silent for 0.4 s, so it takes the
        # early return and must come back for the next step
        kit_part("KARP", ["RIM", "HATC"], 8, {"RIM": [(0, 3 * TBAR), (5 * TBAR, 2 * TBAR)],
                                              "HATC": [(0, 3 * TBAR), (5 * TBAR, 2 * TBAR)]},
                 dict(reverbAmount=knob(10), sidechainCompressorVolume=knob(0), arpeggiatorGate=knob(25),
                      arpeggiatorRate=knob(25), _lpf=open_lpf),
                 arp=dict(arpMode="arp", syncLevel=4, numOctaves=1), loop_mode=0),
        # MIDI and gate rows' own arpeggiators (Kit::renderNonAudioArpPostOutput()), in a kit that renders no audio at
        # all and takes the early return all the time: a MIDI row (channel 2, note 60) arpeggiated over 2 octaves in
        # 16ths, held from bar 0.5 to 2.5, and a gate row (gate 1) in 8ths, held from bar 1 to 3
        non_audio_kit_part("KMIDI", 4, [
            ('midiOutput channel="1" note="60"', dict(arpMode="arp", noteMode="up", octaveMode="up", numOctaves=2,
                                                      syncLevel=6, syncType=0, rate=0, gate=0),
             [(TBAR // 2, 2 * TBAR)]),
            ('gateOutput channel="0"', dict(arpMode="arp", noteMode="up", octaveMode="up", numOctaves=1, syncLevel=5,
                                            syncType=0, rate=0, gate=0), [(TBAR, 2 * TBAR)])]),
    ]
    xml = make_sd.song_xml(LENGTHS, 1, num_synths=0).replace('yScrollSongView="-7"', 'yScrollSongView="0"', 1)
    # The parts above instead of make_sd's, and no song drone
    start = xml.index("\t<instruments>")
    end = xml.index("</song>")
    body = "\t<instruments>\n" + "".join(p[0] for p in parts) + "\t</instruments>\n"
    body += "\t<sessionClips>\n" + "".join(p[1] for p in parts) + "\t</sessionClips>\n"
    return xml[:start] + body + xml[end:]


def gdb_values(emu, context, expressions):
    """Integers the toolchain's gdb prints for these expressions (offsets, sizes), in the scope of a function where
    the types are known (song_emu.gdb_values() without it doesn't find Song)"""
    args = ["-ex", f"list {context}"]
    for expression in expressions:
        args += ["-ex", f"print {expression}"]
    out = subprocess.run([emu.tool_prefix + "gdb", "-batch", *args, emu.elf], capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (-?\d+)$", out, re.M)]
    if len(values) != len(expressions):
        raise SystemExit(f"gdb: {expressions}:\n{out[-500:]}")
    return values


def clips_of_song(emu):
    """The song's session clips in their order (ClipArray, a ResizeableArray of Clip*), and Clip::output"""
    sessions, memory, num, size, start, element, output, loop_length = gdb_values(emu, "Song::Song", [
        "(int)&((Song*)0)->sessionClips", "(int)&((ResizeableArray*)0)->memory",
        "(int)&((ResizeableArray*)0)->numElements", "(int)&((ResizeableArray*)0)->memorySize",
        "(int)&((ResizeableArray*)0)->memoryStart", "(int)&((ResizeableArray*)0)->elementSize",
        "(int)&((Clip*)0)->output", "(int)&((Clip*)0)->loopLength"])
    array = emu.u32(emu.sym["currentSong"]) + sessions
    n, m, s = emu.u32(array + num), emu.u32(array + memory), emu.u32(array + start)
    size_, es = emu.u32(array + size), emu.u32(array + element)
    clips = [emu.u32(m + ((s + i) % size_) * es) for i in range(n)]
    return [(c, emu.u32(c + output), emu.u32(c + loop_length)) for c in clips]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("out")
    ap.add_argument("--tools")
    ap.add_argument("--build", default=SONG)
    ap.add_argument("--bars", type=float, default=8)
    args = ap.parse_args()
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(args.out, exist_ok=True)
    xml = song()
    open(os.path.join(args.out, "song.xml"), "w").write(xml)
    sd = os.path.join(args.out, "sd.img")
    files = {k: v for k, v in FILES.items() if k.startswith("SAMPLES/") and not k.startswith("SAMPLES/WT")}
    files["SONGS/DEFAULT.XML"] = xml.encode()
    fat32.build(sd, files)

    emu = song_emu.Emulator(args.elf, sd, tools, args.build,
                            lambda s: print(s, flush=True) if os.environ.get("EMU_DEBUG") else None)
    sym = emu.sym
    song_emu.setup_sd(emu)
    song_emu.init_sounds(emu)
    song_emu.boot(emu)
    emu.w32(sym["jcong"], 1)
    song_emu.load_startup_song(emu)
    print(f"--init-sounds: {emu.sounds_initialised} Sounds constructed; seed 1", flush=True)

    names = ["KICKK", "KDLY", "KMOD", "KSTUT", "LREV", "LNEW", "DRN", "KARP", "KMIDI"]
    lengths = [1, 8, 7, 4, 2, 2, 8, 8, 4]
    clips = clips_of_song(emu)
    got = [c[2] // TBAR for c in clips]
    if got != lengths:
        raise SystemExit(f"the session clips aren't in the song's order: lengths {got} bars, wanted {lengths}")
    clip = dict(zip(names, (c[0] for c in clips)))

    # The early return's calls by track: each calls advanceWithNothingToRender() once, through the vtable of the
    # GlobalEffectableForClip inside the Kit (a thunk to Kit's) or the AudioOutput (the base's), with that as this
    early = sorted(address for name, (address, _) in sym.by_name.items() if "advanceWithNothingToRender" in name
                   and (name.startswith("_ZN23GlobalEffectableForClip") or name.startswith("_ZThn")))
    calls = {}  # this -> calls per half bar
    window_calls = []
    position = [0]  # Samples played before this window
    half_bars = int(2 * args.bars) + 1

    def count(e):
        this = e.uc.reg_read(song_emu.UC_ARM_REG_R0)
        calls.setdefault(this, [0] * half_bars)[min(half_bars - 1, position[0] * 2 // BAR)] += 1
        window_calls[-1] += 1
    for address in early:
        emu.intercept(address, count)

    # The arpeggiators' notes: the kit arp's steps (Kit::kitArpNoteToRow(), on: r3; its row: r2; the first note of a
    # chord comes from Kit::noteOnPreKitArp() instead) and the MIDI and gate rows' (noteOnPostArp(): the note in r1),
    # with the window's first sample: the same in both builds, and several, so the arps really step
    arp_notes = []

    def arp_note(kind):
        def on(e):
            r1, r2, r3 = (e.uc.reg_read(r) for r in (song_emu.UC_ARM_REG_R1, song_emu.UC_ARM_REG_R2,
                                                      song_emu.UC_ARM_REG_R3))
            if kind != "kit arp":
                arp_notes.append([position[0], kind, r1])
            elif r3 & 0xFF:
                arp_notes.append([position[0], kind, r2])
        return on
    for prefix, kind in (("_ZN3Kit15kitArpNoteToRow", "kit arp"), ("_ZN8MIDIDrum13noteOnPostArp", "MIDI row"),
                         ("_ZN8GateDrum13noteOnPostArp", "gate row")):
        emu.intercept(sym.find(prefix), arp_note(kind))

    # As the user does it in the song view (rows layout, clip i on row i): a clip's pad held (SessionView::padAction(),
    # x 0), the gold knob turned or the stutter knob pressed and released, the pad let go; a clip's status pad (x 16)
    # with Shift held starts or stops it at once (View::clipStatusPadAction(): Session::toggleClipStatus(), instant)
    if emu.call(sym["_Z9getRootUIv"]) != sym["sessionView"]:
        raise SystemExit("the song doesn't open in the song view")
    sv = sym["sessionView"]
    pad, knob_turn = sym["_ZN11SessionView9padActionElll"], sym["_ZN11SessionView16modEncoderActionEll"]
    button = sym["_ZN11SessionView22modEncoderButtonActionEhb"]
    shift = sym["_ZN7Buttons21shiftCurrentlyPressedE"]
    y = {name: i for i, name in enumerate(names)}

    def hold(name, on):
        return [(pad, (sv, 0, y[name], 127 if on else 0))]

    def status_with_shift(name):
        return [(shift, b"\x01"), (pad, (sv, 16, y[name], 127)), (pad, (sv, 16, y[name], 0)), (shift, b"\x00")]

    actions = [  # (bar, what, [(function, arguments) or (address, bytes to write)])
        (0.6, "KSTUT stutter on (its pad held, the stutter knob pressed)", hold("KSTUT", 1) + [(button, (sv, 1, 1))]),
        (1.3, "KSTUT stutter off (silent by now)", [(button, (sv, 1, 0))] + hold("KSTUT", 0)),
        (1.4, "LREV stopped (Shift + status pad)", status_with_shift("LREV")),
        (2.5, "LNEW launched (Shift + status pad: at once, a late start)", status_with_shift("LNEW")),
        (2.75, "KDLY volume knob +6 (silent)", hold("KDLY", 1) + [(knob_turn, (sv, 1, 6))] + hold("KDLY", 0)),
        (2.9, "LREV LPF knob -20 (silent: its filter comes on)",
         hold("LREV", 1) + [(knob_turn, (sv, 1, -20))] + hold("LREV", 0)),
        (3.2, "KSTUT stutter on (silent)", hold("KSTUT", 1) + [(button, (sv, 1, 1))]),
        (3.6, "KSTUT stutter off", [(button, (sv, 1, 0))] + hold("KSTUT", 0)),
        (4.5, "LREV started again (Shift + status pad)", status_with_shift("LREV")),
        (5.75, "LREV stopped again", status_with_shift("LREV")),
        (7.0, "LREV LPF knob +20 (silent: its filter goes off)",
         hold("LREV", 1) + [(knob_turn, (sv, 1, 20))] + hold("LREV", 0)),
        (7.25, "LREV LPF knob -20 (silent: on again, which resets it with a fade)",
         hold("LREV", 1) + [(knob_turn, (sv, 1, -20))] + hold("LREV", 0)),
        (7.5, "LREV started again", status_with_shift("LREV")),
    ]
    player = song_emu.Player(emu)
    player.start()
    out, log = [], []
    done = total = 0
    t = time.time()
    while total < int(args.bars * BAR):
        while done < len(actions) and total >= actions[done][0] * BAR:
            song_emu.drain_uarts(emu)
            for function, call_args in actions[done][2]:
                if isinstance(call_args, bytes):
                    emu.uc.mem_write(function, call_args)
                else:
                    emu.call(function, *call_args)
            song_emu.drain_uarts(emu)
            done += 1
        window_calls.append(0)
        position[0] = total
        w = player.window()
        total += w[1]
        out.append(w[4])
        log.append([w[0], w[1], w[2], window_calls[-1]])
    print(f"played {args.bars:g} bars: {len(log)} windows ({time.time() - t:.1f} s), actions: "
          + ", ".join(f"{a[0]:g} {a[1]}" for a in actions), flush=True)
    x = np.concatenate(out)
    np.save(os.path.join(args.out, "measured.npy"), x)  # Before the 16 bits and their clipping: the exact output
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with open(os.path.join(args.out, "measured.wav"), "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, 1, 2, SR, SR * 4, 4, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)
    half = BAR // 2
    rms = [float(20 * np.log10(np.sqrt(np.mean(x[i:i + half] ** 2)) + 1e-12)) for i in range(0, len(x), half)]
    instructions = sum(w[0] for w in log)
    samples = sum(w[1] for w in log)
    # Which track each counted `this` is: the output pointer plus the GlobalEffectableForClip base's offset
    by_track = {}
    if early:
        # The base's offset in Kit and AudioOutput, from the thunks of their renderGlobalEffectableForClip()
        offsets = [int(m.group(1)) for name in sym.by_name
                   for m in [re.match(r"_ZThn(\d+)_N(3Kit|11AudioOutput)29renderGlobalEffectableForClip", name)] if m]
        for name, (_, output, _) in zip(names, clips):
            for off in offsets:
                if output + off in calls:
                    by_track[name] = calls[output + off]
    result = dict(elf=os.path.abspath(args.elf), bars=args.bars, windows=len(log), samples=samples,
                  instructions_per_128=instructions * 128 / samples, early_return_symbol=bool(early),
                  early_returns=sum(map(sum, calls.values())),
                  early_returns_by_track={k: sum(v) for k, v in by_track.items()},
                  early_returns_by_track_per_half_bar=by_track, rms_db_per_half_bar=rms,
                  arp_notes_fields=["first sample of the window", "arp", "row (kit arp) or note"], arp_notes=arp_notes,
                  window_log_fields=["instructions", "samples", "voices", "early returns"], window_log=log)
    json.dump(result, open(os.path.join(args.out, "result.json"), "w"), indent=1)
    print(f"per 128 samples: {result['instructions_per_128']:,.0f} instructions; output RMS per half bar (dB): "
          + " ".join(f"{r:.0f}" for r in rms), flush=True)
    kinds = {}
    for pos, kind, what in arp_notes:
        kinds.setdefault(kind, []).append(f"{pos / BAR:.2f}:{what}")
    for kind, notes in kinds.items():
        print(f"{kind}: {len(notes)} notes (bar:row or note) " + " ".join(notes[:6]) + (" ..." if len(notes) > 6 else ""),
              flush=True)
    if early:
        print(f"early returns: {result['early_returns']:,}; by track, per half bar (of {BAR // 256} windows of 128):",
              flush=True)
        for name, per in by_track.items():
            print(f"  {name:6} {sum(per):6,}  " + " ".join(f"{c:3}" for c in per), flush=True)
    os.remove(sd)


FILES, LENGTHS = make_sd.samples()

if __name__ == "__main__":
    main()
