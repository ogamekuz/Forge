FORGE {version} — EVE Online industry assistant
==================================================

Русская инструкция — в файле README.ru.txt.

What it is
----------
A local desktop program: exact materials and the real build cost of a blueprint,
what is worth building, a job schedule over all your characters, build reports.
It runs only on this computer and talks only to the official EVE servers (ESI,
EVE login, image server) and Fuzzwork (static game data, market aggregates).

How to start
------------
1. Unzip the WHOLE folder anywhere (Desktop, Documents…). No admin rights needed.
2. Double-click "Forge.cmd". The Forge window opens (no console).
3. If Windows shows "Windows protected your PC" — click "More info", then
   "Run anyway". The program is not code-signed; that is all this means.
4. The firewall may ask about python/pythonw — allow it: that is the local
   report server, it listens on 127.0.0.1 only.

First run
---------
On the "Overview" tab:
  1. "Download SDE" — once. The static game data is big (about 0.5 GB on
     disk), the first download takes a few minutes.
  2. "Add character (EVE SSO)" — the browser opens the official EVE login
     page; log in and allow access. Repeat for each character.
  3. "Characters + structure markets" and "Jita market & indices" — sync.
Then use "Calculator" for any blueprint and "What to build" for ideas.
Language: the RUS / ENG switch in the window header.

Your own structures (optional)
------------------------------
"Settings": your places (buy hub, sell market, build system), stations with
rigs and taxes, freight routes, character roles. Without it Forge still works —
just without your structure bonuses and freight.

Where your data is
------------------
Everything stays in this folder: forge.db (database), forge.toml (settings),
reports\ (build reports), .forge\ (window state, icon cache). Character access
tokens are kept in the Windows Credential Manager, not in these files.
To move Forge to another PC, copy the folder and add the characters again.

If something goes wrong
-----------------------
- Errors are written to forge_desktop.log next to forge.toml.
- Report server ports 8000–8010: if all are busy, reports will not open, the
  rest of the program works.

Source code, updates, issues: see the project page on GitHub.

EVE Online and the EVE logo are the registered trademarks of Fenris Creations ehf.
(formerly CCP hf.). Forge is a third-party tool, not affiliated with or endorsed
by Fenris Creations.
