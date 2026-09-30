@echo off
rem Forge - EVE Online industry panel (portable build), no console window.
rem The panel starts its own local report server (127.0.0.1, ports 8000-8010).
cd /d "%~dp0"
if not exist "%~dp0python\pythonw.exe" (
  echo python\pythonw.exe not found.
  echo Unzip the WHOLE Forge folder, not just this file.
  pause
  exit /b 1
)
start "" "%~dp0python\pythonw.exe" -m forge.desktop --config "%~dp0forge.toml"
