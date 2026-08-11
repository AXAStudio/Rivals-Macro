"""The loadout picker: grid geometry, cell state, and which slot is active.

Everything is derived from one anchor — the Random card, which is always the
top-left cell and matched 0.908-0.927 on every picker capture in `tests/`.
That beats hunting each weapon template across the whole screen, which
false-positives badly (`weapon_melee_maul` scores 0.824 on the melee screen at
a spot that is not even a card).

Geometry ratios are measured from `tests/4-7.png`: cards 168 px at scale 1.27,
grid spacing 202 px, slot indicator row 185 px above the anchor.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .vision import Frame, Match, Rect

GRID_COLS = 5
GRID_ROWS = 2

SPACING_RATIO = 1.20  # grid spacing / card size
SLOT_ROW_DY = -0.916  # slot indicator row offset, in grid spacings
SLOT_BOX_SPACING = 0.665
SLOT_BOX_SIZE = 0.614

# Cell classification. Measured spread is enormous, so these sit mid-gap:
# locked cards score redness 109-113 against <= 19 for anything else, and
# empty cells score grey std 0.8-2.5 against >= 45 for a real card.
LOCKED_REDNESS = 60.0
EMPTY_STD = 10.0

AVAILABLE, LOCKED, EMPTY = "available", "locked", "empty"


@dataclass
class Cell:
    index: int
    row: int
    col: int
    x: int
    y: int
    state: str

    @property
    def clickable(self) -> bool:
        return self.state == AVAILABLE


@dataclass
class Grid:
    x: int
    y: int
    cell_size: int
    spacing: int
    cells: list[Cell]

    def bbox(self, margin: float = 0.15) -> Rect:
        pad = int(self.cell_size * (0.5 + margin))
        left = self.x - pad
        top = self.y - pad
        width = (GRID_COLS - 1) * self.spacing + 2 * pad
        height = (GRID_ROWS - 1) * self.spacing + 2 * pad
        return left, top, width, height

    def nearest(self, x: int, y: int) -> Cell | None:
        """Snap a point to a cell, but only if it really lands on one."""
        limit = self.spacing * 0.55
        best, best_d = None, None
        for cell in self.cells:
            d = max(abs(cell.x - x), abs(cell.y - y))
            if best_d is None or d < best_d:
                best, best_d = cell, d
        if best is None or best_d > limit:
            return None
        return best

    def available(self) -> list[Cell]:
        return [c for c in self.cells if c.clickable]

    def weapon_cells(self) -> list[Cell]:
        """Clickable cells excluding the Random card at index 0."""
        return [c for c in self.cells if c.clickable and c.index != 0]


def classify(frame: Frame, cx: int, cy: int, size: int) -> str:
    patch = frame.cell(cx, cy, size)
    if patch.size == 0:
        return EMPTY
    inner = patch[size // 6 : size - size // 6, size // 6 : size - size // 6]
    if inner.size == 0:
        inner = patch
    b, g, r = (inner[:, :, i].astype(np.float32).mean() for i in range(3))
    if r - max(g, b) > LOCKED_REDNESS:
        return LOCKED
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    if float(gray.std()) < EMPTY_STD:
        return EMPTY
    return AVAILABLE


def build(frame: Frame, anchor: Match, cell_size: int | None = None) -> Grid:
    """Lay the 5x2 grid out from a Random-card match and classify every cell.

    Pass `cell_size` from Matcher.nominal_size: the anchor match's own size
    carries the fine sweep's +/-3% wobble, which multiplies into a 25 px error
    by the fifth column.
    """
    cell_size = cell_size or max(anchor.width, anchor.height)
    spacing = int(round(cell_size * SPACING_RATIO))
    ax, ay = anchor.center
    cells: list[Cell] = []
    for index in range(GRID_COLS * GRID_ROWS):
        row, col = divmod(index, GRID_COLS)
        x = ax + col * spacing
        y = ay + row * spacing
        cells.append(Cell(index, row, col, x, y, classify(frame, x, y, cell_size)))
    return Grid(ax, ay, cell_size, spacing, cells)


def slot_index(frame: Frame, grid: Grid) -> int | None:
    """Which of the four slots the picker is on, from the highlighted box.

    The four boxes sit centred above the grid and exactly one carries a thick
    white border. Its x position gives the slot outright, so the macro never
    has to assume the stage order held.
    """
    box = grid.spacing * SLOT_BOX_SIZE
    row_y = int(round(grid.y + SLOT_ROW_DY * grid.spacing))
    spacing = grid.spacing * SLOT_BOX_SPACING
    center_x = grid.x + (GRID_COLS - 1) * grid.spacing / 2.0

    half_h = int(round(box * 0.62))
    half_w = int(round(spacing * 2.7))
    region = (int(center_x - half_w), row_y - half_h, 2 * half_w, 2 * half_h)
    ox, oy = frame.origin
    x0 = max(0, region[0] - ox)
    y0 = max(0, region[1] - oy)
    band = frame.bgr[y0 : y0 + region[3], x0 : x0 + region[2]]
    if band.size == 0:
        return None

    white = cv2.inRange(band, (205, 205, 205), (255, 255, 255))
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(white)
    floor = box * box * 0.15
    blobs = [stats[i] for i in range(1, count) if stats[i][cv2.CC_STAT_AREA] >= floor]
    if not blobs:
        return None
    blob = max(blobs, key=lambda s: s[cv2.CC_STAT_AREA])
    cx = ox + x0 + blob[cv2.CC_STAT_LEFT] + blob[cv2.CC_STAT_WIDTH] / 2.0
    index = int(round((cx - center_x) / spacing + 1.5))
    return index if 0 <= index <= 3 else None


def signature(frame: Frame, grid: Grid) -> np.ndarray:
    """Tiny fingerprint of the grid area, for detecting the stage advancing."""
    left, top, width, height = grid.bbox()
    ox, oy = frame.origin
    x0 = max(0, left - ox)
    y0 = max(0, top - oy)
    crop = frame.gray[y0 : y0 + height, x0 : x0 + width]
    if crop.size == 0:
        return np.zeros((8, 8), dtype=np.float32)
    return cv2.resize(crop, (24, 12), interpolation=cv2.INTER_AREA).astype(np.float32)


def changed(before: np.ndarray, after: np.ndarray, tolerance: float = 6.0) -> bool:
    if before.shape != after.shape:
        return True
    return float(np.abs(before - after).mean()) > tolerance
