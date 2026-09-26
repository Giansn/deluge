# Messversion v12-diag: CPU-Monitor

| Datei | Version (Settings → Firmware version) | SHA-256 |
|---|---|---|
| `deluge-1.2.1-mastertune-v12-diag-d0d03dcc.bin` | `1.2.1-mastertune-v12-diag-d0d03dcc` | `8c94cf686fad75ba9735fb7b5c4608bec02dfb8409db15172506f546468fbc14` |

Das ist **v12 plus Messung, sonst nichts**. Klang, DSP und Culling sind unverändert. Die Messung zeigt, wie stark die CPU ausgelastet ist, und schickt die Werte an den Computer, wo man sie mitschreiben kann. Zwei saubere Builds (Objektdateien gelöscht, alle 356 neu übersetzt) ergaben dieselbe SHA-256, ohne Compiler-Warnungen. Ersetzt die erste Messversion `b1ba466f`: Deren Culling-Zähler zählte auch Aufrufe, die nichts änderten, und ihre SD-Zeit enthielt das Audio-Rendern während des Lesens. Quellcode: `0001-CPU-monitor-…patch` gegen v12 (`f89b478c`), Branch `mastertune-v12-diag`.

## Test-Song für den Abgleich mit dem Emulator

`loadtest-card.zip` enthält den Song aus dem Volllast-Test (`tests/song/`). Er hat 17 Spuren bei 120 BPM und spielt durchgehend alles gleichzeitig: 8 Synths mit Akkorden, Unison, Filtern und Effekten, FM, Wavetable, ein Kit, eine gestretchte Audiospur, Reverb, Sidechain und den Drone.

1. Den Inhalt der ZIP-Datei ins Hauptverzeichnis der SD-Karte kopieren. Es kommen nur `SONGS/MT_LOADTEST.XML` und der Ordner `SAMPLES/MT_LOADTEST/` dazu, vorhandene Dateien werden nicht überschrieben.
2. Die Messversion starten, den Song `MT_LOADTEST` laden, **Settings → CPU monitor → On**.
3. `tools/cpu_monitor.html` verbinden, **Play** drücken und etwa eine Minute laufen lassen.
4. **CSV exportieren** und hier hochladen. Gern auch einen zweiten Lauf mit einem eigenen, typischen Song.

Der Emulator erwartet für diesen Song:
- ohne CPU-Schutz etwa 118 % Bedarf im Mittel, 45 Stimmen
- mit CPU-Schutz wie auf dem Gerät etwa 60 %, rund 21 Stimmen, etwa 14 abgeschaltete Stimmen pro Sekunde, Direness dauerhaft 14

Der Vergleich zeigt, wie gut der Emulator die echte Hardware trifft. Er zählt Befehle, nicht Takte; Cache und Pipeline fehlen ihm.

## Einschalten

**Settings → CPU monitor → On** (7-Segment: `CPU`). Nach jedem Einschalten des Deluge ist der Monitor wieder aus. Ausgeschaltet kostet er pro Aufruf der Audio-Routine eine einzige Abfrage.

- **OLED:** Die unterste Zeile zeigt invertiert die letzte Sekunde, zweimal pro Sekunde neu, z. B. `C43/71% V24 D0 S2.4/9` (SD ohne Audio, siehe unten). Sie liegt über dem Bild und überdeckt dort, was darunter stünde. Die Bildschirme selbst bleiben unverändert.
- **7-Segment:** Alle 2 Sekunden erscheint kurz `C 43` (CPU-Durchschnitt in %), aber nur wenn gerade kein anderes Popup läuft.
- **USB:** Jede Sekunde geht eine SysEx-Nachricht an den Computer, nur auf USB-MIDI-Port 3. DIN-MIDI bekommt nie etwas, auch die Volca nicht. Ist der USB-Sendepuffer voll, wird die Nachricht ausgelassen, damit Noten und Clock Vorrang haben.

## Am Computer mitschreiben

1. Deluge per USB anschliessen und den Monitor einschalten.
2. `tools/cpu_monitor.html` in **Chrome oder Edge** öffnen (Datei ins Fenster ziehen genügt), dann **Verbinden** und MIDI mit SysEx erlauben.
3. Eingang «Alle Eingänge» lassen oder den Deluge-Port 3 wählen. Kacheln und Kurve (letzte 5 Minuten) laufen dann live mit.
4. **CSV exportieren** speichert alle empfangenen Sekunden. Das Trennzeichen ist das Semikolon, der Dezimalpunkt ein Punkt, eine Zeile entspricht einer Sekunde. Die SD-Spalten heissen `sd_card_*` (ohne Audio) und `sd_latency_incl_audio_*` (Wanduhr).

## Was die Zahlen bedeuten

| OLED | Seite / CSV | Bedeutung |
|---|---|---|
| `C43/…` | CPU Durchschnitt | Rechenzeit der Audio-Routine geteilt durch die Dauer des Audios, das sie erzeugt hat. Dauerhaft über ~90 % wird es eng. |
| `C…/71%` | CPU Spitze | Der teuerste einzelne Aufruf (1–2 Blöcke, ab 16 Samples) im Verhältnis zu seiner Audiodauer. Einzelne Spitzen über 100 % fängt der Ausgabepuffer (128 Samples = 2,9 ms) auf. |
| `V24` | Stimmen jetzt / max | Aktive Stimmen (Voices). |
| `D0` | Direness max, Anteil | 0–14. Über 0 liegt die Audio-Routine hinter dem Ausgang zurück. Dann spart der Deluge Qualität (einfachere Oszillator-Tabellen, lineare statt Sinc-Interpolation), und ab etwa 80 Samples Rückstand schaltet er Stimmen ab. Der Anteil gibt an, wie viel vom Audio mit Direness über 0 entstand. |
| – | Culling | Stimmen pro Sekunde, die der CPU-Schutz wirklich ausblendet oder abschaltet, jede nur einmal. Nicht gezählt werden Aufrufe, die ihre Stimme unverändert lassen, und Stimmen, die schon schnell ausklingen oder aus sind. Voice-Stealing wegen der Polyphonie zählt nicht dazu. |
| `S2.4/9` | SD-Karte Ø / max | Reine Lesezeit eines Sample-Clusters in ms, Durchschnitt und längster Zugriff; `S-` = nichts gelesen. Während der Deluge auf die Karte wartet, rechnet er die Audio-Routine weiter; diese Zeit ist hier abgezogen. Der Wert zeigt, wie schnell die Karte ist. Andere, kleine Aufgaben (Display, MIDI) sind noch enthalten. |
| – | SD-Latenz inkl. Audio | Dieselben Zugriffe vom Start bis zum Ende (Wanduhr), also einschliesslich des Audio-Renderns währenddessen. So lange wartet das Streaming auf einen Cluster. Der Wert steigt mit der CPU-Last: Bei 70 % Last erscheint ein Zugriff von 2 ms auf der Karte als etwa 6–7 ms. |
| – | Längste Audio-Lücke | Längster Abstand zwischen zwei Aufrufen der Audio-Routine. Ab ~2,9 ms drohen Aussetzer. |

Werden die Zahlen gross, wird die OLED-Zeile kürzer (zuerst ohne `%`, dann nur der längste SD-Zugriff, dann ohne SD). Die Seite zeigt immer alles. Ist die SD-Latenz viel grösser als die Kartenzeit, bremst die CPU das Streaming, nicht die Karte.

## Messung

- **Zeit:** Gemessen wird mit dem OS-Timer 0 des RZ/A1L (33,33 MHz), den der Task-Manager ohnehin frei laufen lässt. Er wird nur gelesen, kein anderer Timer wird angefasst. Ein Überlauf alle 129 s schadet nicht, da nur vorzeichenlose Differenzen gebildet werden. Interrupts während der Audio-Routine zählen mit, das ist die echte Belegung.
- **SD ohne Audio:** Der Collector führt eine laufende Summe der Renderzeit, die nie zurückgesetzt wird (32 Bit, nur Differenzen, daher überlaufsicher). Vor und nach jedem Cluster-Zugriff wird sie gelesen; der Zuwachs wird von der Wanduhrzeit abgezogen.
- **Aufwand bei eingeschaltetem Monitor:** zwei Timer-Lesungen pro Aufruf der Audio-Routine, eine Aufgabe alle 50 ms, zwei OLED-Übertragungen und eine SysEx-Nachricht (51 Bytes) pro Sekunde. Es wird kein Speicher reserviert.
- **Klang unverändert:** Die Audio-Routine von v12 steht unverändert in einer eigenen Funktion, die Messung legt sich nur aussen herum. Der Vergleich der Maschinencodes (`tests/cpu_stats/compare_elf.py`, v12 gegen diag) ergibt Folgendes:
  - Die Audio-Routine hat dieselben Befehle wie in v12, nur Datenadressen haben sich verschoben.
  - Alle DSP-Funktionen und DSP-Tabellen sind gleich oder unterscheiden sich nur in verschobenen Adressen.
  - Geändert sind nur die Mess-Stellen (`cullVoice`-Zähler, der nur mitzählt und nichts an der Auswahl ändert, `loadCluster`, OLED, Menü, Tasks, Texte) und ein paar kleine Funktionen ausserhalb des Audiopfads (Datei anlegen, Zahl in Text, Wavetable freigeben), die der Linker leicht anders optimiert hat.

## SysEx-Format

`F0 00 21 7B 01 10` + 44 Bytes + `F7` (Format 2). Der Befehl `0x10` war im Deluge frei. Jedes Feld besteht aus 7-Bit-Gruppen, das niederwertigste Byte kommt zuerst, und zu grosse Werte bleiben am Maximum stehen.

| Bytes | Feld | Einheit |
|---|---|---|
| 1 | Formatversion = 2 | |
| 2 | Laufnummer (zählt Sekunden, 0–16383) | |
| 2 | Fensterlänge | ms |
| 2 / 2 | CPU Durchschnitt / Spitze | 0,1 % |
| 2 / 2 | Stimmen jetzt / max | |
| 1 | Direness max | 0–14 |
| 2 | Anteil Direness > 0 | 0,1 % |
| 2 | Abgeschaltete Stimmen (Culling) | |
| 2 | SD-Cluster gelesen | |
| 4 / 4 | SD-Latenz inkl. Audio Ø / längster Zugriff (Wanduhr) | µs |
| 4 | Längste Audio-Lücke | µs |
| 4 | Erzeugte Samples | |
| 4 / 4 | SD-Karte ohne Audio Ø / längster Zugriff (ab Format 2) | µs |

Hat ein Programm früher die alte Entwickler-ID benutzt, beginnt die Nachricht mit `F0 7D 10`; die Seite versteht beides. Spätere Formate dürfen hinten Felder anhängen. Format 1 (36 Bytes, erste Messversion) versteht die Seite weiterhin, die Kartenzeit bleibt dann leer.

## Tests

`tests/cpu_stats/run.sh <Firmware-Baum>` prüft auf dem PC:

- **Collector:** simulierter Timer mit Überlauf; Durchschnitt, Spitze, Lücke, SD, Culling und Direness stimmen auf ±0,2 %. Die Kartenzeit ist die Wanduhr minus das Rendern während des Lesens, auch wenn die laufende Renderzeit dabei überläuft.
- **OLED-Zeile:** passt bei 200 000 Zufallswerten immer in 21 Zeichen.
- **SysEx:** 503 Nachrichten laufen mit dem Decoder aus der Seite fehlerfrei hin und zurück, auch mit der kurzen Kopfzeile und im alten Format 1. Fremde oder kaputte Nachrichten werden abgelehnt.
- Den Culling-Zähler in `cullVoice` prüft kein PC-Test (er hängt an den Stimmen); er zählt nur in den Zweigen, die eine Stimme wirklich verändern.

`compare_elf.py v12.elf diag.elf <toolchain-bin> "AudioEngine::routine()=AudioEngine::routineUnmeasured()"` macht den Codevergleich.
