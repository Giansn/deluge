# Deluge 1.2.1 mit Master Tune

Das ist die offizielle Community-Firmware **1.2.1** (Tag `release_1_2_1`, Commit `c23bc2fe`) mit einstellbarer Grundstimmung, in mehreren Stufen. v5 bringt zusätzlich den Arpeggiator aus 1.3, v6 eine zweite Bounce-Version, v7 den Zugriff auf die SD-Karte über USB, v8 den Deluge als USB-Audio-Eingang am Computer, v9 klügeres Sample-Streaming und einen RAM-Sparer für Kits.

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

SHA-256: v2 `05b457a0…d2ce7e0cb`, v3 `7858013f…35d4d73`, v4 `cc9127a0…61128209`, v5 `65607a5d…c745251b`, v6 `83974fd2…617ef692`, v7 `48c55bae…f9581a21`, v8 `6cfda14b…99441298`, v9 `032898cf…7c673745` (vollständig: `sha256sum *.bin`).
Quellcode: `patches/0001` bis `0009` gegen `release_1_2_1`. v2 = 0001, v3 = 0001–0003, v4 = 0001–0004, v5 = 0001–0005, v6 = 0001–0006, v7 = 0001–0007, v8 = 0001–0008, v9 = 0001–0009.

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

**Einschalten:** Settings → Community features → **USB audio** (7-Segment: `UAUD`) auf an und den Deluge neu starten. Die Einstellung wirkt nur beim Start und nur, wenn der Deluge als USB-Gerät am Computer hängt, nicht als USB-Host. Ist sie aus (Grundeinstellung), verhält sich der Deluge exakt wie v7.

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
git am /pfad/zu/patches/*.patch        # alle = v9; nur 0001 = v2, 0001-0003 = v3, 0001-0004 = v4, 0001-0005 = v5, 0001-0006 = v6, 0001-0007 = v7, 0001-0008 = v8
./dbt configure -DRELEASE_TYPE:STRING=mastertune-v9   # Name in der Versionsanzeige, z. B. mastertune-v8 für v8
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
```
