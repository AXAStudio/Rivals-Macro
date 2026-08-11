"""Find and focus the Roblox window.

A click into an unfocused window is swallowed activating it, so the first
thing the macro clicks after you press Start does nothing — Start moves focus
to the control panel, and the click that follows only gives it back.

Focus is taken through the API rather than by clicking, so no input is spent
on it. `SetForegroundWindow` alone is refused unless the caller already owns
the foreground, hence the `AttachThreadInput` dance.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9
GAME_CLASS = "WINDOWSCLIENT"  # the Roblox player and app both use this
TITLE_HINT = "roblox"

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def rect(hwnd: int) -> tuple[int, int, int, int]:
    box = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(box))
    return box.left, box.top, box.right - box.left, box.bottom - box.top


def title(hwnd: int) -> str:
    return _text(hwnd)


def alive(hwnd: int | None) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def foreground() -> int:
    return user32.GetForegroundWindow()


def is_foreground(hwnd: int | None) -> bool:
    return bool(hwnd) and user32.GetForegroundWindow() == hwnd


def find_game() -> int | None:
    """The largest visible Roblox window, by class first then by title."""
    by_class: list[int] = []
    by_title: list[int] = []

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if _class(hwnd) == GAME_CLASS:
            by_class.append(hwnd)
        elif TITLE_HINT in _text(hwnd).lower():
            by_title.append(hwnd)
        return True

    user32.EnumWindows(_ENUM_PROC(visit), 0)
    for group in (by_class, by_title):
        real = [h for h in group if rect(h)[2] > 200 and rect(h)[3] > 200]
        if real:
            return max(real, key=lambda h: rect(h)[2] * rect(h)[3])
    return None


def focus(hwnd: int) -> bool:
    """Bring a window to the foreground. Returns whether it actually took."""
    if not alive(hwnd):
        return False
    if is_foreground(hwnd):
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    ours = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(hwnd, None)
    current = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)

    attached = []
    for thread in {target, current} - {ours, 0}:
        if user32.AttachThreadInput(ours, thread, True):
            attached.append(thread)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        for thread in attached:
            user32.AttachThreadInput(ours, thread, False)
    return is_foreground(hwnd)


def describe(hwnd: int | None) -> str:
    if not alive(hwnd):
        return "no Roblox window"
    left, top, width, height = rect(hwnd)
    return f'"{title(hwnd)}" {width}x{height} @ ({left},{top})'
