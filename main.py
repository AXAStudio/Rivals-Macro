"""Entry point.

    python main.py              open the control panel
    python main.py --selftest   check the install without opening a window

`--selftest` exists for the packaged build: a windowed .exe that cannot find
its templates shows nothing at all, so there has to be a way to ask it whether
its data files came along.
"""

from __future__ import annotations

import sys
import traceback


def selftest() -> int:
    """Prove the templates, manifest and glyphs are present and readable."""
    from rivals import weapons
    from rivals.clock import ClockReader
    from rivals.config import CONFIG_PATH, TEMPLATES_DIR
    from rivals.detect import ALL_SENTINELS
    from rivals.vision import Matcher

    print(f"templates : {TEMPLATES_DIR}")
    print(f"settings  : {CONFIG_PATH}")
    if not TEMPLATES_DIR.is_dir():
        print("FAIL: the templates folder is missing from this build")
        return 1

    matcher = Matcher(TEMPLATES_DIR, screen_height=1439)
    missing = [n for n in ALL_SENTINELS if not matcher.exists(n)]
    if missing:
        print(f"FAIL: missing sentinel templates: {missing}")
        return 1
    for name in ALL_SENTINELS:
        matcher.load(name)  # raises if unreadable

    catalogue = weapons.discover(TEMPLATES_DIR)
    counts = {slot: len(catalogue[slot]) for slot in weapons.SLOTS}
    digits = ClockReader(TEMPLATES_DIR).known_digits
    total = len(list(TEMPLATES_DIR.glob("*.png")))

    print(f"sentinels : {len(ALL_SENTINELS)} loaded")
    print(f"weapons   : {counts}")
    print(f"clock     : digits {digits}")
    print(f"templates : {total} png + manifest")
    if not all(counts.values()) or not digits:
        print("FAIL: the template set is incomplete")
        return 1
    print("OK")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    try:
        from rivals.ui import main as run_ui

        run_ui()
        return 0
    except Exception:
        detail = traceback.format_exc()
        # A --windowed build has no console, so an early crash would otherwise
        # be completely silent.
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Rivals Macro failed to start", detail)
            root.destroy()
        except Exception:
            print(detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
