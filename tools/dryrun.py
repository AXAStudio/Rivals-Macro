"""Run the whole macro loop against the real captures, with input stubbed out.

The fake game serves tests/*.png as its screen and advances only when the macro
clicks the right pixels, so this exercises capture -> detect -> decide -> click
end to end on real game art. Nothing touches the real mouse or keyboard.

It covers what a single frame cannot show:

* the real end-of-round sequence — leaderboard, between-rounds, map vote — all
  of which must pass with **zero input**, because a click there votes for a map
  or leaves the server, and anything still swinging when the weapon select
  reopens picks a weapon for you;
* both Roblox dialogs on their real captures - Reconnect on "Disconnected",
  and Cancel (not Retry) on "Connection Failed", which are the same layout with
  different buttons;
* a frozen session, which must produce the Esc/L/Enter leave sequence and then
  a click on the Roblox game page;
* starting with the window unfocused, which must not swallow the first click.

    python tools/dryrun.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rivals import macro as macro_mod  # noqa: E402
from rivals.config import Config  # noqa: E402
from rivals.macro import MacroRunner  # noqa: E402
from rivals.vision import Frame  # noqa: E402

SHOTS = ROOT / "tests"
TEMPLATES = ROOT / "templates"

PLAY_RECT = (1144, 1168, 272, 78)
FFA_RECT = (345, 985, 1869, 450)
JOIN_RECT = (1416, 987, 136, 67)
GRID_RECT = (789, 567, 972, 370)
HOME_RECT = (1108, 336, 236, 48)
RECONNECT_RECT = (1285, 789, 175, 36)   # "Reconnect" on tests/disconnect.png
CANCEL_RECT = (1100, 789, 175, 36)      # "Cancel" on tests/restart.png

SETTLE = 0.8  # grace for a combat burst already in flight when a phase flips


def read(path: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


# phase -> what it shows and how it leaves. `click`: the macro must click in
# that rect. `after`: it advances on its own. `quiet`: any input is a bug.
PHASES = {
    "lobby":    dict(shot="1.png",      click=PLAY_RECT,      goto="modes",    event="play"),
    "modes":    dict(shot="2.png",      click=FFA_RECT,       goto="spectate", event="ffa"),
    "spectate": dict(shot="3.png",      click=JOIN_RECT,      goto="pick0",    event="join"),
    "pick0":    dict(shot="4.png",      click=GRID_RECT,      goto="pick1",    event="pick-primary"),
    "pick1":    dict(shot="5.png",      click=GRID_RECT,      goto="pick2",    event="pick-secondary"),
    "pick2":    dict(shot="6.png",      click=GRID_RECT,      goto="pick3",    event="pick-melee"),
    "pick3":    dict(shot="7.png",      click=GRID_RECT,      goto="combat",   event="pick-utility"),
    "combat":   dict(shot="alive.png",  after=2.0,            goto="died",     event="killed"),
    "died":     dict(shot="dead.png",   key="space",          goto="combatb",  event="respawned"),
    "combatb":  dict(shot="alive.png",  after=1.5,            goto="watching", event="put-in-spectate"),
    "watching": dict(shot="spectating.png", click=JOIN_RECT,   goto="after1",   still=True,
                     event="rejoined-from-spectate"),
    "after1":   dict(shot="after1.png", after=2.0, quiet=True, goto="after2",  event="leaderboard"),
    "after2":   dict(shot="after2.png", after=2.0, quiet=True, goto="after3",  event="between-rounds"),
    "after3":   dict(shot="after3.png", after=2.0, quiet=True, goto="pick0b",  event="map-vote"),
    "pick0b":   dict(shot="after4.png", click=GRID_RECT,      goto="pick1b",   event="pick-primary-2"),
    "pick1b":   dict(shot="5.png",      click=GRID_RECT,      goto="pick2b",   event="pick-secondary-2"),
    "pick2b":   dict(shot="6.png",      click=GRID_RECT,      goto="pick3b",   event="pick-melee-2"),
    "pick3b":   dict(shot="7.png",      click=GRID_RECT,      goto="combat2",  event="pick-utility-2"),
    "combat2":  dict(shot="alive.png",  after=2.0,            goto="dropped",  event="disconnected"),
    "dropped":  dict(shot="disconnect.png", click=RECONNECT_RECT, goto="failed",
                     event="reconnect-clicked"),
    "failed":   dict(shot="restart.png", click=CANCEL_RECT,    goto="home",     event="cancel-clicked"),
    "done":     dict(shot="1.png"),
    # killed during weapon select
    "deadpick": dict(shot="dead_in_picker.png", key="space", goto="repickd",
                     forbid_click=True, event="respawned-from-picker"),
    "repickd":  dict(shot="4.png",      click=GRID_RECT,      goto="done",     event="picked-after"),
    # loadout-assert scenario
    "wrong":    dict(shot="not_grenade_launcher.png", after=3.0, goto="dead2",
                     event="wrong-weapon-on-screen"),
    "dead2":    dict(shot="dead.png",   keys=("space", "m"), goto="repick",    event="space-then-M"),
    "repick":   dict(shot="4.png",      click=GRID_RECT,      goto="done",     event="repicked"),
    # frozen scenario
    "frozen":   dict(shot="8.png"),
    "home":     dict(shot="home.png",   click=HOME_RECT,      goto="done",     event="home-play"),
}


def inside(point, rect) -> bool:
    x, y = point
    left, top, w, h = rect
    return left <= x <= left + w and top <= y <= top + h


class FakeGame:
    def __init__(self, start: str) -> None:
        self.bgr: dict[str, np.ndarray] = {}
        for spec in PHASES.values():
            shot = spec["shot"]
            if shot not in self.bgr:
                self.bgr[shot] = read(SHOTS / shot)
        self.phase = start
        self.started = time.monotonic()
        self.events: list[str] = []
        self.keys: list[str] = []
        self.cursor = (1279, 719)
        self.jumps = self.shots = self.moves = 0
        self.combat_time = 0.0
        self.misclicks: list[str] = []
        self.noisy: list[str] = []
        self.clicks_while_unfocused: list[str] = []
        self.pressed: set[str] = set()
        self.window: "FakeWindow | None" = None
        self.alive = True
        self.focus_calls = 0

    def enter(self, phase: str) -> None:
        if self.phase in ("combat", "combat2"):
            self.combat_time += time.monotonic() - self.started
        self.phase = phase
        self.started = time.monotonic()

    def render(self) -> Frame:
        spec = PHASES[self.phase]
        if "after" in spec and time.monotonic() - self.started > spec["after"]:
            self.events.append(spec["event"])
            self.enter(spec["goto"])
            spec = PHASES[self.phase]
        return Frame(self.bgr[spec["shot"]].copy())

    def note_input(self, kind: str) -> None:
        spec = PHASES[self.phase]
        if not self.settled():
            return
        # `quiet`: nothing at all. `still`: no grinding, but a deliberate click
        # on the screen's own button is exactly what we want.
        if spec.get("quiet") or (spec.get("still") and kind in ("jump", "camera")):
            self.noisy.append(f"{kind} on {self.phase} +{time.monotonic() - self.started:.1f}s")

    def settled(self) -> bool:
        """A burst already in flight cannot stop the instant a phase flips."""
        return time.monotonic() - self.started > SETTLE

    def click(self, x: int, y: int) -> None:
        self.cursor = (x, y)
        self.note_input("click")
        if self.window is not None and not self.window.focused:
            self.clicks_while_unfocused.append(f"{self.phase} {(x, y)}")
            return
        spec = PHASES[self.phase]
        if spec.get("forbid_click") and self.settled():
            self.misclicks.append(f"{self.phase} clicked a card while dead {(x, y)}")
            return
        target = spec.get("click")
        if target is None:
            if self.phase in ("combat", "combat2"):
                self.shots += 1
            return
        if inside((x, y), target):
            self.events.append(spec["event"])
            self.enter(spec["goto"])
        elif self.settled():
            self.misclicks.append(f"{self.phase} {(x, y)}")

    def key(self, name: str) -> None:
        spec = PHASES[self.phase]
        wanted = spec.get("keys")
        if wanted is not None:
            self.pressed.add(name)
            if set(wanted) <= self.pressed:
                self.events.append(spec["event"])
                self.pressed.clear()
                self.enter(spec["goto"])
            return
        if spec.get("key") == name:
            self.events.append(spec["event"])
            self.enter(spec["goto"])
            return
        if name == "space":
            self.jumps += 1
            self.note_input("jump")
            return
        self.keys.append(name)
        self.note_input(f"key:{name}")
        if self.keys[-3:] == ["esc", "l", "enter"] and self.phase == "frozen":
            self.events.append("left-game")
            self.enter("home")


class FakeWindow:
    """Starts unfocused, exactly like a fresh press of Start."""

    def __init__(self, game: FakeGame) -> None:
        self.game = game
        self.focused = False
        self.focus_calls = 0

    def alive(self, hwnd):
        return hwnd is not None

    def find_game(self):
        return 1

    def is_foreground(self, hwnd):
        return self.focused

    def focus(self, hwnd):
        self.focus_calls += 1
        if not self.focused:
            self.game.events.append("focused")
        self.focused = True
        return True

    def describe(self, hwnd):
        return '"Roblox" 2559x1439 @ (0,0)'


class FakeScreen:
    index, left, top = 1, 0, 0
    width, height = 2559, 1439
    center = (1279, 719)

    def __init__(self, game: FakeGame) -> None:
        self.game = game

    def grab(self) -> Frame:
        return self.game.render()

    def close(self) -> None:
        pass


class FakeInputs:
    VK_F8, VK_F9 = 0x77, 0x78

    def __init__(self, game: FakeGame) -> None:
        self.game = game

    def glide_to(self, x, y, duration=0.0, steps=1):
        self.game.cursor = (x, y)

    def move_to(self, x, y):
        self.game.cursor = (x, y)

    def move_relative(self, dx, dy):
        if self.game.phase in ("combat", "combat2"):
            self.game.moves += 1
        self.game.note_input("camera")
        self.game.cursor = (self.game.cursor[0] + dx, self.game.cursor[1] + dy)

    def click(self, button="left", hold=None):
        self.game.click(*self.game.cursor)

    def click_at(self, x, y, glide=True):
        self.game.click(x, y)

    def scroll(self, notches):
        pass

    def key_tap(self, name, hold=None):
        self.game.key(name)

    def key_down(self, name):
        pass

    def key_up(self, name):
        pass

    def is_pressed(self, vk):
        return False


def play(start: str, until, label: str, timeout: float = 240.0,
         assert_interval: float = 600.0) -> FakeGame:
    game = FakeGame(start)
    win = FakeWindow(game)
    game.window = win
    macro_mod.Screen = lambda _idx: FakeScreen(game)  # type: ignore[assignment]
    macro_mod.inputs = FakeInputs(game)  # type: ignore[assignment]
    macro_mod.window = win  # type: ignore[assignment]

    cfg = Config()
    cfg.loadout = {"primary": "grenade_launcher", "secondary": "uzi",
                   "melee": "katana", "utility": "random"}
    cfg.space_interval = 0.25
    cfg.recover_after = 5.0  # 60 s in real use; shortened to keep the run short
    cfg.home_load_wait = 0.5
    cfg.assert_interval = assert_interval

    done = threading.Event()

    def log(message: str) -> None:
        print(f"    {message}")
        if until(game):
            done.set()

    print(f"--- {label} ---")
    runner = MacroRunner(cfg, log)
    runner.start()
    done.wait(timeout=timeout)
    runner.stop()
    runner.join(timeout=15)
    game.alive = runner.is_alive()
    game.focus_calls = win.focus_calls
    return game


def main() -> int:
    a = play("lobby", lambda g: "home-play" in g.events, "full round cycle")
    print("\nevents:", " -> ".join(a.events))
    hz = a.moves / a.combat_time if a.combat_time else 0.0
    print(f"jumps={a.jumps} shots={a.shots} camera={a.moves} "
          f"over {a.combat_time:.1f}s combat -> {hz:.0f} Hz")
    if a.noisy:
        print(f"INPUT ON A PASSIVE SCREEN ({len(a.noisy)}): {a.noisy[:6]}")
    if a.misclicks:
        print(f"MISCLICKS ({len(a.misclicks)}): {a.misclicks[:6]}")
    if a.clicks_while_unfocused:
        print(f"SWALLOWED ({len(a.clicks_while_unfocused)}): {a.clicks_while_unfocused[:4]}")

    print()
    d = play("deadpick", lambda g: "picked-after" in g.events,
             "killed during weapon select", timeout=60)
    print("\nevents:", " -> ".join(d.events), "| misclicks:", d.misclicks[:3])

    print()
    c = play("wrong", lambda g: "repicked" in g.events, "wrong loadout -> re-pick",
             timeout=120, assert_interval=1.0)
    print("\nevents:", " -> ".join(c.events))

    print()
    b = play("frozen", lambda g: "home-play" in g.events, "frozen session -> recovery", timeout=90)
    print("\nevents:", " -> ".join(b.events), "| keys:", b.keys)

    want = ["focused", "play", "ffa", "join", "pick-primary", "pick-secondary", "pick-melee",
            "pick-utility", "killed", "respawned", "put-in-spectate",
            "rejoined-from-spectate", "leaderboard",
            "between-rounds", "map-vote",
            "pick-primary-2", "pick-secondary-2", "pick-melee-2", "pick-utility-2",
            "disconnected", "reconnect-clicked", "cancel-clicked", "home-play"]
    checks = {
        "full cycle in the right order": a.events == want,
        "focused before the first click": a.events[:1] == ["focused"],
        "no click sent while unfocused": not a.clicks_while_unfocused,
        "no click landed off-target": not a.misclicks,
        "silent on leaderboard / map vote / spectating": not a.noisy,
        "rejoined instead of grinding while spectating":
            "rejoined-from-spectate" in a.events,
        "jumped during combat": a.jumps >= 4,
        "fired during combat": a.shots >= 2,
        "camera >= 30 Hz": hz >= 30,
        "re-picked a loadout after the round": "pick-utility-2" in a.events,
        "pressed Space to respawn when dead": "respawned" in a.events,
        "loadout check spotted the wrong weapon": "space-then-M" in c.events,
        "pressed M and re-picked the loadout": "repicked" in c.events,
        "respawned instead of clicking cards while dead":
            "respawned-from-picker" in d.events and not d.misclicks,
        "picked normally once back alive": "picked-after" in d.events,
        "clicked Reconnect on the disconnect dialog": "reconnect-clicked" in a.events,
        "clicked Cancel on connection-failed, not Retry": "cancel-clicked" in a.events,
        "frozen session left with Esc/L/Enter": b.keys[-3:] == ["esc", "l", "enter"],
        "then clicked Play on the game page": "home-play" in b.events,
        "threads exited cleanly": not a.alive and not b.alive and not c.alive and not d.alive,
    }
    print()
    for label, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if a.events != want:
        missing = [e for e in want if e not in a.events]
        print(f"       missing {missing}" if missing else f"       got {a.events}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
