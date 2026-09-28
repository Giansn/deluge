# Stopgap: v16-l2d without the hang in the drone view

`deluge-1.2.1-mastertune-v16-l2d-dronefix-ba499a93.bin` is v16 with L2 cache for code and data (`l2test/…v16-l2d-03ccaac5.bin`) and a single fix, otherwise unchanged. SHA-256 `929bdd63…7e01cb6b`.

- **The bug (v13 to v16, the L2 versions too):** About 15 ms after the drone view opens, the Deluge hangs and the sound stops. The drone view had no drawing routine of its own. The UI timer called it every 15 ms, and it called itself endlessly.
- **The fix:** an empty drawing routine for the drone view (commit `ba499a93`, on v16-l2d `03ccaac5`).
- **Checked in the emulator:**
  - `tests/song/drone_reopen_emu.py`: open songs again, use the drone view, change songs during playback, measure the drone by FFT. v16 hangs, this file passes every check.
  - `DRONE=1 tests/song/run.sh`: passed.
  - The full-load song sounds bit for bit like v16 (`87a7df29…`, `4473b315…`).
- **Not tested on the device.** v17 contains the fix too and replaces this file.

**Replaced by v17** (`deluge-1.2.1-mastertune-v17-l2d-b3385d83.bin` in the main folder). v17 contains this fix too.
