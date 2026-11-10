@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   TG视频下载
echo ========================================
echo.

set "PY_CMD="

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY_CMD=py -3"
)

if not defined PY_CMD (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY_CMD=python"
    )
)

if not defined PY_CMD (
    echo [错误] 未找到 Python 3.10 或更高版本。
    echo 请先安装官方 Python：https://www.python.org/downloads/
    echo 安装时请勾选 “Add python.exe to PATH”。
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo 正在创建虚拟环境，请稍候...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

set "VENV_PY=.venv\Scripts\python.exe"

"%VENV_PY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前 Python 未包含 tkinter（图形界面库）。
    echo 请重新安装官方 Python，并确保勾选 tcl/tk 组件。
    pause
    exit /b 1
)

echo 正在检查并安装依赖（首次运行可能需要一两分钟）...
"%VENV_PY%" -m pip install --upgrade pip -q
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo 完整依赖安装失败，尝试仅安装核心库 telethon ...
    "%VENV_PY%" -m pip install telethon==1.45.0
    if errorlevel 1 (
        echo [错误] 安装依赖失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

echo.
echo 正在启动程序...
echo.
"%VENV_PY%" app.py
if errorlevel 1 (
    echo.
    echo 程序异常退出。
    pause
)
