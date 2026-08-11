"""Re-cut a weapon card template from one of your own captures.

Card art drifts — a skin change or a new variant leaves the shipped template
scoring far below threshold (medkit sits at 0.49 against tests/7.png). This
crops the live card out of a capture and rescales it to the template reference
so it drops straight into templates/.

    python tools/recut.py --image tests/7.png --cell 1 --name weapon_utility_medkit
    python tools/recut.py --image tests/7.png --cell 1 --name ... --apply

Without --apply it only reports what the new template would score. Replaced
files are moved to templates/_replaced/ rather than destroyed.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rivals.config import TEMPLATES_DIR, Config  # noqa: E402
from rivals.detect import Detector  # noqa: E402
from rivals.picker import GRID_COLS  # noqa: E402
from rivals.vision import Frame, Matcher  # noqa: E402

TEMPLATE_PX = 132  # every shipped weapon card is 132x132 at the reference scale


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--cell", type=int, required=True, help="0-9, left to right, top row first")
    parser.add_argument("--name", required=True, help="template name without .png")
    parser.add_argument("--apply", action="store_true", help="write it into templates/")
    args = parser.parse_args()

    image = cv2.imdecode(np.fromfile(str(args.image), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        print(f"cannot read {args.image}")
        return 1
    frame = Frame(image)

    cfg = Config()
    matcher = Matcher(TEMPLATES_DIR, frame.height)
    scene = Detector(matcher, cfg.threshold).identify(frame)
    if scene.grid is None:
        print("that capture is not a loadout picker")
        return 1
    grid = scene.grid
    if not 0 <= args.cell < len(grid.cells):
        print(f"cell must be 0-{len(grid.cells) - 1}")
        return 1

    cell = grid.cells[args.cell]
    print(f"cell {args.cell} (row {cell.row}, col {cell.col}) at ({cell.x},{cell.y}) "
          f"state={cell.state}, {grid.cell_size}px")
    if cell.state != "available":
        print("  refusing: that cell is locked or empty")
        return 1

    patch = frame.cell(cell.x, cell.y, grid.cell_size)
    cut = cv2.resize(patch, (TEMPLATE_PX, TEMPLATE_PX), interpolation=cv2.INTER_AREA)

    scratch = ROOT / "templates" / f"{args.name}.recut.png"
    cv2.imencode(".png", cut)[1].tofile(str(scratch))
    fresh = Matcher(TEMPLATES_DIR, frame.height)
    fresh.k = matcher.k
    hit = fresh.find(frame, f"{args.name}.recut", 0.0, region=grid.bbox(), lock=False)
    landed = grid.nearest(*hit.center) if hit else None
    print(f"  re-cut scores {hit.score:.3f} -> cell {landed.index if landed else '-'}")

    old = TEMPLATES_DIR / f"{args.name}.png"
    if old.exists():
        prev = Matcher(TEMPLATES_DIR, frame.height)
        prev.k = matcher.k
        before = prev.find(frame, args.name, 0.0, region=grid.bbox(), lock=False)
        print(f"  current template scores {before.score:.3f}" if before else "  current: no match")

    if not args.apply:
        scratch.unlink()
        print("  (dry run - pass --apply to install it)")
        return 0

    if old.exists():
        backup = TEMPLATES_DIR / "_replaced"
        backup.mkdir(exist_ok=True)
        shutil.move(str(old), str(backup / f"{args.name}.png"))
        print(f"  moved the old one to templates/_replaced/{args.name}.png")
    shutil.move(str(scratch), str(old))
    print(f"  wrote templates/{args.name}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
