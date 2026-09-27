# «New Sitar Grii 10» im Emulator, 432 und 440 Hz, 27.09.2026

Firmware: das nachgebaute v16 (`7eed1a77…`, ELF mit Symbolen, siehe `2026-09-27-nachbau-v16.md`). Skript `sitar_emu.py` hier im Ordner, auf der Umgebung von `tests/song` (Entwicklungs-Branch `1238201`). Alle Zahlen stehen in `2026-09-27-sitar-emulator.json`.

## Aufbau

- **Karte:** der Song als `SONGS/DEFAULT.XML`, die 136 Samples aus `karte/SAMPLES`, die Grundstimmung über `CommunityFeatures.XML`. Nach dem Boot geprüft: 432,0 bzw. 440,0 Hz.
- **Ersetzt:** 3 fehlende Samples des Kits Hihat (`PsyPack/hihat`, `hihatshort`, `hihatlong`). Im CSV stehen dazu keine Angaben. Die Länge stammt aus `endSamplePos` im Song (8'923, 6'988, 27'340 Samples). Das Format ist dasselbe wie bei den übrigen 11 Samples des Kits (Stereo, 24 Bit, 44,1 kHz). Inhalt: abklingendes Rauschen.
- **Clips:** Im gespeicherten Song laufen Hihat, Guiro, 170 Sitar 2 und Oboe. Für den Vergleich mit dem Gerät habe ich zusätzlich 3L3Ctr0, 014 CR-78 und KIT1 eingeschaltet («alle Kits»).
- **Messung:** wie `run.sh` (`--init-sounds --seed 1`, ohne Culling, also der volle Bedarf des Songs). 1 Takt Vorlauf, dann 4 Takte bei 140 BPM (302'400 Samples). Pro Spur misst die Firmware die Renderzeit selbst, wie der Profiler am Gerät (`profiler::timingOutputs`, `outputTicks[]`, OS-Timer 0 = emulierte Zeit). Die Bereiche kommen aus `profile_by_function`.

## Rechenzeit pro Spur beim Spielen

Emulator: % CPU (400 MHz, 1 Instruktion pro Takt), in Klammern der Anteil an der Audio-Routine. Gerät: Anteil der Zeit laut `2026-09-27-v16-profile-check.jsonl`. Zu dieser Messung passen die 56 %: 12 s, Last 96 %, QL die ganze Zeit, 173 Stimmen abgeschnitten.

| Spur | 432 Hz, alle Kits | 440 Hz, alle Kits | 432 Hz, wie gespeichert | Gerät |
|---|---|---|---|---|
| S 170 Sitar 2 | 16,3 (43,9) | 16,4 (44,3) | 16,3 (50,3) | 6,5 |
| K Hihat | 3,5 (9,4) | 3,5 (9,5) | 3,5 (10,7) | 11,0 |
| K Guiro | 3,0 (8,1) | 3,0 (8,0) | 3,0 (9,3) | 10,8 |
| S Oboe | 2,8 (7,4) | 2,6 (7,0) | 2,8 (8,5) | 2,1 |
| K KIT1 | 2,2 (5,8) | 2,2 (5,9) | 0,1 (0,3) | 8,5 |
| K 3L3Ctr0 | 1,6 (4,4) | 1,6 (4,4) | 0,2 (0,5) | 15,0 |
| K 014 CR-78 | 1,2 (3,4) | 1,2 (3,2) | 0,1 (0,3) | 9,9 |
| A AUDIO1 | 0,4 (1,2) | 0,4 (1,2) | 0,4 (1,3) | 2,6 |
| K Rattle / Rattle V2 | je 0,1 | je 0,1 | je 0,1 | 5,6 / 5,1 |
| S Kbass, 2 × M | 0,0 | 0,0 | 0,0 | 1,4 / 0,1 / 0,1 |
| **Die fünf Kits** | **11,6 (31,1)** | **11,5 (31,1)** | 6,9 (21,2) | **55,2** |
| Gesamt, Mittel (Spitze) | 37,2 (53,4) | 37,0 (51,2) | 32,5 (42,3) | Audio-Routine 97 % |

Höchstens 20 Stimmen, keine Schnitte wegen Last. 30 Stimmen wurden ersetzt, weil einzelne Sounds ihr Stimmenlimit erreichten.

## 432 gegen 440 Hz

**Kein nennenswerter Unterschied:** 37,2 gegen 37,0 % CPU (+0,2). Fast alle Samples werden schon bei 440 Hz umgerechnet, weil die Noten nicht auf dem Grundton der Samples liegen. Bei 440 Hz werden nur 14'106 Instruktionen pro 128 Samples nativ gelesen. Bei 432 Hz fällt das weg, dafür steigt das Umrechnen (`readSamplesResampled`) von 125'521 auf 128'467. Die Grundstimmung kostet in diesem Song also praktisch nichts.

## Warum die Kits am Gerät viel mehr kosten

Im Emulator rendert jeder Aufruf der Audio-Routine 128 Samples. v16 ruft die Routine viel öfter auf und rendert dann kleinere Stücke, im Leerlauf 4–8 Samples (Befund aus dem Emulator, Patch 0053). Beim Spielen wachsen die Stücke mit der Last. Der Aufwand pro Aufruf fällt dann viel öfter an: Effekte einrichten, Patcher, Hüllkurven, Filter-Konfiguration. Zum Test habe ich dieselbe Messung (432 Hz, alle Kits) mit kleineren Fenstern wiederholt:

| Fenster | Gesamt % CPU | Fünf Kits % CPU (Anteil Routine) | Sitar | Rattle, Rattle V2 (ohne Noten) |
|---|---|---|---|---|
| 128 | 37,2 | 11,6 (31,1) | 16,3 (43,9) | je 0,1 |
| 32 verlangt, im Mittel 55* | 43,0 | 14,3 (33,1) | 18,2 (42,2) | je 0,2 |
| 8 | **102,7** | **42,7 (41,6)** | 36,3 (35,3) | 1,5 / 1,6 |

\* `routine()` passt die Fenstergrösse teils selbst an (`sampleThreshold`).

- **Derselbe Song kostet mit 8er-Fenstern 2,8-mal so viel.** Am stärksten wachsen die Stimmen, also Patcher, Hüllkurven und LFOs (3,9 → 21,8 % CPU), Song- und Master-FX samt Ausgabe (1,6 → 11,0), «memory / other» (1,0 → 10,2) und das Sample-Lesen (13,1 → 22,3).
- **Der Anteil der fünf Kits steigt von 31 auf 42 %.** Das Kit Hihat allein wächst von 3,5 auf 15,7 % CPU.
- **Die restliche Lücke zu den 55 % am Gerät:**
  - Am Gerät schneidet v16 bei dieser Last Stimmen ab (173 in 12 s). Laut Nutzer trifft es Sitar und Oboe. Im Emulator ohne Culling bleiben sie, das senkt dort den Anteil der Kits.
  - Caches und SDRAM-Wartezeiten modelliert der Emulator nicht.
  - Welche Clips am Gerät liefen, ist nicht protokolliert.
- **Folgerung:** Den Grossteil der Kit-Kosten am Gerät verursacht der Aufwand pro Aufruf bei den kleinen Fenstern von v16, nicht das Rendern der Noten. Genau das ändert v17 (die Routine läuft seltener). Am Gerät lohnt sich darum derselbe Song mit v17.

## Hinweise

- **Sitar ist im Emulator die teuerste Spur:** 18 Samples, Stereo, 32 Bit float. Sie kostet bei 432 und 440 Hz gleich viel, wird also auch bei 440 Hz umgerechnet.
- **Guiro:** Die Reihe `drum-hit-guiro-mid` hat die ganze Zeit 8 Stimmen, das ist ihr Maximum.
- **Nachbauen:** `python3 sitar_emu.py <deluge.elf> ../karte <out> --song-dir <dev>/mastertune-1.2.1/tests/song --build <Ordner mit blockcount.so aus run.sh> --tenths 4320 --all-kits [--window 8]`. Ein Lauf dauert etwa 25 s, mit `--window 8` etwa 80 s.
