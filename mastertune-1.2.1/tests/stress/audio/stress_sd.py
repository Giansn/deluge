#!/usr/bin/env python3
"""SD card images for the audio stress test (run.sh): the song benchmark's song (../../song/make_sd.py, its docstring
describes it) in the variants the scenarios need, and the Community features switched on as the Deluge reads them at
boot (CommunityFeatures.XML).

Usage: stress_sd.py <image> [--song base|synths16|drive|automation|clipping] [--limiter] [--guard] [--xml-out F]
                    [--seed N]

Songs:
- base: make_sd.py's default song (8 synths, the kit, the audio track, reverb, sidechain, drone; about 90 % CPU of
  demand).
- synths16: 16 synth tracks: the 8 and a copy of each an octave higher (names with a 2: PADA2 ...), about twice the
  synths' load. (make_sd.py --synths only takes the first N of its 8.)
- drive: every LPF (synths, kit rows, kit, audio track, song) in the drive mode (24dBDrive, as make_sd.py --lpf-mode).
- automation: the base song with dense automation in the firmware's own format (AutoParam::writeToFile(): "0x", the
  value now, then per node its value and its position, 8 hex digits each, bit 31 of the position = interpolated): a
  node every 16th note (24 ticks) on every LPF and HPF frequency, resonance and morph, EQ bass and treble, and the
  volume and pan of every synth (the clip's soundParams), every kit row (the note row's soundParams), the kit and
  the audio track (their clips' params) and the song (songParams). The nodes jump (not interpolated) between the
  extremes (-2^31, 2^31 - 1) and random values, a third each, seeded (--seed). In session mode the firmware plays no
  song-level automation (Session: only while recording to the arrangement), so audio_stress_emu.py --song-storm writes
  the song's values between windows in the same pattern.
- clipping: the base song pushed into clipping: the song's volume and every synth's, kit row's, kit's and the audio
  track's volume at the knob's maximum.
--limiter / --guard: CommunityFeatures.XML with outputLimiter / filterCrossingGuard 1 (mastertune v18; builds without
the settings ignore them). Without either, no file: the defaults (both off).
"""
import argparse
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "song"))
import make_sd  # noqa: E402

STEP = 24  # ticks per 16th note (96 per quarter)
MIN, MAX = -2 ** 31, 2 ** 31 - 1


def automation(length, rng):
    """An AutoParam value string for a clip of `length` ticks: a node every 16th note, min / max / random."""
    values = []
    for _ in range(0, length, STEP):
        kind = rng.integers(3)
        values.append(MIN if kind == 0 else MAX if kind == 1 else int(rng.integers(MIN, MAX, endpoint=True)))
    h = lambda v: f"{v & 0xFFFFFFFF:08X}"  # noqa: E731
    return "0x" + h(values[0]) + "".join(h(v) + h(pos) for v, pos in zip(values, range(0, length, STEP)))


def automate_attrs(tag_text, names, length, rng):
    """In an opening tag's text, the attributes `names` automated (added where missing)."""
    for name in names:
        value = automation(length, rng)
        tag_text, n = re.subn(rf'(\s{name}=")[^"]*(")', lambda m: m.group(1) + value + m.group(2), tag_text, count=1)
        if not n:
            end = tag_text.rfind("/>") if tag_text.rstrip().endswith("/>") else tag_text.rfind(">")
            tag_text = tag_text[:end].rstrip() + f' {name}="{value}"' + tag_text[end:]
    return tag_text


SOUND_ATTRS = ["lpfFrequency", "lpfResonance", "lpfMorph", "hpfFrequency", "hpfResonance", "hpfMorph", "volume", "pan"]
GLOBAL_ATTRS = ["volume", "pan"]
FILTER_ATTRS = ["frequency", "resonance", "morph"]
EQ_ATTRS = ["bass", "treble"]


def automate_params_block(block, top_tag, top_attrs, length, rng):
    """A <soundParams>/<kitParams>/<params>/<songParams> block: its own attributes, its <lpf>, <hpf> and
    <equalizer> children."""
    block = re.sub(rf"<{top_tag}\b[^>]*>", lambda m: automate_attrs(m.group(0), top_attrs, length, rng), block, count=1)
    for child, attrs in (("lpf", FILTER_ATTRS), ("hpf", FILTER_ATTRS), ("equalizer", EQ_ATTRS)):
        block = re.sub(rf"<{child}\b[^>]*/>", lambda m: automate_attrs(m.group(0), attrs, length, rng), block)
    return block


def automate(xml, rng, song_length=32 * 4 * 96):
    """The automation song (see the docstring)."""
    def clip(m):
        text = m.group(0)
        length = int(re.search(r'\blength="(\d+)"', text).group(1))
        text = re.sub(r"<soundParams\b.*?</soundParams>",
                      lambda b: automate_params_block(b.group(0), "soundParams", SOUND_ATTRS, length, rng), text,
                      flags=re.S)
        text = re.sub(r"<kitParams\b.*?</kitParams>",
                      lambda b: automate_params_block(b.group(0), "kitParams", GLOBAL_ATTRS, length, rng), text,
                      flags=re.S)
        text = re.sub(r"<params\b.*?</params>",
                      lambda b: automate_params_block(b.group(0), "params", GLOBAL_ATTRS, length, rng), text,
                      flags=re.S)
        return text
    xml = re.sub(r"<(instrumentClip|audioClip)\b.*?</\1>", clip, xml, flags=re.S)
    xml = re.sub(r"<songParams\b.*?</songParams>",
                 lambda b: automate_params_block(b.group(0), "songParams", GLOBAL_ATTRS, song_length, rng), xml,
                 flags=re.S)
    return xml


def synths16():
    """make_sd.synths() and a copy of each an octave higher, named with a 2."""
    base = make_sd.synths()
    out = list(base)
    for instrument, clip in base:
        name = re.search(r'presetName="([^"]+)"', instrument).group(1)
        new = name + "2"
        instrument = instrument.replace(f'presetName="{name}"', f'presetName="{new}"')
        clip = clip.replace(f'instrumentPresetName="{name}"', f'instrumentPresetName="{new}"')
        clip = re.sub(r'(<noteRow y=")(\d+)"', lambda m: f'{m.group(1)}{int(m.group(2)) + 12}"', clip)
        out.append((instrument, clip))
    return out


def clipping(xml):
    full = make_sd.knob(50)
    xml = re.sub(r'(<songParams\b[^>]*?\svolume=")[^"]*"', rf'\g<1>{full}"', xml, flags=re.S)
    for tag in ("soundParams", "kitParams", "params"):
        xml = re.sub(rf'(<{tag}\b[^>]*?\svolume=")[^"]*"', rf'\g<1>{full}"', xml, flags=re.S)
    return xml


def community_features(limiter, guard):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<runtimeFeatureSettings>"]
    if limiter:
        lines.append('\t<setting name="outputLimiter" value="1" />')
    if guard:
        lines.append('\t<setting name="filterCrossingGuard" value="1" />')
    lines.append("</runtimeFeatureSettings>")
    return ("\n".join(lines) + "\n").encode()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("image")
    ap.add_argument("--song", default="base", choices=["base", "synths16", "drive", "automation", "clipping"])
    ap.add_argument("--limiter", action="store_true", help="Community features: output limiter on")
    ap.add_argument("--guard", action="store_true", help="Community features: filter crossing guard on")
    ap.add_argument("--xml-out")
    ap.add_argument("--seed", type=int, default=1, help="the automation's random values")
    a = ap.parse_args()
    files, lengths = make_sd.samples()
    if a.song == "synths16":
        original = make_sd.synths
        make_sd.synths = synths16
        xml = make_sd.song_xml(lengths, 1, 16)
        make_sd.synths = original
    else:
        xml = make_sd.song_xml(lengths, 1, 8)
    if a.song == "drive":
        xml = xml.replace('lpfMode="24dB"', 'lpfMode="24dBDrive"')
    elif a.song == "automation":
        xml = automate(xml, np.random.default_rng(a.seed))
    elif a.song == "clipping":
        xml = clipping(xml)
    files["SONGS/DEFAULT.XML"] = xml.encode()
    if a.limiter or a.guard:
        files["CommunityFeatures.XML"] = community_features(a.limiter, a.guard)
    if a.xml_out:
        open(a.xml_out, "w").write(xml)
    make_sd.fat32.build(a.image, files)
    print(f"{a.image}: song {a.song}, {len(xml):,} bytes of XML, limiter {int(a.limiter)}, guard {int(a.guard)}")


if __name__ == "__main__":
    main()
