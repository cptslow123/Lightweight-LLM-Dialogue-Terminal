@echo off
setlocal
cd /d "%~dp0"
"..\.venv\Scripts\python.exe" -m pip install pyinstaller
"..\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name backend --paths "..\cli" --add-data "config.example.toml;." backend.py
endlocal
