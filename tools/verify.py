"""Replay every tests/*.png through the real decision code and check every call.

No mouse, no keyboard, no game — this loads each capture, runs the same
Detector / picker / MacroRunner._choose_cell the live macro uses, and asserts
the scene it names and the pixel it would click.

    python tools/verify.py
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rivals import clock as clock_mod  # noqa: E402
from rivals import stats as stats_mod  # noqa: E402
from rivals import detect, picker, weapons  # noqa: E402
from rivals.config import TEMPLATES_DIR, Config  # noqa: E402
from rivals.detect import Detector  # noqa: E402
from rivals.macro import MacroRunner  # noqa: E402
from rivals.vision import Frame, Matcher  # noqa: E402

SHOTS = ROOT / "tests"

# Ground truth, read off the captures by hand.
PLAY_RECT = (1144, 1168, 272, 78)
PLAY_RECT_9 = (1143, 1168, 272, 78)  # same button, a different lobby
JOIN_RECT = (1416, 987, 136, 67)
HOME_RECT = (1108, 336, 236, 48)
FFA_RECT = (345, 985, 1869, 450)
ANCHOR, SPACING = (873, 651), 202
TOLERANCE = 45  # px a click may sit from the true card centre


def cell_xy(index: int) -> tuple[int, int]:
    row, col = divmod(index, 5)
    return ANCHOR[0] + col * SPACING, ANCHOR[1] + row * SPACING


# index -> expected state, per capture
CELL_TRUTH = {
    "4.png": ["available"] * 10,
    "5.png": ["available"] * 7 + ["empty"] * 3,
    "6.png": ["available"] * 7 + ["locked", "available", "empty"],
    "7.png": [
        "available", "available", "available", "locked", "available",
        "available", "available", "locked", "locked", "empty",
    ],
}
SLOT_TRUTH = {"4.png": 0, "5.png": 1, "6.png": 2, "7.png": 3}

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((ok, label))
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{('  -> ' + detail) if detail else ''}")
    return ok


def inside(point, rect, pad: int = 0) -> bool:
    x, y = point
    left, top, w, h = rect
    return left - pad <= x <= left + w + pad and top - pad <= y <= top + h + pad


def load(name: str) -> Frame:
    data = np.fromfile(str(SHOTS / name), dtype=np.uint8)
    return Frame(cv2.imdecode(data, cv2.IMREAD_COLOR))


def make_runner(cfg: Config, matcher: Matcher) -> MacroRunner:
    runner = MacroRunner(cfg, log=lambda m: print(f"       . {m}"))
    runner.matcher = matcher
    runner.detector = Detector(matcher, cfg.threshold)
    return runner


def main() -> int:
    cfg = Config()
    shot_names = ([f"{i}.png" for i in range(1, 10)]
                  + ["home.png", "after1.png", "after2.png", "after3.png", "after4.png",
                     "disconnect.png", "restart.png",
                     "alive.png", "dead.png", "not_grenade_launcher.png",
                     "dead_in_picker.png", "spectating.png"])
    frames = {name: load(name) for name in shot_names}
    height = frames["1.png"].height
    matcher = Matcher(TEMPLATES_DIR, height)
    detector = Detector(matcher, cfg.threshold)
    runner = make_runner(cfg, matcher)

    print(f"captures are {frames['1.png'].width}x{height}; "
          f"card base scale {matcher.base_scale('random_card'):.3f}, "
          f"ffa base scale {matcher.base_scale('ffa'):.3f}\n")

    # ---- scene identification -------------------------------------------
    print("scene identification")
    expected_scene = {
        "1.png": detect.LOBBY, "2.png": detect.MODES, "3.png": detect.SPECTATE,
        "4.png": detect.PICKER, "5.png": detect.PICKER, "6.png": detect.PICKER,
        "7.png": detect.PICKER, "9.png": detect.LOBBY,
        "home.png": detect.HOME, "after1.png": detect.LEADERBOARD,
        "after2.png": detect.INTERMISSION, "after3.png": detect.MAPVOTE,
        "after4.png": detect.PICKER, "disconnect.png": detect.RECONNECT,
        "restart.png": detect.CONNFAIL, "8.png": detect.PLAYING,
        "alive.png": detect.PLAYING, "dead.png": detect.PLAYING,
        "not_grenade_launcher.png": detect.PLAYING,
        "dead_in_picker.png": detect.PICKER, "spectating.png": detect.SPECTATE,
    }
    scenes = {}
    t0 = time.perf_counter()
    for name, frame in frames.items():
        scene = detector.identify(frame)
        scenes[name] = scene
        score = f"{scene.match.score:.3f}" if scene.match else "-"
        check(scene.name == expected_scene[name],
              f"{name} -> {scene.name} (expected {expected_scene[name]})", f"score {score}")
    elapsed = time.perf_counter() - t0
    count = len(frames)
    print(f"       {count} identifications in {elapsed:.2f}s "
          f"({elapsed / count * 1000:.0f} ms each), locked k={matcher.k:.3f}\n")

    # ---- click targets for the three button stages -----------------------
    print("button click targets")
    for name, rect, label in (
        ("1.png", PLAY_RECT, "Play"), ("3.png", JOIN_RECT, "Join this game"),
        ("2.png", FFA_RECT, "Free For All"), ("home.png", HOME_RECT, "the blue Play button"),
        ("9.png", PLAY_RECT_9, "Play in a different lobby"),
    ):
        hit = scenes[name].match
        ok = hit is not None and inside(hit.center, rect, pad=8)
        check(ok, f"{name} click lands on {label}", f"{hit.center if hit else None} in {rect}")
    print()

    # ---- picker geometry -------------------------------------------------
    print("picker grid + slot indicator")
    for name in ("4.png", "5.png", "6.png", "7.png"):
        scene = scenes[name]
        grid = scene.grid
        if grid is None:
            check(False, f"{name} grid built")
            continue
        drift = max(
            max(abs(c.x - cell_xy(c.index)[0]), abs(c.y - cell_xy(c.index)[1]))
            for c in grid.cells
        )
        check(drift <= 12, f"{name} all 10 cells land on real cards", f"worst drift {drift} px")
        check(scene.slot == SLOT_TRUTH[name],
              f"{name} slot indicator reads {scene.slot} (expected {SLOT_TRUTH[name]})")
        states = [c.state for c in grid.cells]
        check(states == CELL_TRUTH[name], f"{name} cell states",
              "" if states == CELL_TRUTH[name] else f"got {states}")
    print()

    # ---- weapon selection ------------------------------------------------
    print("weapon selection")
    cases = [
        ("4.png", "primary", "sniper", 9),
        ("4.png", "primary", "bow", 4),
        ("4.png", "primary", "random", 0),
        ("5.png", "secondary", "uzi", 6),
        ("5.png", "secondary", "revolver", 4),
        ("6.png", "melee", "katana", 5),
        ("6.png", "melee", "knife", 6),
        ("7.png", "utility", "grenade", 5),
        ("7.png", "utility", "freeze_ray", 4),
    ]
    for name, slot, choice, want_index in cases:
        grid = scenes[name].grid
        cell, why = runner._choose_cell(frames[name], grid, slot, choice)
        ok = cell is not None and cell.index == want_index
        got = f"cell {cell.index} ({why})" if cell else "None"
        check(ok, f"{name} {slot}={choice} -> cell {want_index}", got)

    # a weapon that is not offered this round must fall back, not misfire
    for name, slot, choice in (("4.png", "primary", "minigun"), ("5.png", "secondary", "daggers")):
        grid = scenes[name].grid
        cell, why = runner._choose_cell(frames[name], grid, slot, choice)
        check(cell is not None and cell.index == 0,
              f"{name} {slot}={choice} (not offered) falls back to Random", f"{cell.index if cell else None} ({why})")

    # paintball_gun is half-hidden by a neighbouring 3D model: it scores 0.730,
    # under the flat threshold, and is only reachable via the per-cell rule.
    grid = scenes["4.png"].grid
    cell, why = runner._choose_cell(frames["4.png"], grid, "primary", "paintball_gun")
    check(cell is not None and cell.index == 2,
          "4.png primary=paintball_gun rescued by the per-cell rule",
          f"cell {cell.index if cell else None} ({why})")

    # rpg is occluded too but its best cell is a near-tie (margin 0.041), so
    # the rule must decline rather than guess.
    cell, why = runner._choose_cell(frames["4.png"], grid, "primary", "rpg")
    check(cell is not None and cell.index == 0,
          "4.png primary=rpg (near-tie) declines and takes Random",
          f"cell {cell.index if cell else None} ({why})")

    # these two were re-cut from these very captures, so this only proves the
    # re-cut installed correctly - not that it generalises to other rounds.
    for name, slot, choice, want in (
        ("7.png", "utility", "medkit", 1), ("5.png", "secondary", "slingshot", 1),
    ):
        grid = scenes[name].grid
        cell, why = runner._choose_cell(frames[name], grid, slot, choice)
        check(cell is not None and cell.index == want,
              f"{name} {slot}={choice} -> cell {want}  [re-cut from this capture, circular]",
              f"cell {cell.index if cell else None} ({why})")
    print()

    # ---- how many offered cards can actually be found --------------------
    print("card coverage (every card actually on screen in each capture)")
    offered = {
        "4.png": {1: "grenade_launcher", 2: "paintball_gun", 3: "assault_rifle", 4: "bow",
                  5: "burst_rifle", 6: "crossbow", 7: "gunblade", 8: "rpg", 9: "sniper"},
        "5.png": {1: "slingshot", 2: "flare_gun", 3: "handgun", 4: "revolver", 5: "spray",
                  6: "uzi"},
        "6.png": {1: "spear", 2: "trowel", 3: "chainsaw", 4: "fists", 5: "katana", 6: "knife",
                  8: "scythe"},
        "7.png": {1: "medkit", 2: "subspace_tripmine", 4: "freeze_ray", 5: "grenade",
                  6: "satchel"},
    }
    total = found = 0
    missed = []
    for name, cards in offered.items():
        grid = scenes[name].grid
        slot = weapons.SLOTS[SLOT_TRUTH[name]]
        for index, key in cards.items():
            total += 1
            cell, _ = runner._choose_cell(frames[name], grid, slot, key)
            if cell is not None and cell.index == index:
                found += 1
            else:
                missed.append(f"{slot}/{key}")
    check(found >= total - 1, f"{found}/{total} offered cards resolve to their own cell",
          f"missed {missed}" if missed else "")
    print()

    # ---- 'any' must never hit a locked or empty cell ---------------------
    print("'any' safety over 400 draws per picker")
    random.seed(11)
    for name in ("4.png", "5.png", "6.png", "7.png"):
        grid = scenes[name].grid
        slot = weapons.SLOTS[SLOT_TRUTH[name]]
        bad, picked = 0, set()
        for _ in range(400):
            cell, _ = runner._choose_cell(frames[name], grid, slot, "any")
            if cell is None or not cell.clickable or cell.index == 0:
                bad += 1
            else:
                picked.add(cell.index)
        check(bad == 0, f"{name} never picked a locked/empty/Random cell",
              f"{len(picked)} distinct cells used")
    print()

    # ---- stage advance detection ----------------------------------------
    print("stage-advance detection (signature must change between stages)")
    pairs = [("4.png", "5.png"), ("5.png", "6.png"), ("6.png", "7.png")]
    for a, b in pairs:
        grid = scenes[a].grid
        sig_a = picker.signature(frames[a], grid)
        sig_b = picker.signature(frames[b], grid)
        check(picker.changed(sig_a, sig_b), f"{a} -> {b} reads as advanced")
    grid = scenes["4.png"].grid
    check(not picker.changed(picker.signature(frames["4.png"], grid),
                             picker.signature(frames["4.png"], grid)),
          "identical frame does not read as advanced")
    print()

    # ---- calibration must not depend on which screen we start from -------
    print("cold start from every screen (scale locks on whatever is up first)")
    for first in shot_names:
        cold = Matcher(TEMPLATES_DIR, height)
        cold_detector = Detector(cold, cfg.threshold)
        cold_detector.identify(frames[first])  # this is what locks k
        wrong = [
            n for n, f in frames.items()
            if cold_detector.identify(f).name != expected_scene[n]
        ]
        k = f"k={cold.k:.3f}" if cold.k else "uncalibrated"
        check(not wrong, f"start on {first} -> all {len(frames)} scenes still correct ({k})",
              f"misread {wrong}" if wrong else "")
    print()

    # ---- post-match screens must be passive ------------------------------
    print("post-match screens")
    for name in ("after1.png", "after2.png", "after3.png"):
        check(scenes[name].name in detect.PASSIVE,
              f"{name} is a passive scene (no input)", scenes[name].name)
    check(scenes["after4.png"].slot == 0,
          "after4.png reopens on the primary slot", str(scenes["after4.png"].slot))
    # the big Random *map* card on the vote screen must not read as the picker
    vote = matcher.find(frames["after3.png"], detect.RANDOM_CARD, 0.0, lock=False)
    check(vote is None or vote.score < cfg.threshold,
          "the map vote's Random card is not mistaken for the weapon picker",
          f"random_card scores {vote.score:.3f} there")

    # Both dialogs share a layout; each must click a different button.
    dc = scenes["disconnect.png"]
    check(dc.match is not None and inside(dc.match.center, (1285, 789, 175, 36), pad=8),
          "disconnect dialog clicks Reconnect, not Leave",
          f"{dc.match.center if dc.match else None}")
    rs = scenes["restart.png"]
    if rs.match is not None:
        cx, cy = rs.match.center
        reach = rs.match.width * 3
        cancel = matcher.find(frames["restart.png"], detect.CANCEL_BUTTON, cfg.threshold,
                              region=(cx - reach, cy, 2 * reach, reach))
        check(cancel is not None and inside(cancel.center, (1100, 789, 175, 36), pad=8),
              "connection-failed dialog clicks Cancel, not Retry",
              f"{cancel.center if cancel else None}")
        # the same outlined button says "Leave" on the other dialog
        stray = matcher.find(frames["disconnect.png"], detect.CONNFAIL_TITLE, cfg.threshold)
        check(stray is None, "connection-failed title does not fire on the disconnect dialog")

    # No capture contains the disconnect dialog, so paste the button on one.
    print()

    # ---- in a match: alive or dead ---------------------------------------
    print("in-match state")
    for name, alive in (("alive.png", True), ("dead.png", False),
                        ("not_grenade_launcher.png", True), ("8.png", True)):
        got = scenes[name].alive
        check(got is alive, f"{name} reads as {'alive' if alive else 'dead'}", f"alive={got}")
    # Spectating draws a full match HUD, health bar and all, so the sentinel
    # order is the only thing keeping it from reading as "alive and grinding".
    for name in ("3.png", "spectating.png"):
        scene = scenes[name]
        check(scene.name == detect.SPECTATE and scene.alive is None,
              f"{name} is spectating, not playing (it shows a health bar too)",
              f"got {scene.name}")
    # spectating.png is at full health, so the health bar template really does
    # fire on it - which is precisely why the ordering has to be right.
    bar = matcher.find(frames["spectating.png"], detect.HEALTH_BAR, cfg.threshold)
    check(bar is not None,
          "spectating.png does carry a matching health bar - the ordering is load-bearing",
          f"{bar.score:.3f}" if bar else "no match")
    order = list(detect.ALL_SENTINELS)
    for earlier in (detect.JOIN_BUTTON, detect.RANDOM_CARD):
        check(order.index(earlier) < order.index(detect.HEALTH_BAR),
              f"{earlier} is checked before health_bar (it can coexist with the HUD)")
    stray = matcher.find(frames["alive.png"], detect.RESPAWN, cfg.threshold)
    check(stray is None, "the Respawn button is not seen while alive",
          f"scored {stray.score:.3f}" if stray else "")

    # Killed while the weapon select is open: the picker still owns the scene,
    # but cards clicked while dead do nothing, so it has to notice anyway.
    dead_probe = make_runner(cfg, matcher)

    class _DeadScreen:
        left = top = 0
        width = frames["dead_in_picker.png"].width
        height = frames["dead_in_picker.png"].height

    dead_probe.screen = _DeadScreen()
    check(dead_probe._is_dead(frames["dead_in_picker.png"]),
          "death is seen even while the weapon select owns the scene")
    check(not dead_probe._is_dead(frames["4.png"]),
          "a normal weapon select does not read as dead")
    check(not dead_probe._is_dead(frames["alive.png"]),
          "being alive in a match does not read as dead")

    print("\nloadout assert (edge matched, bottom-right name plate)")
    probe = make_runner(cfg, matcher)

    class _Screen:
        left = top = 0
        width, height = frames["alive.png"].width, frames["alive.png"].height

    probe.screen = _Screen()
    region = probe._hud_region()
    for name, equipped in (("alive.png", True), ("8.png", True), ("3.png", True),
                           ("not_grenade_launcher.png", False)):
        hit = matcher.find_edges(frames[name], "hud_grenade_launcher",
                                 cfg.assert_threshold, region)
        check(bool(hit) is equipped,
              f"{name}: grenade launcher {'equipped' if equipped else 'NOT equipped'}",
              f"score {hit.score:.3f}" if hit else "no match")
    flats = [n for n in ("4.png", "5.png", "6.png", "7.png", "1.png")
             if matcher.find_edges(frames[n], "hud_grenade_launcher", cfg.assert_threshold, region)]
    check(not flats, "flat screens are rejected by the edge-energy floor", f"fired on {flats}")
    print()

    # ---- match clock -----------------------------------------------------
    print("match clock")
    reader = clock_mod.ClockReader(TEMPLATES_DIR)
    check(reader.known_digits == [0, 1, 2, 5],
          f"glyph templates present for {reader.known_digits}")
    for name, want in (("3.png", "2:50"), ("8.png", "1:15"), ("alive.png", "2:51")):
        got = reader.read(frames[name])
        check(got is not None and got.text == want,
              f"{name} clock reads {want}", f"got {got.text if got else None}")
    # 3:13 and 0:23 contain digits with no glyph template, so they read partially
    for name in ("dead.png", "not_grenade_launcher.png"):
        got = reader.read(frames[name])
        check(got is not None and not clock_mod.near_end(got, cfg.hold_seconds),
              f"{name} clock is found and does not trigger a hold",
              f"read {got.text if got else None}")
    with_clock = {"3.png", "8.png", "alive.png", "dead.png", "not_grenade_launcher.png",
                  "spectating.png"}
    quiet = [n for n in shot_names if n not in with_clock and reader.read(frames[n])]
    check(not quiet, "no clock is read on screens that have none", f"misread {quiet}")

    # No capture shows the last seconds, so repaint the pill to make one.
    base = frames["8.png"].bgr
    three = frames["3.png"].bgr
    native = {"0": three[39:61, 1313:1331], "5": three[39:61, 1292:1310],
              "2": three[39:61, 1259:1278], "1": base[38:60, 1259:1272]}

    def repaint(text: str) -> Frame:
        img = base.copy()
        fill = np.median(img[36:62, 1250:1340].reshape(-1, 3), axis=0).astype(np.uint8)
        img[34:64, 1250:1345] = fill
        img[44:48, 1280:1284] = 255  # colon, clear of both digit slots
        img[52:56, 1280:1284] = 255
        for x, ch in zip((1256, 1292, 1318), text.replace(":", "")):
            glyph = native[ch]
            img[38 : 38 + glyph.shape[0], x : x + glyph.shape[1]] = glyph
        return Frame(img)

    print(f"  (synthetic: pill repainted on 8.png, hold at <= {cfg.hold_seconds}s)")
    for text, want_hold in (("0:00", True), ("0:05", True), ("0:10", True),
                            ("0:11", False), ("0:25", False), ("1:00", False),
                            ("2:52", False)):
        got = reader.read(repaint(text))
        holding = clock_mod.near_end(got, cfg.hold_seconds)
        check(holding == want_hold and got is not None and got.text == text,
              f"clock {text} -> hold={want_hold}",
              f"read {got.text if got else None}, hold={holding}")
    print()

    # ---- activity accounting --------------------------------------------
    print("activity graph accounting")
    st = stats_mod.Stats(lifetime_seconds=3600.0)
    now = time.monotonic()  # Stats reads the real clock, so anchor to it
    st.session_start = now - 600           # 10 minute session
    st._spans = [[now - 500, now - 400],   # 100 s
                 [now - 300, now - 100],   # 200 s
                 [now - 50, -1.0]]         # open, ~50 s
    st._marks = [
        stats_mod.Mark(now - 400, stats_mod.ROUND, "round over"),
        stats_mod.Mark(now - 390, stats_mod.LOADOUT, "weapon select"),
        stats_mod.Mark(now - 100, stats_mod.DROP, "disconnected"),
        stats_mod.Mark(now - 90, stats_mod.RECOVER, "left the game"),
        stats_mod.Mark(now - 5000, stats_mod.ROUND, "ancient, outside the window"),
    ]
    snap = st.snapshot(window=600)
    check(abs(snap.grind_seconds - 350) < 2, "grinding time sums the closed and open spans",
          f"{snap.grind_seconds:.0f}s of an expected 350s")
    check(snap.rounds == 2 and snap.drops == 2,
          "rounds and interruptions counted", f"rounds={snap.rounds} drops={snap.drops}")
    check(len(snap.marks) == 4, "marks outside the window are clipped",
          f"{len(snap.marks)} of 5 kept")
    check(len(snap.spans) == 3, "spans survive the window", f"{len(snap.spans)}")
    check(abs(snap.lifetime_seconds - (3600 + snap.grind_seconds)) < 1,
          "lifetime adds the session on top of the carried total")
    st.grinding(False)
    closed = st.snapshot(window=600)
    st.grinding(False)  # idempotent
    check(abs(closed.grind_seconds - st.snapshot(window=600).grind_seconds) < 0.1,
          "stopping twice does not double count")
    st.grinding(True)
    check(st.snapshot(window=600).grinding, "restarting reopens a span")
    print()

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"{passed}/{total} checks passed")
    for ok, label in results:
        if not ok:
            print(f"  still failing: {label}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
