# L2 cache: the test version for code (l2i)

**From v17 on, the version with L2 for code and data (l2d) is the main file** in the main folder (`deluge-1.2.1-mastertune-v17-l2d-b3385d83.bin`), because it proved itself with v16 on the device: "New Sitar Grii 10" audibly better, no crash. What stays here is the intermediate stage **l2i**: v17 with L2 for code only, otherwise unchanged, also with the profiler (Settings → CPU monitor → Profile, see `prof/README.md`). The matching `.symbols.json` lies next to every file. They are meant for measuring and testing, not for gigs, as long as they aren't checked longer on the device.

**First measurement on the device (with v14):** Without L2 the CPU monitor showed 97%, 13 voices, quality lowered and voices cut. With L2 it was also 97% and quality lowered, but **no voices cut** any more. Once the device crashed shortly after the CPU monitor was switched on. The cause is open. If it happens again, please note the version (l2i or l2d) and the display: frozen, restart or a message like "E…".

| File | What | Risk |
|---|---|---|
| `deluge-1.2.1-mastertune-v17-l2i-e476310e.bin` | L2 **for code only**. The community's state of 12/2024, there since in nightly and beta. | low |
| `../deluge-1.2.1-mastertune-v17-l2d-b3385d83.bin` (main folder) | L2 **for code and data**, with prefetch. The community's state of 04/2026 with the fixes after it. | medium, without a crash with v16 on the device |

SHA-256: l2i `571646a9…2122370c`, l2d `aad4d080…5ec32c4b`. Patches on v17: `0001-…` and `0002-…` (code only), `0003-…` (data).

## What it's about

The RZ/A1L has 128 KB of L2 cache in front of the memory, which 1.2.1 never switches on. Only the L1 cache is used (32 KB each for code and data).
- **Code only:** The code a full song needs all the time doesn't fit into 32 KB and keeps falling out of the L1 cache. The L2 keeps it closer to the CPU. Data must not go into it.
- **Code and data:** In addition, the data in the slow external SDRAM go into the L2: delay and chorus buffers, wave tables and samples. That's probably where the bigger gain is.
- **In both versions:** Before every DMA transfer the firmware writes the buffer back in the L2 too and cleans it, and once more after reading from the card.
  - This concerns the SD card (reading and writing) and the OLED.
  - Audio, MIDI and the LED controller run over uncached addresses, USB without any DMA.
  - The code version needs this too: the processor may speculatively fetch instructions from any memory, so a line of a buffer can end up in the L2.
  - A forgotten path would give rare errors, in the worst case wrong bytes in files on the card.

Nobody knows how much it brings: the community gives no number, and the emulator doesn't model caches. Only the measurement on the device shows it.

## Measuring

1. Copy the test song `diag/loadtest-card.zip` onto the card (see `diag/README.md`).
2. Start with **v17 without L2** (`deluge-1.2.1-mastertune-v17-2cb5e31b.bin`), load `MT_LOADTEST`, **Settings → CPU monitor → On**, **Play**. After about 30 seconds note the display at the top left (`CPU 61% 24V`), ideally three times a few seconds apart.
3. The same with **l2i** and with **l2d**.
4. It's more exact with `tools/cpu_monitor.html` (one minute, export the CSV).

Less CPU % with as many voices means: that's how much the L2 brings. If `QL` (quality lowered) or `VC` (voices cut) appear without L2, they should get rarer with the L2.

## Testing, above all the data version

**Back up the SD card first**, or work with a copy.

- **Saving:** Save a song twice under a new name, once stopped and once while it plays. Compare on the computer: the files must be the same.
- **Samples:** Play a song with many long samples and listen for clicks at regular intervals (streaming from the card).
- **Recording:** Resample or record from the input for a minute and listen to the recording for clicks.
- **OLED:** Scroll quickly through presets and look for pixel errors or shifted lines.
- **USB:** Copy a file onto the card and back with DEx or deluge-editor, then compare.

If something seems off: go back to v17 without L2 and describe to me what happened.

## Back to v17 without L2

Like any firmware update: `deluge-1.2.1-mastertune-v17-2cb5e31b.bin` from the main folder onto the card, and restart.

## Checked

- **Build:** without new warnings. Two complete rebuilds each give the same SHA-256.
- **Emulator** (`tests/l2`, with a model of the L2 controller):
  - At startup the L2 is switched off, cleaned, locked for data and then switched on.
  - Code version: data stay locked.
  - Data version: prefetch on. The data are released once at the end of the startup, right after everything is written back and cleaned.
  - Every OLED image is written back in L1 and L2 (all 25 cache lines) and synchronised before the DMA.
  - The cache maintenance hits every line exactly once, also at odd addresses.
- **Song:** The full-load song sounds bit for bit the same with both versions as without L2, in all three runs. Mutable and Digital also sound like v14 and v15. The third run, with culling as on the device, depends on the computing time and therefore differs from v14.
- **Checker** (code, every DMA transfer of the firmware):
  - He found no error in the L2 changes themselves.
  - His suggestions for the code version (L2 maintenance there too) and for the release of the data (interrupts off, write back instead of only clean) are implemented.
  - Two older bugs he found along the way are fixed since v14 (see the README, v14).
- **Carried over to v15:** The three L2 changes went onto v15 without a conflict. All checks above ran again with the v15 versions.
- **Carried over to v17:** likewise without a conflict. All checks above ran again with the v17 versions, plus v17's whole test series on l2d.
- **Carried over to v16:** likewise without a conflict. All checks above ran again with the v16 versions, plus the profiler with l2i (21 checks).
- **Not checkable in the emulator:**
  - the real cache behaviour and the speed
  - the SD transfers, because the emulator reads the card below the file system. Their cache maintenance is the same function as for the OLED and is checked in the code.
- Loading firmware over USB SysEx (a developer function) is not in these builds. In builds with this function, it switches the L2 off cleanly before the jump.
