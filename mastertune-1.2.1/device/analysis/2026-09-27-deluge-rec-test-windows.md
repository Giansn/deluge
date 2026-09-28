# DelugeRec: tests on Windows, 27.09.2026

Task 4 of the development session. `tools/deluge_rec.py`, `tools/deluge_rec.ico` and `tests/deluge_rec/` are the same on `geraet-ergebnisse` (`e0bc3a7`, now `device-results`) and on the development branch (`git diff` empty). The development session had already carried them over itself, so there is no further commit with the files.

- **Run:** `python mastertune-1.2.1/tests/deluge_rec/test_deluge_rec.py` on Windows 11, Python 3.14.
- **Result:** 25 tests, **all ok**, 6.3 s.
- **On the device:** The Deluge appears on Windows as the recording input "Línea (Deluge)", 2 channels, 44.1 kHz. With all four interfaces: MME, DirectSound, WASAPI, WDM-KS. A recording of my own over WASAPI exclusive was bit-exact 24 bit, with no hole of pure silence in 8 + 20 s of playback.
