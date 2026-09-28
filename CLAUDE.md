# How we work in this repo

- **Keep agent and workflow work compact.** Few agents, clear word limits in the prompt (result at most ~300 words), few tool calls, no essays and no full coverage unless explicitly asked. Verification: at most one checker per finding. The cloud container has 4 CPUs, so only 2 agents run at the same time. Every extra agent makes the wait longer.
- **Roles:** The cloud session develops: firmware code, builds, emulator tests, releases. A local session at the computer with the Deluge only does device tests and live views (`mastertune-1.2.1/DEVICE.md`) and changes no firmware code.
- **Language:** Everything in the repository is in English: docs, reports, code comments, commit messages. Answers to the user in the chat: short and precise, in German with Swiss spelling (ss instead of ß). Don't leave out important details or the big picture, and give reasons.
