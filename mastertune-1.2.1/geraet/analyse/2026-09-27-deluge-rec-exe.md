# DelugeRec.exe mit dem geprüften Stand, 27.09.2026

**Ergebnis:** Die .exe ist mit dem geprüften Stand neu gebaut, auf Windows selbst getestet und im Release ersetzt. Der Link bleibt gleich: https://github.com/Giansn/deluge/releases/download/deluge-rec/DelugeRec.exe

- **Quelle:** `tools/deluge_rec.py`, `tools/deluge_rec.ico` und `tests/deluge_rec` unverändert vom Entwicklungs-Branch (`2234215`, «reviewed (7 fixes) …»), auf `geraet-ergebnisse` als `e0bc3a7`. Das Icon war schon gleich.
- **Tests hier:**
  - `python3 tests/deluge_rec/test_deluge_rec.py`: 25 Tests, alle ok (5,8 s, Python 3.11, ohne Audio und Display).
  - Dazu `--selftest 6` unter Xvfb (Python 3.12): ok, 4,4 s Aufnahme.
- **Workflow «DelugeRec für Windows», Lauf 2** (https://github.com/Giansn/deluge/actions/runs/36343037010): alle Schritte grün, 2 min 15 s.
  - **Build:** PyInstaller, eine Datei, ohne Konsole, Python 3.12.10.
  - **Selbsttest der .exe auf Windows:** PortAudio V19.7.0 geladen (der Runner hat keine Audiogeräte). Die Aufnahme `USB00001.WAV` hat 2 Kanäle, 24 Bit, 44'100 Hz und 193'599 Frames: ok. Selbsttest ok, Exit 0.
  - **Datei:** `DelugeRec.exe` mit 25'075'574 Bytes, SHA-256 `c1645dfd37ed4b60e738afd0b62c624c75a9ac9c88dba09d54db99bd7fbf9e3c`. Sie liegt im Release `deluge-rec` (ersetzt um 19:07 UTC) und als Artefakt `DelugeRec-windows` des Laufs.
- **Hinweis:** Das Tag `deluge-rec` zeigt noch auf den ersten Build (`6dff8ff`), die Datei im Release stammt aus `e0bc3a7`. Aus welchem Commit gebaut wurde, zeigt der Lauf. Vorschlag: der Workflow schreibt den Commit künftig in die Release-Notiz.
- **Nicht geprüft:** mit dem echten Deluge am PC.
