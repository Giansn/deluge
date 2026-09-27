# Deluge 1.2.1 mit Master Tune

Das ist die offizielle Community-Firmware **1.2.1** (Tag `release_1_2_1`, Commit `c23bc2fe`) mit einstellbarer Grundstimmung, in mehreren Stufen. v5 bringt zusätzlich den Arpeggiator aus 1.3, v6 eine zweite Bounce-Version, v7 den Zugriff auf die SD-Karte über USB, v8 den Deluge als USB-Audio-Eingang am Computer, v9 klügeres Sample-Streaming und einen RAM-Sparer für Kits, v10 ein besseres Reverb, v11 ein besseres Delay und kein Knacksen mehr beim Speichern, v12 einen Frequenz-Drone mit bis zu 16 Tönen, v13 mehr Leistung, einen Ping-Pong-Arp, flimmerfreies Dimmen der Pads, genaueres MIDI und einen überarbeiteten Drone, v14 ein Reverb ohne Wabbeln, ein Delay ohne Tonhöhensprung und einen Countdown beim Song-Wechsel, v15 einen lebendigen Drone (Life, FM, Pulse), einen CPU-Monitor in einer Zeile und drei Korrekturen aus der Community, v16 Drone-Spuren für Song- und Arranger-View und einen Profiler.

| Datei | Version (Settings → Firmware version) | Inhalt |
|---|---|---|
| `deluge-1.2.1-mastertune-49e71650.bin` | `1.2.1-mastertune-49e71650` (v2) | Master Tune |
| `deluge-1.2.1-mastertune-v3-09dcce01.bin` | `1.2.1-mastertune-v3-09dcce01` | v2 + Leistungspaket A + Sidechain-Fix |
| `deluge-1.2.1-mastertune-v4-6cb344e2.bin` | `1.2.1-mastertune-v4-6cb344e2` | v3 + Leistungspaket B |
| `deluge-1.2.1-mastertune-v5-5daddd9f.bin` | `1.2.1-mastertune-v5-5daddd9f` | v4 + Arpeggiator aus 1.3, Latch, Ratchet Bounce |
| `deluge-1.2.1-mastertune-v6-1e1af07a.bin` | `1.2.1-mastertune-v6-1e1af07a` | v5 + zweite Bounce-Version: feste Ratchet-Anzahl, Bounce ohne Leiserwerden |
| `deluge-1.2.1-mastertune-v7-ca0b5bd7.bin` | `1.2.1-mastertune-v7-ca0b5bd7` | v6 + SD-Karte über USB für DEx und deluge-editor |
| `deluge-1.2.1-mastertune-v8-76c5a9b8.bin` | `1.2.1-mastertune-v8-76c5a9b8` | v7 + USB-Audio: Ausgang des Deluge als Aufnahme-Eingang am Computer |
| `deluge-1.2.1-mastertune-v9-c0212731.bin` | `1.2.1-mastertune-v9-c0212731` | v8 + klügeres Sample-Streaming, Kit RAM saver |
| `deluge-1.2.1-mastertune-v10-7f9ad5c1.bin` | `1.2.1-mastertune-v10-7f9ad5c1` | v9 + Reverb: neues Modell Digital, Mutable und Freeverb repariert, HPF und LPF |
| `deluge-1.2.1-mastertune-v11-dc37f26a.bin` | `1.2.1-mastertune-v11-dc37f26a` | v10 + Delay: saubere Wiederholungen, kein Knacken bei Zeitänderungen, LPF und HPF im Feedback; kein Knacksen beim Speichern |
| `deluge-1.2.1-mastertune-v12-f89b478c.bin` | `1.2.1-mastertune-v12-f89b478c` | v11 + Frequenz-Drone: 16 Töne, binaural, monaural, isochron, Tempo-Sync, Sidechain, eigene Ansicht |
| `deluge-1.2.1-mastertune-v13-9b861a5c.bin` | `1.2.1-mastertune-v13-9b861a5c` | v12-perf + Drone-Feinschliff, Ping-Pong-Arp, flimmerfreies Dimmen, schnelleres Speichern, genaueres MIDI, CPU-Monitor in Worten |
| `deluge-1.2.1-mastertune-v14-c1d1c8bb.bin` | `1.2.1-mastertune-v14-c1d1c8bb` | v13 + Reverb ohne Wabbeln (Modulation, Pre-delay), Delay ohne Tonhöhensprung, Countdown beim Song-Wechsel, zwei Korrekturen für die Karte |
| `deluge-1.2.1-mastertune-v15-b5f5c900.bin` | `1.2.1-mastertune-v15-b5f5c900` | v14 + lebendiger Drone (Life, FM, Pulse), CPU-Monitor in einer Zeile, Section-Start per CC, USB-MIDI ohne Paketverlust, Clock-Ausgänge unter externer Clock |
| `deluge-1.2.1-mastertune-v16-c610417f.bin` | `1.2.1-mastertune-v16-c610417f` | v15 + Drone-Spuren (Drones als Kit-Spuren in Song- und Arranger-View, Hz-Spur pro Reihe), Profiler, USB audio bleibt nach dem Neustart an |

SHA-256: v2 `05b457a0…d2ce7e0cb`, v3 `7858013f…35d4d73`, v4 `cc9127a0…61128209`, v5 `65607a5d…c745251b`, v6 `83974fd2…617ef692`, v7 `48c55bae…f9581a21`, v8 `6cfda14b…99441298`, v9 `032898cf…7c673745`, v10 `1b4ac767…46e8a6ac62`, v11 `e4d1062e…10c9a0ad`, v12 `97288329…5e7360ac`, v13 `9ff41174…7c4c50e7`, v14 `cb17bbb3…8e23e6e5`, v15 `cdce07f3…da7ca652`, v16 `7eed1a77…71897f9f` (vollständig: `sha256sum *.bin`).
Nachgeprüft am 26.09.2026: Jede Version v2–v12 wurde aus ihrem Commit in einer eigenen Arbeitskopie komplett neu gebaut, mit 441–448 neu übersetzten Dateien. Jede SHA-256 stimmt mit der ausgelieferten Datei überein.
Quellcode: `patches/0001` bis `0055` gegen `release_1_2_1`. v2 = 0001, v3 = 0001–0003, v4 = 0001–0004, v5 = 0001–0005, v6 = 0001–0006, v7 = 0001–0007, v8 = 0001–0008, v9 = 0001–0009, v10 = 0001–0010, v11 = 0001–0011, v12 = 0001–0012, v13 = 0001–0028 (0013–0015 sind die Leistungsversion v12-perf, gleich wie `perf/0001`–`0003`), v14 = 0001–0035, v15 = 0001–0041, v16 = 0001–0055.

## v3 und v4: Unterschiede

| | v3 | v4 |
|---|---|---|
| Sinc-Interpolation mit NEON-Pufferverschiebung: jede umgestimmte, transponierte oder zeitgestreckte Sample-Stimme ohne Cache-Treffer ca. 40 % billiger | ja | ja |
| Menü: kürzere Wartezeiten der UI-Aufgaben, keine verschluckten Encoder-Rasten im Sound-Editor (bis 5 pro Abfrage, wie bisher mit SHIFT) | ja | ja |
| Sidechain-Fix: kein Knacken mehr beim Ducking, auch der Reverb-Rücklauf wird weich nachgeführt | ja | ja |
| Culling wie aktuelle Community-Firmware: seltener, weniger Stimmen, die Hälfte ausgeblendet statt abgeschnitten | nein | ja |
| Stille Kits und Audio-Spuren ohne zeitabhängige Effekte überspringen ihre Effektkette | nein | ja |
| Lautstärkestufe schreibt direkt in den Mix (ein Durchgang weniger), kein doppeltes Nullsetzen | nein | ja |
| Klang im Normalbetrieb | bitgleich zu v2, ausser dem Sidechain-Fix (knackfrei statt Sprung) | wie v3; unter Überlast andere, weichere Culling-Reaktion |

**Leistungspaket A (v3):**
- **NEON-Pufferverschiebung:** Die Verschiebung des 16er-Interpolationspuffers geschieht mit NEON-Befehlen statt Wert für Wert. Das war etwa die Hälfte der Sinc-Kosten.
- **Geprüft:** Auf echtem Cortex-A9-Maschinencode im Emulator ergibt sie für alle Verschiebeweiten dieselben Puffer (`tests/run_neon_shift_test.py`).
- **Verworfen:** Zwei zusätzliche Compiler-Flags (`-funswitch-loops -fsplit-loops`) änderten den Fliesskomma-Code in 81 Funktionen, darunter Audio. Damit war Bitgleichheit nicht beweisbar, deshalb sind sie nicht enthalten.

**Sidechain-Fix (v3, v4):**
- **Fehler:** Die Lautstärkerampe pro Puffer startete beim neuen Wert und lief um die ganze Änderung darüber hinaus. Bei einem Ducking von mehr als 6 dB innerhalb eines Puffers kippte die Verstärkung sogar ins Negative, also mit umgekehrter Polarität.
- **Jetzt:** Die Rampe läuft vom alten zum neuen Wert. Der Fehler steckt auch in der aktuellen Community-Firmware.

**Leistungspaket B (v4):**
- **Gewinn:** Er ist kleiner als bei A. Geschätzt spart es 0,2–0,6 % CPU pro stiller Kit- oder Audio-Spur und rund 0,05 % pro klingendem Sound durch den gesparten Durchgang.
- **Culling:** Der Hauptnutzen ist das neue Culling: Unter Last werden weniger Noten abgeschnitten, und dafür steigt das Knackrisiko bei echter Dauerüberlast leicht.
- **Übersprungene Effektkette:** Sie greift nur, wenn Mod-FX, Delay, Stutter, Sample-Rate-Reduktion, Sättigung und Kompressor aus sind, keine Aufnahme läuft und die Kette seit 4096 Samples exakt null ausgibt. Das Ergebnis ist dann wieder Stille.

## v5: Arpeggiator aus 1.3, dazu Latch und Ratchet Bounce

v5 enthält alles aus v4 und zusätzlich den kompletten Arpeggiator der Community-Firmware 1.3. Die Menüs bleiben im gewohnten 1.2.1-Stil (eine Liste, kein horizontales Menü). Neue Punkte stehen dort, wo sie thematisch hingehören.

| Funktion | Wo | Was sie tut |
|---|---|---|
| **Kit-Arpeggiator** | Kit mit gedrücktem Affect Entire → Menü → Kit arpeggiator | Spielt die Reihen eines Kits wie die Töne eines Akkords (Up, Down, Random, Walk, Pattern …). Pro Reihe abschaltbar mit «Include in kit arp». |
| **Arp für MIDI- und Gate-Reihen** | Reihe auswählen → Menü | Bisher gab es dort nur eine Warn-LED. Jetzt: eigener Arp und Randomizer, Shortcut-Spalte 11 wie bei MIDI-Spuren. |
| **Preset** mit neuem **Walk** | Arpeggiator → Preset | Jetzt auch in der Liste, nicht nur auf dem Pad. |
| **Latch** (neu, nicht in 1.3) | Arpeggiator → Latch | Der Arp spielt nach dem Loslassen weiter. Der nächste Anschlag nach dem Loslassen aller Tasten ersetzt die Noten, ohne dass der Arp aus dem Takt fällt. Ausschalten stoppt die gehaltenen Noten. |
| **Step Repeat** | Arpeggiator → Step repeat | Jeder Schritt wird 1–8 Mal wiederholt. |
| **Notenmodi** Walk 1–3, Pattern | Arpeggiator → Note mode | Walk: zufällig einen Schritt vor oder zurück. Pattern: zufällige, aber sich wiederholende Reihenfolge (neu würfeln durch erneutes Wählen). |
| **Chord Simulator** | Kit-Reihe → Arpeggiator | Eine Drum-Reihe spielt einen Akkord (5th, sus2, Moll, Dur, sus4, m7, 7, maj7). |
| **Ratchet Bounce** (neu, nicht in 1.3) | Arpeggiator → Ratchet bounce, −10 … +10 | Die Schläge eines Ratchets wie ein springender Ball: positive Werte werden schneller und leiser, negative langsamer und lauter, 0 = gleichmässig wie bisher. Beispiel +6 bei 8 Schlägen: Einsätze bei 0/32/54/70/81/88/94/97 % des Schritts, Lautstärke 100 → 29 %. |
| **Randomizer** | eigenes Menü direkt nach Arpeggiator | Lock (wiederholbarer Zufall über 16 Schritte), Gate-, Oktav- und Velocity-Spread, Akkord-Polyphonie und -Wahrscheinlichkeit, Wahrscheinlichkeiten für Note, Swap, Bass, Glide und Reverse. |
| **Reverse-Wahrscheinlichkeit** | Randomizer | Einzelne Arp-Noten spielen ihr Sample rückwärts. Anders als in 1.3 gilt das pro Stimme, gleichzeitig klingende Noten drehen sich also nicht gegenseitig um. |
| **Automation** | Automation-Ansicht, Select-Encoder | Alle neuen Arp-Parameter von Synths und Kit-Reihen, beim Kit (Affect Entire) auch die des Kit-Arps. |

**Dateien:**
- **1.2.1-Songs und -Presets** laden unverändert, auch MIDI- und CV-Spuren mit Arp-Einstellungen.
- **Stock-1.2.1** lädt v5-Dateien. Neue Einträge (Kit-Arp, Randomizer, Latch, Bounce) übergeht sie und verliert sie beim nächsten Speichern. Walk- und Pattern-Modi werden dort zu Up.
- **Interne Parameternummern** entsprechen jetzt denen von 1.3. Betroffen ist nur, welcher Parameter in der Automation-Ansicht beim ersten Öffnen eines alten Songs vorausgewählt ist. Werte, Automationen, Mod-Knob- und MIDI-Learn-Zuweisungen werden über Namen gespeichert und sind nicht betroffen.

**Nicht übernommen:**
- **Pad-Shortcuts aus 1.3 in Spalte 15** (Velocity-Spread, Lock, Note Probability): Diese Pads dienen in 1.2.1 dem Patchen.
- **MIDI-Follow-CCs für Arp-Parameter:** 1.2.1 ordnet MIDI Follow nach dem Pad-Raster, eine Übernahme hätte bestehende Belegungen verschoben.

**Geprüft:**
- **Build:** ohne Fehler und Warnungen im Arp-Code.
- **Zwei getrennte Code-Reviews** (Engine und Menüs):
  - Zwei echte Fehler gefunden und behoben: Bei ungesynctem Arp mit starkem Bounce verzögerte ein zu später letzter Ratchet-Schlag den nächsten Schritt. Und ein Synth ohne Clip konnte bei einer Note abstürzen (derselbe Fehler steckt in 1.3).
  - Eine Gegenprüfung der Korrekturen.
- **Auf dem Gerät nicht getestet.**

## v6: zweite Bounce-Version

v6 enthält v5 unverändert und zwei neue Einstellungen im Arpeggiator-Menü direkt beim Ratchet Bounce. Mit den Grundeinstellungen (Auto, Fade an) verhält sich v6 genau wie v5, auch beim Laden von Songs.

| Einstellung | Werte | Was sie tut |
|---|---|---|
| **Ratchet notes** | Auto, 2 … 8 | **Auto** wie in 1.3 und v5: zufällig 2, 4 oder 8 Noten, gewichtet mit «Number of ratchets». **2 … 8:** immer genau so viele. Ob ein Schritt ratchet, entscheidet dann nur noch die Ratchet-Wahrscheinlichkeit. Bei Sync 1/128 und 1/256 höchstens 2, bei 1/64 höchstens 4: Sonst wären die Abstände kaum länger als ein Audio-Block (2,9 ms). |
| **Bounce fade** | an, aus | **An** wie v5: Mit kürzer werdenden Abständen werden die Schläge leiser, wie bei einem Ball. **Aus:** Alle Schläge behalten ihre Velocity. |

Gleichmässige Ratchets teilen den Schritt jetzt durch die Notenzahl. Für 2, 4 und 8 ergibt das exakt dieselben Zeitpunkte wie vorher.

**Geprüft:** Build ohne Warnungen, zwei Builds mit identischer SHA-256, eine Code-Prüfung. Sie hat bestätigt, dass die Grundeinstellungen exakt wie v5 laufen, und keine Fehler in den neuen Pfaden gefunden. **Auf dem Gerät nicht getestet.**

**Die Figur aus «You Are The Seeds» nachbauen** (siehe `references/pettra-arp/ANALYSE.md`):

| Einstellung | Wert |
|---|---|
| Tempo | 138 |
| Arp Sync | 1/8 |
| Ratchet notes | 3 |
| Ratchet bounce | +6 |
| Bounce fade | aus |
| Ratchet probability | in der Automation-Ansicht nur auf der Achtel vor dem Schlag, an dem die Figur kommen soll, sonst 0 |

Das ergibt Einsätze bei 0, 99 und 169 ms nach Beginn der Achtel, also Abstände von 99, 70 und 49 ms. Gemessen wurden im Stück 99, 64 und 46 ms bei 0:57 und 102, 75 und 46 ms bei 6:39.

## v7: SD-Karte über USB

v7 enthält v6 unverändert und dazu den Dateizugriff über USB-MIDI aus Community-Firmware 1.3 (SysEx-Protokoll «smSysex»). Die Karte bleibt dabei im Deluge. Damit laufen am Computer:

| App | Was geht |
|---|---|
| [DEx](https://dex.silicak.es) | Datei-Browser: Ordner ansehen, Dateien hoch- und herunterladen, umbenennen, kopieren, verschieben, löschen, Ordner anlegen. Dazu wie bisher Display-Spiegelung und Screenshots. |
| [deluge-editor](https://cyface.github.io/deluge-editor/) | Synth- und Kit-Presets direkt von der Karte öffnen und wieder dorthin speichern. |

**Bedienung:** Deluge per USB an den Computer, die Seite in Chrome, Edge oder Opera öffnen und den MIDI-Zugriff erlauben. Die Apps erkennen den Dateizugriff selbst.

**Angepasst an 1.2.1** (sonst wie 1.3):
- Nur über USB. Anfragen über die DIN-Buchsen ignoriert v7, weil 1.2.1 dort keinen Überlaufschutz hat.
- Der USB-MIDI-Sendepuffer ist viermal so gross wie in 1.2.1 (12 statt 3 KB). Eine Antwort geht nur als Ganzes hinaus, sobald sie Platz hat. So passt auch eine volle Ordnerseite mit langen Namen hinein, und deluge-editor sieht jeden Ordner vollständig. In 1.2.1 hätte ein voller Puffer die älteste noch nicht gesendete Nachricht überschrieben.
- Dateinamen mit Zeichen ausserhalb von ASCII (z. B. Umlaute) verschickt v7 maskiert. So bleibt die Liste lesbar, statt die ganze Antwort zu verderben.

**Grenzen:**
- **deluge-editor meldet in Rot «needs community 1.3.0 or later»,** weil der Deluge ehrlich 1.2.1 meldet. Die Meldung stimmt hier nicht: Öffnen und Speichern funktionieren trotzdem.
- **deluge-editor blendet Regler aus, die es erst ab 1.3 kennt.** Darunter sind auch die Arp-Neuerungen aus v5/v6 (Spread, Chord, Walk, Kit-Arp …). Die stellst du am Deluge ein. Beim Speichern bleiben sie in der Datei erhalten.
- **«Live Edit» im deluge-editor geht nicht.** Es braucht zusätzliche Befehle aus einem Firmware-Fork, die auch 1.3 nicht hat.
- **Namen mit Umlauten:** Die Apps zeigen sie falsch an und können solche Dateien meist nicht öffnen. Am besten nur Namen aus A–Z, 0–9 und _ verwenden.
- **Während der Wiedergabe** keine Samples löschen oder überschreiben, die der Song gerade braucht. Grosse Übertragungen gehen über MIDI langsam; für ganze Sample-Sammlungen ist ein Kartenleser schneller.
- **MIDI zum Computer während Übertragungen:** Noten und Clock über USB teilen sich den Sendepuffer mit den Antworten und können deshalb verzögert ankommen. Beim Spielen mit einer DAW über USB keine Dateien übertragen. DIN-MIDI ist nicht betroffen.

**Geprüft:** Host-Test (der Firmware-Code auf dem PC mit AddressSanitizer, auf einer FAT32-RAM-Disk, 40 Prüfpunkte im Ablauf von DEx und deluge-editor), Build ohne Warnungen, zwei Builds mit identischer SHA-256, eine Code-Prüfung. Sie fand drei Fehler, alle behoben: verkürzte Ordnerlisten in deluge-editor, zu knapp bemessenes Warten auf Platz im Sendepuffer, fehlende Absicherung beim Schreiben ohne Puffer. **Auf dem Gerät nicht getestet.**

## v8: USB-Audio, Stufe 1 (Deluge → Computer)

v8 enthält v7 unverändert. Neu kann der Deluge sein Ausgangssignal über USB an den Computer schicken. Er erscheint dort als Audio-Eingang (Stereo, 24 Bit, 44,1 kHz) neben dem gewohnten MIDI. Am Computer kommt genau das an, was an den Ausgängen des Deluge anliegt: dasselbe Signal, das der Deluge beim Resampling aufnimmt, mit Master-Lautstärke und Eingangs-Monitoring.

**Einschalten:** Settings → Community features → **USB audio** (7-Segment: `UAUD`) auf an, **das Menü mit Back verlassen** und den Deluge neu starten (bis v15 speichert der Deluge die Einstellung erst beim Verlassen des Menüs, ab v16 sofort). Die Einstellung wirkt nur beim Start und nur, wenn der Deluge als USB-Gerät am Computer hängt, nicht als USB-Host. Ist sie aus (Grundeinstellung), verhält sich der Deluge exakt wie v7.

**Am Computer** (ohne Treiber, USB Audio Class 1.0):
- macOS: Audio-MIDI-Setup zeigt «Deluge» mit 2 Eingängen.
- Windows: Einstellungen → System → Sound → Eingabe: «Deluge».
- Linux: `arecord -l` zeigt «Deluge».
- In der DAW «Deluge» als Eingang wählen und das Projekt auf 44,1 kHz stellen.

**Technik:**
- Der Deluge gibt den Takt vor (asynchroner Endpunkt): Jedes USB-Paket enthält 44 oder 45 Frames, je nach Füllstand seines Puffers. Weicht sein Quarz vom Takt des Computers ab, gleicht er das mit einem Frame mehr oder weniger aus, ohne Rückkanal und ohne Umrechnung. Die Samples kommen bitgenau an.
- Latenz: etwa 6 ms Puffer plus 1–2 ms USB.
- Liest der Computer eine Weile nicht, verwirft der Deluge den veralteten Puffer und beginnt nach 6 ms Stille neu. Stockt die Audio-Engine, kommt eine kurze Stille statt Knacksern.
- Der Datenstrom läuft im USB-Interrupt über einen eigenen FIFO-Port. MIDI bleibt davon unberührt.

**Grenzen:**
- **Nicht auf dem Gerät getestet.** Es ist die erste Version auf dieser Hardware, ich brauche deine Rückmeldung.
- Nur 44,1 kHz. Läuft die DAW mit einer anderen Rate, rechnet das Betriebssystem um. Im exklusiven Modus oder mit ASIO muss das Projekt auf 44,1 kHz stehen.
- Mit USB audio an sieht der Computer den Deluge als neues Gerät. Die MIDI-Ports in der DAW müssen eventuell neu zugewiesen werden.
- Die Lautstärke regelt der Deluge. Der Computer hat dafür keinen Regler.
- Wird der Deluge mit USB audio nicht erkannt oder hängt er: USB-Kabel abziehen, starten, Einstellung ausschalten.
- **«USB audio gap»** (7-Segment: `UGAP`): Der Computer hat ein leeres Paket bekommen, also eine Lücke von 1 ms in der Aufnahme. Das kann bei sehr hoher Last vorkommen, weil der Deluge beim Berechnen jeder Spur alle Interrupts sperrt (so auch in der aktuellen Community-Firmware). Die Meldung erscheint höchstens alle 10 Sekunden. Bitte melden, wann sie kommt.
- Stufe 2 (Computer → Deluge) folgt, sobald Stufe 1 auf dem Gerät läuft.

**Geprüft:** Deskriptoren gegen die Regeln von USB 2.0, USB Audio 1.0 und USB MIDI (64 Prüfpunkte). Puffer und Paketsteuerung in einer Simulation über 10 Minuten mit Taktabweichungen bis 1400 ppm, einem Computer, der 200 ms nicht liest, und 20 ms Stillstand der Engine (39 Prüfpunkte, mit Sanitizern). Build ohne Warnungen, zwei Builds mit identischer SHA-256, eine Code-Prüfung. Sie fand zwei Fehler, beide behoben: MIDI-Empfang über USB wäre während des Streamings ausgefallen, und nach langem Sperren der Interrupts wäre nur eine Hälfte des Doppelpuffers nachgefüllt worden.

## v9: klügeres Sample-Streaming und RAM-Sparer für Kits

v9 enthält v8 unverändert und verbessert, wie der Deluge Samples von der SD-Karte lädt. Die Karte wird dadurch nicht schneller, aber ihre Leistung geht dorthin, wo sie gebraucht wird, und ungenutzte Kit-Reihen belegen keinen festen RAM mehr.

1. **Laden nach Dringlichkeit (Fehler aus 1.2.1 behoben).** Die Warteschlange für Ladeaufträge sortierte seit jeher nach Speicheradresse statt nach Priorität. Jetzt kommt zuerst, was eine spielende Stimme als Nächstes braucht, danach die Starts von Samples, die vielleicht nie spielen. **Wirkung:** weniger abbrechende Stimmen und weniger «card too slow» unter Last, etwa beim Kit-Wechsel während des Spielens.
2. **Doppelte Reserve beim Streaming.** Eine spielende Stimme hält drei statt zwei Cluster voraus. Der nächste Block hat damit zwei statt eine Clusterdauer Zeit, bei 32-KB-Clustern etwa 250–370 statt 120–190 ms (Stereo). Kostet 32 KB pro gerade streamende Stimme.
3. **Kurze Samples ganz im RAM (Fehler aus 1.2.1 behoben).** Samples bis vier Cluster (bis 128 KB bei 32-KB-Clustern) sollten ganz im Speicher bleiben. Ein Rechenfehler hielt stattdessen die ersten zwei Cluster doppelt, der Rest konnte verdrängt und neu geladen werden.
4. **Kit RAM saver** (Settings → Community features → **Kit RAM saver**, 7-Segment `KRAM`, standardmässig an): Kit-Reihen ohne Noten in allen Clips des Songs geben den festgehaltenen Start ihrer Samples frei, meist 64 KB pro Sample. Bei einem Kit mit 50 Samples, von denen der Song 5 nutzt, sind das rund 3 MB. Die Samples bleiben im Kit und im Cache, bis der RAM anderweitig gebraucht wird. Bekommt eine Reihe Noten, holt der Deluge ihren Start innerhalb einer Sekunde zurück. Ausgenommen sind Kits mit Kit-Arpeggiator oder MIDI-Learn fürs ganze Kit sowie Reihen mit eigenem MIDI-Learn, weil sie auch ohne Noten spielen können.
5. **Kein verlorener Schlag.** Ist der Start eines Samples beim Anschlag nicht im RAM, wartet die Stimme, bis er geladen ist (meist wenige Millisekunden, höchstens 100 ms), und spielt dann von Anfang an. In 1.2.1 fiel der Schlag in so einem Fall aus.

**Grenzen:** Nicht auf dem Gerät getestet. Beim Vorhören oder MIDI-Spielen einer bisher ungenutzten Reihe kann der allererste Schlag wenige Millisekunden später kommen, falls ihr Start inzwischen aus dem RAM verdrängt wurde. Sequenzierte Noten sind nicht betroffen, weil Reihen mit Noten ihre Starts behalten. Wer das nicht will, schaltet den Kit RAM saver aus.

**Geprüft:** die Warteschlange mit dem echten Firmware-Code auf dem PC (Reihenfolge, gleiche Prioritäten, Erkennung der niedrigsten Priorität), Build ohne Warnungen, zwei Builds mit identischer SHA-256, eine Code-Prüfung. Sie fand drei Fehler, alle behoben: Ein wartender Schlag konnte durch die automatische Release-Logik stumm bleiben, der Kit RAM saver konnte beim Laden eines Presets in eine Kit-Reihe in freigegebenen Speicher schreiben, und beim Erhöhen von Unison während des Wartens übernahm die neue Stimme einen falschen Zustand. Ausserdem laden ein später Einstieg in ein Sample (Stummschaltung mitten in der Note aufgehoben) und der Wechsel vom Cache zurück zur Karte jetzt mit der Priorität ihrer Stimme statt mit der niedrigsten.

## v10: Reverb in besserer Qualität

v10 enthält v9 unverändert und überarbeitet das Song-Reverb: ein neues Modell, zwei reparierte Modelle und funktionierende Filter. Alle Einstellungen liegen wie bisher im Reverb-Menü (Song und Sound).

1. **Neues Modell «Digital»** (Model → Digital, 7-Segment `DIGI`): die Plate von Jon Dattorro (1997), aufgebaut wie das Lexicon 224. Dichter, glatter Nachhall ohne metallisches Klingeln, weil die Modulation im Tank die Resonanzen ständig verschiebt. Stereo aus 14 Abgriffen, links und rechts unkorreliert.
   - **Time** (Room size): 0 ≈ 0,6 s, 30 (Standard) ≈ 4,5 s, 45 ≈ 18 s, 50 fast endlos. **Width:** Stereobreite, 0 = mono. **Damping**, **HPF** und **LPF** wie bei Mutable.
   - Gleich laut wie Mutable (Abweichung höchstens 0,5 dB), ein Wechsel des Modells springt also nicht in der Lautstärke.
   - Die aktuelle Community-Firmware hat ein Modell mit diesem Namen, aber mit Fehlern: Die modulierten Allpässe im Tank sind dort keine Allpässe (kürzerer, dünnerer Nachhall), beide Tank-Hälften teilen sich ein Dämpfungsfilter, und die Plate ist 2,2-mal zu klein skaliert. Diese Version folgt dem Paper.
2. **Mutable: Modulation wie im Original.** Durch einen Portierungsfehler liefen die beiden LFOs 16-mal langsamer als bei Mutable Instruments, der Nachhall stand deshalb fast still. Jetzt laufen sie wie in den Modulen Rings, Elements und Clouds (etwa 0,45 und 0,28 Hz), und das «Smearing» im ersten Diffusor ist wieder drin. Songs mit Mutable klingen dadurch etwas lebendiger, Lautstärke und Länge bleiben gleich.
3. **Damping läuft richtig herum (Mutable).** In 1.2.1 lief Damping beim Mutable-Modell verkehrt: 0 und 50 waren hell, 1 am dunkelsten, mit einem Sprung zwischen 0 und 1. Jetzt wie bei Freeverb: 0 hell, 50 dunkel, stufenlos. **Bestehende Songs klingen gleich**, nur die angezeigte Zahl ist jetzt 50 minus die alte (Standard 36 → 14). In der Datei steht der Wert weiter wie in 1.2.1, Songs bleiben also zwischen den Versionen austauschbar.
4. **HPF repariert.** Wegen eines Rechenfehlers reichte er nur bis 85 Hz statt bis 540 Hz, und 1.2.1 übernahm ihn beim Laden eines Songs gar nicht. Jetzt: 0 = 20 Hz, 25 = 190 Hz, 50 = 540 Hz, gespeichert und geladen mit dem Song. Ein in alten Songs gespeicherter Wert wird mit der Frequenz geladen, die er damals tatsächlich hatte (alt 50 → neu 12).
5. **Neu: LPF** (Mutable und Digital) zum Abdunkeln des ganzen Halls: 0 = 500 Hz, 25 = 3,2 kHz, 49 = 18,6 kHz, **50 = aus** (Standard). Das Damping dagegen dunkelt den Nachhall mit der Zeit immer stärker ab.
6. **Freeverb:** Bei Width unter dem Maximum war der rechte Kanal lauter, bei Width 0 um 3,6 dB, jetzt ausgeglichen (≤ 0,1 dB). Und wenn ein langer, lauter Hall den Wertebereich überschritt, klappte der Wert um und knackte laut. Jetzt wird er begrenzt. Sonst rechnet Freeverb bitgleich wie bisher.

**Grenzen:**
- Nicht auf dem Gerät getestet.
- Digital braucht etwa ein Drittel mehr Rechenzeit als Mutable (gerechnet einmal für den ganzen Song).
- Ein mit Digital gespeicherter Song spielt in 1.2.1 ohne Reverb, weil 1.2.1 das Modell nicht kennt.
- HPF und LPF gibt es wie bisher nur für Mutable und Digital, nicht für Freeverb.
- Songs aus der Community-Firmware 1.3 liest v10 mit deren Damping-Richtung, denn auch die Community hat Damping inzwischen umgedreht (19.9.2026). Ältere 1.3-Songs lassen sich davon nicht unterscheiden und kommen mit umgekehrtem Damping, genau wie in der Community-Firmware selbst. Deine Songs aus 1.2.1 und meinen Versionen betrifft das nicht.

**Geprüft:** Host-Test mit dem Reverb-Code der Firmware (47 Prüfpunkte, mit UndefinedBehaviorSanitizer): alle Modelle stabil bei maximaler Room Size, Nachhallzeiten und Pegel von Digital gegen Mutable, Stereo-Balance und -Breite, LFO-Raten, Grenzfrequenzen von HPF und LPF, Umrechnung aller alten Damping- und HPF-Werte (identischer Klang). Build ohne Warnungen, zwei Builds mit identischer SHA-256, eine Code-Prüfung. Sie fand zwei Fehler, beide behoben: Die Presets auf der Reverb-Taste (Small, Medium, Large) wären mit der neuen Damping-Richtung beim Mutable-Modell viel dunkler geworden, und Songs aus der Community-Firmware 1.3 wären mit umgekehrtem Damping geladen worden.

## v11: Delay in besserer Qualität, kein Knacksen beim Speichern

v11 enthält v10, überarbeitet das Delay (Sounds, Kits, Audio-Spuren und Song), behebt das Knacksen beim Speichern und einen Speicherfehler des Reverbs aus v10. Die Messungen stammen aus einem Test mit dem Delay-Code der Firmware auf dem PC.

1. **Saubere Wiederholungen nach jeder Zeitänderung (Fehler aus 1.2.1 behoben).** Das Delay des Deluge dreht seinen Puffer schneller oder langsamer, wenn sich die Zeit ändert. Danach sollte es einen neuen Puffer anlegen und wieder verlustfrei laufen. Ist der neue Puffer aber gleich gross wie der alte, lehnte 1.2.1 ihn ab. Das Delay blieb dann für immer im Umrechnungsmodus, schon wenn der Regler kurz bewegt und zurückgedreht wurde.
   - **Folge in 1.2.1:** Jede Wiederholung verlor 9 dB bei 10 kHz und 24 dB bei 15 kHz, darum wurden die Echos so schnell dumpf.
   - **Jetzt:** Nach einer Zeitänderung sind die Wiederholungen wieder bitgenau.
2. **Umrechnung mit kubischem Kern.** Solange die Zeit sich ändert oder moduliert wird (LFO, Hüllkurve, Automation), schreibt und liest das Delay mit einem kubischen Kern (Catmull-Rom), bei jeder Geschwindigkeit. 1.2.1 nahm dafür Dreiecke, die doppelt so breit waren wie nötig.
   - Eine Wiederholung verliert bei 10 kHz 0,6 dB statt 6,9 dB.
   - Das Rauschen der Umrechnung sinkt von −59 auf −80 dB.
   - Sehr lange Delays (ab etwa 4 s) behalten bei 5 kHz −3,1 dB statt −6,9 dB.
   - Geschwindigkeit und Feedback gleiten innerhalb eines Audio-Blocks, statt alle 2,9 ms zu springen. Das Summen bei 344 Hz sinkt von −78 auf −103 dB.
3. **Kein Knacken beim Ändern der Zeit.**
   - Ein Teil der Schreibwege lag in 1.2.1 ein Sample daneben, beim Wechsel zwischen ihnen sprang die Zeit. Jetzt schreiben alle an dieselbe Stelle. Das Delay ist dadurch 1 Sample (0,02 ms) länger.
   - 1.2.1 legte bei jeder noch so kleinen Verlängerung einen neuen, doppelt so grossen Puffer an. Ein LFO auf der Delay-Zeit wechselte dadurch laufend die Puffer, jedes Mal mit einem kleinen Sprung. Jetzt passiert das erst ab 25 % Verlängerung.
   - Bei einem grossen Sprung der Zeit legt das Delay sofort einen neuen Puffer an. Dieser gleitet jetzt genau wie der alte, den er ersetzt, sonst springt die Zeit, wenn er übernimmt.

   Grösster Sprung im Test gegenüber einem ruhigen Ton:
   - 5 % kürzer: 0,1× statt 3,8×
   - 5 % länger: 0,1× statt 1,9×
   - 40 % länger: 0,1× statt 1,7×
   - 70 % länger: 0,1× statt 2,5×
   - weniger als halb so lang: 0,1× statt 4,1×
   - über die Grundgeschwindigkeit fahren: 0,1× statt 1,7×
   - LFO ±3 % auf der Zeit: 0,1× statt 1,7×
4. **Analog-Modus:** Die Sättigung arbeitet mit weniger Aliasing (−18 statt −14 dB Störanteil bei stark angetriebenem Feedback), wie bereits beim Kompressor.
5. **Neu: LPF und HPF im Feedback** (Delay-Menü, nach Sync). Jede Wiederholung wird dunkler bzw. dünner als die vorige, wie bei Band- und Eimerketten-Delays.
   - **LPF:** 0 = 500 Hz, 25 = 3,2 kHz, 49 = 18,6 kHz, 50 = aus (Standard).
   - **HPF:** 0 = aus (Standard), 1 = 25 Hz, 25 = 190 Hz, 50 = 540 Hz.
   - Im Digital-Modus begrenzt das Delay erst nach den Filtern. So bleibt der HPF auch bei vollem Feedback innerhalb der Aussteuerung.
   - Gespeichert werden sie mit dem Sound, Kit oder Song, aber nur, wenn sie eingeschaltet sind.
6. **Zwei kleine Fehler aus 1.2.1 behoben:** Ein kopierter Sound behält beim Delay-Sync Triole oder Punktierung, und ein neu startendes Delay beginnt ohne Reste im Filter.
7. **Kein Knacksen mehr beim Speichern (Fehler aus 1.2.1 behoben).** Während der Deluge einen Song oder ein Preset für die Karte zusammenbaut, lief in 1.2.1 nur die Anzeige weiter. Der Aufruf der Audio-Engine war an dieser Stelle auskommentiert. Die Ausgabe wiederholte deshalb ihren letzten Puffer, das gab bei jedem Speichern ein kurzes Furzen oder Knacksen. Jetzt läuft die Audio-Engine auch dabei weiter und lädt die Samples nach, die gerade spielen, genau wie beim Laden eines Songs. Das gilt für Songs, Synth- und Kit-Presets und Einstellungen.

8. **Reverb-Damping richtig gespeichert (Fehler aus v10 behoben).** Das dunkelste Damping (50) von Mutable und Digital wurde auf dem Deluge als 0 gespeichert, also als «kein Damping», und kam nach dem Laden hell zurück. Die Firmware wird mit `-funsafe-math-optimizations` gebaut, und damit machte der Compiler aus der Rechnung einen Vergleich, der genau diesen Wert falsch rundete. Auf dem PC trat das nicht auf. Gefunden hat es der Reverb-Test im Cortex-A9-Emulator (siehe «Tests im Emulator»).

**Grenzen:**
- Nicht auf dem Gerät getestet, auch der Fix fürs Speichern nicht.
- Delays über 2 s laufen wie bisher im Umrechnungsmodus, weil ihr Puffer nicht grösser werden kann.
- Wird die Zeit auf einen Schlag stark verändert, gleitet die Tonhöhe der Wiederholungen während eines Durchlaufs, wie bei einem Band, das bremst. Das ist wie in 1.2.1, nur ohne den Sprung beim Pufferwechsel.
- Das Umrechnen braucht mehr Rechenzeit, aber nur, solange die Zeit sich ändert oder moduliert wird. Bis zur Grundgeschwindigkeit sind es 4 Gewichte pro Sample, darüber mehr (8 bei doppelter Geschwindigkeit), dazu die kubische Leseinterpolation. Im Normalbetrieb ist der Aufwand gleich wie bisher.
- Das Speichern dauert etwas länger, weil der Deluge dabei Audio rechnet.

**Geprüft:**
- **Host-Test** mit dem Delay-Code der Firmware (24 Prüfpunkte, UndefinedBehaviorSanitizer bricht beim ersten Fehler ab): Wiederholungen nach Zeitänderungen bitgenau, Höhen und Rauschen bei Modulation, Blocktreppen, Sprünge bei sieben Arten von Zeitänderungen, keine neuen Puffer bei langen Delays, Filterkurven, Aussteuerung mit HPF bei vollem Feedback, Filter abschalten ohne Sprung.
- **Build:** ohne Warnungen, zwei Builds mit identischer SHA-256.
- **Emulator:** Alle Tests laufen zusätzlich auf dem Maschinencode des Cortex-A9 (siehe «Tests im Emulator»). Dort fiel der Damping-Fehler aus v10 auf.
- **Code-Prüfungen:** drei, mit einer Gegenprüfung jedes Befunds. Sie fanden sechs Fehler in meinen Änderungen, alle behoben:
  - Ein neuer Puffer knackte beim Übernehmen.
  - Der HPF übersteuerte bei vollem Feedback bis auf das 1,84-Fache der Begrenzung.
  - Ein abgeschalteter HPF behielt seinen Zustand. Das gab später einen Klick, 175-mal so steil wie der Ton.
  - Filterreste blieben nach einem Neustart des Delays stehen.
  - Unnötige Rechenzeit ging in Bibliotheksaufrufe.
  - Beim Speichern wären gestreamte Samples ohne Nachladen abgebrochen.

## v12: Frequenz-Drone

Ausführliches Handbuch mit Schnellstart, allen Bedienelementen, Menü, Rezepten und technischen Daten: **`docs/Drone-Handbuch.pdf`** (Quelle: `docs/drone-handbuch.html`).

v12 enthält v11 und bringt einen Drone: bis zu 16 Dauertöne, eingestellt in Hz oder als Note, unter die Musik gemischt. Jeder Ton kann schweben wie in Brainwave-Apps, im Tempo des Songs pulsieren und mit der Sidechain ducken.

**Öffnen:** Im Song-View die Taste **Scale** drücken, oder im Song-Menü des Song-Views (Select drücken) den Punkt **Drone** wählen. Zurück in den Song-View geht es mit **Back**, **Song** oder **Scale**. Im Arranger fehlt der Menüpunkt, weil der Rückweg in den Song-View führt.

**Die Drone-Ansicht** ist aufgebaut wie der Song-View, mit einem Ton pro Zeile statt einem Clip:

| Element | Funktion |
|---|---|
| Zeilen | Ton 1 unten, wie Clip 1 im Song-View. Der Y-Encoder scrollt zu den Tönen 9–16. |
| Mute-Spalte | Ton an und aus (grün = an), seine Einstellungen bleiben dabei erhalten |
| Audition-Spalte | Ton wählen (weiss = gewählt) |
| Pads einer Zeile | Pegel als Balken. Ein Pad antippen setzt den Pegel, in 16 Stufen. |
| Farbe | Modus: orange = Ton, blau = binaural, türkis = monaural, violett = isochron. Beats bis 12 Hz pulsieren sichtbar. |
| Oberer Goldknopf | Tonhöhe. In Hz: 1 Hz pro Raste, schnell gedreht 10 Hz, mit Shift 0,01 Hz. Als Note: Halbtöne, mit Shift Cent. |
| Oberen Goldknopf drücken | zwischen Hz und Note wechseln, die Tonhöhe bleibt |
| Unterer Goldknopf | Beat: 0,1 Hz pro Raste, mit Shift 0,01 Hz. Mit Tempo-Sync der Notenwert. |
| Unteren Goldknopf drücken | Tempo-Sync an und aus |
| Select drehen | Modus des gewählten Tons |
| Select drücken | Menü des Tons: Modus, Tonhöhe als Hz oder Note, Frequenz, Note, Cent, Beat, Sync, Klangfarbe, Pegel, Pan. Dazu die Lautstärke des ganzen Drones und die Sidechain. |
| X-Encoder | Pegel fein, mit Shift Pan |
| Play, Record, Tempo, Speichern, Laden | wie gewohnt |

Das OLED zeigt Ton, Modus, Tonhöhe und den Beat mit seinem Bereich (Delta, Theta, Alpha, Beta, Gamma). Die 7-Segment-Anzeige zeigt die Frequenz mit so vielen Nachkommastellen, wie Platz haben (55.25, 440.0, 1200), oder die Note. Beat, Notenwert (16 für 1/16), Pegel und Pan erscheinen dort beim Drehen kurz.

**Modi:**
- **Ton:** ein ruhiger Dauerton.
- **Binaural:** Das linke Ohr hört die Frequenz minus den halben Beat, das rechte plus den halben Beat. Die Schwebung entsteht im Kopf, dafür braucht es Kopfhörer.
- **Monaural:** Beide Töne klingen in beiden Ohren, die Schwebung ist im Klang selbst hörbar.
- **Isochron:** Der Ton pulsiert im Beat an und aus, mit weichen Flanken von 6 ms.

**Klangfarben:** Sine (rein), Soft (Obertöne 2–5, weich), Organ (Oktaven wie Zugriegel), Rich (Obertöne 2–8, sägezahnartig). Sie sind bandbegrenzt: Obertöne über 18 kHz fallen weg, damit nichts spiegelt.

**Tempo-Sync:** Der Beat folgt dem Songtempo, mit Notenwerten von 1/1 bis 1/64. Bei 120 BPM ergibt 1/16 einen Beat von 8 Hz (Alpha). Während der Song läuft, rastet der Puls auf dem Raster ein, im Test auf 1,2 ms genau. Das gilt auch mit externer MIDI-Clock (der Drone folgt ihr zwischen den Clock-Ticks) und mit Sync-Scaling, weil Tempo und Position wie beim Metronom gezählt werden.

**Tonhöhe als Note:** Jeder Ton lässt sich als Note mit Cent einstellen. Er folgt dann dem Master Tune (Settings → Tuning), wie alles andere im Deluge. Ist der Master Tune auf ein externes Gerät eingestellt, zum Beispiel eine volca keys, passt der Drone dazu.

**Pegel:**
- Ein Ton auf Pegel 50 liegt bei −12 dBFS. Die Drone-Lautstärke 50 entspricht 0 dB, die Stufen sind je 1 dB.
- Der Drone folgt der Song-Lautstärke wie das Metronom. Er kommt nach den Song-Effekten dazu, also ohne Song-Reverb und -Delay.
- In Stem-Exporten fehlt er, über USB-Audio ist er zu hören.
- Viele laute Töne zusammen können übersteuern.

**Sidechain:** Der Drone duckt sich unter die Sidechain-Auslöser des Songs, etwa eine Kick mit «Send to sidechain». Stärke, Form, Attack, Release und Sync stehen im Sidechain-Menü des Drones. Der Pegel gleitet von Sample zu Sample, wie beim Sidechain-Fix aus v3, darum knackt nichts.

**Gespeichert** wird der Drone mit dem Song (Tag `<drone>`), aber nur, wenn er eingerichtet ist. Ältere Firmware überspringt den Tag. Beim Laden eines anderen Songs oder bei Clear Song blendet der Drone in etwa 60 ms aus, der Drone des neuen Songs setzt aus der Stille ein. Nach einem Stem-Export setzt er ebenfalls aus der Stille wieder ein.

**Grenzen:**
- Nicht auf dem Gerät getestet.
- Rechenzeit auf dem Cortex-A9, im Emulator gezählt: ein Sinuston 0,35 % CPU, ein binauraler Ton 0,5 %, 16 binaurale Töne mit Obertönen 6,9 %.
- Die Wellentabellen belegen 27 KB internes RAM.
- Der Drone sendet kein MIDI.
- Seine Einstellungen gehören nicht zum Undo. Ein per MIDI gelerntes Undo wirkt in der Drone-Ansicht nicht.

**Geprüft:**
- **Host-Test** mit dem Drone-Code der Firmware (39 Prüfpunkte), auf dem PC mit UndefinedBehaviorSanitizer und auf dem Cortex-A9 im Emulator:
  - Reinheit (Sinus −108 dB)
  - Frequenzen auf 0,001 Hz genau, Note und Master Tune
  - Binaural, monaural und isochron
  - Knackfreiheit bei allen Änderungen
  - Sidechain-Rampe, Bandbegrenzung, Tempo-Lock und Pegel
  - Ausblenden zwischen zwei Songs ohne Sprung, Neustart aus der Stille
- **Build:** ohne Compiler-Warnungen (eine Stack-Warnung beim Aufbau der Wellentabellen ist behoben). Zwei komplette Neubauten aus dem Commit, in einer eigenen Arbeitskopie mit allen 448 Dateien neu übersetzt, ergeben dieselbe SHA-256 wie die ausgelieferte Datei.
- **Code-Prüfung:** zwei Prüfer (Bedienung und Menüs; Speichern und Audio), acht Befunde, jeder einmal gegengeprüft:
  - Sechs bestätigt und behoben:
    - möglicher Absturz bei Undo per MIDI in der Drone-Ansicht
    - Knacks beim Laden eines Songs im Stillstand
    - Beat-Sync mit Sync-Scaling rastete nicht ein
    - falscher Rückweg aus dem Arranger
    - Back-LED blinkte nach dem Menü weiter
    - Beat und Pan fehlten auf der 7-Segment-Anzeige
  - Einer ist nicht hörbar (externe Clock), wurde aber trotzdem verbessert.
  - Einer ist widerlegt (MIDI Follow verhält sich wie im Song-View).

## Leistungs-, Mess- und Testversionen

- **`perf/`:** v12-perf, `deluge-1.2.1-mastertune-v12-perf-ef5caee8.bin`, SHA-256 `d08b09bc…19ce4060c`.
  - v12 mit bitgleich schnelleren Filtern und Oszillatoren: im Volllast-Test −20 % Rechenlast.
  - Mit Culling wie auf dem Gerät klingen 38 % mehr Stimmen.
  - Enthält den CPU-Monitor. Details, Beweise und Vergleich auf dem Gerät: `perf/README.md`.
- **`diag/`:** die Messversion v12-diag, v12 plus CPU-Monitor.
  - Zeigt CPU-Last, Stimmen, Qualitätsabsenkung und SD-Zeiten auf dem OLED und per USB-MIDI.
  - Dazu `tools/cpu_monitor.html` und ein Test-Song. Details: `diag/README.md`.
- **`l2test/`:** zwei Testversionen von v16 mit eingeschaltetem L2-Cache, einmal nur für Code, einmal auch für Daten. Mit v14 zeigte die erste Messung am Gerät: keine abgeschnittenen Stimmen mehr.
  - Zum Messen mit dem CPU-Monitor am Gerät, noch nicht für Auftritte.
  - Anleitung, Risiken und Prüfungen: `l2test/README.md`.
- **`prof/`:** die Profiler-Messversion v15-prof, einmal ohne L2 und einmal mit L2 für Code.
  - Settings → CPU monitor → Profile schickt 1000-mal pro Sekunde an den Computer, wo die CPU gerade arbeitet, dazu die genaue Rechenzeit jeder Spur.
  - `tools/profiler.html` (Chrome/Edge) oder `tools/deluge_profiler.py` zeigen die Zeit pro Spur, Task und Funktion.
  - Anleitung und Prüfungen: `prof/README.md`.
  - Ab v16 hat jede Version den Profiler, auch die L2-Testversionen. Die passende `.symbols.json` liegt neben der Firmware.
- **`research/OPTIMIERUNG.md`:** alle Messungen und Erkenntnisse zur Optimierung.

## v13: mehr Leistung, Ping-Pong-Arp, flimmerfreies Dimmen, genaueres MIDI, Drone-Feinschliff

v13 enthält v12 und die Leistungsversion v12-perf, dazu die folgenden Neuerungen. **Auf dem Gerät ist nichts davon getestet**; geprüft ist alles im Emulator mit dem Maschinencode der Firmware und von unabhängigen Gegenprüfern.

**Drone** (Handbuch `docs/Drone-Handbuch.pdf`, auf v16 nachgeführt):
- **Select drehen stellt die Tonhöhe** in spürbaren Rasten: 1 Hz pro Klick, mit Shift 0,1 Hz; als Note ein Halbton, mit Shift ein Cent. Die Goldknöpfe bleiben für schnelles Stimmen.
- **SYNTH / KIT / MIDI / CV wählen den Modus** des gewählten Tons: Ton / Binaural / Monaural / Isochron. Die LED der Taste zeigt den aktuellen Modus.
- Die Pads leuchten ruhig, nichts pulsiert mehr.
- Isochrone Pulse haben **Attack und Release** (0–50, angezeigt 0–100 %, je 2 % der halben Zykluslänge, nie kürzer als 1 ms).
- **Triolen** beim Tempo-Sync: 1/1T bis 1/64T.
- **Goldknöpfe nach Mod-Sektion:** Cutoff/Resonance = Tonhöhe/Beat, Attack/Release = Puls des gewählten Tons, Sidechain/Reverb = Ducking und Reverb-Send des ganzen Drones. Die LED-Ringe zeigen die Werte.
- **Sidechain linear:** Stärke 30 duckt um 8 dB statt 16 dB.
- **Reverb-Send:** Der Drone kann ins Song-Reverb senden (0–50).

**Arpeggiator: Ping-Pong-Bounce** (Presets zum Anpassen in `presets/`):
- **Ratchet notes → Roll:** Die Schläge werden immer schneller wie ein Ping-Pong-Ball zwischen zwei Platten, die sich schliessen. Schlag j liegt bei S·(1 − rʲ) der Ratchet-Länge S, mit r = 0,95 bei Bounce +1 bis 0,5 bei +10. Unter 16 ms Abstand geht es als Wirbel weiter, bis zum nächsten Schlag im Raster. Negativer Bounce spielt dasselbe rückwärts.
- **Bounce length 1–16:** Ein Ratchet dauert mehrere Arp-Schritte. Die Note bleibt stehen, danach geht der Arp mit der nächsten Note weiter. Rhythmus und Sequenzlänge bleiben im Takt.
- **Bounce velocity** (früher «Bounce fade»): Even, Fade (wie bisher) oder Rise (lauter, je schneller).
- Mit Bounce length 1 und ohne Roll spielt alles exakt wie in v12.

**Pads flimmerfrei gedimmt** (Settings → Community Features → Flicker-free dimming, standardmässig On):
- **Warum es flimmerte:** 1.2.1 dimmt die Pads über den LED-Controller (PIC) mit Dunkelpausen. Unter 40 % werden die Pausen so lang, dass der ganze Bildaufbau langsamer wird, bei 0 % 6,8-mal.
- **Neu:** Der PIC frischt immer im vollen Tempo auf und dimmt nur bis 43,5 %. Den Rest dimmt die Firmware über die Farbwerte der Pads, der Sidebar und der Goldknopf-LEDs. Jede Stufe gibt gleich viel Licht wie vorher (im Emulator auf ±1 % geprüft).
- **Kompromiss:** Die Tasten-LEDs, die 7-Segment-Anzeige und Pads, die der PIC selbst blinken lässt (schneller Play-Cursor, blinkende Shortcuts in Menüs), dimmen nur bis 43,5 %. Sie sind bei ganz tiefer Helligkeit also heller als bisher.
- **Off** sendet Byte für Byte dasselbe wie 1.2.1, zum Vergleichen.

**CPU-Monitor in Worten** (Settings → CPU monitor: Off, On, Alerts):
- Klein oben links, ohne Balken: «CPU 43%  24 voices», also Rechenlast und klingende Stimmen.
- Darunter, nur wenn es passiert:
  - «quality lowered»: Der Deluge rechnet einfacher, um mitzukommen.
  - «voices cut!»: Er schneidet Stimmen ab. Die Meldung blinkt und steht noch 2 s über den letzten Schnitt hinaus.
- **Alerts:** nur diese Warnungen, sonst nichts.
- Die SysEx-Werte für `tools/cpu_monitor.html` bleiben gleich (Modus On).

**Schneller speichern:** Speichern während des Abspielens dauert im Emulator 22 statt 344 ms (Song mit 3 Synths) bzw. 66 statt 716 ms (5 Synths), weiterhin ohne Knacksen.

**Kein Lade-Fenster mehr beim Durchblättern:** Beim Laden und Speichern läuft eine kleine Animation oben rechts im OLED (aus der Community-Firmware 1.3). Der Presetname bleibt sichtbar.

**MIDI- und Gate-Ausgänge genauer:**
- Ereignisse spät im Audio-Block gingen einen ganzen Block (2,9 ms) zu früh hinaus, vor allem beim Speichern.
- Ein Fehler aus 1.2.1: Der Zähler des MIDI/Gate-Timers wurde vor dem Start nie auf 0 gesetzt. Ein Lauf konnte darum bis 38 Samples zu früh oder, nach einem Überlauf, rund 127 ms zu spät kommen.
- Jetzt liegt jeder Timer-Lauf auf ±1,4 Samples genau.

**Leistung:** v12-perf (Filter und Oszillatoren) plus schnellere Spur-Effekte (NEON für Lautstärke, Pan und Reverb-Send, eigene Schleifen für Phaser, Chorus, Flanger, EQ und SRR), alles bitgleich. Im Volllast-Test: Bedarf 89 % statt 118 % (v12); mit Culling wie auf dem Gerät klingen im Mittel 32 statt 21 Stimmen.

**Fehler aus 1.2.1 behoben:** LFOs, Mod-FX und Arp starteten nach dem Laden an einer zufälligen Stelle (nicht initialisierter Speicher). Jetzt beginnen sie immer gleich.

**Geprüft:**
- **Gegenprüfer:** Jede grössere Neuerung wurde von einem unabhängigen Gegenprüfer mit eigenen Angriffsfällen geprüft. Alle Befunde sind behoben und nachgetestet:
  - Drone: doppelte Song-Lautstärke im Reverb-Send, Grenzfälle bei extremen Beats, 7-Segment-Anzeige, Automation überschrieb die Knopf-LEDs
  - MIDI: Zählerrest des Timers
  - Ping-Pong-Arp: hängende Note ohne Sync bei Latch-Wechsel, stille Spannen bei ausgelassener Note, Doppelschläge mit Swing, Sequenzlänge
  - Pads: ungedimmte Blinkfarben (dokumentiert), kurzer Hell-Blitz beim Umschalten (behoben)
- **Arp im Emulator mit der echten Firmware:**
  - jeder Anschlag gegen die Formel, in 7 Fällen (≤ 3 ms Abweichung durch die Audio-Blöcke)
  - 11 Grenzfälle mit Note-ons und Note-offs (`tests/arp`)
- **Pads:** was an den PIC geht, auf allen 26 Helligkeitsstufen, On und Off (`tests/pads`).
- **Song:** Der Volllast-Song klingt bit-gleich, soweit nichts Hörbares geändert wurde.
- **Build:** ohne neue Warnungen. Zwei komplette Neubauten ergeben dieselbe SHA-256 (je 465 neu übersetzte Dateien, `9ff41174…7c4c50e7`). Die Patches ergeben mit `git am` auf `release_1_2_1` genau diesen Stand.

## v14: Reverb ohne Wabbeln, Delay ohne Tonhöhensprung, Countdown beim Song-Wechsel

v14 enthält v13 und bringt drei Neuerungen und zwei Korrekturen beim Lesen und Schreiben der Karte. **Auf dem Gerät ist nichts davon getestet.** Geprüft ist alles im Emulator mit dem Maschinencode der Firmware, auf dem PC mit dem Code von Reverb und Delay und von unabhängigen Gegenprüfern.

**Reverb: steht still, klarer Einsatz** (Menü Reverb, im Song und in jedem Sound, nach LPF):
- **Warum es wabbelte:** Die Modelle Mutable und Digital lesen ihre Verzögerungen an langsam wandernden Stellen. Das soll metallisches Klingeln verhindern. Bei einem gehaltenen Ton schwankt der Hall dadurch um etwa 16 dB in der Lautstärke und um 5 Cent (Mutable) bzw. 19 Cent (Digital) in der Tonhöhe. Das ist das Wabbeln und ein guter Teil des Verwaschenen.
  - Seit v10 lief diese Bewegung beim Mutable-Modell im Tempo des Originals, 16-mal schneller als in 1.2.1.
- **Modulation 0–50, neu Standard 0:**
  - Bei 0 steht der Hall still: Ein gehaltener Ton bleibt darin auf 0,2 dB genau. Die Resonanzen ragen beim Mutable-Modell etwa 3 dB mehr heraus als mit voller Bewegung, beim Digital-Modell nicht.
  - 50 klingt wie v10 bis v13.
  - Für Freeverb nicht vorhanden, Freeverb moduliert nicht.
- **Pre-delay 0–100 ms, Standard 0 (alle Modelle):** Der Hall setzt später ein, der trockene Klang steht zuerst für sich. 10–30 ms machen den Hall deutlich klarer, ohne dass er abgesetzt wirkt.
- **Tipps gegen Verwaschenes:** HPF auf etwa 25 (190 Hz) nimmt den Bass aus dem Hall. Etwas mehr Damping macht die Fahne dunkler. Ohne Modulation klingen beim Mutable-Modell die Höhen etwas länger nach; wem das zu hell ist, nimmt Damping ein paar Schritte höher.
- Songs ohne diese Einstellungen laden mit Modulation 0 und ohne Pre-delay, also stiller als bisher. Wer den alten Klang will, stellt Modulation auf 50.

**Delay: Zeitänderung ohne Tonhöhensprung** (Menü Delay, nach Type):
- **Time change: Fade (neu, Standard) oder Tape.**
  - Bisher bog eine neue Delay-Zeit die Echos in der Tonhöhe, denn was schon im Puffer lag, spielte schneller oder langsamer ab. Bei 25 % kürzerer Zeit waren das +386 Cent, und das Feedback trug es weiter.
  - Mit **Fade** startet die neue Zeit in einem frischen Puffer. Der Eingang blendet in 23 ms hinüber, der alte Puffer spielt seine Echos mit alter Zeit und Tonhöhe aus. Die Tonhöhe bleibt auf 0,01 Cent genau.
  - **Tape** klingt wie bisher, mit Tonhöhengleiten.
- **Alte Songs:** Delays mit moduliertem oder automatisiertem Delay-Rate laden als Tape, damit sie klingen wie bisher. Alle anderen laden als Fade.
- **Mitbehoben:**
  - kein Klick mehr beim ersten Echo nach einer Pause
  - das Ende der Echos blendet aus statt abzureissen
  - Delays bis 4 s ohne Aliasing (Puffer bis 4 statt 2 s)
  - Fehler aus 1.2.1: Unter 3 % Feedback warf der Delay seinen Puffer bei jeder Runde weg.
- **Rechenlast:** +0,8 % pro Delay im Ruhezustand, +1,1 % während eines Wechsels.

**Song-Wechsel mit Countdown** (Song laden, während einer spielt):
- **Countdown:** Solange Wiederholungen bleiben, zählt er die Loops, in der letzten Loop die Takte, im letzten Takt die Beats 4-3-2-1. Er stimmt auch mit Swing und externer MIDI-Clock.
  - **OLED:** dauerhaft in der Titelzeile, z. B. «Bars remaining 3». Die Songliste mit dem nächsten Song bleibt sichtbar.
  - **7-Segment:** die Zahl, blinkend solange Loops gezählt werden. Beats tragen einen Punkt, damit man sie von Takten unterscheidet.
- **Regler bis zum Wechsel:** Ab LOAD steuern Goldknöpfe und Mod-Tasten die Master-FX des laufenden Songs, auch wenn Affect Entire aus ist. Der neue Song behält seine eigenen Werte. Beim Wechsel zeigt nichts mehr auf den alten Song.

**Karte: zwei seltene Fehler behoben** (gefunden vom Gegenprüfer der L2-Testversionen):
- **Cache-Pflege aus 1.2.1:** Rund um jede Übertragung von und zur Karte konnte ein gleichzeitiger Schreibzugriff direkt neben dem Puffer verloren gehen. Jetzt so, wie Linux es seit 2014 macht.
- **SD-Karte über USB (seit v7):** Die Puffer teilten Cache-Zeilen mit der Speicherverwaltung. Beim Kopieren konnten so in seltenen Fällen falsche Bytes in die Datei geraten. Jetzt haben sie eigene Cache-Zeilen.

**Geprüft:**
- **Reverb** (`tests/reverb`, Code der Firmware auf dem PC):
  - 47 Prüfungen aus v10 bei voller Modulation, alle bestanden.
  - 14 neue Prüfungen: Ein gehaltener Ton bleibt bei Modulation 0 auf 0,17 dB (Mutable) bzw. 0,36 dB (Digital) genau, bei 50 schwankt er um 5,6 dB bzw. 19,5 dB. Pre-delay verschiebt den Hall Bit für Bit genau, und beim Einschalten kommt kein alter Klang heraus.
- **Song im Emulator:**
  - Mit Modulation 50 klingt der Volllast-Song beim Mutable-Modell Bit für Bit wie v13.
  - Beim Digital-Modell weichen 176 von 705 792 Samples um 1 LSB ab, eine Rundungsfrage.
- **Delay** (`tests/delay`): Tonhöhe, Artefakte, erstes Echo und Echo-Ende auf dem PC. Im Emulator zusätzlich die Wahl Fade/Tape für alte Songs.
- **Song-Wechsel** (`tests/songchange`, Emulator, auf dem fertigen v14-Build): Countdown in rund 5000 Audio-Fenstern, höchstens 15 ms später als die Takt- und Beatgrenzen. Dazu kommen Swing, externe Clock mit 123 BPM, Stopp während des Wartens, die Regler auf dem laufenden Song und der Punkt für Beats, zusammen 131 Prüfungen. Ein Gegenprüfer fand zwei kleine Punkte, beide sind behoben (Punkt für Beats, hängendes Popup).
- **SD-Karte über USB** (`tests/smsysex`): Host-Test mit AddressSanitizer auf einer FAT32-RAM-Disk, alle Prüfungen bestanden.
- **Build:** ohne neue Warnungen. Zwei komplette Neubauten ergeben dieselbe SHA-256 (je 465 neu übersetzte Dateien, `cb17bbb3…8e23e6e5`). Die Patches ergeben mit `git am` auf `release_1_2_1` genau diesen Stand.

## v15: lebendige Drone, CPU-Monitor in einer Zeile, drei Korrekturen aus der Community

v15 enthält v14. **Auf dem Gerät ist nichts davon getestet.** Geprüft ist alles im Emulator mit dem Maschinencode der Firmware, auf dem PC mit dem Code der Drone und von einem unabhängigen Gegenprüfer. Mit L2-Cache gibt es v15 als Testversionen in `l2test/`.

**Drone: lebt wie ein gespieltes Instrument** (für den ganzen Drone, im Menü des Drones und auf den Goldknöpfen):
- **Life 0–50:** Jeder Ton wandert auf eigenen, langsamen Zufallsbahnen, die sich nie wiederholen.
  - Tonhöhe ±6 Cent, Lautstärke ±3 dB («Atem»), Helligkeit und Stereo-Position («Schimmer»), alles wachsend mit Life.
  - Beide Seiten eines schwebenden Tons wandern gemeinsam, der Beat bleibt genau wie eingestellt.
- **Rate 0–50:** wie schnell. 25 ist die Grundeinstellung; je 12,5 Schritte doppelt oder halb so schnell.
- **FM 0–50:** Ein Modulator knapp neben dem Ton (0,3 Hz daneben) färbt ihn, die Tiefe blüht auf und klingt ab.
  - **FM form:** Sine (weich) oder Saw (heller, blecherner). Der Wechsel blendet über.
  - Für hohe Töne wird die Tiefe begrenzt, damit nichts hörbar spiegelt.
- **Neue Klangfarbe Pulse:** ein bandbegrenzter Puls. **Pulse width 5–50 %** (Standard 30), mit Life wandert die Breite.
- **Bedienung:** In der Drone-Ansicht die Mod-Taste **Delay** wählen: oberer Goldknopf Life, unterer Rate. Mod-Taste **ModFX**: oben FM (drücken: Sine oder Saw), unten Pulse width. Popups und LED-Ringe wie bei den anderen Sektionen.
- **Bei Life 0 und FM 0 ohne Pulse klingt der Drone Bit für Bit wie in v14.** Änderungen von Life und FM gleiten, ein Wechsel zu oder von Pulse geht wie bisher durch Stille.
- **Song-Datei:** neue Attribute am Drone. Ältere Songs laden mit Life und FM 0. Ältere Firmware ignoriert die Attribute, ein Pulse-Ton wird dort Rich.
- **Rechenlast pro binauralem Ton:**
  - ohne Leben 0,6 % CPU
  - Life 0,7–1,0 %
  - FM 1,3 %
  - Pulse 1,0 %
  - alles zusammen 2,1 %
  - 16 Töne mit allem: gut 30 %
  - Der Drone wird nicht wie Synth-Stimmen gekürzt. Viele lebendige Töne in einem vollen Song also mit Blick auf den CPU-Monitor.

**CPU-Monitor: eine Zeile, halbe Höhe** (Settings → CPU monitor):
- Oben links nur noch z. B. «CPU 97% 13V QL VC».
  - **V:** klingende Stimmen
  - **QL** (quality lowered): Der Deluge rechnet einfacher, um mitzukommen.
  - **VC** (voices cut): Er schneidet Stimmen ab. VC blinkt und steht noch 2 s über den letzten Schnitt hinaus.
- **QL und VC erscheinen nur, solange es passiert.** Modus **Alerts** zeigt nur sie.

**Drei Korrekturen aus der Community-Firmware:**
- **Section-Start per CC:** Ein Section-Start, der auf einen CC gelernt ist, ging beim Laden des Songs verloren (Noten blieben). Jetzt bleibt er.
- **USB-MIDI vom Computer:** Kam MIDI schneller als einmal pro Millisekunde (dichte Automationen aus der DAW, SysEx, der Kartenzugriff über USB aus v7), gingen Pakete verloren. Jetzt wartet der Computer, bis der Deluge bereit ist.
- **Clock-Ausgänge unter externer MIDI-Clock:** Folgte der Deluge einer externen Clock, stoppten Gate-Clock und MIDI-Clock-Ausgang nach einem kurzen Stoss. Jetzt laufen beide im Takt der eingehenden Clock weiter. Mit der eigenen Clock bleibt alles wie bisher.

**Geprüft:**
- **Drone** (`tests/drone`, Code der Firmware auf dem PC, mit UndefinedBehaviorSanitizer): 116 Prüfungen, alle bestanden.
  - Life und FM aus: Bit für Bit v14.
  - Drift 6 Cent, Atem 3 dB, jede Bahn eigenständig.
  - FM-Seitenbänder, Spiegelung bei hohen Tönen, Pulsbreite.
  - Keine Klicks bei Änderungen, Song-Datei hin und zurück.
- **Gegenprüfer** (Code, Messungen auf PC und im Emulator): vier Befunde, alle behoben.
  - Ein Ton genau an der 18-kHz-Grenze seiner Obertöne (z. B. Rich auf 2250 Hz, Pulse auf 1500 Hz) wechselte beim Wandern die Obertontabelle, etwa zehnmal in 30 s, jedes Mal ein leiser Tick. Jetzt bleibt die Tabelle. Ein neuer Test prüft das: vorher 97–169 von 322 Abschnitten betroffen, jetzt keiner.
  - Ein Nachklang im Gleichspannungsfilter nach einem Moduswechsel mit FM.
  - Der LED-Ring von Rate zeigt die Mitte als Grundeinstellung.
  - Die Anzeige beim Umschalten der FM-Form wird aufgefrischt.
- **Song im Emulator:** Der Volllast-Song klingt Bit für Bit wie v14, mit Mutable und mit Digital.
- **Clock** (`tests/clock`, Emulator): externe MIDI-Clock mit 123 BPM über 4 Takte.
  - v14: 0 Ticks an beiden Ausgängen.
  - v15: MIDI-Clock 96 pro Takt, so viele wie hereinkommen; die Gate-Clock gleichmässig in jedem Takt.
  - Mit der eigenen Clock gleich wie v14.
- **Section-Start** (`tests/sections`, Emulator): Note, CC und MPE-Zone bleiben nach dem Laden erhalten. v14 verlor den CC.
- **USB-MIDI:** wie in der Community-Firmware übernommen, im Code geprüft. Den USB-Controller bildet der Emulator nicht ab.
- **CPU-Monitor** (`tests/cpu_stats`): Zeile mit und ohne QL/VC, Decoder von `tools/cpu_monitor.html` unverändert.
- **Song-Wechsel** (`tests/songchange`): Countdown und Regler wie in v14.
- **Build:** ohne neue Warnungen. Zwei komplette Neubauten ergeben dieselbe SHA-256 (je 466 neu übersetzte Dateien, `cdce07f3…da7ca652`). Die Patches ergeben mit `git am` auf `release_1_2_1` genau diesen Stand.

## v16: Drone-Spuren, Profiler, USB audio bleibt an

v16 enthält v15. **Auf dem Gerät ist nichts davon getestet.** Geprüft ist alles im Emulator mit dem Maschinencode der Firmware, auf dem PC mit dem Code der Drone und von einem unabhängigen Gegenprüfer. Mit L2-Cache gibt es v16 als Testversionen in `l2test/`.

**Drone-Spuren: Drones in Song- und Arranger-View platzieren und überlagern:**
- **Was es ist:** eine Kit-Spur, deren Reihen Drone-Töne sind. Ihre Clips starten in der Song-View und liegen im Arranger wie die jedes Kits. So lassen sich Drones gezielt setzen und überlagern, jede Spur mit den Effekten, der Lautstärke und der Sidechain ihres Kits.
- **Die Noten sind das Gate:** Solange eine Note dauert, klingt der Ton der Reihe. Er blendet weich ein und aus wie beim Song-Drone. Eine Note über den ganzen Clip hält über die Loop-Grenze. Mit dem Arpeggiator der Reihe pulsiert der Ton im Rhythmus.
- **Drone-Spur erstellen:** In der Drone-Ansicht **Shift + Kit**.
  - Das ergibt ein neues Kit (DRONE1, DRONE2, …) mit 16 Drone-Reihen, den Tönen des Song-Drones.
  - Die eingeschalteten Töne bekommen eine Note über den ganzen Clip.
  - Der Clip steht unten in der Song-View, noch nicht gestartet. Gestartet klingt er wie der Drone, gleich laut. Der Song-Drone selbst bleibt, wie er ist.
- **Eine Kit-Reihe zur Drone-Reihe machen:** In der Clip-View eines Kits das Audition-Pad der Reihe halten (oder eine neue Reihe anlegen), dann **Shift + Kit**. Die Reihe klingt dann mit 200 Hz. Kit allein öffnet wie bisher den Sample-Browser.
- **Tonhöhe spielen:** In der Clip-View eine Drone-Reihe wählen, mit ihrem Audition-Pad (Affect Entire aus).
  - **Select drehen:** 1 Hz pro Raste, mit Shift 0,1 Hz.
  - **Oberer Goldknopf:** 1 Hz, schnell gedreht 10 Hz, mit Shift 0,01 Hz. Bei einem Ton als Note: Halbtöne, mit Shift Cent.
  - **Unterer Goldknopf:** Pegel der Reihe.
  - **Select drücken:** das Ton-Menü der Reihe (Modus, Tonhöhe, Beat, Sync, Puls, Klangfarbe, Pegel, Pan, Arpeggiator).
  - Das OLED zeigt Modus und Hz als Namen der Reihe, die 7-Segment-Anzeige die Hz.
  - Das Pad einer Reihe, die gerade aus dem Sequenzer klingt, wählt sie nur aus. Sie klingt weiter.
- **Hz-Wechsel aufnehmen:** **PLAY**, dann **REC**, dann Select oder den oberen Goldknopf drehen.
  - Jeder Wechsel landet als Knoten in der Hz-Spur der Reihe und spielt im nächsten Loop wieder ab. Zwischen den Knoten gleitet der Ton in etwa 15 ms.
  - Das geht auch in den Arranger, wie jede Aufnahme.
  - Ohne REC ändert sich der Grundton der Reihe, die aufgenommene Spur verschiebt sich mit.
  - Unter REC nimmt Shift + Select die feinen 0,1-Hz-Schritte auf. Shift + oberer Goldknopf löscht die Hz-Spur wie jede Automation, die Reihe kehrt dann sofort zu ihrem Grundton zurück.
- **Die Hz-Spur bleibt:** Noten aufnehmen, verschieben oder euklidisch verteilen lassen sie unverändert. Die Noten sind nur das Gate.
- **MIDI:** Pitch-Bend auf einer Drone-Reihe wirkt im Bend-Bereich der Reihe (Standard 2 Halbtöne) und wird unter REC ebenfalls aufgenommen.
- **Rechenzeit:** pro klingender Reihe 0,3–0,45 % CPU, 16 Reihen 5–7 %. Reihen mit geschlossenem Gate kosten nichts.
- **Song-Datei:** neue Reihen `<droneTone …>` mit den Attributen der Drone-Töne. Die Hz-Spur liegt in den Noten-Daten der Reihe.
  - Ältere Firmware überspringt Drone-Reihen, alle anderen Reihen bleiben richtig, denn v16 speichert die Drone-Reihen zuletzt.
  - Ein v16-Song mit Drone-Spuren sollte trotzdem nicht in älterer Firmware gespeichert werden: Dort gehen die Drone-Reihen verloren.

**Profiler** (Settings → CPU monitor → **Profile**): wie in der Messversion `prof/`, siehe dort. Er zeigt am Computer die Rechenzeit pro Spur, Task und Funktion (`tools/profiler.html`). Die Symbol-Datei zu v16 liegt neben der Firmware.

**USB audio bleibt nach dem Neustart an:** Bisher speicherte das Settings-Menü seine Werte erst beim Verlassen. USB audio verlangt beim Einschalten einen Neustart, und wer direkt aus dem Menü neu startete, fand es danach wieder aus. v16 speichert diese Einstellung sofort. Mit älteren Versionen: vor dem Neustart das Menü mit Back verlassen.

**Geprüft:**
- **Drone** (`tests/drone`, PC): 150 Prüfungen. Der Song-Drone klingt Bit für Bit wie in v15.
- **Emulator** (`tests/song`, DRONE=1, 60 Prüfungen):
  - eine Drone-Reihe klingt mit ihren Hz und folgt der Hz-Spur (300,009 Hz)
  - ihr Pegel liegt 0,00 dB neben einem gleich eingestellten Ton des Song-Drones
  - Speichern und Laden ergeben dieselben Samples
  - REC nimmt Hz-Wechsel auf (250, 260, 220 Hz) und spielt sie ab
  - eine Kit-Reihe wird zur Drone-Reihe
  - Speichern während der Wiedergabe: kein Einbruch (vorher bis 25 dB, jetzt 0,003 dB)
  - Noten aufnehmen, verschieben und euklidisch verteilen lassen die Hz-Spur unverändert
  - Shift beim Aufnehmen, MIDI-Pitch-Bend, das Pad während einer Note
- **Song-Wechsel** (`tests/songchange`): 131 Prüfungen. Der Gegenprüfer spielte ihn zusätzlich mit einer laufenden Drone-Spur durch, ohne Absturz.
- **Der Volllast-Song** klingt Bit für Bit wie v15. Songs ohne Drone-Spuren speichern dasselbe XML wie v15.
- **Gegenprüfer:** sieben Befunde, alle behoben, jeder mit einem Test.
  - Der wichtigste war ein Datenverlust: Eine live aufgenommene Note setzte die Hz-Spur ab dieser Stelle zurück.
  - Dazu kommt die Pad-Auswahl während einer Note: Die Reihe verstummte bis zur nächsten Note.
- **USB audio** (`tests/settings`, Emulator): eingeschaltet, ohne das Menü zu verlassen, dann neu gestartet. Die Karte hält den Wert, das Menü zeigt nach dem Neustart an. 2 von 2 Prüfungen, mit v15 0 von 2.
- **Profiler:** wie in `prof/README.md`, mit v16 erneut im Emulator: 21 Prüfungen bestanden.
- **Build:** ohne neue Warnungen. Je zwei komplette Neubauten ergeben dieselbe SHA-256, auch bei den beiden L2-Versionen. Alle Prüfungen oben liefen auf genau diesen Builds, dazu Clock, Sections und L2 wie in v15 (`tests/clock`, `tests/sections`, `tests/l2`). Der Volllast-Song klingt auch mit L2 Bit für Bit wie v15.

## Bedienung

Das Menü liegt unter **Settings → Tuning → Master tune (Hz)**. Die 7-Segment-Anzeige zeigt `TUNE` → `MTUN`.

- **Bereich:** 415.3 bis 466.2 Hz, das ist ±1 Halbton um 440 Hz.
- **Schritte:** 0.1 Hz. Der Cursor steht zuerst auf der 1-Hz-Stelle. Mit dem horizontalen Encoder wechselst du auf 0.1 Hz.
- **Standard:** 440.0 Hz. Bei diesem Wert verhält sich die Firmware exakt wie das originale 1.2.1. Die einzige Ausnahme sind Aufnahmen, die bei einer anderen Stimmung entstanden sind (siehe unten).

## Was der Stimmung folgt

Alles, was klingt, folgt der Stimmung. Die Rechnung ist exakt, die Abweichung liegt unter 0,002 Cent.

| Bereich | Verhalten |
|---|---|
| Synth- und Kit-Spuren: Wellenformen, Wavetables, Samples, Drum-Samples, FM inkl. Modulatoren, DX7 | folgen der Stimmung |
| Audio-Clips | folgen der Stimmung; die Tonhöhe wird per Time-Stretch verschoben, das Tempo bleibt synchron |
| Gehaltene Noten | werden beim Ändern sofort umgestimmt: Synth, Kit, CV und MIDI |
| CV-Ausgänge | folgen der Stimmung und werden sofort neu ausgegeben |
| Externe MIDI-Geräte (MIDI-Spuren und MIDI-Drums in Kits) | erhalten „Channel Fine Tuning“ (RPN 1) auf jedem benutzten Kanal, bei MPE auf den Member-Kanälen, siehe unten |
| Live-Eingang als Oszillator | bleibt unverändert: Er klingt schon in der aktuellen Stimmung, weil das Instrument darauf gestimmt ist |

## Aufnahmen werden nie doppelt gestimmt

- **Markierung beim Aufnehmen:** Eine Aufnahme auf dem Deluge (Audio-Clip, Resampling, Sample-Aufnahme, Stem-Export) bei einer anderen Stimmung als 440 Hz bekommt in der WAV-Datei einen 12-Byte-Block `mtun` mit dieser Stimmung.
- **Abspielen:** Die Aufnahme wird nur um die Differenz zur aktuellen Stimmung verschoben. Eine Aufnahme bei 432 Hz klingt bei 432 Hz also unverändert und wird bei 440 Hz um genau +31,77 Cent angehoben.
- **Grundton-Erkennung:** Die automatische Erkennung rechnet relativ zur Aufnahme-Stimmung. Die Kette Aufnahme → Erkennung → Transposition → Stimmung trifft den Zielton auf 0,001 Cent genau (siehe Test).
- **Kompatibilität:** Andere Software überspringt den Block. Geprüft sind Python `wave`, libsndfile (Basis vieler DAWs und Editoren) und scipy; alle lesen bitgenau dieselben Audiodaten.
- **Ohne Block:** Samples ohne Block, z. B. aus anderen Quellen, gelten als 440-Hz-Material.

## MIDI-Details

Die Stimmung geht als RPN 1 (6 Control-Change-Meldungen) hinaus:

- **Bei Änderung:** sofort an alle Kanäle, die der Song benutzt. Das stimmt auch gehaltene Noten um.
- **Nach dem Play-Start:** noch einmal, für Geräte, die inzwischen eingeschaltet wurden. Das geschieht erst **nach** den ersten Noten und verzögert sie deshalb nicht.
- **Vor der ersten Note** auf einem Kanal, der die aktuelle Stimmung noch nicht hat, z. B. nach dem Laden eines Songs mit neuen Kanälen.
- **Geteilte Kanäle:** Teilen sich mehrere Spuren oder Drums einen Kanal, wird er nur einmal gestimmt.
- **Schutz des DIN-Puffers:** Der DIN-MIDI-Puffer hat keinen Überlaufschutz. Die Firmware sendet deshalb nur, solange darin Platz ist, und schickt den Rest wenige Millisekunden später. Auch schnelles Drehen am Encoder mit vielen MPE-Kanälen erzeugt so keinen MIDI-Müll.
- **Nie verstimmt:** War seit dem Einschalten nie eine andere Stimmung als 440 Hz aktiv, geht nichts Zusätzliches hinaus. Nach einer Rückkehr auf 440 Hz setzt die Firmware die Geräte einmal auf 0 Cent zurück.

## Speicherung

- **Speicherort:** Der Wert steht in `CommunityFeatures.XML` im Hauptverzeichnis der SD-Karte, Eintrag `masterTune`, in Zehntel-Hz, z. B. `4320` für 432.0 Hz. Er wird beim Verlassen des Settings-Menüs gespeichert.
- **Kompatibilität:** Die offizielle Firmware kennt den Eintrag nicht. Sie behält ihn trotzdem und schreibt ihn unverändert zurück.
- **Übernahme aus der Fork-Version:** Die Fork-Version (`c1.2.0`) speicherte den Wert in den Flash-Bytes 198–199. Die neue Version übernimmt ihn beim ersten Start. Sobald er in der Datei steht, setzt die Firmware die Flash-Bytes beim nächsten Speichern wieder auf 0, wie die offizielle Firmware. Konflikte mit künftigen Firmware-Versionen entfallen damit.

## Installation

1. Kopiere die gewünschte `.bin`-Datei ins Hauptverzeichnis der SD-Karte. Lass dort keine andere `.bin`-Datei liegen.
2. Halte wie gewohnt beim Einschalten **SHIFT** gedrückt. Der Deluge installiert dann die Firmware.
3. Kontrolliere danach unter Settings → Firmware version die Versionsbezeichnung aus der Tabelle oben.

## Grenzen

- **Nicht auf dem Gerät getestet.** Geprüft sind der Build (reproduzierbar, zwei Builds mit identischer SHA-256), die Rechnung und das WAV-Format (Tests unten).
- **CPU:** Abseits von 440 Hz braucht die Wiedergabe etwas mehr Rechenzeit, genau wie eine Transposition um Bruchteile eines Halbtons.
  - Samples, die bei 440 Hz nativ laufen, werden interpoliert.
  - Audio-Clips laufen dauernd über den Time-Stretcher. Das ist der grösste Posten, spürbar erst bei vielen Audio-Clips gleichzeitig.
  - Aufnahmen bei der aktuellen Stimmung laufen dagegen nativ.
- **Klangqualität:** Die Tonverschiebung nutzt dieselbe Interpolation und denselben Time-Stretcher wie beim Transponieren. Bei Samples im Modus „Pitch/Speed unabhängig“ und bei Audio-Clips gelten deshalb die üblichen, sehr leisen Time-Stretch-Artefakte.
- **MIDI:** Das Gerät muss RPN 1 (Channel Fine Tuning) auswerten. Das tun viele Synths und DAWs, aber nicht alle. RPN 1 reicht von −100 bis +99,99 Cent. Nur an den beiden Extremwerten 415.3 Hz (−100,02 Cent) und 466.2 Hz (+100,13 Cent) bleibt eine Rest-Abweichung von höchstens 0,15 Cent.
- **Später eingeschaltete MIDI-Geräte:** Sie bekommen die Stimmung einige Millisekunden nach dem nächsten Play-Start. Ein Gerät, das die Stimmung nur beim Anschlag übernimmt, spielt die allerersten Noten dieses einen Durchlaufs noch in 440 Hz.
- **Fremde Aufnahmen:** Aufnahmen der Fork-Version oder in einem Editor gespeicherte Kopien haben keinen `mtun`-Block (manche Editoren entfernen unbekannte Blöcke). Sie gelten als 440-Hz-Material.
- **Barock-Stimmung:** A = 415.0 Hz liegt knapp ausserhalb des Bereichs. Möglich sind 415.3 Hz, also genau ein Halbton unter 440.

## Versionen

| | Fork `c1.2.0` | Version 1 (`19514d07`) | Version 2 (`49e71650`) |
|---|---|---|---|
| Synth-Spuren, CV | ja | ja | ja |
| Kit-Spuren und Drum-Samples | ja | nein | ja |
| Audio-Clips und Aufnahmen | nein | nein | ja, ohne Doppelstimmung |
| Samples mit automatischer Grundton-Erkennung | Stimmung doppelt, z. B. 32 Cent zu tief bei 432 Hz | korrekt | korrekt, auch für eigene Aufnahmen |
| Live-Eingang als Oszillator | verstimmt | verstimmt | unverändert (korrekt) |
| Gehaltene Noten | erst ab dem nächsten Anschlag | sofort | sofort, auch in Kits |
| Externe MIDI-Geräte | nicht gestimmt | RPN 1, vor der ersten Note nach Play-Start (bis 6 ms Verzögerung pro Kanal über DIN) | RPN 1, ohne Verzögerung beim Play-Start, DIN-Puffer geschützt, MIDI-Drums inklusive |
| Speicherort | Flash-Bytes 198–199 | SD-Datei | SD-Datei |

Version 1 liegt weiterhin in der Git-Historie dieses Ordners.

## Selbst bauen und testen

```sh
git clone https://github.com/SynthstromAudible/DelugeFirmware && cd DelugeFirmware
git checkout release_1_2_1
git am /pfad/zu/patches/*.patch        # alle = v12; nur 0001 = v2, 0001-0003 = v3, 0001-0004 = v4, 0001-0005 = v5, 0001-0006 = v6, 0001-0007 = v7, 0001-0008 = v8, 0001-0009 = v9, 0001-0010 = v10, 0001-0011 = v11
./dbt configure -DRELEASE_TYPE:STRING=mastertune-v12   # Name in der Versionsanzeige, z. B. mastertune-v10 für v10
./dbt build release                      # Ergebnis: build/Release/deluge.bin

# Rechentest (Host-Compiler)
g++ -std=c++20 -O2 -Isrc/deluge /pfad/zu/tests/master_tune_math_test.cpp -o mt_test && ./mt_test

# WAV-Test (pip install soundfile scipy)
python3 /pfad/zu/tests/wav_mtun_chunk_test.py

# NEON-Pufferverschiebung auf Cortex-A9-Code im Emulator (pip install unicorn)
python3 /pfad/zu/tests/run_neon_shift_test.py .

# SD-Zugriff über USB (v7) auf dem PC, mit AddressSanitizer
/pfad/zu/tests/smsysex/run.sh .

# USB-Audio (v8): Deskriptoren und Puffer-Simulation auf dem PC
/pfad/zu/tests/usbaudio/run.sh .

# Lade-Warteschlange (v9) auf dem PC (braucht g++-multilib)
/pfad/zu/tests/streaming/run.sh .

# Reverb (v10): alle Modelle auf dem PC gemessen, mit UndefinedBehaviorSanitizer
/pfad/zu/tests/reverb/run.sh .

# Delay (v11): Wiederholungen, Zeitänderungen und Filter auf dem PC gemessen, mit UndefinedBehaviorSanitizer
/pfad/zu/tests/delay/run.sh .

# Drone (v12): Töne, Beats und Übergänge auf dem PC gemessen, mit UndefinedBehaviorSanitizer
/pfad/zu/tests/drone/run.sh .
```

## Tests im Emulator (Cortex-A9)

Alle Tests laufen zusätzlich zum PC auch auf dem Maschinencode des Deluge-Prozessors, einem Cortex-A9, in einem Emulator (unicorn 2.1.4, die neuste Version).

- **Gleicher Code wie auf dem Deluge:** Gebaut wird mit der Toolchain der Firmware und ihren Code-Flags: Thumb-2, NEON mit Hard-Float, `-O2` und `-funsafe-math-optimizations`. Nur die Link-Zeit-Optimierung über Dateien hinweg fehlt.
- **Wozu:** So fallen Unterschiede zwischen PC und Deluge auf, bevor die Firmware aufs Gerät kommt:
  - 32 Bit statt 64 Bit
  - NEON-Gleitkomma ohne Denormals
  - Umformungen des Compilers durch die Fast-Math-Flags

  Genau so fand der Emulator den Damping-Fehler aus v10.
- **Ablauf:** Der Test läuft als normales Programm mit Ausgabe, Heap, Dateien und Exit-Code (Semihosting der newlib). Speicherfehler und ungültige Befehle meldet der Emulator mit der Stelle im Quellcode.
- **Rechenzeit:** Der Emulator zählt die ausgeführten Befehle der DSP-Teile pro Block von 128 Samples (2,9 ms), `run_all.sh` gibt sie am Ende aus. Stand v12:

  | Teil | Befehle pro Block | ≈ CPU | 1.2.1 (v10) |
  |---|---|---|---|
  | Reverb Freeverb | 70 200 | 6,0 % | |
  | Reverb Mutable | 39 100 | 3,4 % | |
  | Reverb Digital | 53 600 | 4,6 % | |
  | Delay, Zeit ruhig | 9 200 | 0,8 % | 0,7 % |
  | Delay, Zeit moduliert | 40 100 | 3,5 % | 5,0 %: meist liefen zwei Puffer |
  | Delay, moduliert mit LPF/HPF | 44 400 | 3,8 % | |
  | Delay, moduliert, Analog-Modus mit LPF/HPF | 97 000 | 8,4 % | 9,1 % ohne Filter |
  | Drone, 1 Sinuston | 4 100 | 0,35 % | |
  | Drone, 1 binauraler Ton | 6 300 | 0,54 % | |
  | Drone, 16 binaurale Töne mit Obertönen | 79 500 | 6,9 % | |

  Die Prozentangaben rechnen mit 1 Befehl pro Takt bei 400 MHz. Das ist eine grobe Schätzung, denn Caches, Pipeline und Doppel-Ausführung des A9 bildet der Emulator nicht nach. Für Vergleiche zwischen Versionen taugt sie gut.
- **Was nicht emuliert wird:** Die Hardware (SD-Karte, USB, Display) ersetzen die Tests wie auf dem PC durch eigene Nachbildungen. Der WAV-Test ist reines Python und läuft nur auf dem PC.

```sh
# Alle Tests auf PC und Emulator (braucht python3 mit unicorn: pip install unicorn)
/pfad/zu/tests/run_all.sh .            # oder: ... . pc / ... . arm
# Ein einzelner Test im Emulator
ARM=1 /pfad/zu/tests/delay/run.sh .
```
