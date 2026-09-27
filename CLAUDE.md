# Arbeitsweise in diesem Repo

- **Agenten- und Workflow-Arbeit kompakt halten.** Wenige Agenten, klare Wortlimits im Prompt (Ergebnis höchstens ~300 Wörter), wenige Tool-Aufrufe, keine Essays und keine Vollabdeckung ohne ausdrücklichen Auftrag. Verifikation: höchstens ein Gegenprüfer pro Befund. Der Cloud-Container hat 4 CPUs, also laufen nur 2 Agenten gleichzeitig. Jeder zusätzliche Agent verlängert die Wartezeit.
- **Rollen:** Die Cloud-Session entwickelt: Firmware-Code, Builds, Emulator-Tests, Releases. Eine lokale Session am Rechner mit dem Deluge macht nur Gerätetests und Live-Ansicht (`mastertune-1.2.1/GERAET.md`) und ändert keinen Firmware-Code.
- **Antworten an den Nutzer:** kurz und prägnant, auf Deutsch mit Schweizer Rechtschreibung (ss statt ß). Wichtige Details und das Gesamtbild nicht weglassen und begründen.
