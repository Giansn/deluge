#!/usr/bin/env python3
"""Repeated stem and song exports on the v1.3 beta (upstream #4639: stem export crashes after a few songs; #4471:
offline mixdown of a long arrangement; #3309: M123), on the real firmware in the emulator.

The card: make_sd.py's song (--synths synths, the kit, the audio track; 120 BPM: the synth clips 4 bars = 8 s, the
kit's 1 bar = 2 s, the audio clip 2 bars = 4 s) as SONGS/DEFAULT.XML (the startup song) and SONG001 to SONG00n, each
with an arrangement written into the XML (every Output one ClipInstance of 4 bars at bar 0: 8 s), as the firmware
saves it. Per song, as a hand does it:
  clip    song view: SAVE held + RECORD (StemExportType::CLIP)
  drum    the kit's clip (its pad pressed in song view): SAVE held + RECORD (DRUM; the same call as v1.3's
          KIT FX > ACTIONS > EXPORT AUDIO)
  track   arranger (SONG): SAVE held + RECORD (TRACK)
  mixdown arranger, exportMixdown on (Configure Export > Mixdown): SAVE held + RECORD (MIXDOWN)
each export runs inside the RECORD press; the "Export Done" menu is closed with BACK. Then the song browser loads the
next song (not playing: inside the press), and so on (--songs). Options: offline rendering on (the default) or off,
export to silence on or off, the card instant or SDHC-like (every SD wait yields to the task manager), and
SampleRecorder::createNextCluster() failing (INSUFFICIENT_RAM, as when RAM runs short) on chosen calls.

Checked after every export: no crash, freeze (freezeWithError, the E/M codes), fault or hang; the export's state reset
(StemExport::processStarted, UI_MODE_STEM_EXPORT, stopRecording, the audio recorder's recorder and recordingSource,
AudioEngine::firstRecorder, playbackHandler.recording, playback stopped, the UI back to its view); the stems written
(SAMPLES/EXPORTS/<song>/<CLIPS|DRUMS|TRACKS>[-NN]/*.WAV) whole: the RIFF and data sizes match the file, and each one at
least as long as what it exports (a clip's or a row's loop, the arrangement) - not cut short (5 s); the number of stems
the export says it wrote; the heap after each export (a leak: the same export of the same song again leaves less).

Usage: export_repeat_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR [--songs DEFAULT,SONG001,SONG002,SONG001]
          [--exports clip,drum,track,mixdown] [--offline 1|0] [--silence 0|1] [--song-fx 0|1] [--kit-fx 0|1]
          [--sd-latency CMD_US,SECTOR_US|instant]
          [--fail-at N,M] [--fail-each K,L [--fail-in 1,2]] [--synths 1] [--bars 4] [--7seg] [--limit S]
  --fail-at   createNextCluster() calls (counted over the whole run) that fail; --fail-each K,L: those of each export
              (with --fail-in, of those exports only: 1-based in the run's order)
Last line PASS or FAIL; exit 0/1. Results also in <out>/export_repeat.json."""
import argparse
import collections
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.normpath(os.path.join(HERE, ".."))  # beta-1.3/tests
sys.path.insert(0, RIG)
import rig13  # noqa: E402  (sets up the paths of mastertune's rig)
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402
import stress_ui_emu as su  # noqa: E402
from fuzz_ui import B, NullPage  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0  # noqa: E402

KINDS = dict(clip="CLIPS", drum="DRUMS", track="TRACKS", mixdown="TRACKS")
SR = 44100
SECONDS_PER_TICK = 0.5 / 96  # 120 BPM, 96 ticks per quarter note
ARRANGEMENT_TICKS = 1536  # 4 bars


def with_arrangement(xml, ticks=ARRANGEMENT_TICKS):
    """Every Output one ClipInstance (pos 0, 4 bars, its session clip): clipInstances as Output::writeDataToFile()
    writes it. The session clips are in the XML's order, the Outputs too (synths, the kit, the audio track)."""
    start = xml.index("\t<sessionClips>\n")
    clips = re.findall(r"\t\t<(instrumentClip|audioClip)\n(.*?)\t\t</\1>\n", xml[start:], re.S)
    owners = []
    for kind, body in clips:
        m = re.search(r'(?:instrumentPresetName|trackName)="([^"]+)"', body)
        owners.append(m.group(1))
    for index, name in enumerate(owners):
        value = f"0x{0:08X}{ticks:08X}{index:08X}"
        for anchor in (f'\t\t<sound\n\t\t\tpresetName="{name}"\n', f'\t\t<kit\n\t\t\tpresetName="{name}"\n',
                       f'\t\t<audioTrack\n\t\t\tname="{name}"\n'):
            if xml.count(anchor) == 1:
                xml = xml.replace(anchor, f'{anchor}\t\t\tclipInstances="{value}"\n')
                break
        else:
            raise SystemExit(f"no Output for {name}")
    return xml, owners


def clip_seconds(xml):
    """Each session clip's loop length (s) by its owner's name, in the XML's order."""
    start = xml.index("\t<sessionClips>\n")
    out = []
    for kind, body in re.findall(r"\t\t<(instrumentClip|audioClip)\n(.*?)\t\t</\1>\n", xml[start:], re.S):
        name = re.search(r'(?:instrumentPresetName|trackName)="([^"]+)"', body).group(1)
        out.append((name, int(re.search(r'\blength="(\d+)"', body).group(1)) * SECONDS_PER_TICK))
    return out


def wav_info(data):
    """(frames, channels, problem) of a WAV the firmware wrote: RIFF size and the data chunk against the file."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return 0, 0, "no RIFF/WAVE header"
    riff, = struct.unpack_from("<I", data, 4)
    problem = None if riff + 8 == len(data) else f"RIFF size {riff + 8} != file {len(data)}"
    pos, channels, bits, frames = 12, 0, 0, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        if cid == b"fmt ":
            channels, = struct.unpack_from("<H", data, pos + 10)
            bits, = struct.unpack_from("<H", data, pos + 22)
        elif cid == b"data":
            if pos + 8 + size > len(data):
                problem = problem or f"data chunk {size} past the end of the file"
            frames = size // max(1, channels * bits // 8)
            break
        pos += 8 + size + (size & 1)
    if frames is None:
        return 0, channels, problem or "no data chunk"
    return frames, channels, problem


def exports_tree(image):
    """{path: bytes} of every WAV under SAMPLES/EXPORTS."""
    out = {}

    def walk(path):
        try:
            entries = fat32.list_dir(image, path)
        except FileNotFoundError:
            return
        for short, lfn in entries:
            name = lfn or (short[:8].rstrip() + ("." + short[8:].rstrip() if short[8:].strip() else ""))
            if name in (".", "..") or short.startswith("."):
                continue
            p = f"{path}/{name}"
            if name.upper().endswith(".WAV"):
                out[p] = fat32.read_file(image, p)
            elif "." not in name:
                walk(p)
    walk("SAMPLES/EXPORTS")
    return out


class Probe:
    def __init__(self, rig, a):
        self.rig, self.a = rig, a
        emu = rig.emu
        sym = emu.sym
        self.emu, self.sym = emu, sym
        (self.o_started, self.o_stop, self.o_silence, self.o_offline, self.o_mixdown, self.o_done, self.o_total,
         self.o_folder, self.o_recorder, self.o_source, self.o_recording, self.o_yscroll, self.o_clips,
         self.o_output, self.o_type, self.o_songfx, self.o_kitfx) = su.gdb_ints(emu, [
            "list Song::Song",
            "print (int)&((StemExport*)0)->processStarted", "print (int)&((StemExport*)0)->stopRecording",
            "print (int)&((StemExport*)0)->exportToSilence", "print (int)&((StemExport*)0)->renderOffline",
            "print (int)&((StemExport*)0)->exportMixdown", "print (int)&((StemExport*)0)->numStemsExported",
            "print (int)&((StemExport*)0)->totalNumStemsToExport",
            "print (int)&((StemExport*)0)->highestUsedStemFolderNumber",
            "print (int)&((AudioRecorder*)0)->recorder", "print (int)&((AudioRecorder*)0)->recordingSource",
            "print (int)&((PlaybackHandler*)0)->recording", "print (int)&((Song*)0)->songViewYScroll",
            "print (int)&((Song*)0)->sessionClips", "print (int)&((Clip*)0)->output",
            "print (int)&((Output*)0)->type", "print (int)&((StemExport*)0)->includeSongFX",
            "print (int)&((StemExport*)0)->includeKitFX"])
        self.stem = sym["stemExport"]
        self.recorder = sym["audioRecorder"]
        self.first_recorder = sym["_ZN11AudioEngine13firstRecorderE"]
        emu.w8 = getattr(emu, "w8", None) or (lambda addr, v: emu.uc.mem_write(addr, bytes([v & 0xFF])))
        emu.w8(self.stem + self.o_offline, 1 if a.offline else 0)
        emu.w8(self.stem + self.o_silence, 1 if a.silence else 0)
        emu.w8(self.stem + self.o_songfx, 1 if a.song_fx else 0)  # offline: AudioInputChannel::OFFLINE_OUTPUT
        emu.w8(self.stem + self.o_kitfx, 1 if a.kit_fx else 0)
        # createNextCluster() failing on chosen calls (INSUFFICIENT_RAM, Error 1)
        self.calls = 0
        self.calls_at_export = 0
        self.fail_at = {int(x) for x in a.fail_at.split(",") if x}
        self.fail_in = {int(x) for x in a.fail_in.split(",") if x}
        self.fail_each = {int(x) for x in a.fail_each.split(",") if x}
        self.export_number = 0
        self.fails = []
        self.events = collections.deque(maxlen=30)
        self.what = "boot"

        def next_cluster(e):
            self.calls += 1
            if self.calls in self.fail_at or (self.calls - self.calls_at_export in self.fail_each
                                              and (not self.fail_in or self.export_number in self.fail_in)):
                self.fails.append(dict(call=self.calls, at_s=round(e.seconds(), 3), during=self.what))
                return 1
            return None
        emu.intercept(sym.find("_ZN14SampleRecorder17createNextClusterEv"), next_cluster)
        lock = sym["sdRoutineLock"]
        for name, kind in (("_ZN13AudioRecorder15finishRecordingEv", "finishRecording"),
                           ("_ZN14SampleRecorderD2Ev", "~SampleRecorder"), ("f_unlink", "f_unlink")):
            # (SampleRecorder::abort() is inlined in v1.3: the aborts show in self.fails, createNextCluster()'s)
            try:
                emu.intercept(sym.find(name), lambda e, k=kind: self.events.append(
                    (round(e.seconds(), 4), k, e.sym.name_at(e.uc.reg_read(UC_ARM_REG_LR)), e.u8(lock),
                     self.what)) and None)
            except KeyError:
                pass
        emu.uc.ctl_flush_tb()

    # --- state
    def state(self):
        e, s = self.emu, self.stem
        return dict(processStarted=e.u8(s + self.o_started), stopRecording=e.u8(s + self.o_stop),
                    numStemsExported=e.u32(s + self.o_done), totalNumStemsToExport=e.u32(s + self.o_total),
                    folderNumber=struct.unpack("<i", e.uc.mem_read(s + self.o_folder, 4))[0],
                    recorder=e.u32(self.recorder + self.o_recorder),
                    recordingSource=struct.unpack("<i", e.uc.mem_read(self.recorder + self.o_source, 4))[0],
                    firstRecorder=e.u32(self.first_recorder),
                    recording=e.u8(self.rig.v["playbackHandler"] + self.o_recording), playing=self.rig.playing(),
                    uiMode=self.rig.mode(), ui=self.rig.ui_name(), root=self.rig.ui_name(root=True))

    def clip_index_of(self, type_code):
        """The index in sessionClips of the first clip whose Output has this OutputType (KIT 1)."""
        rig, e = self.rig, self.emu
        _, _, memory, count, msize, mstart, esize = rig.heap_offsets
        arr = rig.song() + self.o_clips
        mem, n, size, first, es = (e.u32(arr + o) for o in (memory, count, msize, mstart, esize))
        for i in range(n):
            clip = e.u32(mem + ((first + i) % max(size, 1)) * es)
            if clip and e.u8(e.u32(clip + self.o_output) + self.o_type) == type_code:
                return i
        return None

    # --- the hand
    def press(self, name, settle=0.05, limit_s=10):
        self.rig.button(B[name], True, limit_s=limit_s)
        self.rig.tm(settle, f"{name} held")
        self.rig.button(B[name], False)

    def to_view(self, want, limit=3):
        for _ in range(limit):
            if self.rig.ui_name(root=True) == want:
                return
            self.press("SONG")
            self.rig.tm(0.8, "view transition")
        raise SystemExit(f"could not get to {want} (at {self.rig.ui_name(root=True)})")

    def enter_kit_clip(self):
        self.to_view("sessionView")
        index = self.clip_index_of(1)  # OutputType::KIT
        y = index - struct.unpack("<i", self.emu.uc.mem_read(self.rig.song() + self.o_yscroll, 4))[0]
        if not 0 <= y < 8:
            raise SystemExit(f"the kit's clip (index {index}) is off the grid (y {y})")
        pad = self.emu.sym.find("_ZN12MatrixDriver9padActionElll")
        self.rig.action(f"pad 0,{y}", pad, 0, y, 100)
        self.rig.tm(0.05, "pad held")
        self.rig.action(f"pad 0,{y} off", pad, 0, y, 0)
        self.rig.tm(1.0, "zoom into the kit's clip")
        if self.rig.ui_name(root=True) != "instrumentClipView":
            raise SystemExit(f"not in the kit's clip (at {self.rig.ui_name(root=True)})")

    def export(self, kind):
        """SAVE held + RECORD in the view for kind; returns the emulated seconds the press took."""
        self.calls_at_export = self.calls
        self.export_number += 1
        e = self.emu
        if kind == "clip":
            self.to_view("sessionView")
        elif kind == "drum":
            self.enter_kit_clip()
        else:
            self.to_view("arrangerView")
        e.w8(self.stem + self.o_mixdown, 1 if kind == "mixdown" else 0)
        self.rig.button(B["SAVE"], True)
        self.rig.tm(0.05, "SAVE held")
        _, took = self.rig.button(B["RECORD"], True, limit_s=self.a.limit)  # the export runs inside this press
        self.rig.button(B["RECORD"], False)
        self.rig.button(B["SAVE"], False)
        e.w8(self.stem + self.o_mixdown, 0)
        self.rig.tm(1.0, "after the export")
        closed = 0
        while self.rig.ui_name() not in self.rig.ui_names.values() and closed < 3:  # the "Export Done" menu
            self.press("BACK")
            self.rig.tm(0.3, "menu closed")
            closed += 1
        if kind == "drum":
            self.to_view("sessionView")
        return took / se.CPU_HZ


def check_files(new, kind, expect, owners_s):
    """Problems with the stems one export wrote (new: {path: bytes})."""
    problems, files = [], []
    for path, data in sorted(new.items()):
        frames, channels, problem = wav_info(data)
        seconds = frames / SR
        name = path.rsplit("/", 1)[1]
        need = expect
        if kind == "clip":
            need = next((s for owner, s in owners_s if f"_{owner}_" in name), expect)
        files.append(dict(file=path, bytes=len(data), seconds=round(seconds, 3), need=need, channels=channels))
        if problem:
            problems.append(f"{path}: {problem}")
        if seconds < need * 0.98:
            problems.append(f"{path}: {seconds:.2f} s, short of {need:.2f} s")
    return problems, files


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--songs", default="DEFAULT,SONG001,SONG002,SONG001",
                    help="the songs in turn (DEFAULT: the startup song as loaded), a song change between them")
    ap.add_argument("--exports", default="clip,drum,track,mixdown")
    ap.add_argument("--offline", type=int, default=1, help="offline rendering (StemExport::renderOffline)")
    ap.add_argument("--silence", type=int, default=0, help="export to silence (exportToSilence; the default on the "
                    "Deluge is 1, here 0: each stem stops at its end, 12 s sooner)")
    ap.add_argument("--song-fx", type=int, default=0, help="Configure Export > Song FX (includeSongFX)")
    ap.add_argument("--kit-fx", type=int, default=0, help="Configure Export > Kit FX (includeKitFX)")
    ap.add_argument("--sd-latency", default="1000,42.67")
    ap.add_argument("--fail-at", default="")
    ap.add_argument("--fail-each", default="", help="calls counted from each export's start that fail, e.g. 30")
    ap.add_argument("--fail-in", default="", help="--fail-each only in these exports (1-based, in the run's order)")
    ap.add_argument("--synths", type=int, default=1)
    ap.add_argument("--bars", type=int, default=4, help="the arrangement's length (every Output one instance)")
    ap.add_argument("--7seg", dest="seven", action="store_true", help="the 7-segment display instead of the OLED")
    ap.add_argument("--limit", type=float, default=0, help="emulated seconds an export may take (default: 60 with "
                    "offline rendering, which renders faster than real time, else 300)")
    ap.add_argument("--leak-kb", type=int, default=16, help="SDRAM a repeated export may lose before it's a leak")
    a = ap.parse_args()
    a.limit = a.limit or (60 if a.offline else 300)
    os.makedirs(a.out, exist_ok=True)
    files, lengths = make_sd.samples()
    plain = make_sd.song_xml(lengths, 1, a.synths, False, False, False)
    xml, owners = with_arrangement(plain, a.bars * 384)
    owners_s = clip_seconds(plain)
    songs = [s for s in a.songs.split(",") if s]
    files["SONGS/DEFAULT.XML"] = xml.encode()
    for name in set(songs) - {"DEFAULT"}:
        files[f"SONGS/{name}.XML"] = xml.encode()
    image = os.path.join(a.out, "export.img")
    fat32.build(image, files)
    kit_s = next(s for owner, s in owners_s if owner == "KIT")
    expect = dict(clip=0, drum=kit_s, track=a.bars * 384 * SECONDS_PER_TICK, mixdown=a.bars * 384 * SECONDS_PER_TICK)
    res = dict(elf=os.path.basename(a.elf), options=vars(a), exports=[], problems=[])
    rig = probe = None
    try:
        rig = rig13.Rig13(a.elf, a.tools, a.build, image, sd_latency=a.sd_latency, oled=not a.seven)
        probe = Probe(rig, a)
        null = NullPage(rig.emu)
        rig.load_startup_song()
        rig.tm(0.5, "settle")
        before = exports_tree(image)
        for i, song in enumerate(songs):
            if i or song != "DEFAULT":
                probe.what = f"load {song}"
                probe.to_view("sessionView")
                ch = rig.change_song(song, 0.5)
                if ch["loaded"].upper() != song.upper():
                    raise SystemExit(f"{song} not loaded (song {ch['loaded']!r})")
            for kind in [k for k in a.exports.split(",") if k]:
                probe.what = f"{song} {kind}"
                fails0 = len(probe.fails)
                took = probe.export(kind)
                null.check(probe.what)
                st = probe.state()
                after = exports_tree(image)
                new = {p: d for p, d in after.items() if before.get(p) != d}
                before = after
                failed_here = probe.fails[fails0:]
                fproblems, flist = check_files(new, kind, expect[kind], owners_s)
                reset = [f"{k}={st[k]}" for k in ("processStarted", "stopRecording", "recorder", "recordingSource",
                                                  "firstRecorder", "recording", "playing", "uiMode") if st[k]]
                if st["ui"] != st["root"]:
                    reset.append(f"ui {st['ui']} over {st['root']}")
                problems = [f"not reset: {', '.join(reset)}"] if reset else []
                problems += fproblems  # the stems there are must be whole (an aborted one is deleted)
                if not failed_here:
                    if st["numStemsExported"] != st["totalNumStemsToExport"] or len(new) != st["totalNumStemsToExport"]:
                        problems.append(f"{len(new)} files, exported {st['numStemsExported']} of "
                                        f"{st['totalNumStemsToExport']}")
                row = dict(song=song, kind=kind, took_s=round(took, 2), heap=rig.heap(), state=st, files=flist,
                           cluster_fails=failed_here, problems=problems,
                           popups=[p for p in rig.popups[-6:] if p[1].startswith("button")])
                res["exports"].append(row)
                print(f"{song:8} {kind:8} {took:6.1f} s  {len(new)} files "
                      f"{[f['seconds'] for f in flist]}  sdram {row['heap']['sdram_free'] // 1024} KB, internal "
                      f"{row['heap']['internal_free'] // 1024} KB  fails {[f['call'] for f in failed_here]}"
                      f"{'  PROBLEMS ' + '; '.join(problems) if problems else ''}", flush=True)
                res["problems"] += [f"{song} {kind}: {p}" for p in problems]
    except (su.Stop, SystemExit) as ex:
        res["stopped"] = f"{ex} during {probe.what if probe else 'boot'}"
        try:
            res["state_at_stop"] = probe.state() if probe else None
        except Exception as ex2:  # noqa: BLE001
            res["state_at_stop"] = f"unreadable: {ex2}"
    if rig:
        res["rig_problems"] = rig.problems
        res["error_popups"] = rig.error_popups()
        res["invalid"] = [dict(page=hex(k[0]), at=k[1], during=k[2], n=n) for k, n in rig.invalid.most_common(10)]
    if probe:
        res["cluster_calls"] = probe.calls
        res["cluster_fails"] = probe.fails
        res["recorder_events"] = list(probe.events)
        res["null_writes"] = null.writes if rig else []
    # A leak: the same export of the same song again (after other songs in between) with less SDRAM or internal RAM
    seen = {}
    for row in res["exports"]:
        key = (row["song"], row["kind"])
        if key in seen and not row["cluster_fails"]:
            for region in ("sdram_free", "internal_free"):
                lost = seen[key]["heap"][region] - row["heap"][region]
                if lost > a.leak_kb * 1024:
                    res["problems"].append(f"{key[0]} {key[1]} again: {region} {lost // 1024} KB less")
        seen[key] = row
    json.dump(res, open(os.path.join(a.out, "export_repeat.json"), "w"), indent=1, default=str)
    for p in (res.get("rig_problems") or []):
        print(f"problem: {p.get('kind')} {p.get('detail')} during {p.get('what')} at {p.get('at_s')} s")
    if res.get("stopped"):
        print(f"stopped: {res['stopped']}")
        print(f"state then: {res.get('state_at_stop')}")
        for ev in res.get("recorder_events", [])[-12:]:
            print("  ", ev)
    for p in res["problems"]:
        print(f"problem: {p}")
    print(f"createNextCluster() calls {res.get('cluster_calls')}, failed {[f['call'] for f in res.get('cluster_fails', [])]}"
          f", error popups {res.get('error_popups')}, invalid {res.get('invalid')}, null writes "
          f"{len(res.get('null_writes') or [])}")
    ok = not res.get("stopped") and not res["problems"] and not res.get("rig_problems") and \
        len(res["exports"]) == len(songs) * len([k for k in a.exports.split(",") if k])
    if os.path.exists(image) and ok:
        os.remove(image)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
