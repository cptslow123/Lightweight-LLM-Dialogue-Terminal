@echo off
rem Start llm-harness using the folder of this script
chcp 65001 >nul
title llm-harness
cd /d "%~dp0"
if not exist "..\.venv\Scripts\python.exe" (
    echo [ERROR] Shared .venv not found. Run install.bat first.
    pause
    exit /b 1
)
"..\.venv\Scripts\python.exe" -m llm_harness
pause
