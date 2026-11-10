@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto :use_python
py -3 build_installer.py
if errorlevel 1 exit /b 1
exit /b 0
:use_python
python build_installer.py
if errorlevel 1 exit /b 1
exit /b 0
