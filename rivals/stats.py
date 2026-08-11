"""Session bookkeeping for the activity graph.

Grinding time means time *in a match, actually playing* — the combat loop
running. Lobby, mode list, loadout picking, leaderboards and dialogs all sit
outside it, which is the number worth watching for a grind.

The macro thread writes, the UI thread reads, so everything goes through one
lock and `snapshot` hands back a plain immutable view.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# Event kinds the timeline draws as spikes.
ROUND = "round"  # a match finished
DROP = "drop"  # disconnected / connection failed
RECOVER = "recover"  # had to leave and relaunch
LOADOUT = "loadout"  # weapon select opened

MAX_MARKS = 600


@dataclass(frozen=True)
class Mark:
    at: float
    kind: str
    label: str


@dataclass(frozen=True)
class Snapshot:
    now: float
    window: float  # seconds of history the view covers
    spans: tuple[tuple[float, float], ...] = ()  # grinding, seconds before now
    marks: tuple[tuple[float, str, str], ...] = ()  # (seconds before now, kind, label)
    grind_seconds: float = 0.0
    session_seconds: float = 0.0
    lifetime_seconds: float = 0.0
    rounds: int = 0
    drops: int = 0
    grinding: bool = False


def human(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class Stats:
    def __init__(self, lifetime_seconds: float = 0.0) -> None:
        self._lock = threading.Lock()
        self._lifetime_base = float(lifetime_seconds)
        self._spans: list[list[float]] = []  # [start, end or -1 while open]
        self._marks: list[Mark] = []
        self.session_start = time.monotonic()

    # -- writes (macro thread) --------------------------------------------

    def grinding(self, on: bool) -> None:
        now = time.monotonic()
        with self._lock:
            open_span = self._spans[-1] if self._spans and self._spans[-1][1] < 0 else None
            if on and open_span is None:
                self._spans.append([now, -1.0])
            elif not on and open_span is not None:
                open_span[1] = now

    def mark(self, kind: str, label: str) -> None:
        with self._lock:
            self._marks.append(Mark(time.monotonic(), kind, label))
            if len(self._marks) > MAX_MARKS:
                del self._marks[: len(self._marks) - MAX_MARKS]

    def reset_session(self) -> None:
        with self._lock:
            self._lifetime_base += self._grind_locked(time.monotonic())
            self._spans.clear()
            self._marks.clear()
            self.session_start = time.monotonic()

    # -- reads (UI thread) -------------------------------------------------

    def _grind_locked(self, now: float) -> float:
        return sum((end if end >= 0 else now) - start for start, end in self._spans)

    @property
    def lifetime_seconds(self) -> float:
        with self._lock:
            return self._lifetime_base + self._grind_locked(time.monotonic())

    def snapshot(self, window: float | None = None) -> Snapshot:
        now = time.monotonic()
        with self._lock:
            session = now - self.session_start
            if window is None:
                # Grow with the session, then scroll once it passes an hour.
                window = max(300.0, min(3600.0, session))
            floor = now - window
            spans = []
            for start, end in self._spans:
                stop = end if end >= 0 else now
                if stop < floor:
                    continue
                spans.append((now - max(start, floor), now - stop))
            marks = tuple(
                (now - m.at, m.kind, m.label) for m in self._marks if m.at >= floor
            )
            grind = self._grind_locked(now)
            return Snapshot(
                now=now,
                window=window,
                spans=tuple(spans),
                marks=marks,
                grind_seconds=grind,
                session_seconds=session,
                lifetime_seconds=self._lifetime_base + grind,
                rounds=sum(1 for m in self._marks if m.kind == ROUND),
                drops=sum(1 for m in self._marks if m.kind in (DROP, RECOVER)),
                grinding=bool(self._spans and self._spans[-1][1] < 0),
            )
