# deluge.bin im Vergleich zur Original-Firmware 1.3

## Ergebnis

`deluge.bin` ist ein eigener Build der Synthstrom Deluge Community Firmware mit einer einzigen funktionalen Erweiterung: einer einstellbaren Grundstimmung („Master Tune“, A4 in Hz). Alle übrigen Abweichungen im Binary sind Folgeeffekte dieser Erweiterung.

| | |
|---|---|
| Datei | `deluge.bin`, 1'737'116 Bytes, SHA-256 `786ae3b549b5754b733e11f37b5c40c13073bd3cbd8b11e330d7d9eb6d041309` |
| Versionsstring | `1.3.0-dev-50813bcd-dirty` (Kurzform `c1.3.0`) |
| Build-Pfad im Binary | `/home/g2thek/src/DelugeFirmware/…` |
| Basis | `SynthstromAudible/DelugeFirmware` `main@50813bcd` vom 2026-09-25 |
| `dirty` | Beim Build lagen lokale, nicht veröffentlichte Änderungen vor |

**Was „Original 1.3“ heisst:** Ein fertiges Release 1.3 gibt es upstream nicht. Es gibt nur die Tags `beta` (2026-09-24), `nightly` und `start_1_3_0`. Verglichen wird deshalb auf zwei Ebenen:

1. **Upstream-Unterschied zur offiziellen `beta`:** Die Basis `50813bcd` liegt 3 Commits nach `beta`. Es sind kleine Bugfixes in 3 Dateien (+40/−16 Zeilen):
   - `4fdafedb` don't open another menu (#4944)
   - `04c1bf3a` use underlying param to check highlights for patch cables (#4945)
   - `50813bcd` Bugfix/first last clip double (#4943)

   Nicht enthalten ist der neuere Upstream-Commit `47b1d92b` Fix/ param manager leaks (#4920).
2. **Lokale Änderungen gegenüber genau dieser Basis:** Das ist der Hauptteil dieses Berichts.

## Methode

Ein direkter Byte-Vergleich ist unbrauchbar. Die lokale Änderung vergrössert `.bss` um 64 Bytes, dadurch beginnt das ganze Programm 0x40 Bytes später (`0x2005c3c0` statt `0x2005c380`). Jede absolute Adresse im Image ändert sich.

Deshalb wurde das unveränderte Original als Referenz selbst gebaut:

- gleicher Commit `50813bcd`
- gleiche offizielle Toolchain (DBT v22, `arm-none-eabi-gcc` 14.2.1 xPack)
- Konfiguration `release`
- gleicher Pfad `/home/g2thek/src/DelugeFirmware`, damit `__FILE__`-Strings gleich lang sind
- Arbeitsbaum ebenfalls „dirty“, damit der Versionsstring identisch ist

Das Ergebnis ist `v1.3.0-dev-50813bcd-dirty` mit 1'733'332 Bytes.

Der Vergleich (`tools/fwdiff.py`) verankert beide Images über eindeutige 24-Byte-Fenster. Die Anker werden zu einer monotonen Kette (LIS) verbunden. Danach wird jedes Funktions- und Datensymbol der Referenz mit der Gegenstelle verglichen. Reine Relocation-Effekte werden dabei herausgerechnet: absolute Adressen, BL/B/CBZ-Ziele, ARM-BL und MOVW/MOVT.

Beleg, dass die Referenz passt: 6'029 von 7'514 Symbolen sind identisch (3'026) oder nur verschoben (3'003).

## Die lokale Änderung: Master Tune

**Bedienung**
- Neues Untermenü **Settings → Tuning** (vor „Defaults“) mit einem Eintrag **Master tune (Hz)**. Auf der 7-Segment-Anzeige heissen sie `TUNE` und `MTUN`.
- Wertebereich 415.3–466.2 Hz, also ±1 Halbton um 440 Hz, in Schritten von 0.1 Hz. Intern wird ein `int` in Zehntel-Hz gespeichert (4153–4662). Standard ist 440.0 Hz.

**Berechnung** (neue Funktion bei `0x20078504` im Fork)
- `Hz = v · 0.1`, `ratio = Hz / 440`
- `cents = 1200 · log2(ratio)` als `float`
- `log2Q24 = lround(log2(ratio) · 2^24)`
- `ratioQ30 = lround(ratio · 2^30)`
- Anschliessend werden die CV-Kanäle 0 und 1 neu berechnet, sofern ein globaler Zustand gesetzt ist (vermutlich ein geladener Song).

**Wirkung** (gefundene Lesezugriffe)

| Stelle | Effekt |
|---|---|
| `Voice::calculatePhaseIncrements` (2×) | Phaseninkremente der Synth-Stimmen × `ratioQ30` |
| `DxVoice::init`, `DxVoice::update` (ARM-Code) | DX7-Engine: `log2Q24` als Offset im Log-Frequenz-Bereich |
| `CVEngine::calculateVoltage` | CV-Ausgänge: + `cents` |
| `Sample::workOutMIDINote` | Grundton-Erkennung von Samples teilt durch Master-Tune-Hz statt fix 440 |
| `SampleBrowser::loadAllSamplesInFolder` (3×) | Tonbereiche beim Laden ganzer Sample-Ordner ebenfalls relativ zur Master-Tune-Frequenz |

Damit sind alle vier Stellen umgestellt, an denen das Original fest 440 Hz verwendet (`sample.cpp`, `sample_browser.cpp` 2×, `dx7note.cpp`). In der MIDI-Notenausgabe und in Audio-Clips wurden keine Zugriffe gefunden.

**Speicherung**
- Der Wert wird in den SPI-Flash-Einstellungen in den Bytes **198–199** abgelegt (int16, little endian). `FlashStorage::writeSettings` schreibt sie zusammen mit den Bytes 196/197 in einem 32-Bit-Store.
- Beim Start (`deluge_main`) wird der Wert gelesen und geprüft. Liegt er ausserhalb von 4153–4662 oder stammen die gespeicherten Einstellungen von einer älteren Firmware-Version, gilt 4400 (440.0 Hz).
- **Hinweis:** Upstream sind die Bytes 198–199 heute unbelegt. Belegt ein künftiges offizielles Release diese Bytes anders, können die Einstellungen beim Wechsel zwischen diesem Build und der offiziellen Firmware falsch interpretiert werden.

**Texte**
- Zwei neue l10n-Strings, „Tuning“ und „Master tune (Hz)“, wurden direkt nach `STRING_FOR_DEFAULTS` eingefügt (neue IDs 701/702).
- Alle späteren String-IDs verschieben sich dadurch um +2.
- Sonst gibt es keine neuen oder entfernten Texte.

**Neu gelinkte Bibliotheksfunktionen (newlib libm)**
- `log2` (544 B), `lround` (124 B) und `__log2_data` (2'192 B)
- `__log2_data` und `lround` stimmen byte-genau mit `libm.a` der Toolchain überein, `log2` bis auf Adressbezüge.

## Warum rund 1'100 Funktionen abweichen

Das Binary ist mit LTO und Section Anchors gebaut. Neue globale Variablen verschieben deshalb die Offsets vieler anderer Globals relativ zu ihrem Anker. Zusammen mit den um +2 verschobenen String-IDs ändern sich dadurch die Immediates in vielen Funktionen, ohne dass sich deren Logik ändert.

| Klasse (1'101 Funktionen) | Anzahl | Bedeutung |
|---|---:|---|
| `reloc` | 47 | nur Adressreste |
| `struct` | 197 | nur `[reg, #offset]` verschoben |
| `const` | 64 | nur Konstanten (v. a. String-IDs) |
| `struct+const` | 204 | beides |
| `logic` | 589 | Befehlsfolge anders (Codegen-Folgeeffekte; enthält die Tuning-Stellen oben) |

Dazu kommen 384 geänderte Datenobjekte (vor allem Vtables und Menütabellen). Die vollständige Liste steht in `changed_symbols.csv`.

**Grössenbilanz (+3'784 B)**

| Bereich | Zuwachs |
|---|---:|
| `.text` | +1'208 B (davon 668 B libm, **etwa 540 B neuer App-Code**) |
| `.rodata` | +2'448 B (davon 2'192 B `__log2_data`, 256 B Vtable/Menülisten) |
| `.data` | +16 B |
| `.sdram_data` / `.sdram_rodata` | +32 / +44 B (Texte und l10n-Einträge) |
| `.exceptions` | +12 B |
| `.bss` | +64 B |
| `.sdram_bss` | +128 B (zwei neue Menüobjekte) |

**Einschätzung:** Mit etwa 540 Bytes neuem App-Code ist das Tuning-Feature praktisch vollständig erklärt: Rechenfunktion, Menüklasse, Flash-Lesen/-Schreiben und die Eingriffe in Voice, DX7, CV und Sample-Erkennung. Für weitere versteckte Funktionen bleibt kaum Platz. Ganz ausschliessen lassen sich kleine Logikänderungen ohne Quellcode aber nicht.

## Reproduktion

```sh
git clone --filter=blob:none https://github.com/SynthstromAudible/DelugeFirmware /home/g2thek/src/DelugeFirmware
cd /home/g2thek/src/DelugeFirmware
git config core.abbrev 8            # Kurz-Hash mit 8 Zeichen wie im Original
git checkout 50813bcd62d806e0f49c602dcbbb68166e58916d
touch LOCAL_BUILD_MARKER            # Baum "dirty" -> identischer Versionsstring
./dbt build release                 # lädt Toolchain v22

# Arbeitsordner mit ref.bin/ref.elf (aus build/Release) und fork.bin (= deluge.bin)
T=/pfad/zu/diesem/repo/firmware-diff/tools
export WORK=$PWD/work TC=/home/g2thek/src/DelugeFirmware/toolchain/v22/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-
cd $WORK
python3 $T/fwdiff.py ref.bin fork.bin ref.elf $TC diff.json   # Symbolvergleich -> diff.json
python3 $T/classify.py                                         # Klassifikation -> classified.json
python3 $T/gaps.py                                             # grösste Einfügungen
python3 $T/callers.py 20172738                                 # Aufrufer von log2 im Fork
python3 $T/annot.py 20078504 200785e4                          # neue Tuning-Funktion, annotiert
```
