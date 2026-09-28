# Measurement version v12-diag: CPU monitor

| File | Version (Settings → Firmware version) | SHA-256 |
|---|---|---|
| `deluge-1.2.1-mastertune-v12-diag-d0d03dcc.bin` | `1.2.1-mastertune-v12-diag-d0d03dcc` | `8c94cf686fad75ba9735fb7b5c4608bec02dfb8409db15172506f546468fbc14` |

This is **v12 plus measuring, nothing else**. Sound, DSP and culling are unchanged. The measurement shows how busy the CPU is and sends the values to the computer, where you can log them. Two clean builds (object files deleted, all 356 compiled anew) gave the same SHA-256, without compiler warnings. It replaces the first measurement version `b1ba466f`: its culling counter also counted calls that changed nothing, and its SD time included the audio rendering during the read. Source: `0001-CPU-monitor-…patch` against v12 (`f89b478c`), branch `mastertune-v12-diag`.

## Test song for the comparison with the emulator

`loadtest-card.zip` contains the song from the full-load test (`tests/song/`). It has 17 tracks at 120 BPM and plays everything at the same time throughout: 8 synths with chords, unison, filters and effects, FM, wavetable, a kit, a stretched audio track, reverb, sidechain and the drone.

1. Copy the content of the ZIP file into the root folder of the SD card. Only `SONGS/MT_LOADTEST.XML` and the folder `SAMPLES/MT_LOADTEST/` are added; existing files are not overwritten.
2. Start the measurement version, load the song `MT_LOADTEST`, **Settings → CPU monitor → On**.
3. Connect `tools/cpu_monitor.html`, press **Play** and let it run for about a minute.
4. **Export CSV** and upload it here. A second run with a typical song of your own is welcome too.

The emulator expects for this song:
- without CPU protection about 118% demand on average, 45 voices
- with CPU protection as on the device about 60%, around 21 voices, about 14 voices switched off per second, direness at 14 all the time

The comparison shows how well the emulator matches the real hardware. It counts instructions, not cycles; it has no cache and no pipeline.

## Switching on

**Settings → CPU monitor → On** (7-segment: `CPU`). After every power-up of the Deluge the monitor is off again. Switched off, it costs a single check per call of the audio routine.

- **OLED:** The bottom line shows the last second inverted, updated twice a second, e.g. `C43/71% V24 D0 S2.4/9` (SD without audio, see below). It lies over the picture and covers what would be below it. The screens themselves stay unchanged.
  - **Different from v13 on:** small at the top left, in words, without a bar: `CPU 43%  24 voices`. Below it only when needed `quality lowered` (direness above 0) or blinking `voices cut!` (culling). Plus the mode **Alerts** with only these warnings. The SysEx values and `tools/cpu_monitor.html` are unchanged.
- **7-segment:** Every 2 seconds `C 43` (CPU mean in %) appears briefly, but only when no other popup is running.
- **USB:** Every second a SysEx message goes to the computer, only on USB MIDI port 3. DIN MIDI never gets anything, the Volca neither. If the USB send buffer is full, the message is skipped so that notes and clock have priority.

## Logging on the computer

1. Connect the Deluge by USB and switch the monitor on.
2. Open `tools/cpu_monitor.html` in **Chrome or Edge** (dragging the file into the window is enough), then **Connect** and allow MIDI with SysEx.
3. Leave the input on "All inputs" or choose the Deluge's port 3. Tiles and graph (last 5 minutes) then run live.
4. **Export CSV** saves every second received. The separator is the semicolon, the decimal point a point, one line per second. The SD columns are called `sd_card_*` (without audio) and `sd_latency_incl_audio_*` (wall clock).

## What the numbers mean

| OLED | Page / CSV | Meaning |
|---|---|---|
| `C43/…` | CPU mean | Computing time of the audio routine divided by the duration of the audio it produced. Above ~90% all the time, it gets tight. |
| `C…/71%` | CPU peak | The most expensive single call (1–2 blocks, from 16 samples) relative to its audio duration. Single peaks above 100% are absorbed by the output buffer (128 samples = 2.9 ms). |
| `V24` | Voices now / max | Active voices. |
| `D0` | Direness max, share | 0–14. Above 0 the audio routine is behind the output. Then the Deluge saves on quality (simpler oscillator tables, linear instead of sinc interpolation), and from about 80 samples behind it switches voices off. The share says how much of the audio was produced with direness above 0. |
| – | Culling | Voices per second that the CPU protection really fades or switches off, each only once. Not counted: calls that leave their voice unchanged, and voices that already fade quickly or are off. Voice stealing because of the polyphony doesn't count. |
| `S2.4/9` | SD card mean / max | Pure read time of a sample cluster in ms, mean and longest access; `S-` = nothing read. While the Deluge waits for the card, it keeps computing the audio routine; that time is subtracted here. The value shows how fast the card is. Other, small tasks (display, MIDI) are still included. |
| – | SD latency incl. audio | The same accesses from start to end (wall clock), i.e. including the audio rendering meanwhile. That's how long the streaming waits for a cluster. The value rises with the CPU load: at 70% load an access of 2 ms on the card appears as about 6–7 ms. |
| – | Longest audio gap | The longest interval between two calls of the audio routine. From ~2.9 ms on, dropouts threaten. |

When the numbers get big, the OLED line gets shorter (first without `%`, then only the longest SD access, then without SD). The page always shows everything. If the SD latency is much bigger than the card time, the CPU slows the streaming down, not the card.

## Measuring

- **Time:** It is measured with OS timer 0 of the RZ/A1L (33.33 MHz), which the task manager keeps running freely anyway. It is only read; no other timer is touched. An overflow every 129 s does no harm, since only unsigned differences are taken. Interrupts during the audio routine count too; that is the real occupation.
- **SD without audio:** The collector keeps a running sum of the render time that is never reset (32 bits, only differences, so overflow-safe). It is read before and after every cluster access; the increase is subtracted from the wall-clock time.
- **Cost with the monitor on:** two timer reads per call of the audio routine, a task every 50 ms, two OLED transfers and one SysEx message (51 bytes) per second. No memory is allocated.
- **Sound unchanged:** v12's audio routine stands unchanged in a function of its own; the measurement only wraps around it. The comparison of the machine code (`tests/cpu_stats/compare_elf.py`, v12 against diag) gives:
  - The audio routine has the same instructions as in v12; only data addresses moved.
  - All DSP functions and DSP tables are the same or differ only in moved addresses.
  - Only the measuring points changed (the `cullVoice` counter, which only counts and changes nothing in the choice, `loadCluster`, OLED, menu, tasks, texts), plus a few small functions outside the audio path (creating a file, number to text, freeing a wavetable) that the linker optimised slightly differently.

## SysEx format

`F0 00 21 7B 01 10` + 44 bytes + `F7` (format 2). The command `0x10` was free in the Deluge. Every field consists of 7-bit groups, the least significant byte first, and values that are too big stay at the maximum.

| Bytes | Field | Unit |
|---|---|---|
| 1 | Format version = 2 | |
| 2 | Sequence number (counts seconds, 0–16383) | |
| 2 | Window length | ms |
| 2 / 2 | CPU mean / peak | 0.1% |
| 2 / 2 | Voices now / max | |
| 1 | Direness max | 0–14 |
| 2 | Share direness > 0 | 0.1% |
| 2 | Voices switched off (culling) | |
| 2 | SD clusters read | |
| 4 / 4 | SD latency incl. audio mean / longest access (wall clock) | µs |
| 4 | Longest audio gap | µs |
| 4 | Samples produced | |
| 4 / 4 | SD card without audio mean / longest access (from format 2) | µs |

If a program used the old developer ID before, the message starts with `F0 7D 10`; the page understands both. Later formats may append fields at the end. The page still understands format 1 (36 bytes, the first measurement version); the card time then stays empty.

## Tests

`tests/cpu_stats/run.sh <firmware tree>` checks on the PC:

- **Collector:** a simulated timer with overflow; mean, peak, gap, SD, culling and direness are right to ±0.2%. The card time is the wall clock minus the rendering during the read, even when the running render time overflows meanwhile.
- **OLED line:** always fits into 21 characters with 200,000 random values.
- **SysEx:** 503 messages go there and back without errors with the decoder from the page, also with the short header and in the old format 1. Foreign or broken messages are rejected.
- No PC test checks the culling counter in `cullVoice` (it hangs on the voices); it only counts in the branches that really change a voice.

`compare_elf.py v12.elf diag.elf <toolchain-bin> "AudioEngine::routine()=AudioEngine::routineUnmeasured()"` does the code comparison.
