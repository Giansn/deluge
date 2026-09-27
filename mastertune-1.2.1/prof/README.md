# Profiler-Messversion (v15-prof)

**v15 mit einem Profiler**, sonst unverändert. Sie zeigt am Gerät, wofür der Deluge seine Rechenzeit braucht: pro Spur, pro Task und pro Funktion, mit den echten Caches, die der Emulator nicht kennt. Gedacht für die Fragen «87 % CPU, obwohl nichts spielt» und «warum werden Stimmen abgeschnitten».

| Datei | Was |
|---|---|
| `deluge-1.2.1-mastertune-v15-prof-d8ff0108.bin` | v15 + Profiler |
| `deluge-1.2.1-mastertune-v15-l2i-prof-76fd3515.bin` | v15 mit L2-Cache für Code (wie `l2test/`) + Profiler |
| `*.symbols.json` | die Funktionsnamen genau dieser Firmware, je eine pro Datei |

SHA-256: v15-prof `76e608b3…5b3ef1e2`, v15-l2i-prof `65141b9e…4c854404`. Quellcode: v15 plus `prof/patches/0001-…` und `0002-…`, für l2i dazu die Patches aus `l2test/`.

## Messen

1. Eine der beiden Firmware-Dateien aufspielen, wie jedes Update.
2. Den Deluge per USB mit dem Computer verbinden.
3. `tools/profiler.html` in **Chrome oder Edge** öffnen (nichts zu installieren), **Verbinden** klicken, dann **Symbole laden** und die `.symbols.json` wählen, die zur aufgespielten Firmware gehört.
4. Am Deluge: **Settings → CPU monitor → Profile**. Oben links steht die gewohnte Zeile, dazu schickt er die Messwerte an den Computer.
5. Den grossen Song laden.
   - Etwa 30 s **nichts spielen**.
   - Dann etwa 30 s **spielen**, am besten die Stelle, an der QL oder VC erscheint.
   - Mit **Zurücksetzen** zwischen den beiden Teilen bekommst du getrennte Zahlen.
6. Die Tabellen lesen, oder **Aufnahme speichern** und mir die `.jsonl`-Datei schicken, dann werte ich sie aus.

Mit Claude Code auf dem Computer, an dem der Deluge hängt, geht es auch ohne Browser:

```sh
pip install mido python-rtmidi
python3 tools/deluge_profiler.py record -s 60 -o profil.jsonl
python3 tools/deluge_profiler.py report profil.jsonl --symbols prof/deluge-1.2.1-mastertune-v15-prof-d8ff0108.symbols.json
```

## Was die Zahlen zeigen

- **Nach Spur:** die Rechenzeit jeder Spur, auf dem Gerät genau gemessen (Zeitgeber um jede Spur), als Anteil der Zeit. So sieht man, welche Spur wie viel kostet, auch wenn sie still ist.
- **Nach Task:** Audio-Routine, Laden von der Karte, Anzeige, Scheduler usw.
- **Nach Funktion:** 1000-mal pro Sekunde schaut der Deluge nach, wo im Code er gerade steht.
  - Während eine Spur rechnet, sind die Interrupts gesperrt. Diese Zeit erscheint gesammelt als `Song::renderAudio`, welche Spur es war, steht unter «Nach Spur».
  - Welche Funktionen innerhalb einer Spur arbeiten, zeigt das Profil im Emulator (`tests/song`, `tests/sdload`).
- **Audio-Routine:** ihr Anteil an der Zeit. Im Stillstand füllt sie die freie Zeit mit sehr kleinen Blöcken (siehe unten): Beschäftigung, keine Überlast.

## Was der Emulator schon zeigt (und die Messung am Gerät bestätigen soll)

- **Die 87 % im Stillstand sind keine Überlast.**
  - Ein Rechenfehler in der Aufgabenplanung (`16 / 44100` als ganze Zahl ergibt 0) lässt die Audio-Routine etwa alle 12 µs von Neuem laufen, mit je 4–8 Samples.
  - Jeder Durchgang geht alle Spuren durch. Stille Kits und Audiospuren richten dabei ihre ganze Effektkette ein, bevor sie merken, dass nichts klingt. Das kostet pro Spur rund 550 Befehle, und das jede 12 µs.
  - Im Emulator sind das 90 % Beschäftigung bei nur etwa 12 % echter Last.
  - Auf dem Gerät kommt dazu: Jeder Durchgang liest rund 99 KB Daten, dreimal mehr als in den L1-Cache passt. Deshalb hilft der L2-Cache so deutlich.
- **Unnötig abgeschnittene Stimmen (VC) beim Streamen von Samples.**
  - Wartet der Deluge auf die Karte, rechnet er das Audio innerhalb des Lade-Tasks.
  - Das Culling beurteilt die Last dann nach der mittleren Dauer des Lade-Tasks, samt Wartezeit auf die Karte, und schneidet Stimmen ab, obwohl der Audio-Puffer kaum im Rückstand ist.
  - Im Emulator mit einer langsamen Karte: 17 solche Schnitte, der Puffer höchstens 6 Samples im Rückstand.
- Beides behebe ich in der nächsten Version. Mit dieser Messversion lässt sich vorher und nachher auf dem Gerät vergleichen.

## Geprüft

- **Build:** ohne neue Warnungen. Je zwei komplette Neubauten ergeben dieselbe SHA-256.
- **PC** (`tests/profiler/run.sh`):
  - Ringpuffer und Kodierung der Firmware: 1175 Prüfungen.
  - Die Dekoder der Browser-Seite und des Python-Skripts lesen dieselben Meldungen exakt: 47 und 48 Prüfungen.
- **Emulator** (`tests/profiler/profiler_emu.py`, die echte Firmware, der Timer-Interrupt so, wie ihn die CPU nimmt): 21 Prüfungen, mit beiden ausgelieferten Builds alle bestanden.
  - Keine Meldung verloren. Die Gewichte der Stichproben ergeben die Zeit (4365 von 4400 ms, der Rest noch im Puffer).
  - Der Anteil der Audio-Routine stimmt mit dem CPU-Monitor überein: im Stillstand 60,4 zu 59,8 %, beim Abspielen 97,4 zu 99,2 %.
  - Reverb, Drone und Kompressor stimmen mit den gezählten Befehlen überein.
  - Die Spurzeiten stimmen mit den Stichproben überein: beim Abspielen 89,9 zu 87,3 %.
- **Auf dem Gerät nicht getestet.**
  - Den Timer-Interrupt (OS-Timer 1, bisher unbenutzt) bildet der Emulator nur nach.
  - Kommen keine Daten, oder stürzt der Deluge beim Einschalten von Profile ab, bitte zurück zu v15 und mir Bescheid geben.
  - Solange Profile aus ist, läuft der Profiler nicht.

## Kosten

Mit Profile an: etwa 0,04 % CPU für die Stichproben, zwei Zeitmessungen pro Spur und Audio-Block, rund 8 KB/s über USB-MIDI (Port 3). Der Puffer lässt immer 1 KB frei, damit Noten und Clock nicht warten müssen.
