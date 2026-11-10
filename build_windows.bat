@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0build_windows.py"
    exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if not errorlevel 1 (
    python "%~dp0build_windows.py"
    exit /b %ERRORLEVEL%
)

echo [ERROR] Python 3.10+ not found. Install it and add it to PATH.
exit /b 1
