#!/usr/bin/env python3
"""«New Sitar Grii 10» im Emulator: der Song von der Karte (geraet/karte) mit einer Grundstimmung, gespielt und gemessen.

Nutzt die Emulator-Umgebung von tests/song (song_emu.py, fat32.py) aus dem Entwicklungs-Branch:
- Kartenabbild: SONGS/DEFAULT.XML = der Song, alle Samples aus karte/SAMPLES, CommunityFeatures.XML mit masterTune.
  Die drei fehlenden Samples des Kits Hihat (SAMPLES/PsyPack/hihat*.wav, im CSV ohne Angaben) werden ersetzt:
  Länge aus endSamplePos im Song, Format wie die übrigen 11 Samples des Kits (Stereo, 24 Bit, 44,1 kHz),
  Inhalt abklingendes Rauschen.
- --all-kits: die Clips von 3L3Ctr0, 014 CR-78 und KIT1 spielen zusätzlich (isPlaying="1"), gespeichert sind nur
  Hihat, Guiro, 170 Sitar 2 und Oboe aktiv.
- Messung wie run.sh (--init-sounds, --seed 1): 1 Takt Vorlauf, dann --bars Takte (140 BPM), measure() mit
  profile_by_function(). Pro Spur zusätzlich die Renderzeit, die die Firmware selbst misst (profiler::timingOutputs an,
  profiler::outputTicks[], OS-Timer 0 = emulierte Zeit), wie der Profiler am Gerät.

Usage: sitar_emu.py <deluge.elf> <karte> <out> --song-dir <tests/song> --build <dir mit blockcount.so>
                    [--tenths 4320] [--all-kits] [--culling] [--bars 4] [--window 128]
"""
import argparse
import collections
import json
import os
import re
import struct
import subprocess
import sys

import numpy as np

SONG_BPM = 140
SONG_BAR = 44100 * 60 * 4 // SONG_BPM  # 75'600 Samples
EXTRA_KITS = ("3L3Ctr0", "014 CR-78", "KIT1")
TYPES = {0: "S", 1: "K", 2: "M", 3: "C", 4: "A"}


def wav24_stereo(frames):
    pcm = (np.clip(frames, -1, 1) * 8388607).astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 44100 * 6, 6, 24) + b"data" + struct.pack("<I", len(pcm)) + pcm)


def card_files(karte, tenths, all_kits):
    files = {}
    for dirpath, _, names in os.walk(os.path.join(karte, "SAMPLES")):
        for name in names:
            full = os.path.join(dirpath, name)
            files[os.path.relpath(full, karte).replace(os.sep, "/")] = open(full, "rb").read()
    xml = open(os.path.join(karte, "SONGS", "New Sitar Grii 10.XML"), encoding="utf-8").read()
    rng = np.random.default_rng(1)
    replaced = {}
    for path in re.findall(r'fileName="(SAMPLES/PsyPack/hihat[a-z]*\.wav)"', xml):
        if path in files:
            continue
        zone = xml[xml.find(path):xml.find("</sound>", xml.find(path))]
        length = int(re.search(r'endSamplePos="(\d+)"', zone).group(1))
        t = np.arange(length) / 44100
        frames = rng.standard_normal((length, 2)) * 0.3 * np.exp(-t / (length / 44100 / 4))[:, None]
        files[path] = wav24_stereo(frames)
        replaced[path] = length
    song_names = list(dict.fromkeys(re.findall(r'<sound\b[^>]*?\b(?:presetName|name)="([^"]+)"', xml)))
    if all_kits:
        def activate(m):
            tag = m.group(0)
            name = re.search(r'instrumentPresetName="([^"]*)"', tag)
            if name and name.group(1).strip() in EXTRA_KITS:
                tag = tag.replace('isPlaying="0"', 'isPlaying="1"')
            return tag
        s, e = xml.find("<sessionClips>"), xml.find("</sessionClips>")
        xml = xml[:s] + re.sub(r"<instrumentClip\b[^>]*>", activate, xml[s:e]) + xml[e:]
    files["SONGS/DEFAULT.XML"] = xml.encode()
    files["CommunityFeatures.XML"] = (f'<?xml version="1.0" encoding="UTF-8"?>\n<runtimeFeatureSettings>\n'
                                      f'\t<setting name="masterTune" value="{tenths}" />\n'
                                      f'</runtimeFeatureSettings>\n').encode()
    playing = [re.search(r'instrumentPresetName="([^"]*)"', t).group(1).strip()
               for t in re.findall(r"<instrumentClip\b[^>]*>", xml[xml.find("<sessionClips>"):])
               if 'isPlaying="1"' in t and "instrumentPresetName" in t]
    return files, replaced, song_names, playing


def gdb_offsets(tools, elf, expressions):
    out = subprocess.run([tools + "gdb", "-batch"] + [x for e in expressions for x in ("-ex", f"print {e}")] + [elf],
                         capture_output=True, text=True).stdout
    values = [int(v) for v in re.findall(r"^\$\d+ = (\d+)$", out, re.M)]
    if len(values) != len(expressions):
        raise SystemExit(f"gdb: {expressions}: {out[-400:]}")
    return values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("karte")
    ap.add_argument("out")
    ap.add_argument("--song-dir", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--tools")
    ap.add_argument("--tenths", type=int, default=4320)
    ap.add_argument("--all-kits", action="store_true")
    ap.add_argument("--culling", action="store_true")
    ap.add_argument("--bars", type=float, default=4)
    ap.add_argument("--window", type=int, default=128,
                    help="samples per AudioEngine::routine() call (the DMA shows window - 1 free samples); v16 renders "
                         "4-8 a call when idle (patch 0053, found in the emulator)")
    args = ap.parse_args()
    sys.path.insert(0, args.song_dir)
    import fat32  # noqa: E402
    import song_emu as E  # noqa: E402
    tools = args.tools or os.path.join(os.path.dirname(os.path.abspath(args.elf)),
                                       "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(args.out, exist_ok=True)

    def log(s):
        print(s, flush=True)

    files, replaced, song_names, playing = card_files(args.karte, args.tenths, args.all_kits)
    log(f"card: {len(files)} files, replaced {replaced}; clips playing: {', '.join(playing)}")
    image = os.path.join(args.out, "sd.img")
    fat32.build(image, files)
    emu = E.Emulator(args.elf, image, tools, args.build, log)
    # As in tests/retune/retune_emu.py: a time-stretched voice that is also resampled writes to address 0 (a firmware
    # bug); page 0 takes the write here, which is counted and undone after each VoiceSample::render()
    emu.uc.mem_protect(0, 0x1000, E.UC_PROT_ALL)
    null_writes = [0]
    returns = set()

    def at_return(e):
        if e.u32(0):
            null_writes[0] += 1
            e.w32(0, 0)

    def at_render(e):
        lr = e.uc.reg_read(E.UC_ARM_REG_LR) & ~1
        if lr not in returns:
            returns.add(lr)
            e.intercept(lr, at_return)
    E.setup_sd(emu)
    E.init_sounds(emu)
    E.boot(emu)
    emu.w32(emu.sym["jcong"], 1)
    tuned = emu.u32(emu.sym.find("_ZN10MasterTune12_GLOBAL__N_18tenthsHzE"))
    log(f"master tune after boot (CommunityFeatures.XML): {tuned / 10:g} Hz")
    if tuned != args.tenths:
        raise SystemExit("the master tune from CommunityFeatures.XML didn't arrive")
    E.load_startup_song(emu)
    emu.intercept(emu.sym.find("_ZN11VoiceSample6renderE"), at_render)

    # The song's outputs in the order Song::renderAudio() numbers them (profiler::currentOutput)
    first_output, o_next, o_name, o_type, s_mem = gdb_offsets(tools, args.elf, [
        "(int)&((struct Song*)0)->firstOutput", "(int)&((struct Output*)0)->next",
        "(int)&((struct Output*)0)->name", "(int)&((struct Output*)0)->type", "(int)&((struct String*)0)->stringMemory"])
    outputs = []
    o = emu.u32(emu.u32(emu.sym["currentSong"]) + first_output)
    while o:
        p = emu.u32(o + o_name + s_mem)
        name = bytes(emu.uc.mem_read(p, 64)).split(b"\0")[0].decode(errors="replace") if p else ""
        kind = emu.uc.mem_read(o + o_type, 1)[0]
        label = f"{TYPES.get(kind, '?')} {name.strip()}"
        outputs.append(label if label not in outputs else f"{label} ({outputs.count(label) + 1})")
        o = emu.u32(o + o_next)
    emu.uc.mem_write(emu.sym["_ZN8profiler13timingOutputsE"], b"\x01")
    ticks_at, _ = emu.sym.by_name["_ZN8profiler11outputTicksE"]

    def ticks():
        return np.frombuffer(bytes(emu.uc.mem_read(ticks_at, 4 * len(outputs))), "<u4").astype(np.float64)

    player = E.Player(emu, culling=args.culling)
    snaps = []
    play = player.play

    def play_and_snap(samples_wanted, record=None):
        if record is not None:
            snaps.append(ticks())
        play(samples_wanted, record)
        if record is not None:
            snaps.append(ticks())
    player.play = play_and_snap
    if args.window != 128:  # Player.window() sets 127 free samples, then calls routine(): fewer free samples here
        call = emu.call

        def call_with_window(address, *a, **k):
            if address == player.routine and emu.dma_free:
                emu.dma_free = args.window - 1
            return call(address, *a, **k)
        emu.call = call_with_window
    player.start()
    if args.window == 128:
        result = E.measure(emu, player, SONG_BAR / E.BAR, args.bars * SONG_BAR / E.BAR, args.out, log, song_names)
    else:  # measure() takes full windows as 128 samples: here only the totals and the profile
        player.play(SONG_BAR)
        emu.bc.bc_reset_counts()
        windows = []
        player.play(int(args.bars * SONG_BAR), windows)
        samples = sum(w[1] for w in windows)
        per_block = sum(w[0] for w in windows) / samples * 128
        areas = collections.Counter()
        for name, n in E.profile_by_function(emu).items():
            areas[E.area_of(name)] += n / samples * 128
        result = dict(samples=samples, windows=len(windows), mean_window=samples / len(windows),
                      instructions_per_128=per_block, cpu_percent=per_block / E.CYCLES_PER_BLOCK * 100,
                      culls=dict(player.culls), voices=dict(max=max(w[2] for w in windows)),
                      areas={a: dict(per_128=v, cpu_percent=v / E.CYCLES_PER_BLOCK * 100)
                             for a, v in areas.most_common()})
        log(f"measured: {len(windows)} windows of {samples / len(windows):.1f} samples on average, "
            f"{per_block:,.0f} instructions per 128 samples = {result['cpu_percent']:.1f}% CPU")
    instr_per_tick = E.CPU_HZ / E.PERIPHERAL_HZ
    per_128 = (snaps[1] - snaps[0]) * instr_per_tick / result["samples"] * 128
    result["outputs"] = {name: dict(per_128=float(v), cpu_percent=float(v / E.CYCLES_PER_BLOCK * 100),
                                    percent_of_routine=float(v / result["instructions_per_128"] * 100))
                         for name, v in zip(outputs, per_128)}
    result["master_tune_hz"] = tuned / 10
    result["window"] = args.window
    result["clips_playing"] = playing
    result["replaced_samples"] = replaced
    result["null_writes"] = null_writes[0]
    json.dump(result, open(os.path.join(args.out, "result.json"), "w"), indent=1)
    if args.window == 128:
        E.report(result, log)
    log("\nper output (render time as the firmware measures it, OS timer 0; per 128 samples, % CPU, % of routine):")
    for name, v in sorted(result["outputs"].items(), key=lambda kv: -kv[1]["per_128"]):
        log(f"  {name:<16} {v['per_128']:>11,.0f}  {v['cpu_percent']:6.1f}%  {v['percent_of_routine']:5.1f}%")
    five = [n for n in result["outputs"] if n[2:] in ("3L3Ctr0", "Hihat", "Guiro", "014 CR-78", "KIT1")]
    log(f"  the five kits ({len(five)}): {sum(result['outputs'][n]['cpu_percent'] for n in five):.1f}% CPU, "
        f"{sum(result['outputs'][n]['percent_of_routine'] for n in five):.1f}% of routine; null writes {null_writes[0]}")
    os.remove(image)


if __name__ == "__main__":
    main()
