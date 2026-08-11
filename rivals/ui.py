"""Control panel: presets, loadout, tuning, start/stop, live log."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import detect, inputs, picker, theme, weapons, window
from .stats import Stats
from .timeline import Timeline, legend
from .clock import ClockReader
from .config import BUILTIN_PRESETS, TEMPLATES_DIR, Config
from .detect import Detector
from .macro import MacroRunner
from .vision import Matcher, Screen

SPECIAL_CHOICES = [("Random card", "random"), ("Any offered card", "any")]

# fragment of the status text -> colour of the dot beside it
STATUS_TINT = (
    ("stuck", theme.WARN),
    ("holding", theme.WARN),
    ("waiting", theme.WARN),
    ("disconnected", theme.WARN),
    ("connection failed", theme.WARN),
    ("idle", theme.DIM),
)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = Config.load()
        self.catalogue = weapons.discover(TEMPLATES_DIR)
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.runner: MacroRunner | None = None
        self._hotkeys_down = {inputs.VK_F8: False, inputs.VK_F9: False}
        self._settings_open = False
        self.stats = Stats(self.cfg.lifetime_grind_seconds)
        self._applying = False  # suppress preset-dirtying while applying one

        root.title("Rivals Macro")
        root.geometry("680x960")
        root.minsize(640, 680)
        theme.apply(root)
        theme.dark_titlebar(root)
        self._build()
        self._rebuild_presets()
        self._pump()
        self._poll_hotkeys()
        self._tick_graph()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=(14, 14, 14, 12))
        outer.pack(fill="both", expand=True)
        self._build_header(outer)
        self._build_loadout(outer)
        self._build_controls(outer)
        self.settings = theme.card(outer)
        self._build_settings()
        self._build_log(outer)

    def _build_header(self, parent: ttk.Frame) -> None:
        head = theme.card(parent, padding=(16, 13))
        head.pack(fill="x")
        left = ttk.Frame(head, style="Card.TFrame")
        left.pack(side="left")
        ttk.Label(left, text="Rivals Macro", style="Title.TLabel").pack(anchor="w")
        self.subtitle = ttk.Label(left, text="", style="Dim.TLabel")
        self.subtitle.pack(anchor="w")

        right = ttk.Frame(head, style="Card.TFrame")
        right.pack(side="right")
        pill = ttk.Frame(right, style="Card.TFrame")
        pill.pack(anchor="e")
        self.dot = tk.Label(pill, text="●", bg=theme.CARD, fg=theme.DIM,
                            font=("Segoe UI", 11))
        self.dot.pack(side="left", padx=(0, 6))
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(pill, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        ttk.Label(right, text="F8 start / stop      F9 panic", style="Dim.TLabel").pack(
            anchor="e", pady=(3, 0)
        )

    def _build_loadout(self, parent: ttk.Frame) -> None:
        card = theme.card(parent)
        card.pack(fill="x", pady=(10, 0))

        top = ttk.Frame(card, style="Card.TFrame")
        top.pack(fill="x")
        theme.section(top, "Preset").pack(side="left")
        ttk.Button(top, text="Delete", style="Ghost.TButton", command=self.delete_preset).pack(
            side="right"
        )
        ttk.Button(top, text="Save as…", style="Ghost.TButton",
                   command=self.save_preset).pack(side="right", padx=(0, 4))

        self.preset_var = tk.StringVar(value=self.cfg.preset)
        self.preset_row = ttk.Frame(card, style="Card.TFrame")
        self.preset_row.pack(fill="x", pady=(9, 2))
        self.custom_note = ttk.Label(card, text="", style="Dim.TLabel")
        self.custom_note.pack(anchor="w")

        theme.rule(card).pack(fill="x", pady=12)
        theme.section(card, "Loadout").pack(anchor="w", pady=(0, 9))

        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        self.slot_boxes: dict[str, ttk.Combobox] = {}
        self.slot_maps: dict[str, dict[str, str]] = {}
        for row, slot in enumerate(weapons.SLOTS):
            ttk.Label(grid, text=weapons.SLOT_LABELS[slot], style="Card.TLabel").grid(
                row=row, column=0, sticky="w", pady=4, padx=(0, 14)
            )
            options = list(SPECIAL_CHOICES) + [
                (w.label, w.key) for w in self.catalogue.get(slot, [])
            ]
            self.slot_maps[slot] = {label: value for label, value in options}
            value_to_label = {value: label for label, value in options}
            box = ttk.Combobox(grid, values=list(self.slot_maps[slot]), state="readonly")
            box.set(value_to_label.get(self.cfg.loadout.get(slot, "random"), "Random card"))
            box.grid(row=row, column=1, sticky="ew", pady=4)
            box.bind("<<ComboboxSelected>>", lambda _e: self._loadout_changed())
            self.slot_boxes[slot] = box

        ttk.Label(
            card,
            text="Not every weapon is offered each round. When yours is absent the macro\n"
                 "takes the Random card rather than guessing.",
            style="Dim.TLabel", justify="left",
        ).pack(anchor="w", pady=(10, 0))

    def _build_controls(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(10, 0))
        self.start_btn = ttk.Button(row, text="▶  Start", style="Accent.TButton",
                                    command=self.toggle)
        self.start_btn.pack(side="left")
        ttk.Button(row, text="Scan screen", command=self.test_detection).pack(
            side="left", padx=(8, 0)
        )
        self.settings_btn = ttk.Button(row, text="Settings  ▾", command=self.toggle_settings)
        self.settings_btn.pack(side="right")

    def _spin(self, host, row, text, var, lo, hi, step, hint=""):
        ttk.Label(host, text=text, style="Card.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 14), pady=4
        )
        ttk.Spinbox(host, from_=lo, to=hi, increment=step, width=7, textvariable=var).grid(
            row=row, column=1, sticky="w", pady=4
        )
        if hint:
            ttk.Label(host, text=hint, style="Dim.TLabel").grid(
                row=row, column=2, sticky="w", padx=(12, 0), pady=4
            )

    def _build_settings(self) -> None:
        theme.section(self.settings, "Capture").pack(anchor="w")
        cap = ttk.Frame(self.settings, style="Card.TFrame")
        cap.pack(fill="x", pady=(9, 0))
        cap.columnconfigure(1, weight=1)
        ttk.Label(cap, text="Monitor", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 14), pady=4
        )
        monitors = Screen.describe()
        self.monitor_box = ttk.Combobox(cap, values=monitors, state="readonly")
        self.monitor_box.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
        index = self.cfg.monitor - 1
        self.monitor_box.current(index if 0 <= index < len(monitors) else 0)

        self.threshold_var = tk.DoubleVar(value=self.cfg.threshold)
        self.weapon_var = tk.DoubleVar(value=self.cfg.weapon_threshold)
        self._spin(cap, 1, "Scene threshold", self.threshold_var, 0.5, 0.98, 0.01, "0.85 default")
        self._spin(cap, 2, "Weapon threshold", self.weapon_var, 0.5, 0.98, 0.01, "0.78 default")
        self.focus_var = tk.BooleanVar(value=self.cfg.focus_game)
        ttk.Checkbutton(cap, text="Focus the game window before clicking",
                        variable=self.focus_var).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(7, 0)
        )

        theme.rule(self.settings).pack(fill="x", pady=13)
        theme.section(self.settings, "Combat").pack(anchor="w")
        com = ttk.Frame(self.settings, style="Card.TFrame")
        com.pack(fill="x", pady=(9, 0))
        ttk.Label(com, text="Camera", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 14), pady=4
        )
        self.camera_var = tk.StringVar(value=self.cfg.camera_mode)
        ttk.Combobox(com, values=["relative", "absolute"], state="readonly", width=12,
                     textvariable=self.camera_var).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(com, text="relative = FPS mouse lock", style="Dim.TLabel").grid(
            row=0, column=2, sticky="w", padx=(12, 0), pady=4
        )
        self.space_var = tk.DoubleVar(value=self.cfg.space_interval)
        self.clickmin_var = tk.DoubleVar(value=self.cfg.click_min)
        self.clickmax_var = tk.DoubleVar(value=self.cfg.click_max)
        self.ovalrx_var = tk.DoubleVar(value=self.cfg.oval_rx)
        self.ovalry_var = tk.DoubleVar(value=self.cfg.oval_ry)
        self._spin(com, 1, "Jump every", self.space_var, 0.1, 3.0, 0.05, "seconds")
        self._spin(com, 2, "Fire every, min", self.clickmin_var, 0.05, 3.0, 0.05, "seconds")
        self._spin(com, 3, "Fire every, max", self.clickmax_var, 0.05, 5.0, 0.05, "seconds")
        self._spin(com, 4, "Sweep width", self.ovalrx_var, 0.01, 0.5, 0.01, "fraction of screen")
        self._spin(com, 5, "Sweep height", self.ovalry_var, 0.01, 0.5, 0.01, "fraction of screen")

        theme.rule(self.settings).pack(fill="x", pady=13)
        theme.section(self.settings, "End of round and recovery").pack(anchor="w")
        end = ttk.Frame(self.settings, style="Card.TFrame")
        end.pack(fill="x", pady=(9, 0))
        self.hold_var = tk.IntVar(value=self.cfg.hold_seconds)
        self.recover_var = tk.DoubleVar(value=self.cfg.recover_after)
        self._spin(end, 0, "Hold below", self.hold_var, 0, 59, 1, "seconds left on the clock")
        self._spin(end, 1, "Wedged after", self.recover_var, 15, 600, 5,
                   "seconds with no progress")
        self.assert_var = tk.BooleanVar(value=self.cfg.assert_loadout)
        ttk.Checkbutton(
            end, text="Assert the primary is still equipped", variable=self.assert_var
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(7, 0))
        self.assert_every = tk.DoubleVar(value=self.cfg.assert_interval / 60.0)
        self._spin(end, 3, "Check every", self.assert_every, 1, 120, 1, "minutes")

        self.recovery_var = tk.BooleanVar(value=self.cfg.enable_recovery)
        ttk.Checkbutton(
            end, text="Leave and relaunch if the session wedges (Esc, L, Enter)",
            variable=self.recovery_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(7, 0))
        ttk.Label(
            self.settings,
            text="The leaderboard is what stops the macro at the end of a round.\n"
                 "The clock hold is only a backstop.",
            style="Dim.TLabel", justify="left",
        ).pack(anchor="w", pady=(10, 0))
        bar = ttk.Frame(self.settings, style="Card.TFrame")
        bar.pack(fill="x", pady=(12, 0))
        ttk.Button(bar, text="Save settings", style="Ghost.TButton", command=self.save).pack(
            side="right"
        )

    def _build_log(self, parent: ttk.Frame) -> None:
        self.log_card = theme.card(parent, padding=(12, 12))
        self.log_card.pack(fill="both", expand=True, pady=(10, 0))
        self.timeline = Timeline(self.log_card)
        self.timeline.pack(fill="x")
        legend(self.log_card).pack(anchor="w", pady=(6, 10))
        theme.rule(self.log_card).pack(fill="x", pady=(0, 10))
        body = ttk.Frame(self.log_card, style="Card.TFrame")
        body.pack(fill="both", expand=True)
        self.log_box = tk.Text(
            body, height=7, wrap="none", state="disabled", relief="flat",
            bg=theme.BG, fg=theme.MUTED, font=theme.FONT_MONO,
            insertbackground=theme.FG, selectbackground=theme.BORDER,
            padx=10, pady=8, highlightthickness=0, borderwidth=0,
        )
        scroll = ttk.Scrollbar(body, command=self.log_box.yview,
                               style="Dark.Vertical.TScrollbar")
        self.log_box.configure(yscrollcommand=scroll.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for tag, colour in (("good", theme.GOOD), ("warn", theme.WARN),
                            ("bad", theme.BAD), ("plain", theme.FG)):
            self.log_box.tag_configure(tag, foreground=colour)

    # -- presets -----------------------------------------------------------

    def _all_presets(self) -> dict[str, dict]:
        merged = dict(BUILTIN_PRESETS)
        merged.update(self.cfg.presets or {})
        return merged

    def _rebuild_presets(self) -> None:
        for child in self.preset_row.winfo_children():
            child.destroy()
        for name in self._all_presets():
            ttk.Radiobutton(
                self.preset_row, text=name, value=name, variable=self.preset_var,
                style="Seg.Toolbutton", command=lambda n=name: self.apply_preset(n),
            ).pack(side="left", padx=(0, 6))
        self._sync_preset_state()

    def _current_loadout(self) -> dict[str, str]:
        return {
            slot: self.slot_maps[slot].get(box.get(), "random")
            for slot, box in self.slot_boxes.items()
        }

    def _sync_preset_state(self) -> None:
        """Light the chip matching the dropdowns, or none of them."""
        current = self._current_loadout()
        for name, loadout in self._all_presets().items():
            if all(loadout.get(s) == current.get(s) for s in weapons.SLOTS):
                self.preset_var.set(name)
                self.custom_note.configure(text="")
                self.subtitle.configure(text=name.lower())
                return
        self.preset_var.set("")
        self.custom_note.configure(text="custom loadout — not saved as a preset")
        self.subtitle.configure(text="custom loadout")

    def apply_preset(self, name: str) -> None:
        loadout = self._all_presets().get(name)
        if not loadout:
            return
        self._applying = True
        for slot, box in self.slot_boxes.items():
            wanted = loadout.get(slot, "random")
            for label, value in self.slot_maps[slot].items():
                if value == wanted:
                    box.set(label)
                    break
        self._applying = False
        self._sync_preset_state()
        self.log(f"preset: {name}")

    def _loadout_changed(self) -> None:
        if not self._applying:
            self._sync_preset_state()

    def save_preset(self) -> None:
        name = self._ask_name()
        if not name:
            return
        if name in BUILTIN_PRESETS:
            self.log(f"'{name}' is a built-in preset - pick another name", "warn")
            return
        self.cfg.presets = dict(self.cfg.presets or {})
        self.cfg.presets[name] = self._current_loadout()
        self._collect().save()
        self._rebuild_presets()
        self.preset_var.set(name)
        self._sync_preset_state()
        self.log(f"saved preset '{name}'", "good")

    def delete_preset(self) -> None:
        name = self.preset_var.get()
        if not name:
            self.log("no preset selected", "warn")
            return
        if name in BUILTIN_PRESETS:
            self.log(f"'{name}' is built in and cannot be deleted", "warn")
            return
        self.cfg.presets.pop(name, None)
        self._collect().save()
        self._rebuild_presets()
        self.log(f"deleted preset '{name}'")

    def _ask_name(self) -> str | None:
        """Themed prompt; tkinter's own dialog ignores the dark palette."""
        win = tk.Toplevel(self.root)
        win.title("Save preset")
        win.configure(bg=theme.CARD)
        win.transient(self.root)
        win.resizable(False, False)
        self.root.update_idletasks()
        win.geometry(f"330x150+{self.root.winfo_rootx() + 150}+{self.root.winfo_rooty() + 190}")
        body = ttk.Frame(win, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Preset name", style="Section.TLabel").pack(anchor="w")
        var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=var, font=theme.FONT)
        entry.pack(fill="x", pady=(9, 15))
        entry.focus_set()
        result: dict[str, str | None] = {"name": None}

        def ok(_event=None):
            result["name"] = var.get().strip() or None
            win.destroy()

        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="Save", style="Accent.TButton", command=ok).pack(side="right")
        ttk.Button(row, text="Cancel", style="Ghost.TButton", command=win.destroy).pack(
            side="right", padx=(0, 8)
        )
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda _e: win.destroy())
        win.grab_set()
        self.root.wait_window(win)
        return result["name"]

    # -- plumbing ----------------------------------------------------------

    def log(self, message: str, tag: str = "") -> None:
        self.messages.put((message, tag))

    @staticmethod
    def _tag_for(message: str) -> str:
        low = message.lower()
        if low.startswith("error") or "could not" in low or "failed" in low:
            return "bad"
        if any(w in low for w in ("warning", "stuck", "holding", "not offered", "never")):
            return "warn"
        if any(w in low for w in ("clicked", "picked", "focused", "scene:")):
            return "good"
        return "plain"

    def _pump(self) -> None:
        drained = False
        while True:
            try:
                message, tag = self.messages.get_nowait()
            except queue.Empty:
                break
            drained = True
            self.log_box.configure(state="normal")
            self.log_box.insert("end", message + "\n", tag or self._tag_for(message))
            self.log_box.configure(state="disabled")
        if drained:
            self.log_box.see("end")
        if self.runner and not self.runner.is_alive():
            self.runner = None
            self.start_btn.configure(text="▶  Start", style="Accent.TButton")
            self._set_status("Idle")
        self.root.after(80, self._pump)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)
        low = text.lower()
        colour = theme.GOOD if self.runner else theme.DIM
        for fragment, tint in STATUS_TINT:
            if fragment in low:
                colour = tint
                break
        self.dot.configure(fg=colour)

    def _tick_graph(self) -> None:
        self.timeline.render(self.stats.snapshot())
        self.root.after(1000, self._tick_graph)

    def _poll_hotkeys(self) -> None:
        for vk, action in ((inputs.VK_F8, self.toggle), (inputs.VK_F9, self.stop)):
            down = inputs.is_pressed(vk)
            if down and not self._hotkeys_down[vk]:
                action()
            self._hotkeys_down[vk] = down
        self.root.after(120, self._poll_hotkeys)

    def _collect(self) -> Config:
        cfg = self.cfg
        cfg.monitor = (self.monitor_box.current() or 0) + 1
        cfg.threshold = float(self.threshold_var.get())
        cfg.weapon_threshold = float(self.weapon_var.get())
        cfg.focus_game = bool(self.focus_var.get())
        cfg.camera_mode = self.camera_var.get()
        cfg.space_interval = float(self.space_var.get())
        cfg.click_min = float(self.clickmin_var.get())
        cfg.click_max = max(float(self.clickmax_var.get()), float(self.clickmin_var.get()))
        cfg.oval_rx = float(self.ovalrx_var.get())
        cfg.oval_ry = float(self.ovalry_var.get())
        cfg.hold_seconds = int(self.hold_var.get())
        cfg.enable_recovery = bool(self.recovery_var.get())
        cfg.assert_loadout = bool(self.assert_var.get())
        cfg.assert_interval = max(60.0, float(self.assert_every.get()) * 60.0)
        cfg.recover_after = float(self.recover_var.get())
        cfg.loadout = self._current_loadout()
        cfg.preset = self.preset_var.get()
        cfg.lifetime_grind_seconds = self.stats.lifetime_seconds
        return cfg

    # -- actions -----------------------------------------------------------

    def toggle_settings(self) -> None:
        self._settings_open = not self._settings_open
        # Settings replaces the activity view rather than stacking under it:
        # both at once overflows the window and clips the log.
        if self._settings_open:
            self.log_card.pack_forget()
            self.settings.pack(fill="both", expand=True, pady=(10, 0))
            self.settings_btn.configure(text="Settings  ▴")
        else:
            self.settings.pack_forget()
            self.log_card.pack(fill="both", expand=True, pady=(10, 0))
            self.settings_btn.configure(text="Settings  ▾")
        self._refit()

    def _refit(self) -> None:
        """Grow or shrink to the natural height, clamped to the screen."""
        self.root.update_idletasks()
        width = max(self.root.winfo_width(), self.root.winfo_reqwidth())
        height = min(self.root.winfo_reqheight(), int(self.root.winfo_screenheight() * 0.88))
        self.root.geometry(f"{width}x{height}")

    def save(self) -> None:
        self._collect().save()
        self.log("settings saved to config.json", "good")

    def toggle(self) -> None:
        if self.runner and self.runner.is_alive():
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if self.runner and self.runner.is_alive():
            return
        cfg = self._collect()
        cfg.save()
        label = self.preset_var.get() or "custom"
        self.log(f"starting — {label}: "
                 + ", ".join(f"{s}={cfg.loadout[s]}" for s in weapons.SLOTS), "good")
        self.runner = MacroRunner(cfg, self.log, self._set_status, self.stats)
        self.runner.start()
        self.start_btn.configure(text="■  Stop", style="Stop.TButton")
        self._set_status("Starting")

    def stop(self) -> None:
        if self.runner and self.runner.is_alive():
            self.log("stop requested")
            self.runner.stop()
        self.start_btn.configure(text="▶  Start", style="Accent.TButton")

    def test_detection(self) -> None:
        cfg = self._collect()
        self.log("--- reading the screen now ---")
        threading.Thread(target=self._test_worker, args=(cfg,), daemon=True).start()

    def _test_worker(self, cfg: Config) -> None:
        try:
            hwnd = window.find_game()
            state = "focused" if window.is_foreground(hwnd) else "NOT focused"
            self.log(f"game window: {window.describe(hwnd)}  [{state}]")
            screen = Screen(cfg.monitor)
            matcher = Matcher(TEMPLATES_DIR, screen.height)
            detector = Detector(matcher, cfg.threshold)
            frame = screen.grab()
            scene = detector.identify(frame)
            self.log(f"{screen.width}x{screen.height} -> {detect.SCENE_LABELS[scene.name]}",
                     "good")
            if scene.match:
                self.log(f"  {scene.match.name} at {scene.match.center} "
                         f"score {scene.match.score:.3f} scale {scene.match.scale:.3f}")
            if scene.grid:
                slot = "unreadable" if scene.slot is None else weapons.SLOTS[scene.slot]
                self.log(f"  slot: {slot}   cell {scene.grid.cell_size} px")
                for row in range(picker.GRID_ROWS):
                    cells = scene.grid.cells[row * picker.GRID_COLS:(row + 1) * picker.GRID_COLS]
                    self.log("  " + " ".join(f"{c.state[:5]:>6s}" for c in cells))
                names = [w.template for w in self.catalogue.get(weapons.SLOTS[scene.slot or 0], [])]
                found = matcher.score_all(frame, names, region=scene.grid.bbox())
                found.sort(key=lambda r: r[1], reverse=True)
                for name, score, hit in found[:5]:
                    cell = scene.grid.nearest(*hit.center) if hit else None
                    where = f"cell {cell.index}" if cell else "off-grid"
                    mark = "  <= would pick" if score >= cfg.weapon_threshold else ""
                    self.log(f"    {score:.3f}  {name}  {where}{mark}")
            elif scene.name == detect.UNKNOWN:
                self.log("  no sentinel matched - in a match, loading, or the wrong monitor",
                         "warn")
            reading = ClockReader(TEMPLATES_DIR).read(frame)
            if reading is not None:
                self.log(f"  clock: {reading.text} (<= {reading.upper_bound}s left)")
            screen.close()
        except Exception as exc:
            self.log(f"scan failed: {type(exc).__name__}: {exc}", "bad")

    def _on_close(self) -> None:
        self.stop()
        try:
            self._collect().save()
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
