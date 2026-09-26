# L2-Cache: zwei Testversionen

Beide Versionen sind **v14 mit eingeschaltetem L2-Cache**, sonst unverändert. Sie sind zum Messen und Testen gedacht, nicht für Auftritte, solange sie nicht auf dem Gerät geprüft sind.

| Datei | Was | Risiko |
|---|---|---|
| `deluge-1.2.1-mastertune-v14-l2i-198e9822.bin` | L2 **nur für Code**. Stand der Community von 12/2024, dort seither in Nightly und Beta. | gering |
| `deluge-1.2.1-mastertune-v14-l2d-d0791052.bin` | L2 **für Code und Daten**, mit Prefetch. Stand der Community von 04/2026 mit den Korrekturen danach. | mittel |

SHA-256: l2i `@@L2I_SHA@@`, l2d `@@L2D_SHA@@`. Patches auf v14: `0001-…` und `0002-…` (nur Code), `0003-…` (Daten).

## Worum es geht

Der RZ/A1L hat vor dem Speicher 128 KB L2-Cache, den 1.2.1 nie einschaltet. Genutzt wird nur der L1-Cache (je 32 KB für Code und Daten).
- **Nur Code:** Der Code, den ein voller Song dauernd braucht, passt nicht in 32 KB und fällt ständig aus dem L1-Cache. Der L2 hält ihn näher an der CPU. Daten dürfen nicht hinein.
- **Code und Daten:** Zusätzlich kommen die Daten im langsamen externen SDRAM in den L2: Delay- und Chorus-Puffer, Wellentabellen und Samples. Hier liegt vermutlich der grössere Gewinn.
- **In beiden Versionen:** Vor jeder DMA-Übertragung schreibt die Firmware den Puffer auch im L2 zurück und leert ihn, nach dem Lesen von der Karte noch einmal.
  - Das betrifft die SD-Karte (Lesen und Schreiben) und das OLED.
  - Audio, MIDI und der LED-Controller laufen über Adressen ohne Cache, USB ganz ohne DMA.
  - Auch die Code-Version braucht das: Der Prozessor darf auf Verdacht Befehle aus jedem Speicher holen, so kann eine Zeile eines Puffers im L2 landen.
  - Ein vergessener Pfad würde seltene Fehler geben, im schlimmsten Fall falsche Bytes in Dateien auf der Karte.

Wie viel es bringt, weiss niemand: Die Community nennt keine Zahl, und der Emulator bildet keine Caches ab. Das zeigt nur die Messung am Gerät.

## Messen

1. Den Test-Song `diag/loadtest-card.zip` auf die Karte kopieren (siehe `diag/README.md`).
2. Mit **v14** starten, `MT_LOADTEST` laden, **Settings → CPU monitor → On**, **Play**. Nach etwa 30 Sekunden die Anzeige oben links notieren (`CPU 61%  24 voices`), am besten dreimal im Abstand von einigen Sekunden.
3. Dasselbe mit **l2i** und mit **l2d**.
4. Genauer geht es mit `tools/cpu_monitor.html` (eine Minute, CSV exportieren).

Weniger CPU % bei gleich vielen Stimmen heisst: Der L2 bringt so viel. Erscheinen mit v14 `quality lowered` oder `voices cut!`, sollten sie mit dem L2 seltener werden.

## Testen, vor allem die Daten-Version

**Vorher die SD-Karte sichern** oder mit einer Kopie arbeiten.

- **Speichern:** Einen Song zweimal unter neuem Namen speichern, einmal gestoppt und einmal während er spielt. Am Computer vergleichen: Die Dateien müssen gleich sein.
- **Samples:** Einen Song mit vielen langen Samples spielen und auf Knackser in regelmässigen Abständen hören (Streaming von der Karte).
- **Aufnehmen:** Eine Minute resamplen oder vom Eingang aufnehmen und die Aufnahme auf Knackser anhören.
- **OLED:** Schnell durch Presets blättern und auf Pixelfehler oder verschobene Zeilen achten.
- **USB:** Mit DEx oder deluge-editor eine Datei auf die Karte kopieren und zurück, dann vergleichen.

Wenn etwas auffällt: zurück zu v14 und mir beschreiben, was passiert ist.

## Zurück zu v14

Wie jedes Firmware-Update: `deluge-1.2.1-mastertune-v14-…bin` aus dem Hauptordner auf die Karte und neu starten.

## Geprüft

- **Build:** ohne neue Warnungen. Je zwei komplette Neubauten ergeben dieselbe SHA-256.
- **Emulator** (`tests/l2`, mit einem Modell des L2-Controllers):
  - Beim Start wird der L2 ausgeschaltet, geleert, für Daten gesperrt und dann eingeschaltet.
  - Code-Version: Daten bleiben gesperrt.
  - Daten-Version: Prefetch an. Die Daten werden am Ende des Starts einmal freigegeben, direkt nachdem alles zurückgeschrieben und geleert ist.
  - Jedes OLED-Bild wird vor dem DMA in L1 und L2 zurückgeschrieben (alle 25 Cache-Zeilen) und synchronisiert.
  - Die Cache-Pflege trifft auch bei krummen Adressen jede Zeile genau einmal.
- **Song:** Der Volllast-Song klingt mit beiden Versionen Bit für Bit wie v14, in allen drei Läufen.
- **Gegenprüfer** (Code, jede DMA-Übertragung der Firmware):
  - In den L2-Änderungen selbst fand er keinen Fehler.
  - Umgesetzt sind seine Vorschläge für die Code-Version (L2-Pflege auch dort) und für die Freigabe der Daten (Interrupts aus, zurückschreiben statt nur leeren).
  - Zwei ältere Fehler, die er dabei fand, sind in v14 behoben (siehe README, v14).
- **Nicht prüfbar im Emulator:**
  - das echte Cache-Verhalten und die Geschwindigkeit
  - die SD-Übertragungen, weil der Emulator die Karte unterhalb des Dateisystems liest. Ihre Cache-Pflege ist dieselbe Funktion wie beim OLED und im Code geprüft.
- Das Laden von Firmware per USB-SysEx (Entwickler-Funktion) ist in diesen Builds nicht enthalten. In Builds mit dieser Funktion schaltet sie den L2 vor dem Sprung sauber aus.
