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
  - 1.2.1 nutzt nur L1 (32 KB Code, 32 KB Daten), die 128 KB L2 bleiben aus (`resetprg.c` ruft nur `R_CACHE_L1Init()` auf).
  - Upstream in zwei Stufen:
    - `c44d2476` (12/2024): L2 nur für Code. Daten sind per Lockdown ausgesperrt (`REG9_D_LOCKDOWN0 = 0xFFFFFFFF`), DMA ist damit nicht betroffen. 19 Zeilen.
    - `a2f8bc51` (04/2026): auch Daten, erst am Ende des Boots freigegeben, dazu Prefetch (`REG1_AUX_CONTROL |= 0x30000000`). SD-Lesen leert vor dem DMA L1 und L2, die FatFS-Puffer sind auf 32 Byte ausgerichtet.
    - `260ac76c` (einen Tag später): vor dem Schreiben auf die Karte und vor dem OLED-DMA auch L2 zurückschreiben.
    - `c0586341`, `5d3d093e`: Chainloader (Firmware per USB-SysEx laden). Der Fehler steckt schon in 1.2.1 (L1), unabhängig von L2.
  - DMA-Pfade in v13, am Code geprüft:
    - SD lesen und schreiben (`sd_read.c`, `sd_write.c`) und OLED (`oled_low_level.c`, `oled.cpp`) brauchen L2-Pflege, genau die Stellen von upstream.
    - Audio (SSI) und UART (MIDI, PIC) laufen über die ungecachte Spiegeladresse: nicht betroffen.
    - USB läuft ohne DMA (`USB_CFG_DMA` aus): nicht betroffen. SD über USB (v7) und das schnellere Speichern (v13) schreiben über FatFS, also über `sd_write.c`.
  - **Vermutlich der grösste CPU-Gewinn,** vor allem bei Daten im SDRAM: Delay- und Mod-FX-Puffer (`allocLowSpeed`), Wellentabellen und Samples (`allocStealable`). Upstream nennt keine Zahl, der Emulator kann es nicht messen.
  - Risiko: nur Code gering. Mit Daten mittel: Ein vergessener Pfad gibt seltene Fehler, im schlimmsten Fall falsche Bytes in Dateien auf der Karte.
  - Plan: zuerst eine Testversion nur mit Code-L2, am Gerät messen (CPU-Monitor von v13, Test-Song `diag/loadtest-card.zip`). Daten erst danach, mit allen Nachbesserungen.
- **MIDI/Clock während `routineForSD()`** (`9cd09fb7`):
  - Mit Task-Manager ruft `routineForSD()` nur `AudioEngine::routine()` auf, nicht `playbackHandler.routine()`. Die externe Clock stockt deshalb, während `routineForSD()` läuft: Song laden (`load_song_ui.cpp`), USB, Flash.
  - Eigene Prüfung: In 1.2.1 ist das gleich aufgebaut (`USE_TASK_MANAGER` gesetzt, `#ifndef USE_TASK_MANAGER` vor `playbackHandler.routine()` in `routine_()`).
  - Der Research-Agent hielt die Änderung für nicht übertragbar, der Gegenprüfer hat das nicht geprüft. Vor der Portierung nochmals am Code bestätigen.
  - Klein, niedriges Risiko.
- `FF_FS_TINY 0`: nur beim Laden von Dateien (XML) etwas schneller, nicht beim Streaming.
- Am Treiber selbst gibt es upstream keine Beschleunigung.

## 6. Volllast-Test mit einem vielspurigen Song

`tests/song/run.sh <Firmware-Baum|deluge.elf> [out] [Takte]`, Laufzeit etwa 1 Minute. Rohdaten: `raw/song-load-run-1.json`.

**Aufbau**
- Die **unveränderte v12-`deluge.elf`** läuft im Emulator.
- Ablauf: `resetprg`/`deluge_main` bis `registerTasks`, dann `setupStartupSong` (lädt `SONGS/DEFAULT.XML` von einem FAT32-Abbild mit 32-KB-Clustern), `playButtonPressed`, danach `AudioEngine::routine()` pro Fenster.
- Nachgebildet ist nur die Hardware: Timer, DMA, SPI, Flash. Die SDHI-Initialisierung wird übersprungen.
- Gezählt wird die ganze Routine, inklusive Ticks und Ausgabe. Das Ergebnis ist deterministisch: zwei Läufe sind identisch.

**Song:** 120 BPM, alles gleichzeitig.
- 8 Synths, alle mit 4-Ton-Akkorden, Saw+Square und LP24:
  - Unison 4 + HPF + LFO + Chorus
  - Unison 4 + Phaser + Delay
  - HPF + Flanger + Kompressor
  - Bitcrush + SRR
  - Arp 16tel + Delay
  - Unison 4 + HPF
  - FM (DX7, Algorithmus 5)
  - Wavetable
- Kit mit 8 Spuren in Sechzehnteln; die Kick steuert die Sidechain, zwei Spuren sind gepitcht.
- Audiospur: ein Loop mit 100 BPM, per Timestretch auf 120 BPM.
- Reverb Mutable, 4 binaurale Drone-Töne.
- Ausgang: Peak −1,5 dBFS, RMS −18 dBFS, nichts übersteuert, kein NaN.

**Ergebnis, Lauf 1** (vom Gegenprüfer nachgerechnet)
- **Mittel 962 920 Befehle pro Fenster von 128 Samples = 83 % CPU. Spitze 1 303 588 = 112 %,** bei Akkordwechseln.
- Stimmen: Mittel 29,7, Spitze 36.
- Die Kosten wachsen etwa linear: **rund 126 000 Befehle fix pro Fenster plus 28 000 pro Stimme** (r = 0,98).
- An Clock-Ticks gekürzte Fenster (10 Samples) kosten trotzdem etwa 200 000. Der Aufwand pro Fenster und Stimme ist also hoch.
- Variante mit 1,6 s Release: 58 Stimmen, Mittel 108 %, Spitze 151 %.
- Digital-Reverb statt Mutable: 54 000 statt 40 000 Befehle pro Fenster.

| Bereich | Anteil | Befehle pro Fenster |
|---|---|---|
| Filter (LP/HP-Ladder) | 35,1 % | 337 500 |
| Oszillatoren inkl. Wavetable | 26,3 % | 253 000 |
| Spur-FX (Mod-FX, Bitcrush, Volume/Pan/Reverb-Send) | 10,6 % | 101 700 |
| Stimmen, Patcher, Hüllkurven, LFOs | 9,3 % | 89 700 |
| Reverb (Mutable) | 4,1 % | 39 900 |
| Sidechain, Kompressoren | 3,2 % | 31 200 |
| Sample-Lesen, Interpolation, Timestretch | 2,2 % | 21 300 |
| Delay | 2,1 % | 20 600 |
| Drone | 2,1 % | 20 500 |
| FM (DX7) | 2,1 % | 20 000 |
| Speicher, Sonstiges | 1,4 % | 13 600 |
| Song-/Master-FX, Ausgabe | 1,2 % | 11 500 |
| Playback, Sequencer | 0,3 % | 2 500 |

Top-Funktionen:

| Funktion | Befehle | Anteil |
|---|---|---|
| `LpLadderFilter::doFilter` | 252 600 | 26 % |
| `Voice::renderOsc` | 178 200 | 18,5 % |
| `HpLadderFilter::doFilter` | 78 100 | |
| `Sound::render` | 75 900 | |
| `WaveTable::doRenderingLoop` | 56 700 | |
| `ModControllableAudio::processReverbSendAndVolume` | 56 700 | 5,9 % |
| `reverb::Mutable::process` | 39 800 | |
| `ModControllableAudio::processFX` | 38 000 | |
| `RMSFeedbackCompressor::render` | 24 300 | |
| `Delay::process` | 19 800 | |

**Abgleich mit den Einzel-Benchmarks** (Gegenprüfer): Die Werte passen.
- LP-Ladder: 11 200 pro Stimme im Song, 10 800 im Bench.
- HP-Ladder: 7 200 gegen 7 700.
- Oszillatoren: 179 000 hochgerechnet, 178 000 gemessen.

**Lauf 2: Korrekturlauf** (Rohdaten `raw/song-load-run-2.json`). Akkorde und Arp spielen jetzt den ganzen Takt, das ist die Dauerlast.

| | Bedarf ohne CPU-Schutz | wie auf dem Gerät (Culling und Direness nachgebildet) |
|---|---|---|
| Befehle pro 128 Samples (Mittel) | 1 366 716 = **118 %** | 701 452 = **60 %** |
| Spitze | 2 143 439 = 185 % | 1 714 556 = 148 % (Akkordwechsel) |
| Perzentile 5/50/95/99 | – | 57 / 59 / 68 / 86 % |
| Stimmen Mittel / max | 45,2 / 66 | 20,7 / 52 |
| Abgeschaltete Stimmen | – | **14,2 pro Sekunde** (94 soft, 20 force in 4 Takten) |
| `cpuDireness` | – | **in 100 % der Fenster auf 14** (Maximum) |

- Die Pads behalten mit CPU-Schutz nur 1,5–2,1 Stimmen. **Etwa die Hälfte der Akkordtöne wird abgeschaltet.**
- Direness 14 senkt auch die Kosten pro Stimme, z. B. weil das Oversampling im LP-Ladder wegfällt.
- Anteile bei Dauerlast: Filter 39 %, Oszillatoren 28,9 %, Stimmen/Patcher/Hüllkurven 9,5 %, Spur-FX 7,8 %, Reverb 2,9 %, FM 2,7 %, Sidechain/Kompressoren 2,3 %, Sample-Lesen 1,6 %, Delay 1,5 %, Drone 1,5 %.
- Top-Funktionen: `LpLadderFilter::doFilter` 403 108, `Voice::renderOsc` 279 575, `HpLadderFilter::doFilter` 121 254, `Sound::render` 110 416, `WaveTable::doRenderingLoop` 88 073, `processReverbSendAndVolume` 59 412.
- **Stimmen pro Spur** ohne CPU-Schutz (Mittel / max / Anteil der Zeit):
  - Pads und Wavetable 5,6 / 8 / 100 %
  - Arp 2,3 / 3
  - FM 4,5 / 8
  - Kit 0,1–1,0
- **Kurze Fenster:** 64 von 2 816 Fenstern haben wegen Clock-Ticks nur 10 Samples. Sie kosten pro Sample 2,3-mal so viel. Grob gilt: ein Aufruf von `routine()` kostet etwa 154 000 plus 9 500 pro Sample. Würde `routine()` alle 64 statt 128 Samples aufgerufen, stiege der Bedarf von 118 % auf etwa 131 %.
- **Reverb** über dieselben Samples: Mutable 39 909, Digital 54 343 Befehle pro Fenster (+14 434, +1,2 % CPU). Sonst ist alles gleich.

**Schwellen des CPU-Schutzes** (`audio_engine.cpp`, gleich in 1.2.1 und upstream main): Grundlage ist die Renderzeit des letzten Aufrufs, gemessen in Samples.

| Renderzeit (Samples) | Anteil eines 128er-Fensters | Folge |
|---|---|---|
| ab 50 | 39 % | `cpuDireness` 1: erste Qualitätsabsenkung |
| ab 63 | 49 % | Direness 14 (Maximum): gröbste Oszillator-Tabellen, lineare statt Sinc-Interpolation, kein Filter-Oversampling |
| ab 80 | 62,5 % | Soft-Culling: Stimmen schnell ausblenden |
| ab 112 | 87,5 % | Hard-Culling |

**Folgerung:** Der Deluge spart schon ab etwa 40 % Last an der Qualität und ab etwa 60 % an Stimmen. Jede Einsparung wirkt darum doppelt: Sie bringt mehr Stimmen und bessere Qualität. Ob die Schwellen zu vorsichtig sind, zeigt erst die Messversion auf dem Gerät. Sie zeigt die Direness live an. Eine Änderung der Schwellen wäre nicht bitgleich und bräuchte Tests auf dem Gerät gegen Aussetzer.

**Lauf 1, vom Gegenprüfer gefunden, in Lauf 2 behoben:**
1. Im letzten Achtel jedes Takts schweigen die Synths. Im Dauerzustand liegt die Last bei **etwa 89 %**, nicht 83 %.
2. **Culling auf dem Gerät:** Der Soft-Cull beginnt, wenn ein Fenster mehr als 62,5 % seiner Zeit braucht, der Hard-Cull ab 87,5 %. Bei rund 89 % würde ein echter Deluge mit diesem Song **dauernd Stimmen abschalten** und `cpuDireness` hochsetzen. Im Emulator lief das nicht, weil die Laufzeitmessung 0 lieferte. Der Korrekturlauf bildet es nach.
3. Stimmen pro Spur werden ergänzt.
4. Den Reverb-Vergleich über dieselben Takte wiederholen.

Nach den Zählungen des Gegenprüfers spielen alle 16 Sounds, 6 Pads zu 90 % der Zeit mit 4 Stimmen, und alle 8 Kit-Spuren sind aktiv.

## 7. Priorisierung (nach dem Volllast-Test)

Grundlage: Anteil im vollen Song mal geschätzte Einsparung aus den Benchmarks. Alle Punkte sind bitgleich geplant.

| # | Ziel | Anteil im Song | erwartete Einsparung am Gesamt | Grundlage |
|---|---|---|---|---|
| 1 | **Filter** (LP/HP-Ladder, SVF): Zustand in Registern, Verzweigungen aus der Schleife, `smmlar`-Ketten | 35 % | **etwa 6–7 %** der Gesamtlast | 3.1: LP 15–20 %, HP etwa 20 % |
| 2 | **Oszillatoren:** NEON-Gewichte in `renderWave`/`renderPulseWave`, Dreieck und Crude in NEON | 26 % (davon `renderOsc` 18,5 %) | etwa 3–4 % | 3.2: −21 % `renderWave` |
| 3 | **`processReverbSendAndVolume`** (neu gefunden, noch nicht gebenchmarkt) | 5,9 % | offen, vermutlich viel (skalare Schleife pro Sample) | Song-Profil |
| 4 | **Aufwand pro Fenster und Stimme** (`Sound::render`, Patcher, Hüllkurven) | 9,3 % + 126 000 fix | offen | Song-Profil: 10-Sample-Fenster kosten etwa 200 000 |
| 5 | Wavetable-Schleife | 5,9 % | offen | noch nicht gebenchmarkt |
| 6 | Mod-FX (Phaser, Chorus), Kompressor, EQ | zusammen etwa 6 % | etwa 1–2 % | 3.3 |
| 7 | Analog-Delay-Impulsantwort NEON | im Song nicht aktiv | etwa 3 % pro Analog-Delay | 3.3 |
| 8 | Sample-Lesen (Sinc/linear) | 2,2 % in diesem Song | klein hier; gross bei sample-lastigen Songs | 3.4 |
| 9 | Drone NEON | 2,1 % | klein | 3.5 |

**Schluss:**
- Filter und Oszillatoren machen zusammen über 60 % der Last aus und sind der klare erste Schritt.
- Die Plätze 1–2 bringen zusammen etwa 10 % der Gesamtlast. Das senkt den Bedarf dieses Songs von etwa 89 % auf etwa 80 %, also weniger Culling und seltener reduzierte Qualität.
- Für die Plätze 3–5 braucht es zuerst Benchmarks.

**Getrennt, mit Messung auf dem Gerät:**
- L2-Cache
- MIDI/Clock-Fix in `routineForSD`
- Kompressor-Korrektur (`hpfR`)

**Vorgehen je Optimierung:**
1. Ein Agent setzt um, mit bitgleichem Nachweis nach Abschnitt 1 und gezählter Einsparung.
2. Ein Gegenprüfer kontrolliert.
3. Zum Schluss der ganze Song vorher/nachher: gleicher Ausgang (`measured.wav`), weniger Befehle.

## 7a. Umsetzung: Filter und Oszillatoren (bitgleich, gegengeprüft)

Rohdaten: `raw/perf-filters-oscillators.json`. Patches: `perf/`. Branches `perf-filters` (`1de85ade`) und `perf-oscillators` (`a63fc069`), kombiniert auf `mastertune-v12-perf`.

| | Benchmark (Befehle pro Block) | ganzer Song, Bedarf pro 128 Samples |
|---|---|---|
| Filter | LP24 mono 10 830 → 7 414; HP 7 748 → 3 427; SVF 7 869 → 5 717; LP24 stereo 17 991 → 12 162 | 1 370 014 → 1 173 302 (**−14 %**); LP `doFilter` 403k → 271k, HP 121k → 57k |
| Oszillatoren | Säge 1 578 → 1 112; Dreieck 1 662 → 606; Analog-Square-PW 2 569 → 1 416 | 1 370 014 → 1 292 117 (**−6 %**); `renderOsc` 280k → 202k |

**Filter-Änderungen:**
- Zustand, Koeffizienten und `jcong` lokal halten.
- Feste Verzweigungen einmal pro Block entscheiden.
- Bei Morph 0 wegfallende Terme weglassen.
- `hpfLastWorkingValue` nur am Blockende schreiben.
- Rückkopplung über `smmlar`/`smmla`.
- Stereo in einem Durchgang pro Kanal. Das Rauschen springt dabei per exaktem LCG-Doppelschritt, gesichert mit `static_assert`.
- NEON-Addition beim parallelen Routing.

**Oszillator-Änderungen:**
- NEON-Interpolationsgewichte aus dem Phasenvektor, Tabellenlesen mit `vld2.16`, `applyAmplitude` ausserhalb der Schleife.
- Crude Saw/Square und Dreieck mit 4 Lanes.
- Sync- und PW-Divisionen über `vdiv.f64`: bei 32 Bit exakt, bei 64 Bit mit Korrekturschritt.
- `getTableNumber` über `clz`, für alle 2^32 Eingaben geprüft.

**Nachweis:**
- Benchmarks ARM gegen ARM mit allen festen und zufälligen Fällen identisch: Filter 2 048 × 64 Blöcke, Oszillatoren 30 000 Fälle, dazu je gut 6 000 bzw. 24 000 eigene Fälle der Gegenprüfer. Absichtlich eingebaute Fehler werden erkannt.
- Im Song sind alle 650 108 `renderOsc`-Aufrufe und alle geprüften Filter-Aufrufe identisch.
- Keine neuen Warnungen, keine gefährlichen NEON-Ausrichtungshinweise.

**Offene Befunde der Gegenprüfer:**
- **Der Song-Ausgang hängt vom Speicher-Layout ab.** Schon unveränderter v12-Code mit anderem Versionsnamen ergibt eine andere `measured.wav`. Der Vergleich der ganzen WAV taugt darum nicht als Beweis, ein layout-gleicher Kontroll-Build schon. Die Ursache wird untersucht (Workflow `layout-dependence`): nicht initialisierter Speicher oder eine Reihenfolge nach Adressen.
- **Filtercode +17,8 KB** (Templates inline). Der Stack von `doFilterStereo` wächst von 104 auf 528 B, der interne Heap wird entsprechend kleiner.
  - Im Emulator unsichtbar, auf dem Gerät möglicherweise mehr I-Cache-Fehlzugriffe (L1 32 KB).
  - Möglich: seltene Pfade mit `[[gnu::cold]]`/`noinline` markieren. Erst nach der Messung auf dem Gerät entscheiden.
- **Zwei Hänger, schon in 1.2.1:** Analog-Square mit PW bei `phaseIncrement` 1 und Sync bei `resetterPhaseIncrement` 1 (`samplesIncludingNextCrossoverSample` läuft auf 0 über). Praktisch kaum erreichbar, weil so tiefe Frequenzen nötig sind. Optional die Inkremente nach unten begrenzen.
- Der Test-Song deckt Dreieck unter 711 Hz, Analog-Square mit PW, Osc-Sync und Ringmod nicht ab. Diese Pfade sind nur per Benchmark bewiesen.

## 7b. Leistungsversion v12-perf und nächste Runde

- **v12-perf** (`perf/`, SHA `d08b09bc…`, Branch `mastertune-v12-perf` = v12 + Filter + Oszillatoren + CPU-Monitor):
  - Bedarf im Volllast-Test 1 095 537 statt 1 370 014 (−20 %).
  - Wie auf dem Gerät: 28,5 statt 20,7 Stimmen im Mittel (+38 %), etwa gleich viele Abschaltungen (14/s), Direness dauernd 14.
  - Zwei komplette Neubauten sind identisch. Die Patch-Serie ist geprüft.
- **Referenz für die nächste Runde:** `/home/user/work/baseline-v12-perf`, Song-Ergebnis in `song/`, `measured.wav` SHA `c825dab9…`.
- **Nächste Runde, Spur-Effekte** (Workflow `perf-trackfx`, läuft):
  - `processReverbSendAndVolume` 59 412
  - `processFX` 38 643
  - Kompressor 30 725
  - SRR/Bitcrush 5 397
  - Nachweis per Benchmark und Vergleich jedes Aufrufs im Song.
- **Danach, Stimmen-Pfad:** `Sound::render` 110 543, Aufwand pro Block und Stimme, `renderBasicSource` 28 168.
  - Das geht nur mit Song-Nachweis und wartet darum auf das Ergebnis der Layout-Untersuchung.
- **Später:** Wavetable-Schleife 88 073, FM 36 614.

## 7c. Fehler gefunden: LFOs starten mit Zufallsphase (1.2.1), in v13 behoben

Rohdaten: `raw/layout-dependence.json`.
- **Ursache:** `LFO` (`modulation/lfo.h`) lässt `phase` und `holdValue` uninitialisiert. `Sound::Sound()` setzt die drei Zeitstempel `timeStartedSkippingRendering{ModFX,LFO,Arp}` nicht.
- **Folge:** Beim ersten Ton schalten LFO, Mod-FX-LFO und Arp um «Timer minus Altwert» weiter. Freilaufende LFOs und Mod-FX starten also nach jedem Laden an einer zufälligen Stelle. Ein Random-Walk-LFO kann mit einem Versatz bis etwa zum Zehnfachen seines Bereichs beginnen, der nur langsam abklingt.
- Upstream (main) hat dieselben Stellen.
- **Korrektur in v13:**
  - `phase = 0`, `holdValue = 0`
  - Zeitstempel im Konstruktor wie in `startSkippingRendering()`
  - `whichNoteCurrentlyOnPostArp = 0`
- **Nachweis im Emulator:** derselbe Song mit zwei verschiedenen RAM-Füllungen vor dem Start (`--fill 0xA5A5A5A5` / `0x5A5A5A5A`).
  - v12-perf: 352 580 von 352 896 Samples verschieden, −16,7 dB, bis 3 749 LSB.
  - v13: 30 Samples um 1 LSB (−113 dB). Der Rest kommt aus kleinen, nie beschriebenen Heap-Blöcken beim Laden (FFT-Konfiguration, Wavetable-Bänder, Patch-Kabel) und ist unhörbar.
- **Weiterer uninitialisierter Wert, gefunden bei der Spur-FX-Prüfung:** `modFXLFOWaveType` bei GRAIN in `processFX`. Wird in v13 mitkorrigiert.
- **Vergleichsrezept für künftige Patches auf v12-Basis:** `EMU_OPTS="--init-sounds --seed 1"` (siehe `tests/song/run.sh`). Ab v13 ist `--init-sounds` nicht mehr nötig.

## 7d. Spur-Effekte (bitgleich, gegengeprüft)

Rohdaten: `raw/perf-trackfx.json`. Patch `perf/0004`.

| Änderung | vorher | nachher |
|---|---|---|
| `processReverbSendAndVolume` mit NEON | 59 412 | 13 977 |
| Phaser/Chorus/Flanger je eigene Schleife | 38 643 | 24 040 |
| EQ spezialisiert | 7 210 | 4 021 (Bench) |
| SRR lokal, Bitcrush NEON | 5 397 | 5 014 |

- **Song:** Bedarf 1 095 537 → 1 035 101 (−5,5 %). Wie auf dem Gerät: 28,5 → 31,9 Stimmen.
- **Nachweis:**
  - 22 Prüfsummen mit 4 × 4 000 Zufallsfällen
  - alle 165 312 Aufrufe im Song identisch (mit `--init-sounds --seed 1`)
  - der Gegenprüfer mit eigenen Grenzfällen und einer absichtlich eingebauten Mutation
- **Kompressor nicht geändert:** Die Gleitkomma-Rechnung wählte je nach Kontext andere Befehle, das Ergebnis wäre nicht bitgleich gewesen.

## 7e. Speichern während des Abspielens (v13)

Rohdaten: `raw/save-speed.json`. Commit `c6201e47` (auf v13).
- **Problem in v12:** Die Knacks-Korrektur aus v11 bediente das Audio beim Speichern bei jedem geschriebenen Zeichen. Das war sauber, aber langsam: `routine()` lief etwa alle 5 Samples und renderte Fenster von rund 24 Samples.
- **Korrektur:** `routineWhileAccessingFile()` bedient Audio und UI erst, wenn mindestens 8 Samples fällig sind. Dann rendert `routine()` mindestens 64 Samples voraus (`minNumSamplesToRender`, nur während des Dateizugriffs).
- **Gemessen im Emulator,** Speichern während des Abspielens:

| Fall | vorher | nachher | grösste Lücke nachher |
|---|---|---|---|
| 3 Synths (etwa 50 % CPU), ab Takt 2 | 344 ms | 21,8 ms | 71 Samples |
| 5 Synths (etwa 70 % CPU), ab Takt 2 | 716 ms | 65,7 ms | 97 Samples, kein Underrun |
| mitten im Takt, 3 / 5 Synths | 80 / 180 ms | 10,3 / 16,0 ms | 56 / 66 Samples |

- Das geschriebene XML ist identisch, normales Abspielen bleibt bitgleich. Die Wartezeit der SD-Karte ist im Emulator nicht modelliert.

## 7f. MIDI- und Gate-Timing (v13)

Rohdaten: `raw/midi-timing.json`. Commits `c89b7b35` und `a8e8af32` (auf v13). Messung: `tests/song`, `MIDI=1 ./run.sh`.
- **Fehler 1 (neu sichtbar durch 7e):** `scheduleMidiGateOutISR()` rechnete die Wartezeit modulo 128. Lag das Ereignis spät im vorausgerenderten Fenster, ging MIDI (Clock, Noten) oder Gate einen ganzen Puffer, also 2,9 ms, zu früh hinaus. Beim Speichern betraf das 25 von 97 Timer-Läufen. Neu: `s = 128 + t − due − (movement & 127)`, bei Underrun +128, für schon gespielte Positionen 1, begrenzt auf 5605 Samples (16-Bit-TGRA).
- **Fehler 2 (alt, schon in 1.2.1):** `TCNT_2` wurde vor dem Start des Timers nie auf 0 gesetzt. Nach dem Compare-Match zählt er weiter, bis die ISR ihn stoppt, bei gesperrten Interrupts bis zu 451 Counts. Der nächste Lauf kam darum bis zu 38 Samples zu früh, oder nach einem 16-Bit-Überlauf rund 127 ms zu spät. Neu wird `TCNT` vor jedem Start auf 0 gesetzt.
- **Ergebnis:** Timer wie geplant gegen den Zeitpunkt, an dem die DMA das Fenster-Sample erreicht: beim Abspielen 192 Läufe, beim Speichern 97, alle zwischen −1,4 und +0,2 Samples.
- **Offen, beide alt:**
  - `Song::renderAudio()` sperrt die Interrupts pro Output. Der Timer-Interrupt läuft dadurch bis 40 Samples (0,9 ms) später.
  - Noten gehen etwa 2,3–2,6 ms vor dem Ton hinaus, weil `doMIDIClockOutTick()` den Puffer sofort sendet, wenn eine Clock auf denselben Tick fällt. Für externe Geräte mit eigener Latenz ist das eher günstig; eine Änderung wäre mit dem Nutzer abzusprechen.
- **Mess-Modell** in `song_emu.py`: Timer (MTU2 Kanal 2) samt Zähler, MIDI-UART mit DMA und 16-Byte-FIFO bei 31 250 Baud, Interrupts mit Sperrzeiten, Wiedergabe in Echtzeit wie der Task-Manager.

## 7g. Delay ohne Tonhöhensprung (v14)

Rohdaten: `raw/delay-v14.json`. Commits `cd7f09ef`, `9ec940a2` (Branch `delay-v14`).
- **Ursache des Verbiegens:** Bei einer Zeitänderung dreht der Puffer mit einer anderen Rate. Was schon drin liegt, spielt schneller oder langsamer ab, die Tonhöhe verschiebt sich um das Ratenverhältnis (25 % kürzer: +386 Cent), und das Feedback trägt es weiter.
- **Fade (neu, Standard):** Die neue Zeit läuft in einem frischen Puffer, der Eingang blendet in 23 ms hinüber, der alte Puffer spielt seine Echos mit Originalzeit und -tonhöhe aus. Tonhöhe ≤ 0,008 Cent, Artefakte ≤ −85 dB. Tape entspricht dem bisherigen Verhalten.
- **Mitbehoben:**
  - Klick beim ersten Echo nach einer Pause
  - hart abgeschnittenes Echo-Ende
  - Aliasing über 2 s (Puffer bis 4 s)
  - Unter 3 % Feedback warf der Delay seinen Puffer bei jeder Runde weg (1.2.1).
- **Ping-Pong** funktioniert, wirkt aber nur bei Stereo-Ausgabe (Kopfhörer oder R-Ausgang), über den Lautsprecher ist der Deluge mono.
- CPU: +0,8 % im Ruhezustand, während eines Wechsels +1,1 % pro Delay.

## 7h. Flackern der Pads bei tiefer Helligkeit (v13)

Rohdaten: `raw/pad-dim.json`. Commits `29a22d25`, `9b861a5c` (auf v13). Test: `tests/pads/run.sh`.
- **Ursache:** Der PIC dimmt über die Zeit: Jeder Scan-Schritt ist die «refresh time» an und das «dimmer interval» dunkel. 1.2.1 hält die Summe bis 40 % bei 23. Darunter bleibt die refresh time bei 8, und das Dunkle wächst pro Stufe um 1,2. Der Scan wird langsamer: bei 4 % 5,2-mal, bei 0 % 6,8-mal. Refresh-Werte unter 10 geben zudem falsche Farben (upstream Discussion #1869). Upstream gibt es keinen Fix.
- **Lösung (Community Features → Flicker-free dimming, Standard On):** Der PIC dimmt nur bis 43,5 % (refresh 10, Periode 23), den Rest skaliert `PIC::send()` bei allen Pad-, Sidebar- und Goldknopf-Werten. Licht je Stufe wie 1.2.1 (±1 %). Off sendet Byte für Byte dasselbe wie 1.2.1.
- **Kompromiss:** Tasten-LEDs, 7-Segment und PIC-eigene Blinkfarben (schneller Cursor, Menü-Shortcuts) dimmen nur bis 43,5 %.
- **Am Gerät zu prüfen:** das Modell refresh/(refresh + dimmer), die lineare PWM und ob die Werte 1–3 ruhig leuchten.

## 7i. Übertakten

- Der Deluge läuft mit 13,33 MHz × 30 (PLL) = **400 MHz**, der Nennfrequenz des RZ/A1L (`peripheral_init_basic.c`: `FRQCR = 0x1035`). Bus 133 MHz, Peripherie 66,7 / 33,3 MHz.
- Per Software geht es nicht höher: Der PLL-Faktor ist fest, `FRQCR` wählt nur Teiler.
- Nur ein anderer Quarz würde alles beschleunigen, ausserhalb der Spezifikation und mit Folgen für SDRAM-Timing, MIDI-Baudrate, SD, Timer und Wärme. Gewinn höchstens etwa 10 %.
- Mehr bringt Software: die Optimierungen (−25 % Bedarf von v12 zu v13) und der ungenutzte L2-Cache (128 KB, upstream eingeschaltet).

## 7j. L2-Cache: Testversionen (zu v14)

Ordner `l2test/`, Tests `tests/l2`. Commits auf v14: `d29b4fd5` und `198e9822` (nur Code), `d0791052` (auch Daten). Eine erste Fassung auf v13 (`1ac6d827`, `67a80eb8`) ist nach der Gegenprüfung ersetzt.
- **Nur Code:** L2 nach dem L1 eingeschaltet, alle Wege für Daten gesperrt (`REG9_D_LOCKDOWN0`).
  - Vor jeder DMA-Übertragung (SD lesen und schreiben, OLED) werden L1 und L2 für den Puffer zurückgeschrieben und geleert, nach dem Lesen noch einmal.
  - Der Grund (Gegenprüfer): RAM ist ausführbar (`TTB_PARA_NORMAL_CACHE` ohne XN). Ein spekulativer Befehlsabruf kann so eine Zeile eines Puffers in den L2 holen, und Daten treffen sie trotz Sperre.
  - FatFS-Puffer auf eigenen Cache-Zeilen.
- **Auch Daten:** dazu Prefetch für Daten und Code. Die Daten werden am Ende des Starts freigegeben, mit gesperrten Interrupts, nachdem alle Wege zurückgeschrieben und geleert sind (`CLEAN_INV_WAY`, Sync).
  - Interrupts aus, weil eine Operation per Adresse während der Operation per Weg beim L2C-310 einen Fehler zurückgibt.
  - Zurückschreiben statt nur Leeren, weil Zeilen aus spekulativen Befehlsabrufen inzwischen geänderte Daten halten können.
- **Gegenprüfer, ältere Fehler** (in v14 behoben):
  - `v7_dma_inv_range()` verlor einen Schreibzugriff neben dem Puffer (Linux-Fix von 2014).
  - Die SysEx-Dateipuffer aus v7 teilten Cache-Zeilen mit dem Allokator.
- **DMA in v13 vollständig:**
  - SD-Karte und OLED brauchen die Pflege.
  - Audio (SSI) und UART (MIDI, PIC) laufen über die ungecachte Spiegeladresse.
  - USB läuft ohne DMA.
  - Die Cluster der Samples haben Rohans Polster (`dummy[32]` davor, 32 Bytes danach). Ihr DMA-Bereich teilt deshalb keine Cache-Zeile mit Feldern, die während des Ladens geschrieben werden.
- **Chainloader** (Firmware per USB-SysEx) ist in Release-Builds nicht enthalten (`ENABLE_SYSEX_LOAD` aus). Für Builds mit ihm: L1-Pflege (upstream `c0586341`), dazu L2 zurückschreiben, leeren und ausschalten.
- **Emulator:** Startfolge, Pflege vor dem OLED-DMA (25 von 25 Zeilen, dann Sync) und Pflege bei krummen Adressen geprüft. Der Volllast-Song ist bei beiden Versionen bitgleich zu v14.
- **Offen:** der Gewinn. Nur am Gerät messbar (CPU-Monitor, `MT_LOADTEST`).

## 7k. Reverb: Wabbeln und Verwaschenes (v14)

Rohdaten: `raw/reverb-v14.json`. Commit `7c1ffede`, Tests `tests/reverb/modulation_test.cpp`.
- **Ursache:** Die modulierten Verzögerungen (Mutable: Smear im ersten Diffusor und zwei Tank-Delays; Digital: die zwei Tank-Allpässe) lassen einen gehaltenen Ton im Hall schwanken:
  - Mutable um 16,4 dB und 5 Cent
  - Digital um 15,5 dB und 19 Cent
  - Bei 1.2.1-Tempo (16-mal langsamer, v10 hat das auf das Original beschleunigt) sind es 8,4 dB und 5,6 Cent.
  - Ohne Modulation 0,1 dB und 0,3 Cent.
- **Tiefe gegen Wabbeln** (gehaltener 220-Hz-Ton, 5–95 %):

| Tiefe | Mutable dB | Mutable Cent | Digital dB | Digital Cent |
|---|---|---|---|---|
| 0 | 0,1 | 0,3 | 0,1 | 0,3 |
| 0,1 | 0,7 | 0,3 | 1,7 | 0,4 |
| 0,2 | 1,4 | 0,5 | 3,5 | 0,8 |
| 0,4 | 6,1 | 1,1 | 8,0 | 2,7 |
| 1 | 16,4 | 5,0 | 15,5 | 19,4 |

- **Resonanzen** (Spitzen im Spektrum des Nachhalls über dem Median): Mutable 20,5 dB ohne Modulation gegen 17,3 dB mit voller, Digital 11,2 gegen 12,3 dB. Die Modulation glättet also nur beim Mutable-Modell etwas, und nur um 3 dB.
- **Nachhall ohne Modulation:** beim Mutable-Modell breitbandig 5 % länger, bei 4 kHz etwa 23 % länger (Damping 14). Beim Digital-Modell unverändert.
- **Umsetzung:** Menü Modulation 0–50 (Standard 0, 50 = v10–v13) und Pre-delay 0–100 ms (17,6 KB RAM).
  - Bei 50 ist der Volllast-Song mit Mutable bitgleich zu v13. Mit Digital weichen 176 von 705 792 Samples um 1 LSB ab (Rundung der Offsets).

## 7l. Ruhelast grosser Songs (zu v15)

**Anlass:** Am Gerät zeigte der CPU-Monitor bei einem grossen Projekt 87 %, obwohl nichts spielte.

**Messung im Emulator** (v15, Stillstand, nie gespielt):
- Song: die 8 Synths des Volllast-Songs achtmal (64 Synths) und das Kit viermal, Drone mit 4 Tönen an.
- Ergebnis: 8,6 % bei Fenstern von 128 Samples, 13,5 % bei Fenstern von 16–24 Samples. Letzteres ist der Normalfall im Stillstand, weil die Engine dann alle 11 bis 16 Samples rechnet.
- Pro Aufruf kosten die 64 stummen Synths zusammen rund 2 000 Befehle (`SoundInstrument::renderOutput`, etwa 31 je Synth), die 4 Kits 600 (`Kit::renderOutput`). Stumme Spuren werden also schon früh übersprungen.
- Den Rest tragen das Song-Reverb (Mutable, 27–41 %), der Drone (14–19 %), der Master-Kompressor (8–12 %) und die feste Arbeit pro Aufruf (`Song::renderAudio`, `doSomeOutputting`).

**Folgerungen:**
- Die Zahl der Spuren erklärt 87 % nicht. Mögliche Ursachen im echten Song:
  - Sounds, die nie still werden (Delay mit hohem Feedback, LFO auf der Lautstärke, Latch-Arp)
  - viele Drone-Töne
  - Audiospuren mit Eingangs-Monitoring
  - Cache-Fehlgriffe, die der Emulator nicht kennt
- Nächster Schritt: den Song des Nutzers (`SONGS/<Name>.XML`) im Emulator laden und nach Funktion aufschlüsseln.
- Mögliche Hebel, falls es an den Master-Effekten liegt:
  - Reverb und Master-Kompressor in Stille überspringen (Reverb-Eingang und -Fahne unter der Hörschwelle)
  - im Stillstand in grösseren Blöcken rechnen, was die feste Arbeit pro Aufruf auf mehr Samples verteilt

## 7m. Leerlauf und falsche Culls (v17)

**Ursachen**, gefunden mit dem Emulator (`tests/sdload`) und dem Profiler:
- **Aufgabenplanung:** Das Intervall der Playback-Routine ist `16 / 44100`, als ganze Zahl also 0. Die Audio-Routine läuft darum etwa alle 12 µs und rechnet je 4–8 Samples. Jeder Durchgang geht alle Spuren durch.
- **Stille Kits und Audiospuren** richteten ihre ganze Effektkette ein, bevor sie die Stille prüften: rund 550 Befehle pro Spur und Durchgang.
- **Falsche Culls beim Streamen:** Wartet der Deluge auf die Karte, rechnet er das Audio im Lade-Task. `setDireness` beurteilte die Last dann nach der mittleren Dauer dieses Tasks, samt Kartenzeit.
  - Mit einer langsamen Karte gab es 17–25 Culls, obwohl der DMA höchstens 9 Samples im Rückstand war.

**Behebung in v17** (Patches 0056–0060):
- Das Intervall ist jetzt 0,36 ms, wie gedacht.
- Direness und Culling richten sich nach der gemessenen Zeit der Audio-Routine selbst.
- **Mindestfenster:** Bei leichter Last (unter 50 %, Direness 0, kein Dateizugriff) rechnet sie erst ab 32 fälligen Samples.
- Stille Spuren prüfen die Stille zuerst. Das ist dieselbe Bedingung wie bisher, nur vor dem Einrichten, und bleibt bitgleich.
- Der CPU-Monitor zählt Durchgänge ohne Rechnen nicht als belegt.

**A/B im Emulator** (`tests/sdload`). Die Geräteschätzung rechnet mit 1 Befehl pro Takt bei 400 MHz plus 60 ns pro SDRAM-Zeile:

| Fall | Aufrufe/s | Samples/Render | Monitor | Gerät (Schätzung) | Culls/QL | Reserve |
|---|---|---|---|---|---|---|
| Leerlauf v16 | 7132 | 6,2 | 90,2 % | 182 % | 0/0 | 109 |
| Leerlauf ohne Mindestfenster | 3569 | 12,4 | 29,8 % | 64 % | 0/0 | 106 |
| Leerlauf v17 | 2943 (735 rechnend) | 60 | 11,7 % | 19 % | 0/0 | 79 |
| Streaming v16 | 5308 | 8,4 | 92,7 % | 98 % | 37/4 | 83 |
| Streaming ohne Mindestfenster | 3996 | 11,1 | 94,5 % | 98,5 % | 0/0 | 75 |
| Streaming v17 | 2592 (733 rechnend) | 60 | 38,6 % | 40 % | 0/0 | 42 |

- **Entscheid:** Das Mindestfenster bleibt.
  - Ohne es bliebe ein typisches Streaming-Projekt bei rund 95 % Last.
  - **Sein Preis:** Live-Noten schwanken um bis 1,4 ms (im Mittel gleich), und die Reserve ist kleiner. Es gab keinen Underrun.
- **Stille Spuren:** Pro stille Spur sinkt der Aufwand von 550 auf 210 Befehle. Im grossen Song sind es −42 % Befehle und −26 % SDRAM-Zeilen pro Durchgang.

## 7n. Erste Messung am Gerät (v16, «New Sitar Grii 10»)

Die Messung machte die lokale Session mit dem Profiler (`geraet/2026-09-27-v16-*` auf dem Branch `geraet-ergebnisse`). Der Song hat 13 Spuren: 7 Kits mit 1–36 Drums, 3 Synths, eine Audiospur mit Monitoring und 2 MIDI-Spuren, dazu Reverb Mutable.

- **Stillstand:**
  - 86 % Anzeige, die Audio-Routine läuft 87 % der Zeit.
  - Die stillen Kits und die Audiospur brauchen zusammen 53 %, die Synths je 0,4 %.
  - Das bestätigt 7m auf dem Gerät.
- **Spielen:**
  - Es klingen nur 16–18 Stimmen, trotzdem 96 % Last. QL steht ständig auf 12–14.
  - In 12 s wurden 173 Stimmen geschnitten. Die längste Lücke war 4,7 ms, der Puffer reicht für 2,9 ms.
  - Die Kits tragen die Last: 3L3Ctr0 15 % (36 Drums, Phaser), Hihat 11 % (Flanger), Guiro 11 % (Delay), CR-78 10 % (Flanger), KIT1 8,5 %. Der Reverb braucht 6 %, die drei Synths zusammen 10 %.
  - Geschnitten werden vor allem die gehaltenen Synth-Stimmen, darum sind Oboe und Sitar kaum zu hören.
- **Folgerungen:**
  - v17 hilft im Stillstand. Spuren mit Mod-FX und klingendem Delay überspringt es aber bewusst nicht.
  - Unter Volllast hilft v17 wenig. Die Kosten liegen in den Kits und ihren Effektketten.
  - Im Stillstand kostet Guiro 14,7 %, Rattle mit ähnlichem Delay nur 2,8 %: noch ungeklärt.
- **Nächste Schritte:**
  - den Song im Emulator nach Funktion aufschlüsseln, denn auf dem Gerät sieht der Profiler nicht in die Spuren
  - Mod-FX- und Delay-Fahnen stiller Spuren überspringen, sobald sie abgeklungen sind
  - die Effektketten der Kits beim Spielen verbilligen
  - die L2-Versionen mit demselben Song messen

## 8. Offen

- [x] Volllast-Test Lauf 1 eingetragen, Priorisierung angepasst.
- [x] Korrekturlauf eingetragen (Lauf 2).
- [ ] Messversion auf dem Gerät mit `MT_LOADTEST`: Emulator kalibrieren, Direness- und Culling-Schwellen prüfen.
- [ ] Benchmarks für `processReverbSendAndVolume`, den Aufwand pro Fenster in `Sound::render` und die Wavetable-Schleife.
- [ ] Wavetable-Oszillator, Grain, `hopEnd` und die Stereo-Unison-Pan-Schleife messen, falls der Volllast-Test sie als relevant zeigt.
- [ ] Den Song «New Sitar Grii 10» im Emulator: Wohin geht die Zeit in den Kits (7n)?
- [ ] Stille Spuren mit Mod-FX oder Delay-Fahne überspringen, sobald die Fahne abgeklungen ist.
- [ ] Die Schwelle des Mindestfensters (50 % Last) am Gerät prüfen.
- [ ] MIDI/Clock-Fix in `routineForSD()` (`9cd09fb7`): Übertragbarkeit am Code bestätigen.
- [x] MIDI-/Gate-Timer: 2,9 ms zu früh und Zählerrest behoben (7f).
- [x] Noten 2,5 ms vor dem Ton (7f): bleibt so (Entscheid des Nutzers).
- [x] L2-Cache: Nachbesserungen gesammelt und die DMA-Pfade von v13 geprüft (Abschnitt 5).
- [x] **Messversion** gebaut (`diag/`, SHA `8c94cf68…`). Der Test-Song für die SD-Karte ist `diag/loadtest-card.zip`.
  - CPU-Last pro Block (Mittel/Spitze), Stimmen, `cpuDireness`, Culling, SD-Latenz pro Cluster.
  - Anzeige auf dem OLED, per SysEx nur über USB, dazu eine Web-MIDI-Seite `tools/cpu_monitor.html` mit CSV-Export.
  - Ziele:
    - Emulator gegen Hardware kalibrieren, mit demselben Test-Song wie im Volllast-Test
    - echte Ausgangslage für L2-Cache und Optimierungen
