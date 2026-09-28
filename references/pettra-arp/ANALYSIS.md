# Nachmessung der beiden Ausschnitte (Claude, 2026-09-26)

Zwei unabhängige Analysen (Rhythmus, Tonhöhe/Klang) plus eine eigene Gegenprüfung der Kernaussage. Alles aus dem fertigen Mix gemessen, deshalb mit Sicherheitsangabe.

## Bestätigt (zwei Methoden stimmen überein)

- **Tempo 138 BPM** (Beat 434,8 ms). Das README nennt 137,8, das ist nur die Rasterstufe der Tempo-Schätzung.
- **Es gibt eine beschleunigende Figur.** Drei, vier gleich laute Anschläge, deren Abstände jeweils auf etwa das 0,65- bis 0,75-Fache schrumpfen und die genau eine Achtel füllen, bis in den nächsten Schlag:

  | Stelle im Lied | Abstände | Summe |
  |---|---|---|
  | 0:57,2 | 99 → 64 → 46 ms | 209 ms |
  | 6:39,2 | 102 → 75 → 46 ms | 223 ms |
  | 6:31,1 (nur Rhythmus-Analyse) | 99 → 65 → 54 ms | 218 ms |
  | 6:40,2 (nur Rhythmus-Analyse) | 144 → 94 → 64 ms | 302 ms |

  Eine Achtel dauert bei 138 BPM 217 ms. Die Abstandszeiten der ersten beiden Stellen haben die Rhythmus-Analyse und die HPSS-Gegenprüfung unabhängig voneinander auf wenige Millisekunden genau gefunden.
- **Kein durchgehendes 1/32-Arpeggio.** Keine der Methoden findet eine 54-ms-Periodik. Der Arp spielt in Phrasen, nicht durchgehend. Die Angaben «1/32, 25 % Gate, Zufallsreihenfolge» im README lassen sich nicht bestätigen.
- **Klang:** Sägezahn, Filter offen (Obertöne bis über 3,5 kHz, Abfall etwa 5–6 dB/Oktave, kaum Resonanz), leicht verstimmt (10–15 Cent), eher schmal im Stereo. Bei 6:36 breiter.
- **Pumpen:** Der Arp selbst wird nicht gepumpt. Bass und Pad ducken nach dem Kick, der Bass hat seine Spitze auf dem Offbeat.

## Nur von einer Methode gefunden (unsicher)

- Eine **verlangsamende** Figur direkt nach dem Kick bei 0:56,1 und 1:03,1: 48 → 55 → 67 → 81 → 99 ms, etwa ×1,2 pro Anschlag.
- **Harmonie bei 6:36:** Bewegung in D-harmonisch-Moll (A, B♭, C#, D, E, F, G). Bei 0:57 liegt A-Dur mit B und G.
- **Gate eher 50 % oder mehr** statt 25 %. Das Pad verdeckt hier viel.
- Ob die Anschläge der Figur einzelne Arp-Töne oder ganze Akkord-Stösse sind, lässt sich aus dem Mix nicht sicher trennen.

## Nachbau auf dem Deluge mit Firmware v6 (genau)

- **Tempo:** 138.
- **Arp:** Sync 1/8, Ratchet notes 3, Ratchet bounce +6, Bounce fade aus.
- **Einsätze:** 0, 99 und 169 ms nach Beginn der Achtel, also Abstände von 99, 70 und 49 ms. Gemessen wurden 99, 64 und 46 ms bei 0:57 und 102, 75 und 46 ms bei 6:39.
- **Ratchet probability:** In der Automation-Ansicht nur auf die Achtel vor dem Schlag setzen, an dem die Figur kommen soll, sonst 0.

## Nachbau auf dem Deluge mit Firmware v5 (angenähert)

- **Tempo:** 138.
- **Arp:** Sync 1/8, Ratchet Bounce +6 bis +7. Das ergibt ein Verhältnis von 0,70 bzw. 0,65, gemessen sind etwa 0,67.
- **Ratchet-Wahrscheinlichkeit:** In der Automation-Ansicht nur auf die Achtel vor den Schlägen setzen, an denen die Figur kommen soll, sonst 0. Im Stück kommt die Figur nur ab und zu, nicht auf jedem Schritt.
- **Gleich laute Anschläge:** Der Bounce macht die späten Anschläge leiser, im Stück sind sie gleich laut. Abhilfe: das Patch-Kabel Velocity → Level des Synths auf 0 stellen.
- **Grenze der 1.3-Ratchets:** Sie wählen die Anzahl zufällig aus 2, 4 oder 8. Im Stück sind es meist 3 Anschläge pro Achtel. Mit 4 kommt ein zusätzlicher kurzer Anschlag etwa 25 ms vor dem Schlag.
- **Exakt ohne Arp:** Die Figur als Noten in den Clip setzen (tief hineinzoomen). Bei 0:57 liegen die Einsätze 0, 99 und 163 ms nach Beginn der Achtel.
