"""The macro loop.

Scene driven rather than a fixed sequence: every poll it works out which screen
is actually on display and does the right thing for it. That means a respawn,
a slow load, or landing on the mode list out of order all resolve themselves
instead of wedging a linear state machine.

    lobby     -> click Play, then scroll the mode list to Free For All
    modes     -> click the Free For All banner
    spectate  -> click the green "Join this game!" arrow
    picker    -> read the highlighted slot, pick that slot's weapon
    home      -> Roblox game page: click the blue Play button
    reconnect -> click Reconnect, which lands back in the lobby
    in match  -> jump spam + camera sweep + fire

    leaderboard / between rounds / map vote -> sit completely still

The passive trio matters: a round ends into the leaderboard and the game walks
itself from there to the next weapon select. Anything clicked on the way votes
for a map or leaves the server, and anything still swinging when the weapon
select opens picks a weapon for you.
"""

from __future__ import annotations

import math
import random
import threading
import time
from typing import Callable

from . import clock, detect, inputs, picker, stats as stats_mod, weapons, window
from .clock import ClockReader
from .config import TEMPLATES_DIR, Config
from .detect import Detector, Scene
from .picker import Grid
from .stats import Stats
from .vision import Frame, Match, Matcher, Screen

STAGE_TEXT = {
    detect.LOBBY: "Lobby - clicking Play",
    detect.MODES: "Mode list - finding Free For All",
    detect.SPECTATE: "Spectating - joining",
    detect.PICKER: "Picking loadout",
    detect.PLAYING: "In a match - grinding",
    detect.HOME: "Roblox game page - relaunching",
    detect.LEADERBOARD: "Match over - waiting it out",
    detect.INTERMISSION: "Between rounds - waiting",
    detect.MAPVOTE: "Map vote - letting others pick",
    detect.RECONNECT: "Disconnected - reconnecting",
    detect.CONNFAIL: "Connection failed - cancelling",
    detect.UNKNOWN: "In match - grinding",
    "idle": "Idle",
    "waiting": "Waiting for a known screen",
    "holding": "Round ending - holding still",
    "dead": "Dead - respawning",
    "recovering": "Stuck - leaving the game",
}


class Stopped(Exception):
    """Raised internally when the operator stops the run."""


class MacroRunner(threading.Thread):
    def __init__(
        self,
        cfg: Config,
        log: Callable[[str], None],
        on_stage: Callable[[str], None] | None = None,
        stats: Stats | None = None,
    ) -> None:
        super().__init__(daemon=True, name="rivals-macro")
        self.cfg = cfg
        self.log = log
        self.on_stage = on_stage or (lambda _s: None)
        self.stats = stats or Stats()
        self._halt = threading.Event()
        self.screen: Screen | None = None
        self.matcher: Matcher | None = None
        self.detector: Detector | None = None
        self.catalogue = weapons.discover(TEMPLATES_DIR)
        self.clock = ClockReader(TEMPLATES_DIR)
        self.in_match = False
        self._last_progress = time.monotonic()
        self._recalibrated = False
        self._combat_state: dict | None = None
        self._holding = False
        self._picker_probe: tuple[int, int, int, int] | None = None
        self.window: int | None = None
        self._focused = False
        self._warned_no_window = False
        self._focus_complaint = 0.0
        self._dialog_probe_at = 0.0
        self._last_band = None
        self._need_reselect = False
        self._next_assert = 0.0
        self._warned_assert = False

    # -- lifecycle ---------------------------------------------------------

    def stop(self) -> None:
        self._halt.set()

    @property
    def stopping(self) -> bool:
        return self._halt.is_set()

    def run(self) -> None:
        try:
            self.screen = Screen(self.cfg.monitor)
            self.matcher = Matcher(TEMPLATES_DIR, self.screen.height)
            self.detector = Detector(self.matcher, self.cfg.threshold)
            self.log(
                f"monitor {self.screen.index}: {self.screen.width}x{self.screen.height}, "
                f"card scale {self.matcher.base_scale('random_card'):.3f}, "
                f"clock digits {self.clock.known_digits}"
            )
            self._ensure_focus()
            self._loop()
        except Stopped:
            pass
        except Exception as exc:
            self.log(f"ERROR: {type(exc).__name__}: {exc}")
        finally:
            self.stats.grinding(False)
            if self.screen:
                self.screen.close()
            self.on_stage(STAGE_TEXT["idle"])
            self.log("macro stopped")

    # -- primitives --------------------------------------------------------

    def _tick(self, seconds: float) -> None:
        """Interruptible sleep that also honours the F9 panic key.

        The remainder guard matters: waiting out a sub-millisecond leftover
        costs a whole Windows timer tick, which alone drags the 60 Hz camera
        loop down to 40 Hz.
        """
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining > 0.0015:
                if self._halt.wait(min(0.05, remaining)):
                    raise Stopped
            if inputs.is_pressed(inputs.VK_F9):
                self.stop()
                raise Stopped
            if self._halt.is_set():
                raise Stopped
            if remaining <= 0.0015:
                return

    def _grab(self) -> Frame:
        assert self.screen is not None
        return self.screen.grab()

    def _ensure_focus(self) -> bool:
        """Own the game window before clicking into it.

        Focus is taken through the Win32 API, not by clicking: a click used to
        activate a window is consumed by the activation, which is exactly why
        the first click after Start went nowhere.

        Not finding a window at all is *not* a reason to refuse. Every click
        the macro makes follows a template that already matched, so Roblox is
        demonstrably on screen; refusing would turn an unrecognised window
        class into a silent, total stall.
        """
        if not self.cfg.focus_game:
            return True
        if not window.alive(self.window):
            self.window = window.find_game()
            if self.window is not None:
                self.log(f"game window: {window.describe(self.window)}")
                self._warned_no_window = False
            elif not self._warned_no_window:
                self._warned_no_window = True
                self.log("WARNING: no Roblox window found - clicking anyway, since a "
                         "Roblox screen was recognised. Focus handling is off until it appears.")
        if self.window is None:
            self._focused = False
            return True

        ok = window.is_foreground(self.window) or window.focus(self.window)
        now = time.monotonic()
        if ok and not self._focused:
            self.log("game window focused")
            self._tick(self.cfg.focus_settle)
        elif not ok and now - self._focus_complaint > 15.0:
            # Log the *first* failure too. Previously this only fired on a
            # transition out of "focused", so a run that never got focus at all
            # said nothing and looked like the macro had simply frozen.
            self._focus_complaint = now
            self.log("could not focus the game window - input paused. Is something "
                     "running as administrator?")
        self._focused = ok
        return ok

    def _click(self, x: int, y: int, jitter: int = 5, settle: float = 0.5) -> None:
        if not self._ensure_focus():
            self._tick(0.5)
            return
        inputs.click_at(x + random.randint(-jitter, jitter), y + random.randint(-jitter, jitter))
        self._tick(settle)

    def _click_match(self, hit: Match, settle: float = 0.8) -> None:
        self._click(*hit.center, settle=settle)

    # -- main loop ---------------------------------------------------------

    def _loop(self) -> None:
        assert self.detector is not None
        last_scene = None
        while not self.stopping:
            frame = self._grab()
            scene = self.detector.identify(frame)

            if scene.name != last_scene:
                extra = ""
                if scene.name == detect.PICKER and scene.slot is not None:
                    extra = f" (slot {scene.slot + 1}/4)"
                self.log(f"scene: {detect.SCENE_LABELS[scene.name]}{extra}")
                self.on_stage(STAGE_TEXT[scene.name])
                self._mark_scene(scene.name)
                last_scene = scene.name

            if scene.name == detect.LOBBY:
                self.in_match = False
                self._do_lobby(scene)
            elif scene.name == detect.MODES:
                self._do_modes(scene)
            elif scene.name == detect.SPECTATE:
                self._do_spectate(scene)
            elif scene.name == detect.PICKER:
                self.in_match = True
                self._remember_picker(scene)
                self._do_picker(frame, scene)
            elif scene.name == detect.HOME:
                self.in_match = False
                self._do_home(scene)
            elif scene.name == detect.PLAYING:
                self._do_playing(frame, scene)
                continue
            elif scene.name in detect.PASSIVE:
                # The round is over. The game walks itself to the next weapon
                # select; anything we click here votes or leaves.
                self.in_match = False
                self._tick(0.4)
            elif scene.name == detect.RECONNECT:
                self.in_match = False
                self._do_reconnect(scene)
            elif scene.name == detect.CONNFAIL:
                self.in_match = False
                self._do_connfail(scene)
            else:
                last_scene = self._do_unknown(frame, last_scene)
                continue

            self._holding = False
            self.stats.grinding(False)
            self._last_progress = time.monotonic()
            self._combat_state = None  # a real screen means the round is not running

    def _do_unknown(self, frame: Frame, last_scene: str | None) -> str | None:
        """Either we are in a match, or something we do not recognise is up.

        Progress is what distinguishes the two: a live match ticks the clock
        every second. If neither a known screen nor a clock change has happened
        for `recover_after`, the session is wedged however healthy it looks,
        and we bail out rather than grind at a frozen frame.
        """
        reading = self.clock.read(frame)
        stalled = self._note_progress(frame)
        now = time.monotonic()

        if self.in_match:
            if clock.near_end(reading, self.cfg.hold_seconds):
                self.stats.grinding(False)
                self._hold(reading)
            elif self.cfg.enable_recovery and stalled > self.cfg.recover_after:
                self.log(f"no clock movement for {stalled:.0f} s - assuming a dead session")
                self._recover()
                self.in_match = False
                self._last_progress = time.monotonic()
            else:
                self._resume()
                self.stats.grinding(True)
                self._combat_burst(self.cfg.combat_check_interval)
            return last_scene

        if stalled > 30.0 and not self._recalibrated:
            # The window may have been resized: drop the scale lock so the next
            # poll re-sweeps instead of hunting at a scale that no longer fits.
            self.log("30 s on an unrecognised screen - re-calibrating scale")
            if self.matcher is not None:
                self.matcher.unlock()
            self._recalibrated = True
        elif self.cfg.enable_recovery and stalled > self.cfg.recover_after:
            self._recover()
            self._last_progress = time.monotonic()
            self._recalibrated = False
        self.stats.grinding(False)
        self.on_stage(STAGE_TEXT["waiting"])
        self._tick(0.6)
        return last_scene

    def _mark_scene(self, name: str) -> None:
        """Put a spike on the activity graph for anything that interrupts."""
        kind = {
            detect.LEADERBOARD: (stats_mod.ROUND, "round over"),
            detect.RECONNECT: (stats_mod.DROP, "disconnected"),
            detect.CONNFAIL: (stats_mod.DROP, "connection failed"),
            detect.PICKER: (stats_mod.LOADOUT, "weapon select"),
        }.get(name)
        if kind:
            self.stats.mark(*kind)

    # -- in a match --------------------------------------------------------

    def _note_progress(self, frame: Frame) -> float:
        """Seconds since anything last moved. Returns the current stall.

        Seeing a health bar is not proof of life - a frozen client still draws
        one. Only the clock strip changing counts.
        """
        now = time.monotonic()
        band = clock.band_signature(self.clock, frame)
        if clock.moved(self._last_band, band):
            self._last_band = band
            self._last_progress = now
        return now - self._last_progress

    def _do_playing(self, frame: Frame, scene: Scene) -> None:
        """The health bar is up, so we are positively in a match.

        Previously this was inferred - the macro assumed it was playing from
        the moment the weapon select closed. Now it knows, and it knows
        whether it is looking at the world or at the Respawn button.
        """
        self.in_match = True
        stalled = self._note_progress(frame)
        if self.cfg.enable_recovery and stalled > self.cfg.recover_after:
            self.log(f"no clock movement for {stalled:.0f} s - assuming a dead session")
            self.stats.grinding(False)
            self._recover()
            self.in_match = False
            self._last_progress = time.monotonic()
            return
        if scene.alive is False:
            self.stats.grinding(False)
            self._combat_state = None
            self._do_dead()
            return

        reading = self.clock.read(frame)
        if clock.near_end(reading, self.cfg.hold_seconds):
            self.stats.grinding(False)
            self._hold(reading)
            return
        self._resume()
        self._check_loadout(frame)
        self.stats.grinding(True)
        self._combat_burst(self.cfg.combat_check_interval)

    def _do_dead(self) -> None:
        """Space respawns. If the loadout was wrong, M reopens the picker."""
        if not self._ensure_focus():
            self._tick(0.5)
            return
        self.on_stage(STAGE_TEXT["dead"])
        inputs.key_tap("space")
        self._tick(0.5)
        if self._need_reselect:
            self._need_reselect = False
            inputs.key_tap("m")
            self.log("wrong loadout - reopened the weapon select with M")
            self.stats.mark(stats_mod.LOADOUT, "loadout reselect")
            self._tick(1.0)

    def _hud_region(self) -> tuple[int, int, int, int] | None:
        """Bottom-right, where the equipped weapon's name plate sits."""
        if self.screen is None:
            return None
        return (
            self.screen.left + int(self.screen.width * 0.60),
            self.screen.top + int(self.screen.height * 0.74),
            int(self.screen.width * 0.40),
            int(self.screen.height * 0.16),
        )

    def _check_loadout(self, frame: Frame) -> None:
        """Is the primary we asked for actually in hand?"""
        if not self.cfg.assert_loadout or self._need_reselect or self.matcher is None:
            return
        now = time.monotonic()
        if now < self._next_assert:
            return
        self._next_assert = now + self.cfg.assert_interval

        choice = self.cfg.loadout.get("primary", "random")
        template = f"hud_{choice}"
        if choice in ("random", "any") or not self.matcher.exists(template):
            if not self._warned_assert:
                self._warned_assert = True
                self.log(f"loadout check off: no HUD name plate for primary '{choice}'")
            return
        region = self._hud_region()
        if region is None:
            return
        hit = self.matcher.find_edges(frame, template, self.cfg.assert_threshold, region)
        if hit is not None:
            self.log(f"loadout check: {choice} still equipped ({hit.score:.2f})")
            return
        # One miss can be a killfeed card sitting over the name plate.
        self._tick(1.2)
        retry = self.matcher.find_edges(
            self._grab(), template, self.cfg.assert_threshold, region
        )
        if retry is not None:
            self.log(f"loadout check: {choice} still equipped ({retry.score:.2f}, on retry)")
            return
        self._need_reselect = True
        self.log(f"loadout check FAILED: {choice} is not equipped - re-picking after the next death")
        self.stats.mark(stats_mod.DROP, "wrong loadout")

    # -- end of round ------------------------------------------------------

    def _hold(self, reading) -> None:
        """Stop dead for the last seconds of the round.

        The round drops straight into the weapon select with no spectator
        screen in between, so a jump or a stray click in those final seconds
        picks a weapon for you. No input at all goes out until a real screen
        appears.
        """
        if not self._holding:
            self._holding = True
            self._combat_state = None
            self.on_stage(STAGE_TEXT["holding"])
            self.log(f"clock reads {reading.text} - holding, no input until the picker")
        self._tick(self.cfg.hold_poll)

    def _resume(self) -> None:
        if self._holding:
            self._holding = False
            self.on_stage(STAGE_TEXT[detect.UNKNOWN])
            self.log("clock moved on - back to grinding")

    def _remember_picker(self, scene: Scene) -> None:
        """Cache where the Random card sits, for the cheap in-combat probe."""
        if scene.match is None:
            return
        left, top, width, height = scene.match.rect
        pad = int(max(width, height) * 0.6)
        self._picker_probe = (left - pad, top - pad, width + 2 * pad, height + 2 * pad)

    def _leave_probe(self) -> tuple[int, int, int, int] | None:
        """Bottom-right corner, where the leaderboard's Leave button sits."""
        if self.screen is None:
            return None
        width = int(self.screen.width * 0.34)
        height = int(self.screen.height * 0.24)
        return (
            self.screen.left + self.screen.width - width,
            self.screen.top + self.screen.height - height,
            width,
            height,
        )

    def _centre_probe(self) -> tuple[int, int, int, int] | None:
        """Middle of the screen, where Roblox puts its modal dialogs."""
        if self.screen is None:
            return None
        width = int(self.screen.width * 0.22)
        height = int(self.screen.height * 0.28)
        return (
            self.screen.left + self.screen.width // 2 - width // 2,
            self.screen.top + self.screen.height // 2 - height // 2,
            width,
            height,
        )

    def _respawn_probe(self) -> tuple[int, int, int, int] | None:
        """Where the Respawn button appears, a little below centre."""
        if self.screen is None:
            return None
        return (
            self.screen.left + int(self.screen.width * 0.36),
            self.screen.top + int(self.screen.height * 0.62),
            int(self.screen.width * 0.28),
            int(self.screen.height * 0.18),
        )

    def _round_interrupted(self) -> str | None:
        """Small-region matches off one capture, far cheaper than identify.

        This is what actually stops the macro swinging into something it should
        not touch: the leaderboard ends the round, the picker means a respawn,
        and a dialog means the connection went. All reachable in half a second
        instead of waiting out the next full scene read.
        """
        if self.matcher is None:
            return None
        frame = self._grab()
        corner = self._leave_probe()
        if corner and self.matcher.find(
            frame, detect.LEAVE_BUTTON, self.cfg.threshold, region=corner
        ):
            return "leaderboard"
        if self._picker_probe and self.matcher.find(
            frame, detect.RANDOM_CARD, self.cfg.threshold, region=self._picker_probe
        ):
            return "weapon select"
        respawn = self._respawn_probe()
        if respawn:
            if self.matcher.find(frame, detect.RESPAWN, self.cfg.threshold, region=respawn):
                return "respawn screen"
            # Being moved into spectate looks exactly like being alive to the
            # health bar, so the probe has to watch for the Join button too.
            if self.matcher.find(frame, detect.JOIN_BUTTON, self.cfg.threshold, region=respawn):
                return "spectate screen"
        # A dropped connection is not urgent the way a round ending is, and
        # the full scene read catches it anyway - so it rides a slower cadence
        # and keeps the half-second probe cheap.
        now = time.monotonic()
        centre = self._centre_probe() if now >= self._dialog_probe_at else None
        if centre:
            self._dialog_probe_at = now + self.cfg.dialog_probe_interval
            for name, label in (
                (detect.RECONNECT_BUTTON, "disconnect dialog"),
                (detect.CONNFAIL_TITLE, "connection-failed dialog"),
            ):
                floor = max(self.cfg.threshold, detect.SENTINEL_FLOOR.get(name, 0.0))
                if self.matcher.find(frame, name, floor, region=centre, lock=False):
                    return label
        return None

    def _recover(self) -> None:
        """Bail out of a wedged session: Escape, L, Enter leaves the game."""
        self.stats.mark(stats_mod.RECOVER, "left the game")
        self.log(f"{self.cfg.recover_after:.0f} s stuck - leaving the game (Esc, L, Enter)")
        self.on_stage(STAGE_TEXT["recovering"])
        for key, pause in (("esc", 0.8), ("l", 0.5), ("enter", 3.0)):
            inputs.key_tap(key)
            self._tick(pause)
        self.log("sent the leave sequence - expecting the Roblox game page")

    # -- scenes ------------------------------------------------------------

    def _do_lobby(self, scene: Scene) -> None:
        assert scene.match is not None
        self._click_match(scene.match, settle=1.5)
        self.log("clicked Play")
        self._seek_ffa()

    def _do_modes(self, scene: Scene) -> None:
        assert scene.match is not None
        self._click_match(scene.match, settle=1.5)
        self.log("clicked Free For All")

    def _do_spectate(self, scene: Scene) -> None:
        assert scene.match is not None
        self._click_match(scene.match, settle=1.5)
        self.log("clicked Join this game")

    def _do_reconnect(self, scene: Scene) -> None:
        """Roblox dropped us. Clicking Reconnect lands back in the lobby."""
        assert scene.match is not None
        self._click_match(scene.match, settle=1.0)
        self.log(f"clicked Reconnect - waiting {self.cfg.home_load_wait:.0f}s to load")
        self._tick(self.cfg.home_load_wait)

    def _do_connfail(self, scene: Scene) -> None:
        """"Connection Failed": Cancel drops us on the Roblox game page.

        The dialog is found by its title, not its buttons — Cancel and the
        disconnect dialog's Leave are the same outlined button with a different
        word, and they cross-match at 0.893. So the Cancel button is only ever
        hunted inside the dialog the title already identified.
        """
        assert scene.match is not None and self.matcher is not None
        cx, cy = scene.match.center
        reach = scene.match.width * 3
        region = (cx - reach, cy, 2 * reach, reach)
        cancel = self.matcher.find(
            self._grab(), detect.CANCEL_BUTTON, self.cfg.threshold, region=region
        )
        if cancel is None:
            self.log("connection failed, but the Cancel button was not found - waiting")
            self._tick(1.0)
            return
        self._click_match(cancel, settle=1.0)
        self.log(f"clicked Cancel - waiting {self.cfg.home_load_wait:.0f}s for the game page")
        self._tick(self.cfg.home_load_wait)

    def _do_home(self, scene: Scene) -> None:
        """Roblox game page: hit the blue Play button and wait for the load."""
        assert scene.match is not None
        self._click_match(scene.match, settle=1.0)
        self.log(f"clicked Play on the game page - waiting {self.cfg.home_load_wait:.0f}s to load")
        self._tick(self.cfg.home_load_wait)

    def _seek_ffa(self) -> None:
        """Park the cursor mid-screen and wheel down until the banner shows."""
        assert self.screen is not None and self.detector is not None
        cx, cy = self.screen.center
        inputs.glide_to(cx, cy, duration=0.18)
        self._tick(0.5)
        for burst in range(self.cfg.scroll_bursts + 1):
            hit = self.detector.find_ffa(self._grab())
            if hit:
                self._click_match(hit, settle=1.5)
                self.log(f"clicked Free For All (after {burst} scrolls)")
                return
            if burst == self.cfg.scroll_bursts:
                break
            inputs.scroll(-self.cfg.scroll_notches)
            self._tick(0.3)
        self.log("Free For All never appeared - backing out to re-read the screen")

    # -- loadout picker ----------------------------------------------------

    def _is_dead(self, frame: Frame) -> bool:
        """Respawn button up? Cheap, region-restricted."""
        if self.matcher is None:
            return False
        region = self._respawn_probe()
        if region is None:
            return False
        return self.matcher.find(
            frame, detect.RESPAWN, self.cfg.threshold, region=region
        ) is not None

    def _do_picker(self, frame: Frame, scene: Scene) -> None:
        assert scene.grid is not None
        # You can be killed while the weapon select is open. Cards clicked
        # while dead do nothing, so the macro would click, wait out the full
        # slot timeout, and repeat - four times over.
        if self._is_dead(frame):
            self.log("killed during weapon select - respawning before picking")
            self.stats.grinding(False)
            self._do_dead()
            return
        grid = scene.grid
        slot_no = scene.slot
        if slot_no is None:
            self.log("slot indicator unreadable - treating as primary")
            slot_no = 0
        slot = weapons.SLOTS[slot_no]
        choice = self.cfg.loadout.get(slot, "random")

        if choice == "skip":
            self.log(f"{slot}: skip is not possible in game, taking Random instead")
            choice = "random"

        cell, why = self._choose_cell(frame, grid, slot, choice)
        if cell is None:
            self.log(f"{slot}: no clickable card found, waiting")
            self._tick(0.5)
            return

        before = picker.signature(frame, grid)
        self.log(f"{slot}: clicking cell {cell.row},{cell.col} ({why})")
        self._click(cell.x, cell.y, jitter=8, settle=0.35)
        self._await_advance(grid, before, slot_no)

    def _choose_cell(self, frame: Frame, grid: Grid, slot: str, choice: str):
        """Pick a grid cell. Only ever returns a cell centre, never a raw match."""
        assert self.matcher is not None
        if choice == "random":
            first = grid.cells[0]
            if first.clickable:
                return first, "Random card"

        if choice not in ("random", "any"):
            names = weapons.template_names(self.catalogue, slot, choice)
            hit = self.matcher.find_best(
                frame, names, self.cfg.weapon_threshold, region=grid.bbox()
            )
            if hit:
                cell = grid.nearest(*hit.center)
                if cell and cell.clickable:
                    return cell, f"{choice} @ {hit.score:.2f}"
                self.log(f"{slot}: {choice} matched but not on a usable cell")
            else:
                per_cell = self._argmax_cell(frame, grid, names)
                if per_cell is not None:
                    score, cell, margin = per_cell
                    return cell, f"{choice} @ {score:.2f} per-cell, margin {margin:.2f}"
                self.log(f"{slot}: {choice} not offered this round")
            first = grid.cells[0]
            if first.clickable:
                return first, "fallback to Random"

        options = grid.weapon_cells() or grid.available()
        if options:
            return random.choice(options), "any available"
        return None, ""

    def _argmax_cell(self, frame: Frame, grid: Grid, names: list[str]):
        """Score a card against every cell alone; accept a clear winner only.

        A card partly hidden by a neighbouring weapon model scores too low for
        the flat threshold but still beats every other cell by a wide margin.
        A card that simply is not on screen does not.
        """
        assert self.matcher is not None
        pad = int(grid.cell_size * 0.62)
        best = None
        for name in names:
            scored = []
            for cell in grid.cells:
                region = (cell.x - pad, cell.y - pad, 2 * pad, 2 * pad)
                hit = self.matcher.find(frame, name, 0.0, region=region, lock=False)
                scored.append(((hit.score if hit else 0.0), cell))
            scored.sort(key=lambda pair: -pair[0])
            (top_score, top_cell), (runner_up, _) = scored[0], scored[1]
            margin = top_score - runner_up
            if (
                top_cell.clickable
                and top_score >= self.cfg.argmax_floor
                and margin >= self.cfg.argmax_margin
                and (best is None or top_score > best[0])
            ):
                best = (top_score, top_cell, margin)
        return best

    def _await_advance(self, grid: Grid, before, slot_no: int) -> None:
        """Wait for the picker to actually move on before clicking again."""
        assert self.matcher is not None
        deadline = time.monotonic() + self.cfg.slot_timeout
        while time.monotonic() < deadline:
            self._tick(0.2)
            frame = self._grab()
            if self._is_dead(frame):
                self.log("died mid-pick - dropping out of the slot wait")
                return
            now_slot = picker.slot_index(frame, grid)
            if now_slot is not None and now_slot != slot_no:
                return
            if picker.changed(before, picker.signature(frame, grid)):
                return
            if not self.matcher.find(frame, detect.RANDOM_CARD, self.cfg.threshold):
                return  # picker closed entirely - loadout is done
        self.log("picker did not advance - re-reading the screen")

    # -- combat ------------------------------------------------------------

    def _combat_burst(self, duration: float) -> None:
        """Jump, sweep and fire for `duration`, then hand back for a re-check."""
        assert self.screen is not None
        cx, cy = self.screen.center
        rx = self.cfg.oval_rx * self.screen.width
        ry = self.cfg.oval_ry * self.screen.height
        box_w = self.cfg.click_box_w * self.screen.width
        box_h = self.cfg.click_box_h * self.screen.height
        relative = self.cfg.camera_mode == "relative"

        if not self._ensure_focus():
            self._tick(0.5)
            return
        state = self._combat_state
        if state is None:
            inputs.glide_to(cx, cy, duration=0.15)
            inputs.click()  # hand focus and the cursor lock back to the game
            state = {
                "angle": random.uniform(0, math.tau),
                "speed": self.cfg.oval_speed * random.choice((1, -1)),
                "phase": random.uniform(0, math.tau),
            }
            self._combat_state = state
            self.log("combat loop running (jump spam + camera sweep + fire)")

        now = time.monotonic()
        end = now + duration
        next_probe = now + self.cfg.probe_interval
        next_space = now
        next_click = now + random.uniform(self.cfg.click_min, self.cfg.click_max)
        next_reroll = now + random.uniform(3.0, 8.0)
        prev = (0.0, 0.0)
        last = now

        while not self.stopping and time.monotonic() < end:
            if inputs.is_pressed(inputs.VK_F9):
                self.stop()
                raise Stopped
            now = time.monotonic()
            dt = min(0.1, now - last)
            last = now

            state["angle"] += state["speed"] * dt
            wobble = 1.0 + 0.22 * math.sin(now * 0.7 + state["phase"])
            tx = math.cos(state["angle"]) * rx * wobble
            ty = math.sin(state["angle"]) * ry * (2.0 - wobble)
            if relative:
                inputs.move_relative(round(tx - prev[0]), round(ty - prev[1]))
            else:
                inputs.move_to(int(cx + tx), int(cy + ty))
            prev = (tx, ty)

            if now >= next_reroll:
                state["speed"] = (
                    self.cfg.oval_speed * random.uniform(0.6, 1.5) * random.choice((1, -1))
                )
                state["phase"] = random.uniform(0, math.tau)
                next_reroll = now + random.uniform(3.0, 8.0)

            if now >= next_space:
                inputs.key_tap("space")
                jitter = random.uniform(-self.cfg.space_jitter, self.cfg.space_jitter)
                next_space = now + max(0.1, self.cfg.space_interval + jitter)

            if now >= next_click:
                if relative:
                    inputs.click()
                else:
                    inputs.click_at(
                        int(cx + random.uniform(-box_w / 2, box_w / 2)),
                        int(cy + random.uniform(-box_h / 2, box_h / 2)),
                        glide=False,
                    )
                next_click = now + random.uniform(self.cfg.click_min, self.cfg.click_max)

            if now >= next_probe:
                next_probe = now + self.cfg.probe_interval
                if not self._ensure_focus():
                    return
                interrupt = self._round_interrupted()
                if interrupt is not None:
                    self.log(f"{interrupt} is up - stopping input")
                    return

            self._tick(0.016)
