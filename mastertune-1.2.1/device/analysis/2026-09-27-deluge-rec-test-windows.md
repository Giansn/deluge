# DelugeRec: Tests unter Windows, 27.09.2026

Auftrag 4 der Entwicklungs-Session. `tools/deluge_rec.py`, `tools/deluge_rec.ico` und `tests/deluge_rec/` stimmen auf `geraet-ergebnisse` (`e0bc3a7`) und auf dem Entwicklungs-Branch überein (`git diff` leer). Die Entwicklungs-Session hatte sie schon selbst übertragen. Darum kein weiterer Commit mit den Dateien.

- **Lauf:** `python mastertune-1.2.1/tests/deluge_rec/test_deluge_rec.py` auf Windows 11, Python 3.14.
- **Ergebnis:** 25 Tests, **alle ok**, 6,3 s.
- **Am Gerät:** Der Deluge erscheint unter Windows als Aufnahme-Eingang «Línea (Deluge)», 2 Kanäle, 44,1 kHz. Bei allen vier Schnittstellen: MME, DirectSound, WASAPI, WDM-KS. Ein eigener Mitschnitt über WASAPI exklusiv war bitgenau 24 bit, kein Loch mit reiner Stille in 8 + 20 s Wiedergabe.
