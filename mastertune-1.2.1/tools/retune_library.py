#!/usr/bin/env python3
"""Retunes a Deluge sample library once to the master tune, so its samples play natively (mastertune firmware).

At a master tune other than 440 Hz the firmware shifts every sample voice by the tuning, even an untransposed drum:
the voice is interpolated (sinc) instead of read natively, about six times the CPU per voice. A WAV file with the
12-byte "mtun" chunk counts as audio in that tuning (see the README, "Aufnahmen werden nie doppelt gestimmt") and plays
unshifted when the master tune is the same. This tool makes a converted copy of the card, once:

- Audio: every WAV and AIFF under SAMPLES/ and every file the songs, kits and synths reference, resampled once from
  its own tuning (its mtun chunk, or 440 Hz without one) to the target tuning and to 44.1 kHz in the same pass
  (soxr, very high quality, the ratio as an exact fraction: 440/432 = 55/54), with the mtun chunk. Bit depth,
  channels and format stay (float stays float). "smpl" (loops, sample period), "cue " and "fact" are scaled; other
  chunks are kept. AIFF files become WAV (the firmware reads mtun only from WAV) and the XML paths follow.
- Peaks: resampling can put peaks over full scale (a file normalized to 0 dBFS). Nothing is clipped: such an integer
  file is written as 32-bit float instead (unclipped in the file; the firmware reads it, but limits float samples to
  full scale as it loads them), or with --no-float at a level just low enough (the report gives the dB).
- XML: in every song, kit and synth (SONGS/, KITS/, SYNTHS/) the sample positions (start, end, loop points, per
  multisample range too, the early-2016 millisecond positions, the audio clips' start and end) scaled to match.
  transpose and cents stay: they are relative to the file in its own tuning.
- Length kept: audio clips and sample sources with time stretch on ("pitch/speed independent") or the STRETCH repeat
  mode keep their length on the Deluge whatever the tuning, so resampling would make them longer (+1.85 % for 432 Hz)
  and time-stretch again. They get a pitch shift that keeps the duration instead (Rubber Band R3, pip install
  pylibrb); without pylibrb they stay as they are (the firmware keeps shifting them at run time). A file that serves
  both ways gets a second copy, <name>_ts.wav, for the length-keeping uses.
- Left alone: wavetables (files with a "clm " chunk, files a wavetable oscillator uses, and unreferenced mono files
  whose length is a multiple of 2048 samples, which may be single-cycle wavetables), files already in the target
  tuning at 44.1 kHz, files the firmware can't read (AIFC, RF64, other formats), and everything else on the card
  (copied unchanged).

At master tune 440 Hz (or any other) the converted card still plays in tune: the firmware shifts by the difference
between the master tune and the file's mtun. A loop shorter than about 0.2 s gets a length rounded to whole samples,
so its repetition rate (the pitch of a single-cycle loop) can be off by more than 0.1 cents: the report lists them.

It never writes into the card folder. Usage (Windows: py instead of python3):
  python3 retune_library.py --card CARD_COPY --out NEW_CARD --tuning 432 [--rate 44100] [--dry-run] [--jobs N]
                            [--no-float]
  --rate 0 keeps each file's sample rate. The report goes to the console and, unless --dry-run, to
  NEW_CARD/RETUNE_REPORT.txt (and .json).

Needs: pip install numpy soxr (and pylibrb for the length-keeping pitch shift).
"""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import time
import unicodedata
import zlib
from fractions import Fraction

import numpy as np

MIN_TENTHS, MAX_TENTHS, DEFAULT_TENTHS = 4153, 4662, 4400  # MasterTune::isValid(), kDefaultTenthsHz
AUDIO_EXTENSIONS = (".wav", ".aif", ".aiff")
XML_FOLDERS = ("SONGS", "KITS", "SYNTHS")
LOOP_WARN_CENTS = 0.1
SUFFIX_TS = "_ts"  # The copy for length-keeping uses of a file that is also used as a plain sample
BLOCK = 1 << 17  # Frames converted at a time (about 3 s): a job's memory doesn't grow with the file's length
TMP_SUFFIX = ".retune-tmp"  # A file being written; renamed when complete
PROGRESS = "RETUNE_PROGRESS.jsonl"  # In the new card's folder while converting: the files finished (for --resume)
MEMORY_SHARE = 0.5  # Of the memory available at the start, the conversions use at most this share (--max-memory)
MEMORY_UNKNOWN = 2 << 30  # Assumed available where it can't be read

try:
    import soxr
except ImportError:  # Checked in main(), so that --help works without it
    soxr = None
try:
    import pylibrb
except ImportError:
    pylibrb = None


# --------------------------------------------------------------------------------------------------------------------
# Audio files: reading and writing WAV, reading AIFF


class AudioInfo:
    """What a WAV or AIFF file holds. kind: "wav", "aiff" or None (not convertible, see error)."""

    def __init__(self):
        self.kind = None
        self.error = None
        self.channels = self.rate = self.bits = self.frames = 0
        self.float = False
        self.mtun = None  # Valid mtun value (tenths of Hz), as the firmware reads it
        self.clm = False  # Serum wavetable chunk
        self.chunks = []  # WAV: (id, bytes) in file order, "data" with b"" as a placeholder; AIFF: its chunks
        self.data_pos = self.data_len = None  # Where the audio data is in the file
        self.file_size = 0
        self.block_align = 0
        self.big_endian = False
        self.aiff_note = None  # AIFF INST: (baseNote, detune, lowNote, highNote, lowVel, highVel, gain)
        self.aiff_loop = None  # AIFF: (start, end) as the firmware reads the sustain loop

    @property
    def tuning(self):
        return self.mtun if self.mtun is not None else DEFAULT_TENTHS


def is_valid_tuning(tenths):
    return MIN_TENTHS <= tenths <= MAX_TENTHS


def read_audio_info(fh):
    """The headers of an open file: the chunks are read and the audio data skipped (Source reads it block by block).
    So that neither planning a whole card nor converting a long file holds its audio in memory."""
    info = AudioInfo()
    fh.seek(0, os.SEEK_END)
    info.file_size = fh.tell()
    fh.seek(0)
    head = fh.read(12)
    if len(head) < 12:
        info.error = "too short"
        return info
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        _read_wav(fh, info)
    elif head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        _read_aiff(fh, info, head[8:12])
    else:
        info.error = "RF64 (not supported by the firmware)" if head[:4] in (b"RF64", b"BW64") else \
            "not a WAV or AIFF file"
    return info


def _chunks(fh, info, size_format, audio_id, audio_head):
    """(id, position of the body, size present in the file, body) per chunk; of the audio chunk only its first
    audio_head bytes."""
    pos = 12
    while pos + 8 <= info.file_size:
        fh.seek(pos)
        h = fh.read(8)
        cid, size = h[:4], struct.unpack(size_format, h[4:])[0]
        present = max(0, min(size, info.file_size - pos - 8))
        yield cid, pos + 8, present, fh.read(min(present, audio_head) if cid == audio_id else present)
        pos += 8 + size + (size & 1)


def _read_wav(fh, info):
    fmt = None
    for cid, at, size, body in _chunks(fh, info, "<I", b"data", 0):
        if cid == b"data":
            info.data_pos, info.data_len = at, size
            info.chunks.append((cid, b""))
        else:
            info.chunks.append((cid, body))
            if cid == b"fmt ":
                fmt = body
            elif cid == b"mtun" and len(body) >= 4:
                value = struct.unpack_from("<i", body)[0]
                info.mtun = value if is_valid_tuning(value) else info.mtun
            elif cid == b"clm " and body[:3] == b"<!>":
                info.clm = True
    if fmt is None or info.data_pos is None or len(fmt) < 16:
        info.error = "no fmt or data chunk"
        return info
    tag, info.channels, info.rate, _, info.block_align, info.bits = struct.unpack_from("<HHIIHH", fmt)
    # What the firmware can't read (AudioFile::loadFile(): format 1, or 3 with 32 bits; 1 or 2 channels) stays as it is:
    # converting it would gain nothing on the Deluge
    if tag == 0xFFFE:
        info.error = "WAVE_FORMAT_EXTENSIBLE: the Deluge can't read it"
        return info
    if info.channels not in (1, 2):
        info.error = f"{info.channels} channels: the Deluge reads only mono and stereo"
        return info
    if tag == 3 and info.bits != 32:
        info.error = f"{info.bits}-bit float: the Deluge reads only 32-bit float"
        return info
    info.float = tag == 3
    if tag not in (1, 3) or info.channels < 1 or info.block_align != info.channels * ((info.bits + 7) // 8):
        info.error = f"format {tag}, {info.bits} bits (only PCM and float)"
        return info
    if (info.float and info.bits not in (32, 64)) or (not info.float and info.bits not in (8, 16, 24, 32)):
        info.error = f"{info.bits}-bit {'float' if info.float else 'PCM'}"
        return info
    info.frames = info.data_len // info.block_align
    info.kind = "wav"
    return info


def _extended_to_float(b):
    exponent, mantissa = struct.unpack(">HQ", b)
    sign = -1 if exponent & 0x8000 else 1
    exponent &= 0x7FFF
    return 0.0 if exponent == 0 and mantissa == 0 else sign * mantissa * 2.0 ** (exponent - 16383 - 63)


def _read_aiff(fh, info, form):
    if form == b"AIFC":
        info.error = "AIFC (the firmware reads only plain AIFF)"
        return info
    comm, ssnd, markers, inst = None, None, {}, None
    for cid, at, size, body in _chunks(fh, info, ">I", b"SSND", 8):
        info.chunks.append((cid, b"" if cid == b"SSND" else body))
        if cid == b"COMM":
            comm = body
        elif cid == b"SSND":
            ssnd = (body, at, size)
        elif cid == b"MARK" and len(body) >= 2:
            n, p = struct.unpack_from(">H", body)[0], 2
            for _ in range(n):
                if p + 7 > len(body):
                    break
                marker_id, position, name_len = struct.unpack_from(">HIB", body, p)
                markers[marker_id] = position
                p += 7 + name_len + ((name_len + 1) & 1)  # pstring padded to an even total length
        elif cid == b"INST" and len(body) >= 14:
            inst = body
    if comm is None or ssnd is None or len(comm) != 18 or len(ssnd[0]) < 8:
        info.error = "no COMM or SSND chunk"
        return info
    info.channels, info.frames, info.bits = struct.unpack_from(">hIh", comm)
    info.rate = int(round(_extended_to_float(comm[8:18])))
    if info.bits not in (8, 16, 24, 32) or info.channels < 1:
        info.error = f"{info.bits}-bit AIFF"
        return info
    info.block_align = info.channels * info.bits // 8
    offset = struct.unpack_from(">I", ssnd[0])[0]
    info.data_pos, info.data_len = ssnd[1] + 8 + offset, max(0, ssnd[2] - 8 - offset)
    info.frames = min(info.frames, info.data_len // info.block_align)
    info.big_endian = True
    if inst is not None:
        info.aiff_note = tuple(struct.unpack_from(">BbBBBBh", inst))
        # As AudioFile::loadFile(): the sustain loop's markers, whatever its play mode
        begin_id, end_id = struct.unpack_from(">HH", inst, 10)
        info.aiff_loop = (markers.get(begin_id, 0), markers.get(end_id, 0))
    info.kind = "aiff"
    return info


def decode(raw, info):
    """Audio data in the file's format (whole frames) as float64, shape (frames, channels), full scale 1.0."""
    bits, e = info.bits, ">" if info.big_endian else "<"
    if info.float:
        x = np.frombuffer(raw, f"{e}f{bits // 8}").astype(np.float64)
    elif bits == 8:
        x = np.frombuffer(raw, np.int8 if info.big_endian else np.uint8).astype(np.float64)
        x = x / 128 if info.big_endian else (x - 128) / 128
    elif bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        if info.big_endian:
            b = b[:, ::-1]
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (v - ((v & 0x800000) << 1)).astype(np.float64) / 2 ** 23
    else:
        x = np.frombuffer(raw, f"{e}i{bits // 8}").astype(np.float64) / 2 ** (bits - 1)
    return x.reshape(-1, info.channels)


def encode(x, bits, is_float):
    """Little-endian WAV data from float64 (frames, channels); returns (bytes, clipped samples). The callers write no
    clipped samples: see over_full_scale()."""
    if is_float:
        return x.astype(f"<f{bits // 8}").tobytes(), 0
    scale = 2 ** (bits - 1)
    y = np.round(x * scale)
    clipped = int(np.count_nonzero((y > scale - 1) | (y < -scale)))
    y = np.clip(y, -scale, scale - 1).astype(np.int64)
    if bits == 8:
        return (y + 128).astype(np.uint8).tobytes(), clipped
    if bits == 24:
        v = (y.reshape(-1) & 0xFFFFFF).astype(np.uint32)
        return np.stack([v & 0xFF, (v >> 8) & 0xFF, v >> 16], axis=1).astype(np.uint8).tobytes(), clipped
    return y.astype(f"<i{bits // 8}").tobytes(), clipped


def over_full_scale(x, bits):
    """How many samples an integer format of bits would clip (as encode() rounds)."""
    scale = 2 ** (bits - 1)
    y = np.round(x * scale)
    return int(np.count_nonzero((y > scale - 1) | (y < -scale)))


def headroom_gain(hi, lo, bits):
    """The gain (<= 1) that brings audio whose highest sample is hi and lowest lo just into the integer format of
    bits: the positive peak to the largest code (scale - 1), the negative one to -scale. Only these two samples count:
    scaling by a positive gain and rounding keep the order of the samples, so no other one can go over first (the
    same gain as checking every sample, without holding them)."""
    scale = 2 ** (bits - 1)
    g = 1.0
    if hi > 0:
        g = min(g, (scale - 1) / (scale * hi))
    if lo < 0:
        g = min(g, 1 / -lo)
    peaks = np.array([hi, lo])
    while over_full_scale(peaks * g, bits):  # Only float rounding could leave one
        g *= 1 - 2 ** -40
    return g


def db(v):
    return 20 * math.log10(v) if v > 0 else -math.inf


def chunk(cid, body):
    return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def mtun_chunk(tenths):
    return chunk(b"mtun", struct.pack("<i", tenths))


# --------------------------------------------------------------------------------------------------------------------
# Positions


def scale_pos(p, f, n_old, n_new):
    """A sample position in the new file: rounded, the file's end stays its end, 0 (unset) stays 0."""
    if p <= 0:
        return p
    if p >= n_old:
        return n_new
    return min(n_new, math.floor(p * f + Fraction(1, 2)))


def scale_span(a, b, f, n_old, n_new):
    """(a, b) with b as a + the scaled length, so that the length (a loop's period) is off by 0.5 samples at most.
    Returns (a', b', error of the length in cents)."""
    a2 = scale_pos(a, f, n_old, n_new)
    if b <= 0:
        return a2, b, 0.0
    if b >= n_old:
        b2 = n_new
    else:
        b2 = min(n_new, a2 + math.floor((b - a) * f + Fraction(1, 2)))
    err = 1200 * math.log2((b2 - a2) / float((b - a) * f)) if b > a and b2 > a2 else 0.0
    return a2, b2, err


def scale_smpl(body, f, n_old, n_new, rate, warnings, where):
    b = bytearray(body)
    if len(b) >= 36:
        struct.pack_into("<I", b, 8, (1000000000 + (rate >> 1)) // rate)  # dwSamplePeriod, as SampleRecorder writes
        loops = min(struct.unpack_from("<I", b, 28)[0], (len(b) - 36) // 24)
        for i in range(loops):
            o = 36 + i * 24
            start, end = struct.unpack_from("<II", b, o + 8)
            s2, e2, err = scale_span(start, end, f, n_old, n_new)
            struct.pack_into("<II", b, o + 8, s2, e2)
            if abs(err) > LOOP_WARN_CENTS:
                warnings.append(f"{where}: smpl loop {start}-{end} ({end - start} samples) -> {s2}-{e2}: its period "
                                f"is {err:+.2f} cents off (rounded to whole samples)")
    return bytes(b)


def scale_cue(body, f, n_old, n_new):
    b = bytearray(body)
    if len(b) >= 4:
        n = min(struct.unpack_from("<I", b)[0], (len(b) - 4) // 24)
        for i in range(n):
            o = 4 + i * 24
            struct.pack_into("<I", b, o + 4, scale_pos(struct.unpack_from("<I", b, o + 4)[0], f, n_old, n_new))
            if b[o + 8:o + 12] == b"data":
                struct.pack_into("<I", b, o + 20, scale_pos(struct.unpack_from("<I", b, o + 20)[0], f, n_old, n_new))
    return bytes(b)


# --------------------------------------------------------------------------------------------------------------------
# Conversion (runs in worker processes)


class Source:
    """A file's audio, read by frame range and decoded (float64, (frames, channels)): never the whole file at once."""

    def __init__(self, fh, info):
        self.fh, self.info, self.n = fh, info, info.frames

    def read(self, a, b):
        ba = self.info.block_align
        self.fh.seek(self.info.data_pos + a * ba)
        return decode(self.fh.read((b - a) * ba), self.info)

    def blocks(self, block):
        for a in range(0, self.n, block):
            yield self.read(a, min(self.n, a + block))

    def periodic(self, a, b):
        """Frames a to b of the audio repeated endlessly both ways (0: its first frame; needs frames)."""
        parts = []
        while a < b:
            i = a % self.n
            k = min(b - a, self.n - i)
            parts.append(self.read(i, i + k))
            a += k
        return parts[0] if len(parts) == 1 else np.concatenate(parts)


def resample(blocks, f_num, f_den, channels, n_new):
    """The audio (blocks) resampled by the exact ratio f_num / f_den, soxr very high quality, block by block: soxr's
    stream gives the same samples as resampling the whole file at once. n_new frames in all (round(n * ratio)),
    padded with silence at the end where soxr gives fewer."""
    rs = soxr.ResampleStream(f_den, f_num, channels, dtype="float64", quality="VHQ")
    sent = 0
    for x in blocks:
        y = rs.resample_chunk(np.ascontiguousarray(x, dtype=np.float64))[:n_new - sent]
        if len(y):
            sent += len(y)
            yield y
    y = rs.resample_chunk(np.zeros((0, channels)), last=True)[:n_new - sent]  # The rest, flushed
    sent += len(y)
    if len(y):
        yield y
    if sent < n_new:
        yield np.zeros((n_new - sent, channels))


RB_PAD, RB_STEP = 16384, 4096  # Rubber Band: the padding on each side, the frames per process() call


def pitch_shift_keep_length(src, rate, scale, loop, block):
    """Rubber Band R3 (finer engine, channels together): pitch times scale, the same length and timing. Its real-time
    mode at a fixed ratio: the offline mode (study pass) lets the timing drift (about 0.13 % for 432/440, events up to
    tens of ms early, caught up at the end). Padded on both sides, so that the edges get whole analysis windows: with
    the file's own other end for a loop (an audio clip or a looping sample: no dip in level at the loop point), with
    silence for a one-shot. Block by block: the input (Rubber Band's preferred start pad of silence, the padding, the
    audio, the padding) goes in RB_STEP frames at a time, its start delay and the padding are dropped from the output,
    which yields the file's n frames in blocks."""
    from pylibrb import Option, RubberBandStretcher
    n, ch = src.n, src.info.channels
    if n == 0:
        return
    options = Option.PROCESS_REALTIME | Option.ENGINE_FINER | Option.CHANNELS_TOGETHER
    stretcher = RubberBandStretcher(int(rate), ch, options, 1.0, float(scale))
    start_pad = stretcher.get_preferred_start_pad()
    stretcher.set_max_process_size(RB_STEP)
    delay = stretcher.get_start_delay()
    total = start_pad + RB_PAD + n + RB_PAD
    skip = delay + RB_PAD  # Output frames before the file's first one
    have = sent = 0

    def padded(a, b):
        """Frames a to b of the input as Rubber Band gets it."""
        k0, k1 = a - start_pad - RB_PAD, b - start_pad - RB_PAD  # As the file's frames
        y = np.zeros((b - a, ch))
        s, e = (max(k0, -RB_PAD), k1) if loop else (max(k0, 0), min(k1, n))
        if s < e:
            y[s - k0:e - k0] = src.periodic(s, e)
        return y

    def take(out):
        """Of the retrieved frames (channels, frames) the file's."""
        nonlocal have, sent
        a, b = max(skip, have), min(skip + n, have + out.shape[1])
        if a < b:
            sent += b - a
            yield out[:, a - have:b - have].T.astype(np.float64)
        have += out.shape[1]

    step_block = max(RB_STEP, block // RB_STEP * RB_STEP)  # The steps stay at multiples of RB_STEP
    for a in range(0, total, step_block):
        b = min(total, a + step_block)
        audio = np.ascontiguousarray(padded(a, b).T, dtype=np.float32)
        for i in range(a, b, RB_STEP):
            stretcher.process(audio[:, i - a:i - a + RB_STEP], final=i + RB_STEP >= total)
            available = stretcher.available()
            if available > 0:
                yield from take(stretcher.retrieve(available))
        del audio
    while have < skip + n:
        available = stretcher.available()
        if available <= 0:
            break
        yield from take(stretcher.retrieve(available))
    if sent < n:
        yield np.zeros((n - sent, ch))


def converted_blocks(src, job):
    """The job's converted audio, float64 blocks of (frames, channels)."""
    blocks = src.blocks(job["block"])
    if job["mode"] == "keep_length" and job["pitch_num"] != job["pitch_den"]:
        blocks = pitch_shift_keep_length(src, src.info.rate, Fraction(job["pitch_num"], job["pitch_den"]), job["loop"],
                                         job["block"])
    if job["f_num"] != job["f_den"]:
        blocks = resample(blocks, job["f_num"], job["f_den"], src.info.channels, job["frames_new"])
    return blocks


def build_wav(info, frames_new, rate_new, data_len, f, mtun, warnings, where, as_float=False):
    """The WAV file around its audio data of data_len bytes: (the bytes before the data, those after it). The
    original's chunks in their order (fmt with the new rate, smpl, cue and fact scaled, mtun replaced and put right
    before data), or for an AIFF a new fmt with its note (inst) and sustain loop (smpl). as_float: the audio is 32-bit
    float instead of the original's integer format (fmt changed, a fact chunk added)."""
    n_old = info.frames
    tag, bits = (3, 32) if as_float else (1, info.bits)
    block_align = info.channels * bits // 8
    out = []
    data_at = None
    if info.kind == "wav":
        has_fact = any(cid == b"fact" for cid, _ in info.chunks)
        for cid, body in info.chunks:
            if cid == b"fmt ":
                b = bytearray(body)
                struct.pack_into("<II", b, 4, rate_new, rate_new * block_align)
                if as_float:
                    struct.pack_into("<H", b, 0, tag)
                    struct.pack_into("<HH", b, 12, block_align, bits)
                out.append(chunk(cid, bytes(b)))
            elif cid == b"mtun":
                continue
            elif cid == b"data":
                if as_float and not has_fact:  # Every format but PCM has one (the firmware skips it)
                    out.append(chunk(b"fact", struct.pack("<I", frames_new)))
                if mtun is not None:
                    out.append(mtun_chunk(mtun))
                data_at = len(out)
            elif cid == b"smpl":
                out.append(chunk(cid, scale_smpl(body, f, n_old, frames_new, rate_new, warnings, where)))
            elif cid == b"cue ":
                out.append(chunk(cid, scale_cue(body, f, n_old, frames_new)))
            elif cid == b"fact" and len(body) >= 4:
                out.append(chunk(cid, struct.pack("<I", frames_new) + body[4:]))
            else:
                out.append(chunk(cid, body))
    else:  # AIFF -> WAV
        out.append(chunk(b"fmt ", struct.pack("<HHIIHH", tag, info.channels, rate_new, rate_new * block_align,
                                               block_align, bits)))
        note = info.aiff_note
        has_note = note is not None and (note[0] or note[1]) and note[0] < 128
        if has_note or (info.aiff_loop and any(info.aiff_loop)):
            unity, fraction = 0, 0
            if has_note:  # The firmware: note - detune / 100, for both AIFF INST and WAV inst
                value = note[0] - note[1] / 100
                unity = int(math.floor(value))
                fraction = int(round((value - unity) * 2 ** 32)) & 0xFFFFFFFF
            loops = []
            if info.aiff_loop and any(info.aiff_loop):
                s, e = info.aiff_loop
                loops = [struct.pack("<6I", 0, 0, s, e, 0, 0)]
            body = struct.pack("<9I", 0, 0, 0, unity, fraction, 0, 0, len(loops), 0) + b"".join(loops)
            out.append(chunk(b"smpl", scale_smpl(body, f, n_old, frames_new, rate_new, warnings, where)))
        if has_note:
            gain = max(-128, min(127, note[6]))
            out.append(chunk(b"inst", struct.pack("<BbbBBBB", note[0], note[1], gain, note[2], note[3], note[4],
                                                  note[5])))
        if as_float:
            out.append(chunk(b"fact", struct.pack("<I", frames_new)))
        if mtun is not None:
            out.append(mtun_chunk(mtun))
        data_at = len(out)
    head, tail = b"".join(out[:data_at]), b"".join(out[data_at:])
    pad = b"\0" if data_len & 1 else b""
    size = 4 + len(head) + 8 + data_len + len(pad) + len(tail)
    if size >= 1 << 32:
        raise RuntimeError(f"the converted file would have {size + 8} bytes: over the 4 GB a WAV file can hold")
    return (b"RIFF" + struct.pack("<I", size) + b"WAVE" + head + b"data" + struct.pack("<I", data_len),
            pad + tail)


def write_wav(path, src, job, as_float, gain):
    """Converts and writes the job's file to path, block by block: returns what it found (the peaks before the gain,
    the samples clipped as written, the warnings, the file's size)."""
    info = src.info
    bits = 32 if as_float else info.bits
    is_float = as_float or info.float
    data_len = job["frames_new"] * info.channels * (bits // 8)
    warnings = []
    head, tail = build_wav(info, job["frames_new"], job["rate_new"], data_len, Fraction(job["f_num"], job["f_den"]),
                           job["mtun"], warnings, job["rel"], as_float)
    hi, lo, clipped, frames = -math.inf, math.inf, 0, 0
    with open(path, "wb") as out:
        out.write(head)
        for y in converted_blocks(src, job):
            if not len(y):
                continue
            frames += len(y)
            hi, lo = max(hi, float(np.max(y))), min(lo, float(np.min(y)))
            data, c = encode(y * gain if gain != 1 else y, bits, is_float)
            clipped += c
            out.write(data)
            del y, data
        if frames != job["frames_new"]:
            raise RuntimeError(f"length {frames}, expected {job['frames_new']}")
        out.write(tail)
        out.flush()
        os.fsync(out.fileno())  # On the disk before it gets its name: a crash leaves no half-written file under it
    return dict(hi=hi, lo=lo, peak=max(hi, -lo) if frames else 0.0, clipped=clipped, warnings=warnings,
                size=len(head) + data_len + len(tail))


def convert_job(job):
    """One output file, written block by block (a few blocks of job["block"] frames in memory, whatever the file's
    length) to a temporary name, renamed when complete. job: dict (see plan()); returns (index, result dict)."""
    t = time.time()
    tmp = job["dst"] + TMP_SUFFIX
    os.makedirs(os.path.dirname(job["dst"]), exist_ok=True)
    try:
        with open(job["src"], "rb") as fh:
            src = Source(fh, read_audio_info(fh))
            info = src.info
            as_float, gain = False, 1.0
            r = write_wav(tmp, src, job, as_float, gain)
            # Peaks over full scale (resampling a file normalized to 0 dBFS): never clipped. An integer file becomes
            # 32-bit float, or (--no-float) just as much quieter as it needs: known only at the end, so such a file is
            # converted and written a second time (the same samples: the conversion is deterministic)
            over = 0 if info.float else r["clipped"]
            if over:
                if job.get("no_float"):
                    gain = headroom_gain(r["hi"], r["lo"], info.bits)
                else:
                    as_float = True
                r2 = write_wav(tmp, src, job, as_float, gain)
                if (r2["hi"], r2["lo"]) != (r["hi"], r["lo"]):
                    raise RuntimeError("the second pass gave other samples")
                if r2["clipped"]:  # Can't happen (see above): never write a clipped file
                    raise RuntimeError(f"{r2['clipped']} samples would be clipped")
                r = dict(r2, peak=r["peak"])
        os.replace(tmp, job["dst"])
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    warnings, peak = r["warnings"], r["peak"]
    if info.float and peak >= 1:
        warnings.append(f"{job['out_rel']}: float, the resampled peak is {db(peak):+.2f} dBFS: kept in the file, "
                        f"but the firmware limits float samples to full scale as it loads them")
    return job["index"], dict(size=r["size"], seconds=time.time() - t, warnings=warnings, peak=peak, over=over,
                              as_float=as_float, gain=gain, gain_db=db(gain) if gain != 1 else None)


# --------------------------------------------------------------------------------------------------------------------
# XML: a small tokenizer that keeps every byte's position, so that edits change only the values


class Node:
    __slots__ = ("name", "attrs", "children", "text", "parent", "start")

    def __init__(self, name, start, parent):
        self.name, self.start, self.parent = name, start, parent
        self.attrs = {}  # name -> (value, start, end)
        self.children = []
        self.text = None  # (value, start, end) of the first non-blank text

    def get(self, name):
        """A value as the firmware's readTagOrAttributeValue() finds it: attribute or child tag. (value, start, end)."""
        if name in self.attrs:
            return self.attrs[name]
        for c in self.children:
            if c.name == name and c.text is not None:
                return c.text
            if c.name == name:
                return ("", c.start, c.start)
        return None

    def child(self, name):
        return next((c for c in self.children if c.name == name), None)

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()


TOKEN = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<![^>]*>|<(/?)([^\s/>]+)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>",
                   re.S)
ATTR = re.compile(r"([^\s=/]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


def parse_xml(text):
    root = Node("#root", 0, None)
    cur, pos = root, 0
    for m in TOKEN.finditer(text):
        if m.start() > pos and cur.text is None and not cur.children:
            s = text[pos:m.start()]
            if s.strip():
                lead = len(s) - len(s.lstrip())
                cur.text = (s.strip(), pos + lead, pos + lead + len(s.strip()))
        pos = m.end()
        if m.group(2) is None:
            continue
        closing, name, rest = m.group(1), m.group(2), m.group(3)
        if closing:
            if cur.name != name:
                raise ValueError(f"</{name}> closes <{cur.name}> at byte {m.start()}")
            cur = cur.parent
            continue
        node = Node(name, m.start(), cur)
        base = m.start(3)
        for a in ATTR.finditer(rest):
            g = 2 if a.group(2) is not None else 3
            node.attrs[a.group(1)] = (a.group(g), base + a.start(g), base + a.end(g))
        cur.children.append(node)
        if not rest.rstrip().endswith("/"):
            cur = node
    if cur is not root:
        raise ValueError(f"<{cur.name}> not closed")
    return root


def as_int(v, default=0):
    try:
        return int(v[0], 0) if v is not None else default
    except ValueError:
        return default


def context_name(node):
    n = node
    while n is not None:
        for key in ("name", "presetName", "trackName"):
            v = n.get(key) if n.name in ("sound", "audioClip", "kit") else None
            if v is not None and v[0]:
                return f"{n.name} {v[0]}"
        n = n.parent
    return "?"


class Ref:
    """A file reference in an XML: its path value and the positions that go with it, as (value, start, end) with the
    character positions in the XML text. No link to the parse tree: the plan keeps only these of a whole card's XML
    (the texts are read again when writing)."""
    __slots__ = ("xml", "path", "use", "context", "positions", "ms", "loop_mode", "looping", "rel", "encoding", "plan")

    def __init__(self, xml, path, use, context):
        self.xml, self.path, self.use, self.context = xml, path, use, context
        self.positions = {}  # name -> (value, start, end)
        self.ms = {}  # early-2016 format: startSeconds, startMilliseconds, endSeconds, endMilliseconds
        self.loop_mode = 0
        self.looping = use == "keep_length"  # Audio clips loop; sources: see add()
        self.rel = self.encoding = self.plan = None


def refs_in_xml(xml_rel, root, warnings):
    """(refs, other paths that look like audio files and aren't understood)."""
    refs, understood = [], set()

    def add(node, holder, zone_owner, use, loop_mode=0):
        path = holder.get("fileName")
        if path is None or not path[0]:
            return
        r = Ref(xml_rel, path, use, context_name(node))
        r.loop_mode = loop_mode
        r.looping = loop_mode in (2, 3)
        zone = zone_owner.child("zone")
        if zone is not None:
            for k in ("startSamplePos", "endSamplePos", "startLoopPos", "endLoopPos"):
                v = zone.get(k)
                if v is not None and v[0]:
                    r.positions[k] = v
            for k in ("startSeconds", "startMilliseconds", "endSeconds", "endMilliseconds"):
                v = zone.get(k)
                if v is not None and v[0]:
                    r.ms[k] = v
        refs.append(r)
        understood.add(path[1])

    for node in root.iter():
        if re.fullmatch(r"osc\d", node.name):
            kind = node.get("type")
            loop_mode = as_int(node.get("loopMode"))
            keep_length = as_int(node.get("timeStretchEnable")) != 0 or loop_mode == 3
            if kind is not None and kind[0] == "wavetable":
                use = "wavetable"
            else:
                use = "keep_length" if keep_length else "resample"
            add(node, node, node, use, loop_mode)
            for group, item in (("sampleRanges", "sampleRange"), ("wavetableRanges", "wavetableRange")):
                g = node.child(group)
                for r in (g.children if g is not None else []):
                    if r.name == item:
                        add(r, r, r, "wavetable" if item == "wavetableRange" or use == "wavetable" else use,
                            loop_mode)
        elif node.name == "sound" and node.get("fileName") is not None:  # The early-2016 format
            loop_mode = as_int(node.get("continuous"))
            add(node, node, node, "keep_length" if loop_mode == 3 else "resample", loop_mode)
        elif node.name == "audioClip":
            path = node.get("filePath")
            if path is not None and path[0]:
                r = Ref(xml_rel, path, "keep_length", context_name(node))
                for k in ("startSamplePos", "endSamplePos"):
                    v = node.get(k)
                    if v is not None and v[0]:
                        r.positions[k] = v
                refs.append(r)
                understood.add(path[1])
    others = []
    for node in root.iter():
        for key in ("fileName", "filePath"):
            v = node.get(key)
            if v is not None and v[1] not in understood and v[0].lower().endswith(AUDIO_EXTENSIONS):
                others.append(v[0])
    return refs, others


# --------------------------------------------------------------------------------------------------------------------
# The plan


# Paths in the XML files: the firmware's FAT has code page 437 and no Unicode API (src/fatfs/ffconf.h: FF_CODE_PAGE
# 437, FF_LFN_UNICODE 0), so the Deluge writes them as CP437 bytes; an XML written on a computer may have UTF-8. The
# XML text is read as UTF-8 with surrogateescape: the bytes that aren't UTF-8 are surrogates, and written back as they
# were.


def path_key(rel):
    """For comparing paths as FAT does: ignoring case (and the Unicode normalization of the computer's names)."""
    return unicodedata.normalize("NFC", rel).lower()


def card_path(value, by_key):
    """The file an XML path names: (path as on the card or, if missing, as named; without a leading slash, the path's
    encoding in the XML: "utf-8" or "cp437"). UTF-8 first, else CP437."""
    raw = value.lstrip("/").encode("utf-8", "surrogateescape")
    cp437 = raw.decode("cp437")
    try:
        utf8 = raw.decode("utf-8")
    except UnicodeDecodeError:
        utf8 = None
    if utf8 is not None and (utf8 == cp437 or path_key(utf8) in by_key):
        return by_key.get(path_key(utf8), utf8), "utf-8"
    if utf8 is None or path_key(cp437) in by_key:
        return by_key.get(path_key(cp437), cp437), "cp437"
    return utf8, "utf-8"


def xml_value(path, encoding):
    """A path as XML text in the given encoding (see card_path())."""
    if encoding == "cp437":
        try:
            return unicodedata.normalize("NFC", path).encode("cp437").decode("utf-8", "surrogateescape")
        except UnicodeEncodeError:
            pass
    return path


def readable(s):
    """Report text: bytes that aren't UTF-8 (surrogates) shown as the firmware reads them, as CP437."""
    return re.sub("[\udc80-\udcff]+", lambda m: m.group().encode("utf-8", "surrogateescape").decode("cp437"), s)


class FilePlan:
    def __init__(self, rel):
        self.rel = rel  # Path on the card, as found (forward slashes)
        self.uses = set()  # "resample", "keep_length", "wavetable", "unknown"
        self.referenced = False
        self.loops = False  # A length-keeping use loops (audio clip, LOOP or STRETCH repeat mode)
        self.info = None
        self.size = 0
        self.outputs = {}  # use -> Output ("resample" / "keep_length")
        self.action = ""  # For the report
        self.category = "left as it is"


class Output:
    def __init__(self, rel, mode, f, frames_new, rate_new, mtun, pitch=Fraction(1), convert=True):
        self.rel, self.mode, self.f, self.frames_new, self.rate_new = rel, mode, f, frames_new, rate_new
        self.mtun, self.pitch, self.convert = mtun, pitch, convert
        self.size = None
        self.warnings = []


def card_files(card):
    out = []
    for dirpath, dirnames, filenames in os.walk(card):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, card).replace(os.sep, "/"))
    return out


def unique_name(rel, taken):
    stem, ext = os.path.splitext(rel)
    candidate, i = rel, 2
    while path_key(candidate) in taken:
        candidate = f"{stem}_{i}{ext}"
        i += 1
    taken.add(path_key(candidate))
    return candidate


def plan(args, log):
    card = args.card
    files = card_files(card)
    by_key = {path_key(f): f for f in files}
    taken = set(by_key)
    warnings, xml_errors = [], []

    # XML references: one file at a time, kept are only its references and positions (a card can hold hundreds of MB
    # of XML), its size and checksum (read_xml() reads it again for writing)
    xmls, all_refs = {}, []
    for rel in files:
        top = rel.split("/")[0].upper()
        if top in XML_FOLDERS and rel.lower().endswith(".xml"):
            with open(os.path.join(card, rel), "rb") as fh:
                raw = fh.read()
            size, crc = len(raw), zlib.crc32(raw)
            text = raw.decode("utf-8", errors="surrogateescape")
            del raw
            try:
                root = parse_xml(text)
            except ValueError as e:
                paths = set(re.findall(r"[\"'>]([^\"'<>]+\.(?:wav|aif|aiff))[\"'<]", text, re.I))
                xml_errors.append((rel, str(e), paths))
                continue
            refs, others = refs_in_xml(rel, root, warnings)
            del root, text
            xmls[rel] = dict(refs=refs, others=others, size=size, crc=crc)
            all_refs += refs

    plans = {}

    def plan_for(rel):
        key = path_key(rel)
        if key not in plans:
            plans[key] = FilePlan(by_key.get(key, rel))
        return plans[key]

    for rel in files:
        if rel.split("/")[0].upper() == "SAMPLES" and rel.lower().endswith(AUDIO_EXTENSIONS):
            plan_for(rel)
    for r in all_refs:
        r.rel, r.encoding = card_path(r.path[0], by_key)
        p = plan_for(r.rel)
        p.uses.add(r.use)
        p.referenced = True
        p.loops |= r.use == "keep_length" and r.looping
        r.plan = p
    for rel, x in xmls.items():
        for path in x["others"]:
            path = card_path(path, by_key)[0]
            p = plan_for(path)
            p.uses.add("unknown")
            p.referenced = True
            warnings.append(f"{rel}: {path} is referenced in a way this tool doesn't know: the file stays as it is")
    for rel, err, paths in xml_errors:
        warnings.append(f"{rel}: not readable as XML ({err}); copied unchanged, the files it names stay as they are")
        for path in sorted(paths):
            p = plan_for(card_path(path, by_key)[0])
            p.uses.add("unknown")
            p.referenced = True

    target, rate = args.tenths, args.rate
    jobs = []
    for key in sorted(plans):
        p = plans[key]
        if key not in by_key:
            p.action = p.category = "missing"
            warnings.append(f"{p.rel}: referenced but not on the card")
            continue
        with open(os.path.join(card, p.rel), "rb") as fh:
            p.info = info = read_audio_info(fh)  # Only the headers: the jobs read the audio
        info.chunks = []  # Not needed until the job, which reads the file again
        p.size = info.file_size
        if info.kind is None:
            p.action = f"left as it is: {info.error}"
            if p.referenced:
                warnings.append(f"{p.rel}: {info.error}; left as it is")
            continue
        if not p.uses:
            p.uses.add("resample")  # Unreferenced: as a sample in a kit or synth, the usual use
            if info.channels == 1 and info.frames % 2048 == 0 and info.frames and not info.clm:
                p.action = "left as it is: may be a wavetable (mono, a multiple of 2048 samples, not referenced)"
                continue
        if info.clm or "wavetable" in p.uses or "unknown" in p.uses:
            p.action = "left as it is: " + ("wavetable" if info.clm or "wavetable" in p.uses else "unknown use")
            if p.referenced and p.uses & {"resample", "keep_length"} and (info.clm or "wavetable" in p.uses):
                warnings.append(f"{p.rel}: used as a wavetable and as a sample; left as it is (the sample uses keep "
                                f"being shifted at run time)")
            continue
        src = info.tuning
        rate_new = rate or info.rate
        if not 5000 <= rate_new <= 96000:
            p.action = "left as it is: sample rate out of range"
            continue
        f_resample = Fraction(rate_new, info.rate) * Fraction(src, target)
        f_keep = Fraction(rate_new, info.rate)
        mtun = None if target == DEFAULT_TENTHS else target
        frames = info.frames
        rounded = lambda f: math.floor(frames * f + Fraction(1, 2))  # noqa: E731
        if src == target and rate_new == info.rate:
            p.category = "already native"
            p.action = "already in the target tuning" + (" at 44.1 kHz" if rate_new == 44100 else "")
            for use in p.uses:
                p.outputs[use] = Output(p.rel, use, Fraction(1), frames, info.rate, None, convert=False)
            continue
        wav_names = []

        def converted_name(suffix=""):
            """The converted file's path: the same (an AIFF as .wav), or with the suffix for a second copy."""
            stem, ext = os.path.splitext(p.rel)
            if info.kind == "aiff":
                ext = ".WAV" if ext.isupper() else ".wav"
            if not suffix and info.kind == "wav":
                return p.rel
            name = stem + suffix + ext
            if path_key(name) in taken:
                name = unique_name(stem + (suffix or "_aif") + ext, taken)
            taken.add(path_key(name))
            wav_names.append(name)
            return name

        actions = []
        if "resample" in p.uses:
            p.outputs["resample"] = Output(converted_name(), "resample", f_resample, rounded(f_resample), rate_new,
                                           mtun)
            actions.append(f"resampled x{float(f_resample):.6f}")
        if "keep_length" in p.uses:
            if src == target and "resample" in p.uses:  # Only the sample rate changes: the same file serves both
                p.outputs["keep_length"] = p.outputs["resample"]
            elif pylibrb is not None or src == target:
                name = converted_name(SUFFIX_TS if "resample" in p.uses else "")
                p.outputs["keep_length"] = Output(name, "keep_length", f_keep, rounded(f_keep), rate_new, mtun,
                                                  pitch=Fraction(target, src))
                actions.append("pitch-shifted keeping the length" if src != target else "sample rate converted")
            else:
                # Stays as it is (the firmware shifts it at run time), under its own name if the plain uses get the
                # converted file under the original name
                name = p.rel
                if "resample" in p.uses and path_key(p.outputs["resample"].rel) == path_key(p.rel):
                    stem, ext = os.path.splitext(p.rel)
                    name = unique_name(stem + SUFFIX_TS + ext, taken)
                p.outputs["keep_length"] = Output(name, "keep_length", Fraction(1), frames, info.rate, None,
                                                  convert=False)
                actions.append("its audio clip / time-stretch uses left as they are (needs pylibrb)")
                warnings.append(f"{p.rel}: used by an audio clip or a time-stretched sample; without pylibrb "
                                f"(Rubber Band) it stays as it is for that use and the firmware keeps shifting it")
        p.category = "converted" if any(o.convert for o in p.outputs.values()) else "left as it is"
        p.action = ", ".join(actions) + (f" -> {', '.join(wav_names)}" if wav_names else "")
        for use, o in p.outputs.items():
            if o.convert and (use == "resample" or o is not p.outputs.get("resample")):
                jobs.append(dict(index=len(jobs), src=os.path.join(card, p.rel), rel=p.rel, out_rel=o.rel,
                                 dst=os.path.join(args.out or "", o.rel), mode=o.mode, f_num=o.f.numerator,
                                 f_den=o.f.denominator, pitch_num=o.pitch.numerator, pitch_den=o.pitch.denominator,
                                 frames_new=o.frames_new, rate_new=o.rate_new, mtun=o.mtun, loop=p.loops,
                                 no_float=args.no_float, frames=frames, channels=info.channels,
                                 block=args.block))
                o.job = jobs[-1]
    return files, xmls, plans, jobs, warnings


def xml_edits(xmls, warnings):
    """Per XML: the edits (start, end, new text) and the report lines."""
    result = {}
    for rel, x in xmls.items():
        edits, lines = [], []
        for r in x["refs"]:
            p = r.plan
            o = p.outputs.get(r.use)
            if o is None:
                continue
            changes = []
            if path_key(o.rel) != path_key(r.rel):
                new_path = ("/" if r.path[0].startswith("/") else "") + o.rel
                edits.append((r.path[1], r.path[2], xml_value(new_path, r.encoding)))  # As the XML had it
                changes.append(f"path -> {new_path}")
            f, n_old, n_new = o.f, p.info.frames, o.frames_new
            if f != 1:
                pos = {k: as_int(v) for k, v in r.positions.items()}
                new = {}
                start = pos.get("startSamplePos", 0)
                new["startSamplePos"] = scale_pos(start, f, n_old, n_new)
                if "endSamplePos" in pos:
                    new["endSamplePos"] = scale_span(start, pos["endSamplePos"], f, n_old, n_new)[1]
                loop_start = pos.get("startLoopPos", 0)
                if loop_start > 0:
                    new["startLoopPos"] = scale_pos(loop_start, f, n_old, n_new)
                if pos.get("endLoopPos", 0) > 0:
                    a = loop_start if loop_start > 0 else start
                    new["endLoopPos"] = scale_span(a, pos["endLoopPos"], f, n_old, n_new)[1]
                # The loop's period (repeat mode LOOP): rounding to whole samples
                if r.loop_mode == 2:
                    a = loop_start if loop_start > 0 else start
                    b = pos.get("endLoopPos", 0) or pos.get("endSamplePos", 0) or n_old
                    err = scale_span(a, b, f, n_old, n_new)[2]
                    if abs(err) > LOOP_WARN_CENTS:
                        warnings.append(f"{rel}: {r.context}, {r.path[0]}: loop of {b - a} samples -> its period is "
                                        f"{err:+.2f} cents off after rounding (a single-cycle or very short loop)")
                for k, v in r.positions.items():
                    if k in new and new[k] != pos[k]:
                        edits.append((v[1], v[2], str(new[k])))
                        changes.append(f"{k} {pos[k]} -> {new[k]}")
                if r.ms:  # Milliseconds: only the tuning moves them (the firmware converts at the file's rate)
                    tune = f * Fraction(p.info.rate, o.rate_new)
                    for side in ("start", "end"):
                        s, m = r.ms.get(side + "Seconds"), r.ms.get(side + "Milliseconds")
                        total = as_int(s) * 1000 + as_int(m)
                        if not total:
                            continue
                        new_ms = math.floor(total * tune + Fraction(1, 2))
                        if m is not None:
                            if s is not None:
                                edits.append((s[1], s[2], str(new_ms // 1000)))
                                edits.append((m[1], m[2], str(new_ms % 1000)))
                            else:
                                edits.append((m[1], m[2], str(new_ms)))
                        else:
                            edits.append((s[1], s[2], str(round(new_ms / 1000))))
                            if new_ms % 1000:
                                warnings.append(f"{rel}: {r.context}: {side} in whole seconds only, "
                                                f"{total} -> {new_ms} ms rounded to {round(new_ms / 1000)} s")
                        changes.append(f"{side} {total} ms -> {new_ms} ms")
            if changes:
                lines.append(f"  {r.context}: {r.path[0]}: " + ", ".join(changes))
        result[rel] = (edits, lines)
    return result


def read_xml(card, rel, x):
    """An XML's text, read again for writing: it must be the file the plan read."""
    with open(os.path.join(card, rel), "rb") as fh:
        raw = fh.read()
    if len(raw) != x["size"] or zlib.crc32(raw) != x["crc"]:
        raise RuntimeError(f"{rel} changed on the card during the run: nothing of it written, run again")
    return raw.decode("utf-8", errors="surrogateescape")


def apply_edits(text, edits):
    out, pos = [], 0
    for start, end, new in sorted(set(edits)):
        if start < pos:
            raise RuntimeError(f"overlapping edits at character {start}")
        out += (text[pos:start], new)
        pos = end
    out.append(text[pos:])
    return "".join(out)


# --------------------------------------------------------------------------------------------------------------------
# Memory: how many files are converted at once


# A job's peak memory (measured with VmHWM on Linux, see tests/retune/pc_test.py, test_streaming()): its worker
# process with numpy, soxr and Rubber Band loaded (35 MB), and per sample of a block in flight (read, decoded,
# resampled or pitch-shifted, encoded; 35 to 90 bytes per sample, counted at the larger of the block's input and
# output) plus the resampler's and Rubber Band's own state (up to 10 MB). The estimates keep a margin.
WORKER_MEMORY = 50 << 20
BYTES_PER_SAMPLE = 100
JOB_MEMORY = 16 << 20


def job_memory(job):
    """A job's estimated peak memory in bytes, beyond its process's own (WORKER_MEMORY): from the file's length up
    to one block, since a longer file is converted block by block."""
    frames = min(job["frames"], job["block"])
    return int(JOB_MEMORY + frames * max(1.0, job["f_num"] / job["f_den"]) * job["channels"] * BYTES_PER_SAMPLE)


def available_memory():
    """(bytes of memory available for new processes, how it was read), or (None, why not)."""
    try:
        import psutil
        return psutil.virtual_memory().available, "psutil"
    except Exception:  # Not installed (or not working here)
        pass
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024, "/proc/meminfo"
        except (OSError, ValueError, IndexError):
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in ("total", "available", "total_page", "available_page",
                                                           "total_virtual", "available_virtual", "extended")]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.available, "GlobalMemoryStatusEx"
        except Exception:
            pass
    return None, "can't be read here (pip install psutil)"


def parse_size(text):
    """--max-memory: 4G, 4GB, 3000M, 3000 (MB)."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d*)?)\s*([kmgt]?)i?b?\s*", text, re.I)
    if not m:
        raise argparse.ArgumentTypeError(f"{text!r}: a size such as 4G, 3000M or 3000 (MB)")
    size = int(float(m.group(1)) * {"": 1 << 20, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "t": 1 << 40}[
        m.group(2).lower()])
    if size < 1 << 20:
        raise argparse.ArgumentTypeError(f"{text!r}: at least 1 MB")
    return size


class Limiter:
    """Which jobs run at once: the worker processes' own memory and the running jobs' estimates (job_memory()) stay
    within the budget, and never more jobs than processes. A job that needs more than the budget alone runs alone."""

    def __init__(self, todo, args):
        available, how = available_memory()
        if args.max_memory:
            self.budget = args.max_memory
            self.why = "--max-memory" + (f"; {human(available)} available ({how})" if available else "")
        elif available is None:
            self.budget = int(MEMORY_UNKNOWN * MEMORY_SHARE)
            self.why = f"the available memory {how}: {human(MEMORY_UNKNOWN)} assumed"
        else:
            self.budget = int(available * MEMORY_SHARE)
            self.why = f"half of the {human(available)} available ({how}); --max-memory to change"
        self.block = args.block
        self.need = {j["index"]: job_memory(j) for j in todo}
        smallest = min(self.need.values(), default=0)
        self.workers = max(1, min(args.jobs, len(todo), int(self.budget // (WORKER_MEMORY + smallest))))
        self.fixed = self.workers * WORKER_MEMORY
        self.running = {}  # index -> estimate
        self.most_jobs = self.most_memory = 0

    def describe(self):
        if not self.need:
            return "memory: nothing to convert"
        lo, hi = min(self.need.values()), max(self.need.values())
        text = (f"memory: at most {human(self.budget)} for the conversions ({self.why}); a job needs about "
                f"{human(WORKER_MEMORY + lo)} to {human(WORKER_MEMORY + hi)} (a file is converted in blocks of "
                f"{self.block} frames, whatever its length): {self.workers} process(es) at once")
        if self.fixed + hi > self.budget:
            text += ", fewer while large files run" + (" (a file that needs more than that runs alone)"
                                                      if WORKER_MEMORY + hi > self.budget else "")
        return text

    def fits(self, job):
        if not self.running:
            return True
        return (len(self.running) < self.workers
                and self.fixed + sum(self.running.values()) + self.need[job["index"]] <= self.budget)

    def start(self, job):
        self.running[job["index"]] = self.need[job["index"]]
        self.most_jobs = max(self.most_jobs, len(self.running))
        self.most_memory = max(self.most_memory, self.fixed + sum(self.running.values()))

    def finish(self, index):
        del self.running[index]


def run_jobs(todo, args, done):
    """Converts the jobs (the largest first), as many at once as the Limiter lets; done(job, result) as each ends.
    Returns the Limiter (what it chose, what ran)."""
    limiter = Limiter(todo, args)
    print(limiter.describe(), flush=True)
    if not todo:
        return limiter
    order = sorted(todo, key=lambda j: (-limiter.need[j["index"]], -j["frames"], j["index"]))
    print(f"converting {len(todo)} files with {limiter.workers} process(es) ...", flush=True)
    count = 0

    def finished(job, result):
        nonlocal count
        done(job, result)
        count += 1
        if not args.quiet and (count % 50 == 0 or count == len(todo)):
            print(f"  {count}/{len(todo)}", flush=True)

    if limiter.workers == 1:
        for j in order:
            limiter.start(j)
            finished(j, convert_job(j)[1])
            limiter.finish(j["index"])
        return limiter
    by_index = {j["index"]: j for j in todo}
    with concurrent.futures.ProcessPoolExecutor(limiter.workers) as pool:
        running, pending = {}, list(reversed(order))
        while pending or running:
            while pending and limiter.fits(pending[-1]):
                j = pending.pop()
                limiter.start(j)
                running[pool.submit(convert_job, j)] = j["index"]
            ready, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in ready:
                index = running.pop(fut)
                limiter.finish(index)
                try:
                    finished(by_index[index], fut.result()[1])
                except Exception as e:
                    for other in running:
                        other.cancel()
                    raise RuntimeError(f"{by_index[index]['rel']} -> {by_index[index]['out_rel']}: {e} (the files "
                                       f"finished so far are kept: --resume continues)") from e
    return limiter


# --------------------------------------------------------------------------------------------------------------------
# Progress: --resume


def progress_header(args):
    return dict(retune_progress=1, tuning=args.tenths, rate=args.rate, no_float=args.no_float)


def job_key(job):
    """What a finished file was made from and how: the same key, the same file."""
    st = os.stat(job["src"])
    fields = [job[k] for k in ("rel", "out_rel", "mode", "f_num", "f_den", "pitch_num", "pitch_den", "frames_new",
                               "rate_new", "mtun", "loop", "no_float")] + [st.st_size, st.st_mtime_ns]
    return hashlib.sha1(json.dumps(fields).encode()).hexdigest()


class Progress:
    """PROGRESS in the new card's folder: its options, then a line per converted file as soon as it is complete
    (written under a temporary name, on the disk, renamed). --resume skips those files (if they are still there with
    their size) and removes the temporary files of the ones that weren't finished. Removed at the end of a run."""

    def __init__(self, args):
        self.path = os.path.join(args.out, PROGRESS)
        self.finished, self.removed = {}, 0
        if args.resume and os.path.exists(self.path):
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                for line in fh.read().splitlines()[1:]:  # The header: checked in main()
                    try:
                        e = json.loads(line)
                        self.finished[e["key"]] = e
                    except (ValueError, KeyError, TypeError):  # A line cut off by the interruption
                        pass
            for dirpath, _, names in os.walk(args.out):
                for name in names:
                    if name.endswith(TMP_SUFFIX):
                        os.remove(os.path.join(dirpath, name))
                        self.removed += 1
            self.fh = open(self.path, "a", encoding="utf-8")
        else:
            self.fh = open(self.path, "w", encoding="utf-8")
            self.write(progress_header(args))

    def write(self, entry):
        self.fh.write(json.dumps(entry) + "\n")
        self.fh.flush()
        os.fsync(self.fh.fileno())

    def result(self, job):
        """The result of a job finished before, or None."""
        e = self.finished.get(job["key"])
        try:
            if e is not None and e["out"] == job["out_rel"] and os.path.getsize(job["dst"]) == e["result"]["size"]:
                return e["result"]
        except (OSError, KeyError, TypeError):
            pass
        return None

    def add(self, job, result):
        self.write(dict(key=job["key"], out=job["out_rel"], result=result))

    def close(self):
        self.fh.close()
        os.remove(self.path)


def read_progress_header(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.loads(fh.readline())
    except (OSError, ValueError):
        return None


def write_file(dst, data):
    """Written under a temporary name, then renamed: the name exists only for the complete file."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst + TMP_SUFFIX, "wb") as fh:
        fh.write(data)
    os.replace(dst + TMP_SUFFIX, dst)


def copy_file(src, dst, resume):
    """A copy, under a temporary name first; with --resume one that is already there (same size and time) stays."""
    if resume and os.path.isfile(dst):
        s, d = os.stat(src), os.stat(dst)
        if s.st_size == d.st_size and abs(s.st_mtime - d.st_mtime) <= 2:
            return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst + TMP_SUFFIX)
    os.replace(dst + TMP_SUFFIX, dst)


# --------------------------------------------------------------------------------------------------------------------


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Needs: pip install numpy soxr (and pylibrb for audio clips and time-stretched "
                                        "samples)")
    ap.add_argument("--card", required=True, help="the card's folder (a copy of the SD card); only read")
    ap.add_argument("--out", help="the new card's folder (must not exist or be empty, except with --resume); not needed "
                                  "with --dry-run")
    ap.add_argument("--tuning", "--target", type=float, default=432.0,
                    help="the master tune the library is for, in Hz (415.3 to 466.2, 0.1 Hz steps; default 432)")
    ap.add_argument("--rate", type=int, default=44100,
                    help="sample rate of the converted files (default 44100: the Deluge's own, so they need no "
                         "interpolation); 0 keeps each file's rate")
    ap.add_argument("--no-float", action="store_true",
                    help="an integer file whose resampled peaks go over full scale is written a little quieter, "
                         "just enough (the report gives the dB), instead of as 32-bit float")
    ap.add_argument("--dry-run", action="store_true", help="only report what would be done")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                    help="files converted at once (processes) at most; fewer if the memory needs it (see "
                         "--max-memory)")
    ap.add_argument("--max-memory", type=parse_size,
                    help="memory the conversions may use together, e.g. 4G or 3000M (default: half of the memory "
                         "available at the start)")
    ap.add_argument("--resume", action="store_true",
                    help="continue an interrupted run into the same --out (same options): the files it finished "
                         "are kept, the rest converted, then all songs, kits and synths written")
    ap.add_argument("--block", type=int, default=BLOCK, help=argparse.SUPPRESS)  # Frames per block (tests)
    ap.add_argument("--quiet", action="store_true", help="only the summary and the warnings on the console")
    args = ap.parse_args()
    args.tenths = int(round(args.tuning * 10))
    if abs(args.tenths - args.tuning * 10) > 1e-6 or not is_valid_tuning(args.tenths):
        ap.error("--tuning: 415.3 to 466.2 Hz in steps of 0.1 Hz, as the firmware's menu")
    if args.rate and not 5000 <= args.rate <= 96000:
        ap.error("--rate: 5000 to 96000 (or 0)")
    if args.jobs < 1 or args.block < 1:
        ap.error("--jobs (and --block): at least 1")
    args.card = os.path.abspath(args.card)
    if not os.path.isdir(args.card):
        ap.error(f"--card: {args.card} is not a folder")
    if not args.dry_run:
        if not args.out:
            ap.error("--out is needed (or --dry-run)")
        args.out = os.path.abspath(args.out)
        c, o = os.path.normcase(args.card) + os.sep, os.path.normcase(args.out) + os.sep
        if o.startswith(c) or c.startswith(o):
            ap.error("--out must be outside the card's folder (and not contain it)")
        if os.path.exists(args.out) and (not os.path.isdir(args.out) or os.listdir(args.out)):
            if not args.resume or not os.path.isdir(args.out):
                ap.error(f"--out: {args.out} exists and is not empty (--resume continues an interrupted run in it)")
            header = read_progress_header(os.path.join(args.out, PROGRESS))
            if header is None:
                ap.error(f"--resume: {args.out} has no {PROGRESS}: that run is complete, or was made by an older "
                         f"version of this tool (its files can't be trusted); convert into a new, empty folder")
            if header != progress_header(args):
                ap.error(f"--resume: {args.out} was started with other options ({header}); use the same --tuning, "
                         f"--rate and --no-float, or a new, empty folder")
    elif args.out:
        args.out = os.path.abspath(args.out)
    if soxr is None:
        sys.exit("needs soxr: pip install soxr (and numpy)")

    lines = []
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass

    def log(s="", console=True):
        s = readable(s)
        lines.append(s)
        if console:
            print(s, flush=True)

    t0 = time.time()
    files, xmls, plans, jobs, warnings = plan(args, log)
    edits = xml_edits(xmls, warnings)

    # Converting: the audio first, then the other files and the XML (so that an interrupted run leaves no XML that
    # points at audio not yet converted)
    results = {}
    if args.dry_run:
        print(Limiter(jobs, args).describe().replace("memory:", "memory (if converting):"), flush=True)
    else:
        os.makedirs(args.out, exist_ok=True)
        progress = Progress(args)
        todo = []
        for j in jobs:
            j["key"] = job_key(j)
            r = progress.result(j)
            if r is None:
                todo.append(j)
            else:
                results[j["index"]] = r
        if args.resume:
            print(f"resume: {len(jobs) - len(todo)} of {len(jobs)} files were converted before, "
                  f"{progress.removed} unfinished file(s) removed", flush=True)

        def done(job, result):
            results[job["index"]] = result
            progress.add(job, result)

        limiter = run_jobs(todo, args, done)
        if todo:
            print(f"memory: at most {limiter.most_jobs} job(s) ran at once, estimated {human(limiter.most_memory)} of "
                  f"{human(limiter.budget)}", flush=True)
        # Everything else: copied, the XML edited
        # An original is copied unless all its outputs are converted files (under its name or, an AIFF, as WAV)
        not_copied = set()
        for p in plans.values():
            if p.outputs and all(o.convert or o.rel.lower() != p.rel.lower() for o in p.outputs.values()):
                not_copied.add(p.rel.lower())
            for o in p.outputs.values():
                if not o.convert and o.rel.lower() != p.rel.lower():  # The original under a second name
                    copy_file(os.path.join(args.card, p.rel), os.path.join(args.out, o.rel), args.resume)
        for rel in files:
            if rel.lower() in not_copied:
                continue
            dst = os.path.join(args.out, rel)
            if rel in edits and edits[rel][0]:
                data = apply_edits(read_xml(args.card, rel, xmls[rel]), edits[rel][0])
                write_file(dst, data.encode("utf-8", errors="surrogateescape"))
            else:
                copy_file(os.path.join(args.card, rel), dst, args.resume)
    for j in jobs:
        warnings += results.get(j["index"], {}).get("warnings", [])

    # Report
    log(f"retune_library: {args.card} -> {args.out or '(dry run)'}, tuning {args.tenths / 10:g} Hz, "
        f"rate {args.rate or 'kept'}, {'DRY RUN' if args.dry_run else 'written'}")
    log(f"Rubber Band (pylibrb) for audio clips and time-stretched samples: "
        f"{'yes' if pylibrb is not None else 'not installed'}")
    log("")
    log("Audio files:", console=not args.quiet)
    counts, size_old, size_new, as_float, quieter = {}, 0, 0, [], []
    for key in sorted(plans):
        p = plans[key]
        counts[p.category] = counts.get(p.category, 0) + 1
        info = p.info
        desc = ""
        if info is not None and info.kind:
            desc = (f"{info.kind.upper()} {info.channels} ch {info.bits}-bit{' float' if info.float else ''} "
                    f"{info.rate} Hz, {info.frames} samples, tuning {info.tuning / 10:g} Hz"
                    + (" (mtun)" if info.mtun is not None else ""))
        log(f"  {p.rel}: {p.action}" + (f"  [{desc}]" if desc else ""), console=not args.quiet)
        for use, o in p.outputs.items():
            if not o.convert or (use == "keep_length" and o is p.outputs.get("resample")):
                continue
            r = results.get(getattr(o, "job", {}).get("index"), {})
            o.size = r.get("size") or (o.frames_new * info.block_align + 100)
            size_old += p.size
            size_new += o.size
            if r.get("as_float"):
                as_float.append((o.rel, info, r))
            elif r.get("gain_db") is not None:
                quieter.append((o.rel, info, r))
            log(f"      -> {o.rel}: {o.rate_new} Hz, {info.frames} -> {o.frames_new} samples, "
                f"{human(p.size)} -> {'~' if args.dry_run else ''}{human(o.size)}"
                + (f", mtun {o.mtun / 10:g} Hz" if o.mtun else "")
                + (", 32-bit float (peaks over 0 dBFS)" if r.get("as_float") else "")
                + (f", {r['gain_db']:+.2f} dB (peaks over 0 dBFS)" if r.get("gain_db") is not None else ""),
                console=not args.quiet)
    log("", console=not args.quiet)
    log("Songs, kits, synths (positions and paths changed):", console=not args.quiet)
    n_edits = 0
    for rel in sorted(edits):
        e, ls = edits[rel]
        if ls:
            n_edits += len(e)
            log(f"  {rel}", console=not args.quiet)
            for line in ls:
                log(line, console=not args.quiet)
    log("", console=not args.quiet)
    log("Summary:")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        log(f"  {v:6d}  {k}")
    log(f"  {len(jobs)} files written ({human(size_old)} -> {human(size_new)}), "
        f"{sum(1 for e in edits.values() if e[0])} XML files with {n_edits} values changed, "
        f"{len(files)} files on the card; {time.time() - t0:.1f} s")
    def fmt_name(info):
        return f"{info.bits}-bit {'float' if info.float else 'PCM'}"

    if as_float:
        log("")
        log(f"Als 32-Bit-Float geschrieben, weil Spitzen über 0 dBFS ({len(as_float)}): written as 32-bit float, "
            f"because resampling put peaks over full scale; the file keeps them, the firmware limits them to full "
            f"scale as it loads the file (--no-float: a little quieter instead)")
        for rel, info, r in as_float:
            log(f"  {rel}: {fmt_name(info)} -> 32-bit float, peak {db(r['peak']):+.2f} dBFS, {r['over']} samples "
                f"over full scale")
    if quieter:
        log("")
        log(f"Leiser geschrieben, weil Spitzen über 0 dBFS ({len(quieter)}, --no-float): the level lowered just "
            f"enough that nothing is clipped")
        for rel, info, r in quieter:
            log(f"  {rel}: {r['gain_db']:+.2f} dB ({fmt_name(info)}, the resampled peak was {db(r['peak']):+.2f} "
                f"dBFS, {r['over']} samples over full scale)")
    if warnings:
        log("")
        log(f"Warnings ({len(warnings)}):")
        for w in warnings:
            log("  " + w)
    def out_format(o):
        """The output's format and level where the peaks changed them."""
        r = results.get(getattr(o, "job", {}).get("index"), {})
        if not r.get("over"):
            return {}
        return dict(peak_dbfs=round(db(r["peak"]), 3), samples_over_full_scale=r["over"],
                    written_as="32-bit float" if r["as_float"] else None,
                    gain_db=None if r["gain_db"] is None else round(r["gain_db"], 3),
                    gain=None if r["gain_db"] is None else r["gain"])

    if not args.dry_run:
        with open(os.path.join(args.out, "RETUNE_REPORT.txt"), "w", encoding="utf-8",
                  errors="backslashreplace") as fh:
            fh.write("\n".join(lines) + "\n")
        report = dict(
            tuning=args.tenths, rate=args.rate, warnings=[readable(w) for w in warnings],
            files={p.rel: dict(action=p.action, uses=sorted(p.uses),
                               source=None if p.info is None or not p.info.kind else dict(
                                   rate=p.info.rate, frames=p.info.frames, tuning=p.info.tuning,
                                   channels=p.info.channels, bits=p.info.bits, float=p.info.float),
                               outputs={u: dict(path=o.rel, frames=o.frames_new, rate=o.rate_new, mtun=o.mtun,
                                                converted=o.convert, factor=str(o.f), pitch=str(o.pitch),
                                                **out_format(o))
                                        for u, o in p.outputs.items()})
                   for p in plans.values()},
            xml={rel: [readable(line) for line in e[1]] for rel, e in edits.items() if e[1]})
        with open(os.path.join(args.out, "RETUNE_REPORT.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
        progress.close()  # Complete: nothing left to resume
        print(f"\nreport: {os.path.join(args.out, 'RETUNE_REPORT.txt')}")


if __name__ == "__main__":
    main()
