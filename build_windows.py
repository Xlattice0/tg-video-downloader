# -*- coding: utf-8 -*-
"""Build TGVideoDownloader.exe with PyInstaller (1.3.15)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "tg-video-downloader.spec"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
DIST_EXE = ROOT / "dist" / "TGVideoDownloader.exe"
ICON = ROOT / "app-icon.ico"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=str(ROOT))


def main() -> int:
    py = sys.executable
    if not VENV_PY.exists():
        print("Creating venv ...", flush=True)
        run([py, "-m", "venv", str(ROOT / ".venv")])
    vpy = str(VENV_PY)
    print("Installing dependencies and PyInstaller ...", flush=True)
    run([vpy, "-m", "pip", "install", "--upgrade", "pip"])
    run([vpy, "-m", "pip", "install", "-r", "requirements.txt", "pyinstaller"])
    if not ICON.is_file():
        raise SystemExit(f"Missing app icon: {ICON}")
    print("Building exe (this may take a few minutes) ...", flush=True)
    # 图标由 tg-video-downloader.spec 的 EXE(icon=...) 打进 exe
    run([vpy, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)])
    if not DIST_EXE.exists():
        raise SystemExit("Build finished but dist/TGVideoDownloader.exe was not found.")
    print(f"OK: {DIST_EXE} ({DIST_EXE.stat().st_size} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
