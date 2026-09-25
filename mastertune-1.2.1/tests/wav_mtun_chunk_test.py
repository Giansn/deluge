#!/usr/bin/env python3
"""Checks the WAV files the recorder writes with the "mtun" (master tune) chunk.

Builds the header byte for byte like SampleRecorder::setup() (with and without the extra margins / "smpl" chunk,
with and without the tuning chunk) and checks that
- the header length matches audioDataStartPosBytes and the loop end still sits at byte 92,
- the chunk walk of AudioFile::loadFile() finds the tuning and the audio data where the firmware expects them,
- other software (Python wave, libsndfile, scipy) reads the same audio and simply skips the chunk.

Needs: pip install soundfile scipy
"""
import io
import struct
import sys
import wave

import numpy as np
import scipy.io.wavfile
import soundfile

SAMPLE_RATE = 44100
MTUN = 0x6E75746D  # "mtun"
failures = 0


def check(cond, msg):
    global failures
    if not cond:
        print("FAIL:", msg)
        failures += 1


def recorder_header(num_channels, extra_margins, tuning, data_len, loop_start=0, loop_end=0):
    """Mirror of SampleRecorder::setup() in 1.2.1 plus the master tune chunk."""
    write_chunk = tuning != 4400
    start = (112 if extra_margins else 44) + (12 if write_chunk else 0)  # audioDataStartPosBytes
    h = struct.pack("<III", 0x46464952, data_len + start - 8, 0x45564157)
    h += struct.pack("<IIHHIIHH", 0x20746D66, 16, 1, num_channels, SAMPLE_RATE, SAMPLE_RATE * num_channels * 3,
                     num_channels * 3, 24)
    if extra_margins:
        h += struct.pack("<II", 0x6C706D73, 60)
        h += struct.pack("<9I", 0, 0, (1000000000 + (SAMPLE_RATE >> 1)) // SAMPLE_RATE, 0, 0, 0, 0, 1, 0)
        h += struct.pack("<6I", 0, 0, loop_start, loop_end, 0, 0)
    if write_chunk:
        h += struct.pack("<IIi", MTUN, 4, tuning)
    h += struct.pack("<II", 0x61746164, data_len)
    return h, start


def firmware_chunk_walk(data):
    """The WAV part of AudioFile::loadFile(): returns (recordedMasterTune, audio start, audio length)."""
    pos, recorded, start, length = 12, 4400, None, None
    while pos + 8 <= len(data):
        name, size = struct.unpack_from("<II", data, pos)
        chunk_data = pos + 8
        if name == 0x61746164:
            start, length = chunk_data, size
        elif name == MTUN and size >= 4:
            value = struct.unpack_from("<i", data, chunk_data)[0]
            if 4153 <= value <= 4662:  # MasterTune::isValid()
                recorded = value
        pos = chunk_data + ((size + 1) & ~1)
    return recorded, start, length


rng = np.random.default_rng(1)
for num_channels in (1, 2):
    for extra_margins in (False, True):
        for tuning in (4400, 4153, 4320, 4420, 4662):
            frames = 1000
            audio = rng.integers(-(1 << 23), 1 << 23, size=(frames, num_channels), dtype=np.int32)
            pcm = b"".join(int(v).to_bytes(3, "little", signed=True) for v in audio.flatten())
            header, start = recorder_header(num_channels, extra_margins, tuning, len(pcm), 17, frames - 5)
            wav = header + pcm
            name = f"{num_channels}ch margins={extra_margins} tuning={tuning}"

            check(len(header) == start, f"{name}: header is {len(header)} bytes, audioDataStartPosBytes {start}")
            if extra_margins:
                check(struct.unpack_from("<I", wav, 92)[0] == frames - 5, f"{name}: loop end not at byte 92")
            check(struct.unpack_from("<I", wav, start - 4)[0] == len(pcm), f"{name}: data size not before audio")

            recorded, data_start, data_len = firmware_chunk_walk(wav)
            check(recorded == tuning, f"{name}: firmware reads tuning {recorded}")
            check(data_start == start and data_len == len(pcm), f"{name}: firmware finds data at {data_start}")

            with wave.open(io.BytesIO(wav)) as w:
                check(w.getnchannels() == num_channels and w.getframerate() == SAMPLE_RATE, f"{name}: wave format")
                check(w.readframes(frames) == pcm, f"{name}: Python wave reads different audio")

            sf_audio, sf_rate = soundfile.read(io.BytesIO(wav), dtype="int32", always_2d=True)
            check(sf_rate == SAMPLE_RATE and np.array_equal(sf_audio >> 8, audio), f"{name}: libsndfile differs")

            sp_rate, sp_audio = scipy.io.wavfile.read(io.BytesIO(wav))
            sp_audio = sp_audio.reshape(frames, num_channels)
            check(sp_rate == SAMPLE_RATE and np.array_equal(sp_audio >> 8, audio), f"{name}: scipy differs")

print(f"{failures} FAILURES" if failures else "all checks passed")
sys.exit(1 if failures else 0)
