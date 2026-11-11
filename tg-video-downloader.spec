# TG视频下载 1.3.15
# -*- mode: python ; coding: utf-8 -*-
# 本地：双击 build_windows.bat；CI：python -m PyInstaller --noconfirm tg-video-downloader.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("telethon", "cryptg", "python_socks", "sv_ttk", "PIL", "pystray"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += [
    "socks",
    "sockshandler",
    "python_socks",
    "python_socks.async_",
    "python_socks.sync",
    "sv_ttk",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "PIL.ImageDraw",
    "PIL.ImageOps",
    "pystray",
    "pystray._win32",
]

# 运行时窗口图标（frozen 时位于 sys._MEIPASS）
SPEC_DIR = Path(SPECPATH)
for name in ("app-icon.ico", "app-icon-256.png", "app-icon-64.png"):
    src = SPEC_DIR / name
    if src.is_file():
        datas += [(str(src), ".")]

ICON = str(SPEC_DIR / "app-icon.ico")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TGVideoDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON,
)
