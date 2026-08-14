@echo off
rem Install llm-harness: create venv + install package
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found in PATH. Install Python 3.10+ first.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
) else (
    echo [1/2] venv already exists, skipping.
)
echo [2/2] Installing package...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -e ".[files]"
if errorlevel 1 (
    echo [ERROR] pip install failed. Check network/proxy.
    pause
    exit /b 1
)
echo.
echo Done! Run start.bat to launch llm-harness.
pause
