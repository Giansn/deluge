# Optimierung: gesammelte Erkenntnisse

Stand: 26.09.2026, Firmware v12 (`mastertune-v12`, Commit `f89b478c`). Das Dokument wird ergänzt, sobald laufende Messungen fertig sind (Abschnitt 6 und 8).
Rohdaten der Mess- und Prüf-Agenten: `research/raw/*.json`. Benchmarks: `tests/bench/<bereich>/run.sh`.

## 1. Methode

**Messung**
- Der echte Firmware-Code läuft im Emulator (unicorn 2.1.4) als Cortex-A9-Code. Er ist mit den Flags der Firmware gebaut: Thumb-2, NEON Hard-Float, `-O2`, `-funsafe-math-optimizations`, `-fno-inline-functions`. Nur LTO fehlt.
- Gezählt werden die ausgeführten Befehle pro Block von 128 Samples (2,9 ms), nach dem Aufwärmen gemittelt über etwa 10 Blöcke. `ARM_PROFILE=1` liefert die Befehle pro Funktion.
- Skala: **% CPU = Befehle pro Block / 1 161 000** (400 MHz, 1 Befehl pro Takt).
- Grenzen:
  - Cache, Pipeline und Doppel-Ausführung fehlen. Die Zahlen taugen darum für Vergleiche (vorher/nachher, Teil gegen Teil), als absolute Last sind sie grob.
  - Effekte des Speichers (SDRAM, L2-Cache) sind nicht sichtbar.

**Qualitätsregel: bitgleich**
Eine Optimierung zählt als sicher, wenn der Ausgang Bit für Bit gleich bleibt. Nachgewiesen wird das in vier Stufen:
1. Prüfsummen vorher/nachher, immer ARM gegen ARM. Auf dem PC runden die Ersatzfunktionen von `*_rounded` in `fixedpoint.h` anders.
2. Viele Eingaben, auch zufällige, und Grenzfälle:
   - krumme Blockgrössen (Render-Fenster werden an Ticks geschnitten)
   - Parameterwechsel mitten im Klang
   - Stille, Vollaussteuerung und absichtliche Überläufe
3. Der ganze Song im Volllast-Test gibt vorher und nachher denselben Ausgang (Abschnitt 6).
4. Ein mathematisches Argument, von einem Gegenprüfer kontrolliert.

Stolperfallen:
- Wegen Fast-Math kann eine umgestellte Gleitkomma-Rechnung andere Bits ergeben.
- NEON-Zugriffe mit Ausrichtungshinweis brechen auf der Hardware bei falscher Ausrichtung ab, im Emulator womöglich nicht.

Was nicht bitgleich ist, wird wie bei Reverb und Delay gemessen: Abweichung in dB, Spektrum, Knackfreiheit. Es wird im README als Klangänderung gekennzeichnet und getrennt ausgeliefert.

**Schon umgesetzt, frühere Pakete**
- v3 (Paket A): NEON-Verschiebung im Interpolationspuffer, Compiler-Flags, UI-Scheduler, Encoder.
- v4 (Paket B):
  - stille Ketten überspringen
  - Voice-Culling aus der Community
  - Ausgabe in einem Durchgang
  - `memset`
- v9: Streaming-Fixes (Priorität der Lade-Warteschlange, mehr Vorauslesen, Pin-Zähler).
- v11: Delay mit kubischer Interpolation. Moduliert kostet es 3,5 % statt 5,0 % in v10, weil meist nur noch ein Puffer läuft.

**Warum CPU-Einsparung auch Klangqualität ist:** Unter Last setzt die Firmware `AudioEngine::cpuDireness` hoch und spart dann an der Qualität:
- Oszillatoren nehmen gröbere Wellentabellen (`voice.cpp`, `tableNumber < cpuDireness + 6`).
- Gepitchte Samples werden linear statt mit Sinc interpoliert (`sample_controls.cpp`).
- Im Extremfall werden Stimmen abgeschaltet (Culling).

Jede Einsparung senkt deshalb die Wahrscheinlichkeit, dass volle Songs so an Qualität verlieren.

## 2. Kosten im Überblick

Befehle pro Block von 128 Samples, pro Instanz oder Stimme, im Emulator gezählt.

| Baustein | Befehle | CPU | Bemerkung |
|---|---|---|---|
| Oszillator Säge/Rechteck/Sinus (Tabelle) | 1 500–1 600 | 0,13 % | pro Oszillator und Stimme |
| Oszillator Rechteck mit PW | 2 600 | 0,23 % | |
| Oszillator Säge Unison 4 / 8 | 6 300 / 12 500 | 0,54 / 1,08 % | |
| Filter LP24 mono / stereo | 10 800 / 21 600 | 0,93 / 1,86 % | pro Stimme; ab etwa 10 % Resonanz der tanh-Pfad |
| Filter LP24 mit Drive, Oversampling | 27 000 | 2,32 % | hohe Cutoff und Resonanz |
| Filter LP24 + HPF stereo | 35 800 | 3,09 % | |
| SVF mono | 7 900 | 0,68 % | |
| Sample nativ stereo | 3 500 | 0,30 % | ohne Tonhöhenänderung |
| Sample linear stereo (+7 HT) | 12 000 | 1,03 % | |
| Sample Sinc stereo (+7 HT) | 20 800 | 1,79 % | |
| Timestretch Sinc stereo | 40 300 | 3,47 % | zwei Leseköpfe |
| DX7 modern (NEON) / MkI | 8 700 / 26 600 | 0,75 / 2,29 % | pro Stimme |
| Chorus / Flanger | 8 900 / 9 100 | 0,77 / 0,78 % | pro Spur |
| Phaser | 19 900 | 1,72 % | |
| EQ Bass + Höhen | 7 200 | 0,62 % | |
| SRR 1/4 + Bitcrush | 5 400 | 0,46 % | |
| Kompressor (Song) | 14 900 | 1,28 % | |
| Delay ruhig / moduliert / mit Filtern | 9 200 / 40 100 / 44 400 | 0,8 / 3,5 / 3,8 % | v11 |
| Delay Analog-Modus, moduliert, mit Filtern | 97 000 | 8,4 % | davon Impulsantwort 42 800 (3,7 %) |
| Reverb Freeverb / Mutable / Digital | 70 200 / 39 100 / 53 600 | 6,0 / 3,4 / 4,6 % | einmal pro Song |
| Drone 1 Sinuston / 1 binaural | 4 100 / 6 300 | 0,35 / 0,54 % | |
| Drone 16 binaural mit Obertönen | 79 500 | 6,85 % | |

**Einordnung:** Kosten pro Stimme (Oszillatoren, Filter, Sample-Lesen) vervielfachen sich mit der Stimmenzahl und bestimmen darum die Last in vollen Songs. Beispiel: 30 Stimmen mit LP24-Filter kosten allein etwa 28 % (mono) bis 56 % (stereo) CPU nach dieser Skala. Die Song-Effekte kommen nur einmal vor. Die echte Verteilung im vollen Song zeigt der Volllast-Test (Abschnitt 6).

## 3. Befunde und Vorschläge je Bereich

Alle Vorschläge unten sind nach Angabe der Mess-Agenten **bitgleich** möglich. Das ist geschätzt und muss bei der Umsetzung bewiesen werden. Einsparungen gelten pro Block von 128 Samples.

### 3.1 Filter (`dsp/filter`)
- Hot Loops:
  - `LpLadderFilter::doFilter`/`doFilterStereo`: etwa 84 Befehle pro Sample für LP24, über 99 % der Kosten.
  - `HpLadderFilter::doFilter`: etwa 60 pro Sample, 89 mit 2D-tanh.
  - `SVFilter::doFilter`: etwa 61 pro Sample.
- Hauptursache: Pro Sample werden etwa 20 Werte neu geladen und 5–7 gespeichert. Der Compiler kann Aliasing mit dem Ausgabepuffer nicht ausschliessen.

| Vorschlag | Ort | Einsparung | Risiko |
|---|---|---|---|
| Zustand, Koeffizienten und `jcong` in lokalen Variablen; `sampleIncrement==1` spezialisieren | lpladder/hpladder/svf `doFilter*` | LP24 etwa 1 000 mono, 2 000 stereo; HP/SVF 500–700 | niedrig–mittel (Register) |
| Saturations-Verzweigung (`morph>0 \|\| resonance>510M`) aus der Schleife | `lpladder.h` `scaleInput` | 400–600 mono | niedrig |
| Rückkopplungs-Summen mit `smmlar` statt `smmulr`+`add` | lpladder `do24dB`/`do12dB`/`doDrive`, hpladder | 384 / 250 / 128 | niedrig (Rundung identisch) |
| HP-Ladder: `temp_fc` bei `morph==0`, Resonanz-Zweige aus der Schleife, `hpfLastWorkingValue` nur am Blockende | `hpladder.cpp` | etwa 1 300 mono (17 %) | niedrig |
| SVF: `smmlar` weglassen, wenn `c_band`/`c_high` 0 ist; `band_mode` aus der Schleife | `svf.cpp` | etwa 640 mono | niedrig |
| NEON-Addition beim parallelen Routing | `filter_set.cpp` | 800 mono, 1 600 stereo | niedrig |

Gesamt: LP24 15–20 %, HP etwa 20 %, SVF etwa 12 %. Bei 30 Stimmen mit Filter sind das **etwa 4–5 % CPU**.
Nicht empfohlen: L und R in NEON-Lanes. `vqrdmulh` rundet anders, das Ergebnis wäre nicht bitgleich.

### 3.2 Oszillatoren (`render_wave.h`, `vector_rendering_function.h`, `voice.cpp`)
- Hot Loops:
  - `renderWave`: 1 371 pro Aufruf (87 %), 42 Befehle pro 4 Samples.
  - `renderPulseWave`: 2 468.
  - Dreieck unter etwa 711 Hz als skalare Schleife.
  - Divisionen im PW- und Sync-Setup (`__udivmoddi4` 626).

| Vorschlag | Ort | Einsparung | Risiko |
|---|---|---|---|
| Interpolationsgewichte per NEON aus dem Phasenvektor, `{0}`-Inits weg, `applyAmplitude` als Template | `vector_rendering_function.h`, `render_wave.h` | `renderWave` etwa 290 pro Aufruf (−21 %), Puls etwa 500; Unison 8 etwa 2 300 | niedrig |
| Dreieck-Schleife in NEON (`smmlar` exakt als `vmull`+`vrshrn`+`vadd`) | `voice.cpp` `renderOsc` | etwa 1 100 pro Aufruf | niedrig–mittel |
| Crude Saw/Square (unter etwa 72 Hz) vektorisieren | `voice.cpp` | Saw etwa 430, Square etwa 850 | niedrig |
| PW/Sync-Divisionen per VFP-Double mit Korrektur | `voice.cpp` | etwa 200, Analog-Square-PW +550 | mittel |
| `getTableNumber` per `clz` | `voice.cpp` | etwa 30 | niedrig |

- Nicht gemessen:
  - Wavetable-Oszillator (braucht Sample, Cluster und FFT)
  - Pan-Schleife bei Stereo-Unison, geschätzt etwa 1 300 pro Teil. Sie ginge ebenfalls bitgleich mit NEON.

### 3.3 Effekte (`mod_controllable_audio.cpp`, `rms_feedback.cpp`, `impulse_response_processor.h`)

| Vorschlag | Ort | Einsparung | Risiko |
|---|---|---|---|
| **Analog-Delay-Impulsantwort mit NEON:** `vqrdmulhq_s32` mit halbierten Koeffizienten. Alle 26 Koeffizienten sind gerade, daher identisch mit `smmulr`. | `impulse_response_processor.h` `process` | 42 800 → etwa 6 000–8 000, **etwa −3 % CPU pro Analog-Delay** | niedrig |
| Phaser: Allpass-Zustand in Registern, L/R getrennt, 6 Stufen entrollt | `processFX` (PHASER) | 6 000–7 500 | niedrig |
| Kompressor: `calcRMS` in die Render-Schleife, Zustand lokal, Blend ohne `memcpy` | `rms_feedback.cpp` | etwa 2 000 (+400 bei Blend) | niedrig |
| EQ: Zustände lokal, Schleife nach Bass/Höhen spezialisiert | `processFX`/`doEQ` | etwa 2 000 | niedrig |
| Mod-FX-Schleife pro Typ (Template), LFO und Index lokal | `processFX` (Chorus/Flanger) | 1 500–2 500 | niedrig |
| SRR-Zustand lokal, Bitcrush mit NEON `vand` | `processSRRAndBitcrushing` | SRR 1 000–1 500, Bitcrush 450 | niedrig |

Nicht gemessen: Grain.

### 3.4 Samples, Timestretch, DX7 (`sample_low_level_reader.cpp`, `interpolate.h`, `dsp/dx`)
- Hot Loops:
  - `readSamplesResampled`: Sinc stereo 13 278 pro Block.
  - `interpolate()`: ein Funktionsaufruf pro Sample, 58 Befehle stereo.
  - Linear: `jumpForwardLinear` und `interpolateLinear` ebenfalls als Aufruf pro Sample.
  - MkI-Engine: etwa 30 Befehle pro Sample und Operator, mit Verzweigungen.

| Vorschlag | Ort | Einsparung | Risiko |
|---|---|---|---|
| Sinc-Schleife: `interpolate()` inline, `oscPos` und Amplitude lokal, Template nach Kanälen, Verdichtung und Cache-Schreiben | `readSamplesResampled` + `interpolate.h` | 3 000–4 500 pro Sinc-Stimme (15–20 %), doppelt bei Timestretch | niedrig–mittel |
| Interpolationspuffer für den ganzen Block in NEON-Registern (`vext`) | `readSamplesResampled`, `shift_buffer.h` | 1 500–2 000 stereo | mittel |
| Stereo-Reduktion mit `vpadd` | `interpolate.h` | etwa 650 | niedrig |
| Lineare Schleife inline, Zustand lokal | `readSamplesResampled` (else-Zweig) | 5 000–6 000 (etwa 45 %) | niedrig |
| MkI-Engine: `sinLog` ohne Verzweigungen, Vorzeichen per XOR-Maske | `EngineMkI.cpp` | 5 000–6 000 pro MkI-Stimme (20 %) | niedrig |
| `neon_fm_kernel` mit n=128 erlauben, Stack-Reloads in `compute_fb` | `fm_core.cpp`, Assembler | etwa 550 | mittel, wenig Nutzen |

Nicht gemessen: `considerUpcomingWindow`/Cluster und `TimeStretcher::hopEnd`.

### 3.5 Drone (v12)
- Profil:
  - Äussere Schleife in `Drone::render`: 1 169 Befehle pro Block
  - `memset`: 222
  - `renderVoice`: etwa 20 Befehle pro Sample
- Kandidaten:
  - NEON für die Umwandlung Float → Int und das Mischen
  - kein `memset` des Mischpuffers (erste Stimme schreibt statt addiert)
- Die Drone rechnet in Float mit Fast-Math. Bitgleichheit ist darum nur bei unveränderter Reihenfolge der Operationen sicher, sonst wird gemessen.

## 4. Gefundene Fehler

- **Kompressor:** `calcRMS` verwendet `hpfL` für beide Kanäle, `hpfR` bleibt ungenutzt (`rms_feedback.cpp`, 1.2.1). Die Korrektur ist nicht bitgleich und kommt darum als Klangänderung getrennt.
- Die Befunde aus der v12-Prüfung sind behoben und im README v12 beschrieben. Rohdaten: `raw/review-v12-drone.json`.

## 5. SD-Karte, Treiber, Speicher

**So liest der Deluge (1.2.1).** Geprüft von einem Agenten und einem Gegenprüfer, Rohdaten in `raw/sd-driver.json`.
- **Bus:** 4 Bit, High-Speed per CMD6, P1φ 66,67 MHz / 2 = **33,3 MHz**. Das sind 16,7 MB/s brutto und etwa 15,5 MB/s netto.
  - Kein Spielraum: P1φ ist fest, `/1` ergäbe 66,7 MHz und läge über der Norm, UHS (1,8 V, CMD11) gibt es nicht.
  - Karten ohne High-Speed laufen mit 16,7 MHz.
- **Streaming umgeht FatFs:** Die Sektoradressen der Cluster sind vorberechnet (`sdAddress`). FASTSEEK, exFAT und `FF_FS_TINY` spielen für die Wiedergabe keine Rolle.
- **Pro Cluster** (Grösse = Cluster der Karte, meist 32 KB):
  - Ablauf: `CMD13`, `CMD18` mit automatischem `CMD12`, DMA mit 64-Byte-Bursts, dann nochmals `CMD13`.
  - Geschätzt 2,2–2,6 ms. Davon Übertragung etwa 2,0 ms, Zugriffszeit der Karte 0,1–0,5 ms mit Spitzen von 10–100 ms, Befehle etwa 50 µs.
  - Das ergibt etwa 13 MB/s oder rund 70 Stereo-16-Bit-Stimmen bei günstigen Zugriffen.
  - Laut Rohans Kommentar in `diskio.c` braucht eine schlechte Karte gelegentlich 200-mal länger als eine gute.
- **Warten:** `USE_TASK_MANAGER` ist aktiv. Beim Warten auf Befehl und DMA gibt der Treiber an `TaskManager::yield` ab, dort laufen alle Tasks, auch UI und MIDI.
  - Hardware-Warteschlange mit Tiefe 1.
  - Die Pause zwischen DMA-Ende und nächstem Befehl hängt davon ab, wie lange der gerade laufende Task dauert, z. B. OLED-Rendern. Das ist die eigentliche Stellschraube für die Latenz.
- **Schreiben** (Aufnahme): 32 KB per `CMD25`, ohne Vorlöschen (ACMD23). Das Audio läuft weiter. Die Hauptschleife steht typisch 3–10 ms, bei Garbage Collection der Karte bis 250–500 ms.

**Einfluss einer schnelleren Karte**
- Kein Einfluss auf die DSP-Last.
- Wenig Einfluss auf Drums und kurze Samples, weil sie im SDRAM bleiben.
- Mittlerer Einfluss auf viele lange Samples und Audio-Spuren: Hier zählt die Zugriffszeit.
- Grösster Einfluss auf Aufnahmen: Schreibspitzen führen zu Aussetzern.
- Empfehlung:
  - Markenkarte mit Class 10 / U1 oder besser, dazu A1; V30 bei vielen Aufnahmen.
  - A2 und UHS bringen nichts.
  - FAT32 ist Pflicht (kein exFAT), 32 GB SDHC ist der einfachste Fall.

**Treiber-Optionen**

| Option | Gewinn | Urteil |
|---|---|---|
| Takt über 33,3 MHz, UHS | keiner möglich | Hardware-Grenze |
| `CMD13` vor und nach dem Lesen weglassen | 10–20 µs pro Cluster (<1 %) | nein, Zustandsprüfung geht verloren |
| Mehrere Cluster mit einem `CMD18` | unbelegt: Puffer nicht zusammenhängend, Warteschlange nach Priorität | nein |
| Asynchrones Lesen per Interrupt | Hauptschleife 2–3 ms pro Cluster frei | nein, hohes Risiko (Reentranz, FatFs) |
| ACMD23 vor dem Schreiben | 0–20 % Schreibtempo, kartenabhängig | vorerst nein (Gegenprüfer: Risiko überschätzt) |
| Grössere Schreibblöcke beim Aufnehmen (128 KB) | weniger Busy-Phasen | vielleicht später |
| **Während SD-Wartezeiten nur kurze Tasks zulassen** | geringere Latenz beim Streaming | prüfenswert, zuerst messen |
| **Mess-Build:** `REPORT_LOAD_TIME` mit tieferer Schwelle, `REPORT_AWAY_TIME` | echte Latenzen statt Schätzung | ja, nur mit Hardware |

**Aus der Community (main seit 1.2.1)**
- **L2-Cache:**
  - 1.2.1 nutzt nur L1 (32 KB Code, 32 KB Daten), die 128 KB L2 bleiben aus.
  - Upstream: `c44d2476` (12/2024, nur Code), `a2f8bc51` (04/2026, auch Daten). Dazu die Cache-Pflege für DMA: `260ac76c` (Flush vor dem Schreiben, OLED), `5d3d093e` (alle Caches leeren), `c0586341` (Chainloader).
  - **Vermutlich der grösste CPU-Gewinn,** vor allem bei Daten im externen SDRAM (Samples, Delay- und Reverb-Puffer). Upstream nennt keine Zahl, der Emulator kann es nicht messen.
  - Risiko mittel: DMA-Kohärenz bei SD, OLED, USB und Audio.
  - Plan: eigene Testversion mit allen Nachbesserungen, dazu eine CPU-Anzeige zum Vergleichen auf dem Gerät.
- **MIDI/Clock während `routineForSD()`** (`9cd09fb7`):
  - Mit Task-Manager ruft `routineForSD()` nur `AudioEngine::routine()` auf, nicht `playbackHandler.routine()`. Die externe Clock stockt deshalb, während `routineForSD()` läuft: Song laden (`load_song_ui.cpp`), USB, Flash.
  - Eigene Prüfung: In 1.2.1 ist das gleich aufgebaut (`USE_TASK_MANAGER` gesetzt, `#ifndef USE_TASK_MANAGER` vor `playbackHandler.routine()` in `routine_()`).
  - Der Research-Agent hielt die Änderung für nicht übertragbar, der Gegenprüfer hat das nicht geprüft. Vor der Portierung nochmals am Code bestätigen.
  - Klein, niedriges Risiko.
- `FF_FS_TINY 0`: nur beim Laden von Dateien (XML) etwas schneller, nicht beim Streaming.
- Am Treiber selbst gibt es upstream keine Beschleunigung.

## 6. Volllast-Test mit einem vielspurigen Song

**Läuft** (Workflow `song-load-run`, `tests/song/`).
- Aufbau: die echte v12-Firmware (`deluge.elf`) im Emulator, ohne Hardware-Init, nur der Audioteil.
  - SD über Hooks in `diskio.c` auf ein Kartenabbild.
  - `getTxBufferCurrentPlace()` liefert je 128 neue Samples.
  - Befehle werden mit einem Zähler in C pro Basisblock gezählt.
- Song: etwa 12 Spuren bei 120 BPM.
  - 8 Synths mit Akkorden, Unison, LP24/HPF, LFO, Mod-FX, Delay, Kompressor, Bitcrush und Arp
  - ein Kit mit 8 Spuren; die Kick steuert die Sidechain
  - eine Audiospur
  - Reverb, Sidechain, Drone mit 4 Tönen
  - Ziel etwa 30 Stimmen, ohne Voice-Culling
- Ergebnisse: *folgen*

## 7. Priorisierung (vorläufig, wird nach dem Volllast-Test festgelegt)

**Bitgleiche Optimierungen, Kandidaten fürs Leistungspaket:**
1. Filter: pro Stimme, bei 30 Stimmen etwa 4–5 % CPU.
2. Sample-Lesen: Sinc 15–20 %, linear etwa 45 % pro Sample-Stimme.
3. Analog-Delay-Impulsantwort mit NEON: etwa −3 % pro Instanz.
4. Oszillatoren: NEON-Gewichte, wirkt vor allem bei Unison.
5. Phaser, Kompressor, EQ, MkI-Engine.
6. Drone mit NEON.

**Getrennt, mit Messung auf dem Gerät:**
- L2-Cache
- MIDI/Clock-Fix im SD-Routine
- Kompressor-Korrektur (`hpfR`)

**Vorgehen je Optimierung:**
1. Ein Agent setzt um, mit bitgleichem Nachweis nach Abschnitt 1 und gezählter Einsparung.
2. Ein Gegenprüfer kontrolliert.
3. Zum Schluss der ganze Song vorher/nachher identisch.

## 8. Offen

- [ ] Volllast-Test: Ergebnisse eintragen (Abschnitt 6), Priorisierung festlegen (Abschnitt 7).
- [ ] Wavetable-Oszillator, Grain, `hopEnd` und die Stereo-Unison-Pan-Schleife messen, falls der Volllast-Test sie als relevant zeigt.
- [ ] MIDI/Clock-Fix: Übertragbarkeit am Code bestätigen.
- [ ] L2-Cache: Nachbesserungen vollständig sammeln.
- [ ] **Messversion** (v12 + Messanzeige, läuft, Workflow `diag-build`):
  - CPU-Last pro Block (Mittel/Spitze), Stimmen, `cpuDireness`, Culling, SD-Latenz pro Cluster.
  - Anzeige auf dem OLED, per SysEx nur über USB, dazu eine Web-MIDI-Seite `tools/cpu_monitor.html` mit CSV-Export.
  - Ziele:
    - Emulator gegen Hardware kalibrieren, mit demselben Test-Song wie im Volllast-Test
    - echte Ausgangslage für L2-Cache und Optimierungen
