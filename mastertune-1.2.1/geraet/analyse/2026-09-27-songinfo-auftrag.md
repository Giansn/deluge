# Auftrag an die Entwicklungs-Session: Song und Firmware für DelugeRec (SysEx 0x12), 27.09.2026

## Wunsch des Nutzers

Die Aufnahmen von DelugeRec sollen den exakten Songnamen, das Datum und die Firmware haben.

## Warum es die Firmware braucht

- **Datum:** kennt der PC. DelugeRec v5 schreibt es schon in den Namen und in die Datei.
- **Songname und Firmware:** USB-Audio trägt nur Samples, der Deluge muss beides selbst melden. Abfragen darf der PC nicht, denn der Nutzer will, dass er nichts an den Deluge sendet. Also meldet der Deluge es von sich aus, wie schon die CPU-Werte (SysEx 0x10) auf Port 3.

## Protokoll

- **Nachricht:** `F0 00 21 7B 01 12 <JSON> F7` auf USB-MIDI-Port 3 (`upstreamUSBMIDIDevice_port3`), nur gesendet, nie beantwortet.
  - JSON: `{"song":"Rescue 3","fw":"1.2.1-mastertune-v18"}`, UTF-8, 7 Byte in 8 gepackt wie `util/pack.c` (`pack_8bit_to_7bit`).
  - `0x12`, weil `0x10` der CPU-Monitor und `0x11` der Profiler belegen.
- **Wann:**
  - nur solange der Computer den Audio-Stream offen hat (`usbAudioIsStreaming()`, neu)
  - sofort beim Start des Streams und beim Wechsel des Songnamens, sonst alle 2 s
  - nur mit 1 KB Platz im USB-MIDI-Sendepuffer, wie beim CPU-Monitor
- **Inhalt:**
  - `song`: `currentSong->name`. Für einen neuen, nie gespeicherten Song leer. FatFs liefert Namen in CP437 (`FF_CODE_PAGE 437`, `FF_LFN_UNICODE 0`). Der Patch wandelt sie mit `ff_oem2uni` nach UTF-8 um, höchstens 120 Zeichen und 240 Byte, gekürzt vor einem ganzen Zeichen.
  - `fw`: `kFirmwareVersionString`.

## Patch

`2026-09-27-songinfo.patch` in diesem Ordner, auf v17 mit `patches/0001–0074`, sauber anwendbar mit `git am` (geprüft). Bitte in v18 unter der nächsten freien Nummer übernehmen.

- **Neue Dateien:** `io/usb/usb_song_info.h` und `.cpp` (die Routine), `usb_song_info_message.cpp` (die Nachricht allein, ohne Hardware).
- **Geänderte Dateien:**
  - `usb_audio`: `usbAudioIsStreaming()` gibt `pipeRunning` zurück.
  - `deluge.cpp`: die Aufgabe «usb song info» alle 0,25 s, gleich nach dem CPU-Monitor.

## Geprüft

- **Build:** `dbt build release` fehlerfrei, keine neuen Warnungen. Die Routine ist gelinkt.
- **Nachricht gegen DelugeRec:** Der Nachrichtenbau der Firmware, für den PC kompiliert, mit der echten CP437-Tabelle von FatFs (`ffunicode.c`), und der Leser von DelugeRec v5 (`DelugeInfo.parse`) passen zusammen. Alle 8 Fälle kommen exakt an:
  - «Grüezi» aus CP437
  - «Café ¢ 45°»
  - `"` und `\` im Namen
  - leerer Song
  - 200 Zeichen, gekürzt auf 120
  - 120 × «ü» (genau 240 Byte)
  - ein Umlaut jenseits der 120 Zeichen
  - ein Steuerzeichen
  - Die längste mögliche Nachricht hat 428 Byte.
- **Prüfung:** ein Prüfer über App und Patch. Fünf Fehler bestätigt und behoben:
  - Umlaute in CP437 (jetzt nach UTF-8 umgewandelt)
  - Port 3 nie nachgeholt, wenn belegt
  - möglicher Hänger von python-rtmidi beim Schliessen (jetzt Abfrage statt Callback)
  - der Kopf `F0 7D 12` nach SysEx mit der Entwickler-ID 0x7D (wird angenommen)
  - `--list` ohne MIDI-System
  - Ohne Befund: Puffergrössen, Escaping, Timer-Überlauf, `currentSong` beim Laden, Senden ohne Host (`sendBufferSpace()` 0).
- **clang-format:** ohne Befund.

## Nicht geprüft

- **Am Gerät:** Bitte mit DelugeRec v5 testen. Der Dateiname soll dann «Songname Datum Zeit v18.WAV» lauten, und das Display zeigt «SONG …».
- **Im Emulator:** USB läuft dort nicht.

## Hinweise

- **Port 3 ist geteilt:** Auch CPU-Monitor und Profiler senden dort, und `deluge_profiler.py` liest dort. Unter Windows kann nur ein Programm einen MIDI-Eingang offen haben. DelugeRec und `deluge_profiler.py` gehen also nicht gleichzeitig.
- **Port 1 bleibt frei:** DelugeRec öffnet nie Port 1, den eine DAW braucht.
