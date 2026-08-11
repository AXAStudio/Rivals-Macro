"""Read the match timer in the top-centre pill.

Segmentation rather than OCR: the glyphs are white on a dark pill, so a
brightness threshold plus connected components gives exactly four tall blobs —
the hourglass icon and the three digits of M:SS. The leftmost is the hourglass;
the rest are the digits, normalised to a canonical size and correlated against
glyph templates cut from `tests/3.png` and `tests/8.png`.

Only 0/1/2/5 have templates so far, since those are the only digits the
captures contain. That is enough for the job it does — proving the round is
nearly over needs the minutes digit to read 0 — and an unrecognised digit
simply reports "unknown", which the caller treats as "keep playing".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .vision import Frame

GLYPH_SIZE = (24, 32)  # canonical width, height every glyph is scaled to
REFERENCE_HEIGHT = 1439

# Search band for the pill, as fractions of the frame.
BAND_HALF_W = 0.065
BAND_TOP = 0.012
BAND_BOTTOM = 0.058

WHITE_FLOOR = 190
MIN_AREA = 40  # at the reference height; scaled with the frame
TALL_RATIO = 0.7  # a glyph is "tall" relative to the tallest blob found

MATCH_FLOOR = 0.75
MATCH_MARGIN = 0.20


@dataclass
class Clock:
    minutes: int | None
    tens: int | None
    ones: int | None

    @property
    def text(self) -> str:
        show = lambda d: "?" if d is None else str(d)  # noqa: E731
        return f"{show(self.minutes)}:{show(self.tens)}{show(self.ones)}"

    @property
    def upper_bound(self) -> int | None:
        """Worst-case seconds left, or None if it cannot be bounded."""
        if self.minutes is None or self.tens is None:
            return None
        total = self.minutes * 60 + self.tens * 10
        return total + (9 if self.ones is None else self.ones)


class ClockReader:
    def __init__(self, templates_dir: Path) -> None:
        self.glyphs: dict[int, np.ndarray] = {}
        for path in sorted(Path(templates_dir).glob("digit_*.png")):
            try:
                value = int(path.stem.split("_")[1])
            except (IndexError, ValueError):
                continue
            image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is not None:
                self.glyphs[value] = cv2.resize(
                    image, GLYPH_SIZE, interpolation=cv2.INTER_AREA
                ).astype(np.float32)

    @property
    def known_digits(self) -> list[int]:
        return sorted(self.glyphs)

    def band(self, frame: Frame) -> tuple[int, int, int, int]:
        cx = frame.width // 2
        half = int(frame.width * BAND_HALF_W)
        return (
            max(0, cx - half),
            int(frame.height * BAND_TOP),
            min(frame.width, cx + half),
            int(frame.height * BAND_BOTTOM),
        )

    def read(self, frame: Frame) -> Clock | None:
        if not self.glyphs:
            return None
        x0, y0, x1, y1 = self.band(frame)
        patch = frame.gray[y0:y1, x0:x1]
        if patch.size == 0:
            return None

        white = cv2.inRange(patch, WHITE_FLOOR, 255)
        count, _, stats, _ = cv2.connectedComponentsWithStats(white)
        floor = MIN_AREA * (frame.height / REFERENCE_HEIGHT) ** 2
        blobs = [stats[i] for i in range(1, count) if stats[i][cv2.CC_STAT_AREA] >= floor]
        if not blobs:
            return None

        tallest = max(b[cv2.CC_STAT_HEIGHT] for b in blobs)
        tall = sorted(
            (b for b in blobs if b[cv2.CC_STAT_HEIGHT] >= TALL_RATIO * tallest),
            key=lambda b: b[cv2.CC_STAT_LEFT],
        )
        if len(tall) != 4:  # hourglass + M + S + S, or this is not the clock
            return None

        values = []
        for blob in tall[1:]:
            x, y = blob[cv2.CC_STAT_LEFT], blob[cv2.CC_STAT_TOP]
            w, h = blob[cv2.CC_STAT_WIDTH], blob[cv2.CC_STAT_HEIGHT]
            values.append(self._classify(white[y : y + h, x : x + w]))
        return Clock(*values)

    def _classify(self, glyph: np.ndarray) -> int | None:
        if glyph.size == 0:
            return None
        candidate = cv2.resize(glyph, GLYPH_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
        scores = sorted(
            (
                (float(cv2.matchTemplate(candidate, ref, cv2.TM_CCOEFF_NORMED)[0, 0]), value)
                for value, ref in self.glyphs.items()
            ),
            reverse=True,
        )
        best = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else 0.0
        if best[0] < MATCH_FLOOR or best[0] - runner_up < MATCH_MARGIN:
            return None
        return best[1]


SIGNATURE_SIZE = (48, 12)
SIGNATURE_TOLERANCE = 0.5


def band_signature(reader: "ClockReader", frame: Frame) -> np.ndarray:
    """Fingerprint of the clock strip, for proving the match is still running.

    Deliberately not digit based: only 0/1/2/5 have glyph templates, so a clock
    reading 3:47 comes back as "?:??" every poll and would look frozen. Pixels
    do not care which digits they are — a single ones-digit tick moves this by
    ~1.0 against 0.00 for an identical frame.
    """
    x0, y0, x1, y1 = reader.band(frame)
    patch = frame.gray[y0:y1, x0:x1]
    if patch.size == 0:
        return np.zeros(SIGNATURE_SIZE[::-1], dtype=np.float32)
    return cv2.resize(patch, SIGNATURE_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)


def moved(before: np.ndarray | None, after: np.ndarray) -> bool:
    if before is None or before.shape != after.shape:
        return True
    return float(np.abs(before - after).mean()) > SIGNATURE_TOLERANCE


def near_end(clock: Clock | None, seconds: int) -> bool:
    """True only when the clock *proves* the round has `seconds` or less left."""
    if clock is None:
        return False
    bound = clock.upper_bound
    return bound is not None and bound <= seconds
