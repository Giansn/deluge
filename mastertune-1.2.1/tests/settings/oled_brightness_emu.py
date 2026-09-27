#!/usr/bin/env python3
"""Settings > Defaults > OLED brightness (mastertune-v18), on the real firmware in the emulator (the harness of
../song), with the OLED's side modelled: the SSD1309 on RSPI0 (8-bit bytes written to SPDR, the image by the DMA),
its D/C line and chip select through the PIC.

The contrast (command 0x81 + value, D/C low) was 0xFF, sent once by oledMainInit() at boot. Now the menu's levels 1-10
give 1, 4, 14, 29, 51, 79, 114, 155, 202, 255; the command goes through the SPI transfer queue between two images
(destination OLED_CODE_FOR_COMMAND): oledRoutine() sends D/C low before the selection, oledSelectingComplete()
writes the two bytes without the DMA, then D/C high and the deselection. CommunityFeatures.XML keeps it as the entry
oledContrast (missing: 255), read at boot, which queues the command then.

The model: the emulated Deluge has an OLED (the OLED DMA channel's CHCFG as the firmware checks it; RSPI's TEND set).
The PIC: every byte the firmware puts into the PIC's ring is taken when the ring is flushed (uartFlushIfNotSending()),
in order; the OLED's messages among them (247-251, told from pad data by where the code writing them comes from, by
the ELF's debug info: setupOLED(), oledMainInit(), oledRoutine(), sendOledCommand()) set D/C and the chip select, and
248-251 are echoed into the PIC's receive ring (the firmware waits for 248/249's echo). The image's DMA: each start
(CHCTRL SETEN) is logged with the PIC's state then, and oledTransferComplete() runs as the DMA's interrupt 614 us later
(768 bytes at 10 MHz; 20 ms in step 4, to hold it in flight). The CV DAC shares the queue and RSPI0: its 32-bit
messages get their receive interrupt (cvSPITransferComplete()) 3.2 us later.

Checks (a card without CommunityFeatures.XML, the song DEFAULT.XML):
1. boot with the OLED: oledMainInit()'s 0x81 0xFF; no other contrast command; the menu on 10 and shown (isRelevant())
2. images go out: every DMA start with the OLED selected and D/C high, 768 bytes from one of the canvases, never two
   at once; no byte written to the SPI while a DMA runs; the queue empty and idle afterwards
3. Settings open, the encoder (Integer::selectEncoderAction()) from 10 down by 7: level 3, the OLED gets 0x81 14 with
   D/C low, selected, no DMA running; the PIC got D/C low before the selection, D/C high before the deselection;
   images after it with D/C high. Three quick turns (-1, -1, +4, the tasks not run between): 0x81 4, 0x81 1, 0x81 51
4. up to level 6 (0x81 79); a new image, and while its DMA runs a turn back to 5: nothing written until it's done,
   then 0x81 51
5. the menu left: CommunityFeatures.XML says oledContrast 51
6. restart: oledMainInit()'s 0x81 0xFF, then (the file read) 0x81 51 through the queue, D/C low; the menu on 5;
   images as in 2
7. the 7-segment Deluge (no OLED) on the same card: the item not shown; 51 read; a new value (level 8) sends nothing
   (no SPI byte, nothing queued) but is saved: 155
8. a damaged entry, a card whose CommunityFeatures.XML says oledContrast "abc" (stringToInt() gives 0): it counts as
   missing, the contrast 255 and the menu on 10 (not the darkest, 1), only oledMainInit()'s 0x81 0xFF; Settings left:
   saved as 255. The same boot with the entry "", "0", "-5", "-2147483648", "99999": 255 each time; with "1": 1
   (readSetting() is inlined into the file's reading, so each value is a boot)
Usage: oled_brightness_emu.py <deluge.elf> [--tools PREFIX] [--out DIR]   (BLOCKCOUNT_DIR: where blockcount.so is)
Exit status 0 when every check passes."""
import argparse
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "song"))
import fat32  # noqa: E402
import make_sd  # noqa: E402
import song_emu as se  # noqa: E402
from unicorn import UC_HOOK_MEM_WRITE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0  # noqa: E402

MENU_VALUE_OFFSET = 12  # Integer's value_ (OledBrightness::readCurrentValue(): str r4, [r0, #12])
CONTRAST = [1, 4, 14, 29, 51, 79, 114, 155, 202, 255]  # Per level 1-10
IMAGE_BYTES = 128 * 48 // 8
DMA_US = IMAGE_BYTES * 8 / 10e6 * 1e6  # 614 us at the SPI's 10 MHz
# The OLED's code (oled.c, oled_low_level.c; the line info is thin there, the inline chain's names are enough)
OLED_WRITERS = ("setupOLED", "oledMainInit", "oledRoutine", "sendOledCommand", "oledSelectingComplete",
                "oledLowLevelTimerCallback")
DESELECT, SELECT, DC_LOW, DC_HIGH = 249, 248, 250, 251

failures = 0
checks = 0


def check(what, ok, detail=""):
    global failures, checks
    checks += 1
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {what}" + (f": {detail}" if detail else ""), flush=True)


class OledSide:
    """The OLED, its PIC-driven D/C and chip select, and the image's DMA, as the firmware sees them."""

    def __init__(self, emu, oled=True):
        self.emu = emu
        sym = emu.sym
        uc = emu.uc
        self.dma_base = dma = se.dmac_channel_base(se.OLED_SPI_DMA_CHANNEL)
        if oled:  # An OLED Deluge: the OLED DMA channel's CHCFG as deluge_main() checks it (0b1101000 | channel)
            for i, b in enumerate(struct.pack("<I", 0x68 | se.OLED_SPI_DMA_CHANNEL)):
                emu.mmio[dma + 0x2C + i] = b
        # RSPI0's SPSR: TEND too (R_RSPI_WaitEnd()), besides SPTEF and SPRF
        emu.readers[se.RSPI0 + 3] = lambda size: 0x60 | (0x80 if emu.spi_rx_full else 0)
        spdr_write = emu.writers[se.RSPI0 + 4]

        def spi_write(size, value):
            spdr_write(size, value)
            if size == 1:  # 8-bit: the OLED's commands (the CV DAC's are 32-bit)
                self.spi.append((emu.now(), value & 0xFF, self.dc, self.cs, self.busy))
            elif size == 4:  # The CV DAC's message (sendCVTransfer()): its receive interrupt 32 bits at 10 MHz later
                self.cv += 1
                self.ints.schedule("cv_spi", emu.now() + 3.2e-6 * se.CPU_HZ, self.cv_complete)
        emu.writers[se.RSPI0 + 4] = spi_write
        emu.writers[dma + 0x28] = self.on_chctrl
        self.spi = []  # (time, byte, D/C, selected, DMA running)
        self.dmas = []  # (time, N0SA, N0TB, D/C, selected, DMA running already)
        self.busy = False
        self.dma_us = DMA_US
        self.canvases = {}
        for name in ("_ZN6deluge3hid7display4OLED4mainE", "_ZN6deluge3hid7display4OLED5popupE",
                     "_ZN6deluge3hid7display4OLED7consoleE"):
            self.canvases[sym[name]] = name
        for n, (a, size) in sym.by_name.items():
            if "cpuMonitorImage" in n:
                self.canvases[a] = n
        self.ints = se.Interrupts(emu)
        self.complete = sym["oledTransferComplete"]
        self.cv_complete = sym["cvSPITransferComplete"]
        self.cv = 0  # CV DAC messages
        # The PIC: its ring, where the PIC reads next, what was written where and by which code
        self.tx, self.tx_size = sym.by_name["picTxBuffer"]
        self.items = sym["uartItems"]
        self.pic_pos = 0
        self.ring = {}  # position -> (byte, pc)
        for base in (self.tx, self.tx + se.UNCACHED_MIRROR_OFFSET):
            uc.hook_add(UC_HOOK_MEM_WRITE, self.on_ring_write, begin=base, end=base + self.tx_size - 1)
        self.writer_cache = {}
        self.dc = None  # Unknown before boot
        self.cs = False
        self.pic = []  # (time, message) of the OLED's messages as the PIC took them
        # The PIC's answers: into its receive ring, where the DMA's write address (CRDA) says
        self.rx, self.rx_size = sym.by_name["picRxBuffer"]
        self.read_addr = sym["rxBufferReadAddr"]  # [UART_ITEM_PIC]
        channel = emu.elf_bytes_at(sym["rxDmaChannels"], 1)[0]
        self.rx_written = None
        emu.readers[se.dmac_channel_base(channel) + 0x1C] = self.crda
        emu.intercept(sym.find("uartFlushIfNotSending"), self.on_flush)
        uc.ctl_flush_tb()

    # --- the PIC

    def crda(self, size):
        if self.rx_written is None:
            return self.emu.u32(self.read_addr)  # Nothing arrived
        return self.rx + self.rx_written % self.rx_size

    def answer(self, byte):
        if self.rx_written is None:
            self.rx_written = self.emu.u32(self.read_addr) - self.rx
        self.emu.uc.mem_write(self.rx + self.rx_written % self.rx_size, bytes([byte]))
        self.rx_written += 1

    def on_ring_write(self, uc, access, address, size, value, _):
        base = self.tx if address < self.tx + se.UNCACHED_MIRROR_OFFSET else self.tx + se.UNCACHED_MIRROR_OFFSET
        for i in range(size):
            self.ring[address - base + i] = ((value >> (8 * i)) & 0xFF, uc.reg_read(UC_ARM_REG_PC))

    def oled_writer(self, pc):
        """Whether the code at pc is one of the OLED's (its inline chain, by the debug info)"""
        if pc not in self.writer_cache:
            out = subprocess.run([self.emu.tool_prefix + "addr2line", "-f", "-i", "-C", "-e", self.emu.elf,
                                  hex(pc)], capture_output=True, text=True).stdout
            self.writer_cache[pc] = any(w in out for w in OLED_WRITERS)
        return self.writer_cache[pc]

    def on_flush(self, emu):
        if emu.uc.reg_read(UC_ARM_REG_R0) != 0:  # UART_ITEM_PIC
            return None
        write_pos = struct.unpack("<H", emu.uc.mem_read(self.items, 2))[0]
        while self.pic_pos != write_pos:
            byte, pc = self.ring.get(self.pic_pos, (0, 0))
            self.pic_pos = (self.pic_pos + 1) % self.tx_size
            if 247 <= byte <= 251 and self.oled_writer(pc):
                self.pic.append((emu.now(), byte))
                if byte == DC_LOW:
                    self.dc = 0
                elif byte == DC_HIGH:
                    self.dc = 1
                elif byte == SELECT:
                    self.cs = True
                elif byte == DESELECT:
                    self.cs = False
                if byte >= SELECT:
                    self.answer(byte)
        return None

    # --- the image's DMA

    def on_chctrl(self, size, value):
        if value & 1:  # SETEN
            n0sa = self.emu.plain_read(self.dma_base, 4)
            n0tb = self.emu.plain_read(self.dma_base + 8, 4)
            self.dmas.append((self.emu.now(), n0sa, n0tb, self.dc, self.cs, self.busy))
            self.busy = True
            self.ints.schedule("oled_dma", self.emu.now() + self.dma_us * 1e-6 * se.CPU_HZ, self.complete,
                               self.dma_done)

    def dma_done(self, emu):
        self.busy = False

    # --- what the checks look at

    def commands(self, since=0):
        """The contrast commands written to the SPI from then on: [(time, value, D/C, selected, DMA running)]"""
        out = []
        spi = [s for s in self.spi if s[0] >= since]
        for a, b in zip(spi, spi[1:]):
            if a[1] == 0x81:
                out.append((a[0], b[1], a[2] if a[2] == b[2] else None, a[3] and b[3], a[4] or b[4]))
        return out

    def images_ok(self, since=0):
        dmas = [d for d in self.dmas if d[0] >= since]
        bad = [d for d in dmas if not (d[3] == 1 and d[4] and not d[5] and d[2] == IMAGE_BYTES
                                       and d[1] in self.canvases)]
        during = [s for s in self.spi if s[0] >= since and s[4]]
        return dmas, bad, during


class Deluge:
    def __init__(self, elf, sd, tools, oled=True):
        self.emu = emu = se.Emulator(elf, sd, tools, os.environ.get("BLOCKCOUNT_DIR", HERE), lambda s: None)
        self.side = OledSide(emu, oled)  # Without the OLED: the SPI, the CV DAC and the PIC all the same
        se.setup_sd(emu)
        se.boot(emu)
        se.load_startup_song(emu)
        self.sym = sym = emu.sym
        self.dma = se.RealTimeDma(emu)
        self.menu = sym["oledBrightnessMenu"]
        self.editor = sym["soundEditor"]

    def run(self, seconds):
        se.run_task_manager(self.emu, seconds)

    def have_oled(self):
        return self.emu.u8(self.sym["_ZN6deluge3hid7display16have_oled_screenE"])

    def contrast(self):
        return self.emu.u8(self.sym["oledContrast"])

    def relevant(self):
        return self.emu.call(self.sym["_ZN6deluge3gui9menu_item14OledBrightness10isRelevantEP20ModControllableAudiol"],
                             self.menu, 0, 0) & 0xFF

    def level(self):
        """The menu's value as it shows it (readCurrentValue())"""
        self.emu.call(self.sym["_ZN6deluge3gui9menu_item14OledBrightness16readCurrentValueEv"], self.menu)
        return self.emu.u32(self.menu + MENU_VALUE_OFFSET)

    def open_settings(self):
        emu, sym = self.emu, self.sym
        emu.call(sym["_ZN11SoundEditor5setupEP4ClipPK8MenuIteml"], self.editor, 0, sym["settingsRootMenu"], 0)
        emu.call(sym["_Z6openUIP2UI"], self.editor)
        return emu.call(sym["_Z12getCurrentUIv"]) == self.editor

    def turn(self, offset):
        """The select encoder on the item: Integer::selectEncoderAction() (value, writeCurrentValue(), redraw)"""
        self.level()
        se.drain_uarts(self.emu)
        self.emu.call(self.sym["_ZN6deluge3gui9menu_item7Integer19selectEncoderActionEl"], self.menu, offset)
        return self.emu.u32(self.menu + MENU_VALUE_OFFSET)

    def set_level(self, level):
        """As the menu's writeCurrentValue() does with that value"""
        self.emu.w32(self.menu + MENU_VALUE_OFFSET, level)
        self.emu.call(self.sym["_ZN6deluge3gui9menu_item14OledBrightness17writeCurrentValueEv"], self.menu)

    def leave(self):
        self.emu.call(self.sym["_ZN11SoundEditor14exitCompletelyEv"], self.editor)

    def queue(self):
        """(read position, write position, sending) of the SPI transfer queue"""
        return (self.emu.u8(self.sym["spiTransferQueueReadPos"]), self.emu.u8(self.sym["spiTransferQueueWritePos"]),
                self.emu.u8(self.sym["spiTransferQueueCurrentlySending"]))

    def close(self):
        self.dma.close()


def saved(sd):
    try:
        xml = fat32.read_file(sd, "CommunityFeatures.XML").decode("ascii", "replace")
    except FileNotFoundError:
        return None
    m = re.search(r'name="oledContrast"\s+value="(\d+)"', xml)
    return int(m.group(1)) if m else None


def check_images(d, since, what):
    dmas, bad, during = d.side.images_ok(since)
    check(f"{what}: images went out, each selected with D/C high, 768 bytes from a canvas, one at a time; no SPI byte "
          f"during one", len(dmas) > 0 and not bad and not during,
          f"{len(dmas)} images, {len(bad)} bad {bad[:2]}, {len(during)} bytes during a transfer")


def check_commands(d, since, wanted, what):
    cmds = d.side.commands(since)
    values = [c[1] for c in cmds]
    ok = values == wanted and all(c[2] == 0 and c[3] and not c[4] for c in cmds)
    check(f"{what}: {', '.join(f'0x81 {v}' for v in wanted) or 'no command'} (D/C low, selected, no DMA running)", ok,
          f"{[(v, 'D/C', dc, 'sel', cs, 'dma', busy) for _, v, dc, cs, busy in cmds]}")
    return cmds


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools")
    ap.add_argument("--out", default=os.getcwd())
    a = ap.parse_args()
    tools = a.tools or os.path.join(os.path.dirname(os.path.abspath(a.elf)),
                                    "../../toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-")
    os.makedirs(a.out, exist_ok=True)
    sd = os.path.join(a.out, "oledbrightness.img")
    files, lengths = make_sd.samples()
    files["SONGS/DEFAULT.XML"] = make_sd.song_xml(lengths, 1, 2).encode()
    make_sd.fat32.build(sd, files)

    print("== 1. boot with the OLED, a card without CommunityFeatures.XML", flush=True)
    d = Deluge(a.elf, sd, tools)
    side = d.side
    boot = side.commands()
    check("the OLED found, oledMainInit()'s 0x81 0xFF (D/C low, selected), no other contrast command",
          d.have_oled() and [c[1:4] for c in boot] == [(0xFF, 0, True)], f"{boot}")
    check("the contrast 255, the menu on 10 and shown", d.contrast() == 255 and d.level() == 10 and d.relevant(),
          f"{d.contrast()}, level {d.level()}, relevant {d.relevant()}")

    print("== 2. images", flush=True)
    t = d.emu.now()
    d.run(0.3)
    check_images(d, t, "0.3 s")
    check("no contrast command", not side.commands(t), f"{side.commands(t)}")

    print("== 3. Settings, the encoder", flush=True)
    check("Settings open", d.open_settings())
    d.run(0.1)
    t = d.emu.now()
    pic_t = len(side.pic)
    level = d.turn(-7)
    d.run(0.2)
    check("level 3, the contrast 14", level == 3 and d.contrast() == 14, f"level {level}, {d.contrast()}")
    cmds = check_commands(d, t, [14], "the OLED got")
    stream = [m for _, m in side.pic[pic_t:]]
    around = []
    if cmds:
        at = cmds[0][0]
        before = [m for tm, m in side.pic[pic_t:] if tm <= at][-2:]
        after = [m for tm, m in side.pic[pic_t:] if tm > at][:2]
        around = before + ["cmd"] + after
    check("the PIC: D/C low, select | the command | D/C high, deselect", around == [DC_LOW, SELECT, "cmd", DC_HIGH,
                                                                                  DESELECT], f"{around}")
    check("images after it with D/C high", any(dm[0] > (cmds[0][0] if cmds else 0) for dm in side.dmas),
          f"{len(side.dmas)} images in all")
    check_images(d, t, "meanwhile")
    t = d.emu.now()
    levels = [d.turn(-1), d.turn(-1), d.turn(+4)]
    d.run(0.3)
    check("three quick turns: levels 2, 1, 5; the contrast 51", levels == [2, 1, 5] and d.contrast() == 51,
          f"{levels}, {d.contrast()}")
    check_commands(d, t, [4, 1, 51], "the OLED got, in order")
    check_images(d, t, "meanwhile")
    print(f"  (the PIC's OLED messages since step 3: {len(stream)}, D/C low {stream.count(DC_LOW)} times)")

    print("== 4. a turn while an image's DMA runs", flush=True)
    d.turn(+1)  # Level 6: 0x81 79
    d.run(0.2)
    side.dma_us = 20000  # Held in flight for 20 ms
    sym = d.sym
    d.emu.uc.mem_write(sym["_ZN6deluge3hid7display4OLED12needsSendingE"], b"\x01")
    d.emu.call(sym["_ZN6deluge3hid7display4OLED13sendMainImageEv"])  # A new image to send
    started = len(side.dmas)
    for _ in range(200):
        d.run(0.001)
        if len(side.dmas) > started and side.busy:
            break
    t = d.emu.now()
    running = side.busy
    spi_before = len(side.spi)
    d.turn(-1)  # Back to 5 while the DMA runs
    written_at_once = len(side.spi) - spi_before
    d.run(0.1)
    side.dma_us = DMA_US
    cmds = side.commands(t)
    done = [dm[0] for dm in side.dmas if dm[0] <= t][-1] + 20000e-6 * se.CPU_HZ
    check("a DMA running at the turn; nothing written then; 0x81 51 after it ended, D/C low, selected", running
          and written_at_once == 0 and [c[1] for c in cmds] == [51] and cmds[0][0] >= done and cmds[0][2] == 0
          and cmds[0][3] and not cmds[0][4],
          f"running {running}, {written_at_once} bytes at once, {[(c[1], c[2], c[3], c[4]) for c in cmds]}, "
          f"{(cmds[0][0] - done) / se.CPU_HZ * 1e3 if cmds else 0:.1f} ms after the DMA's end")
    check_images(d, t, "meanwhile")
    d.run(0.2)
    q = d.queue()
    check("the queue empty and idle", q[0] == q[1] and not q[2], f"{q}")

    print("== 5. the menu left", flush=True)
    d.leave()
    check("CommunityFeatures.XML: oledContrast 51", saved(sd) == 51, f"{saved(sd)}")
    d.close()
    del d

    print("== 6. restart", flush=True)
    d = Deluge(a.elf, sd, tools)
    side = d.side
    boot = side.commands()
    t = d.emu.now()
    d.run(0.3)
    later = side.commands(t)
    check("oledMainInit()'s 0x81 0xFF, then 0x81 51 from the file through the queue (D/C low, selected, no DMA)",
          [c[1:4] for c in boot] == [(0xFF, 0, True)] + ([(51, 0, True)] if len(boot) > 1 else [])
          and [c[1] for c in boot + later] == [0xFF, 51] and all(c[2] == 0 and c[3] and not c[4] for c in boot + later),
          f"at boot {[c[1:] for c in boot]}, then {[c[1:] for c in later]}")
    check("the contrast 51, the menu on 5", d.contrast() == 51 and d.level() == 5, f"{d.contrast()}, {d.level()}")
    check_images(d, 0, "since boot")
    d.close()
    del d

    print("== 7. the 7-segment Deluge on the same card", flush=True)
    d = Deluge(a.elf, sd, tools, oled=False)
    check("no OLED; the item not shown; 51 read", not d.have_oled() and not d.relevant() and d.contrast() == 51,
          f"OLED {d.have_oled()}, relevant {d.relevant()}, {d.contrast()}")
    d.open_settings()
    d.run(0.1)
    side = d.side
    spi, dmas, q = len(side.spi), len(side.dmas), d.queue()
    d.set_level(8)
    queued = [d.emu.u8(d.sym["spiTransferQueue"] + 8 * (i % 32)) for i in range(q[1], q[1] + (d.queue()[1] - q[1]) % 32)]
    d.run(0.1)
    check("level 8: nothing sent (no SPI byte, no image, nothing for the OLED queued), the contrast kept as 155",
          len(side.spi) == spi and len(side.dmas) == dmas and not [x for x in queued if x != 1]
          and d.contrast() == 155, f"{len(side.spi) - spi} bytes, {len(side.dmas) - dmas} images, queued "
          f"destinations {queued}, {d.contrast()}")
    d.leave()
    check("CommunityFeatures.XML: oledContrast 155", saved(sd) == 155, f"{saved(sd)}")
    d.close()
    del d

    print('== 8. a damaged entry: CommunityFeatures.XML with oledContrast "abc"', flush=True)

    def card_with(value):
        path = os.path.join(a.out, "oledbrightness-entry.img")
        files["CommunityFeatures.XML"] = ('<?xml version="1.0" encoding="UTF-8"?>\n<runtimeFeatureSettings>\n'
                                          f'\t<setting name="oledContrast" value="{value}" />\n'
                                          '</runtimeFeatureSettings>\n').encode()
        make_sd.fat32.build(path, files)
        return path

    sd_bad = card_with("abc")
    d = Deluge(a.elf, sd_bad, tools)
    d.run(0.2)
    cmds = d.side.commands()
    check("read as missing: the contrast 255, the menu on 10 (not the darkest), only oledMainInit()'s 0x81 0xFF",
          d.contrast() == 255 and d.level() == 10 and [c[1] for c in cmds] == [0xFF],
          f"{d.contrast()}, level {d.level()}, commands {[c[1] for c in cmds]}")
    d.open_settings()
    d.run(0.1)
    d.leave()
    check("Settings left: CommunityFeatures.XML says oledContrast 255", saved(sd_bad) == 255, f"{saved(sd_bad)}")
    d.close()
    del d
    got = {}
    for value in ("", "0", "-5", "-2147483648", "99999", "1"):
        d = Deluge(a.elf, card_with(value), tools)
        got[value] = d.contrast()
        d.close()
        del d
    check('the entry "", "0", "-5", "-2147483648", "99999": 255 each; "1": 1',
          got == {"": 255, "0": 255, "-5": 255, "-2147483648": 255, "99999": 255, "1": 1}, f"{got}")

    print(f"OLED brightness ({os.path.basename(a.elf)}): {checks - failures} of {checks} ok", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
