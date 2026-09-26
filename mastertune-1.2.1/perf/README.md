# Leistungsversion v12-perf

| Datei | Version (Settings → Firmware version) | SHA-256 |
|---|---|---|
| `deluge-1.2.1-mastertune-v12-perf-ef5caee8.bin` | `1.2.1-mastertune-v12-perf-ef5caee8` | `d08b09bcfca73b42b03c3fc22daa1a7fb1f2d86db51e2c9129fccf919ce4060c` |

**v12-perf ist v12 mit schnelleren Filtern und Oszillatoren und dem CPU-Monitor der Messversion.** Filter und Oszillatoren rechnen Bit für Bit gleich wie in v12, nur mit weniger Befehlen. Klang, Bedienung und Songs bleiben wie in v12. Der CPU-Monitor ist nach dem Einschalten aus (Settings → CPU monitor, siehe `diag/README.md`).

**Nicht auf dem Gerät getestet.** Gemessen und bewiesen ist alles im Cortex-A9-Emulator mit dem Maschinencode der Firmware.

## Gewinn

Volllast-Test (`tests/song`): die echte Firmware mit einem Song aus 17 Spuren, die alle gleichzeitig spielen, 4 Takte. Die Befehle sind pro Block von 128 Samples gezählt.

| | v12 | v12-perf | Änderung |
|---|---|---|---|
| **Bedarf** (ohne CPU-Schutz, alle 45 Stimmen im Mittel) | 1 370 014 = 118 % | 1 095 537 = 94 % | **−20 %** |
| Filter (LP/HP-Ladder) | 534 726 | 338 014 | −37 % |
| Oszillatoren | 396 102 | 318 208 | −20 % |
| alle anderen Bereiche | unverändert | unverändert | |
| **Wie auf dem Gerät** (CPU-Schutz nachgebildet): Stimmen im Mittel | 20,7 | **28,5** | **+38 %** |
| Wie auf dem Gerät: Pads behalten von 4 Akkordtönen | 1,5–2,1 | etwa 3 | |

Der Deluge hält die Last mit seinem CPU-Schutz bei etwa 60 %. Die schnellere Firmware bringt deshalb vor allem **mehr Stimmen, die gleichzeitig klingen dürfen**. Mit diesem extremen Test-Song bleibt die Qualitätsabsenkung (Direness) trotzdem auf dem Maximum, weil sie schon ab etwa 40–49 % Last greift. In normalen Songs mit weniger Stimmen senkt die Einsparung, wie oft und wie stark der Deluge an der Qualität spart.

**Einzeln gemessen** (Benchmarks, pro Block):
- Filter: LP24 mono 10 830 → 7 414, HP 7 748 → 3 427, SVF 7 869 → 5 717, LP24 stereo 17 991 → 12 162.
- Oszillatoren: Säge 1 578 → 1 112, Dreieck 1 662 → 606, Analog-Square mit PW 2 569 → 1 416.

## Was geändert ist

1. **Filter** (`0001`, `dsp/filter`):
   - Zustand und Koeffizienten bleiben während eines Blocks in Registern.
   - Feste Entscheidungen werden einmal pro Block getroffen statt bei jedem Sample.
   - Terme, die bei Morph 0 nichts beitragen, fallen weg.
   - Die Rückkopplung läuft über kombinierte Multiplizier-Addier-Befehle.
   - Stereo läuft in einem Durchgang pro Kanal; das Rauschen springt dabei exakt mit.
   - Beim parallelen Routing addiert NEON.
2. **Oszillatoren** (`0002`, `render_wave.h`, `vector_rendering_function.h`, `voice.cpp`):
   - Interpolationsgewichte und Tabellenlesen mit NEON.
   - Tiefe Säge/Rechteck und Dreieck mit 4 Samples auf einmal.
   - Divisionen bei Sync und PW über die Gleitkomma-Einheit, mit exakter Korrektur.
3. **CPU-Monitor** (`0003`): wie in der Messversion `diag/`.

## Warum der Klang gleich bleibt

Jede Änderung wurde von einem Umsetzer bewiesen und von einem unabhängigen Gegenprüfer nachgerechnet:
- **Prüfsummen** aller Ausgänge im Emulator, v12 gegen v12-perf, alle identisch:
  - feste Fälle
  - Zufallsfälle: Filter 2 048 × 64 Blöcke, Oszillatoren 30 000 Fälle
  - eigene Angriffsfälle der Gegenprüfer: etwa 6 000 bzw. 24 000, mit Extremwerten, Resets, Blöcken von 1–5 Samples, mono und stereo
  - `getTableNumber` für alle 4,3 Milliarden möglichen Eingaben
- **Im ganzen Song:** Alle 650 108 Oszillator-Aufrufe und alle geprüften Filter-Aufrufe liefern bei gleicher Eingabe exakt dieselbe Ausgabe. Stimmen pro Spur und alle anderen Bereiche sind unverändert.
- **Begründung pro Änderung:** Rundung, Überlauf, Aliasing und alle Sonderpfade. Beispiel: `smmlar(x, y, 0) = x`.
- **Build:** keine neuen Warnungen. Zwei komplette Neubauten ergeben dieselbe SHA-256. `git am` aller Patches (`patches/0001–0012`, dann `perf/0001–0003`) auf `release_1_2_1` ergibt exakt diesen Stand.

## Grenzen und offene Punkte

- **Der Emulator zählt Befehle, nicht Takte.** Wie viel auf dem Gerät übrig bleibt, zeigt der Vergleich mit dem CPU-Monitor (unten).
- **Der Filtercode ist 17,8 KB grösser** (spezialisierte Schleifen). Im Emulator ist das unsichtbar. Auf dem Gerät kann es den Befehls-Cache stärker belasten. Zeigt die Messung das, werden seltene Pfade verkleinert.
- **Der Song-Ausgang hängt vom Speicher-Layout der Firmware ab,** schon in v12. Die ganze WAV-Datei taugt darum nicht als Beweis, der Vergleich pro Aufruf schon. Die Ursache wird untersucht.
- **Nicht enthalten:**
  - Wavetable-Schleife, Lautstärke/Reverb-Send pro Spur, Aufwand pro Block und Stimme (folgen)
  - die Kompressor-Korrektur (nicht bitgleich)
  - der L2-Cache

## Auf dem Gerät vergleichen

1. Den Test-Song aus `diag/loadtest-card.zip` auf die Karte kopieren (siehe `diag/README.md`).
2. **Messversion** `diag/deluge-1.2.1-mastertune-v12-diag-d0d03dcc.bin` flashen:
   - Song `MT_LOADTEST` laden, Settings → CPU monitor → On
   - `tools/cpu_monitor.html` verbinden, Play, etwa 1 Minute laufen lassen, CSV speichern
3. **v12-perf** flashen und dasselbe wiederholen.
4. Beide CSV-Dateien hier hochladen.

Zu erwarten:
- mehr Stimmen (V) bei ähnlicher CPU-Anzeige
- mit dem Test-Song etwa gleich viele Abschaltungen pro Sekunde (im Emulator 14 bei beiden), aber mehr Stimmen, die gleichzeitig klingen
- bei eigenen, normalen Songs eine tiefere CPU-Anzeige und seltener Direness über 0

Zurück zu v12 geht jederzeit mit der v12-Datei.
