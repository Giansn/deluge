#!/usr/bin/env python3
"""Old file (no timeChange): synth A has an LFO on its delay rate, synth B not. Saved right after loading (nothing
rendered) and after playing: which timeChange attributes the firmware writes."""
import os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../song"))
import make_sd, song_emu, fat32

def build(path):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda *args, **kwargs: ""
    k = make_sd.knob
    env = dict(attack=k(0), decay=k(20), sustain=k(20), release=k(5))
    a = make_sd.synth("SYNA", make_sd.SAW, make_sd.SQUARE, 1, dict(delayFeedback=k(25)), env, make_sd.PAD_ENV2,
                      [("velocity", "volume", 25), ("lfo1", "delayRate", 20)])
    b = make_sd.synth("SYNB", make_sd.SAW, make_sd.SQUARE, 1, dict(delayFeedback=k(25)), env, make_sd.PAD_ENV2,
                      [("velocity", "volume", 25)])
    make_sd.synths = lambda: [a, b]
    files, lengths = make_sd.samples()
    files = {p: d for p, d in files.items() if not p.startswith("SAMPLES/")}
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 2).encode()
    fat32.build(path, files)

def run(elf, play_bars):
    sd = f"tc{play_bars}.img"
    build(sd)
    tools = os.path.join(os.path.dirname(os.path.abspath(elf)), "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    emu = song_emu.Emulator(elf, sd, tools, os.environ.get("BLOCKCOUNT_DIR", os.getcwd()), lambda s: None)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    song_emu.load_startup_song(emu)
    if play_bars:
        player = song_emu.Player(emu)
        player.start()
        player.play(play_bars * 88200)
        emu.call(emu.sym.find("_ZN15PlaybackHandler11endPlaybackEv"))
    out = f"written{play_bars}.xml"
    song_emu.write_back_song(emu, out)
    xml = open(out, encoding="utf-8", errors="replace").read()
    for name in ("SYNA", "SYNB"):
        m = re.search(r'<sound\b[^>]*presetName="%s".*?</sound>' % name, xml, re.S)
        d = re.search(r'<delay\b[^>]*/>', m.group(0), re.S) if m else None
        tc = re.search(r'timeChange="(\d)"', d.group(0)) if d else None
        print(f"  play {play_bars} bar(s): {name} delay timeChange = {tc.group(1) if tc else 'not written'}")
    songd = re.search(r'<song\b.*?<delay\b[^>]*/>', xml, re.S)
    # the song's own delay: the <delay> directly under <song> (global effectable)
    sd_attrs = re.findall(r'\n\t<delay\b[^>]*/>', xml)
    for s in sd_attrs:
        tc = re.search(r'timeChange="(\d)"', s)
        print(f"  play {play_bars} bar(s): song delay timeChange = {tc.group(1) if tc else 'not written'}")

elf = sys.argv[1]
for bars in (0, 1):
    run(elf, bars)
