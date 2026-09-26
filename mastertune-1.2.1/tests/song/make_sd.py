#!/usr/bin/env python3
"""Builds the SD card image for the song benchmark: generated samples and the song SONGS/DEFAULT.XML.

Usage: make_sd.py <image> [--reverb-model N] [--xml-out file]

The song ("everything at once", 120 BPM, 4/4, the firmware's default resolution of 96 ticks per quarter note):
- 8 synth tracks, all playing 4-bar clips of 4-note chords (Cm9, Abmaj7, Fm9, G7sus4, one chord per bar), 2
  oscillators (saw and square) each, a
  24 dB ladder low-pass with an envelope, all sidechain-ducked by the kick and sending to the reverb (the chords' notes
  last 2 beats, and ring on with a long release):
    PADA  unison 4, HPF, LFO1 on the LPF, chorus
    PADB  unison 4, LFO1 on the LPF, phaser, delay (dotted 8ths, ping-pong)
    PADC  HPF, flanger, compressor
    PADD  unison 2, LFO1 on the LPF, bitcrush and sample rate reduction
    ARP   arpeggiator in 16ths (up, 2 octaves) on the chords, delay
    PADE  unison 4, HPF, LFO1 on the LPF (the big plain pad)
    FM    the DX7 engine (an electric piano, algorithm 5), no filter use
    WT    a wavetable (32 frames morphing, LFO2 on its position) and a square, LFO1 on the LPF
- 1 kit with 8 rows in a 1-bar 16th-note pattern: kick (sends to the sidechain), snare, clap, closed and open hat,
  rim, a tom transposed +5 and a bell transposed -7 semitones (pitched sample playback)
- 1 audio track playing a 2-bar loop recorded at 100 BPM in a 2-bar clip, so time-stretched to 120 BPM
- Song: the reverb (Mutable, the default model, or --reverb-model), ducked by the sidechain; the drone with 4 binaural
  tones (100, 150, 200 and 300 Hz carriers with 4 to 10 Hz beats), ducked by the sidechain too.
"""
import argparse
import math
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fat32  # noqa: E402

SR = 44100
TICKS_PER_QUARTER = 96  # inputTickMagnitude 2
BAR = 4 * TICKS_PER_QUARTER
STEP = TICKS_PER_QUARTER // 4
RNG = np.random.default_rng(1)


# --- samples

def wav(data, channels=1):
    data = np.clip(np.asarray(data), -1, 1)
    pcm = (data * 32767).astype("<i2")
    if channels == 2:
        pcm = pcm.reshape(-1)
    raw = pcm.tobytes()
    fmt = struct.pack("<HHIIHH", 1, channels, SR, SR * 2 * channels, 2 * channels, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(raw)) + raw
    return b"RIFF" + struct.pack("<I", len(body)) + body


def t(seconds):
    return np.arange(int(seconds * SR)) / SR


def env(x, decay):
    return np.exp(-x / decay)


def onepole_hp(x, fc):
    a = math.exp(-2 * math.pi * fc / SR)
    y = np.zeros_like(x)
    prev_x = prev_y = 0.0
    for i, v in enumerate(x):
        prev_y = a * (prev_y + v - prev_x)
        prev_x = v
        y[i] = prev_y
    return y


def samples():
    out = {}
    x = t(0.5)
    phase = 2 * math.pi * np.cumsum(45 + 110 * np.exp(-x / 0.03)) / SR
    out["KICK"] = 0.95 * np.sin(phase) * env(x, 0.15)
    x = t(0.4)
    out["SNARE"] = 0.5 * np.sin(2 * math.pi * 185 * x) * env(x, 0.06) + 0.45 * RNG.uniform(-1, 1, x.size) * env(x, 0.1)
    x = t(0.3)
    noise = RNG.uniform(-1, 1, x.size)
    bursts = sum(env(np.maximum(x - d, 0), 0.008) * (x >= d) for d in (0, 0.011, 0.023))
    out["CLAP"] = 0.6 * noise * (bursts + env(x, 0.07))
    x = t(0.1)
    out["HATC"] = 0.4 * onepole_hp(RNG.uniform(-1, 1, x.size), 7000) * env(x, 0.02)
    x = t(0.5)
    out["HATO"] = 0.35 * onepole_hp(RNG.uniform(-1, 1, x.size), 6000) * env(x, 0.18)
    x = t(0.1)
    out["RIM"] = 0.6 * (np.sin(2 * math.pi * 1700 * x) + 0.5 * np.sin(2 * math.pi * 820 * x)) * env(x, 0.012)
    x = t(0.6)
    out["TOM"] = 0.8 * np.sin(2 * math.pi * np.cumsum(110 + 40 * env(x, 0.05)) / SR) * env(x, 0.2)
    x = t(1.2)
    out["BELL"] = 0.4 * sum(a * np.sin(2 * math.pi * f * x) * env(x, d)
                            for f, a, d in ((523.25, 1, 0.5), (1310, 0.5, 0.25), (2093, 0.3, 0.15), (2870, 0.2, 0.08)))
    files = {f"SAMPLES/{name}.WAV": wav(data) for name, data in out.items()}
    lengths = {name: len(data) for name, data in out.items()}

    # The loop: 2 bars at 100 BPM, stereo (kick, hats, a bass line)
    bar = 60 / 100 * 4
    x = t(2 * bar)
    left = np.zeros(x.size)
    for beat in range(8):
        start = int(beat * bar / 4 * SR)
        k = out["KICK"][: x.size - start]
        left[start:start + k.size] += 0.7 * k
        h = int((beat + 0.5) * bar / 4 * SR)
        hh = out["HATO"][: x.size - h]
        left[h:h + hh.size] += 0.5 * hh
    bass_notes = [36, 36, 39, 36, 41, 39, 36, 34]
    bass = np.zeros(x.size)
    for i, n in enumerate(bass_notes):
        s, e = int(i * bar / 4 * SR), int((i + 1) * bar / 4 * SR)
        xx = x[s:e] - x[s]
        f = 440 * 2 ** ((n - 69) / 12)
        bass[s:e] = 0.35 * np.sign(np.sin(2 * math.pi * f * xx)) * env(xx, 0.25)
    right = left.copy()
    left += bass
    right += 0.8 * bass
    loop = np.stack([left, right], axis=1) * 0.6
    files["SAMPLES/LOOP.WAV"] = wav(loop, 2)
    lengths["LOOP"] = x.size

    # The wavetable: 32 cycles of 2048 samples, sine to saw with a moving pulse
    frames = []
    ph = np.arange(2048) / 2048
    for i in range(32):
        m = i / 31
        saw = sum(np.sin(2 * math.pi * k * ph) / k for k in range(1, 40)) * (2 / math.pi)
        frames.append((1 - m) * np.sin(2 * math.pi * ph) + m * saw * 0.9)
    files["SAMPLES/WT.WAV"] = wav(np.concatenate(frames) * 0.8)
    return files, lengths


# --- parameters: knob positions 0 to 50 as the Deluge stores them

def knob(k):
    v = int(round(-2 ** 31 + k / 50 * 2 ** 32))
    return f"0x{max(-2 ** 31, min(2 ** 31 - 1, v)) & 0xFFFFFFFF:08X}"


def cable(k):  # Patch cable amounts, -50 to 50
    return f"0x{int(round(k / 50 * 2 ** 30)) & 0xFFFFFFFF:08X}"


def attrs(d, indent):
    pad = "\t" * indent
    return "".join(f'\n{pad}{k}="{v}"' for k, v in d.items())


VOLUME_SYNTH = knob(19)
VOLUME_DRUM = knob(30)
VOLUME_KIT = knob(36)
VOLUME_LOOP = knob(24)

SOUND_PARAMS = dict(
    portamento=knob(0), compressorShape="0xDC28F5B2", oscAVolume=knob(50), oscAPulseWidth=knob(25),
    oscAWavetablePosition=knob(25), oscBVolume=knob(40), oscBPulseWidth=knob(25), oscBWavetablePosition=knob(25),
    noiseVolume=knob(0), volume=VOLUME_SYNTH, pan=knob(25), lpfFrequency=knob(30), lpfResonance=knob(15),
    hpfFrequency=knob(0), hpfResonance=knob(0), lfo1Rate=knob(30), lfo2Rate=knob(25), modulator1Amount=knob(0),
    modulator1Feedback=knob(0), modulator2Amount=knob(0), modulator2Feedback=knob(0), carrier1Feedback=knob(0),
    carrier2Feedback=knob(0), modFXRate=knob(25), modFXDepth=knob(25), delayRate=knob(25), delayFeedback=knob(0),
    reverbAmount=knob(15), arpeggiatorRate=knob(25), stutterRate=knob(25), sampleRateReduction=knob(0),
    bitCrush=knob(0), modFXOffset=knob(25), modFXFeedback=knob(25), compressorThreshold=knob(25),
    arpeggiatorGate=knob(25), noteProbability=knob(50), bassProbability=knob(0), swapProbability=knob(0),
    glideProbability=knob(0), reverseProbability=knob(0), chordProbability=knob(0), ratchetProbability=knob(0),
    ratchetAmount=knob(0), sequenceLength=knob(0), chordPolyphony=knob(0), rhythm=knob(0), spreadVelocity=knob(0),
    spreadGate=knob(0), spreadOctave=knob(0), lpfMorph=knob(0), hpfMorph=knob(0), waveFold=knob(0),
)

MOD_KNOBS = """
			<modKnobs>
				<modKnob controlsParam="pan" />
				<modKnob controlsParam="volumePostFX" />
				<modKnob controlsParam="lpfResonance" />
				<modKnob controlsParam="lpfFrequency" />
				<modKnob controlsParam="env1Release" />
				<modKnob controlsParam="env1Attack" />
				<modKnob controlsParam="delayFeedback" />
				<modKnob controlsParam="delayRate" />
				<modKnob controlsParam="reverbAmount" />
				<modKnob controlsParam="volumePostReverbSend" patchAmountFromSource="compressor" />
				<modKnob controlsParam="pitch" patchAmountFromSource="lfo1" />
				<modKnob controlsParam="lfo1Rate" />
				<modKnob controlsParam="portamento" />
				<modKnob controlsParam="stutterRate" />
				<modKnob controlsParam="bitcrushAmount" />
				<modKnob controlsParam="sampleRateReduction" />
			</modKnobs>"""


def sound_params_block(tag, params, env1, env2, cables, indent):
    pad = "\t" * indent
    out = f"{pad}<{tag}{attrs(params, indent + 1)}>\n"
    out += f"{pad}\t<envelope1{attrs(env1, indent + 2)} />\n"
    out += f"{pad}\t<envelope2{attrs(env2, indent + 2)} />\n"
    out += f"{pad}\t<patchCables>\n"
    for source, destination, amount in cables:
        out += f'{pad}\t\t<patchCable source="{source}" destination="{destination}" amount="{cable(amount)}" />\n'
    out += f"{pad}\t</patchCables>\n"
    out += f'{pad}\t<equalizer bass="0x00000000" treble="0x00000000" bassFrequency="0x00000000" ' \
           f'trebleFrequency="0x00000000" />\n'
    out += f"{pad}</{tag}>\n"
    return out


def note_data(notes):
    """notes: (pos, length, velocity); hex as the firmware writes noteData: position, length, velocity, probability"""
    return "0x" + "".join(f"{p:08X}{n:08X}{v:02X}14" for p, n, v in sorted(notes))


def instrument_clip(name, folder, length, note_rows, extra="", params_block="", arp=None, kit=False):
    arp = arp or dict(arpMode="off")
    a = dict(syncLevel=6, numOctaves=2, syncType=0, chordType=0, noteMode="up", octaveMode="up", mpeVelocity="off")
    a.update(arp)
    out = f'\t\t<instrumentClip{attrs(dict(inKeyMode=0, yScroll=40, instrumentPresetName=name, instrumentPresetFolder=folder, isPlaying=1, isSoloing=0, isArmedForRecording=0, length=length, colourOffset=0, section=0), 3)}>\n'
    out += f"\t\t\t<arpeggiator{attrs(a, 4)} />\n"
    out += params_block
    out += "\t\t\t<noteRows>\n"
    for key, value, notes, row_params in note_rows:
        out += f'\t\t\t\t<noteRow {key}="{value}" noteData="{note_data(notes)}"'
        if row_params:
            out += ">\n" + row_params + "\t\t\t\t</noteRow>\n"
        else:
            out += " />\n"
    out += "\t\t\t</noteRows>\n"
    out += "\t\t</instrumentClip>\n"
    return out


NOTE_LENGTH = BAR // 2  # The chords' notes: 2 beats, then their release rings on
CHORDS = [[48, 55, 58, 63], [44, 51, 55, 60], [41, 48, 51, 55], [43, 50, 53, 60]]  # Cm9, Abmaj7, Fm9, G7sus4


def chord_rows(octave, velocity=90):
    rows = {}
    for bar, chord in enumerate(CHORDS):
        for n in chord:
            rows.setdefault(n + 12 * octave, []).append((bar * BAR, NOTE_LENGTH, velocity))
    return [("y", y, notes, None) for y, notes in sorted(rows.items())]


def dx7_patch():
    """An electric piano: algorithm 5 (three carrier-modulator pairs), 156 bytes as the Deluge stores them."""
    ops = []
    # op6 .. op1: rates, levels, scaling, sens, output level, mode, coarse, fine, detune
    specs = [  # (carrier?, level, coarse)
        (False, 60, 1),  # op6 -> op5
        (True, 90, 1),   # op5
        (False, 72, 14),  # op4 -> op3 (the tine)
        (True, 92, 1),   # op3
        (False, 78, 1),  # op2 -> op1
        (True, 99, 1),   # op1
    ]
    for carrier, level, coarse in specs:
        rates = [95, 30, 20, 60] if carrier else [95, 40, 30, 60]
        levels = [99, 85, 70, 0] if carrier else [99, 70, 40, 0]
        ops += rates + levels + [39, 0, 0, 0, 0, 2, 0, 2, level, 0, coarse, 0, 7 + (1 if carrier else 0)]
    common = [99, 99, 99, 99, 50, 50, 50, 50, 4, 5, 1, 35, 0, 0, 0, 1, 0, 3, 24]
    name = list(b"MT E.PIANO")
    data = ops + common + name + [0x3F]
    assert len(data) == 156, len(data)
    return "".join(f"{b:02X}" for b in data)


def synth(name, osc1, osc2, unison, params, env1, env2, cables, mod_fx="none", notes_octave=0, arp=None,
          delay=None, extra_osc1=""):
    sound = dict(presetName=name, presetFolder="SYNTHS", defaultVelocity=64, isArmedForRecording=0,
                 activeModFunction=1, colour=0, polyphonic="poly", voicePriority=1, mode="subtractive",
                 modFXType=mod_fx, lpfMode="24dB", hpfMode="HPLadder", filterRoute="H2L", maxVoices=8)
    out = f"\t\t<sound{attrs(sound, 3)}>\n"
    out += f"\t\t\t<osc1{attrs(osc1, 4)}{extra_osc1} />\n" if not osc1.get("_body") else osc1["_body"]
    out += f"\t\t\t<osc2{attrs(osc2, 4)} />\n"
    out += '\t\t\t<lfo1 type="triangle" syncLevel="0" syncType="0" />\n'
    out += '\t\t\t<lfo2 type="sine" syncLevel="0" syncType="0" />\n'
    out += f'\t\t\t<unison num="{unison}" detune="12" spread="10" />'
    out += MOD_KNOBS + "\n"
    d = dict(pingPong=1, analog=0, syncLevel=5, syncType=2)
    if delay:
        d.update(delay)
    out += f"\t\t\t<delay{attrs(d, 4)} />\n"
    out += '\t\t\t<sidechain attack="327244" release="936" syncLevel="6" syncType="0" />\n'
    out += '\t\t\t<audioCompressor attack="83886080" release="83886080" thresh="0" ratio="1073741824" ' \
           'compHPF="0" compBlend="2147483647" />\n'
    out += "\t\t</sound>\n"
    p = dict(SOUND_PARAMS)
    p.update(params)
    block = sound_params_block("soundParams", p, env1, env2, cables, 3)
    clip = instrument_clip(name, "SYNTHS", 4 * BAR, chord_rows(notes_octave), params_block=block, arp=arp)
    return out, clip


PAD_ENV1 = dict(attack=knob(12), decay=knob(30), sustain=knob(40), release=knob(12))  # Release about 0.85 s
PAD_ENV2 = dict(attack=knob(10), decay=knob(25), sustain=knob(20), release=knob(25))
BASE_CABLES = [("velocity", "volume", 25), ("envelope2", "lpfFrequency", 12), ("note", "lpfFrequency", 5),
               ("compressor", "volumePostReverbSend", 30)]  # compressor = the sidechain (ducking by the kick)
LFO_FILTER = ("lfo1", "lpfFrequency", 10)
SAW = dict(type="saw", transpose=0, cents=0, retrigPhase=-1)
SQUARE = dict(type="square", transpose=0, cents=7, retrigPhase=-1)


def synths():
    parts = []
    parts.append(synth("PADA", SAW, SQUARE, 4, dict(hpfFrequency=knob(12), modFXDepth=knob(30), reverbAmount=knob(25)),
                       PAD_ENV1, PAD_ENV2, BASE_CABLES + [LFO_FILTER], mod_fx="chorus", notes_octave=0))
    parts.append(synth("PADB", SAW, SQUARE, 4, dict(delayFeedback=knob(20), delayRate=knob(25), modFXDepth=knob(30)),
                       PAD_ENV1, PAD_ENV2, BASE_CABLES + [LFO_FILTER], mod_fx="phaser", notes_octave=1))
    parts.append(synth("PADC", SAW, SQUARE, 1, dict(hpfFrequency=knob(15), compressorThreshold=knob(35),
                                                    modFXDepth=knob(30), modFXFeedback=knob(30)),
                       PAD_ENV1, PAD_ENV2, BASE_CABLES, mod_fx="flanger", notes_octave=1))
    parts.append(synth("PADD", SAW, SQUARE, 2, dict(bitCrush=knob(22), sampleRateReduction=knob(18),
                                                    volume=knob(17)),
                       PAD_ENV1, PAD_ENV2, BASE_CABLES + [LFO_FILTER], notes_octave=0))
    arp_env = dict(attack=knob(0), decay=knob(18), sustain=knob(15), release=knob(3))
    parts.append(synth("ARP", SAW, SQUARE, 1, dict(delayFeedback=knob(22), lpfFrequency=knob(34),
                                                   arpeggiatorGate=knob(20)),
                       arp_env, PAD_ENV2, BASE_CABLES, notes_octave=1,
                       arp=dict(arpMode="arp", noteMode="up", octaveMode="up", numOctaves=2, syncLevel=6)))
    parts.append(synth("PADE", SAW, SQUARE, 4, dict(hpfFrequency=knob(10), reverbAmount=knob(30)),
                       PAD_ENV1, PAD_ENV2, BASE_CABLES + [LFO_FILTER], notes_octave=0))
    fm_osc1 = dict(type="dx7", transpose=0, cents=0, retrigPhase=-1)
    fm = synth("FM", fm_osc1, dict(type="square", transpose=0, cents=0, retrigPhase=-1), 1,
               dict(oscBVolume=knob(0), lpfFrequency=knob(50), lpfResonance=knob(0), reverbAmount=knob(20)),
               dict(attack=knob(0), decay=knob(30), sustain=knob(45), release=knob(12)), PAD_ENV2,
               [("velocity", "volume", 25), ("compressor", "volumePostReverbSend", 30)], notes_octave=1,
               extra_osc1=f'\n\t\t\t\tdx7patch="{dx7_patch()}"')
    parts.append(fm)
    wt_osc1 = dict(type="wavetable", transpose=0, cents=0, retrigPhase=-1)
    wt_body = f'\t\t\t<osc1{attrs(wt_osc1, 4)}\n\t\t\t\tfileName="SAMPLES/WT.WAV" />\n'
    wt_osc1["_body"] = wt_body
    parts.append(synth("WT", wt_osc1, SQUARE, 2, dict(oscBVolume=knob(30)),
                       PAD_ENV1, PAD_ENV2, BASE_CABLES + [LFO_FILTER, ("lfo2", "oscAWavetablePosition", 20)],
                       notes_octave=1))
    return parts


# --- kit

KIT_ROWS = [  # name, steps (16ths), velocity, transpose
    ("KICK", [0, 4, 8, 12], 110, 0),
    ("SNARE", [4, 12], 100, 0),
    ("CLAP", [12, 14], 90, 0),
    ("HATC", [0, 1, 3, 4, 5, 7, 8, 9, 11, 12, 13, 15], 70, 0),
    ("HATO", [2, 6, 10, 14], 80, 0),
    ("RIM", [3, 10], 80, 0),
    ("TOM", [13, 15], 95, 5),
    ("BELL", [0, 6, 11], 85, -7),
]

KIT_PARAMS = dict(
    reverbAmount=knob(10), volume=VOLUME_KIT, pan=knob(25), sidechainCompressorShape="0xDC28F5B2",
    modFXDepth=knob(25), modFXRate=knob(20), stutterRate=knob(25), sampleRateReduction=knob(0), bitCrush=knob(0),
    modFXOffset=knob(25), modFXFeedback=knob(0), compressorThreshold=knob(0),
)


def global_params_block(tag, params, indent):
    pad = "\t" * indent
    out = f"{pad}<{tag}{attrs(params, indent + 1)}>\n"
    out += f'{pad}\t<delay rate="{knob(25)}" feedback="{knob(0)}" />\n'
    out += f'{pad}\t<lpf frequency="{knob(50)}" resonance="{knob(0)}" />\n'
    out += f'{pad}\t<hpf frequency="{knob(0)}" resonance="{knob(0)}" />\n'
    out += f'{pad}\t<equalizer bass="0x00000000" treble="0x00000000" bassFrequency="0x00000000" ' \
           f'trebleFrequency="0x00000000" />\n'
    out += f"{pad}</{tag}>\n"
    return out


def kit(lengths):
    k = dict(presetName="KIT", presetFolder="KITS", defaultVelocity=64, isArmedForRecording=0, activeModFunction=0,
             colour=0, lpfMode="24dB", hpfMode="HPLadder", filterRoute="H2L", modFXType="none")
    out = f"\t\t<kit{attrs(k, 3)}>\n"
    out += '\t\t\t<delay pingPong="1" analog="0" syncLevel="7" syncType="0" />\n'
    out += '\t\t\t<sidechain attack="327244" release="936" syncLevel="6" syncType="0" />\n'
    out += '\t\t\t<audioCompressor attack="83886080" release="83886080" thresh="0" ratio="1073741824" ' \
           'compHPF="0" compBlend="2147483647" />\n'
    out += "\t\t\t<soundSources>\n"
    rows = []
    for index, (name, steps, velocity, transpose) in enumerate(KIT_ROWS):
        s = dict(name=name, polyphonic="auto", voicePriority=1, mode="subtractive", lpfMode="24dB",
                 hpfMode="HPLadder", filterRoute="H2L", modFXType="none", maxVoices=8)
        if name == "KICK":
            s["sideChainSend"] = 2147483647
        out += f"\t\t\t\t<sound{attrs(s, 5)}>\n"
        osc = dict(type="sample", transpose=transpose, cents=0, loopMode=1, reversed=0, timeStretchEnable=0,
                   timeStretchAmount=0, fileName=f"SAMPLES/{name}.WAV")
        out += f"\t\t\t\t\t<osc1{attrs(osc, 6)}>\n"
        out += f'\t\t\t\t\t\t<zone startSamplePos="0" endSamplePos="{lengths[name]}" />\n'
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
        p = dict(SOUND_PARAMS)
        p.update(oscBVolume=knob(0), lpfFrequency=knob(50), lpfResonance=knob(0), volume=VOLUME_DRUM,
                 reverbAmount=knob(5 if name != "BELL" else 20), compressorThreshold=knob(0))
        row_env1 = dict(attack=knob(0), decay=knob(25), sustain=knob(50), release=knob(0))
        row_params = sound_params_block("soundParams", p, row_env1, PAD_ENV2, [("velocity", "volume", 25)], 5)
        notes = [(s_ * STEP, STEP, velocity - (10 if name == "HATC" and s_ % 2 else 0)) for s_ in steps]
        rows.append(("drumIndex", index, notes, row_params))
    out += "\t\t\t</soundSources>\n"
    out += "\t\t\t<selectedDrumIndex>0</selectedDrumIndex>\n"
    out += "\t\t</kit>\n"
    block = global_params_block("kitParams", KIT_PARAMS, 3)
    clip = instrument_clip("KIT", "KITS", BAR, rows, params_block=block, kit=True)
    return out, clip


def audio_track(lengths):
    out = f'\t\t<audioTrack{attrs(dict(name="LOOP", inputChannel="none", activeModFunction=0, lpfMode="24dB", hpfMode="HPLadder", filterRoute="H2L", modFXType="none"), 3)}>\n'
    out += '\t\t\t<delay pingPong="1" analog="0" syncLevel="7" syncType="0" />\n'
    out += '\t\t\t<sidechain attack="327244" release="936" syncLevel="6" syncType="0" />\n'
    out += '\t\t\t<audioCompressor attack="83886080" release="83886080" thresh="0" ratio="1073741824" ' \
           'compHPF="0" compBlend="2147483647" />\n'
    out += "\t\t</audioTrack>\n"
    clip = f'\t\t<audioClip{attrs(dict(trackName="LOOP", filePath="SAMPLES/LOOP.WAV", startSamplePos=0, endSamplePos=lengths["LOOP"], pitchSpeedIndependent=1, attack=0, priority=1, overdubsShouldCloneAudioTrack=1, isPlaying=1, isSoloing=0, isArmedForRecording=0, length=2 * BAR, colourOffset=0, section=0), 3)}>\n'
    clip += global_params_block("params", dict(KIT_PARAMS, volume=VOLUME_LOOP, reverbAmount=knob(5)), 3)
    clip += "\t\t</audioClip>\n"
    return out, clip


def drone():
    out = '\t<drone volume="34" sidechain="20" sidechainShape="-601295438">\n'
    for i, (freq, beat, pan) in enumerate(((10000, 400, 0), (15000, 600, -10), (20000, 800, 10), (30000, 1000, 0))):
        out += f'\t\t<tone index="{i}" active="1" mode="1" timbre="0" byNote="0" note="57" cents="0" ' \
               f'frequency="{freq}" beat="{beat}" sync="0" level="36" pan="{pan}" />\n'
    out += "\t</drone>\n"
    return out


def song_xml(lengths, reverb_model):
    head = dict(firmwareVersion="c1.2.1", earliestCompatibleFirmware="4.1.0-alpha", arrangementAutoScrollOn=0,
                xScroll=0, xZoom=24, yScrollSongView=-7, yScrollArrangementView=-7, xScrollArrangementView=0,
                xZoomArrangementView=192, timePerTimerTick=229, timerTickFraction=-1073741824, rootNote=0,
                inputTickMagnitude=2, swingAmount=0, swingInterval=6, affectEntire=0, activeModFunction=1,
                modFXType="none", lpfMode="24dB", hpfMode="HPLadder", filterRoute="H2L", sessionLayout=0)
    out = '<?xml version="1.0" encoding="UTF-8"?>\n<song' + attrs(head, 1) + ">\n"
    out += "\t<modeNotes>\n" + "".join(f"\t\t<modeNote>{n}</modeNote>\n" for n in (0, 2, 3, 5, 7, 8, 10)) + \
           "\t</modeNotes>\n"
    out += f'\t<reverb roomSize="1288490112" dampening="1546188288" width="2147483647" lowCut="0" ' \
           f'highCut="2147483647" pan="0" model="{reverb_model}">\n'
    out += '\t\t<compressor attack="0" release="0" volume="1073741824" shape="-601295438" syncLevel="7" />\n'
    out += "\t</reverb>\n"
    out += '\t<delay pingPong="1" analog="0" syncLevel="7" syncType="0" />\n'
    out += '\t<sidechain attack="327244" release="936" syncLevel="7" syncType="0" />\n'
    out += '\t<audioCompressor attack="83886080" release="83886080" thresh="0" ratio="1073741824" compHPF="0" ' \
           'compBlend="2147483647" />\n'
    song_params = dict(reverbAmount="0x80000000", volume=knob(22), pan="0x00000000",
                       sidechainCompressorShape="0xDC28F5B2", modFXDepth="0x00000000", modFXRate="0xE0000000",
                       stutterRate="0x00000000", sampleRateReduction="0x80000000", bitCrush="0x80000000",
                       modFXOffset="0x00000000", modFXFeedback="0x80000000", compressorThreshold="0x00000000",
                       tempo="0x00002EE0")
    out += global_params_block("songParams", song_params, 1)
    instruments, clips = [], []
    for part in synths() + [kit(lengths), audio_track(lengths)]:
        instruments.append(part[0])
        clips.append(part[1])
    out += "\t<instruments>\n" + "".join(instruments) + "\t</instruments>\n"
    out += "\t<sessionClips>\n" + "".join(clips) + "\t</sessionClips>\n"
    out += drone()
    out += "</song>\n"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--reverb-model", type=int, default=1, help="0 Freeverb, 1 Mutable (default), 2 Digital")
    ap.add_argument("--xml-out")
    args = ap.parse_args()
    files, lengths = samples()
    xml = song_xml(lengths, args.reverb_model)
    files["SONGS/DEFAULT.XML"] = xml.encode()
    if args.xml_out:
        open(args.xml_out, "w").write(xml)
    fat32.build(args.image, files)
    print(f"{args.image}: {len(files)} files, song {len(xml):,} bytes")


if __name__ == "__main__":
    main()
