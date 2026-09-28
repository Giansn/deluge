# «Rescue»: der LPF des Songs wirkt nicht (dringender Auftrag, 27.09.2026)

## Dateien

- `karte/SONGS/Rescue.XML`: unverändert von der Karte SD DELUGE, gleich wie im Backup vom 26.09. Gespeichert mit `c1.2.0`. Eine Spur: `Xylophon`.
- `karte/samples-rescue.csv`: die 8 Samples mit Spur, Grösse, Format und Länge. Alle vorhanden. Der Song ist ohne sie nicht stumm, darum keine Samples.

## Notizen des Nutzers

1. **Welcher LPF:** der LPF der ganzen Song-Ansicht (Song-Master), nicht der einer Spur.
2. **Was «wirkt nicht» heisst:** nicht genauer beschrieben (Klang unverändert, Wert fest oder springt zurück?).
3. **Versionen:** «alle Versionen». Es gibt Rescue und Rescue 2–6, alle mit demselben Befund unten. Wie es mit v16-l2d war, ist nicht getrennt notiert.

## Befund in der XML

- **Die Song-LPF-Frequenz ist automatisiert:** `songParams/lpf frequency="0x7FFFFFFF7FFFFFFF80000000"` (24 Hex-Ziffern statt 8). Vermutlich ein einziger Knoten am Anfang mit dem Wert ganz offen (`0x7FFFFFFF`), der den LPF bei der Wiedergabe immer wieder öffnet. Bitte im Emulator prüfen.
- **Resonanz auf Maximum:** `resonance="0x7FFFFFFF"`. `lpfMode` 24dB, `filterRoute` H2L, `affectEntire` 1, `currentFilterType` lpf.
- **In der ganzen Bibliothek** (`deluge topics`, 578 Songs mit Song-LPF) ist der Song-LPF bei 34 Songs automatisiert. Genau dieser Wert steht in 10 Songs: Rescue, Rescue 2–6 (gespeichert mit `c1.2.1`) und **Didge Base V2 3–6**. Rescue ist aus Didge Base V2 entstanden.
  - Didge Base V2 bis V2 4 hatte ein 1.3-Build gespeichert (Noten nur im 1.3-Format, am 26.09. für 1.2 repariert).
  - Möglich also: ein Wert aus 1.3, den 1.2.1 als Automation liest.
  - Andere Formen kommen vor, z. B. `Didge Base V2 2`: `0x7FFFFFFF7FFFFFFFFFFFFFFF7E000000`, `Didge Base`: `0x7FFFFFFF7E00000000004AE47C000000`, `Pseuyy 28Mixed 14–17`: `0x7FFFFFFF720000000000BCB470000000`.
- Rescue 2–6 liegen in `deluge topics` (gleicher Inhalt wie auf der Karte DELUGEBACKU, nur die Sample-Pfade angepasst). Auf Wunsch pushe ich sie auch.
