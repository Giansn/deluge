# Baseline Master für alle Songs, 27.09.2026

Firmware: mastertune v17-l2d (`b3385d83`, der Stand auf dem Gerät). Code aus `release_1_2_1` mit `patches/0001–0074` und `l2test/0001–0003`. Songs aus `geraet/karte/SONGS`. Die Messung lief im Emulator mit genau dieser Firmware.

## Kurz

- **Eine abgesegnete Baseline gibt es nicht.** Weder Synthstrom noch die Entwickler der Community-Firmware geben Werte für Song, Spuren oder Master-Kompressor vor.
  - Offiziell ist nur ein Ziel: am VU-Meter des Deluge nicht über das 3. Pad von oben, also höchstens −4,5 (`docs/community_features.md`, Abschnitt 4.1.8).
  - Wie die Entwickler den Pegel angelegt haben, zeigen die Voreinstellungen: Song 35, Kit 35, Synth und Kit-Reihe 40.
- **Ab 40 verliert keine Spur selbst Qualität.**
  - Jeder Lautstärkeregler ist eine reine Multiplikation in 32 Bit, mit über 30 dB Reserve über der Vollaussteuerung.
  - Verzerren kann nur die Summe am Ausgang: Bei 0 dBFS schneidet der Deluge hart ab, für Kopfhörer, Line-Out, USB und Resample gleich.
  - Man hört es zuerst an der Spur, die man gerade aufdreht, weil ihre Spitzen abgeschnitten werden.
- **Gemessen an deinem Song:** «New Sitar Grii 10» mit allen 7 Clips.

  | Kits | Spitze |
  |---|---|
  | wie gespeichert | −6,1 dBFS |
  | auf 35 | −1,5 dBFS |
  | auf 40 | +0,5 dBFS, es clippt |

  Das Clipping beginnt also genau bei 40.

## Warum ab 40

- **Die Kette:**
  1. Stimme und Effekte der Spur
  2. Lautstärke der Spur, dann die Summe im Kit
  3. Kit-Lautstärke, dann die Summe im Song
  4. Song-Lautstärke und Master-Effekte
  5. Ausgang

  Keine dieser Lautstärken sättigt. Zwischen dem Regler einer Spur und dem Ausgang ist in deinen Songs auch keine verzerrende Stufe an:
  - keine SATURATION
  - kein Kompressor
  - kein Bitcrush
  - die Filter von Kit und Song offen oder ohne Resonanz
  - Flanger und Phaser rechnen linear
  - Hall und Digital-Delay clippen erst weit über 0 dBFS
  - Das einzige Analog-Delay (Kit 3L3Ctr0) hat kein Feedback.

  Absichtlich verzerrende Stufen werden stärker, je lauter das Signal davor ist: SATURATION, Analog-Delay, Filter mit viel Resonanz, Kompressor. In deinen Songs wirkt keine davon nach dem Regler einer Spur.
- **Die einzige Grenze ist der Ausgang** (`doSomeOutputting`, `lshiftAndSaturate<8>`): hart bei 0 dBFS.
  - Kurz davor sitzt eine weiche Sättigung (tanh) im Master-Kompressor. Sie läuft immer mit, drückt aber bei 0 dBFS nur 0,17 dB, darunter fast nichts.
  - Nachgerechnet mit der Tabelle `tanH2d` der Firmware.
- **Die Messung** (Emulator, knapp 7 s, alle 7 Clips; `2026-09-27-baseline-pegel.json`):

| Einstellung | Spitze | Samples über 0 dBFS |
|---|---|---|
| wie gespeichert: Kits 21–27, Reihen meist 40 | −6,1 dBFS | 0 |
| alle Kits auf 35 (Standard) | −1,5 dBFS | 0 |
| alle Kits auf 40 | **+0,5 dBFS** | **30** |
| alle Reihen und Synths von 40 auf 50 | −5,1 dBFS | 0 |

- **Die Spitze kommt vom Kit 3L3Ctr0 (Kicks und Bässe).**
  - Mit den Kits steigt sie um 4,6 und 6,7 dB, genau wie der Regler von 3L3Ctr0: 27 → 35 sind +4,7 dB, 27 → 40 sind +6,8 dB.
  - Die Reihen von 40 auf 50 heben sie nur um 1 dB. Sie kommt also von Reihen, die nicht auf 40 stehen, wohl von den vier Kicks und Bässen dort auf 50.

## Was ein Regler in dB macht

Alle Lautstärkeregler (Song, Kit, Kit-Reihe, Synth, 0–50) folgen derselben Parabel: Von a nach b ändert sich der Pegel um 40·log10(b/a) dB.

| Regler | 20 | 25 | 27 | 30 | 35 | 40 | 45 | 50 |
|---|---|---|---|---|---|---|---|---|
| gegenüber 40 | −12,0 | −8,2 | −6,8 | −5,0 | −2,3 | 0 | +2,0 | +3,9 |
| Song und Kit, gegenüber 35 (Standard) | −9,9 | −6,0 | −4,7 | −2,9 | −0,2 | +2,1 | +4,2 | +6,0 |

- Synth und Kit-Reihe haben 0 dB bei 25. Ihr Standard 40 hebt also schon um 8,2 dB.
- Song und Kit haben 0 dB bei 35,4, ihrem Standard. Ein Kit hat fest noch +1,9 dB dazu, als Ausgleich für die Filterresonanz.

## Die Baseline

| Was | Wert | Warum | Beleg |
|---|---|---|---|
| Song-Lautstärke (Song-Ansicht, AFFECT ENTIRE, LEVEL; oder SONG MENU > MASTER > VOLUME) | 35 als Start, dann so weit hinunter, bis die Spitze bei −6 dBFS liegt | Sie wirkt vor dem Ausgang: Alles wird leiser, das Verhältnis der Spuren bleibt. In 32 Bit kostet Leiserstellen keine Qualität. | Voreinstellung; Messung |
| Kit (Clip-Ansicht mit AFFECT ENTIRE, oder in der Song-Ansicht das Clip-Pad halten) | höchstens 35 | Deine Kits stehen auf 21–27 | Voreinstellung |
| Synth, Kit-Reihe | 40 als Obergrenze für Lautes wie Kick und Bass, 45–50 nur für leise Samples | Von 40 auf 50 sind es +3,9 dB | Voreinstellung |
| Master-Kompressor | aus (Threshold 0, wie in deinen Songs) | Bei 0 regelt er nicht und hebt nichts an. Eingeschaltet gleicht er seine Reduktion mit Make-up-Gain aus, er schafft also keine Reserve. Für Aufnahmen: Mastering am PC. | Code; die Doku nennt nur Bereiche |
| Spitze | höchstens −6 dBFS | In DelugeRec bleibt das Pad −3 dunkel. Am VU-Meter des Deluge höchstens das 3. Pad von oben. Die Reserve deckt Stellen ab, die man nicht gemessen hat. | Doku 4.1.8 (−4,5); Messung |
| VOLUME-Knopf am Deluge | beliebig | analog nach dem Wandler, wirkt nicht auf USB und Resample | Code |
| VOL in DelugeRec | 0 dB | bitgenau. Leiser stellen repariert kein Clipping, das schon im Deluge passiert. | DelugeRec |
| Startsong | DEFAULTS > STARTUP SONG > TEMPLATE, darin die Song-Lautstärke speichern | Der Deluge lädt beim Einschalten `SONGS/DEFAULT.XML` als Song ohne Namen (`deluge.cpp`). | Doku; Code |

**Für deine Songs:**
- **«New Sitar Grii 10»:**
  - Die Song-Lautstärke kann bei 35 bleiben, solange die Kits um 25 stehen (−6,1 dBFS).
  - Mit den Kits auf dem Standard 35 braucht der Song 27: −1,5 − 4,7 = −6,2 dBFS.
- **«Rescue»:**
  - Ein Synth auf 34,8, nicht gemessen.
  - Im Master steht die Resonanz des Tiefpasses auf dem Maximum. Solange der Tiefpass ganz offen ist, läuft der Filter nicht. Dreht man ihn zu, arbeitet er mit voller Resonanz.

## Das VU-Meter des Deluge

- **Es zeigt keine Spitzen.** Es zeigt den Mittelwert, und seine dB sind gestaucht: `(ln(Mittelwert) − 16,7) × 4`. Ein angezeigtes dB sind etwa 2,2 echte dB (`view.cpp`, `envelope_follower.cpp`).
- **Es kann schon clippen, während das Meter gelb zeigt.** Die Doku nennt das 2. Pad von oben «soft clipping», die Firmware schneidet aber hart ab (siehe oben).
- **Für die Baseline gilt DelugeRec:** Es misst jede Spitze einzeln.
- **Einschalten:** AFFECT ENTIRE an, LEVEL/PAN wählen, dann LEVEL/PAN nochmals drücken (Doku 4.1.8).

## Belegt und nicht belegt

- **Offiziell** (Repo von Synthstrom: `docs/community_features.md`, `CHANGELOG.md`):
  - das Ziel −4,5 am VU-Meter
  - Release 1.1: Der Kompressor wurde geändert, «um Clipping zu verringern». Songs aus 1.0 brauchen eventuell eine neue Song-Lautstärke.
- **Voreinstellungen der Firmware:** Song 35, Kit 35, Synth und Reihe 40, Velocity 64, Master-Kompressor aus.
- **Community, nur als Suchtreffer gesehen:** Das Forum war von hier aus gesperrt, die Autoren konnte ich nicht prüfen.
  - Die Song-Lautstärke in der Vorlage tief stellen und den Pegel am Ausgang oder am Mischpult holen.
  - Ab Velocity 100 clippt es mit den Standardwerten.
- **Nicht gefunden:**
  - Werte für Spuren, Master-Kompressor, Sends und Panorama
  - wie viele Spuren ohne Clipping gehen

## Messung nachbauen

```
python3 baseline_pegel.py <deluge.elf> ../karte <out> --song-dir <dev>/mastertune-1.2.1/tests/song \
    --build <Ordner mit blockcount.so>
```

- **Dauer und Aufbau:** Ein Lauf dauert etwa 2 min. Das Skript nutzt `sitar_emu.py`: 440 Hz, alle Kits, 1 Takt Vorlauf, dann 302'400 Samples.
- **Spitze:** vor dem Clip am Ausgang gemessen, darum kann sie über 0 dBFS liegen.
- **Grenze:** Nur dieser Ausschnitt ist gemessen, andere Stellen im Song können lauter sein.
