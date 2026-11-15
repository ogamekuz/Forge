FORGE {version} for macOS — EVE Online industry assistant
=========================================================

Русская инструкция — в файле README.ru.txt.

What it is
----------
A local desktop program: exact materials and the real build cost of a blueprint,
what is worth building, a job schedule over all your characters, build reports.
It runs only on this computer and talks only to the official EVE servers (ESI,
EVE login, image server) and Fuzzwork (static game data, market aggregates).

Needs a Mac with Apple Silicon (M1 or newer) and macOS {macos} or newer.

How to start
------------
1. Drag Forge.app into Applications (or anywhere you like).
2. Double-click Forge.app. The first time macOS says it could not verify
   that "Forge" is free of malware — click "Done". The program is not signed
   with a paid Apple Developer ID; that is all this means.
3. Open System Settings → Privacy & Security, scroll down to the message
   about "Forge" and click "Open Anyway", then confirm with your password or
   Touch ID. From then on Forge opens with a normal double-click.
   (macOS 14 and older: right-click Forge.app → Open → Open.)
4. When Forge first stores a character's access token, macOS may ask to use
   the keychain — click "Always Allow". After an update it may ask once more.

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
~/Library/Application Support/Forge: forge.db (database), forge.toml
(settings), reports/ (build reports), .forge/ (window state), forge_desktop.log.
Icon cache: ~/Library/Caches/Forge. Character access tokens are kept in the
macOS Keychain, not in these files.
To open the folder: Settings → System → Data → "Show", or in Finder
Go → Go to Folder… and paste ~/Library/Application Support/Forge
Update: replace Forge.app with the new one — settings and data stay.
Uninstall: delete Forge.app and the folder above.

If something goes wrong
-----------------------
- Errors are written to forge_desktop.log in the data folder.
- Report server ports 8000–8010: if all are busy, reports will not open, the
  rest of the program works.

Source code, updates, issues: see the project page on GitHub.

EVE Online and the EVE logo are the registered trademarks of Fenris Creations ehf.
(formerly CCP hf.). Forge is a third-party tool, not affiliated with or endorsed
by Fenris Creations.
