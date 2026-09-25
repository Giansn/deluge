# Deluge 1.2.1 mit Master Tune

Offizielle Community-Firmware **1.2.1** (Tag `release_1_2_1`, Commit `c23bc2fe`) mit einstellbarer Grundstimmung.
Der Quellcode der bisherigen Master-Tune-Version war nicht verfügbar. Die Funktion ist deshalb neu umgesetzt und korrigiert die Punkte, die in der Analyse aufgefallen sind.

| | |
|---|---|
| Datei | `deluge-1.2.1-mastertune-19514d07.bin` |
| SHA-256 | `9024a95b4b6f2ebd10bd5e5070de4919fd45790b40b0f807a8d194349d2fbcc6` |
| Anzeige unter Settings → Firmware version | `1.2.1-mastertune-19514d07` |
| Quellcode | `0001-Add-master-tune.patch` (gegen `release_1_2_1`) |

## Bedienung

**Settings → Tuning → Master tune (Hz)**, auf der 7-Segment-Anzeige `TUNE` → `MTUN`.

- **Bereich:** 415.3 bis 466.2 Hz, also ±1 Halbton um 440 Hz.
- **Schritte:** 0.1 Hz. Der Cursor steht zuerst auf der 1-Hz-Stelle, mit dem horizontalen Encoder wechselst du auf 0.1 Hz.
- **Standard:** 440.0 Hz. Bei diesem Wert verhält sich die Firmware exakt wie das originale 1.2.1. Zusätzliches MIDI geht nur hinaus, wenn seit dem Einschalten eine andere Stimmung aktiv war: Dann setzt die Firmware die Geräte einmal auf 0 Cent zurück.

## Was der Stimmung folgt

| Bereich | Verhalten |
|---|---|
| Synth-Spuren: Wellenformen, Wavetables, Samples, FM inkl. Modulatoren, DX7 | folgen der Stimmung exakt (Abweichung unter 0,002 Cent) |
| Gehaltene Noten | werden beim Ändern sofort umgestimmt: Synth, CV und MIDI |
| CV-Ausgänge | folgen der Stimmung und werden sofort neu ausgegeben |
| Externe MIDI-Geräte | erhalten „Channel Fine Tuning“ (RPN 1) auf jedem Kanal, den eine MIDI-Spur benutzt, bei MPE auf den Member-Kanälen. Gesendet wird vor der ersten Note nach jeder Änderung und nach jedem Play-Start |
| Kit-Spuren: Drum-Samples, Synth-Drums, MIDI-Drums | bleiben in ihrer Originaltonhöhe |
| Audio-Clips und Aufnahmen | bleiben in ihrer Originaltonhöhe |
| Automatische Grundton-Erkennung von Samples | rechnet wie im Original mit 440 Hz, dadurch kein Doppeleffekt |

## Speicherung

- **Speicherort:** Der Wert steht in `CommunityFeatures.XML` im Hauptverzeichnis der SD-Karte, Eintrag `masterTune`, in Zehntel-Hz, z. B. `4320` für 432.0 Hz. Er wird beim Verlassen des Settings-Menüs gespeichert.
- **Kompatibilität:** Die offizielle Firmware kennt den Eintrag nicht. Sie behält ihn trotzdem und schreibt ihn unverändert zurück.
- **Übernahme aus der bisherigen Version:** Die alte Version speicherte den Wert in den Flash-Bytes 198–199. Die neue Version übernimmt ihn beim ersten Start. Sobald er in der Datei steht, werden die Flash-Bytes beim nächsten Speichern wieder auf 0 gesetzt, wie bei der offiziellen Firmware. Konflikte mit künftigen Firmware-Versionen entfallen damit.

## Installation

1. Kopiere `deluge-1.2.1-mastertune-19514d07.bin` ins Hauptverzeichnis der SD-Karte. Lass dort keine andere `.bin`-Datei liegen.
2. Halte wie gewohnt beim Einschalten **SHIFT** gedrückt. Der Deluge installiert dann die Firmware.
3. Unter Settings → Firmware version muss danach `1.2.1-mastertune-19514d07` stehen.

## Grenzen

- **Nicht auf dem Gerät getestet.** Geprüft sind der Build, die Rechnung (siehe Test unten) und der erzeugte Maschinencode.
- **MIDI:** Das Gerät muss RPN 1 (Channel Fine Tuning) auswerten. Das tun viele Synths und DAWs, aber nicht alle. RPN 1 reicht von −100 bis +99,99 Cent. Nur an den beiden Extremwerten 415.3 Hz (−100,02 Cent) und 466.2 Hz (+100,13 Cent) bleibt eine Rest-Abweichung von höchstens 0,15 Cent.
- **Geräte, die erst später eingeschaltet werden,** bekommen die Stimmung beim nächsten Play-Start oder bei der nächsten Änderung.
- **Barock-Stimmung:** A = 415.0 Hz liegt knapp ausserhalb des Bereichs. Möglich sind 415.3 Hz, also genau ein Halbton unter 440.

## Unterschiede zur bisherigen Version

Bisherige Datei: `deluge-1.2.1-mastertune.bin`, SHA-256 `53d1264f…eda66eca1`, Version `c1.2.0`.

| | bisher | neu |
|---|---|---|
| Menü, Bereich, Schritte | Settings → Tuning, 415.3–466.2 Hz | gleich |
| Gehaltene Noten | Synth-Stimmen erst ab dem nächsten Anschlag | sofort |
| Externe MIDI-Geräte | nicht gestimmt | RPN 1 Channel Fine Tuning |
| Drum-Samples in Kits | mitgestimmt | Originaltonhöhe |
| Samples mit automatischer Grundton-Erkennung | Stimmung doppelt angewendet, z. B. 32 Cent zu tief bei 432 Hz | korrekt |
| Speicherort | Flash-Bytes 198–199 | SD-Datei, alter Wert wird übernommen |
| Versionsanzeige | `c1.2.0` | `1.2.1-mastertune-<commit>` |

## Selbst bauen und testen

```sh
git clone https://github.com/SynthstromAudible/DelugeFirmware && cd DelugeFirmware
git checkout release_1_2_1
git am /pfad/zu/0001-Add-master-tune.patch
./dbt configure -DRELEASE_TYPE:STRING=mastertune
./dbt build release                      # Ergebnis: build/Release/deluge.bin

# Rechentest (Host-Compiler)
g++ -std=c++20 -O2 -Isrc/deluge /pfad/zu/tests/master_tune_math_test.cpp -o mt_test && ./mt_test
```
