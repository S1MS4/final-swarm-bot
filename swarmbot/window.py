"""Locating the Roblox window and converting between window and screen space.

Everything downstream works in *window-relative* coordinates so the bot is
independent of window size and position.  This module is the only place that
knows about screen coordinates.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

import win32api
import win32con
import win32gui
import win32process

from . import config


def enable_dpi_awareness() -> None:
    """Make this process per-monitor DPI aware.

    Without this, Windows lies to us about window rects on scaled displays and
    every captured frame is offset from where we actually click.
    """
    try:
        # PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        return self.left + int(x), self.top + int(y)


class WindowNotFound(RuntimeError):
    pass


@dataclass
class GameWindow:
    hwnd: int
    title: str

    @property
    def client_rect(self) -> Rect:
        """Client area in screen coordinates (excludes title bar and borders)."""
        left, top = win32gui.ClientToScreen(self.hwnd, (0, 0))
        _, _, w, h = win32gui.GetClientRect(self.hwnd)
        return Rect(left, top, w, h)

    @property
    def is_foreground(self) -> bool:
        return win32gui.GetForegroundWindow() == self.hwnd

    @property
    def is_alive(self) -> bool:
        return bool(win32gui.IsWindow(self.hwnd))

    def focus(self) -> bool:
        """Bring the game to the front.  Returns whether it worked.

        Windows refuses `SetForegroundWindow` from a process that does not own
        the current foreground window - it silently does nothing, which is why
        starting the bot from an editor or terminal used to fail outright.

        The documented way round it is to attach to the foreground window's
        input queue first, which makes the two threads share foreground state
        and lifts the restriction.  A synthetic ALT tap is added because the
        lock is also released for the thread that last handled input.
        """
        if self.is_foreground:
            return True
        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)

        foreground = win32gui.GetForegroundWindow()
        target_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        this_thread = win32api.GetCurrentThreadId()
        attached = False
        try:
            if target_thread and target_thread != this_thread:
                attached = bool(win32process.AttachThreadInput(target_thread, this_thread, True))
            # Tapping ALT satisfies the foreground lock in the common cases.
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.BringWindowToTop(self.hwnd)
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:  # noqa: BLE001 - pywin32 raises bare pywintypes.error
            pass
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(target_thread, this_thread, False)
                except Exception:  # noqa: BLE001
                    pass
        return self.is_foreground


def _matches(title: str) -> bool:
    if not title:
        return False
    if any(bad.lower() in title.lower() for bad in config.WINDOW_TITLE_EXCLUDE):
        return False
    return any(want.lower() in title.lower() for want in config.WINDOW_TITLE_CANDIDATES)


def find_game_window(title_override: str | None = None) -> GameWindow:
    """Return the Roblox client window, or raise WindowNotFound."""
    found: list[tuple[int, str]] = []

    def _cb(hwnd: int, _) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title_override is not None:
            if title_override.lower() in title.lower():
                found.append((hwnd, title))
        elif _matches(title):
            found.append((hwnd, title))

    win32gui.EnumWindows(_cb, None)

    if not found:
        want = title_override or "/".join(config.WINDOW_TITLE_CANDIDATES)
        raise WindowNotFound(f"No visible window matching {want!r}. Is the game running?")

    # Prefer the largest matching window - the game client, not a tooltip.
    def area(item: tuple[int, str]) -> int:
        _, _, w, h = win32gui.GetClientRect(item[0])
        return w * h

    hwnd, title = max(found, key=area)
    return GameWindow(hwnd=hwnd, title=title)


def list_windows() -> list[tuple[int, str, tuple[int, int]]]:
    """All visible titled windows - a debugging aid for window discovery."""
    out: list[tuple[int, str, tuple[int, int]]] = []

    def _cb(hwnd: int, _) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        _, _, w, h = win32gui.GetClientRect(hwnd)
        out.append((hwnd, title, (w, h)))

    win32gui.EnumWindows(_cb, None)
    return sorted(out, key=lambda i: -i[2][0] * i[2][1])
