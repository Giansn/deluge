# Lokale Session: Gerätetests und Live-Ansicht

Diese Anleitung ist für die Claude-Session auf dem Rechner, an dem der Deluge per USB hängt.

**Rollen:**
- **Die Cloud-Session entwickelt:** Firmware-Code, Builds, Emulator-Tests, Releases. Sie bleibt dafür allein zuständig.
- **Die lokale Session macht nur zwei Dinge:** Tests am Gerät und die Live-Ansicht. Sie ändert keinen Firmware-Code, baut keine Firmware und spielt nur `.bin`-Dateien aus diesem Repo auf.

## Einrichten

```sh
git clone https://github.com/giansn/deluge && cd deluge
git checkout claude/wizardly-brahmagupta-nnrk07      # hier liegt der aktuelle Stand
pip install mido python-rtmidi
python3 mastertune-1.2.1/tools/deluge_profiler.py ports   # der Deluge erscheint mit drei Ports, der dritte ist der Monitor-Port
```

Vor jeder Arbeit `git pull`, denn die Cloud-Session liefert laufend nach.

**Windows:**
- **Python 3.12 nehmen.** `python-rtmidi` hat für neuere Versionen keine fertigen Pakete.
- **Port-Namen:** Die Ports heissen `Deluge 0`, `MIDIIN2 (Deluge) 1` und `MIDIIN3 (Deluge) 2`. Ab Commit nach `0377a67` erkennt das Skript `MIDIIN3` von selbst, `-p` ist dann nicht mehr nötig.

## Aufträge (Stand 28.09.2026, v18.3 ist da)

Erledigt: der Bericht zu v16, l2d, der Song für den Emulator, der Retune-Test unter Windows, «Rescue». Die Aufträge zu v17 entfallen: v18.3 enthält v17, v18 und v18.2, alles gilt jetzt für v18.3. Was es bringt, steht im README in den Abschnitten «v18», «v18.2» und «v18.3».

1. **v18.3 aufspielen:** `deluge-1.2.1-mastertune-v18.3-l2d-3581f019.bin`, die Hauptdatei mit L2 für Code und Daten (v18.3 gibt es nur so). Settings → Firmware version zeigt `1.2.1-mastertune-` / `v18.3-l2d`. Bei einem Absturz: `deluge-1.2.1-mastertune-v18-124aeaa2.bin`, v18 ohne L2.
2. **Die wichtigste Messung: «New Sitar Grii 10» mit v18**, wie der v16-Bericht. 432 Hz, CPU monitor auf Profile, 30 s Stillstand, dann 40 s dieselbe Stelle mit denselben Clips. `live -s 70 -o 2026-..-v18.2-l2d-grosser-song.jsonl --symbols deluge-1.2.1-mastertune-v18.3-l2d-3581f019.symbols.json`.
   - Der Emulator erwartet etwa die Last von v17 (41 statt 93 % Anzeige bei v16). Die neuen Filter kosten etwas mehr: Der Testsong braucht 91,4 statt 89,3 %.
   - Notieren: QL, abgeschnittene Stimmen, die längste Lücke, und ob Oboe und Sitar zu hören sind.
3. **Filter und Lautstärke am Gehör**, jeweils kurz, am besten mit Kopfhörer:
   - **Rascheln:** Synth mit LPF 24 dB, Cutoff tief (etwa 10–20), Resonanz mittel, ein langer Ton. Das Rauschband um den Ton muss weg sein. Dasselbe mit 12 dB und Drive.
   - **Brummen:** In «New Sitar Grii 10» das Song-LPF ganz zu. Das Brummen bei 32 Hz muss weg sein.
   - **Übergänge:** Cutoff und Resonanz schnell drehen, Filtermodus und Routing wechseln, während ein Ton klingt. Keine Stufen, keine Klicks.
   - **Parallel-Routing:** Ein Song mit Routing «Parallel» ist 6 dB leiser als mit v17. Stört das?
   - **Drive:** LPF-Modus Drive mit viel Resonanz. Der Bass bleibt, statt dünn zu werden. Neu in v18.3: Ein Song mit mehreren Drive-Filtern verliert am Taktanfang keine Stimmen mehr, und hoch oben mit Resonanz klingt Drive weiter sauber ohne Spiegeltöne.
   - **EQ:** Bass und Treble drehen, während Musik läuft. Keine Klicks.
   - **Lautstärke:** Jede Raste ändert 0,5 dB, die Anzeige zeigt dB (`-3.5`), ganz unten `OFF`. Leise eingestellte Spuren klingen sauber.
   - **Sidechain:** Ein alter Song mit hartem Pumpen setzt jetzt über mindestens 5 ms ein, etwas weicher. Stört das?
   - **Output limiter** (Settings → Community features, `LIMT`): an einem Song, der übersteuert. Kein hartes Verzerren mehr. Beim Umschalten kann es einmal knacken.
   - **Filter crossing guard** (Settings → Community features, `CROS`): HPF und LPF mit Resonanz aufeinander zu drehen. Kein schmerzhafter Pegelsprung mehr.
   - **Nach einer Stille (neu in v18.2):** ein Kit eine Weile nicht spielen lassen (Pause im Clip oder stummschalten), währenddessen sein LPF, EQ oder seine Lautstärke verstellen, dann wieder spielen. Der erste Schlag setzt ohne Ticken und ohne hellen Anschlag ein, gleich mit dem neuen Klang.
4. **OLED und Song-Übersicht:**
   - **Helligkeit:** Settings → OLED brightness, Stufen 1–10. Jeder Dreh wirkt sofort, die Stufe gilt nach dem Neustart weiter. Ist Stufe 1 noch lesbar?
   - **Gruppen:** «New Sitar Grii», «New Sitar Grii 2» und «… 10» erscheinen als eine Gruppe. «TR-808» oder «Jam 2026-09-27» bleiben eigene Songs.
5. **DelugeRec mit Songnamen** (die neuste Version im Release https://github.com/Giansn/deluge/releases/tag/deluge-rec, ab v7):
   - USB audio an, einen Song laden, aufnehmen. Die Datei muss «Songname, Datum - 1.2.1 v18.2.WAV» heissen (ab DelugeRec v7; v6 kürzte auf v18).
   - Einen anderen Song laden und wieder aufnehmen. Der neue Name muss erscheinen.
   - Das kann nur das Gerät prüfen, im Emulator läuft kein USB.
6. **Die Bibliothek `deluge topics` auf 432 Hz umwandeln, neu mit der Version, die den Laptop nicht mehr einfriert** (`tools/retune_library.py`, Stand ab Commit `868d85f`, also zuerst `git pull`):
   - **Was neu ist:**
     - Jede Datei wird in Stücken von etwa 3 s umgerechnet. Pro Datei braucht es so etwa 60 MB Speicher statt bis zu 8,5 GB, auch bei langen Aufnahmen.
     - Wie viele Dateien gleichzeitig laufen, richtet sich nach dem freien Speicher: höchstens die Hälfte davon. Die Konsole zeigt, was gewählt wurde.
     - Das Ergebnis ist Byte für Byte gleich wie mit der alten Version.
   - **Am einfachsten unter Windows: DelugeTuner** (`DelugeTuner-vN.exe` im Release https://github.com/Giansn/deluge/releases/tag/deluge-tuner, nichts zu installieren). KARTE: die Kopie der Karte, ZIEL: ein Ordner auf dem Computer, den Goldknopf auf 432.0 Hz, zuerst LESEN (zeigt nur, was es täte), dann UMSTIMMEN. ESC hält an, START mit denselben Angaben macht weiter. BERICHT öffnet `RETUNE_REPORT.txt`. Die Befehlszeile unten geht weiterhin.
   - **Neu beginnen:** Der eingefrorene Ordner stammt von der alten Version und lässt sich nicht fortsetzen. Ihn löschen oder umbenennen, dann in einen neuen, leeren Ordner umwandeln:
     `python tools/retune_library.py --card <Kopie der Karte> --out <neuer leerer Ordner>`
   - **Bricht es ab** (Absturz, Strom, volle Platte): denselben Befehl mit `--resume` und denselben Ordnern. Fertige Dateien bleiben, halbe werden neu gemacht.
   - Dateien, deren Spitzen über 0 dBFS gingen, schreibt es wie gewünscht als 32-Bit-Float. Der Deluge begrenzt sie beim Abspielen trotzdem auf 0 dBFS.
   - Danach auf eine zweite Karte kopieren, `RETUNE_REPORT.txt` mit pushen und Auftrag 2 mit der umgewandelten Karte wiederholen.

### Erledigt: «Rescue», das LPF wirkt nicht (27.09.2026)

- **Befund** (Emulator mit der echten XML, gegengeprüft): Das LPF wirkt. Die Resonanz des Song-LPF steht aber auf Maximum, darum schwingt der Filter ab Knopf etwa 36 selbst. Sein Ton wandert mit dem Knopf nach unten, liegt rund 10 dB über der Musik und übersteuert. Unten bleibt ein Ton unter 60 Hz. v16 verhält sich gleich.
- **Die Automation** (ein Knoten «ganz offen») ist nicht die Ursache. In der Song-Ansicht wirkt sie nur beim Aufnehmen ins Arrangement.
- **Entscheid des Nutzers:** Die Firmware begrenzt die Resonanz nicht, Selbstschwingung bleibt möglich wie im Original. v18 macht sie 6 dB leiser und ohne das Brummen unten.
- **Am Gerät:** in der Song-Ansicht mit der Filter-Taste auf LPF, den unteren Goldknopf (Resonanz) unter etwa 35, dann speichern. Wer die Automation auch loswerden will: SHIFT halten, dann den oberen Goldknopf drücken («Automation deleted»). Ohne SHIFT wechselt der Druck den Filtertyp.

## Stand (28.09.2026)

| Datei | Was |
|---|---|
| `deluge-1.2.1-mastertune-v18.3-l2d-3581f019.bin` | **aktuelle Version**, mit L2-Cache für Code und Daten. Im README die Abschnitte «v18.3», «v18.2» und «v18». |
| `deluge-1.2.1-mastertune-v18.2-l2d-c9c65066.bin` | v18.2, Drive wie v18 |
| `deluge-1.2.1-mastertune-v18-l2d-6fa0875b.bin` | v18, mit dem Fehler nach Stille |
| `deluge-1.2.1-mastertune-v18-124aeaa2.bin` | v18 ohne L2, zum Zurückwechseln |
| `deluge-1.2.1-mastertune-v17-l2d-b3385d83.bin` | v17, die Version davor |
| `l2test/deluge-1.2.1-mastertune-v17-l2i-e476310e.bin` | v17 mit L2 nur für Code |
| `*.symbols.json` neben jeder `.bin` | Funktionsnamen für den Profiler, nur zu genau dieser `.bin` passend |
| `hotfix/…v16-l2d-dronefix…` | abgelöst durch v17 |

**Aufspielen:** die `.bin` ins Hauptverzeichnis der SD-Karte, keine andere `.bin` daneben. Beim Einschalten **SHIFT** halten. Danach unter Settings → Firmware version den Namen prüfen.

## Live-Ansicht

Am Deluge: **Settings → CPU monitor → On**. Mit **Profile** kommen zusätzlich Spuren, Tasks und Funktionen.

```sh
cd mastertune-1.2.1
python3 tools/deluge_profiler.py live                          # eine Zeile pro Sekunde, Ctrl-C beendet
python3 tools/deluge_profiler.py live -s 60 -o messung.jsonl \
    --symbols deluge-1.2.1-mastertune-v17-l2d-b3385d83.symbols.json  # 60 s, mit Profile auch die teuersten Funktionen
python3 tools/deluge_profiler.py report messung.jsonl --symbols deluge-1.2.1-mastertune-v17-l2d-b3385d83.symbols.json
```

Für Claude: `live` mit `-s` und `-o` im Hintergrund laufen lassen und die Ausgabe lesen, oder danach `report`.

**Die Zeile:** `CPU 43.0 % (peak 71.0)  voices  24 (max  30)  QL 0 (0.0 % of the time)  cut 0  gap 3.1 ms  SD 11 loads, avg 1.7 max 9.0 ms`
- **CPU:** Auslastung der Audio-Fenster, Mittel und Spitze der letzten Sekunde.
  - **Wichtig bis v16:** Im Leerlauf zeigt sie 85–90 %. Das ist Beschäftigung, keine Überlast (siehe `prof/README.md`). Ab v17 zeigt sie die echte Last.
- **voices:** klingende Stimmen, jetzt und Höchstwert.
- **QL:** Qualität gesenkt (Direness 0–14) und wie viel der Zeit.
- **cut:** abgeschnittene Stimmen (Culling). Die Zahl, die zählt.
- **gap:** längste Pause zwischen zwei Durchgängen der Audio-Routine. Ab etwa 2,9 ms droht ein Aussetzer.
- **SD:** Ladevorgänge von der Karte, mittlere und längste Dauer.
- **Mit Profile** alle 5 s: der Anteil der Audio-Routine, die teuersten Spuren (auf dem Gerät gemessen), Tasks und mit `--symbols` Funktionen.

## Tests am Gerät (v16, zum Teil erledigt; für v17 siehe Aufträge)

Jeden Punkt mit ok oder nicht ok und einer kurzen Beobachtung notieren.

1. **Version:** Settings → Firmware version zeigt `1.2.1-mastertune-v16-c610417f`.
2. **USB audio bleibt an:**
   - Settings → Community features → USB audio → an.
   - Das Menü **nicht** verlassen und den Deluge ausschalten.
   - Nach dem Einschalten muss der Haken noch gesetzt sein, und der Computer sieht den Deluge als Audiogerät.
3. **CPU monitor:** On, Alerts und Profile wechseln. Kein Absturz, `live` zeigt Zeilen. Mit Profile kommt alle 5 s die Zusammenfassung.
4. **Drone-Spuren** (Handbuch `docs/Drone-Handbuch.pdf`, Kapitel 16):
   - In der Drone-Ansicht **Shift + Kit**: Ein Kit DRONE1 erscheint, sein Clip steht unten in der Song-View.
   - Clip starten: Er klingt wie der Drone, gleich laut.
   - In der Clip-View eine Drone-Reihe wählen und Select drehen: Die Hz ändern sich, das OLED zeigt sie.
   - **PLAY**, **REC**, Select drehen: Die Hz-Wechsel werden im nächsten Loop wieder abgespielt.
   - Song speichern, neu laden: Alles klingt gleich.
5. **Messung mit dem grossen Song** (Grundlage für den Vergleich mit v17):
   - `live -s 70 -o v16-grosser-song.jsonl`.
   - Den Song laden, 30 s nichts spielen, dann 30 s spielen, am besten die Stelle, an der QL oder VC erscheint.
6. **L2-Versionen:** Punkt 5 mit l2i und mit l2d wiederholen. Einen Absturz mit Version, Handlung und Anzeige notieren: eingefroren, Neustart oder eine Meldung wie «E…».

**Stürzt der Deluge ab oder verhält er sich seltsam:** zurück zu v16 (oder v15 im Hauptordner). Version, Handlung und Anzeige genau notieren.

## Ergebnisse an die Cloud-Session

- **Wohin:** in `mastertune-1.2.1/geraet/`, der Name nach dem Muster `JJJJ-MM-TT-<version>-<thema>`.
  - Ein kurzer Bericht als `.md`: die Punkte oben mit ok oder nicht ok und den Beobachtungen.
  - Die Messungen als `.jsonl`.
- **Pushen:** auf einen eigenen Branch `geraet-ergebnisse`, nie auf den Entwicklungs-Branch. So kommen sich die beiden Sessions nicht in die Quere.
- **Melden:** Der Nutzer sagt der Cloud-Session Bescheid, dann holt sie die Ergebnisse.
