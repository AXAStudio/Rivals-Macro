"""The activity graph: a grinding band with spikes where things interrupted it."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import stats as stats_mod
from . import theme
from .stats import Snapshot, human

PAD_X = 10
PAD_TOP = 10
AXIS_H = 16

SPIKE = {
    stats_mod.ROUND: (theme.WARN, "round over"),
    stats_mod.DROP: (theme.BAD, "disconnected"),
    stats_mod.RECOVER: (theme.BAD, "relaunched"),
    stats_mod.LOADOUT: (theme.ACCENT, "weapon select"),
}
BAND = "#1f7a4d"  # grinding fill
BAND_EDGE = theme.GOOD
TRACK = "#14171e"


class Timeline(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, style="Card.TFrame")
        head = ttk.Frame(self, style="Card.TFrame")
        head.pack(fill="x")
        theme.section(head, "Activity").pack(side="left")
        self.summary = ttk.Label(head, text="", style="Dim.TLabel")
        self.summary.pack(side="right")

        stat_row = ttk.Frame(self, style="Card.TFrame")
        stat_row.pack(fill="x", pady=(8, 8))
        self.tiles: dict[str, ttk.Label] = {}
        for key, caption in (
            ("grind", "Grinding"), ("session", "Session"),
            ("share", "In game"), ("rounds", "Rounds"), ("drops", "Interruptions"),
        ):
            cell = ttk.Frame(stat_row, style="Card.TFrame")
            cell.pack(side="left", padx=(0, 22))
            value = ttk.Label(cell, text="—", style="Status.TLabel")
            value.pack(anchor="w")
            ttk.Label(cell, text=caption, style="Dim.TLabel").pack(anchor="w")
            self.tiles[key] = value

        self.canvas = tk.Canvas(
            self, height=96, bg=theme.BG, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.render(self._last))
        self._last: Snapshot | None = None

    # -- drawing -----------------------------------------------------------

    def render(self, snap: Snapshot | None) -> None:
        self._last = snap
        c = self.canvas
        c.delete("all")
        width = c.winfo_width()
        height = c.winfo_height()
        if width < 40 or height < 30:
            return

        left, right = PAD_X, width - PAD_X
        baseline = height - AXIS_H
        top = PAD_TOP
        span_w = max(1, right - left)

        c.create_rectangle(left, top, right, baseline, fill=TRACK, outline="")
        if snap is None or snap.window <= 0:
            c.create_text(width // 2, (top + baseline) // 2, text="no activity yet",
                          fill=theme.DIM, font=theme.FONT_SMALL)
            return

        def x_of(seconds_ago: float) -> float:
            frac = 1.0 - min(max(seconds_ago / snap.window, 0.0), 1.0)
            return left + frac * span_w

        # time gridlines
        step = self._grid_step(snap.window)
        marker = step
        while marker < snap.window:
            x = x_of(marker)
            c.create_line(x, top, x, baseline, fill=theme.BORDER)
            c.create_text(x, baseline + AXIS_H / 2, text=f"-{self._tick_label(marker)}",
                          fill=theme.DIM, font=("Segoe UI", 7))
            marker += step
        c.create_text(right - 2, baseline + AXIS_H / 2, text="now", anchor="e",
                      fill=theme.DIM, font=("Segoe UI", 7))

        # grinding band across the lower half
        band_top = baseline - (baseline - top) * 0.42
        for start_ago, end_ago in snap.spans:
            x0, x1 = x_of(start_ago), x_of(end_ago)
            if x1 - x0 < 1:
                x1 = x0 + 1
            c.create_rectangle(x0, band_top, x1, baseline, fill=BAND, outline="")
            c.create_line(x0, band_top, x1, band_top, fill=BAND_EDGE)

        # spikes for the things that interrupted it
        for seconds_ago, kind, label in snap.marks:
            colour = SPIKE.get(kind, (theme.MUTED, kind))[0]
            x = x_of(seconds_ago)
            c.create_line(x, top + 4, x, baseline, fill=colour, width=2)
            c.create_oval(x - 3, top + 1, x + 3, top + 7, fill=colour, outline="")

        c.create_line(left, baseline, right, baseline, fill=theme.BORDER)

        share = (snap.grind_seconds / snap.session_seconds * 100) if snap.session_seconds else 0
        self.tiles["grind"].configure(text=human(snap.grind_seconds))
        self.tiles["session"].configure(text=human(snap.session_seconds))
        self.tiles["share"].configure(text=f"{share:.0f}%")
        self.tiles["rounds"].configure(text=str(snap.rounds))
        self.tiles["drops"].configure(text=str(snap.drops))
        self.summary.configure(
            text=f"last {self._tick_label(snap.window)}     "
                 f"lifetime {human(snap.lifetime_seconds)}"
        )

    @staticmethod
    def _grid_step(window: float) -> float:
        for step in (60, 120, 300, 600, 900, 1800):
            if window / step <= 6:
                return float(step)
        return 3600.0

    @staticmethod
    def _tick_label(seconds: float) -> str:
        if seconds >= 3600:
            return f"{seconds / 3600:.0f}h"
        if seconds >= 60:
            return f"{seconds / 60:.0f}m"
        return f"{seconds:.0f}s"


def legend(parent: tk.Widget) -> ttk.Frame:
    """Colour key for the spikes."""
    row = ttk.Frame(parent, style="Card.TFrame")
    entries = [(BAND_EDGE, "grinding")] + [
        (colour, text) for colour, text in SPIKE.values()
    ]
    for colour, text in entries:
        cell = ttk.Frame(row, style="Card.TFrame")
        cell.pack(side="left", padx=(0, 14))
        tk.Label(cell, text="■", fg=colour, bg=theme.CARD, font=("Segoe UI", 8)).pack(side="left")
        ttk.Label(cell, text=text, style="Dim.TLabel").pack(side="left", padx=(4, 0))
    return row
