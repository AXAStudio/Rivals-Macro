"""Low level mouse/keyboard synthesis through the Win32 SendInput API.

Roblox ignores a lot of higher level automation, so everything here goes out as
a real INPUT record: keys are sent as scancodes and camera movement is sent as
relative deltas, which is what an FPS with a locked cursor actually reads.
"""

from __future__ import annotations

import ctypes
import random
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

# --- DPI awareness --------------------------------------------------------
try:  # per-monitor v2, so our pixels match mss's pixels on scaled displays
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # pragma: no cover - older Windows
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

# Default Windows timer granularity is ~15.6 ms, which would cap the camera
# loop at ~30 Hz and make the sweep visibly steppy. Ask for 1 ms.
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:  # pragma: no cover
    pass

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

WHEEL_DELTA = 120

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

# Scancodes (set 1) for the handful of keys the macro touches.
SCAN = {
    "space": 0x39,
    "w": 0x11,
    "a": 0x1E,
    "s": 0x1F,
    "d": 0x20,
    "e": 0x12,
    "r": 0x13,
    "shift": 0x2A,
    "esc": 0x01,
    "l": 0x26,
    "m": 0x32,
    "enter": 0x1C,
    "1": 0x02,
    "2": 0x03,
    "3": 0x04,
    "4": 0x05,
}

VK_F8, VK_F9 = 0x77, 0x78


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def _send(*records: INPUT) -> None:
    count = len(records)
    array = (INPUT * count)(*records)
    user32.SendInput(count, array, ctypes.sizeof(INPUT))


def _mouse(dx: int = 0, dy: int = 0, data: int = 0, flags: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0))


def _key(scan: int, flags: int) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(0, scan, flags | KEYEVENTF_SCANCODE, 0, 0))


# --- geometry -------------------------------------------------------------


def virtual_screen() -> tuple[int, int, int, int]:
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


# --- mouse ----------------------------------------------------------------


def move_to(x: int, y: int) -> None:
    """Absolute move, in virtual-desktop pixels."""
    vx, vy, vw, vh = virtual_screen()
    nx = int(round((x - vx) * 65535 / max(1, vw - 1)))
    ny = int(round((y - vy) * 65535 / max(1, vh - 1)))
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    _send(_mouse(nx, ny, flags=flags))


def move_relative(dx: int, dy: int) -> None:
    if dx or dy:
        _send(_mouse(int(dx), int(dy), flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_MOVE_NOCOALESCE))


def glide_to(x: int, y: int, duration: float = 0.12, steps: int = 12) -> None:
    """Absolute move broken into a short eased path, so it is not a teleport."""
    sx, sy = cursor_pos()
    steps = max(1, steps)
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)
        move_to(int(round(sx + (x - sx) * ease)), int(round(sy + (y - sy) * ease)))
        time.sleep(duration / steps)
    move_to(x, y)


def click(button: str = "left", hold: float | None = None) -> None:
    down, up = (
        (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
        if button == "left"
        else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
    )
    _send(_mouse(flags=down))
    time.sleep(hold if hold is not None else random.uniform(0.03, 0.07))
    _send(_mouse(flags=up))


def click_at(x: int, y: int, glide: bool = True) -> None:
    if glide:
        glide_to(x, y)
    else:
        move_to(x, y)
    time.sleep(random.uniform(0.04, 0.09))
    click()


def scroll(notches: int) -> None:
    """Positive scrolls up, negative scrolls down."""
    step = 1 if notches > 0 else -1
    for _ in range(abs(notches)):
        _send(_mouse(data=step * WHEEL_DELTA, flags=MOUSEEVENTF_WHEEL))
        time.sleep(random.uniform(0.02, 0.05))


# --- keyboard -------------------------------------------------------------


def key_down(name: str) -> None:
    _send(_key(SCAN[name], 0))


def key_up(name: str) -> None:
    _send(_key(SCAN[name], KEYEVENTF_KEYUP))


def key_tap(name: str, hold: float | None = None) -> None:
    key_down(name)
    time.sleep(hold if hold is not None else random.uniform(0.03, 0.06))
    key_up(name)


def is_pressed(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)
