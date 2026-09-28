#!/usr/bin/env python3
"""Separates the synths from a mix with MDX-Net (KUIELab's "other" model as UVR ships it, kuielab_a_other.onnx), run
with onnxruntime on the CPU and a numpy STFT, as KUIELab's ConvTDFNet stft()/istft() and demix() do it: n_fft 8192,
hop 1024, the first 2048 bins (up to 11 kHz), 512 frames per chunk, n_fft/2 of trim on both sides of each chunk, the
output times 1.035 (UVR's "compensate" for this model). The input is [1, 4, 2048, 512]: left real, left imaginary,
right real, right imaginary. What comes out is the mix without drums, bass and vocals.

Usage: separate.py <in.wav|mp3> <out.wav> [model.onnx]
The model (30 MB) is downloaded from github.com/TRvlvr/model_repo if it isn't given. Needs numpy, librosa,
soundfile, onnxruntime (pip, no PyTorch)."""
import os
import sys
import urllib.request

import librosa
import numpy as np
import onnxruntime as ort
import soundfile as sf

URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/kuielab_a_other.onnx"
N_FFT, HOP, DIM_F, DIM_T = 8192, 1024, 2048, 512
CHUNK = HOP * (DIM_T - 1)
TRIM = N_FFT // 2
GEN = CHUNK - 2 * TRIM
COMPENSATE = 1.035


def stft(x):
    return np.stack([librosa.stft(c, n_fft=N_FFT, hop_length=HOP, window="hann", center=True, pad_mode="reflect")
                     for c in x])


def istft(X):
    return np.stack([librosa.istft(c, hop_length=HOP, window="hann", center=True, length=CHUNK) for c in X])


def separate(mix, session):
    """mix: [2, n] at 44.1 kHz; returns the synths, [2, n]"""
    n = mix.shape[1]
    pad = GEN - n % GEN
    padded = np.concatenate([np.zeros((2, TRIM)), mix, np.zeros((2, pad)), np.zeros((2, TRIM))], 1).astype(np.float32)
    out = []
    for i in range(0, n + pad, GEN):
        X = stft(padded[:, i:i + CHUNK])[:, :DIM_F, :DIM_T]
        x = np.stack([X[0].real, X[0].imag, X[1].real, X[1].imag])[None].astype(np.float32)
        y = session.run(None, {"input": x})[0][0]
        Y = np.zeros((2, N_FFT // 2 + 1, DIM_T), np.complex64)
        Y[0, :DIM_F] = y[0] + 1j * y[1]
        Y[1, :DIM_F] = y[2] + 1j * y[3]
        out.append(istft(Y)[:, TRIM:-TRIM])
    return np.concatenate(out, 1)[:, :n] * COMPENSATE


def main():
    src, dst = sys.argv[1], sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(dst)),
                                                               "kuielab_a_other.onnx")
    if not os.path.exists(model):
        urllib.request.urlretrieve(URL, model)
    session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    mix, sr = sf.read(src, always_2d=True)
    assert sr == 44100, "the model wants 44.1 kHz"
    if mix.shape[1] == 1:
        mix = np.repeat(mix, 2, 1)
    synths = separate(mix.T, session)
    sf.write(dst, synths.T, sr)


if __name__ == "__main__":
    main()
