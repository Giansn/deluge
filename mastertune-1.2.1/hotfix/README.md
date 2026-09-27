# Zwischenlösung: v16-l2d ohne Hänger in der Drone-Ansicht

`deluge-1.2.1-mastertune-v16-l2d-dronefix-ba499a93.bin` ist v16 mit L2-Cache für Code und Daten (`l2test/…v16-l2d-03ccaac5.bin`) und einer einzigen Korrektur, sonst unverändert. SHA-256 `929bdd63…7e01cb6b`.

- **Der Fehler (v13 bis v16, auch die L2-Versionen):** Etwa 15 ms nach dem Öffnen der Drone-Ansicht hängt der Deluge, und der Ton bricht ab. Der Drone-Ansicht fehlte eine eigene Zeichenroutine. Der UI-Timer rief sie alle 15 ms auf, und sie rief sich selbst endlos auf.
- **Die Korrektur:** eine leere Zeichenroutine für die Drone-Ansicht (Commit `ba499a93`, auf v16-l2d `03ccaac5`).
- **Geprüft im Emulator:**
  - `tests/song/drone_reopen_emu.py`: Songs neu öffnen, die Drone-Ansicht bedienen, Songwechsel während der Wiedergabe, den Drone per FFT messen. v16 hängt, diese Datei besteht alle Prüfungen.
  - `DRONE=1 tests/song/run.sh`: bestanden.
  - Der Volllast-Song klingt Bit für Bit wie v16 (`87a7df29…`, `4473b315…`).
- **Auf dem Gerät nicht getestet.** v17 enthält die Korrektur ebenfalls und ersetzt diese Datei.
