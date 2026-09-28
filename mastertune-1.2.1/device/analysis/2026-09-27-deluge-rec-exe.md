# DelugeRec.exe mit dem geprüften Stand, 27.09.2026

**Ergebnis:** Die .exe ist mit dem geprüften Stand neu gebaut, auf Windows selbst getestet und im Release ersetzt. Der Link bleibt gleich: https://github.com/Giansn/deluge/releases/download/deluge-rec/DelugeRec.exe

- **Quelle:** `tools/deluge_rec.py`, `tools/deluge_rec.ico` und `tests/deluge_rec` unverändert vom Entwicklungs-Branch (`2234215`, «reviewed (7 fixes) …»), auf `geraet-ergebnisse` als `e0bc3a7`. Das Icon war schon gleich.
- **Tests hier:**
  - `python3 tests/deluge_rec/test_deluge_rec.py`: 25 Tests, alle ok (5,8 s, Python 3.11, ohne Audio und Display).
  - Dazu `--selftest 6` unter Xvfb (Python 3.12): ok, 4,4 s Aufnahme.
- **Workflow «DelugeRec für Windows», Lauf 2** (https://github.com/Giansn/deluge/actions/runs/36343037010): alle Schritte grün, 2 min 15 s.
  - **Build:** PyInstaller, eine Datei, ohne Konsole, Python 3.12.10.
  - **Selbsttest der .exe auf Windows:** PortAudio V19.7.0 geladen (der Runner hat keine Audiogeräte). Die Aufnahme `USB00001.WAV` hat 2 Kanäle, 24 Bit, 44'100 Hz und 193'599 Frames: ok. Selbsttest ok, Exit 0.
  - **Datei:** `DelugeRec.exe` mit 25'075'574 Bytes, SHA-256 `c1645dfd37ed4b60e738afd0b62c624c75a9ac9c88dba09d54db99bd7fbf9e3c`. Sie liegt im Release `deluge-rec` (ersetzt um 19:07 UTC) und als Artefakt `DelugeRec-windows` des Laufs.
- **Hinweis:** Das Tag `deluge-rec` zeigt noch auf den ersten Build (`6dff8ff`), die Datei im Release stammt aus `e0bc3a7`. Aus welchem Commit gebaut wurde, zeigt der Lauf. Vorschlag: der Workflow schreibt den Commit künftig in die Release-Notiz.
- **Nicht geprüft:** mit dem echten Deluge am PC.

## v3: Versionsnummern und Icon

- **Nummerierung:** `VERSION` in `deluge_rec.py` ist eine ganze Zahl wie bei der Firmware (v16, v17). Jede Änderung am Programm zählt eins hoch und bekommt eine Zeile unter «Versions». Rückwirkend: v1 erster Build (`6dff8ff`), v2 geprüfter Stand (`e0bc3a7`), v3 Monitor, Englisch, Versionsnummer, Icon (`b20307b`).
- **Wo sichtbar:** im Fenstertitel, beim Start auf dem Display («USB REC V3»), mit `--version`, im Selbsttest und in den Dateieigenschaften der .exe (Dateiversion 3.0.0.0).
- **Releases:**
  - Die .exe heisst immer `DelugeRec-vN.exe`, im Build, im Artefakt und in beiden Releases.
  - Jede Version bekommt ein eigenes Release `deluge-rec-vN`. `deluge-rec` (https://github.com/Giansn/deluge/releases/tag/deluge-rec) hat immer die neuste als einzige Datei. Der alte Direktlink auf `DelugeRec.exe` gilt nicht mehr, weil sich der Dateiname mit jeder Version ändert.
  - Eine veröffentlichte Version wird nie ersetzt. Ändern sich `deluge_rec.py` oder das Icon ohne neue Nummer, bricht der Build ab. Sonst baut und prüft er nur.
  - Das Tag `deluge-rec` bleibt auf dem ersten Build. Den genauen Stand zeigt das Tag `deluge-rec-vN`.
- **Icon:** das Deluge-Logo (25 Quadrate in sieben Streifen) in den Farben der Pegelanzeige, von unten grün, gelb, rot, dazu der rote Aufnahmepunkt oben rechts. `tools/deluge_rec_icon.py` zeichnet es.
- **Lauf 4** (https://github.com/Giansn/deluge/actions/runs/36346822155): alle Schritte grün, 1 min 24 s.
  - 32 Tests auf Windows ok.
  - Selbsttest der .exe: «version: v3», die Aufnahme mit 2 Kanälen, 24 Bit, 44'100 Hz und 194'040 Frames ok.
  - `DelugeRec.exe` mit 25'081'413 Bytes, SHA-256 `7fa4ecffbbb4f69b1e13bc4f52e43b5aa13f9c872db8f8be80fb4af7bf03a898`, im Release `deluge-rec-v3` (https://github.com/Giansn/deluge/releases/tag/deluge-rec-v3). Der bisherige Link liefert dieselbe Datei.
- **Lauf 5** (https://github.com/Giansn/deluge/actions/runs/36347216300): alle Schritte grün. Der Build heisst jetzt `DelugeRec-v3.exe`. v3 war schon veröffentlicht und unverändert, deshalb gab es kein neues Release. `deluge-rec` hat die veröffentlichte Datei von v3 bekommen (gleiche SHA-256) und die alte `DelugeRec.exe` verloren.

## v4: VOL, Boxen, Deluge-Proportionen, Monitor ohne Knacksen

- **Anlass:** Die Pads standen im roten Bereich, obwohl der Deluge ganz leise gestellt war. Beim Start knackste es manchmal im Kopfhörer.
- **Pegel:** Der VOLUME-Knopf des Deluge ist analog und sitzt nach dem Wandler. Das USB-Signal folgte ihm deshalb nie, wie auch beim Resampling (`usbAudioPushFrames` bekommt `outputBufferForResampling`).
  - **VOL:** ein neuer senkrechter Regler über THRESH, von 0 dB (bitgenau) bis −30 dB, mit Pfeil hoch/runter, Mausrad, Ziehen, Doppelklick = 0 dB. Er wirkt auf die Aufnahme, die Pads, ARM und den Monitor. Änderungen gleitet er über einen Block.
  - **Übersteuern:** Was der Deluge selbst übersteuert, kann VOL nicht retten. Das letzte Pad blinkt deshalb nach dem Eingangssignal.
- **Knacksen:** Der Monitor setzte ohne Blende ein. Er blendet jetzt über 10 ms ein und aus, auch über Blockgrenzen, und springt mit Überblendung vor.
  - Sein Ausgang öffnet und schliesst mit dem Eingang des Deluge. Vorher startete PortAudio bei jedem Neuverbinden unter dem offenen Ausgang neu.
- **Oberfläche:** eine Box um jede Taste, um VOL und um THRESH («OUT FOLDER» las sich wie ein Wort), das Fenster im Seitenverhältnis des Deluge (305 × 208 mm).
- **Prüfung:**
  - Zwei Prüfer, jeder Befund einmal gegengeprüft. Vier Fehler bestätigt und behoben: kein Ausblenden an Blockgrenzen, zu kurze Überblendung bei kleinen Blöcken, Sprung beim Start mit gespeichertem VOL, ein Test abhängig vom Blinktakt.
  - 39 Tests. Eine Stream-Simulation mit echten Threads, Taktabweichung bis 20 % und Schwankungen im Takt der Blöcke zeigt keinen Sprung grösser als der Sinus selbst. Die alte v3 sprang dort um 0,44.
- **Lauf 6** (https://github.com/Giansn/deluge/actions/runs/36349256737): alle Schritte grün. `DelugeRec-v4.exe` mit 25'087'902 Bytes, SHA-256 `a1c6174d868f2934606e7da6b59dcdb07553138c2d9e3eea930a89ee50cb2cb2`, im Release `deluge-rec-v4` und in `deluge-rec`.
- **Nicht geprüft:** mit dem echten Deluge am PC.

## v5: Dateinamen mit Song, Datum und Firmware

- **Name:** «Songname Datum Zeit Firmware.WAV», zum Beispiel `Rescue 3 2026-09-27 21-30-05 v17.WAV`. Nie überschrieben, sonst « (2)», nach 4 GB « part 2».
- **In der Datei:** eine RIFF-INFO-Liste mit Titel = Song, Datum, Programm (DelugeRec v5) und einem Kommentar mit der Uhrzeit, der vollen Firmware und VOL.
- **Woher Song und Firmware kommen:** Der Deluge muss sie melden. Dafür gibt es einen Firmware-Patch, siehe `2026-09-27-songinfo-auftrag.md`.
  - DelugeRec hört auf USB-MIDI-Port 3 nur zu (python-rtmidi). Port 1 für die DAW bleibt frei, gesendet wird nichts.
  - Ohne den Patch heisst die Datei nur nach Datum und Zeit.
- **Prüfung:** ein Prüfer über App und Patch. Fünf Fehler bestätigt und behoben:
  - Umlaute in CP437
  - Port 3 wurde nicht nachgeholt, wenn er belegt war
  - möglicher Hänger von rtmidi beim Schliessen
  - Kopf `F0 7D 12`
  - `--list` ohne MIDI-System
  - 46 Tests.
- **Lauf 7** (https://github.com/Giansn/deluge/actions/runs/36354450337): alle Schritte grün.
  - Selbsttest unter Windows: `midi: rtmidi 5.0.0, 0 inputs`, die Aufnahme hiess `2026-09-27 22-13-15.WAV`.
  - `DelugeRec-v5.exe` mit 25'395'835 Bytes, SHA-256 `0c267315499470f0f67bea0ac39f72c00e1bfa4ca65e2850ea7492965d47768d`.
- **Nicht geprüft:** mit dem echten Deluge. Für Songname und Firmware braucht es zuerst eine Firmware mit dem Patch.
