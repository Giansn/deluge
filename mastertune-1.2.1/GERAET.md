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

## Aufträge (Stand 27.09.2026, nach dem ersten Bericht)

1. **L2-Versionen mit demselben Song** (Punkt 6):
   - «New Sitar Grii 10» mit `l2test/…v16-l2i-0186f612.bin`, dann mit `…v16-l2d-03ccaac5.bin`.
   - Genau wie im ersten Bericht: CPU monitor auf Profile, 30 s Stillstand, dann etwa 40 s dieselbe Stelle spielen.
   - Mit der passenden `.symbols.json` aus `l2test/`.
   - Dazu notieren, ob Knackser oder Aussetzer zu hören sind. Lücken über 2,9 ms können welche geben.
2. **Den Song für den Emulator:**
   - Auf dem Gerät sieht der Profiler nicht in eine Spur hinein. Die Interrupts sind gesperrt, während sie rechnet. Der Emulator kann das.
   - Bitte `SONGS/New Sitar Grii 10.XML` pushen. Instrumente und Kits stecken in der Song-Datei.
   - Dazu die Samples, die er benutzt, mit ihren Pfaden ab dem Karten-Hauptverzeichnis, nach `geraet/karte/…`.
   - Über 100 MB zusammen: nur die XML und eine Liste der Samples mit Grösse und Länge.
3. **Die übrigen v16-Punkte**, wenn es passt: 1 (Versionsanzeige), 2 (USB audio), 4 (Drone-Spuren).

## Stand (27.09.2026)

| Datei | Was |
|---|---|
| `deluge-1.2.1-mastertune-v16-c610417f.bin` | **aktuelle Version:** Drone-Spuren, Profiler, USB audio bleibt nach dem Neustart an. Im README der Abschnitt «v16». |
| `l2test/deluge-1.2.1-mastertune-v16-l2i-0186f612.bin` | v16 mit L2-Cache nur für Code (Risiko gering) |
| `l2test/deluge-1.2.1-mastertune-v16-l2d-03ccaac5.bin` | v16 mit L2-Cache für Code und Daten (Risiko mittel) |
| `*.symbols.json` neben jeder `.bin` | Funktionsnamen für den Profiler, nur zu genau dieser `.bin` passend |

**In Arbeit (Cloud):**
- **v17 (Leistung):** Die Audio-Routine läuft nicht mehr alle 12 µs, und beim Streamen von der Karte werden keine Stimmen mehr unnötig abgeschnitten. Im Emulator sinkt die Anzeige im Leerlauf von 90 auf 16 %.
- **Song-Browser:** Versionen `TRACK`, `TRACK 2`, `TRACK 3` klappen unter `TRACK` auf.

**Aufspielen:** die `.bin` ins Hauptverzeichnis der SD-Karte, keine andere `.bin` daneben. Beim Einschalten **SHIFT** halten. Danach unter Settings → Firmware version den Namen prüfen.

## Live-Ansicht

Am Deluge: **Settings → CPU monitor → On**. Mit **Profile** kommen zusätzlich Spuren, Tasks und Funktionen.

```sh
cd mastertune-1.2.1
python3 tools/deluge_profiler.py live                          # eine Zeile pro Sekunde, Ctrl-C beendet
python3 tools/deluge_profiler.py live -s 60 -o messung.jsonl \
    --symbols deluge-1.2.1-mastertune-v16-c610417f.symbols.json  # 60 s, mit Profile auch die teuersten Funktionen
python3 tools/deluge_profiler.py report messung.jsonl --symbols deluge-1.2.1-mastertune-v16-c610417f.symbols.json
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

## Tests am Gerät (v16, auf dem Gerät noch nie geprüft)

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
