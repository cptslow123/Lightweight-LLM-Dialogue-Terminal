@echo off
rem Start llm-harness using the folder of this script
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run: python -m venv .venv
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m llm_harness
pause
