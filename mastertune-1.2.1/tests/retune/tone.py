"""Test helpers: a tone's frequency to a small fraction of a cent, and a firmware's-eye view of a WAV file."""
import struct

import numpy as np
from scipy.signal import butter, sosfiltfilt


def demodulate(x, rate, f_guess, bandwidth):
    """The tone near f_guess as a complex baseband signal (zero-phase low-pass of bandwidth Hz)."""
    t = np.arange(len(x)) / rate
    z = np.asarray(x, np.float64) * np.exp(-2j * np.pi * f_guess * t)
    sos = butter(4, bandwidth / (rate / 2), output="sos")
    return sosfiltfilt(sos, z.real) + 1j * sosfiltfilt(sos, z.imag)


def tone_frequency(x, rate, f_guess, bandwidth=40.0, threshold=0.3, margin_s=None, segments=None):
    """Frequency of the tone near f_guess: the slope of its phase (weighted least squares) where its amplitude is
    above threshold * its maximum, away from the edges of those stretches by margin_s (default 4 / bandwidth), or
    within the given (start, stop) sample segments. Returns (Hz, samples used)."""
    zf = demodulate(x, rate, f_guess, bandwidth)
    amp = np.abs(zf)
    margin = int((margin_s if margin_s is not None else 4 / bandwidth) * rate)
    use = np.zeros(len(x), bool)
    if segments is None:
        on = amp > threshold * amp.max()
        edges = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(np.int8), [0]])))
        segments = list(zip(edges[::2], edges[1::2]))
    for a, b in segments:
        if b - a > 2 * margin + 16:
            use[a + margin:b - margin] = True
    idx = np.flatnonzero(use)
    if idx.size < 16:
        return float("nan"), 0
    # Each stretch its own phase offset (a stretch starts with the note's own phase): fit one slope to all of them
    phase = np.unwrap(np.angle(zf))
    t = np.arange(len(x)) / rate
    breaks = np.flatnonzero(np.diff(idx) > 1)
    groups = np.split(idx, breaks + 1)
    num = den = 0.0
    for g in groups:
        w = amp[g] ** 2
        tm = np.sum(w * t[g]) / np.sum(w)
        pm = np.sum(w * phase[g]) / np.sum(w)
        num += np.sum(w * (t[g] - tm) * (phase[g] - pm))
        den += np.sum(w * (t[g] - tm) ** 2)
    return f_guess + num / den / (2 * np.pi), int(idx.size)


def cents(f, f_ref):
    return 1200 * np.log2(f / f_ref)


def firmware_wav_view(data):
    """The WAV part of AudioFile::loadFile() (mastertune v16): what the firmware reads from a WAV file."""
    view = dict(rate=None, channels=None, byte_depth=None, float=False, mtun=4400, midi_note=None, loop=(0, 0),
                data_start=None, data_length=None, wavetable=False)
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos = 12
    while pos + 8 <= len(data):
        name, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if name == b"data":
            view["data_start"], view["data_length"] = body, size
        elif name == b"fmt ":
            tag, ch, rate, _, _, bits = struct.unpack_from("<HHIIHH", data, body)
            if tag == 0xFFFE:
                tag = struct.unpack_from("<H", data, body + 24)[0]
            view.update(rate=rate, channels=ch, byte_depth=bits // 8, float=tag == 3)
        elif name == b"mtun" and size >= 4:
            v = struct.unpack_from("<i", data, body)[0]
            if 4153 <= v <= 4662:
                view["mtun"] = v
        elif name == b"smpl":
            d = struct.unpack_from("<9I", data, body)
            if (d[3] or d[4]) and d[3] < 128:
                view["midi_note"] = d[4] / 2 ** 32 + d[3]
            if d[7] == 1:
                loop = struct.unpack_from("<6I", data, body + 36)
                view["loop"] = (loop[2], loop[3])
        elif name == b"inst":
            note, fine = struct.unpack_from("<Bb", data, body)
            if note < 128:
                view["midi_note"] = note - fine * 0.01
        elif name == b"clm " and data[body:body + 3] == b"<!>":
            view["wavetable"] = True
        pos = body + ((size + 1) & ~1)
    return view
