"""Work out which screen we are looking at.

Each scene has one sentinel template. Measured across all eight captures in
`tests/`, the worst true positive is 0.908 and the loudest false positive is
0.755, so a threshold of 0.85 separates them with room on both sides.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import picker
from .picker import Grid
from .vision import Frame, Match, Matcher

LOBBY = "lobby"
MODES = "modes"
SPECTATE = "spectate"
PICKER = "picker"
HOME = "home"
LEADERBOARD = "leaderboard"
INTERMISSION = "intermission"
MAPVOTE = "mapvote"
RECONNECT = "reconnect"
CONNFAIL = "connfail"
PLAYING = "playing"
UNKNOWN = "unknown"

PLAY_BUTTON = "play_button"
FFA = "ffa"
JOIN_BUTTON = "join_button"
RANDOM_CARD = "random_card"
HOME_PLAY = "home_play"
LEAVE_BUTTON = "leave_button"
TOHUB_BUTTON = "tohub_button"
MAPVOTE_BANNER = "mapvote_banner"
RECONNECT_BUTTON = "reconnect"
CONNFAIL_TITLE = "connfail_title"
CANCEL_BUTTON = "cancel_button"
HEALTH_BAR = "health_bar"
RESPAWN = "respawn"

SCENE_OF = {
    RANDOM_CARD: PICKER,
    PLAY_BUTTON: LOBBY,
    JOIN_BUTTON: SPECTATE,
    FFA: MODES,
    HOME_PLAY: HOME,
    LEAVE_BUTTON: LEADERBOARD,
    TOHUB_BUTTON: INTERMISSION,
    MAPVOTE_BANNER: MAPVOTE,
    RECONNECT_BUTTON: RECONNECT,
    CONNFAIL_TITLE: CONNFAIL,
    HEALTH_BAR: PLAYING,
}
# One list, checked in order, for every situation.
#
# There used to be a shorter mid-match list to save time. It cost two bugs:
# it omitted `home_play`, so a drop to the game page mid-match was invisible,
# and it omitted `join_button`, so spectating - which draws a full match HUD,
# health bar and all - read as being alive and the macro ground away at a game
# it was only watching. A second ordering that can silently disagree with the
# first is not worth the milliseconds.
#
# Order is by frequency, subject to one hard rule: anything that can coexist
# with the match HUD must come before `health_bar`. Spectating demonstrably
# does. The weapon select does not in any capture on hand, but dying mid-select
# could put both on screen at once, and picking must win over grinding - so it
# goes first on principle rather than on evidence.
ALL_SENTINELS = (
    JOIN_BUTTON,
    RANDOM_CARD,
    HEALTH_BAR,
    LEAVE_BUTTON,
    PLAY_BUTTON,
    TOHUB_BUTTON,
    MAPVOTE_BANNER,
    RECONNECT_BUTTON,
    CONNFAIL_TITLE,
    HOME_PLAY,
    FFA,
)

# Screens between the end of a round and the next weapon select. The game
# drives itself through these; anything we click here votes for a map or
# leaves the server, so the macro sits still.
PASSIVE = frozenset({LEADERBOARD, INTERMISSION, MAPVOTE})

# A plain dark rounded rectangle carries little structure, so an uncalibrated
# wide scale sweep can find something close enough in the HUD - it hit 0.880 on
# the health bar in tests/8.png. At the right scale a real dialog scores 1.000
# against a worst-case 0.797 elsewhere, so the bar can afford to be high.
SENTINEL_FLOOR = {RECONNECT_BUTTON: 0.90}

# "Cancel" and "Leave" are the same outlined button with a different word:
# the cancel template scores 0.893 against Leave on the disconnect dialog.
# So the connection-failed dialog is identified by its *title*, and the
# Cancel button is only ever hunted inside that dialog.

# ...and it must never be the template that locks the scale for everything else.
NEVER_CALIBRATE = frozenset({RECONNECT_BUTTON})

DIALOGS = frozenset({RECONNECT, CONNFAIL})

SCENE_LABELS = {
    LOBBY: "Lobby",
    MODES: "Mode list",
    SPECTATE: "Spectating",
    PICKER: "Loadout picker",
    HOME: "Roblox game page",
    LEADERBOARD: "Match over - leaderboard",
    INTERMISSION: "Between rounds",
    MAPVOTE: "Map vote",
    RECONNECT: "Disconnected",
    CONNFAIL: "Connection failed",
    PLAYING: "In a match",
    UNKNOWN: "In match / unrecognised",
}


@dataclass
class Scene:
    name: str
    match: Match | None = None
    grid: Grid | None = None
    slot: int | None = None
    alive: bool | None = None  # only meaningful for PLAYING

    def __bool__(self) -> bool:
        return self.name != UNKNOWN


class Detector:
    def __init__(self, matcher: Matcher, threshold: float = 0.85) -> None:
        self.matcher = matcher
        self.threshold = threshold

    def identify(self, frame: Frame, sentinels: tuple[str, ...] = ALL_SENTINELS) -> Scene:
        for name in sentinels:
            floor = max(self.threshold, SENTINEL_FLOOR.get(name, 0.0))
            hit = self.matcher.find(frame, name, floor, lock=name not in NEVER_CALIBRATE)
            if hit is None:
                continue
            scene = SCENE_OF[name]
            if scene == PICKER:
                grid = picker.build(frame, hit, self.matcher.nominal_size(RANDOM_CARD))
                return Scene(PICKER, hit, grid, picker.slot_index(frame, grid))
            if scene == PLAYING:
                # The health bar says a match HUD is up; the Respawn button
                # says we are looking at it from the grave.
                dead = self.matcher.find(frame, RESPAWN, self.threshold) is not None
                return Scene(PLAYING, hit, alive=not dead)
            return Scene(scene, hit)
        return Scene(UNKNOWN)

    def find_ffa(self, frame: Frame) -> Match | None:
        return self.matcher.find(frame, FFA, self.threshold)
