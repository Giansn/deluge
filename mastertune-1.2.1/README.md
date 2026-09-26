# Deluge 1.2.1 mit Master Tune

Das ist die offizielle Community-Firmware **1.2.1** (Tag `release_1_2_1`, Commit `c23bc2fe`) mit einstellbarer Grundstimmung, in vier Stufen. v5 bringt zusätzlich den Arpeggiator aus 1.3.

| Datei | Version (Settings → Firmware version) | Inhalt |
|---|---|---|
| `deluge-1.2.1-mastertune-49e71650.bin` | `1.2.1-mastertune-49e71650` (v2) | Master Tune |
| `deluge-1.2.1-mastertune-v3-09dcce01.bin` | `1.2.1-mastertune-v3-09dcce01` | v2 + Leistungspaket A + Sidechain-Fix |
| `deluge-1.2.1-mastertune-v4-6cb344e2.bin` | `1.2.1-mastertune-v4-6cb344e2` | v3 + Leistungspaket B |
| `deluge-1.2.1-mastertune-v5-5daddd9f.bin` | `1.2.1-mastertune-v5-5daddd9f` | v4 + Arpeggiator aus 1.3, Latch, Ratchet Bounce |

SHA-256: v2 `05b457a0…d2ce7e0cb`, v3 `7858013f…35d4d73`, v4 `cc9127a0…61128209`, v5 `65607a5d…c745251b` (vollständig: `sha256sum *.bin`).
Quellcode: `patches/0001` bis `0005` gegen `release_1_2_1`. v2 = 0001, v3 = 0001–0003, v4 = 0001–0004, v5 = 0001–0005.

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
git am /pfad/zu/patches/*.patch        # alle = v5; nur 0001 = v2, 0001-0003 = v3, 0001-0004 = v4
./dbt configure -DRELEASE_TYPE:STRING=mastertune-v5   # Name in der Versionsanzeige, z. B. mastertune-v4 für v4
./dbt build release                      # Ergebnis: build/Release/deluge.bin

# Rechentest (Host-Compiler)
g++ -std=c++20 -O2 -Isrc/deluge /pfad/zu/tests/master_tune_math_test.cpp -o mt_test && ./mt_test

# WAV-Test (pip install soundfile scipy)
python3 /pfad/zu/tests/wav_mtun_chunk_test.py

# NEON-Pufferverschiebung auf Cortex-A9-Code im Emulator (pip install unicorn)
python3 /pfad/zu/tests/run_neon_shift_test.py .
```
