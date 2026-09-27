#!/usr/bin/env python3
"""Tests of tools/retune_library.py for large libraries: files converted block by block, the memory limiter and
--resume.

- Blocks: the pc_test card plus files over full scale, a 6 s audio clip at 48 kHz (Rubber Band, then resampled) and
  a loop shorter than Rubber Band's padding, converted with the default block and with blocks of 1000 frames (every
  file in many blocks, Rubber Band's steps cut across them): the same bytes, float and --no-float. The clip and the
  short loop equal, sample for sample, the previous whole-file conversion (reference_keep_length() + soxr at once);
  the clip keeps its length (round(n * 44100 / 48000)) and its attacks' times.
- Large file: 4 min of 24-bit stereo (64 MB) with a peak over full scale near its end: the process's peak memory
  (VmHWM on Linux) against a 10 s file of the same kind (no growth with the length) and against the tool's own
  estimate; the output (--no-float: a gain found in a first pass, written in the second; float) equal, sample for
  sample, to resampling the whole file at once (soxr.resample) with that gain.
- Limiter: job_memory() grows with the file up to one block; Limiter never lets the running jobs' estimates and the
  processes' own memory exceed the budget (a simulated schedule), a job larger than the budget runs alone; a run with
  --max-memory keeps to it (the tool reports what ran at once).
- --resume: a run killed (with its worker processes) after a few files, one finished file then truncated and a stray
  temporary file added, continued with --resume: the same new card as an uninterrupted run (every byte, the report
  but for its time), no temporary files and no progress file left; refused: other options, a complete folder, a
  non-empty folder without --resume.

Usage: stream_resume_test.py <work dir>   Needs: what pc_test.py needs
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from fractions import Fraction

import numpy as np
import soxr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pc_test  # noqa: E402
from pc_test import (TOOL, attacks, check, no_memory_measurement, notes, peaky, rnd, run_measured, run_tool,  # noqa
                     sine, wav, wav_audio)

spec = importlib.util.spec_from_file_location("retune_library", TOOL)
RL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RL)
MB = 1 << 20


def reference_keep_length(x, rate, scale, loop):
    """The whole-file Rubber Band pitch shift of retune_library.py before it converted block by block (commit
    77e6d42), kept as the reference: the whole padded file in memory, fed in steps of 4096 frames."""
    from pylibrb import Option, RubberBandStretcher
    options = Option.PROCESS_REALTIME | Option.ENGINE_FINER | Option.CHANNELS_TOGETHER
    stretcher = RubberBandStretcher(int(rate), x.shape[1], options, 1.0, float(scale))
    n, pad, step = x.shape[0], 16384, 4096
    padded = np.pad(x, ((pad, pad), (0, 0)), mode="wrap" if loop else "constant")
    padded = np.concatenate([np.zeros((stretcher.get_preferred_start_pad(), x.shape[1])), padded])
    audio = np.ascontiguousarray(padded.T, dtype=np.float32)
    stretcher.set_max_process_size(step)
    delay = stretcher.get_start_delay()
    out, have = [], 0
    for i in range(0, audio.shape[1], step):
        stretcher.process(audio[:, i:i + step], final=i + step >= audio.shape[1])
        a = stretcher.available()
        if a > 0:
            out.append(stretcher.retrieve(a))
            have += a
    while have < delay + pad + n:
        a = stretcher.available()
        if a <= 0:
            break
        out.append(stretcher.retrieve(a))
        have += a
    y = np.concatenate(out, axis=1).T.astype(np.float64) if out else np.zeros((0, x.shape[1]))
    y = y[delay + pad:delay + pad + n]
    if y.shape[0] < n:
        y = np.concatenate([y, np.zeros((n - y.shape[0], x.shape[1]))])
    return y


def resample_at_once(x, f):
    """soxr over the whole file at once, as the tool did before: round(n * f) frames."""
    n = rnd(x.shape[0] * f)
    y = soxr.resample(x, f.denominator, f.numerator, quality="VHQ")
    return np.concatenate([y, np.zeros((max(0, n - y.shape[0]), x.shape[1]))])[:n]


def source_audio(path):
    with open(path, "rb") as fh:
        info = RL.read_audio_info(fh)
        return info, RL.Source(fh, info).read(0, info.frames)


def data_bytes(path):
    data = open(path, "rb").read()
    v = pc_test.firmware_wav_view(data)
    return data[v["data_start"]:v["data_start"] + v["data_length"]]


def song(clips):
    """A song with an audio clip per file (length kept: Rubber Band)."""
    rows = "".join(f'\t\t<audioClip trackName="T{i}" filePath="{p}" startSamplePos="0" endSamplePos="{n}" '
                   f'pitchSpeedIndependent="1" length="768" />\n' for i, (p, n) in enumerate(clips))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<song firmwareVersion="c1.2.1">\n\t<sessionClips>\n' + rows
            + '\t</sessionClips>\n</song>\n').encode()


def put(card, rel, data):
    path = os.path.join(card, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def tree(folder):
    """Every file's content, by path; of the report without its first line (the folders) and the time."""
    out = {}
    for dirpath, _, names in os.walk(folder):
        for name in names:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, folder).replace(os.sep, "/")
            data = open(p, "rb").read()
            if rel == "RETUNE_REPORT.txt":
                data = re.sub(rb"; [\d.]+ s\n", b"\n", data.split(b"\n", 1)[1])
            out[rel] = hashlib.sha256(data).hexdigest()
    return out


def same_tree(a, b, what):
    ta, tb = tree(a), tree(b)
    diff = sorted(k for k in set(ta) | set(tb) if ta.get(k) != tb.get(k))
    return check(not diff and ta, f"{what}: different files: {diff[:10]}")


# --- blocks


CLIP_ONSETS = [0.3, 1.3, 2.05, 2.9, 4.5, 5.4]


def blocks_card(card):
    pc_test.build_card(card)
    clip = np.stack([notes(450, 48000, 6.0, CLIP_ONSETS), notes(675, 48000, 6.0, CLIP_ONSETS, amp=0.4)], axis=1)
    put(card, "SAMPLES/BIG/CLIP48.WAV", wav(clip, 48000, 24))
    put(card, "SAMPLES/BIG/TINY.WAV", wav(np.sin(2 * np.pi * np.arange(10000) / 100) * 0.5, 44100, 16))
    put(card, "SONGS/CLIPS.XML", song([("SAMPLES/BIG/CLIP48.WAV", len(clip)), ("SAMPLES/BIG/TINY.WAV", 10000)]))
    put(card, "SAMPLES/PEAK/P16.WAV", wav(peaky(44100, 0.5), 44100, 16))
    put(card, "SAMPLES/PEAK/P24.WAV", wav(np.stack([peaky(48000, 0.5), sine(700, 48000, 0.5, 0.3)], 1), 48000, 24))
    put(card, "SAMPLES/PEAK/P8.WAV", wav(peaky(22050, 0.3), 22050, 8))
    put(card, "SAMPLES/PEAK/PF.WAV", wav(peaky(44100, 0.5), 44100, 32, True))


def test_blocks(work):
    failures_before = pc_test.failures
    card = os.path.join(work, "card")
    blocks_card(card)
    outs = {}
    for mode in ([], ["--no-float"]):
        for block in (None, 1000):
            out = os.path.join(work, "out" + "".join(mode) + (f"_block{block}" if block else ""))
            code, text = run_tool("--card", card, "--out", out, "--tuning", "432", "--jobs", "2", *mode,
                                  *(["--block", str(block)] if block else []))
            check(code == 0, f"blocks: the tool failed ({mode}, block {block}): {text[-1500:]}")
            outs[(tuple(mode), block)] = out
        same_tree(outs[(tuple(mode), None)], outs[(tuple(mode), 1000)],
                  f"blocks ({mode or 'float'}): default block vs 1000 frames")
    out = outs[((), None)]
    # The clip: Rubber Band at 48 kHz, then 48 -> 44.1 kHz; the tiny loop: shorter than Rubber Band's padding
    scale = Fraction(4320, 4400)
    for rel, rate in (("SAMPLES/BIG/CLIP48.WAV", 48000), ("SAMPLES/BIG/TINY.WAV", 44100)):
        info, x = source_audio(os.path.join(card, rel))
        ref = reference_keep_length(x, rate, scale, True)
        if rate != 44100:
            ref = resample_at_once(ref, Fraction(44100, rate))
        want = RL.encode(ref, info.bits, False)[0]
        got = data_bytes(os.path.join(out, rel))
        n_new = rnd(info.frames * Fraction(44100, rate))
        check(len(got) == n_new * info.block_align, f"blocks: {rel}: {len(got) // info.block_align} frames, "
                                                    f"expected {n_new}")
        check(got == want, f"blocks: {rel} differs from the whole-file conversion (Rubber Band at once, soxr at once)")
        if rel.endswith("CLIP48.WAV"):
            _, y = wav_audio(open(os.path.join(out, rel), "rb").read())
            a0, a1 = attacks(x[:, 0]) / rate, attacks(y[:, 0]) / 44100
            ok = len(a0) == len(a1) == len(CLIP_ONSETS)
            d = float(np.max(np.abs(a0 - a1))) * 1000 if ok else math.inf
            check(ok and d < 5.0, f"blocks: {rel}: attacks {a0} s -> {a1} s")
            print(f"blocks: Rubber Band clip {info.frames} -> {n_new} frames, attacks within {d:.2f} ms")
    print(f"blocks: {'ok' if pc_test.failures == failures_before else 'FAILED'}")


# --- a large file


def long_wav(path, seconds, rate=44100, burst_at=0.95):
    """24-bit stereo: two tones and a burst over full scale (see pc_test.peaky()) near the end, written in pieces."""
    n = int(seconds * rate)
    ch, ba = 2, 6
    with open(path, "wb") as fh:
        fmt = pc_test.struct.pack("<HHIIHH", 1, ch, rate, rate * ba, ba, 24)
        fh.write(b"RIFF" + pc_test.struct.pack("<I", 4 + 8 + 16 + 8 + n * ba) + b"WAVE" + pc_test.chunk(b"fmt ", fmt)
                 + b"data" + pc_test.struct.pack("<I", n * ba))
        burst = int(burst_at * n)
        for a in range(0, n, 1 << 20):
            t = np.arange(a, min(n, a + (1 << 20))) / rate
            x = np.stack([0.4 * np.sin(2 * np.pi * 440 * t), 0.3 * np.sin(2 * np.pi * 1234.5 * t)], axis=1)
            k = np.arange(len(t)) + a - burst
            m = (k >= 0) & (k < 48)
            x[m, 0] = np.sqrt(2) * np.sin(np.pi / 2 * k[m] + np.pi / 4)
            fh.write(pc_test.pcm(np.clip(x, -1, 1), 24))
    return n


def test_large(work, seconds=240):
    failures_before = pc_test.failures
    cards = {}
    for name, s in (("short", 10), ("long", seconds)):
        cards[name] = os.path.join(work, name)
        os.makedirs(os.path.join(cards[name], "SAMPLES"))
        long_wav(os.path.join(cards[name], "SAMPLES", "LONG.WAV"), s)
    size = os.path.getsize(os.path.join(cards["long"], "SAMPLES", "LONG.WAV")) / MB
    peaks, outs, estimate = {}, {}, None
    # Measured first, before this process grows (only VmHWM is independent of it)
    for name, mode in (("short", "--no-float"), ("long", "--no-float"), ("long", "")):
        out = os.path.join(work, f"out_{name}{mode}")
        code, text, peak, how = run_measured([sys.executable, TOOL, "--card", cards[name], "--out", out, "--tuning",
                                              "432", "--jobs", "1", *([mode] if mode else [])])
        if not check(code == 0, f"large: the tool failed ({name} {mode}): {text[-1500:]}"):
            return
        peaks[(name, mode)], outs[(name, mode)] = peak, out
        m = re.search(r"a job needs about [\d.]+ MB to ([\d.]+) MB", text)
        if name == "long" and m:
            estimate = float(m.group(1))
    if peaks[("long", "--no-float")] is None:
        no_memory_measurement("large")
    else:
        p_short, p_long, p_float = peaks[("short", "--no-float")], peaks[("long", "--no-float")], peaks[("long", "")]
        print(f"large: {size:.0f} MB of 24-bit stereo ({seconds} s), converted in blocks of {RL.BLOCK} frames: peak "
              f"{p_long:.0f} MB (--no-float, two passes), {p_float:.0f} MB (float); a 10 s file: {p_short:.0f} MB "
              f"({how}); the tool's estimate for a job: {estimate} MB")
        check(max(p_long, p_float) - p_short < 25, f"large: the memory grows with the file's length: {p_short:.0f} MB "
                                                   f"for 10 s, {p_long:.0f} / {p_float:.0f} MB for {seconds} s")
        check(estimate is not None and max(p_long, p_float) <= estimate,
              f"large: peak {max(p_long, p_float):.0f} MB over the tool's estimate {estimate} MB")
    # The output against the whole file resampled at once
    info, x = source_audio(os.path.join(cards["long"], "SAMPLES", "LONG.WAV"))
    f = Fraction(4400, 4320)
    ref = resample_at_once(x, f)
    del x
    scale = 2 ** 23
    js = json.load(open(os.path.join(outs[("long", "--no-float")], "RETUNE_REPORT.json"), encoding="utf-8"))
    o = js["files"]["SAMPLES/LONG.WAV"]["outputs"]["resample"]
    g = o.get("gain")
    check(g is not None and g < 1 and o.get("samples_over_full_scale", 0) > 0 and ref.max() > 1.2,
          f"large: the peak near the end wasn't found: {o}")
    if g is not None:
        _, y = wav_audio(open(os.path.join(outs[("long", "--no-float")], "SAMPLES/LONG.WAV"), "rb").read())
        want = np.round(ref * g * scale)
        diff = int(np.count_nonzero(np.round(y * scale) != want))
        check(y.shape == ref.shape and diff == 0 and (want.max() == scale - 1 or want.min() == -scale),
              f"large (--no-float): {diff} samples differ from the whole file resampled at once times the gain")
        del y, want
    v, y = wav_audio(open(os.path.join(outs[("long", "")], "SAMPLES/LONG.WAV"), "rb").read())
    check(v["float"] and y.shape == ref.shape and np.array_equal(y, ref.astype(np.float32).astype(np.float64)),
          "large (float): differs from the whole file resampled at once")
    del y, ref
    for d in list(cards.values()) + list(outs.values()):
        shutil.rmtree(d, ignore_errors=True)  # 250 MB
    print(f"large: {'ok' if pc_test.failures == failures_before else 'FAILED'}")


# --- the memory limiter


def fake_job(index, frames, channels=2, f=Fraction(55, 54), mode="resample", block=RL.BLOCK):
    return dict(index=index, frames=frames, channels=channels, f_num=f.numerator, f_den=f.denominator, mode=mode,
                pitch_num=1, pitch_den=1, block=block)


def test_limiter(work):
    failures_before = pc_test.failures
    # job_memory(): with the file's length, up to a block
    small, block, huge = (RL.job_memory(fake_job(0, n)) for n in (1000, RL.BLOCK, 90_000_000))
    check(small < block == huge and huge < 100 * MB,
          f"limiter: job_memory {small / MB:.1f} / {block / MB:.1f} / {huge / MB:.1f} MB (1000 frames / a block / 35 min)")
    rng = random.Random(1)
    for budget_mb, jobs in ((7000, 12), (400, 12), (150, 4), (60, 4)):
        todo = [fake_job(i, rng.choice([2000, 50_000, 400_000, 90_000_000]), rng.choice([1, 2]),
                         rng.choice([Fraction(55, 54), Fraction(8800, 7938), Fraction(4, 1)]))
                for i in range(200)]
        args = argparse.Namespace(jobs=jobs, max_memory=budget_mb * MB, block=RL.BLOCK)
        lim = RL.Limiter(todo, args)
        # A schedule as run_jobs() makes it: the largest first, jobs ending in random order
        pending = sorted(todo, key=lambda j: -lim.need[j["index"]])[::-1]
        running, worst, alone_ok = [], 0, True
        while pending or running:
            while pending and lim.fits(pending[-1]):
                j = pending.pop()
                lim.start(j)
                running.append(j["index"])
            total = lim.fixed + sum(lim.need[i] for i in running)
            if len(running) > 1:
                worst = max(worst, total)
            elif total > lim.budget:
                alone_ok = alone_ok and len(running) == 1
            if len(running) > lim.workers:
                check(False, f"limiter: {len(running)} jobs for {lim.workers} processes")
            lim.finish(running.pop(rng.randrange(len(running))))
        check(worst <= lim.budget and alone_ok and 1 <= lim.workers <= jobs,
              f"limiter ({budget_mb} MB, --jobs {jobs}): {lim.workers} processes, up to {worst / MB:.0f} MB "
              f"with more than one job")
        if budget_mb == 7000:
            check(lim.workers == jobs, f"limiter: 7 GB, --jobs {jobs}: only {lim.workers} processes")
    # A job larger than the budget: alone
    args = argparse.Namespace(jobs=4, max_memory=60 * MB, block=RL.BLOCK)
    big, other = fake_job(0, 90_000_000), fake_job(1, 1000)
    lim = RL.Limiter([big, other], args)
    check(lim.fits(big), "limiter: a job over the budget doesn't run even alone")
    lim.start(big)
    check(not lim.fits(other), "limiter: another job runs beside a job over the budget")
    # The tool keeps to --max-memory and says what ran at once
    card = os.path.join(work, "card")
    for i in range(6):
        put(card, f"SAMPLES/S{i}.WAV", wav(np.stack([sine(300 + 50 * i, 44100, 3.0)] * 2, 1), 44100, 24))
    for budget in (200, 1000):
        out = os.path.join(work, f"out{budget}")
        code, text = run_tool("--card", card, "--out", out, "--tuning", "432", "--jobs", "4", "--max-memory",
                              f"{budget}M")
        m = re.search(r"(\d+) process\(es\) at once", text)
        m2 = re.search(r"at most (\d+) job\(s\) ran at once, estimated ([\d.]+) MB of ([\d.]+) MB", text)
        ok = code == 0 and m and m2 and float(m2.group(2)) <= float(m2.group(3)) == budget
        check(ok and int(m2.group(1)) <= int(m.group(1)) <= 4 and (budget > 200 or int(m.group(1)) < 4),
              f"limiter: --max-memory {budget}M: {text[-1200:]}")
        if ok:
            print(f"limiter: --max-memory {budget}M, --jobs 4: {m.group(1)} process(es), at most {m2.group(1)} "
                  f"job(s) at once, estimated {m2.group(2)} MB")
    print(f"limiter: {'ok' if pc_test.failures == failures_before else 'FAILED'}")


# --- --resume


def start_killable(args):
    kw = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if hasattr(os, "killpg"):
        kw["start_new_session"] = True
    else:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen([sys.executable, TOOL, *args], **kw)


def kill_all(proc):
    """The tool and its worker processes, at once (as a crash or a hard reset would)."""
    if hasattr(os, "killpg"):
        os.killpg(proc.pid, signal.SIGKILL)
    else:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    proc.wait()


def test_resume(work):
    failures_before = pc_test.failures
    card, full, part = (os.path.join(work, x) for x in ("card", "full", "part"))
    pc_test.build_card(card)
    clips = []
    for i in range(8):  # Enough work (Rubber Band) to stop the run in the middle
        rel = f"SAMPLES/CLIPS/C{i}.WAV"
        x = np.stack([notes(300 + 40 * i, 44100, 8.0, [0.5, 2.5, 4.5, 6.5])] * 2, axis=1)
        put(card, rel, wav(x, 44100, 16))
        clips.append((rel, len(x)))
    put(card, "SONGS/CLIPS.XML", song(clips))
    args = ["--card", card, "--tuning", "432", "--jobs", "2", "--no-float"]
    code, text = run_tool(*args, "--out", full)
    if not check(code == 0, f"resume: the uninterrupted run failed: {text[-1500:]}"):
        return
    n_jobs = int(re.search(r"(\d+) files written", text).group(1))
    progress = os.path.join(part, RL.PROGRESS)
    proc = start_killable(args + ["--out", part])
    t = time.time()
    while proc.poll() is None and time.time() - t < 120:
        try:
            with open(progress, encoding="utf-8") as fh:
                if len(fh.read().splitlines()) >= 5:  # The header and 4 files
                    break
        except OSError:
            pass
        time.sleep(0.02)
    interrupted = proc.poll() is None
    kill_all(proc)
    if not check(interrupted and not os.path.exists(os.path.join(part, "RETUNE_REPORT.txt")),
                 "resume: the run ended before it could be interrupted"):
        return
    entries = []
    for line in open(progress, encoding="utf-8").read().splitlines()[1:]:
        try:
            entries.append(json.loads(line))
        except ValueError:  # Cut off by the kill
            pass
    temps = [os.path.join(d, n) for d, _, names in os.walk(part) for n in names if n.endswith(RL.TMP_SUFFIX)]
    xml_written = os.path.exists(os.path.join(part, "SONGS", "SONG.XML"))
    # A finished file that lost its end (as a crash can do to a file not yet on the disk) and a stray temporary file
    victim = os.path.join(part, entries[0]["out"])
    with open(victim, "r+b") as fh:
        fh.truncate(os.path.getsize(victim) - 100)
    put(part, "SAMPLES/STRAY.WAV" + RL.TMP_SUFFIX, b"half a file")
    # Refused: without --resume, with other options
    code, text = run_tool(*args, "--out", part)
    check(code != 0 and "--resume" in text and "not empty" in text, f"resume: a non-empty folder was taken: {text}")
    code, text = run_tool(*[a if a != "432" else "440" for a in args], "--out", part, "--resume")
    check(code != 0 and "other options" in text, f"resume: other options were accepted: {text[-600:]}")
    # Continued
    code, text = run_tool(*args, "--out", part, "--resume")
    if not check(code == 0, f"resume: the resumed run failed: {text[-1500:]}"):
        return
    m = re.search(r"resume: (\d+) of (\d+) files were converted before, (\d+) unfinished file\(s\) removed", text)
    check(m and int(m.group(1)) == len(entries) - 1 and int(m.group(2)) == n_jobs
          and int(m.group(3)) == len(temps) + 1,
          f"resume: expected {len(entries) - 1} of {n_jobs} files skipped, {len(temps) + 1} removed: {m and m.group()}")
    same_tree(full, part, "resume: the resumed card vs an uninterrupted run")
    left = [n for _, _, names in os.walk(part) for n in names if n.endswith(RL.TMP_SUFFIX) or n == RL.PROGRESS]
    check(not left, f"resume: left over: {left}")
    code, text = run_tool(*args, "--out", part, "--resume")
    check(code != 0 and RL.PROGRESS in text, f"resume: a complete folder was resumed: {text[-600:]}")
    print(f"resume: killed after {len(entries)} of {n_jobs} files ({len(temps)} being written, XML "
          f"{'written' if xml_written else 'not yet written'}); resumed: {m and m.group(1)} skipped, the rest "
          f"converted, the card the same as an uninterrupted run")
    print(f"resume: {'ok' if pc_test.failures == failures_before else 'FAILED'}")


def main():
    work = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "retune-stream-resume")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    test_blocks(os.path.join(work, "blocks"))
    test_large(os.path.join(work, "large"))
    test_limiter(os.path.join(work, "limiter"))
    test_resume(os.path.join(work, "resume"))
    print("stream_resume_test:", "all ok" if not pc_test.failures else f"{pc_test.failures} FAILURES")
    sys.exit(1 if pc_test.failures else 0)


if __name__ == "__main__":
    main()
