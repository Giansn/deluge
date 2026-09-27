#!/usr/bin/env python3
"""Tests for tools/baseline_check.py: a made-up card with every case, and the card copy in geraet/karte.

- Levels: song and kit above 35, synth and row above 40, 40 itself allowed, automation's loudest point, the master
  compressor.
- Samples: the peak of every format the firmware reads (WAV PCM 8, 16, 24 and 32 bit, 32-bit float limited to full
  scale, AIFF 16, 24 and 8 bit signed), within the zone; what it can't read (WAVE_FORMAT_EXTENSIBLE); a missing file; a
  path in CP437 bytes as the Deluge writes it; a sound that plays more than samples (an audible oscillator, FM) gets no
  allowance for a quiet sample; MIDI rows are no audio.
- Stages after the knobs: SATURATION, compressor, analog delay with feedback, LPF drive, resonance of an active filter,
  on a kit, an audio track, a synth, a row and the song, and what doesn't count (a digital delay, open filters).
- Files: songs in subfolders, lower-case .xml, "._" files, a broken XML, --out, --card.
- The card copy: «New Sitar Grii 10» and «Rescue», as checked by hand (geraet/analyse/2026-09-27-baseline-master.md).

Usage: python3 test_baseline_check.py   (stdlib only)
"""
import contextlib
import io
import math
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import baseline_check as bc  # noqa: E402

KARTE = os.path.join(HERE, "..", "..", "geraet", "karte")
V40, V45, V50, DEFAULT35 = "0x4CCCCCA8", "0x66666662", "0x7FFFFFFF", "0x3504F334"


# --- audio files


def pcm(peak_db, bits, n=4000, loud_outside=None):
    """Integer samples of a full-scale int format, peaking at peak_db; with loud_outside=(a, b) at full scale outside
    frames [a, b)."""
    full = 2 ** (bits - 1) - 1
    v = round(full * 10 ** (peak_db / 20))
    x = [(v if i % 2 else -v) if i % 50 == 7 else (v // 3 if i % 2 else -v // 3) for i in range(n)]
    if loud_outside:
        x = [full if (i < loud_outside[0] or i >= loud_outside[1]) and i % 97 == 0 else s for i, s in enumerate(x)]
    return x


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def wav(path, samples, bits=16, tag=1):
    if tag == 3:
        raw = struct.pack(f"<{len(samples)}f", *samples)
    elif bits == 8:
        raw = bytes(s + 128 for s in samples)
    elif bits == 24:
        raw = b"".join(struct.pack("<i", s)[:3] for s in samples)
    else:
        raw = struct.pack(f"<{len(samples)}{'h' if bits == 16 else 'i'}", *samples)
    align = bits // 8
    fmt = struct.pack("<HHIIHH", tag, 1, 44100, 44100 * align, align, bits)
    if tag == 0xFFFE:
        fmt += struct.pack("<HHI", 22, bits, 4) + b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"junk" + struct.pack("<I", 3) + b"abc\0"
    body += b"data" + struct.pack("<I", len(raw)) + raw
    write(path, b"RIFF" + struct.pack("<I", len(body)) + body)


def aiff(path, samples, bits=16):
    if bits == 8:
        raw = bytes(s & 0xFF for s in samples)
    elif bits == 24:
        raw = b"".join(struct.pack(">i", s)[1:] for s in samples)
    else:
        raw = struct.pack(f">{len(samples)}h", *samples)
    comm = struct.pack(">hIh", 1, len(samples), bits) + bytes.fromhex("400EAC44000000000000")
    ssnd = struct.pack(">II", 0, 0) + raw
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    write(path, b"FORM" + struct.pack(">I", len(body)) + body)


# --- songs


def sound(name, path=None, zone=None, osc2="square", mode="subtractive", ranges=None, extra=""):
    osc1 = '<osc1 type="saw" />'
    if path or ranges:
        z = f'<zone startSamplePos="{zone[0]}" endSamplePos="{zone[1]}" />' if zone else ""
        r = "".join(f'<sampleRange fileName="{p}"><zone startSamplePos="0" endSamplePos="99999" /></sampleRange>'
                    for p in ranges or [])
        osc1 = (f'<osc1 type="sample"' + (f' fileName="{path}"' if path else "") + ">" + z +
                (f"<sampleRanges>{r}</sampleRanges>" if r else "") + "</osc1>")
    return f'<sound name="{name}" presetName="{name}" mode="{mode}" {extra}>{osc1}<osc2 type="{osc2}" /></sound>'


def row(index, volume, osc_b="0x80000000", extra=""):
    return (f'<noteRow drumIndex="{index}"><soundParams volume="{volume}" oscAVolume="0x7FFFFFFF" '
            f'oscBVolume="{osc_b}" noiseVolume="0x80000000" compressorThreshold="0x00000000" {extra} /></noteRow>')


def kit_clip(name, rows, volume=DEFAULT35, extra="", children=""):
    return (f'<instrumentClip instrumentPresetName="{name}" isPlaying="0" isPlaying="1">'
            f'<kitParams volume="{volume}" compressorThreshold="0x00000000" lpfMorph="0x80000000" {extra}>'
            f'<delay rate="0x00000000" feedback="0x80000000" /><lpf frequency="0x7FFFFFFF" resonance="0x80000000" />'
            f'<hpf frequency="0x80000000" resonance="0x80000000" />{children}</kitParams>'
            f'<noteRows>{"".join(rows)}</noteRows></instrumentClip>')


def song(instruments="", clips="", volume=DEFAULT35, compressor="0x00000000", params="", attrs="", children=""):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<song firmwareVersion="c1.2.1" lpfMode="24dB" {attrs}>'
            f'<instruments>{instruments}</instruments>{children}'
            f'<songParams volume="{volume}" compressorThreshold="{compressor}" {params}>'
            f'<delay rate="0x00000000" feedback="0x80000000" /><lpf frequency="0x7FFFFFFF" resonance="0x80000000" />'
            f'<hpf frequency="0x80000000" resonance="0x80000000" /></songParams>'
            f'<sessionClips>{clips}</sessionClips></song>\n')


def notes_of(text, root):
    return bc.check_song(bc.parse_xml(text), bc.Card(root))[1]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def sample(self, rel, fn, *args, **kwargs):
        fn(os.path.join(self.root, *rel.split("/")), *args, **kwargs)
        return rel


class Levels(Case):
    def test_song_and_master_compressor(self):
        self.assertEqual(notes_of(song(), self.root), [])
        self.assertEqual(notes_of(song(volume="0x1999997E"), self.root), [])  # 30: below the default is the headroom
        n = notes_of(song(volume=V40, compressor="0x40000000"), self.root)
        self.assertEqual(n, ["Song-Lautstärke 40: +2,1 dB über dem Standard 35", "Master-Kompressor an (Threshold 25)"])

    def test_kit_and_automation(self):
        kit = f'<kit presetName="K"><soundSources>{sound("a")}</soundSources></kit>'
        self.assertEqual(notes_of(song(kit, kit_clip("K", [row(0, V40)])), self.root), [])
        self.assertEqual(notes_of(song(kit, kit_clip("K", [row(0, V40)], volume=V40)), self.root),
                         ["Kit «K»: 40, +2,1 dB über 35"])
        # Automation: the value now is 35, a node reaches 45
        auto = DEFAULT35 + "66666662" + "80000060"
        self.assertEqual(notes_of(song(kit, kit_clip("K", [row(0, V40)], volume=auto)), self.root),
                         ["Kit «K»: 45, +4,2 dB über 35"])

    def test_synth_and_audio(self):
        synth = sound("Saw")
        clip = f'<instrumentClip instrumentPresetName="Saw"><soundParams volume="{V45}" /></instrumentClip>'
        self.assertEqual(notes_of(song(synth, clip), self.root), ["Synth «Saw»: 45, +2,0 dB über 40"])
        fm = sound("Bell", path=self.sample("SAMPLES/q.wav", wav, pcm(-20, 16)), mode="fm")
        clip = f'<instrumentClip instrumentPresetName="Bell"><soundParams volume="{V45}" /></instrumentClip>'
        self.assertEqual(notes_of(song(fm, clip), self.root), ["Synth «Bell»: 45, +2,0 dB über 40"])
        track = '<audioTrack name="A1" lpfMode="24dB"><delay analog="0" /></audioTrack>'
        clip = f'<audioClip trackName="A1"><params volume="{V40}" /></audioClip>'
        self.assertEqual(notes_of(song(track, clip), self.root), ["Audio-Spur «A1»: 40, +2,1 dB über 35"])

    def test_multisample_synth(self):
        ranges = [self.sample("SAMPLES/lo.wav", wav, pcm(-12, 16)), self.sample("SAMPLES/hi.wav", wav, pcm(-3, 16))]
        synth = sound("Keys", ranges=ranges)
        clip = (f'<instrumentClip instrumentPresetName="Keys"><soundParams volume="{V50}" oscBVolume="0x80000000" />'
                f'</instrumentClip>')
        self.assertEqual(notes_of(song(synth, clip), self.root),
                         ["Synth «Keys»: 50, Sample bis -3,0 dBFS, wirkt wie 42,1"])


class Rows(Case):
    def test_samples(self):
        s = self.sample
        drums = [
            sound("full24", s("SAMPLES/full24.wav", wav, pcm(0, 24), 24)),
            sound("quiet16", s("SAMPLES/quiet16.wav", wav, pcm(-8, 16))),
            sound("zone", s("SAMPLES/zone.wav", wav, pcm(-6, 16, loud_outside=(1000, 2000))), zone=(1000, 2000)),
            sound("aiff24", s("SAMPLES/a24.aif", aiff, pcm(-1, 24), 24)),
            sound("float", s("SAMPLES/float.wav", wav, [0.25, -2.0, 0.5] * 100, 32, 3)),
            sound("eight", s("SAMPLES/eight.wav", wav, [96, -96, 10, -10] * 100, 8)),
            sound("ext", s("SAMPLES/ext.wav", wav, pcm(0, 16), 16, 0xFFFE)),
            sound("missing", "SAMPLES/nope.wav"),
            sound("umlaut", "SAMPLES/Kick äöü.wav".encode("cp437").decode("utf-8", "surrogateescape")),
            sound("osc", s("SAMPLES/quiet2.wav", wav, pcm(-20, 16))),
            '<midiOutput channel="1" note="36" />',
            sound("forty", s("SAMPLES/full16.wav", wav, pcm(0, 16))),
            sound("auto", "SAMPLES/full16.wav"),
            sound("aiff8", s("SAMPLES/a8.aif", aiff, [100, -100, 3] * 50, 8)),
            sound("aiff16", s("SAMPLES/a16.aif", aiff, pcm(-2, 16))),
            sound("int32", s("SAMPLES/i32.wav", wav, pcm(-0.5, 32), 32)),
        ]
        s("SAMPLES/Kick äöü.wav", wav, pcm(0, 16))
        rows = [row(i, V50) for i in range(9)] + [row(9, V50, osc_b="0x7FFFFFFF"), row(10, V50), row(11, V40),
                                                  row(12, V40 + "7FFFFFFF00000060"), row(13, V50), row(14, V45),
                                                  row(15, V45)]
        kit = f'<kit presetName="K"><soundSources>{"".join(drums)}</soundSources></kit>'
        self.assertEqual(notes_of(song(kit, kit_clip("K", rows)), self.root), [
            "Reihe «full24» in Kit «K»: 50, Sample bis 0,0 dBFS, wirkt wie 50,0",
            "Reihe «aiff24» in Kit «K»: 50, Sample bis -1,0 dBFS, wirkt wie 47,2",
            "Reihe «float» in Kit «K»: 50, Sample bis 0,0 dBFS, wirkt wie 50,0",
            "Reihe «eight» in Kit «K»: 50, Sample bis -2,5 dBFS, wirkt wie 43,3",
            "Reihe «ext» in Kit «K»: 50, +3,9 dB über 40 (WAVE_FORMAT_EXTENSIBLE, das liest der Deluge nicht)",
            "Reihe «missing» in Kit «K»: 50, +3,9 dB über 40 (Sample fehlt: SAMPLES/nope.wav)",
            "Reihe «umlaut» in Kit «K»: 50, Sample bis 0,0 dBFS, wirkt wie 50,0",
            "Reihe «osc» in Kit «K»: 50, +3,9 dB über 40",
            "Reihe «auto» in Kit «K»: 50, Sample bis 0,0 dBFS, wirkt wie 50,0",
            "Reihe «aiff8» in Kit «K»: 50, Sample bis -2,1 dBFS, wirkt wie 44,2",
            "Reihe «int32» in Kit «K»: 45, Sample bis -0,5 dBFS, wirkt wie 43,7",
        ])

    def test_peak_reader(self):
        cases = [(wav, pcm(-3, 16), (16,), -3), (wav, pcm(-3, 24), (24,), -3), (wav, pcm(-3, 32), (32,), -3),
                 (aiff, pcm(-3, 16), (16,), -3), (aiff, pcm(-3, 24), (24,), -3)]
        for i, (fn, x, args, want) in enumerate(cases):
            p = os.path.join(self.root, f"p{i}")
            fn(p, x, *args)
            peak, error = bc.sample_peak(p)
            self.assertIsNone(error)
            self.assertAlmostEqual(20 * math.log10(peak), want, places=3, msg=f"case {i}")
        p = os.path.join(self.root, "z.wav")
        wav(p, [0] * 100 + [16384] + [0] * 99)
        self.assertEqual(bc.sample_peak(p, 0, 100), (0.0, None))
        self.assertEqual(bc.sample_peak(p, 100, 101), (0.5, None))
        self.assertEqual(bc.sample_peak(p, 50, 10)[0], 0.5)  # End before start: the rest of the file
        self.assertEqual(bc.sample_peak(p, 150, 10 ** 9)[0], 0.0)  # End past the file
        self.assertEqual(bc.sample_peak(p, 10 ** 9, None)[0], 0.0)  # Start past the file
        write(p, b"RIFF\x04\x00\x00\x00WAVE")
        self.assertEqual(bc.sample_peak(p), (None, "kein fmt oder data"))
        write(p, b"OggS" + bytes(20))
        self.assertEqual(bc.sample_peak(p), (None, "kein WAV oder AIFF, das liest der Deluge nicht"))


class Stages(Case):
    def test_after_the_knobs(self):
        kit = ('<kit presetName="K" clippingAmount="2" lpfMode="24dBDrive"><delay analog="1" />'
               f'<soundSources>{sound("a")}</soundSources></kit>')
        clip = kit_clip("K", [row(0, V40, extra='compressorThreshold="0x20000000"')],
                        extra='compressorThreshold="0x10000000"').replace(
            '<delay rate="0x00000000" feedback="0x80000000" />', '<delay rate="0x00000000" feedback="0xC0000000" />')
        synth = sound("S")
        sclip = ('<instrumentClip instrumentPresetName="S"><soundParams volume="0x40000000" '
                 'compressorThreshold="0x08000000" /></instrumentClip>')
        track = '<audioTrack name="A" hpfMode="HPLadder"><delay analog="0" /></audioTrack>'
        aclip = ('<audioClip trackName="A"><params volume="0xE0000000"><delay feedback="0x40000000" />'
                 '<hpf frequency="0x90000000" resonance="0x19999999" /></params></audioClip>')
        text = song(kit + synth + track, clip + sclip + aclip, attrs='lpfMode="SVF_Band"',
                    children='<delay analog="1" />').replace(
            '<songParams volume="0x3504F334" compressorThreshold="0x00000000" >'
            '<delay rate="0x00000000" feedback="0x80000000" /><lpf frequency="0x7FFFFFFF" resonance="0x80000000" />',
            '<songParams volume="0x3504F334" compressorThreshold="0x00000000" >'
            '<delay rate="0x00000000" feedback="0x90000000" /><lpf frequency="0x60000000" resonance="0x19999999" />')
        self.assertEqual(notes_of(text, self.root), [
            "Stufen nach den Reglern, die mit dem Pegel stärker verzerren: Analog-Delay mit Feedback im Master; "
            "Tiefpass mit Resonanz 30 im Master; SATURATION 2 im Kit «K»; Kompressor im Kit «K»; Analog-Delay mit "
            "Feedback im Kit «K»; Tiefpass mit Drive im Kit «K»; Kompressor nach dem Regler der Reihe «a» in Kit «K»; "
            "Kompressor nach dem Regler von Synth «S»; Hochpass mit Resonanz 30 in der Audio-Spur «A»"])

    def test_what_does_not_count(self):
        # A digital delay with feedback, an analog one without, filters open or closed whatever their resonance
        kit = f'<kit presetName="K" lpfMode="24dB"><delay analog="0" /><soundSources>{sound("a")}</soundSources></kit>'
        clip = kit_clip("K", [row(0, V40)]).replace(
            '<delay rate="0x00000000" feedback="0x80000000" /><lpf frequency="0x7FFFFFFF" resonance="0x80000000" />'
            '<hpf frequency="0x80000000" resonance="0x80000000" />',
            '<delay rate="0x00000000" feedback="0x40000000" /><lpf frequency="0x7FFFFFFF" resonance="0x7FFFFFFF" />'
            '<hpf frequency="0x80000000" resonance="0x7FFFFFFF" />')
        self.assertEqual(notes_of(song(kit, clip, children='<delay analog="1" />'), self.root), [])
        # An active filter with little resonance
        clip = kit_clip("K", [row(0, V40)]).replace('<lpf frequency="0x7FFFFFFF" resonance="0x80000000" />',
                                                    '<lpf frequency="0x20000000" resonance="0x90000000" />')
        self.assertEqual(notes_of(song(kit, clip), self.root), [])


class Files(Case):
    def test_card(self):
        loud = self.sample("SAMPLES/loud.wav", wav, pcm(0, 16))
        kit = f'<kit presetName="K"><soundSources>{sound("r", loud)}</soundSources></kit>'
        write(os.path.join(self.root, "SONGS", "A.XML"), song().encode())
        write(os.path.join(self.root, "SONGS", "sub", "b.xml"), song(kit, kit_clip("K", [row(0, V50)])).encode())
        write(os.path.join(self.root, "SONGS", "._A.XML"), b"\0\x05\x16\x07")
        write(os.path.join(self.root, "SONGS", "Broken.XML"), b"<song><instruments></song>")
        out = os.path.join(self.root, "report.md")
        with contextlib.redirect_stdout(io.StringIO()) as console:
            sys.argv = ["baseline_check.py", self.root, "--out", out]
            bc.main()
        text = console.getvalue()
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read(), text)
        self.assertIn("# Baseline-Prüfung: 3 Songs, 2 mit Hinweisen", text)
        self.assertIn("| A | 35 | aus | - | - | ok |", text)
        self.assertIn("| b | 35 | aus | 35 | 50 | 1 |", text)
        self.assertIn("| Broken | - | - | - | - | nicht lesbar |", text)
        self.assertIn("Nicht lesbar: </song> schliesst <instruments>", text)
        self.assertIn("- Reihe «r» in Kit «K»: 50, Sample bis 0,0 dBFS, wirkt wie 50,0", text)
        self.assertNotIn("._A", text)
        # A song away from the card: --card says where the samples are
        away = os.path.join(self.root, "away.XML")
        write(away, song(kit, kit_clip("K", [row(0, V50)])).encode())
        self.assertIn("Sample fehlt", bc.report(bc.song_files([away])))
        self.assertIn("wirkt wie 50,0", bc.report(bc.song_files([away], self.root)))


@unittest.skipUnless(os.path.isdir(os.path.join(KARTE, "SONGS")), "no card copy in geraet/karte")
class CardCopy(unittest.TestCase):
    def test_songs(self):
        text = bc.report(bc.song_files([KARTE]))
        self.assertIn("# Baseline-Prüfung: 2 Songs, 1 mit Hinweisen", text)
        self.assertIn("| New Sitar Grii 10 | 35 | aus | 35 | 50 | 6 |", text)
        self.assertIn("| Rescue | 35 | aus | - | 35 | ok |", text)
        section = text[text.index("## New Sitar Grii 10"):]
        self.assertEqual([line for line in section.splitlines() if line.startswith("- ")], [
            "- Reihe «RADJ_Syn_Bass_Note_Amin_2» in Kit «3L3Ctr0»: 50, Sample bis 0,0 dBFS, wirkt wie 49,6",
            "- Reihe «NA_B_Double-Bass-Shot» in Kit «3L3Ctr0»: 50, Sample bis -1,0 dBFS, wirkt wie 47,2",
            "- Reihe «00DB_Kick_Power_people2» in Kit «3L3Ctr0»: 43, Sample bis -1,0 dBFS, wirkt wie 40,8",
            "- Reihe «LP24_OrgPerc_Kick_01» in Kit «3L3Ctr0»: 50, Sample bis 0,0 dBFS, wirkt wie 49,9",
            "- Reihe «LP24_OrgPerc_Kick_02» in Kit «3L3Ctr0»: 50, Sample bis 0,0 dBFS, wirkt wie 49,9",
            "- Reihe «hihatlong» in Kit «Hihat»: 50, +3,9 dB über 40 (Sample fehlt: SAMPLES/PsyPack/hihatlong.wav)",
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
