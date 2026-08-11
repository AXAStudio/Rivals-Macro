"""Dark theme for the control panel.

ttk's default themes hardcode most colours; `clam` is the one that exposes
enough knobs to restyle, so everything here builds on it. Combobox and Spinbox
popups are plain Tk widgets underneath and ignore ttk styling entirely, which
is what the `option_add` calls are for.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#0f1116"
CARD = "#171a21"
RAISED = "#1e222b"
BORDER = "#252932"
FG = "#e6e8ee"
MUTED = "#868c9e"
DIM = "#5d6375"

ACCENT = "#4c8dff"
ACCENT_HOVER = "#6ba0ff"
GOOD = "#3ddc84"
WARN = "#ffb020"
BAD = "#ff5f56"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI Semibold", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_LABEL = ("Segoe UI Semibold", 8)
FONT_TITLE = ("Segoe UI Semibold", 14)
FONT_MONO = ("Cascadia Mono", 9)


def apply(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.background", RAISED)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", FONT)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style.configure(".", background=BG, foreground=FG, font=FONT, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Header.TFrame", background=CARD)

    style.configure("TLabel", background=BG, foreground=FG, font=FONT)
    style.configure("Card.TLabel", background=CARD, foreground=FG, font=FONT)
    style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=FONT_SMALL)
    style.configure("Dim.TLabel", background=CARD, foreground=DIM, font=FONT_SMALL)
    style.configure("Section.TLabel", background=CARD, foreground=MUTED, font=FONT_LABEL)
    style.configure("Title.TLabel", background=CARD, foreground=FG, font=FONT_TITLE)
    style.configure("Status.TLabel", background=CARD, foreground=FG, font=FONT_BOLD)

    # --- buttons ---------------------------------------------------------
    style.configure(
        "TButton", background=RAISED, foreground=FG, font=FONT, padding=(14, 7),
        borderwidth=0, focuscolor=RAISED, relief="flat",
    )
    style.map(
        "TButton",
        background=[("pressed", BORDER), ("active", BORDER), ("disabled", CARD)],
        foreground=[("disabled", DIM)],
    )
    style.configure(
        "Accent.TButton", background=ACCENT, foreground="#ffffff", font=FONT_BOLD,
        padding=(18, 10), borderwidth=0, focuscolor=ACCENT,
    )
    style.map("Accent.TButton", background=[("pressed", ACCENT), ("active", ACCENT_HOVER)])
    style.configure(
        "Stop.TButton", background=BAD, foreground="#ffffff", font=FONT_BOLD,
        padding=(18, 10), borderwidth=0, focuscolor=BAD,
    )
    style.map("Stop.TButton", background=[("pressed", BAD), ("active", "#ff7a72")])
    style.configure(
        "Ghost.TButton", background=CARD, foreground=MUTED, font=FONT_SMALL,
        padding=(9, 5), borderwidth=0, focuscolor=CARD,
    )
    style.map("Ghost.TButton", background=[("active", RAISED)], foreground=[("active", FG)])

    # --- inputs ----------------------------------------------------------
    for widget in ("TCombobox", "TSpinbox"):
        style.configure(
            widget, fieldbackground=RAISED, background=RAISED, foreground=FG,
            arrowcolor=MUTED, bordercolor=BORDER, lightcolor=RAISED, darkcolor=RAISED,
            insertcolor=FG, padding=(8, 6), borderwidth=0, relief="flat",
        )
        style.map(
            widget,
            fieldbackground=[("readonly", RAISED), ("focus", RAISED)],
            background=[("readonly", RAISED), ("active", RAISED)],
            foreground=[("readonly", FG), ("disabled", DIM)],
            arrowcolor=[("active", FG)],
            bordercolor=[("focus", ACCENT)],
            lightcolor=[("focus", ACCENT)],
            darkcolor=[("focus", ACCENT)],
        )
    style.configure("TCombobox", selectbackground=RAISED, selectforeground=FG)

    style.configure(
        "TCheckbutton", background=CARD, foreground=FG, font=FONT_SMALL,
        focuscolor=CARD, indicatorbackground=RAISED, indicatorforeground="#ffffff",
        bordercolor=BORDER, lightcolor=RAISED, darkcolor=RAISED,
        indicatormargin=(0, 0, 8, 0), padding=(0, 5), indicatorrelief="flat",
    )
    style.map(
        "TCheckbutton",
        background=[("active", CARD)],
        foreground=[("disabled", DIM)],
        indicatorbackground=[("selected", ACCENT), ("active", BORDER)],
        indicatorforeground=[("selected", "#ffffff")],
        bordercolor=[("selected", ACCENT), ("focus", ACCENT)],
        lightcolor=[("selected", ACCENT)],
        darkcolor=[("selected", ACCENT)],
    )

    # segmented preset picker
    style.configure(
        "Seg.Toolbutton", background=RAISED, foreground=MUTED, font=FONT_SMALL,
        padding=(12, 7), borderwidth=0, focuscolor=RAISED, relief="flat", anchor="center",
    )
    style.map(
        "Seg.Toolbutton",
        background=[("selected", ACCENT), ("active", BORDER)],
        foreground=[("selected", "#ffffff"), ("active", FG)],
    )

    # clam draws stepper arrows by default and they will not theme cleanly;
    # a trough-and-thumb-only layout is both tidier and simpler to colour.
    style.layout(
        "Dark.Vertical.TScrollbar",
        [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})],
    )
    style.configure(
        "Dark.Vertical.TScrollbar", background=BORDER, troughcolor=BG, bordercolor=BG,
        lightcolor=BORDER, darkcolor=BORDER, arrowcolor=BG, width=9, borderwidth=0,
    )
    style.map("Dark.Vertical.TScrollbar", background=[("active", DIM), ("pressed", DIM)])
    style.configure("Sep.TFrame", background=BORDER)
    return style


def dark_titlebar(root: tk.Tk) -> None:
    """Ask DWM for the dark title bar so the frame matches the window."""
    import ctypes

    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        flag = ctypes.c_int(1)
        for attribute in (20, 19):  # 20 on Win10 1903+/11, 19 on older builds
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(flag), ctypes.sizeof(flag)
            ) == 0:
                return
    except Exception:  # pragma: no cover - cosmetic only
        pass


def card(parent: tk.Widget, **kw) -> ttk.Frame:
    """A padded panel. Tk has no rounded corners, so this is flat by design."""
    frame = ttk.Frame(parent, style="Card.TFrame", padding=kw.pop("padding", 16))
    return frame


def section(parent: tk.Widget, text: str) -> ttk.Label:
    return ttk.Label(parent, text=text.upper(), style="Section.TLabel")


def rule(parent: tk.Widget) -> ttk.Frame:
    line = ttk.Frame(parent, style="Sep.TFrame", height=1)
    return line
