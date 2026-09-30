@echo off
rem Forge 3.0 - desktop control panel (PySide6), no console window.
rem The panel starts its own local report server (127.0.0.1, ports 8000-8010).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo No .venv found. Create it first:
  echo   py -3.12 -m venv .venv
  echo   .venv\Scripts\pip install -e .[dev,desktop]
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m forge.desktop
