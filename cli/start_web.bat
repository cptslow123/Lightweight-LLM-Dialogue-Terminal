@echo off
cd /d "%~dp0"
.\.venv\Scripts\python.exe android_terminal\server.py %*
