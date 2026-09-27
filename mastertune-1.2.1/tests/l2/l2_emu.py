#!/usr/bin/env python3
"""The L2 test versions in the emulator (the song harness of ../song, with its model of the PL310 L2 controller):
what the firmware does with the L2 at boot, and the cache maintenance before a DMA transfer.

  boot:   the controller is set up once, after the L1: disabled, emptied (invalidate by way), data locked out
          (D lockdown 0xFF...), instructions open, enabled. Data version: prefetch on (aux control bits 28, 29) and
          data unlocked once at the end of the boot, right after cleaning and invalidating all ways and a sync. Code
          version: data locked all along. v13/v14/v15: the controller is never touched.
  OLED:   a transfer through the real oledSelectingComplete() (the path of every OLED frame): both L2 versions clean
          and invalidate every line of the image in the L2 (and sync) before the DMA starts (the code version too:
          a speculative instruction fetch can bring a line of a DMA buffer into the L2).
  ranges: invalidate_range_all_caches() on unaligned ranges: each line exactly once, then a sync.
  chainloader: L2CacheCleanFlushAllAndDisable(): cleaned and invalidated by way, synced, disabled (only in builds
          with ENABLE_SYSEX_LOAD, which release builds leave off).

The SD transfers can't run here (the harness reads and writes the card image below FatFS); their maintenance is the
same function, checked in the source and by 'ranges'.

Usage: l2_emu.py <deluge.elf> <v13 | v14 | v15 | l2i | l2d>
  TOOLS: the toolchain prefix (arm-none-eabi-), if the ELF is not in a firmware tree's build/Release"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import make_sd  # noqa: E402
import song_emu  # noqa: E402

LINE = 32
UNLOCKED, LOCKED = 0, 0xFFFFFFFF


def build_sd(path):
    make_sd.kit = lambda lengths: ("", "")
    make_sd.audio_track = lambda lengths: ("", "")
    make_sd.drone = lambda *args, **kwargs: ""
    env = dict(attack=make_sd.knob(0), decay=make_sd.knob(20), sustain=make_sd.knob(30), release=make_sd.knob(10))
    synth = make_sd.synth("L2", make_sd.SAW, make_sd.SQUARE, 1, {}, env, make_sd.PAD_ENV2, [], notes_octave=1)
    make_sd.synths = lambda: [synth]
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 1).encode()
    make_sd.fat32.build(path, files)


class Check:
    def __init__(self):
        self.failed = 0

    def __call__(self, ok, what):
        print(("  ok    " if ok else "  FAIL  ") + what)
        self.failed += not ok


def buffer_address(emu):
    """A RAM buffer to send and to maintain (what the OLED gets is not checked here)"""
    for name in ("spareRenderingBuffer", "miscStringBuffer"):
        try:
            return emu.sym[name]
        except KeyError:
            continue
    raise KeyError("no buffer symbol")


def names(log):
    return [n for n, _, _ in log]


def pa_lines(log, op="clean_inv_pa"):
    return [v for n, v, _ in log if n == op]


def check_boot(check, log, variant):
    if variant in ("v13", "v14", "v15"):
        check(not log, f"{variant}: the L2 controller is never touched ({len(log)} accesses)")
        return
    enable = [i for i, (n, v, _) in enumerate(log) if n == "control" and v & 1]
    check(len(enable) == 1, f"enabled once ({len(enable)})")
    if not enable:
        return
    before = log[:enable[0]]
    seq = names(before)
    check(seq[:2] == ["control", "inv_way"] and before[0][1] == 0 and before[1][1] == 0xFF,
          "disabled, then all 8 ways invalidated, before anything else: " + " ".join(seq))
    d = [v for n, v, _ in before if n == "d_lockdown"]
    i = [v for n, v, _ in before if n == "i_lockdown"]
    check(d == [LOCKED] and i == [UNLOCKED], f"at enable: data locked out {[hex(x) for x in d]}, "
          f"instructions open {[hex(x) for x in i]}")
    aux = [v for n, v, _ in before if n == "aux_control"]
    if variant == "l2i":
        check(not aux, "code version: prefetch untouched")
        later = [v for n, v, _ in log[enable[0]:] if n in ("d_lockdown", "i_lockdown")]
        check(not later, "code version: data stays locked out")
    else:
        check(len(aux) == 1 and aux[0] & 0x30000000 == 0x30000000, f"data version: prefetch on {[hex(x) for x in aux]}")
        after = log[enable[0] + 1:]
        unlock = [k for k, (n, v, _) in enumerate(after) if n == "d_lockdown"]
        check(len(unlock) == 1 and after[unlock[0]][1] == UNLOCKED, "data version: data unlocked once")
        if unlock:
            before_unlock = [(n, v) for n, v, _ in after[max(0, unlock[0] - 2):unlock[0]]]
            check(before_unlock == [("clean_inv_way", 0xFF), ("sync", 0)],
                  f"  ... right after cleaning and invalidating all ways and a sync: {before_unlock}")


def check_oled(check, emu, variant):
    sym = emu.sym
    log = emu.l2_log
    start = len(log)
    image = buffer_address(emu) + 8  # not line-aligned on purpose
    # a frame queued as the firmware does it, then the moment its chip select is done
    emu.call(sym.find("enqueueSPITransfer"), 0, image)
    emu.uc.mem_write(sym["spiTransferQueueReadPos"], bytes([(emu.u8(sym["spiTransferQueueWritePos"]) - 1) & 31]))
    emu.call(sym.find("oledSelectingComplete"))
    new = log[start:]
    dma = [(k, v) for k, (n, v, _) in enumerate(new) if n == "oled_dma"]
    check(len(dma) == 1, f"one OLED DMA start ({len(dma)})")
    if not dma:
        return
    k, (address, size) = dma[0]
    check(size == 768, f"  768 bytes (128 x 48 pixels) from {address:#x}: {size}")
    before = new[:k]
    lines = pa_lines(before)
    want = list(range(address & ~(LINE - 1), address + size, LINE))
    if variant in ("l2i", "l2d"):
        check(sorted(lines) == want, f"{variant}: every line of the image cleaned and invalidated in the L2 "
              f"({len(lines)} of {len(want)})")
        last_pa = max(i for i, (n, _, _) in enumerate(before) if n.endswith("_pa")) if lines else -1
        check(any(n == "sync" for n, _, _ in before[last_pa + 1:]), "  ... then synced, before the DMA starts")
    else:
        check(not lines, f"{variant}: no L2 maintenance ({len(lines)})")


def check_ranges(check, emu):
    function = emu.sym.find("invalidate_range_all_caches")
    base = buffer_address(emu)
    for start, end in ((base + 5, base + 5 + 100), (base, base + 64), (base + 31, base + 33), (base + 32, base + 33)):
        mark = len(emu.l2_log)
        emu.call(function, start, end)
        new = emu.l2_log[mark:]
        lines = pa_lines(new)
        want = list(range(start & ~(LINE - 1), end, LINE))
        check(lines == want and new[-1][0] == "sync" and len(new) == len(want) + 1,
              f"invalidate_range_all_caches(+{start - base}, +{end - base}): lines {[hex(x - base) for x in lines]}, "
              f"then {new[-1][0]}")


def check_chainloader(check, emu):
    try:
        emu.sym.find("L2CacheCleanFlushAllAndDisable")
    except KeyError:
        print("  skipped: the chainloader is not in release builds (ENABLE_SYSEX_LOAD is off)")
        return
    mark = len(emu.l2_log)
    emu.call(emu.sym.find("L2CacheCleanFlushAllAndDisable"))
    new = [(n, v) for n, v, _ in emu.l2_log[mark:]]
    check(new == [("clean_inv_way", 0xFF), ("sync", 0), ("control", 0)],
          f"chainloader: cleaned and invalidated by way, synced, disabled: {new}")


def main():
    elf, variant = sys.argv[1], sys.argv[2]
    out = os.getcwd()
    sd = os.path.join(out, f"l2-{variant}.img")
    build_sd(sd)
    tools = os.environ.get("TOOLS") or os.path.join(os.path.dirname(os.path.abspath(elf)),
                                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/"
                                                    "arm-none-eabi-")
    emu = song_emu.Emulator(elf, sd, tools, os.environ.get("BLOCKCOUNT_DIR", HERE), lambda s: None)
    song_emu.setup_sd(emu)
    song_emu.boot(emu)
    song_emu.load_startup_song(emu)
    player = song_emu.Player(emu)
    player.start()
    player.play(44100 // 2)
    check = Check()
    print(f"== {variant}: {os.path.basename(elf)}")
    print("boot:")
    check_boot(check, list(emu.l2_log), variant)
    print("OLED:")
    check_oled(check, emu, variant)
    if variant in ("l2i", "l2d"):
        print("ranges:")
        check_ranges(check, emu)
        print("chainloader:")
        check_chainloader(check, emu)
    print(f"{'all ok' if not check.failed else str(check.failed) + ' FAILED'}")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
