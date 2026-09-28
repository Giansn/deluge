#!/usr/bin/env python3
"""PC test of tools/retune_library.py: a card with every kind of file and reference, converted to 432 Hz.

Checks:
- pitch of every converted file (FFT phase slope, per channel) against the exact target, and the length;
- what the firmware reads (AudioFile::loadFile(), mirrored in tone.firmware_wav_view()): mtun, sample rate, format,
  the note from smpl/inst, the smpl loop; smpl/cue/fact scaled; AIFF made WAV with its note and loop;
- every position in the songs, kits and synths against independently computed values, the paths (AIFF -> WAV, the
  _ts copy of a file used both ways), and nothing else in the XML changed;
- left alone: wavetables, files already at 432 Hz, unreferenced files that may be wavetables, all other files; the
  card folder itself unchanged; a dry run writes nothing; a second run finds nothing to do;
- readable by libsndfile (soundfile), scipy, Python's wave, sox and ffmpeg (the last two if installed);
- the way back: the converted card converted to 440 Hz sounds like the original (pitch);
- peaks over full scale after resampling: an integer file written as 32-bit float with its samples unclipped, or with
  --no-float just as much quieter as needed, never clipped (test_peaks());
- memory: a dry run over 300 MB of songs (many large XML files) keeps only their references and positions, not the
  texts and parse trees (test_xml_memory(); the peak memory is measured with VmHWM on Linux, resource/wait4 on
  other Unix, psutil on Windows if installed, else that part is skipped).

Usage: pc_test.py <work dir>   Needs: numpy scipy soxr soundfile pylibrb (sox and ffmpeg optional, psutil on Windows)
"""
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import warnings
import wave
from fractions import Fraction

import numpy as np
import scipy.io.wavfile
import soundfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tone import cents, firmware_wav_view, tone_frequency  # noqa: E402

TOOL = os.path.join(HERE, "..", "..", "tools", "retune_library.py")
warnings.simplefilter("ignore", scipy.io.wavfile.WavFileWarning)  # scipy skips the chunks it doesn't know (mtun...)
TARGET = 4320
failures = 0
stats = dict(pitch=[], pitch_keep=[])


def check(cond, msg):
    global failures
    if not cond:
        print("FAIL:", msg)
        failures += 1
    return cond


# --- files


def sine(freq, rate, seconds, amp=0.5, fade=0.01):
    n = int(round(seconds * rate))
    t = np.arange(n) / rate
    x = amp * np.sin(2 * np.pi * freq * t)
    k = int(fade * rate)
    if k:
        w = 0.5 - 0.5 * np.cos(np.pi * np.arange(k) / k)
        x[:k] *= w
        x[-k:] *= w[::-1]
    return x


def notes(freq, rate, seconds, onsets, amp=0.5, decay=0.15):
    """Notes with a 2 ms attack and a decay: their attacks are the markers for the timing of a length-kept file."""
    n = int(round(seconds * rate))
    t = np.arange(n) / rate
    x = np.zeros(n)
    for o in onsets:
        s = int(o * rate)
        tt = t[:n - s]
        x[s:] += amp * np.minimum(tt / 0.002, 1) * np.exp(-tt / decay) * np.sin(2 * np.pi * freq * tt)
    return x


def attacks(x):
    """Where the envelope first rises through half the notes' level (0.25), note by note."""
    from scipy.signal import hilbert
    env = np.abs(hilbert(x))
    out, armed = [], True
    for i in np.flatnonzero((env[1:] > 0.25) & (env[:-1] <= 0.25)) + 1:
        if armed or env[out[-1]:i].min() < 0.1:  # A new note only after the level fell below 0.1 (hysteresis)
            out.append(i)
            armed = False
    return np.array(out)


def chunk(cid, body):
    return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def pcm(x, bits, is_float=False):
    x = np.asarray(x, np.float64)
    if is_float:
        return x.astype(f"<f{bits // 8}").tobytes()
    scale = 2 ** (bits - 1)
    y = np.clip(np.round(x * scale), -scale, scale - 1).astype(np.int64)
    if bits == 8:
        return (y + 128).astype(np.uint8).tobytes()
    if bits == 24:
        v = (y.reshape(-1) & 0xFFFFFF).astype(np.uint32)
        return np.stack([v & 0xFF, (v >> 8) & 0xFF, v >> 16], axis=1).astype(np.uint8).tobytes()
    return y.astype(f"<i{bits // 8}").tobytes()


def wav(x, rate, bits=16, is_float=False, extensible=False, before=(), after=(), mtun=None):
    x = np.asarray(x).reshape(len(x), -1)
    ch = x.shape[1]
    ba = ch * bits // 8
    tag = 3 if is_float else 1
    if extensible:
        guid = struct.pack("<H", tag) + bytes.fromhex("000000001000800000aa00389b71")
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, ch, rate, rate * ba, ba, bits, 22, bits, 0) + guid
    else:
        fmt = struct.pack("<HHIIHH", tag, ch, rate, rate * ba, ba, bits)
    body = b"WAVE" + chunk(b"fmt ", fmt) + b"".join(before)
    if mtun:
        body += chunk(b"mtun", struct.pack("<i", mtun))
    body += chunk(b"data", pcm(x, bits, is_float)) + b"".join(after)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def smpl_chunk(rate, note, fraction, loops):
    body = struct.pack("<9I", 0, 0, (10 ** 9 + rate // 2) // rate, note, fraction, 0, 0, len(loops), 0)
    body += b"".join(struct.pack("<6I", i, 0, s, e, 0, 0) for i, (s, e) in enumerate(loops))
    return chunk(b"smpl", body)


def cue_chunk(points):
    body = struct.pack("<I", len(points)) + b"".join(
        struct.pack("<II4sIII", i + 1, p, b"data", 0, 0, p) for i, p in enumerate(points))
    return chunk(b"cue ", body)


def extended(rate):
    exp, m = 16383 + 63, int(rate)
    while m < (1 << 63):
        m <<= 1
        exp -= 1
    return struct.pack(">HQ", exp, m)


def aiff(x, rate, bits, markers, inst):
    x = np.asarray(x).reshape(len(x), -1)
    ch = x.shape[1]
    scale = 2 ** (bits - 1)
    y = np.clip(np.round(x * scale), -scale, scale - 1).astype(f">i{bits // 8}").tobytes()

    def c(cid, body):
        return cid + struct.pack(">I", len(body)) + body + (b"\0" if len(body) & 1 else b"")
    comm = struct.pack(">hIh", ch, len(x), bits) + extended(rate)
    mark = struct.pack(">H", len(markers))
    for mid, pos, name in markers:
        n = name.encode()
        mark += struct.pack(">HIB", mid, pos, len(n)) + n + (b"" if (len(n) + 1) % 2 == 0 else b"\0")
    body = b"AIFF" + c(b"COMM", comm) + c(b"MARK", mark) + c(b"INST", inst) + c(b"SSND", struct.pack(">II", 0, 0) + y)
    return b"FORM" + struct.pack(">I", len(body)) + body


def clm_chunk(cycle):
    return chunk(b"clm ", f"<!>{cycle} 10000000 wwwww".encode())


# --- the card

CARD_FILES = {}  # path -> (bytes, description for the checks)
TONES = {}  # path -> (list of frequencies per channel, rate, tuning of the content)


def add(path, data, tones=None, rate=None, tuning=4400):
    CARD_FILES[path] = data
    if tones:
        TONES[path] = (tones, rate, tuning)


def build_card(card):
    kick = np.stack([sine(600, 48000, 1.0), sine(750, 48000, 1.0, 0.4)], axis=1)
    add("SAMPLES/DRUMS/KICK.WAV",
        wav(kick, 48000, 24, before=[smpl_chunk(48000, 60, 0, [(12000, 36000)])],
            after=[cue_chunk([4800, 24000]), chunk(b"LIST", b"INFOINAM\x05\x00\x00\x00kick\x00\x00")]),
        [600, 750], 48000)
    add("SAMPLES/DRUMS/SNARE.WAV", wav(sine(900, 44100, 0.5), 44100, 16), [900], 44100)
    hat_inst = struct.pack(">BbBBBBh", 72, 10, 0, 127, 1, 127, 0) + struct.pack(">HHH", 1, 1, 2) + \
        struct.pack(">HHH", 0, 0, 0)
    add("SAMPLES/DRUMS/HAT.AIF", aiff(sine(2000, 44100, 0.4), 44100, 16, [(1, 4410, "beg"), (2, 13230, "end")],
                                      hat_inst), [2000], 44100)
    flt = np.stack([sine(1000, 96000, 0.6), sine(1250, 96000, 0.6)], axis=1)
    add("SAMPLES/FLOAT.WAV", wav(flt, 96000, 32, True, before=[chunk(b"fact", struct.pack("<I", len(flt)))]),
        [1000, 1250], 96000)
    add("SAMPLES/U8.WAV", wav(sine(500, 22050, 0.8, 0.8), 22050, 8), [500], 22050)
    add("SAMPLES/I32.WAV", wav(sine(700, 44100, 0.5), 44100, 32), [700], 44100)
    # WAVE_FORMAT_EXTENSIBLE: the firmware can't read it (AudioFile::loadFile() wants format 1 or 3), so it stays
    add("SAMPLES/EXT.WAV", wav(sine(700, 44100, 0.5), 44100, 24, extensible=True))
    add("SAMPLES/REC432.WAV", wav(sine(440, 44100, 0.5), 44100, 16, mtun=4320))
    add("SAMPLES/REC445.WAV", wav(sine(800, 44100, 0.5), 44100, 16, mtun=4450), [800], 44100, 4450)
    add("SAMPLES/WT/SERUM.WAV", wav(sine(44100 / 2048, 44100, 2048 * 4 / 44100, fade=0), 44100, 16,
                                    before=[clm_chunk(2048)]))
    add("SAMPLES/WT/UNREF.WAV", wav(sine(44100 / 2048, 44100, 4096 / 44100, fade=0), 44100, 16))
    add("SAMPLES/WT/WT2.WAV", wav(sine(44100 / 2048 * 3, 44100, 2048 * 8 / 44100, fade=0), 44100, 16))
    clip = np.stack([notes(450, 44100, 2.0, [0.1, 0.6, 1.1, 1.6]), notes(450, 44100, 2.0, [0.35, 0.85, 1.35, 1.85])],
                    axis=1)
    add("SAMPLES/LOOP/CLIP.WAV", wav(clip, 44100, 16), [450, 450], 44100)
    add("SAMPLES/LOOP/BOTH.WAV", wav(notes(1100, 44100, 1.0, [0.1, 0.6]), 44100, 16), [1100], 44100)
    add("SAMPLES/SHORT.WAV", wav(np.sin(2 * np.pi * np.arange(611) / 611) * 0.5, 44100, 16))
    add("SAMPLES/OLD.WAV", wav(sine(1300, 44100, 1.5), 44100, 16), [1300], 44100)
    add("SAMPLES/MULTI/A.WAV", wav(sine(300, 44100, 1.0), 44100, 16), [300], 44100)
    add("SAMPLES/MULTI/B.WAV", wav(sine(2700, 44100, 1.0), 44100, 16), [2700], 44100)
    add("SAMPLES/STRETCH.WAV", wav(notes(1700, 44100, 1.0, [0.1, 0.6]), 44100, 16), [1700], 44100)
    add("SAMPLES/SYNC.WAV", wav(notes(1950, 44100, 1.0, [0.05, 0.3, 0.55, 0.8]), 44100, 16), [1950], 44100)
    add("SETTINGS/NOTES.TXT", b"not audio\n")
    add("CommunityFeatures.XML", b'<runtimeFeatures>\n\t<setting name="masterTune" value="4320" />\n'
                                 b'</runtimeFeatures>\n')

    def row(name, path, start, end, loop_mode=1, stretch=0, loops=""):
        return (f'\t\t\t\t<sound name="{name}" polyphonic="auto" mode="subtractive">\n'
                f'\t\t\t\t\t<osc1 type="sample" transpose="0" cents="0" loopMode="{loop_mode}" reversed="0" '
                f'timeStretchEnable="{stretch}" timeStretchAmount="0" fileName="{path}">\n'
                f'\t\t\t\t\t\t<zone startSamplePos="{start}" endSamplePos="{end}"{loops} />\n'
                f'\t\t\t\t\t</osc1>\n\t\t\t\t\t<osc2 type="sample" transpose="0" cents="0" loopMode="0" />\n'
                f'\t\t\t\t</sound>\n')
    song = ('<?xml version="1.0" encoding="UTF-8"?>\n<song firmwareVersion="c1.2.1">\n\t<instruments>\n'
            '\t\t<kit presetName="KIT" presetFolder="KITS">\n\t\t\t<soundSources>\n'
            + row("KICK", "SAMPLES/DRUMS/KICK.WAV", 2400, 38400)
            + row("SNARE", "SAMPLES/DRUMS/SNARE.WAV", 0, 22050)
            + row("HAT", "SAMPLES/DRUMS/HAT.AIF", 441, 17000, 2, loops=' startLoopPos="4410" endLoopPos="13230"')
            + row("REC", "SAMPLES/REC432.WAV", 0, 22050)
            + row("R445", "SAMPLES/rec445.wav", 100, 20000)  # Another case than on the card, as FAT allows
            + row("BOTH", "SAMPLES/LOOP/BOTH.WAV", 1000, 40000)
            + row("STRETCH", "SAMPLES/STRETCH.WAV", 0, 44100, 1, 1)
            + row("SYNC", "SAMPLES/SYNC.WAV", 500, 44100, 3)
            + row("MISSING", "SAMPLES/NOPE.WAV", 0, 1000)
            + '\t\t\t</soundSources>\n\t\t</kit>\n'
            '\t\t<sound presetName="SYN" presetFolder="SYNTHS" mode="subtractive">\n'
            '\t\t\t<osc1 type="sample" transpose="0" cents="0" loopMode="0" timeStretchEnable="0" '
            'fileName="SAMPLES/FLOAT.WAV">\n\t\t\t\t<zone startSamplePos="9600" endSamplePos="57600" />\n'
            '\t\t\t</osc1>\n'
            '\t\t\t<osc2 type="sample" transpose="0" cents="0" loopMode="2" fileName="SAMPLES/U8.WAV">\n'
            '\t\t\t\t<zone startSamplePos="0" endSamplePos="17640" startLoopPos="2205" endLoopPos="15435" />\n'
            '\t\t\t</osc2>\n\t\t</sound>\n'
            '\t\t<sound presetName="MULTI" presetFolder="SYNTHS" mode="subtractive">\n'
            '\t\t\t<osc1 type="sample" loopMode="2" timeStretchEnable="0">\n\t\t\t\t<sampleRanges>\n'
            '\t\t\t\t\t<sampleRange rangeTopNote="54" fileName="SAMPLES/MULTI/A.WAV" transpose="12" cents="0">\n'
            '\t\t\t\t\t\t<zone startSamplePos="0" endSamplePos="44100" startLoopPos="11025" endLoopPos="33075" />\n'
            '\t\t\t\t\t</sampleRange>\n'
            '\t\t\t\t\t<sampleRange fileName="SAMPLES/MULTI/B.WAV" transpose="0" cents="0">\n'
            '\t\t\t\t\t\t<zone startSamplePos="441" endSamplePos="43000" startLoopPos="0" endLoopPos="30000" />\n'
            '\t\t\t\t\t</sampleRange>\n\t\t\t\t</sampleRanges>\n\t\t\t</osc1>\n'
            '\t\t\t<osc2 type="wavetable" transpose="0" fileName="SAMPLES/WT/WT2.WAV" />\n\t\t</sound>\n'
            '\t\t<sound presetName="SHORT" presetFolder="SYNTHS" mode="subtractive">\n'
            '\t\t\t<osc1 type="sample" loopMode="2" fileName="SAMPLES/SHORT.WAV">\n'
            '\t\t\t\t<zone startSamplePos="0" endSamplePos="611" />\n\t\t\t</osc1>\n\t\t</sound>\n'
            '\t\t<audioTrack name="LOOP" />\n\t</instruments>\n\t<sessionClips>\n'
            '\t\t<audioClip trackName="LOOP" filePath="SAMPLES/LOOP/CLIP.WAV" startSamplePos="0" '
            'endSamplePos="88200" pitchSpeedIndependent="1" length="768" />\n'
            '\t\t<audioClip trackName="LOOP2" filePath="SAMPLES/LOOP/BOTH.WAV" startSamplePos="4410" '
            'endSamplePos="44100" pitchSpeedIndependent="0" length="384" />\n'
            '\t</sessionClips>\n</song>\n')
    add("SONGS/SONG.XML", song.encode())
    kit = ('<?xml version="1.0" encoding="UTF-8"?>\n<kit>\n\t<soundSources>\n\t\t<sound>\n\t\t\t<name>KICK</name>\n'
           '\t\t\t<osc1>\n\t\t\t\t<type>sample</type>\n\t\t\t\t<loopMode>1</loopMode>\n'
           '\t\t\t\t<fileName>SAMPLES/DRUMS/KICK.WAV</fileName>\n\t\t\t\t<zone>\n'
           '\t\t\t\t\t<startSamplePos>2400</startSamplePos>\n\t\t\t\t\t<endSamplePos>48000</endSamplePos>\n'
           '\t\t\t\t</zone>\n\t\t\t</osc1>\n\t\t</sound>\n\t</soundSources>\n</kit>\n')
    add("KITS/KIT.XML", kit.encode())
    old = ('<?xml version="1.0" encoding="UTF-8"?>\n<sound>\n\t<fileName>SAMPLES/OLD.WAV</fileName>\n'
           '\t<zone>\n\t\t<startSeconds>0</startSeconds>\n\t\t<startMilliseconds>250</startMilliseconds>\n'
           '\t\t<endSeconds>1</endSeconds>\n\t\t<endMilliseconds>200</endMilliseconds>\n\t</zone>\n</sound>\n')
    add("SYNTHS/OLD.XML", old.encode())
    for path, data in CARD_FILES.items():
        full = os.path.join(card, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "wb").write(data)


def tree_hash(folder):
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in sorted(os.walk(folder)):
        dirnames.sort()
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            h.update(os.path.relpath(p, folder).encode() + open(p, "rb").read())
    return h.hexdigest()


def run_tool(*args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# Linux: a Python command runs under this wrapper, which prints the process's own peak memory (VmHWM) as it exits.
# ru_maxrss would carry over the peak of the process that started it (Linux keeps it across fork and exec).
PEAK_WRAPPER = """import atexit, runpy, sys
def peak():
    for line in open('/proc/self/status'):
        if line.startswith('VmHWM:'):
            sys.stderr.write('\\nVMHWM_KB %s\\n' % line.split()[1])
            sys.stderr.flush()
atexit.register(peak)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""


def run_measured(args):
    """Runs a command: (exit status, output, peak memory in MB or None, how it was measured). Linux (a Python
    command): its own peak (VmHWM, PEAK_WRAPPER); other Unix: the child's maximum resident set size (os.wait4);
    Windows: its peak working set (psutil, polled until it ends); without either None, and the caller skips its
    memory checks."""
    if args[0] == sys.executable and os.path.exists("/proc/self/status"):
        r = subprocess.run([sys.executable, "-c", PEAK_WRAPPER, *args[1:]], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        text = r.stdout.decode("utf-8", "replace")
        m = re.search(r"\nVMHWM_KB (\d+)\n", text)
        if m:
            return r.returncode, text[:m.start()] + text[m.end():], int(m.group(1)) / 1024, "VmHWM"
        return r.returncode, text, None, None
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT)
        peak, how = None, None
        if hasattr(os, "wait4"):
            _, status, usage = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)
            # ru_maxrss: kB on Linux, bytes on macOS
            peak, how = usage.ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10), "ru_maxrss"
        else:
            try:
                import psutil
            except ImportError:
                psutil = None
            if psutil is not None:
                how, peak = "psutil peak working set", 0.0
                try:
                    ps = psutil.Process(proc.pid)
                    while proc.poll() is None:
                        m = ps.memory_info()
                        peak = max(peak, getattr(m, "peak_wset", m.rss) / (1 << 20))
                        time.sleep(0.02)
                except psutil.Error:  # Ended between poll() and memory_info(): the last reading stands
                    pass
            proc.wait()
        out.seek(0)
        return proc.returncode, out.read().decode("utf-8", "replace"), peak, how


def no_memory_measurement(what):
    print(f"{what}: SKIPPED the memory measurement: this system has no os.wait4 and psutil isn't installed "
          f"(pip install psutil)")


def rnd(x):
    return math.floor(x + Fraction(1, 2))


# --- peaks over full scale


def peaky(rate, seconds, at=0.25, burst=48):
    """A quiet tone with a short burst at a quarter of the sample rate, 45 degrees off the samples: they sit at full
    scale, the waveform between them peaks at +3 dB, and resampling brings those peaks out."""
    x = sine(440, rate, seconds, 0.3)
    s = int(at * rate)
    x[s:s + burst] = np.sqrt(2) * np.sin(np.pi / 2 * np.arange(burst) + np.pi / 4)
    return np.clip(x, -1, 1)


def wav_audio(data):
    """(view, float64 samples (frames, channels) at full scale 1.0, unclipped) of a WAV the tool wrote."""
    v = firmware_wav_view(data)
    raw = data[v["data_start"]:v["data_start"] + v["data_length"]]
    if v["float"]:
        x = np.frombuffer(raw, "<f4").astype(np.float64)
    elif v["byte_depth"] == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        u = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (u - ((u & 0x800000) << 1)) / 2 ** 23
    else:
        x = np.frombuffer(raw, f"<i{v['byte_depth']}") / 2 ** (8 * v["byte_depth"] - 1)
    return v, x.reshape(-1, v["channels"])


def test_peaks(work):
    """Files whose resampled peaks go over full scale: by default written as 32-bit float with the samples as
    resampled (unclipped); with --no-float in their format, just as much quieter as needed; nothing clipped, both
    listed in the report. Other files keep their format."""
    import soxr
    card = os.path.join(work, "card")
    inst = struct.pack(">BbBBBBh", 60, 0, 0, 127, 1, 127, 0) + struct.pack(">HHH", 0, 0, 0) * 2
    stereo = np.stack([peaky(48000, 0.5), sine(700, 48000, 0.5, 0.3)], axis=1)
    files = {"SAMPLES/PEAK16.WAV": (wav(peaky(44100, 0.5), 44100, 16), 44100, 16),
             "SAMPLES/PEAK24.WAV": (wav(stereo, 48000, 24), 48000, 24),
             "SAMPLES/PEAK.AIF": (aiff(peaky(44100, 0.5), 44100, 16, [], inst), 44100, 16),
             "SAMPLES/PEAKF.WAV": (wav(peaky(44100, 0.5) * 1.0, 44100, 32, True), 44100, 32),
             "SAMPLES/QUIET.WAV": (wav(sine(440, 44100, 0.5, 0.5), 44100, 16), 44100, 16)}
    for rel, (data, _, _) in files.items():
        os.makedirs(os.path.dirname(os.path.join(card, rel)), exist_ok=True)
        open(os.path.join(card, rel), "wb").write(data)

    def reference(rel):
        """The source resampled as the tool does it, without clipping: soxr VHQ, the exact ratio, the rounded length."""
        data, rate, _ = files[rel]
        src = soundfile.read(io.BytesIO(data), always_2d=True, dtype="float64")[0]
        f = Fraction(44100, rate) * Fraction(4400, TARGET)
        n = rnd(src.shape[0] * f)
        y = soxr.resample(src, f.denominator, f.numerator, quality="VHQ")
        y = np.concatenate([y, np.zeros((max(0, n - y.shape[0]), y.shape[1]))])[:n]
        return y

    peaks = ("SAMPLES/PEAK16.WAV", "SAMPLES/PEAK24.WAV", "SAMPLES/PEAK.WAV")
    failures_before = failures
    for mode in ("float", "no-float"):
        out = os.path.join(work, mode)
        code, text = run_tool("--card", card, "--out", out, "--tuning", "432", "--jobs", "2",
                              *(["--no-float"] if mode == "no-float" else []))
        if not check(code == 0, f"peaks ({mode}): the tool failed: {text[-1500:]}"):
            continue
        rep = open(os.path.join(out, "RETUNE_REPORT.txt"), encoding="utf-8").read()
        js = json.load(open(os.path.join(out, "RETUNE_REPORT.json"), encoding="utf-8"))
        check(re.search(r"\d samples clipped", rep) is None, f"peaks ({mode}): the report says something was clipped")
        for rel in peaks + ("SAMPLES/QUIET.WAV",):
            src_rel = rel.replace("PEAK.WAV", "PEAK.AIF")
            _, rate, bits = files[src_rel]
            v, y = wav_audio(open(os.path.join(out, rel), "rb").read())
            ref = reference(src_rel)
            outs = js["files"][src_rel]["outputs"]["resample"]
            check(v["mtun"] == TARGET and v["rate"] == 44100 and y.shape == ref.shape,
                  f"peaks ({mode}): {rel}: mtun {v['mtun']}, {v['rate']} Hz, {y.shape} (expected {ref.shape})")
            if rel == "SAMPLES/QUIET.WAV":
                check(not v["float"] and v["byte_depth"] == 2 and "QUIET" not in rep.split("Summary:")[1],
                      f"peaks ({mode}): QUIET.WAV (no peaks over full scale) changed format or is listed")
                continue
            check(ref.max() > 1.2, f"peaks: the test file {rel} doesn't go over full scale ({ref.max():.3f})")
            line = next((ln for ln in rep.split("Summary:")[1].splitlines() if ln.startswith(f"  {rel}: ")), "")
            if mode == "float":
                check(v["float"] and v["byte_depth"] == 4 and b"fact" in open(os.path.join(out, rel), "rb").read(),
                      f"peaks: {rel} not written as 32-bit float (with a fact chunk)")
                err = float(np.max(np.abs(y - ref)))
                check(y.max() > 1.2 and err < 1e-6, f"peaks: {rel}: peak {y.max():.4f}, off the unclipped resample "
                                                     f"by up to {err:.2e}")
                check("Written as 32-bit float, because resampling put peaks over full scale (3)" in rep
                      and f"{bits}-bit PCM -> 32-bit float, peak +" in line,
                      f"peaks: {rel} not listed as written as float: {line!r}")
                check(outs.get("written_as") == "32-bit float", f"peaks: JSON of {rel}: {outs}")
            else:
                check(not v["float"] and v["byte_depth"] == bits // 8, f"peaks (--no-float): {rel} changed format")
                fit = float(np.sum(y * ref) / np.sum(ref * ref))  # The level, measured
                g, gain_db = outs.get("gain") or 1.0, outs.get("gain_db")
                check(gain_db is not None and g < 1 and abs(20 * math.log10(g) - gain_db) < 0.001
                      and abs(20 * math.log10(fit / g)) < 0.001,
                      f"peaks (--no-float): {rel}: level {20 * math.log10(fit):+.4f} dB, the report says {gain_db}")
                # Nothing clipped: every sample is the scaled resample to within the rounding (half a step); just
                # enough quieter: the peak reaches full scale within a step
                err = float(np.max(np.abs(y - g * ref))) * 2 ** (bits - 1)
                check(err < 0.5 + 1e-6, f"peaks (--no-float): {rel}: off the scaled resample by {err:.3f} steps "
                                        f"(clipped?)")
                check(max(y.max() * 2 ** (bits - 1), -y.min() * 2 ** (bits - 1) - 1) > 2 ** (bits - 1) - 3,
                      f"peaks (--no-float): {rel}: lowered more than needed (peak {y.max():.6f} / {y.min():.6f})")
                check("Written a little quieter, because resampling put peaks over full scale (3, --no-float)" in rep
                      and line.startswith(f"  {rel}: -") and " dB (" in line,
                      f"peaks (--no-float): {rel} not listed with its dB: {line!r}")
        # A float file stays float and keeps its peaks; the report says the firmware limits them
        v, y = wav_audio(open(os.path.join(out, "SAMPLES/PEAKF.WAV"), "rb").read())
        check(v["float"] and y.max() > 1.2 and "PEAKF.WAV: float, the resampled peak is +" in rep,
              f"peaks ({mode}): the float file: float {v['float']}, peak {y.max():.3f}, or not in the warnings")
    print(f"peaks: {'ok' if failures == failures_before else 'FAILED'}")


# --- memory of a dry run over a card with many large songs


def big_song(n_kits, rows=16, clips=4):
    """A song as the Deluge writes them: kits with sample rows (each with its zone positions, many parameters) and
    clips with long note data (about 8 KB of XML per sample row, as on a real card: 295 MB with about 30 000
    positions); the names with an umlaut in CP437, so that the text isn't plain ASCII."""
    params = " ".join(f'{k}="0x{(i * 0x1234567) & 0xFFFFFFFF:08X}"' for i, k in enumerate(
        ("arpeggiatorGate", "portamento", "compressorShape", "oscAVolume", "oscBVolume", "oscAPulseWidth",
         "oscBPulseWidth", "noiseVolume", "volume", "pan", "lpfFrequency", "lpfResonance", "hpfFrequency",
         "hpfResonance", "lfo1Rate", "lfo2Rate", "modulator1Amount", "modulator2Amount", "modulator1Feedback",
         "modulator2Feedback", "carrier1Feedback", "carrier2Feedback", "modFXRate", "modFXDepth", "delayRate",
         "delayFeedback", "reverbAmount", "arpeggiatorRate", "stutterRate", "sampleRateReduction", "bitCrush",
         "modFXOffset", "modFXFeedback")))
    knobs = "".join(f'\t\t\t\t\t\t<modKnob controlsParam="{p}" />\n' for p in
                    ("pan", "volumePostFX", "lpfResonance", "lpfFrequency", "env1Release", "env1Attack",
                     "delayFeedback", "delayRate", "reverbAmount", "volumePostReverbSend", "pitch", "lfo1Rate",
                     "portamento", "stutterRate", "bitcrushAmount", "sampleRateReduction"))
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<song firmwareVersion="c1.2.1" earliestCompatibleFirmware="4.1.0">'
           '\n\t<instruments>\n']
    for k in range(n_kits):
        out.append(f'\t\t<kit presetName="Kit {k} ä" presetFolder="KITS">\n\t\t\t<soundSources>\n')
        for r in range(rows):
            start = 100 + (k * rows + r) * 37 % 40000
            out.append(
                f'\t\t\t\t<sound name="Row {r} ä" polyphonic="auto" voicePriority="1" mode="subtractive" '
                f'lpfMode="24dB" modFXType="none" filterRoute="H2L">\n'
                f'\t\t\t\t\t<osc1 type="sample" transpose="0" cents="0" loopMode="1" reversed="0" '
                f'timeStretchEnable="0" timeStretchAmount="0" fileName="SAMPLES/ONE.WAV">\n'
                f'\t\t\t\t\t\t<zone startSamplePos="{start}" endSamplePos="{start + 4000}" />\n'
                f'\t\t\t\t\t</osc1>\n\t\t\t\t\t<osc2 type="square" transpose="0" cents="0" retrigPhase="-1" />\n'
                f'\t\t\t\t\t<lfo1 type="triangle" syncLevel="0" />\n\t\t\t\t\t<lfo2 type="triangle" />\n'
                f'\t\t\t\t\t<unison num="1" detune="8" />\n\t\t\t\t\t<delay pingPong="1" analog="0" syncLevel="7" />\n'
                f'\t\t\t\t\t<compressor syncLevel="6" attack="327244" release="936" />\n'
                f'\t\t\t\t\t<defaultParams {params}>\n'
                f'\t\t\t\t\t\t<envelope1 attack="0x80000000" decay="0xE6666654" sustain="0x7FFFFFFF" '
                f'release="0x80000000" />\n'
                f'\t\t\t\t\t\t<envelope2 attack="0xE6666654" decay="0xE6666654" sustain="0xFFFFFFE9" '
                f'release="0xE6666654" />\n'
                f'\t\t\t\t\t\t<patchCables>\n\t\t\t\t\t\t\t<patchCable source="velocity" destination="volume" '
                f'amount="0x3FFFFFE8" />\n\t\t\t\t\t\t</patchCables>\n\t\t\t\t\t</defaultParams>\n'
                f'\t\t\t\t\t<modKnobs>\n{knobs}\t\t\t\t\t</modKnobs>\n\t\t\t\t</sound>\n')
        out.append('\t\t\t</soundSources>\n\t\t</kit>\n')
    out.append('\t</instruments>\n\t<sessionClips>\n')
    for k in range(n_kits * clips):
        out.append(f'\t\t<instrumentClip inKeyMode="0" instrumentPresetName="Kit {k // clips} ä" length="1536">\n'
                   f'\t\t\t<noteRows>\n')
        for r in range(rows):
            data = "".join(f"{(t * 24):08X}0000000C40{(r * 7 + t) % 127:02X}FF40" for t in range(64))
            out.append(f'\t\t\t\t<noteRow drumIndex="{r}" noteDataWithLift="0x{data}" />\n')
        out.append('\t\t\t</noteRows>\n\t\t</instrumentClip>\n')
    out.append('\t</sessionClips>\n</song>\n')
    return "".join(out).encode("cp437")


def test_xml_memory(work, total_mb=300, file_mb=3):
    """A dry run over a card with total_mb of songs (as many files of file_mb as that takes: hard links of one song,
    so that it takes no disk space): its peak memory must stay far below the XML's size (before: about 11 bytes per
    character held, 3.4 GB for 295 MB of XML on a real card)."""
    one = wav(sine(440, 44100, 1.0), 44100, 16)
    kits = max(1, round(file_mb * (1 << 20) / len(big_song(1))))
    song = big_song(kits)
    n = max(1, round(total_mb * (1 << 20) / len(song)))
    cards = {}
    for name, count in (("small", 1), ("big", n)):
        card = os.path.join(work, name)
        os.makedirs(os.path.join(card, "SAMPLES"))
        os.makedirs(os.path.join(card, "SONGS"))
        open(os.path.join(card, "SAMPLES", "ONE.WAV"), "wb").write(one)
        first = os.path.join(card, "SONGS", "SONG000.XML")
        open(first, "wb").write(song)
        for i in range(1, count):
            p = os.path.join(card, "SONGS", f"SONG{i:03d}.XML")
            try:
                os.link(first, p)
            except OSError:
                shutil.copyfile(first, p)
        cards[name] = card
    xml_mb = n * len(song) / (1 << 20)
    failures_before = failures
    rows = kits * 16
    measured = {}
    for name, card in cards.items():
        t = time.time()
        code, text, peak, how = run_measured([sys.executable, TOOL, "--card", card, "--dry-run", "--tuning", "432",
                                              "--quiet"])
        measured[name] = peak
        count = 1 if name == "small" else n
        m = re.search(r"(\d+) XML files with (\d+) values changed", text)
        check(code == 0 and m is not None and int(m.group(1)) == count and int(m.group(2)) == count * rows * 2,
              f"memory ({name}): the dry run failed or found other positions: {text[-800:]}")
        if name == "big":
            print(f"xml memory: dry run over {n} songs, {xml_mb:.0f} MB of XML ({rows} sample rows each): "
                  + (f"peak {peak:.0f} MB ({how}; one song: {measured['small']:.0f} MB), {time.time() - t:.0f} s"
                     if peak is not None else "not measured"))
    if measured["big"] is None:
        no_memory_measurement("xml memory")
        return
    # What grows with the card is only the references and positions (and the report): far less than the XML
    check(measured["big"] < 1024 and measured["big"] - measured["small"] < xml_mb / 3,
          f"xml memory: peak {measured['big']:.0f} MB for {xml_mb:.0f} MB of XML ({measured['small']:.0f} MB for one "
          f"song): the texts or parse trees are held")
    print(f"xml memory: {'ok' if failures == failures_before else 'FAILED'}")


def main():
    work = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "retune-pc")
    shutil.rmtree(work, ignore_errors=True)
    card, out, back = (os.path.join(work, x) for x in ("card", "out", "back"))
    build_card(card)
    before = tree_hash(card)

    # Dry run: nothing written
    code, text = run_tool("--card", card, "--tuning", "432", "--dry-run")
    check(code == 0, f"dry run failed: {text[-800:]}")
    check("DRY RUN" in text and not os.path.exists(out), "dry run wrote something")
    code, text = run_tool("--card", card, "--out", out, "--tuning", "432", "--jobs", "2")
    print(text[text.find("Summary:"):].rstrip())
    if not check(code == 0, f"tool failed: {text[-1500:]}"):
        return
    check(tree_hash(card) == before, "the card folder changed")
    code2, text2 = run_tool("--card", card, "--out", out, "--tuning", "432")
    check(code2 != 0 and "not empty" in text2, "writing into a non-empty folder was not refused")
    code2, text2 = run_tool("--card", card, "--out", os.path.join(card, "NEW"), "--tuning", "432")
    check(code2 != 0 and "outside" in text2, "writing into the card folder was not refused")

    # --- audio
    tune = Fraction(4400, TARGET)
    expect = {  # source path -> output path, factor, frames, pitch ratio, keep-length?
        "SAMPLES/DRUMS/KICK.WAV": ("SAMPLES/DRUMS/KICK.WAV", Fraction(44100, 48000) * tune, False),
        "SAMPLES/DRUMS/SNARE.WAV": ("SAMPLES/DRUMS/SNARE.WAV", tune, False),
        "SAMPLES/DRUMS/HAT.AIF": ("SAMPLES/DRUMS/HAT.WAV", tune, False),
        "SAMPLES/FLOAT.WAV": ("SAMPLES/FLOAT.WAV", Fraction(44100, 96000) * tune, False),
        "SAMPLES/U8.WAV": ("SAMPLES/U8.WAV", Fraction(44100, 22050) * tune, False),
        "SAMPLES/I32.WAV": ("SAMPLES/I32.WAV", tune, False),
        "SAMPLES/REC445.WAV": ("SAMPLES/REC445.WAV", Fraction(4450, TARGET), False),
        "SAMPLES/LOOP/CLIP.WAV": ("SAMPLES/LOOP/CLIP.WAV", Fraction(1), True),
        "SAMPLES/LOOP/BOTH.WAV": ("SAMPLES/LOOP/BOTH.WAV", tune, False),
        "SAMPLES/LOOP/BOTH_ts.WAV": ("SAMPLES/LOOP/BOTH_ts.WAV", Fraction(1), True),
        "SAMPLES/OLD.WAV": ("SAMPLES/OLD.WAV", tune, False),
        "SAMPLES/MULTI/A.WAV": ("SAMPLES/MULTI/A.WAV", tune, False),
        "SAMPLES/MULTI/B.WAV": ("SAMPLES/MULTI/B.WAV", tune, False),
        "SAMPLES/STRETCH.WAV": ("SAMPLES/STRETCH.WAV", Fraction(1), True),
        "SAMPLES/SYNC.WAV": ("SAMPLES/SYNC.WAV", Fraction(1), True),
        "SAMPLES/SHORT.WAV": ("SAMPLES/SHORT.WAV", tune, False),
    }
    sox, ffmpeg = shutil.which("sox"), shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg = None
    readers = {"libsndfile": 0, "scipy": 0, "wave": 0, "sox": 0, "ffmpeg": 0}
    for src, (dst, f, keep) in expect.items():
        src_data = CARD_FILES.get(src) or CARD_FILES[src.replace("_ts", "")]
        src_key = src.replace("_ts", "")
        path = os.path.join(out, dst)
        if not check(os.path.exists(path), f"{dst} not written"):
            continue
        data = open(path, "rb").read()
        view = firmware_wav_view(data)
        tones, rate, tuning = TONES.get(src_key, (None, None, 4400))
        n_src = None
        if src_key.endswith(".AIF"):
            n_src = struct.unpack_from(">I", src_data, 12 + 8 + 2)[0]
            src_view = dict(byte_depth=2, float=False, channels=1)
        else:
            src_view = firmware_wav_view(src_data)
            n_src = src_view["data_length"] // (src_view["channels"] * src_view["byte_depth"])
        n_new = rnd(n_src * f)
        check(view["mtun"] == TARGET, f"{dst}: the firmware reads mtun {view['mtun']}")
        check(view["rate"] == 44100, f"{dst}: rate {view['rate']}")
        check(view["byte_depth"] == src_view["byte_depth"] and view["float"] == src_view["float"]
              and view["channels"] == src_view["channels"], f"{dst}: format changed")
        frames = view["data_length"] // (view["channels"] * view["byte_depth"])
        check(frames == n_new, f"{dst}: {frames} samples, expected {n_new}")
        check(data.index(b"mtun") < view["data_start"], f"{dst}: mtun after the data")
        # Readers
        a, r = soundfile.read(io.BytesIO(data), always_2d=True)
        readers["libsndfile"] += check(r == 44100 and a.shape == (frames, view["channels"]), f"{dst}: libsndfile")
        r2, a2 = scipy.io.wavfile.read(io.BytesIO(data))
        readers["scipy"] += check(r2 == 44100 and a2.shape[0] == frames, f"{dst}: scipy")
        if not view["float"] and b"\xfe\xff" != data[20:22]:
            with wave.open(io.BytesIO(data)) as w:
                readers["wave"] += check(w.getnframes() == frames and w.getframerate() == 44100, f"{dst}: wave")
        if sox:
            r = subprocess.run([sox, "--i", "-s", path], capture_output=True, text=True)
            readers["sox"] += check(r.returncode == 0 and int(r.stdout.strip() or -1) == frames,
                                    f"{dst}: sox reads {r.stdout.strip()} {r.stderr.strip()}")
        if ffmpeg:
            r = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-f", "null", "-"], capture_output=True, text=True)
            readers["ffmpeg"] += check(r.returncode == 0 and not r.stderr.strip(), f"{dst}: ffmpeg {r.stderr[:200]}")
        # Pitch, per channel
        if tones:
            for c, f0 in enumerate(tones):
                expected = f0 * TARGET / tuning
                fm, used = tone_frequency(a[:, c], 44100, expected, threshold=0.2 if keep or "BOTH" in src else 0.5,
                                          margin_s=0.05 if keep or "BOTH" in src else None)
                err = cents(fm, expected)
                (stats["pitch_keep"] if keep else stats["pitch"]).append(abs(err))
                check(abs(err) < (0.01 if src_key == "SAMPLES/U8.WAV" or keep else 0.001),
                      f"{dst} channel {c}: {fm:.5f} Hz, expected {expected:.5f} ({err:+.5f} cents)")
                if keep:  # Length kept: the notes' attacks where they were
                    orig = soundfile.read(io.BytesIO(src_data), always_2d=True)[0][:, c]
                    a0, a1 = attacks(orig), attacks(a[:, c])
                    if check(len(a0) == len(a1) and len(a0) > 0, f"{dst}: {len(a0)} attacks -> {len(a1)}"):
                        d = int(np.max(np.abs(a1 - a0)))
                        stats.setdefault("attack", []).append(d)
                        check(d <= 88, f"{dst}: the attacks moved by up to {d} samples ({a0} -> {a1})")
    # Chunks
    kick = firmware_wav_view(open(os.path.join(out, "SAMPLES/DRUMS/KICK.WAV"), "rb").read())
    fk = Fraction(44100, 48000) * tune
    check(kick["midi_note"] == 60, f"KICK: note {kick['midi_note']}")
    check(kick["loop"] == (rnd(12000 * fk), rnd(12000 * fk) + rnd(24000 * fk)), f"KICK: smpl loop {kick['loop']}")
    kd = open(os.path.join(out, "SAMPLES/DRUMS/KICK.WAV"), "rb").read()
    period = struct.unpack_from("<I", kd, kd.index(b"smpl") + 16)[0]
    check(period == (10 ** 9 + 22050) // 44100, f"KICK: smpl period {period}")
    cue = kd.index(b"cue ")
    pts = [struct.unpack_from("<I", kd, cue + 12 + i * 24 + 4)[0] for i in range(2)]
    check(pts == [rnd(4800 * fk), rnd(24000 * fk)], f"KICK: cue {pts}")
    check(b"LIST" in kd and b"kick" in kd, "KICK: LIST chunk lost")
    hat = firmware_wav_view(open(os.path.join(out, "SAMPLES/DRUMS/HAT.WAV"), "rb").read())
    check(abs(hat["midi_note"] - (72 - 0.1)) < 1e-6, f"HAT (AIFF): note {hat['midi_note']}, the firmware read 71.9")
    check(hat["loop"] == (rnd(4410 * tune), rnd(4410 * tune) + rnd(8820 * tune)), f"HAT: loop {hat['loop']}")
    fl = open(os.path.join(out, "SAMPLES/FLOAT.WAV"), "rb").read()
    check(struct.unpack_from("<I", fl, fl.index(b"fact") + 8)[0] == rnd(57600 * Fraction(44100, 96000) * tune),
          "FLOAT: fact not updated")
    # Left alone, byte for byte
    for p in ("SAMPLES/REC432.WAV", "SAMPLES/EXT.WAV", "SAMPLES/WT/SERUM.WAV", "SAMPLES/WT/UNREF.WAV", "SAMPLES/WT/WT2.WAV",
              "SETTINGS/NOTES.TXT", "CommunityFeatures.XML"):
        check(open(os.path.join(out, p), "rb").read() == CARD_FILES[p], f"{p} changed")
    check(not os.path.exists(os.path.join(out, "SAMPLES/DRUMS/HAT.AIF")), "HAT.AIF still there beside HAT.WAV")

    # --- XML
    song = open(os.path.join(out, "SONGS/SONG.XML")).read()
    orig = CARD_FILES["SONGS/SONG.XML"].decode()

    def zone_of(xml, path):
        m = re.search(r'fileName="' + re.escape(path) + r'"[^>]*>\s*<zone ([^/]*)/>', xml)
        return dict(re.findall(r'(\w+)="(\d+)"', m.group(1))) if m else None

    def expect_zone(path, new_path, f, n_old, n_new, start, end, ls=0, le=0):
        z = zone_of(song, new_path)
        if not check(z is not None, f"SONG.XML: no zone for {new_path}"):
            return
        s2 = rnd(start * f) if 0 < start < n_old else (n_new if start >= n_old else start)
        e2 = n_new if end >= n_old else s2 + rnd((end - start) * f)
        want = dict(startSamplePos=s2, endSamplePos=e2)
        if ls:
            want["startLoopPos"] = rnd(ls * f)
        if le:
            a = ls or start
            want["endLoopPos"] = (rnd(a * f) if a else 0) + rnd((le - a) * f)
        got = {k: int(v) for k, v in z.items()}
        check(all(got.get(k) == v for k, v in want.items()), f"SONG.XML {new_path}: {got}, expected {want}")

    expect_zone("", "SAMPLES/DRUMS/KICK.WAV", fk, 48000, rnd(48000 * fk), 2400, 38400)
    expect_zone("", "SAMPLES/DRUMS/SNARE.WAV", tune, 22050, rnd(22050 * tune), 0, 22050)
    expect_zone("", "SAMPLES/DRUMS/HAT.WAV", tune, 17640, rnd(17640 * tune), 441, 17000, 4410, 13230)
    expect_zone("", "SAMPLES/REC432.WAV", Fraction(1), 22050, 22050, 0, 22050)
    f445 = Fraction(4450, TARGET)
    expect_zone("", "SAMPLES/rec445.wav", f445, 22050, rnd(22050 * f445), 100, 20000)
    expect_zone("", "SAMPLES/LOOP/BOTH.WAV", tune, 44100, rnd(44100 * tune), 1000, 40000)
    expect_zone("", "SAMPLES/STRETCH.WAV", Fraction(1), 44100, 44100, 0, 44100)
    expect_zone("", "SAMPLES/SYNC.WAV", Fraction(1), 44100, 44100, 500, 44100)
    ff = Fraction(44100, 96000) * tune
    expect_zone("", "SAMPLES/FLOAT.WAV", ff, 57600, rnd(57600 * ff), 9600, 57600)
    fu = Fraction(44100, 22050) * tune
    expect_zone("", "SAMPLES/U8.WAV", fu, 17640, rnd(17640 * fu), 0, 17640, 2205, 15435)
    expect_zone("", "SAMPLES/MULTI/A.WAV", tune, 44100, rnd(44100 * tune), 0, 44100, 11025, 33075)
    expect_zone("", "SAMPLES/MULTI/B.WAV", tune, 44100, rnd(44100 * tune), 441, 43000, 0, 30000)
    check('filePath="SAMPLES/LOOP/CLIP.WAV" startSamplePos="0" endSamplePos="88200"' in song, "CLIP positions changed")
    check('filePath="SAMPLES/LOOP/BOTH_ts.WAV" startSamplePos="4410" endSamplePos="44100"' in song,
          "the audio clip on BOTH.WAV doesn't use its _ts copy with its positions unchanged")
    check('fileName="SAMPLES/WT/WT2.WAV"' in song and "SAMPLES/NOPE.WAV" in song, "wavetable/missing paths changed")
    # Nothing else changed: the same text with the numbers and paths taken out
    def skeleton(x):
        return re.sub(r'"[^"]*"', '""', re.sub(r">[^<]*<", "><", x))
    check(skeleton(song) == skeleton(orig), "SONG.XML: more than values changed")
    kit = open(os.path.join(out, "KITS/KIT.XML")).read()
    check(f"<startSamplePos>{rnd(2400 * fk)}</startSamplePos>" in kit
          and f"<endSamplePos>{rnd(48000 * fk)}</endSamplePos>" in kit, "KIT.XML (child tags) not scaled")
    old = open(os.path.join(out, "SYNTHS/OLD.XML")).read()
    s_ms, e_ms = rnd(250 * tune), rnd(1200 * tune)
    check("<startSeconds>0</startSeconds>" in old and f"<startMilliseconds>{s_ms}</startMilliseconds>" in old
          and f"<endSeconds>{e_ms // 1000}</endSeconds>" in old
          and f"<endMilliseconds>{e_ms % 1000}</endMilliseconds>" in old, f"OLD.XML (ms) not scaled: {old}")

    # --- report
    rep = open(os.path.join(out, "RETUNE_REPORT.txt"), encoding="utf-8").read()
    check("SAMPLES/NOPE.WAV: referenced but not on the card" in rep, "missing file not reported")
    check(re.search(r"SHORT\.WAV: loop of 611 samples -> its period is [+-]\d", rep) is not None,
          "short loop not reported")
    check("may be a wavetable" in rep and "wavetable" in rep, "wavetables not reported")

    # --- a second run: nothing to do
    code, text = run_tool("--card", out, "--tuning", "432", "--dry-run")
    m = re.search(r"(\d+) files written", text)
    check(code == 0 and m and m.group(1) == "0", f"second run converts again: {text[-600:]}")

    # --- back to 440: sounds like the original
    code, text = run_tool("--card", out, "--out", back, "--tuning", "440", "--jobs", "2")
    check(code == 0, f"back to 440 failed: {text[-800:]}")
    back_err = []
    for src, (tones, rate, tuning) in TONES.items():
        dst = src.replace(".AIF", ".WAV")
        p = os.path.join(back, dst)
        if not tones or not check(os.path.exists(p), f"440: {dst} missing"):
            continue
        view = firmware_wav_view(open(p, "rb").read())
        check(view["mtun"] == 4400, f"440: {dst} still has mtun {view['mtun']}")
        a, _ = soundfile.read(p, always_2d=True)
        for c, f0 in enumerate(tones):
            expected = f0 * 4400 / tuning
            keepish = any(k in src for k in ("CLIP", "BOTH", "STRETCH", "SYNC"))
            fm, _ = tone_frequency(a[:, c], 44100, expected, threshold=0.2 if keepish else 0.5,
                                   margin_s=0.05 if keepish else None)
            back_err.append(abs(cents(fm, expected)))
            check(abs(cents(fm, expected)) < (0.01 if "U8" in src or keepish else 0.002),
                  f"440: {dst} {fm:.5f} Hz, expected {expected:.5f}")

    print(f"pitch error of the converted files: max {max(stats['pitch']):.6f} cents (resampled, "
          f"{len(stats['pitch'])} channels), max {max(stats['pitch_keep'] or [0]):.6f} cents (length kept, "
          f"{len(stats['pitch_keep'])} channels, attacks within {max(stats.get('attack', [0]))} samples); "
          f"back to 440 Hz: max {max(back_err):.6f} cents")
    print("read by: " + ", ".join(f"{k} {v}" for k, v in readers.items()) + f" of {len(expect)} files"
          + ("" if sox else " (sox not installed)") + ("" if ffmpeg else " (ffmpeg not installed)"))

    test_peaks(os.path.join(work, "peaks"))
    test_xml_memory(os.path.join(work, "xml_memory"))
    print(f"{failures} FAILURES" if failures else "all checks passed")


if __name__ == "__main__":
    main()
    sys.exit(1 if failures else 0)
