"""Persisted settings for the macro."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

def _bundled_root() -> Path:
    """Where read-only data lives: the source tree, or the unpacked bundle."""
    if getattr(sys, "frozen", False):
        # PyInstaller unpacks --add-data here (a temp dir for a one-file build,
        # the app folder for one-dir). Either way it is read-only.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _writable_root() -> Path:
    """Where settings live: beside the .exe, so they survive and can be found.

    Falls back to LOCALAPPDATA when that folder is read-only - dropping the
    .exe in Program Files would otherwise make saving fail silently.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent
    beside = Path(sys.executable).resolve().parent
    probe = beside / ".write-test"
    try:
        probe.touch()
        probe.unlink()
        return beside
    except OSError:
        fallback = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")
        ) / "RivalsMacro"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


ROOT = _bundled_root()
TEMPLATES_DIR = ROOT / "templates"
CONFIG_PATH = _writable_root() / "config.json"

# Loadout slot values are either a weapon key ("sniper"), "random" (click the
# Random card) or "any" (click whichever card of that slot shows up first).
DEFAULT_LOADOUT = {
    "primary": "grenade_launcher",
    "secondary": "random",
    "melee": "random",
    "utility": "random",
}

# Built-in loadouts. Users can save their own alongside these; those live in
# Config.presets and are merged over the top by name.
BUILTIN_PRESETS = {
    "Glass Grind": {
        "primary": "grenade_launcher",
        "secondary": "random",
        "melee": "random",
        "utility": "random",
    },
    "All Random": {slot: "random" for slot in DEFAULT_LOADOUT},
    "Anything Offered": {slot: "any" for slot in DEFAULT_LOADOUT},
}


@dataclass
class Config:
    # --- capture / vision -------------------------------------------------
    monitor: int = 1
    # Measured over tests/: worst true positive 0.908, loudest false 0.755.
    threshold: float = 0.85  # scene sentinels
    weapon_threshold: float = 0.78  # a named weapon inside the grid
    # Second chance when that misses: score the card against each cell alone
    # and take the winner only if it stands clear of the runner-up. Rescues
    # cards a neighbouring 3D model overlaps (paintball_gun: 0.730, margin
    # 0.248) without letting near-ties through (rpg: 0.690, margin 0.041).
    argmax_floor: float = 0.68
    argmax_margin: float = 0.15

    # --- loadout ----------------------------------------------------------
    loadout: dict = field(default_factory=lambda: dict(DEFAULT_LOADOUT))
    preset: str = "Glass Grind"
    lifetime_grind_seconds: float = 0.0  # carried across sessions
    presets: dict = field(default_factory=dict)  # user-saved, name -> loadout

    # --- combat -----------------------------------------------------------
    camera_mode: str = "relative"  # "relative" (FPS mouse lock) or "absolute"
    space_interval: float = 0.5  # jump spam period, seconds
    space_jitter: float = 0.08
    click_min: float = 0.25
    click_max: float = 0.80
    oval_rx: float = 0.14  # oval radii as a fraction of screen w/h
    oval_ry: float = 0.07
    oval_speed: float = 1.1  # radians per second, re-randomised periodically
    click_box_w: float = 0.35  # random-click box as a fraction of screen w/h
    click_box_h: float = 0.35

    # --- flow -------------------------------------------------------------
    scroll_notches: int = 3  # wheel notches per scroll burst
    scroll_bursts: int = 14  # how many bursts before giving up on the ffa card
    slot_timeout: float = 8.0  # how long to wait for the picker to advance
    # The 0.5 s regional probe covers the urgent cases (leaderboard, respawn),
    # so the full scan - ~310 ms across five sentinels - can be infrequent.
    combat_check_interval: float = 2.5

    # --- end of round ------------------------------------------------------
    # The round ends straight into the weapon select, with no spectator screen
    # in between, so any stray click or jump lands on a weapon card. Once the
    # clock proves this many seconds or fewer remain, all input stops.
    hold_seconds: int = 10
    hold_poll: float = 0.25
    probe_interval: float = 0.5  # cheap round-end / death / spectate check
    dialog_probe_interval: float = 1.5  # disconnect dialogs, less urgent

    # --- loadout assert --------------------------------------------------
    # Periodically confirm the primary you asked for is the one actually in
    # hand. Needs a `hud_<weapon>.png` name plate template; only the grenade
    # launcher has one so far, so any other primary skips the check.
    assert_loadout: bool = True
    assert_interval: float = 600.0
    assert_threshold: float = 0.70

    # --- focus ---------------------------------------------------------------
    # A click into an unfocused window is swallowed activating it, so the first
    # click after Start would otherwise do nothing.
    focus_game: bool = True
    focus_settle: float = 0.35  # let the window settle before clicking into it

    # --- recovery ----------------------------------------------------------
    enable_recovery: bool = True
    recover_after: float = 60.0  # unrecognised, out of a match, for this long
    home_load_wait: float = 8.0  # after clicking Play on the Roblox game page

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        cfg = cls()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return cfg
            known = {f.name for f in fields(cls)}
            for key, value in raw.items():
                if key in known:
                    setattr(cfg, key, value)
            merged = dict(DEFAULT_LOADOUT)
            merged.update(cfg.loadout or {})
            cfg.loadout = merged
        return cfg

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
