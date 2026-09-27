#!/usr/bin/env python3
"""Emulator test of the converted library: the real deluge.elf plays the same song from the original card and from
the card tools/retune_library.py made of it for 432 Hz, and this compares what the firmware does.

Five runs (each its own process), 4 bars after a 1-bar warm-up: the original card at master tune 440 Hz (the
reference: native playback), the original at 432 Hz, the converted card at 432 Hz, and both at 432 Hz without the
sample cache (VoiceSample::possiblySetUpCache() sets none up, as when the RAM is short or the cache was stolen: then
every resampled voice runs the sinc interpolation, as measured on the Deluge). The master tune comes from
CommunityFeatures.XML on the card (checked after boot).

The song (120 BPM exactly: 229.6875 samples per tick, so the 2-bar audio clip fits without drift), each part a tone of
its own frequency (at least 230 Hz apart) so that it can be measured in the mix:
- kit KIT, ONCE unless said: KICK (48 kHz stereo 24-bit, 800 Hz after 0.1 s of silence, zone start 50 ms, end
  800 ms, beat 1), SNARE (1100 Hz, beat 3), LOOPROW (3400 Hz with a sawtooth level of its loop's period, LOOP repeat
  mode, loop 0.5 to 0.75 s = 850 cycles, the whole bar), HAT (an AIFF, 2700 Hz, beats 2 and 4), TOM (400 Hz,
  transposed +5: never native), STRETCH (a 1700 Hz note, time stretch on: its length is kept, the 7th 16th);
- synth SMP: a sample (1400 Hz) played at 60 (native) for half a bar and at 67 (never native) for the other half;
- synth MULTI: a multisample, range A (300 Hz, transpose +12) played at 48, range B (3000 Hz) at 60: both native;
- audio clip LOOP: 2 bars (176400 samples: native at 440 Hz), 2400 Hz notes on every beat.

Measured:
- VoiceSample::render() intercepted: per file its calls, the phaseIncrement (stack+12) and timeStretchRatio (stack+16),
  native (both 1 << 24), and the instructions per call (to its return: one voice, one window of up to 128 samples);
  TimeStretcher::init() and hopEnd() calls; writes to address 0 (see run()).
- instructions per 128 samples (AudioEngine::routine()), and the song test's area "sample reading / interpolation /
  time-stretch".
- the output: each part's pitch (phase slope of its demodulated band) against the exact target; its markers (onsets
  and ends: the KICK's zone start and end, the one-shots' file ends, the LOOPROW's loop wraps and period, the audio
  clip's notes) of the converted card against the original at 432 Hz (at 440 Hz for the length-kept parts).

Usage: retune_emu.py <deluge.elf> <work dir> [--tools PREFIX] [--build DIR with blockcount.so] [--bars N] [--jobs N]
       [--reuse]
Needs: python3 with unicorn 2, numpy, scipy, soxr, soundfile, pylibrb (for the audio clip and the STRETCH row), a C
compiler (blockcount.c). About 1 minute.
"""
import argparse
import collections
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SONG = os.path.join(HERE, "..", "song")
sys.path.insert(0, HERE)
sys.path.insert(0, SONG)
import fat32  # noqa: E402
import make_sd as M  # noqa: E402
import song_emu as E  # noqa: E402
from pc_test import aiff, notes, sine, wav  # noqa: E402
from tone import cents, demodulate, tone_frequency  # noqa: E402

TOOL = os.path.join(HERE, "..", "..", "tools", "retune_library.py")
SR = 44100
BAR = E.BAR  # Samples
STEP = M.STEP  # Ticks per 16th
TUNE = 432 / 440
UNITY = 1 << 24
D = "SAMPLES/RT/"

# name, frequency of the content (at 440 Hz), transpose (semitones) of how it's played, whether its length is kept
PARTS = [
    ("KICK", 800, 0), ("SNARE", 1100, 0), ("LOOPROW", 3400, 0), ("HAT", 2700, 0), ("TOM", 400, 5),
    ("STRETCH", 1700, 0), ("SMP C3", 1400, 0), ("SMP G3", 1400, 7), ("MULTI A", 300, 0), ("MULTI B", 3000, 0),
    ("CLIP", 2400, 0),
]


# --- the card

def samples():
    f = {}
    k = np.concatenate([np.zeros(4800), sine(800, 48000, 0.9, 0.3, fade=0.002)])
    f[D + "KICK.WAV"] = wav(np.stack([k, 0.8 * k], axis=1), 48000, 24)
    f[D + "SNARE.WAV"] = wav(sine(1100, SR, 0.4, 0.3, fade=0.002), SR, 16)
    n = np.arange(SR)
    level = 0.05 + 0.3 * (((n - 22050) % 11025) / 11025)  # The loop's sawtooth, 850 cycles of 3400 Hz per loop
    f[D + "LOOP.WAV"] = wav(level * np.sin(2 * np.pi * 3400 * n / SR), SR, 16)
    hat_inst = struct.pack(">BbBBBBh", 60, 0, 0, 127, 1, 127, 0) + bytes(12)
    f[D + "HAT.AIF"] = aiff(sine(2700, SR, 0.3, 0.3, fade=0.002), SR, 16, [], hat_inst)
    f[D + "TOM.WAV"] = wav(sine(400, SR, 0.5, 0.3, fade=0.002), SR, 16)
    f[D + "STRETCH.WAV"] = wav(notes(1700, SR, 0.5, [0.01], amp=0.3, decay=0.25), SR, 16)
    f[D + "SMP.WAV"] = wav(sine(1400, SR, 2.0, 0.3), SR, 16)
    f[D + "MULTIA.WAV"] = wav(sine(300, SR, 2.0, 0.3), SR, 16)
    f[D + "MULTIB.WAV"] = wav(sine(3000, SR, 2.0, 0.3), SR, 16)
    clip = notes(2400, SR, 4.0, [0.5 * i + 0.01 for i in range(8)], amp=0.3, decay=0.2)
    f[D + "CLIP.WAV"] = wav(np.stack([clip, clip], axis=1), SR, 16)
    return f


QUIET = dict(reverbAmount=M.knob(0), lpfFrequency=M.knob(50), lpfResonance=M.knob(0), compressorThreshold=M.knob(0),
             hpfFrequency=M.knob(0), oscBVolume=M.knob(0), delayFeedback=M.knob(0), modFXDepth=M.knob(0))
ENV = dict(attack=M.knob(0), decay=M.knob(25), sustain=M.knob(50), release=M.knob(0))
KIT_ROWS = [  # name, file, osc attributes, zone, notes (tick, length)
    ("KICK", "KICK.WAV", {}, dict(startSamplePos=2400, endSamplePos=38400), [(0, STEP)]),
    ("SNARE", "SNARE.WAV", {}, dict(startSamplePos=0, endSamplePos=17640), [(8 * STEP, STEP)]),
    ("LOOPROW", "LOOP.WAV", dict(loopMode=2), dict(startSamplePos=0, endSamplePos=44100, startLoopPos=22050,
                                                    endLoopPos=33075), [(0, 16 * STEP)]),
    ("HAT", "HAT.AIF", {}, dict(startSamplePos=0, endSamplePos=13230), [(4 * STEP, STEP), (12 * STEP, STEP)]),
    ("TOM", "TOM.WAV", dict(transpose=5), dict(startSamplePos=0, endSamplePos=22050), [(10 * STEP, STEP)]),
    ("STRETCH", "STRETCH.WAV", dict(timeStretchEnable=1), dict(startSamplePos=0, endSamplePos=22050),
     [(6 * STEP, STEP)]),
]


def kit():
    k = dict(presetName="KIT", presetFolder="KITS", defaultVelocity=64, isArmedForRecording=0, activeModFunction=0,
             colour=0, lpfMode="24dB", hpfMode="HPLadder", filterRoute="H2L", modFXType="none")
    out = f"\t\t<kit{M.attrs(k, 3)}>\n\t\t\t<soundSources>\n"
    rows = []
    for index, (name, path, osc_extra, zone, row_notes) in enumerate(KIT_ROWS):
        s = dict(name=name, polyphonic="auto", voicePriority=1, mode="subtractive", lpfMode="24dB",
                 hpfMode="HPLadder", filterRoute="H2L", modFXType="none", maxVoices=8)
        out += f"\t\t\t\t<sound{M.attrs(s, 5)}>\n"
        osc = dict(type="sample", transpose=0, cents=0, loopMode=1, reversed=0, timeStretchEnable=0,
                   timeStretchAmount=0, fileName=D + path)
        osc.update(osc_extra)
        out += f"\t\t\t\t\t<osc1{M.attrs(osc, 6)}>\n"
        out += "\t\t\t\t\t\t<zone" + "".join(f' {a}="{v}"' for a, v in zone.items()) + " />\n"
        out += "\t\t\t\t\t</osc1>\n"
        out += '\t\t\t\t\t<osc2 type="sample" transpose="0" cents="0" loopMode="0" reversed="0" ' \
               'timeStretchEnable="0" timeStretchAmount="0" />\n'
        out += '\t\t\t\t\t<unison num="1" detune="8" spread="0" />\n'
        out += "\t\t\t\t</sound>\n"
        p = dict(M.SOUND_PARAMS, volume=M.VOLUME_DRUM, **QUIET)
        row_params = M.sound_params_block("soundParams", p, ENV, M.PAD_ENV2, [], 5)
        rows.append(("drumIndex", index, [(t, n, 100) for t, n in row_notes], row_params))
    out += "\t\t\t</soundSources>\n\t\t\t<selectedDrumIndex>0</selectedDrumIndex>\n\t\t</kit>\n"
    block = M.global_params_block("kitParams", dict(M.KIT_PARAMS, reverbAmount=M.knob(0)), 3)
    return out, M.instrument_clip("KIT", "KITS", M.BAR, rows, params_block=block, kit=True)


def synth(name, osc1_body, note_rows):
    sound = dict(presetName=name, presetFolder="SYNTHS", defaultVelocity=64, isArmedForRecording=0,
                 activeModFunction=1, colour=0, polyphonic="poly", voicePriority=1, mode="subtractive",
                 modFXType="none", lpfMode="24dB", hpfMode="HPLadder", filterRoute="H2L", maxVoices=8)
    out = f"\t\t<sound{M.attrs(sound, 3)}>\n" + osc1_body
    out += '\t\t\t<osc2 type="sample" transpose="0" cents="0" loopMode="0" />\n'
    out += '\t\t\t<lfo1 type="triangle" syncLevel="0" syncType="0" />\n'
    out += '\t\t\t<lfo2 type="sine" syncLevel="0" syncType="0" />\n'
    out += '\t\t\t<unison num="1" detune="0" spread="0" />\n'
    out += "\t\t</sound>\n"
    p = dict(M.SOUND_PARAMS, oscAVolume=M.knob(50), volume=M.knob(30), **QUIET)
    block = M.sound_params_block("soundParams", p, ENV, M.PAD_ENV2, [], 3)
    return out, M.instrument_clip(name, "SYNTHS", M.BAR, note_rows, params_block=block)


def song_xml():
    head = dict(firmwareVersion="c1.2.1", earliestCompatibleFirmware="4.1.0-alpha", arrangementAutoScrollOn=0,
                xScroll=0, xZoom=24, yScrollSongView=-7, yScrollArrangementView=-7, xScrollArrangementView=0,
                xZoomArrangementView=192, timePerTimerTick=229, timerTickFraction=-1342177280, rootNote=0,
                inputTickMagnitude=2, swingAmount=0, swingInterval=6, affectEntire=0, activeModFunction=1,
                modFXType="none", lpfMode="24dB", hpfMode="HPLadder", filterRoute="H2L", sessionLayout=0)
    out = '<?xml version="1.0" encoding="UTF-8"?>\n<song' + M.attrs(head, 1) + ">\n"
    out += '\t<reverb roomSize="1288490112" dampening="1546188288" width="2147483647" pan="0" model="1">\n'
    out += '\t\t<compressor attack="0" release="0" volume="1073741824" shape="-601295438" syncLevel="7" />\n'
    out += "\t</reverb>\n"
    song_params = dict(reverbAmount="0x80000000", volume=M.knob(22), pan="0x00000000",
                       sidechainCompressorShape="0xDC28F5B2", modFXDepth="0x00000000", modFXRate="0xE0000000",
                       stutterRate="0x00000000", sampleRateReduction="0x80000000", bitCrush="0x80000000",
                       modFXOffset="0x00000000", modFXFeedback="0x80000000", compressorThreshold="0x00000000",
                       tempo="0x00002EE0")
    out += M.global_params_block("songParams", song_params, 1)
    half = 8 * STEP
    smp = synth("SMP", f'\t\t\t<osc1 type="sample" transpose="0" cents="0" loopMode="0" timeStretchEnable="0" '
                       f'fileName="{D}SMP.WAV">\n\t\t\t\t<zone startSamplePos="0" endSamplePos="88200" />\n'
                       f'\t\t\t</osc1>\n',
                [("y", 60, [(0, half, 100)], None), ("y", 67, [(half, half, 100)], None)])
    multi = synth("MULTI", '\t\t\t<osc1 type="sample" loopMode="0" timeStretchEnable="0">\n\t\t\t\t<sampleRanges>\n'
                           f'\t\t\t\t\t<sampleRange rangeTopNote="54" fileName="{D}MULTIA.WAV" transpose="12" '
                           'cents="0">\n\t\t\t\t\t\t<zone startSamplePos="0" endSamplePos="88200" />\n'
                           '\t\t\t\t\t</sampleRange>\n'
                           f'\t\t\t\t\t<sampleRange fileName="{D}MULTIB.WAV" transpose="0" cents="0">\n'
                           '\t\t\t\t\t\t<zone startSamplePos="0" endSamplePos="88200" />\n'
                           '\t\t\t\t\t</sampleRange>\n\t\t\t\t</sampleRanges>\n\t\t\t</osc1>\n',
                  [("y", 48, [(0, half, 100)], None), ("y", 60, [(half, half, 100)], None)])
    track = '\t\t<audioTrack name="LOOP" inputChannel="none" activeModFunction="0" lpfMode="24dB" ' \
            'hpfMode="HPLadder" filterRoute="H2L" modFXType="none" />\n'
    clip = f'\t\t<audioClip{M.attrs(dict(trackName="LOOP", filePath=D + "CLIP.WAV", startSamplePos=0, endSamplePos=176400, pitchSpeedIndependent=1, attack=0, priority=1, overdubsShouldCloneAudioTrack=1, isPlaying=1, isSoloing=0, isArmedForRecording=0, length=2 * M.BAR, colourOffset=0, section=0), 3)}>\n'
    clip += M.global_params_block("params", dict(M.KIT_PARAMS, volume=M.knob(30), reverbAmount=M.knob(0)), 3)
    clip += "\t\t</audioClip>\n"
    parts = [kit(), smp, multi, (track, clip)]
    out += "\t<instruments>\n" + "".join(p[0] for p in parts) + "\t</instruments>\n"
    out += "\t<sessionClips>\n" + "".join(p[1] for p in parts) + "\t</sessionClips>\n"
    return out + "</song>\n"


def community_features(tenths):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<runtimeFeatureSettings>\n'
            f'\t<setting name="masterTune" value="{tenths}" />\n</runtimeFeatureSettings>\n').encode()


def write_card(folder):
    files = samples()
    files["SONGS/DEFAULT.XML"] = song_xml().encode()
    files["CommunityFeatures.XML"] = community_features(4320)
    for path, data in files.items():
        full = os.path.join(folder, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "wb").write(data)


def read_card(folder):
    out = {}
    for dirpath, _, filenames in os.walk(folder):
        for name in filenames:
            if name.startswith("RETUNE_REPORT"):
                continue
            full = os.path.join(dirpath, name)
            out[os.path.relpath(full, folder).replace(os.sep, "/")] = open(full, "rb").read()
    return out


# --- the run

def gdb_offsets(emu, expressions):
    out = subprocess.run([emu.tool_prefix + "gdb", "-batch"] + [x for e in expressions for x in ("-ex", f"print {e}")]
                         + [emu.elf], capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (\d+)$", out, re.M)]
    if len(values) != len(expressions):
        raise SystemExit(f"gdb: {expressions}: {out[-400:]}")
    return values


def run(args, log):
    """One run (its own process: unicorn and blockcount.so keep state): the card at a master tune."""
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    image = os.path.join(out_dir, "sd.img")
    files = read_card(args.card)
    files["CommunityFeatures.XML"] = community_features(args.tenths)
    fat32.build(image, files)
    emu = E.Emulator(args.elf, image, args.tools, args.build, log)
    # A firmware bug, not the test's: readSamplesResampled() reads *cacheWritePos at its start and writes it back at
    # its end; the time stretcher's reader (readSamplesForTimeStretching()) passes NULL, and as the pointer was read
    # the compiler dropped the "if (cacheWritePos)" before the write: a time-stretched voice that is also resampled
    # (sinc) writes a pointer to address 0. The emulator's page 0 is read-only (null pointers read 0 there); here it
    # takes the write, which is counted and undone after each voice's render.
    emu.uc.mem_protect(0, 0x1000, E.UC_PROT_ALL)
    null_writes = [0]
    E.setup_sd(emu)
    E.init_sounds(emu)
    E.boot(emu)
    emu.w32(emu.sym["jcong"], 1)
    tuned = emu.u32(emu.sym.find("_ZN10MasterTune12_GLOBAL__N_18tenthsHzE"))
    log(f"master tune after boot (CommunityFeatures.XML): {tuned / 10:g} Hz")
    if tuned != args.tenths:
        raise SystemExit("the master tune from CommunityFeatures.XML didn't arrive")
    E.load_startup_song(emu)
    if args.no_cache:  # As when the RAM is short or the cache was stolen: possiblySetUpCache() sets none up
        emu.intercept(emu.sym.find("_ZN11VoiceSample18possiblySetUpCacheE"), lambda e: 1)

    file_path_offset, string_memory_offset = gdb_offsets(
        emu, ["(int)&((AudioFile*)0)->filePath", "(int)&((String*)0)->stringMemory"])
    names = {}

    def sample_name(sample):
        if sample not in names:
            p = emu.u32(sample + file_path_offset + string_memory_offset)
            raw = bytes(emu.uc.mem_read(p, 128)) if p else b"?"
            names[sample] = raw.split(b"\0")[0].decode(errors="replace").split("/")[-1]
        return names[sample]

    stats = collections.defaultdict(lambda: dict(calls=0, native=0, interpolated=0, stretched=0, instructions=0,
                                                 increments=collections.Counter()))
    counting = [False]
    current = []
    returns = set()

    def at_render(e):
        lr = e.uc.reg_read(E.UC_ARM_REG_LR) & ~1
        if lr not in returns:
            returns.add(lr)
            e.intercept(lr, at_return)
        if counting[0]:
            sp = e.uc.reg_read(E.UC_ARM_REG_SP)
            sample, _, _, phase_increment, stretch = struct.unpack("<5I", e.uc.mem_read(sp, 20))
            current[:] = [sample_name(sample), phase_increment, stretch, e.bc.bc_total()]

    def at_return(e):
        if e.u32(0):
            null_writes[0] += counting[0]
            e.w32(0, 0)
        if not current:
            return
        name, pinc, tsr, start = current
        current.clear()
        s = stats[name]
        s["calls"] += 1
        s["native"] += pinc == UNITY and tsr == UNITY
        s["interpolated"] += pinc != UNITY
        s["stretched"] += tsr != UNITY
        s["instructions"] += e.bc.bc_total() - start
        s["increments"][(pinc, tsr)] += 1

    stretcher = collections.Counter()

    def counter(name):
        def f(e):
            if counting[0]:
                stretcher[name] += 1
        return f
    emu.intercept(emu.sym.find("_ZN11VoiceSample6renderE"), at_render)
    emu.intercept(emu.sym.find("_ZN13TimeStretcher4initE"), counter("init"))
    emu.intercept(emu.sym.find("_ZN13TimeStretcher6hopEndE"), counter("hopEnd"))

    player = E.Player(emu)
    player.start()
    t = time.time()
    player.play(BAR)
    log(f"warm-up: 1 bar ({time.time() - t:.1f} s)")
    emu.bc.bc_reset_counts()
    counting[0] = True
    windows = []
    t = time.time()
    player.play(int(args.bars * BAR), windows)
    counting[0] = False
    log(f"measured: {args.bars:g} bars, {len(windows)} windows ({time.time() - t:.1f} s)")
    instr = np.array([w[0] for w in windows], np.float64)
    nsamp = np.array([w[1] for w in windows])
    audio = np.concatenate([w[4] for w in windows])
    np.save(os.path.join(out_dir, "output.npy"), audio)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    with open(os.path.join(out_dir, "measured.wav"), "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
                 struct.pack("<IHHIIHH", 16, 1, 2, SR, SR * 4, 4, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)
    functions = E.profile_by_function(emu)
    total = int(nsamp.sum())
    areas = collections.Counter()
    for name, n in functions.items():
        areas[E.area_of(name)] += n / total * 128
    os.remove(image)
    result = dict(
        tenths=args.tenths, no_cache=args.no_cache, samples=total, per_128=float(instr.sum() / total * 128),
        full_window_mean=float(instr[nsamp == 128].mean()), areas=dict(areas.most_common()),
        top=[(n, round(c / total * 128)) for n, c in functions.most_common(12)],
        voices={k: dict(v, increments={f"{a:#x}/{b:#x}": c for (a, b), c in v["increments"].most_common(4)},
                        per_call=v["instructions"] / max(1, v["calls"])) for k, v in sorted(stats.items())},
        time_stretcher=dict(stretcher), peak=float(np.abs(audio).max()), null_writes=null_writes[0])
    json.dump(result, open(os.path.join(out_dir, "run.json"), "w"), indent=1)


# --- the analysis

EDGE = 8820  # 0.2 s: the band filters' edges at the start and end of the recording are left out


def expected_hz(f, transpose):
    return f * 2 ** (transpose / 12) * TUNE


def crossings(env, level, rising):
    """Sample positions (fractional, interpolated) where env crosses level upwards (or downwards)."""
    a, b = env[:-1], env[1:]
    idx = np.flatnonzero((a < level) & (b >= level)) if rising else np.flatnonzero((a >= level) & (b < level))
    return idx + (level - a[idx]) / (b[idx] - a[idx])


def band(mono, f, bandwidth=40.0):
    env = np.abs(demodulate(mono, SR, f, bandwidth))
    env[:EDGE] = env[-EDGE:] = 0
    return env


def events(env, level, hold=1323):
    """Onsets (rising through level, the level held for hold samples after) and ends (falling through it, held for
    hold samples before), interpolated to fractions of a sample: a note's edges, not a neighbour's transient."""
    def held(x, after):
        i = int(x)
        seg = env[i + 2:i + 2 + hold] if after else env[max(0, i - hold):i - 1]
        return seg.size == hold - (0 if after else 1) and seg.min() > 0.8 * level
    return (np.array([x for x in crossings(env, level, True) if held(x, True)]),
            np.array([x for x in crossings(env, level, False) if held(x, False)]))


def markers(env_a, env_b, share=0.5):
    """Onsets and ends in two runs at the same level (share of the first run's level)."""
    level = share * np.percentile(env_a[EDGE:-EDGE], 99.5)
    return [events(e, level) for e in (env_a, env_b)]


KEEP_LENGTH = ("CLIP", "STRETCH")  # Their markers against the original at 440 Hz (no time stretch there)


def pair_up(a, b, tolerance=2000):
    """Differences b - a of the markers that correspond (nearest within tolerance)."""
    d = []
    for x in a:
        if len(b):
            j = np.argmin(np.abs(b - x))
            if abs(b[j] - x) < tolerance:
                d.append(b[j] - x)
    return np.array(d)


def pitch(mono, f):
    fm, used = tone_frequency(mono, SR, f, bandwidth=30, threshold=0.3, margin_s=0.06, edge=EDGE)
    return dict(hz=fm, cents=float(cents(fm, f)), samples=used)


def analyse(outputs):
    mono = {k: v.mean(axis=1) for k, v in outputs.items()}
    res = {}
    for name, f, tr in PARTS:
        fe = expected_hz(f, tr)
        res[name] = dict(expected_hz=fe, pitch={k: pitch(mono[k], fe * (1 / TUNE if k == "orig440" else 1))
                                               for k in mono})
        ref = "orig440" if name in KEEP_LENGTH else "orig432"
        env_o = band(mono[ref], fe / TUNE if ref == "orig440" else fe)
        env_c = band(mono["conv432"], fe)
        (on_o, off_o), (on_c, off_c) = markers(env_o, env_c)
        res[name]["markers"] = dict(reference=ref, onsets=len(on_o), ends=len(off_o), onsets_conv=len(on_c),
                                    ends_conv=len(off_c), onset_diff=pair_up(on_o, on_c).tolist(),
                                    end_diff=pair_up(off_o, off_c).tolist())
        if name in KEEP_LENGTH:  # The firmware's own time stretch at 432 Hz against its native playback at 440 Hz
            (on_o, off_o), (on_s, off_s) = markers(env_o, band(mono["orig432"], fe))
            res[name]["markers"]["firmware_stretch_diff"] = np.abs(np.concatenate(
                [pair_up(on_o, on_s), pair_up(off_o, off_s)])).max().item()
    # The loop's period from its wraps (the sawtooth level falls through the middle at each wrap)
    for k in ("orig432", "conv432"):
        env = band(mono[k], expected_hz(3400, 0), 200.0)
        wraps = events(env, 0.5 * np.percentile(env[EDGE:-EDGE], 99.5))[1]
        periods = np.diff(wraps)
        periods = periods[(periods > 10000) & (periods < 12500)]
        res["LOOPROW"][f"loop_period_{k}"] = float(np.median(periods)) if periods.size else float("nan")
        res["LOOPROW"][f"loop_periods_{k}"] = int(periods.size)
    return res


RUNS = [  # key, card, master tune, sample cache
    ("orig440", "card-orig", 4400, True), ("orig432", "card-orig", 4320, True), ("conv432", "card-432", 4320, True),
    ("orig432-nocache", "card-orig", 4320, False), ("conv432-nocache", "card-432", 4320, False),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("work")
    ap.add_argument("--tools", help="toolchain prefix (.../arm-none-eabi-); default: the ELF's firmware tree's")
    ap.add_argument("--build", help="directory with blockcount.so (default: the work dir, built there)")
    ap.add_argument("--bars", type=float, default=4)
    ap.add_argument("--jobs", type=int, default=2, help="runs at once (default 2)")
    ap.add_argument("--run", nargs=3, metavar=("CARD", "OUT", "TENTHS"), help=argparse.SUPPRESS)
    ap.add_argument("--no-cache", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--reuse", action="store_true", help="keep the runs already in the work dir (analysis only)")
    args = ap.parse_args()
    args.elf = os.path.abspath(args.elf)
    work = os.path.abspath(args.work)
    args.tools = args.tools or os.path.join(os.path.dirname(args.elf),
                                            "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")

    def log(s):
        print(s, flush=True)

    if args.run:
        args.card, args.out, args.tenths = args.run[0], args.run[1], int(args.run[2])
        run(args, log)
        return
    os.makedirs(work, exist_ok=True)
    if not args.build:
        args.build = work
        uc = os.path.dirname(__import__("unicorn").__file__)
        subprocess.run(["cc", "-O2", "-shared", "-fPIC", f"-I{uc}/include", os.path.join(SONG, "blockcount.c"), "-o",
                        os.path.join(work, "blockcount.so"), f"-L{uc}/lib", "-l:libunicorn.so.2",
                        f"-Wl,-rpath,{uc}/lib"], check=True)
    orig, conv = os.path.join(work, "card-orig"), os.path.join(work, "card-432")
    for d in (orig, conv):
        shutil.rmtree(d, ignore_errors=True)
    write_card(orig)
    r = subprocess.run([sys.executable, TOOL, "--card", orig, "--out", conv, "--tuning", "432", "--quiet"],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"retune_library.py failed:\n{r.stdout}{r.stderr}")
    log("retune_library.py: " + r.stdout[r.stdout.find("Summary:"):r.stdout.find("report:")].strip())

    t = time.time()
    procs, pending = [], [r for r in RUNS if not (args.reuse and os.path.exists(os.path.join(work, r[0], "run.json")))]
    while pending or procs:
        while pending and len(procs) < args.jobs:
            key, card, tenths, cache = pending.pop(0)
            cmd = [sys.executable, os.path.abspath(__file__), args.elf, work, "--tools", args.tools, "--build",
                   args.build, "--bars", str(args.bars), "--run", os.path.join(work, card), os.path.join(work, key),
                   str(tenths)] + ([] if cache else ["--no-cache"])
            procs.append((key, subprocess.Popen(cmd, stdout=open(os.path.join(work, key + ".log"), "w"),
                                                stderr=subprocess.STDOUT)))
        time.sleep(1)
        for key, p in list(procs):
            if p.poll() is not None:
                procs.remove((key, p))
                if p.returncode:
                    raise SystemExit(f"run {key} failed:\n" + open(os.path.join(work, key + ".log")).read()[-2000:])
    log(f"{len(RUNS)} runs of {args.bars:g} bars after a 1-bar warm-up ({time.time() - t:.0f} s)")
    results = {k: json.load(open(os.path.join(work, k, "run.json"))) for k, *_ in RUNS}
    ana = analyse({k: np.load(os.path.join(work, k, "output.npy")) for k in ("orig440", "orig432", "conv432")})
    json.dump(dict(runs=results, analysis=ana), open(os.path.join(work, "result.json"), "w"), indent=1, default=str)
    report(results, ana, work, log)


def report(results, ana, work, log):
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            log("FAIL: " + msg)

    def voices(key):
        return {k.replace(".AIF", ".WAV"): v for k, v in results[key]["voices"].items()}
    v440, vo, vc = voices("orig440"), voices("orig432"), voices("conv432")
    von, vcn = voices("orig432-nocache"), voices("conv432-nocache")
    log("\nvoices: VoiceSample::render() calls (one voice, one window of up to 128 samples), native (phaseIncrement and"
        "\ntimeStretchRatio both 1 << 24), and instructions per call (to its return); 432 Hz unless said:")
    log(f"  {'file':12s} {'original at 440':>22s}   {'original':>22s}   {'converted':>22s}   "
        f"{'no cache: orig':>14s} {'conv':>6s}")
    for name in sorted(vc):
        def c(v):
            x = v.get(name, {})
            return f"{x.get('native', 0):5d}/{x.get('calls', 0):5d} {x.get('per_call', 0):7,.0f}"
        log(f"  {name:12s} {c(v440):>22s}   {c(vo):>22s}   {c(vc):>22s}   "
            f"{von.get(name, {}).get('per_call', 0):14,.0f} {vcn.get(name, {}).get('per_call', 0):6,.0f}")
    # Converted at 432 Hz: native wherever the original is at 440 Hz, and the 48 kHz KICK now too (44.1 kHz); the
    # voices last 1.85 % longer (the files are), so they render in a few more windows
    for name, x in v440.items():
        y = vc.get(name, {})
        share = 1 if name == "KICK.WAV" else x["native"] / x["calls"]
        check(y.get("calls") and abs(y["native"] / y["calls"] - share) < 0.002,
              f"{name}: native {y.get('native')}/{y.get('calls')} at 432 Hz converted, {x['native']}/{x['calls']} "
              f"at 440 Hz original")
    for name in ("KICK.WAV", "SNARE.WAV", "LOOP.WAV", "HAT.WAV", "STRETCH.WAV", "CLIP.WAV", "MULTIA.WAV",
                 "MULTIB.WAV"):
        check(vo.get(name, {}).get("native", 1) == 0, f"{name}: native at 432 Hz on the original card?")
    ts = {k: results[k]["time_stretcher"] for k in results}
    log("TimeStretcher::init() / hopEnd() calls: " + ", ".join(
        f"{k} {ts[k].get('init', 0)} / {ts[k].get('hopEnd', 0)}" for k in ("orig440", "orig432", "conv432")))
    check(not ts["conv432"] and not ts["orig440"], "time stretching on the converted card or at 440 Hz")
    check(ts["orig432"].get("hopEnd", 0) > 0, "the original at 432 Hz doesn't time-stretch")
    log("writes to address 0 (readSamplesResampled() for the time stretcher, see run()): "
        + ", ".join(f"{k} {results[k]['null_writes']}" for k in results))

    log("\ninstructions per 128 samples (the whole AudioEngine::routine()), and the area sample reading / "
        "interpolation / time-stretch:")
    area = "sample reading / interpolation / time-stretch"
    for k in results:
        r = results[k]
        log(f"  {k:16s} {r['per_128']:9,.0f} ({r['per_128'] / E.CYCLES_PER_BLOCK * 100:4.1f}% CPU)   "
            f"{r['areas'].get(area, 0):9,.0f}")
    po, pc, p440 = (results[k]["per_128"] for k in ("orig432", "conv432", "orig440"))
    pon, pcn = results["orig432-nocache"]["per_128"], results["conv432-nocache"]["per_128"]
    log(f"  converted against original at 432 Hz: {pc - po:+,.0f} ({(pc / po - 1) * 100:+.1f}%), without the cache "
        f"{pcn - pon:+,.0f} ({(pcn / pon - 1) * 100:+.1f}%); against the original at 440 Hz {(pc / p440 - 1) * 100:+.1f}%")
    check(pc < po and pcn < pon, "the converted card costs more")
    check(abs(pc / p440 - 1) < 0.03, "the converted card at 432 Hz doesn't cost what the original does at 440 Hz")
    for k in results:
        check(results[k]["peak"] < 0.99, f"{k}: output clipped (peak {results[k]['peak']:.2f})")

    log("\npitch against the exact target (cents; at 440 Hz against the 440 Hz pitch), and the markers (the notes'"
        "\nonsets and ends) of the converted card against the original's at 432 Hz, for the audio clip and the STRETCH"
        "\nrow (length kept, Rubber Band) against the original's at 440 Hz (samples):")
    worst = 0.0
    for name, f, tr in PARTS:
        a = ana[name]
        p, m = a["pitch"], a["markers"]
        on, off = np.abs(m["onset_diff"]), np.abs(m["end_diff"])
        log(f"  {name:8s} {a['expected_hz']:9.3f} Hz   at 440 {p['orig440']['cents']:+.5f}   original "
            f"{p['orig432']['cents']:+.5f}   converted {p['conv432']['cents']:+.5f}   onsets {len(on)}/{m['onsets']}"
            + (f" max {on.max():.1f}" if on.size else "") + f", ends {len(off)}/{m['ends']}"
            + (f" max {off.max():.1f}" if off.size else "")
            + (f" (the firmware's time stretch at 432 Hz: {m['firmware_stretch_diff']:.0f})" if name in KEEP_LENGTH
               else ""))
        worst = max(worst, abs(p["conv432"]["cents"]))
        check(abs(p["conv432"]["cents"]) < 0.01, f"{name}: converted card {p['conv432']['cents']:+.5f} cents")
        check(name.startswith(("SMP", "MULTI")) or
              (m["onsets"] == m["onsets_conv"] == len(on) and m["ends"] == m["ends_conv"] == len(off)),
              f"{name}: markers {m['onsets']}/{m['ends']} -> {m['onsets_conv']}/{m['ends_conv']}, paired "
              f"{len(on)}/{len(off)}")
        # Position markers: the KICK's zone start and end, the file ends of the one-shots, the loop's wraps (a slow
        # ramp's middle is no marker), the length-kept notes (Rubber Band's own timing: within ~1.4 ms). The synths'
        # notes start at the file's start and end at note-off: only reported
        limit = 60 if name in KEEP_LENGTH else 4
        if not name.startswith(("SMP", "MULTI")):
            worst_marker = max(on.max() if on.size and name != "LOOPROW" else 0, off.max() if off.size else 0)
            check(worst_marker <= limit, f"{name}: markers moved by {worst_marker:.1f} samples (limit {limit})")
    lo, lc = ana["LOOPROW"]["loop_period_orig432"], ana["LOOPROW"]["loop_period_conv432"]  # 850 cycles
    log(f"  LOOPROW loop period (median of {ana['LOOPROW']['loop_periods_conv432']} wraps): original {lo:.2f} samples "
        f"(11025 * 440/432 = {11025 / TUNE:.2f}), converted {lc:.2f} (11229: "
        f"{1200 * math.log2(11025 / TUNE / 11229):+.3f} cents)")
    check(abs(lc - 11229) < 0.3 and abs(lo - 11025 / TUNE) < 0.3, "LOOPROW: loop period")
    log(f"\nworst pitch error on the converted card: {worst:.5f} cents")
    log(f"{len(failures)} FAILURES" if failures else "all checks passed")
    log(f"results: {work}/result.json, measured.wav in {work}/<run>/")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
