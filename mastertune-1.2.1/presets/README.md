# Presets: Ping-Pong-Arp

Zwei Synth-Vorlagen für den Ratchet-Bounce, ab **v13**. Beide hat die Firmware selbst gespeichert (im Emulator, mit demselben Code wie «Save» am Gerät). Das Format stimmt also genau.

| Datei | Was sie macht | Hörbeispiel |
|---|---|---|
| `SYNTHS/PINGPONG ROLL.XML` | **Neu:** Ping-Pong-Ball zwischen zwei Platten, die sich schliessen. Pro Ratchet 16 Schläge über 4 Achtel, immer schneller und lauter, bis in den nächsten Schlag. | `demo/PINGPONG ROLL.wav` |
| `SYNTHS/PETTRA PINGPONG.XML` | Die Figur aus Pettra «You Are The Seeds» (0:57, 6:39): 3 gleich laute Schläge in einer Achtel, jeder Abstand ×0,7, bis in den nächsten Schlag. | `demo/PETTRA PINGPONG.wav` |

Die Hörbeispiele sind mit der echten Firmware im Emulator gerendert, auf einem gehaltenen A-Dur-Akkord bei 120 BPM.

## Auf die Karte

Die beiden `.XML`-Dateien in den Ordner `SYNTHS` der SD-Karte kopieren. Auf dem Deluge dann einen Synth-Clip öffnen und das Preset wie jedes andere laden. Die Arp-Einstellungen kommen mit.

## Einstellungen

Alles unter **Menü → Arpeggiator**:

| Einstellung | PINGPONG ROLL | PETTRA PINGPONG | Was sie tut |
|---|---|---|---|
| Sync | 1/8 | 1/8 | Länge eines Arp-Schritts |
| Ratchet notes | **Roll** | 3 | Roll = so viele Schläge, bis sich die Platten schliessen |
| Ratchet bounce | +4 | +6 | Beschleunigung: jeder Abstand ist r × der vorherige, r = 0,95 bei +1 bis 0,5 bei +10. Negativ: langsamer werdend. 0: gleichmässig. |
| **Bounce length** (neu) | 4 | 1 | Über wie viele Arp-Schritte ein Ratchet dauert |
| **Bounce velocity** (neu, früher «Bounce fade») | Rise | Even | Even = gleich laut, Fade = leiser (Ball), Rise = lauter (Ping-Pong) |
| Ratchet probability | 100 % | 100 % | Wie oft ein Schritt ratchet |

Die Rechnung hinter Roll:
- Eine Spanne S hat L Arp-Schritte. Schlag j liegt bei **S·(1 − rʲ)**. Die Reihe konvergiert genau auf das Ende der Spanne, dort treffen sich die Platten auf dem nächsten Schlag im Raster.
- Sobald ein Abstand unter 16 ms fällt, gehen die Schläge im 16-ms-Takt weiter («trrrr»).
- Beispiel PINGPONG ROLL bei 120 BPM: Abstände 200, 160, 128, 102, 82 … 21, 17 ms, danach 16 ms.

## Anpassen

- **Nur ab und zu ein Fill statt auf jedem Schritt:**
  - Ratchet probability senken.
  - Oder in der Automation-Ansicht nur auf die Schritte setzen, vor denen der Fill kommen soll, sonst 0. Bei Pettra kommt die Figur nur ab und zu.
- **Länger oder kürzer:** Bounce length ändern oder Sync. Bei 1/16 und Bounce length 8 dauert der Fill auch eine halbe Note.
- **Weicher oder härter beschleunigen:** Ratchet bounce +1 … +10.
- **Der Schlag nach dem Fill soll knallen:** Ratchet probability unter 100 %. Dann ist der Landeschritt eine normale Note mit voller Velocity. Mit 100 % beginnt dort gleich der nächste Fill, und Rise startet ihn mit halber Velocity.
- **Klang:**
  - zwei leicht verstimmte Sägezähne (+12 Cent), 24-dB-Tiefpass eher offen
  - Hüllkurve kurz ohne Sustain, Velocity → Lautstärke 70 %
  - Für Pettra wie bei 6:36: Filter tiefer (dunkler) und etwas lauter.
- **Tempo:** Die Presets speichern kein Tempo. Pettra läuft bei 138 BPM.
