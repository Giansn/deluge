#!/usr/bin/env python3
"""SD card images for sdload_emu.py: big cards, many files, long names, big samples (tests/song/fat32.py's options).
Only what the firmware can read at the start of a sample is written (the headers and the first part of the audio);
the rest of every big file is sparse (reads as zeros), so gigabytes of card take a few MB of disk.

Usage: make_bigsd.py <image> <kind> [--fsinfo valid|invalid|stale] [--fragment K]
  small  the song tests' card (make_sd.py's song: 8 synths, a kit, the audio loop) on a 2 GB card: the reference
  bigidle  the song of the earlier idle measurement (64 synths: make_sd.py's 8 synths 8 times, 4 copies of its kit,
         the audio loop) on the 2 GB card: the reference for s2 without its 200 samples and full card
  s1     the same song on a 32 GB card (1,048,576 clusters of 32 KB, a FAT of 4 MB = 8,193 sectors per copy) holding
         20 GB of other files (5,120 of 4 MB in 52 folders SAMPLES/ARCHIVE00..51, nothing written): the used clusters
         are contiguous from the FAT's start, the first free cluster is 655,466; --fsinfo: the FSInfo sector as a
         computer may leave it (fat32.build())
  s2     a big project on that full card: the big song of the idle test (64 synths: make_sd.py's 8 synths 8 times, 4
         copies of its kit, the audio loop) plus 200 samples: 170 kit rows (10 kits of 17 rows, 0.5 MB mono samples,
         each row a note per bar) and 30 audio tracks (long stereo samples of 80 s, 14.1 MB, 40-bar clips, i.e. no time
         stretching). The 200 samples are in SAMPLES/BIG among its 3,000 files with long names (~35 characters: 3
         long-name entries + 1 short each; 12,002 entries, 751 sectors in 12 clusters): every 15th in the folder's
         order (by name, as fat32.build() writes it), so they are spread evenly over the folder. Other files: 16 GB
         (4,096 of 4 MB in 41 folders). --fragment K: the 200 used samples' clusters interleaved in runs of K clusters
  s4     streaming: 1 of make_sd.py's synths (PADA), its kit and loop, plus 16 audio tracks playing long stereo samples
         (the first 2 MB of each is real audio, then zeros) from SAMPLES/BIG as in s2 (16 of s2's 30, spread over the
         folder), on the 32 GB card with 4 GB of other files (1,024 in 11 folders)
  s5     the song browser: SONGS with 1,200 songs (SONG001..SONG400, each with versions A and B; every 10th
         with a long name instead, e.g. "SONG 012 final mix B.XML"): each begins as the firmware writes a song (the
         XML header with previewNumPads and the preview the browser draws, then the startup song's attributes; the
         first 4 KB written, 39 KB each), plus the startup song
Writes <image> and <image>.json (fat32.build()'s layout: where the FAT, the directories and the files are).
"""
import argparse
import json
import math
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "../song"))
import fat32  # noqa: E402
import make_sd  # noqa: E402

SR = 44100
CARD_32GB = 1 << 20  # Clusters of 32 KB
MB = 1 << 20
LONG_FRAMES = 40 * 2 * SR  # 40 bars at 120 BPM: 80 s
SHORT_BYTES = MB // 2
BIG_DIR = "SAMPLES/BIG"


def wav_header(frames, channels):
    data_bytes = frames * 2 * channels
    fmt = struct.pack("<HHIIHH", 1, channels, SR, SR * 2 * channels, 2 * channels, 16)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + data_bytes) + b"WAVE" + b"fmt "
            + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", data_bytes))


def audio(frames, channels, seed):
    """A quiet chord with some noise, 16 bit."""
    rng = np.random.default_rng(seed)
    t = np.arange(frames) / SR
    x = sum(0.12 * np.sin(2 * math.pi * f * (1 + seed * 0.01) * t) for f in (110, 165, 220)) \
        + 0.03 * rng.uniform(-1, 1, frames)
    x = np.stack([x] * channels, axis=1) if channels == 2 else x
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def sample_file(frames, channels, seed, real_bytes):
    """(size, parts) for fat32.build(): the header and the first real_bytes of audio, the rest sparse."""
    head = wav_header(frames, channels)
    n = min(frames, real_bytes // (2 * channels))
    return (len(head) + frames * 2 * channels, [(0, head + audio(n, channels, seed))])


def big_dir_names():
    """3,000 long names in SAMPLES/BIG."""
    kinds = ["Drum Hit", "Texture", "Vocal Chop", "Field Rec", "Synth Stab", "Bass Loop"]
    return [f"{kinds[i % 6]} {i:04d} Ambient Room Take.wav" for i in range(3000)]


def used_samples(names):
    """The 200 of them the song uses, in the folder's order: every 15th entry as fat32.build() writes the folder
    (sorted by name), so they are spread evenly over it. 30 of them (spread too) for the audio tracks, 170 for the
    kit rows."""
    order = sorted(range(len(names)), key=lambda i: names[i])
    used = order[7::15]
    long_ones = [used[(2 * k + 1) * len(used) // 60] for k in range(30)]
    return used, long_ones, [i for i in used if i not in long_ones]


def song_file(xml, n):
    """A song as the firmware writes it (Song::writeToFile()): previewNumPads and the preview's 8 rows of 18 pads
    (RGB in hex) after the versions, then the rest of xml."""
    colours = bytes((n * 37 + k * (5 + n % 7)) % 256 for k in range(8 * 18 * 3)).hex().upper()
    at = xml.index('earliestCompatibleFirmware="4.1.0-alpha"') + len('earliestCompatibleFirmware="4.1.0-alpha"')
    return xml[:at] + f'\n\tpreviewNumPads="144"\n\tpreview="{colours}"' + xml[at:]


def archive_fillers(gigabytes):
    n = gigabytes * 256  # 4 MB each
    return [(f"SAMPLES/ARCHIVE{i // 100:02d}/ARC{i:05d}.WAV", 4 * MB) for i in range(n)]


def rename(part, old, new):
    return tuple(p.replace(f'presetName="{old}"', f'presetName="{new}"')
                 .replace(f'instrumentPresetName="{old}"', f'instrumentPresetName="{new}"') for p in part)


def big_synths(copies):
    parts = []
    for c in range(copies):
        for p in make_sd.synths():
            name = re.search(r'presetName="([^"]+)"', p[0]).group(1)
            parts.append(rename(p, name, f"{name[:4]}{c}"))
    return parts


def sample_kit(name, rows):
    """A kit like make_sd.kit(), its rows playing the given samples: rows = [(path, frames)], one note per bar each
    (at a different 16th per row)."""
    saved = make_sd.KIT_ROWS
    make_sd.KIT_ROWS = [(f"R{j:02d}", [j % 16], 100, 0) for j in range(len(rows))]
    try:
        inst, clip = make_sd.kit({f"R{j:02d}": frames for j, (_, frames) in enumerate(rows)})
    finally:
        make_sd.KIT_ROWS = saved
    for j, (path, _) in enumerate(rows):
        inst = inst.replace(f'fileName="SAMPLES/R{j:02d}.WAV"', f'fileName="{path}"')
    return rename((inst, clip), "KIT", name)


def audio_tracks(tracks, playing=True):
    """Audio tracks with 40-bar clips of long samples (the sample's length: no time stretching)."""
    out, clips = "", ""
    for n, path in enumerate(tracks):
        name = f"AUD{n:02d}"
        a = make_sd.attrs(dict(name=name, inputChannel="none", activeModFunction=0, lpfMode="24dB", hpfMode="HPLadder",
                               filterRoute="H2L", modFXType="none"), 3)
        out += f"\t\t<audioTrack{a}>\n"
        out += '\t\t\t<delay pingPong="1" analog="0" syncLevel="7" syncType="0" />\n'
        out += '\t\t\t<sidechain attack="327244" release="936" syncLevel="7" syncType="0" />\n'
        out += '\t\t\t<audioCompressor attack="83886080" release="83886080" thresh="0" ratio="1073741824" ' \
               'compHPF="0" compBlend="2147483647" />\n'
        out += "\t\t</audioTrack>\n"
        c = make_sd.attrs(dict(trackName=name, filePath=path, startSamplePos=0, endSamplePos=LONG_FRAMES,
                               pitchSpeedIndependent=1, attack=0, priority=1, overdubsShouldCloneAudioTrack=1,
                               isPlaying=int(playing), isSoloing=0, isArmedForRecording=0, length=40 * make_sd.BAR,
                               colourOffset=0, section=0), 3)
        clips += f"\t\t<audioClip{c}>\n"
        clips += make_sd.global_params_block("params", dict(make_sd.KIT_PARAMS, volume=make_sd.knob(10),
                                                           reverbAmount=make_sd.knob(0)), 3)
        clips += "\t\t</audioClip>\n"
    return out, clips


def song(synth_parts, kits, extra_tracks):
    """make_sd.song_xml() with these synths, kits (list of (instrument, clip)) and audio tracks after its loop."""
    orig = make_sd.synths, make_sd.kit, make_sd.audio_track
    loop = make_sd.audio_track
    make_sd.synths = lambda: synth_parts
    make_sd.kit = lambda lengths: ("".join(k[0] for k in kits), "".join(k[1] for k in kits))
    make_sd.audio_track = lambda lengths: tuple(a + b for a, b in zip(loop(lengths), extra_tracks))
    try:
        files, lengths = make_sd.samples()
        files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, len(synth_parts)).encode()
    finally:
        make_sd.synths, make_sd.kit, make_sd.audio_track = orig
    return files, lengths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("kind", choices=["small", "bigidle", "s1", "s2", "s4", "s5"])
    ap.add_argument("--fsinfo", default="valid", choices=["valid", "invalid", "stale"])
    ap.add_argument("--fragment", type=int, default=0)
    args = ap.parse_args()
    opts = dict(fsinfo=args.fsinfo)
    notes = {}
    if args.kind in ("small", "s1"):
        files, lengths = make_sd.samples()
        files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1).encode()
        if args.kind == "s1":
            opts.update(clusters=CARD_32GB, fillers=archive_fillers(20))
    elif args.kind == "bigidle":
        files0, lengths0 = make_sd.samples()
        one = make_sd.kit(lengths0)
        files, lengths = song(big_synths(8), [rename(one, "KIT", f"KIT{c}") for c in range(4)], ("", ""))
        notes = dict(synths=64, kits=4)
    elif args.kind in ("s2", "s4"):
        names = big_dir_names()
        used, long_ones, short_ones = used_samples(names)  # 200: 30 + 170
        big = {}
        if args.kind == "s2":
            for i in long_ones:
                big[f"{BIG_DIR}/{names[i]}"] = sample_file(LONG_FRAMES, 2, i, 64 * 1024)
            for i in short_ones:
                big[f"{BIG_DIR}/{names[i]}"] = sample_file(SHORT_BYTES // 2 - 22, 1, i, 64 * 1024)
            files0, lengths0 = make_sd.samples()
            one = make_sd.kit(lengths0)
            kit_parts = [rename(one, "KIT", f"KIT{c}") for c in range(4)]
            shorts = [(f"{BIG_DIR}/{names[i]}", SHORT_BYTES // 2 - 22) for i in short_ones]
            kit_parts += [sample_kit(f"BKIT{k}", shorts[k * 17:(k + 1) * 17]) for k in range(10)]
            tracks = audio_tracks([f"{BIG_DIR}/{names[i]}" for i in long_ones])
            files, lengths = song(big_synths(8), kit_parts, tracks)
            notes = dict(synths=64, kits=14, kit_rows_big=len(shorts), audio_tracks_big=len(long_ones))
        else:
            streams = [long_ones[k * len(long_ones) // 16] for k in range(16)]
            for i in streams:
                big[f"{BIG_DIR}/{names[i]}"] = sample_file(LONG_FRAMES, 2, i, 2 * MB)
            files0, lengths0 = make_sd.samples()
            tracks = audio_tracks([f"{BIG_DIR}/{names[i]}" for i in streams])
            files, lengths = song(make_sd.synths()[:1], [make_sd.kit(lengths0)], tracks)
            notes = dict(synths=1, kits=1, audio_tracks_big=len(streams))
        files.update(big)
        fillers = [(f"{BIG_DIR}/{n}", (64 + 37 * i % 2000) * 1024) for i, n in enumerate(names)
                   if f"{BIG_DIR}/{n}" not in big]
        opts.update(clusters=CARD_32GB, fillers=fillers + archive_fillers(16 if args.kind == "s2" else 4))
        if args.fragment:
            opts["fragment"] = (sorted(big), args.fragment)
    else:  # s5
        files, lengths = make_sd.samples()
        xml = make_sd.song_xml(lengths, 1, 1)
        files["SONGS/DEFAULT.XML"] = xml.encode()
        for n in range(1, 401):
            for v in ("", "A", "B"):
                name = f"SONG{n:03d}{v}.XML" if n % 10 else f"SONG {n:03d} final mix{' ' + v if v else ''}.XML"
                data = song_file(xml, 3 * n + len(v)).encode()
                files[f"SONGS/{name}"] = (len(data), [(0, data[:4096])])
    layout = fat32.build(args.image, files, **opts)
    folders = {d: dict(files=layout["dir_files"][d], entries=n, sectors=-(-n * 32 // 512))
               for d, n in layout["dir_entries"].items() if n >= 256}
    layout["notes"] = dict(notes, kind=args.kind, fsinfo=args.fsinfo, fragment=args.fragment, folders=folders,
                           files_with_data=len(files),
                           real_bytes=sum(len(d) if isinstance(d, bytes) else sum(len(p) for _, p in d[1])
                                          for d in files.values()),
                           file_bytes=sum(fat32.content_size(d) for d in files.values()))
    json.dump(layout, open(args.image + ".json", "w"))
    print(f"{args.image}: {args.kind}, {layout['num_clusters']:,} clusters ({layout['num_clusters'] * 32 / 1024 / 1024:.1f}"
          f" GB), {layout['used_clusters']:,} used, {len(files)} files + {layout['fillers']} fillers, "
          f"{layout['notes']['real_bytes'] / MB:.1f} MB written, song {len(files['SONGS/DEFAULT.XML']):,} bytes; FAT "
          f"{layout['fat_sectors']:,} sectors per copy, first free cluster {layout['first_free_cluster']:,}"
          + "".join(f"; {d}: {f['files']:,} files, {f['entries']:,} entries, {f['sectors']:,} sectors"
                    for d, f in folders.items()))


if __name__ == "__main__":
    main()
