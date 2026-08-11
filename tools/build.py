"""Package the macro as a standalone Windows .exe.

    python tools/build.py            one file, dist/RivalsMacro.exe
    python tools/build.py --onedir   a folder that starts faster
    python tools/build.py --console  keep a console window for debugging

The result needs nothing installed on the target machine - no Python, no
OpenCV. Templates are bundled inside; settings are written next to the .exe.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "RivalsMacro"

# Nothing here is imported by the app, but PyInstaller pulls some of it in via
# transitive hints and each one costs tens of megabytes.
EXCLUDE = [
    "matplotlib", "scipy", "pandas", "PIL", "IPython", "notebook", "pytest",
    "setuptools", "pip", "wheel", "sqlite3", "unittest", "pydoc", "doctest",
    "email", "html", "http", "xmlrpc", "curses", "lib2to3",
]


# Built from a dedicated venv rather than the system interpreter. This machine
# carries an obsolete `typing` backport in site-packages that PyInstaller flatly
# refuses to work alongside, and a clean environment also keeps whatever else is
# installed from leaking into the bundle.
BUILD_DEPS = ["pyinstaller", "mss", "numpy", "opencv-python-headless"]


NOTES = """Rivals Macro
============

Run RivalsMacro.exe. Nothing else needs installing.

  F8   start / stop
  F9   panic stop

Pick a preset (Glass Grind = grenade launcher + Random), make sure Roblox is on
screen, then press Start or F8.

"Scan screen" tells you what the macro can currently see. Use it first if
nothing seems to happen - it reports the window it found, whether that window
has focus, and which screen it recognised.

Settings are saved to config.json next to this file.

Windows SmartScreen may warn about an unrecognised app: this build is not
code-signed. Choose "More info" -> "Run anyway" if you trust where you got it.
"""


def build_python() -> Path:
    venv = ROOT / ".build-venv"
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if python.exists():
        return python
    print("creating the build environment (one time, a few minutes)...")
    subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    subprocess.check_call([str(python), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    subprocess.check_call([str(python), "-m", "pip", "install", "-q", *BUILD_DEPS])
    return python


def build(onefile: bool, console: bool) -> Path:
    python = build_python()
    for stale in (ROOT / "build", ROOT / "dist", ROOT / f"{NAME}.spec"):  # noqa: E501
        if stale.is_dir():
            shutil.rmtree(stale)
        elif stale.exists():
            stale.unlink()

    cmd = [
        str(python), "-m", "PyInstaller",
        "--name", NAME,
        "--onefile" if onefile else "--onedir",
        "--console" if console else "--windowed",
        "--noconfirm", "--clean",
        # ';' is the separator on Windows, ':' elsewhere
        "--add-data", f"{ROOT / 'templates'}{';' if sys.platform == 'win32' else ':'}templates",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT),
    ]
    for module in EXCLUDE:
        cmd += ["--exclude-module", module]
    cmd.append(str(ROOT / "main.py"))

    print(" ".join(cmd[:8]), "...")
    start = time.perf_counter()
    subprocess.check_call(cmd, cwd=ROOT)
    print(f"built in {time.perf_counter() - start:.0f}s")

    exe = ROOT / "dist" / (f"{NAME}.exe" if onefile else f"{NAME}/{NAME}.exe")
    return exe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onedir", action="store_true", help="folder build, faster start")
    parser.add_argument("--console", action="store_true", help="keep a console window")
    parser.add_argument("--skip-check", action="store_true")
    args = parser.parse_args()

    exe = build(onefile=not args.onedir, console=args.console)
    if not exe.exists():
        print(f"FAILED: {exe} was not produced")
        return 1
    size = exe.stat().st_size / 1e6
    if args.onedir:
        size = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file()) / 1e6
    print(f"\n{exe}  ({size:.0f} MB)")

    notes = exe.parent / "README.txt"
    notes.write_text(NOTES, encoding="utf-8")
    print(f"wrote {notes.name}")

    if not args.skip_check:
        print("\nrunning the packaged self-test...")
        done = subprocess.run([str(exe), "--selftest"], capture_output=True, text=True, timeout=180)
        print(done.stdout.strip() or done.stderr.strip())
        if done.returncode != 0:
            print("FAILED: the bundle is not self-consistent")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
