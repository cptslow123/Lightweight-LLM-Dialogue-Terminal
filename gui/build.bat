@echo off
setlocal
cd /d "%~dp0"
if not exist node_modules call npm install
call npm run build
if errorlevel 1 exit /b 1
call build_backend.bat
if errorlevel 1 exit /b 1
call npm run tauri:build -- --no-bundle
if errorlevel 1 exit /b 1
if not exist release mkdir release
copy /y "src-tauri\target\release\light-harness-gui.exe" "release\Light Harness.exe" >nul
copy /y "dist\backend.exe" "release\backend.exe" >nul
copy /y "data\config.toml" "release\config.toml" >nul
copy /y "config.example.toml" "release\config.example.toml" >nul
echo Portable build created: %CD%\release
endlocal
