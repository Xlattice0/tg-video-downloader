# -*- coding: utf-8 -*-
"""Build the Windows Setup installer (Inno Setup) after the exe exists."""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ISS = ROOT / "installer" / "setup.iss"
DIST_EXE = ROOT / "dist" / "TGVideoDownloader.exe"
SETUP_NAME = "TGVideoDownloader-Setup-1.4.0.exe"
DIST_SETUP = ROOT / "dist" / SETUP_NAME
INNO_INSTALLER = ROOT / "build" / "innosetup-install.exe"
INNO_URL = "https://files.jrsoftware.org/is/6/innosetup-6.5.4.exe"

ISCC_CANDIDATES = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
]


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=str(ROOT), **kw)


def find_iscc() -> Path | None:
    for p in ISCC_CANDIDATES:
        if p and p.is_file():
            return p
    from shutil import which
    found = which("ISCC") or which("iscc")
    return Path(found) if found else None


def install_inno_setup() -> None:
    INNO_INSTALLER.parent.mkdir(parents=True, exist_ok=True)
    if not INNO_INSTALLER.is_file():
        print(f"Downloading Inno Setup from {INNO_URL} ...", flush=True)
        urllib.request.urlretrieve(INNO_URL, INNO_INSTALLER)
    print("Installing Inno Setup (silent) ...", flush=True)
    run([str(INNO_INSTALLER), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"])


def ensure_exe() -> None:
    if DIST_EXE.is_file():
        print(f"Using existing exe: {DIST_EXE} ({DIST_EXE.stat().st_size} bytes)", flush=True)
        return
    print("Exe not found, building it first ...", flush=True)
    run([sys.executable, str(ROOT / "build_windows.py")])
    if not DIST_EXE.is_file():
        raise SystemExit("TGVideoDownloader.exe was not built.")


def main() -> int:
    if not ISS.is_file():
        raise SystemExit(f"Missing {ISS}")
    # Always rebuild exe so installer ships the current source (app_dir, version).
    print("Building exe first ...", flush=True)
    run([sys.executable, str(ROOT / "build_windows.py")])
    ensure_exe()

    iscc = find_iscc()
    if iscc is None:
        try:
            install_inno_setup()
        except Exception as e:
            print(f"Silent Inno Setup install failed: {e}", flush=True)
            print("Trying winget ...", flush=True)
            run([
                "winget", "install", "--id", "JRSoftware.InnoSetup", "--exact",
                "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity",
            ])
        iscc = find_iscc()
    if iscc is None:
        raise SystemExit("ISCC.exe not found after installing Inno Setup.")

    print(f"Compiling installer with {iscc} ...", flush=True)
    run([str(iscc), str(ISS)])
    if not DIST_SETUP.is_file():
        raise SystemExit(f"Build finished but {DIST_SETUP} was not found.")
    print(f"OK: {DIST_SETUP} ({DIST_SETUP.stat().st_size} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
