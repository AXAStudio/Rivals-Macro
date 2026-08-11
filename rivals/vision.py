"""Screen capture and template matching.

Two things make this work on real captures:

*Per-template reference heights.* The templates were not all cropped from the
same size capture — measured against `tests/`, the weapon cards peak at scale
1.25-1.28 on a 1439-tall screen while `ffa.png` peaks at 1.34. One global scale
cannot serve both, so each template carries the screen height its pixels are
native to (`templates/manifest.json`) and its own base scale is derived from
that.

*A single global correction `k`.* Whatever the operator's window size is, it
shifts every template by the same factor. The first successful match locks `k`;
after that each search only tries +/-3% around it instead of the full sweep.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import mss
import numpy as np

DEFAULT_REFERENCE = 1133

# Multipliers on a template's base scale. Wide until `k` is known, then narrow.
WIDE_K = (1.0, 0.95, 1.05, 0.90, 1.10, 0.85, 1.15, 0.80, 1.20, 1.26, 0.75, 0.70)
FINE_K = (1.0, 0.97, 1.03)

COARSE_DOWNSCALE = 0.5
Rect = tuple[int, int, int, int]  # left, top, width, height, absolute coords


@dataclass
class Match:
    name: str
    score: float
    left: int
    top: int
    width: int
    height: int
    scale: float

    @property
    def center(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2

    @property
    def rect(self) -> Rect:
        return self.left, self.top, self.width, self.height


class Frame:
    """One capture: colour for cell classification, grey for matching."""

    def __init__(self, bgr: np.ndarray, origin: tuple[int, int] = (0, 0)) -> None:
        self.bgr = bgr
        self.gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        self.origin = origin
        self._small: np.ndarray | None = None
        self._edges: np.ndarray | None = None

    @property
    def width(self) -> int:
        return self.gray.shape[1]

    @property
    def height(self) -> int:
        return self.gray.shape[0]

    def small(self) -> np.ndarray:
        if self._small is None:
            self._small = cv2.resize(
                self.gray, None, fx=COARSE_DOWNSCALE, fy=COARSE_DOWNSCALE,
                interpolation=cv2.INTER_AREA,
            )
        return self._small

    def edges(self) -> np.ndarray:
        """Sobel magnitude, for templates whose background is the game world.

        A HUD element drawn over gameplay sits on whatever happens to be
        behind it, which flips local contrast and wrecks intensity matching.
        Gradients care about the element's shape, not what shows through.
        """
        if self._edges is None:
            gx = cv2.Sobel(self.gray, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(self.gray, cv2.CV_32F, 0, 1, ksize=3)
            self._edges = cv2.magnitude(gx, gy)
        return self._edges

    def view(self, region: Rect | None) -> tuple[np.ndarray, tuple[int, int], float]:
        """Grey pixels to search, their absolute origin, and their downscale."""
        if region is None:
            return self.small(), self.origin, COARSE_DOWNSCALE
        ox, oy = self.origin
        x0 = max(0, region[0] - ox)
        y0 = max(0, region[1] - oy)
        x1 = min(self.width, x0 + region[2])
        y1 = min(self.height, y0 + region[3])
        return self.gray[y0:y1, x0:x1], (ox + x0, oy + y0), 1.0

    def cell(self, cx: int, cy: int, size: int) -> np.ndarray:
        """Colour crop centred on an absolute point, clipped to the frame."""
        ox, oy = self.origin
        half = size // 2
        x0 = max(0, cx - ox - half)
        y0 = max(0, cy - oy - half)
        return self.bgr[y0 : y0 + size, x0 : x0 + size]


class Screen:
    """One monitor's pixels. Build it on the thread that uses it."""

    def __init__(self, monitor_index: int = 1) -> None:
        self._sct = mss.mss()
        monitors = self._sct.monitors
        if not 0 < monitor_index < len(monitors):
            monitor_index = 1
        self.index = monitor_index
        self.mon = monitors[monitor_index]

    left = property(lambda self: self.mon["left"])
    top = property(lambda self: self.mon["top"])
    width = property(lambda self: self.mon["width"])
    height = property(lambda self: self.mon["height"])

    @property
    def center(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2

    def grab(self) -> Frame:
        shot = self._sct.grab(self.mon)
        bgr = cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)
        return Frame(bgr, origin=(self.left, self.top))

    def close(self) -> None:
        self._sct.close()

    @staticmethod
    def describe() -> list[str]:
        with mss.mss() as sct:
            return [
                f"{i}: {m['width']}x{m['height']} @ ({m['left']},{m['top']})"
                for i, m in enumerate(sct.monitors)
                if i > 0
            ]


def _resize(template: np.ndarray, factor: float) -> np.ndarray:
    if abs(factor - 1.0) < 1e-3:
        return template
    w = max(8, int(round(template.shape[1] * factor)))
    h = max(8, int(round(template.shape[0] * factor)))
    return cv2.resize(
        template, (w, h), interpolation=cv2.INTER_AREA if factor < 1.0 else cv2.INTER_LINEAR
    )


class Matcher:
    def __init__(self, templates_dir: Path, screen_height: int) -> None:
        self.dir = Path(templates_dir)
        self.screen_height = screen_height
        self.k: float | None = None
        self._templates: dict[str, np.ndarray] = {}
        self._refs: dict[str, int] = {}
        self._default_ref = DEFAULT_REFERENCE
        manifest = self.dir / "manifest.json"
        if manifest.exists():
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            self._default_ref = int(raw.get("_default", DEFAULT_REFERENCE))
            self._refs = {k: int(v) for k, v in raw.items() if not k.startswith("_")}

    # -- templates ---------------------------------------------------------

    def load(self, name: str) -> np.ndarray:
        if name not in self._templates:
            path = self.dir / f"{name}.png"
            image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise FileNotFoundError(f"template not readable: {path}")
            self._templates[name] = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return self._templates[name]

    def exists(self, name: str) -> bool:
        return (self.dir / f"{name}.png").exists()

    def base_scale(self, name: str) -> float:
        return self.screen_height / float(self._refs.get(name, self._default_ref))

    def nominal_size(self, name: str) -> int:
        """Template height at the calibrated scale.

        Use this for geometry rather than an individual Match's size: a match
        may land on any rung of the +/-3% fine sweep, and that wobble compounds
        once it is multiplied out across a row of grid cells.
        """
        scale = self.base_scale(name) * (self.k if self.k is not None else 1.0)
        return int(round(self.load(name).shape[0] * scale))

    def unlock(self) -> None:
        self.k = None

    @property
    def calibrated(self) -> bool:
        return self.k is not None

    # -- searching ---------------------------------------------------------

    def find(
        self,
        frame: Frame,
        name: str,
        threshold: float,
        region: Rect | None = None,
        lock: bool = True,
    ) -> Match | None:
        template = self.load(name)
        image, origin, downscale = frame.view(region)
        if image.size == 0:
            return None
        base = self.base_scale(name)
        factors = FINE_K if self.k is not None else WIDE_K
        anchor = self.k if self.k is not None else 1.0

        best: Match | None = None
        for factor in factors:
            scale = base * anchor * factor
            sized = _resize(template, scale * downscale)
            if sized.shape[0] > image.shape[0] or sized.shape[1] > image.shape[1]:
                continue
            result = cv2.matchTemplate(image, sized, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(result)
            candidate = Match(
                name=name,
                score=float(score),
                left=int(origin[0] + loc[0] / downscale),
                top=int(origin[1] + loc[1] / downscale),
                width=int(sized.shape[1] / downscale),
                height=int(sized.shape[0] / downscale),
                scale=scale,
            )
            # Refine every rung, not just the coarse winner. Half-resolution
            # scores cannot separate scales for a small template - a 36px-tall
            # dialog button becomes 18px - so picking the rung on coarse score
            # alone chose the wrong scale and refined it to 0.448 when the
            # right rung was a 1.000.
            if downscale < 1.0:
                candidate = self._refine(frame, name, template, candidate) or candidate
            if best is None or candidate.score > best.score:
                best = candidate
        if best is None or best.score < threshold:
            return None
        if lock and self.k is None:
            # Lock once and hold. Re-locking on every match lets each fine
            # sweep's +/-3% wobble drag the calibration around, and geometry
            # derived from it drifts with it.
            self.k = best.scale / base
        return best

    def find_first(
        self, frame: Frame, names: list[str], threshold: float, region: Rect | None = None
    ) -> Match | None:
        for name in names:
            if not self.exists(name):
                continue
            hit = self.find(frame, name, threshold, region=region)
            if hit:
                return hit
        return None

    def find_best(
        self, frame: Frame, names: list[str], threshold: float, region: Rect | None = None
    ) -> Match | None:
        best: Match | None = None
        for name in names:
            if not self.exists(name):
                continue
            hit = self.find(frame, name, threshold, region=region)
            if hit and (best is None or hit.score > best.score):
                best = hit
        return best

    def find_edges(
        self, frame: Frame, name: str, threshold: float, region: Rect
    ) -> Match | None:
        """Gradient-domain match, always region-restricted and full resolution.

        Flat areas of a frame have near-zero gradient everywhere, and a
        near-constant patch correlates perfectly with any other near-constant
        patch - the weapon picker screens score a spurious 1.000 that way. The
        energy floor below rejects that outright.
        """
        template = self.load(name)
        scale = self.base_scale(name) * (self.k if self.k is not None else 1.0)
        sized = _resize(template, scale)
        gx = cv2.Sobel(sized.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(sized.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
        edge_template = cv2.magnitude(gx, gy)

        ox, oy = frame.origin
        x0 = max(0, region[0] - ox)
        y0 = max(0, region[1] - oy)
        x1 = min(frame.width, x0 + region[2])
        y1 = min(frame.height, y0 + region[3])
        window = frame.edges()[y0:y1, x0:x1]
        if window.size == 0 or window.shape[0] < edge_template.shape[0]                 or window.shape[1] < edge_template.shape[1]:
            return None
        if float(window.mean()) < float(edge_template.mean()) * 0.15:
            return None  # nothing but flat colour here

        result = cv2.matchTemplate(window, edge_template, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        if score < threshold:
            return None
        return Match(
            name=name, score=float(score),
            left=int(ox + x0 + loc[0]), top=int(oy + y0 + loc[1]),
            width=edge_template.shape[1], height=edge_template.shape[0], scale=scale,
        )

    def score_all(self, frame: Frame, names: list[str], region: Rect | None = None):
        """Diagnostic sweep. Never locks, so one loud score cannot skew the rest."""
        out = []
        for name in names:
            if not self.exists(name):
                continue
            hit = self.find(frame, name, threshold=0.0, region=region, lock=False)
            out.append((name, hit.score if hit else 0.0, hit))
        return out

    # -- internals ---------------------------------------------------------

    def _refine(self, frame: Frame, name: str, template: np.ndarray, coarse: Match) -> Match | None:
        """Re-match at full resolution in a small window for an exact centre."""
        ox, oy = frame.origin
        pad = 24
        x0 = max(0, coarse.left - ox - pad)
        y0 = max(0, coarse.top - oy - pad)
        x1 = min(frame.width, coarse.left - ox + coarse.width + pad)
        y1 = min(frame.height, coarse.top - oy + coarse.height + pad)
        crop = frame.gray[y0:y1, x0:x1]
        sized = _resize(template, coarse.scale)
        if crop.size == 0 or sized.shape[0] > crop.shape[0] or sized.shape[1] > crop.shape[1]:
            return None
        result = cv2.matchTemplate(crop, sized, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        return Match(
            name=name,
            score=float(score),
            left=int(ox + x0 + loc[0]),
            top=int(oy + y0 + loc[1]),
            width=sized.shape[1],
            height=sized.shape[0],
            scale=coarse.scale,
        )
