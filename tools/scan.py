"""Ask the matcher what it sees — on the live screen or on a saved capture.

    python tools/scan.py                  # whatever is on monitor 1 right now
    python tools/scan.py --monitor 2
    python tools/scan.py --image tests/4.png
    python tools/scan.py --image tests/4.png --all   # score every template

Use this to calibrate: open the screen in question, run it, and check the
sentinel you expect scores well clear of everything else.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rivals import detect, picker, weapons, window  # noqa: E402
from rivals.config import TEMPLATES_DIR, Config  # noqa: E402
from rivals.detect import Detector  # noqa: E402
from rivals.vision import Frame, Matcher, Screen  # noqa: E402


def main() -> int:
    cfg = Config.load()
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitor", type=int, default=cfg.monitor)
    parser.add_argument("--image", type=Path, default=None)
    parser.add_argument("--all", action="store_true", help="score every template too")
    args = parser.parse_args()

    screen = None
    if args.image:
        data = np.fromfile(str(args.image), dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is None:
            print(f"cannot read {args.image}")
            return 1
        frame = Frame(image)
        source = str(args.image)
    else:
        screen = Screen(args.monitor)
        frame = screen.grab()
        source = f"monitor {screen.index}"

    matcher = Matcher(TEMPLATES_DIR, frame.height)
    detector = Detector(matcher, cfg.threshold)
    catalogue = weapons.discover(TEMPLATES_DIR)

    start = time.perf_counter()
    scene = detector.identify(frame)
    elapsed = time.perf_counter() - start

    if not args.image:
        hwnd = window.find_game()
        state = "focused" if window.is_foreground(hwnd) else "NOT focused"
        print(f"game window: {window.describe(hwnd)}  [{state}]")
    print(f"{source}: {frame.width}x{frame.height}")
    print(f"scene: {detect.SCENE_LABELS[scene.name]}   ({elapsed * 1000:.0f} ms)")
    if scene.match:
        print(f"  {scene.match.name} at {scene.match.center} "
              f"score {scene.match.score:.3f} scale {scene.match.scale:.3f}")
    print(f"  calibration k = {matcher.k:.3f}" if matcher.k else "  uncalibrated")

    if scene.alive is not None:
        print(f"  state: {'ALIVE' if scene.alive else 'DEAD (Respawn button up)'}")
        primary = cfg.loadout.get("primary", "random")
        if not scene.alive:
            print("  loadout: not checked while dead - the weapon bar is hidden")
            primary = None
        plate = f"hud_{primary}" if primary else ""
        if primary and matcher.exists(plate):
            W, H = frame.width, frame.height
            region = (int(W * 0.60), int(H * 0.74), int(W * 0.40), int(H * 0.16))
            hit = matcher.find_edges(frame, plate, 0.0, region)
            score = f"{hit.score:.3f}" if hit else "rejected (no edge energy)"
            verdict = "equipped" if hit and hit.score >= cfg.assert_threshold else "NOT equipped"
            print(f"  loadout: {primary} {verdict}  ({score})")
        elif primary:
            print(f"  loadout: no HUD name plate for '{primary}', check skipped")

    if scene.grid:
        slot_name = "unreadable" if scene.slot is None else weapons.SLOTS[scene.slot]
        print(f"  slot: {slot_name}   cell {scene.grid.cell_size} px, spacing {scene.grid.spacing} px")
        for row in range(picker.GRID_ROWS):
            cells = scene.grid.cells[row * picker.GRID_COLS : (row + 1) * picker.GRID_COLS]
            print("    " + " ".join(f"{c.state[:5]:>6s}" for c in cells))
        names = [w.template for w in catalogue.get(weapons.SLOTS[scene.slot or 0], [])]
        scored = matcher.score_all(frame, names, region=scene.grid.bbox())
        scored.sort(key=lambda r: r[1], reverse=True)
        print("  weapon cards in the grid:")
        for name, score, hit in scored[:8]:
            cell = scene.grid.nearest(*hit.center) if hit else None
            where = f"cell {cell.index}" if cell else "off-grid"
            flag = "  <= would pick" if score >= cfg.weapon_threshold else ""
            print(f"    {score:.3f}  {name:34s} {where}{flag}")

    if args.all:
        names = ["play_button", "join_button", "ffa", "random_card"] + weapons.all_templates(
            catalogue
        )
        start = time.perf_counter()
        scored = matcher.score_all(frame, names)
        elapsed = time.perf_counter() - start
        scored.sort(key=lambda r: r[1], reverse=True)
        print(f"\nall {len(scored)} templates, full frame ({elapsed:.2f}s):")
        for name, score, hit in scored[:12]:
            flag = "  <= over threshold" if score >= cfg.threshold else ""
            print(f"  {score:.3f}  {name:34s} {hit.center if hit else '-'}{flag}")

    if screen:
        screen.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
