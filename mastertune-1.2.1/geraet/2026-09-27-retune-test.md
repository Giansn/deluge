# Test von `tools/retune_library.py` (27.09.2026, Windows)

Stand `18a6d0e`. Python 3.14 mit numpy, scipy, soundfile, soxr 1.1.0, pylibrb 0.1.2, ffmpeg (imageio-ffmpeg). Kein sox. Der Emulator-Teil (`retune_emu.py`) lief hier nicht, er braucht die Linux-Toolchain.

## Mitgelieferte Tests

| Test | Ergebnis |
|---|---|
| `pc_test.py` | **alle Prüfungen bestanden** (148 s). Gelesen von libsndfile 16/16, scipy 16/16, ffmpeg 16/16, `wave` 15/16 (die Float-Datei, erwartet). |
| `paths_memory_test.py` | Umlaute ok. Der Speicher-Teil bricht unter Windows ab: `os.wait4` gibt es nur unter Unix (Zeile 115). Ein Fehler des Tests, nicht des Werkzeugs. Vorschlag: unter Windows `psutil` oder die Messung überspringen. |

## Echte Daten: «New Sitar Grii 10» (`karte/`, 136 Samples)

`--card karte --out … --tuning 432`: 136 umgewandelt, 3 fehlen (die `PsyPack`-Hihats, auch nicht im Backup), 2 s.

- **Quellordner unverändert** (Prüfsummen aller Dateien vorher und nachher gleich).
- **XML:** Nur Zahlen geändert, sonst Byte für Byte gleich. 151 Werte: 7 × `startSamplePos`, 144 × `endSamplePos`.
- **Dateien:** alle mit `mtun` = 4320. Die vier Dateien mit 96 kHz sind jetzt 44,1 kHz, alle anderen bleiben bei 44,1 kHz. Formate bleiben: 18 × Float, 62 × 16 bit, 52 × 24 bit, 4 × 32 bit PCM.
- **Länge:** ×1,018425 bis ×1,018671 (erwartet 55/54 = 1,018519, Rundung bei kurzen Dateien).
- **Tonhöhe** (Kreuzkorrelation der Spektren, Original gegen umgewandelt): Kontrabass, Oboe, Sitar und DUB je −31,78 Cent, erwartet −31,77.
- **Pegel** unverändert (Oboe −38,97 dB, Sitar −26,40 dB vorher und nachher).
- **Gegen eine unabhängige Umrechnung** (scipy `resample_poly` 55/54, Kaiser 12): Abweichung −63 bis −101 dB relativ. Bei der Oboe (16 bit, leise) ist das das Rundungsrauschen der 16 bit (etwa −103 dBFS), bei der Sitar (Float) die leicht andere Filterform. Nicht hörbar.
- **Übersteuern:** Bei 15 von 136 Dateien liegen nach der Umrechnung einzelne Spitzen über 0 dBFS und werden abgeschnitten. Meist 1–6 Samples. Auffällig: `DUB/RADJ_Syn_Bass_Note_F#min_2.wav` 3670 Samples bei +0,01 dB (das Original liegt schon auf 0 dBFS), `Hat/LCD2_ClosedHH_60.wav` 4 Samples bei +1,36 dB. Vorschlag: solche Dateien als 32-bit Float schreiben (die Firmware liest es) oder minimal leiser.
- Ein Loop-Hinweis: `DRUMS/Crash/CR78 Cymbal.wav`, Loop von 1318 Samples, Periode −0,53 Cent nach dem Runden.

## Probelauf über die ganze Karte (Backup vom 25.09., 7337 Dateien)

`--dry-run --tuning 432`: 162 s (warm 48 s).

| | Anzahl |
|---|---|
| umgewandelt | 5148 (23,0 → 22,5 GB) |
| XML mit geänderten Positionen | 648, zusammen 46 704 Werte |
| macOS-Begleitdateien `._…` («not a WAV or AIFF file») | 607, 0,1 MB, richtig so |
| fehlend (in XML genannt, nicht auf der Karte) | 59: `PsyPack` 32, `Percussion` 9, `Nature SOunds` 5, `Fuego` 4, weitere |
| «unknown use» | 8, aus dem nicht lesbaren `KITS/041 Jonathan Snipes (Waterfalls).XML` («`</sound>` closes `<modKnobs>`»): bleibt unverändert, seine Samples auch |
| 64-bit Float (liest die Firmware nicht) | 6 |
| leere 0-Byte-Dateien («too short») | 5 |
| Wavetables / vielleicht Wavetables | 2 / 4 |
| WAVE_FORMAT_EXTENSIBLE | 1 |

Kurze Loops in `SAMPLES/RESAMPLE/Chrigu Jam*/output_*.wav`: 10 Hinweise auf die Periode nach dem Runden.

**Speicher:** Spitze **3,4 GB** schon beim Probelauf (PC mit 15,7 GB: geht). Das Audio ist es nicht. Der Plan hält alle 968 XML-Texte (295 Mio. Zeichen, als Python-str mit surrogateescape 2 Byte pro Zeichen) und ihre Parse-Bäume gleichzeitig. Vorschlag: pro XML nur die Referenzen und Positionen behalten und den Text beim Schreiben neu lesen.

## Für den Gerätetest

- Eine Teil-Umwandlung darf nicht auf die Karte: Samples, die auch andere Songs benutzen, wären dort umgewandelt, deren Positionen aber nicht. Also immer die ganze Karte umwandeln und auf eine zweite Karte schreiben. Das Original bleibt unberührt.
- Dann «New Sitar Grii 10» bei 432 Hz mit der umgewandelten Karte messen (l2d, Profile) und mit der Messung vom Original vergleichen.
