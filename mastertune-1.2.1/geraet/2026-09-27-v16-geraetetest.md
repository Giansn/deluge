# Gerätetest v16, 27.09.2026

Lokale Session am Windows-11-PC, Deluge per USB. Firmware `deluge-1.2.1-mastertune-v16-c610417f.bin` (SHA-256 `7eed1a77…71897f9f`), Werkzeuge aus Commit `0377a67`. Die lokale Session hat am Deluge nur mitgehört (MIDI-Eingang), nichts gesendet und nichts auf die Karte geschrieben.

## Punkte aus GERAET.md

| # | Test | Ergebnis | Beobachtung |
|---|---|---|---|
| 1 | Version | offen | Laut Nutzer v16 aufgespielt; die Anzeige unter Settings → Firmware version ist nicht abgelesen. Die v16-Symbole ergeben stimmige Funktionsnamen. |
| 2 | USB audio bleibt an | nicht getestet | |
| 3 | CPU monitor | teilweise ok | On und Profile laufen, Profile mehrmals umgeschaltet ohne Absturz. Alerts nicht geprüft. |
| 4 | Drone-Spuren | nicht getestet | |
| 5 | Messung grosser Song | ok | «New Sitar Grii 10», 30 s Stillstand, 39 s Spielen mit Clips starten und stoppen. Zahlen unten. |
| 6 | L2-Versionen | nicht getestet | |

## Messung «New Sitar Grii 10» (`2026-09-27-v16-grosser-song.jsonl`, CPU monitor auf Profile)

| | Stillstand, 30 s | Spielen, 39 s |
|---|---|---|
| CPU-Anzeige Mittel / Spitze | 86,3 % / 62 % | 95,8 % / 213,6 % |
| Stimmen höchstens | 0 | 16 |
| Qualität gesenkt (QL) | nie | alle 39 s, Stufe 12–14 |
| Abgeschnittene Stimmen | 0 | 124, in 21 s |
| Längste Lücke der Audio-Routine | 0,4 ms | 4,2 ms; in 24 s über 2,9 ms |
| Audio-Routine / Scheduler (Profil) | 87,4 % / 10,7 % | 96,7 % / 2,1 % |

**Rechenzeit pro Spur** (auf dem Gerät gemessen, Anteil der Zeit):

| Spur | Stillstand | Spielen |
|---|---|---|
| K Guiro | 14,7 % | 10,5 % |
| K 3L3Ctr0 | 12,0 % | 13,2 % |
| K 014 CR-78 | 6,6 % | 7,3 % |
| K KIT1 | 5,7 % | 7,2 % |
| A AUDIO1 | 5,5 % | 2,8 % |
| K Rattle V2 | 3,1 % | 3,5 % |
| K Hihat | 2,9 % | 7,8 % |
| K Rattle | 2,8 % | 4,0 % |
| S 170 Sitar 2 / S Oboe / S Kbass | je 0,4 % | 10,4 / 7,6 / 3,3 % |
| M (2 MIDI-Spuren) | 0,4–0,5 % | 0,1 % |

**Funktionen:** `Song::renderAudio` (Zeit in den Spuren) 70 % im Stillstand, 85 % beim Spielen. `reverb::Mutable::process` 6,5 / 5,9 %. Im Stillstand dazu der Scheduler: `TaskManager::chooseBestTask` 5,7 %, `getSecondsFromStart` 4,5 %.

## Beobachtungen

- **Stillstand:** Die stillen Kits und die Audiospur belegen zusammen 53 % der Zeit, die Synths je 0,4 %. Das passt zum Befund aus dem Emulator (Audio-Routine fast pausenlos, stille Spuren richten jedes Mal ihre Effekte ein), also zu v17.
- **Die Rangfolge im Stillstand folgt nicht allein den Effekten auf Kit-Ebene:** Guiro (Delay, Feedback 14–15) kostet 14,7 %, Rattle mit ähnlichem Delay nur 2,8 %. KIT1 ohne Kit-Effekte kostet 5,7 %. Das wäre im Emulator mit einem ähnlichen Song zu prüfen.
- **Spielen:** Die Schnitte hängen an der Last, nicht an der Karte. 20 der 21 Sekunden mit Schnitten haben eine Lücke über 2,9 ms. In den 5 Sekunden mit Kartenzugriffen (bis 23 ms pro Zugriff) gab es keinen Schnitt. Geschnitten wird, wenn die Kits 3L3Ctr0, Hihat, Guiro und 014 CR-78 zusammen spielen.
- **Hörbar (Nutzer):** Oboe und Sitar sind fast nicht zu hören, solange die Kits spielen. Es sind offenbar ihre Stimmen, die abgeschnitten werden.
- Erster Lauf mit CPU monitor auf On (`2026-09-27-v16-cpumonitor.jsonl`, nach 75 s gestoppt, die Datei enthält davon die ersten rund 60 s): beim Starten von Clips Schnitte bis 21 pro Sekunde zusammen mit Kartenzugriffen (4–7 ms) und Lücken bis 4,6 ms.

## Der Song (aus dem Backup vom 25.09., gespeichert mit c1.2.1)

140 BPM, Song-Reverb Mutable (Room 16), 13 Spuren:

| Spur | Art | Einstellungen, die ohne Noten weiterlaufen |
|---|---|---|
| KIT1 | Kit, 1 Drum (Resample-WAV) | Delay auf dem Drum |
| AUDIO1 | Audiospur | Eingangs-Monitoring an (rechts), Reverb-Send 9 |
| Oboe, 170 Sitar 2, Kbass | Synths, Sample + Rechteck | – |
| Rattle V2 | Kit, 19 Drums | – |
| Rattle | Kit, 14 Drums | Delay auf dem Kit, Feedback 14 |
| Hihat | Kit, 14 Drums | Flanger auf dem Kit |
| Guiro | Kit, 8 Drums | Delay auf dem Kit, Feedback 15, Reverb-Send 4 |
| 3L3Ctr0 | Kit, 36 Drums | Phaser (Feedback 9) und HPF auf dem Kit |
| 014 CR-78 | Kit, 14 Drums | Flanger auf dem Kit |
| 2 × MIDI | | |

## Hinweise zu Werkzeugen und Anleitung

- **Windows-Portnamen:** `Deluge 0`, `MIDIIN2 (Deluge) 1`, `MIDIIN3 (Deluge) 2`. `open_input()` sucht eine 3 am Namensende und nimmt deshalb Port 1, von dem nichts kommt. Mit `-p "MIDIIN3 (Deluge) 2"` geht es. Vorschlag: auch `midiin3` erkennen.
- **`pip install mido python-rtmidi`** scheitert unter Windows mit Python 3.13 und 3.14. `python-rtmidi` 1.5.8 hat Windows-Pakete nur bis 3.12, sonst will pip kompilieren. Mit Python 3.12 läuft alles.

## Dateien

- `2026-09-27-v16-grosser-song.jsonl`: die Messung oben (70 s, Profile)
- `2026-09-27-v16-profile-check.jsonl`: 12 s Profile beim Spielen
- `2026-09-27-v16-cpumonitor.jsonl`: erster Lauf, CPU monitor auf On
