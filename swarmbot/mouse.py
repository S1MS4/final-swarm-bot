"""Real mouse input via SendInput, plus the safety interlocks around it.

Roblox ignores PostMessage-style synthetic clicks, so we drive the actual system
cursor.  That means the bot owns the machine while it runs, and every click has
to be gated on:

  * the abort hotkey not having been pressed,
  * the game window still being the foreground window.

`dry_run` turns every click into a log line, which is how the state machine gets
validated against a live game without touching it.
"""

from __future__ import annotations

import ctypes
import math
import random
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field

import keyboard

from . import config
from .window import GameWindow

# --------------------------------------------------------------------------
# SendInput plumbing
# --------------------------------------------------------------------------

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(*inputs: _INPUT) -> None:
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    sent = _user32.SendInput(n, arr, ctypes.sizeof(_INPUT))
    if sent != n:
        raise OSError(f"SendInput sent {sent}/{n}: {ctypes.get_last_error()}")


def _mouse_input(flags: int, dx: int = 0, dy: int = 0) -> _INPUT:
    return _INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(dx, dy, 0, flags, 0, None))


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    """Screen pixels -> the 0..65535 virtual-desktop space SendInput expects."""
    vx = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    nx = int(round((x - vx) * 65535 / max(vw - 1, 1)))
    ny = int(round((y - vy) * 65535 / max(vh - 1, 1)))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def _move_absolute(x: int, y: int) -> None:
    ax, ay = _to_absolute(x, y)
    _send(_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay))


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


# --------------------------------------------------------------------------
# Abort
# --------------------------------------------------------------------------


class Aborted(RuntimeError):
    """Raised when the user hits the abort hotkey."""


class AbortSwitch:
    """A latching global-hotkey kill switch.

    Latching matters: once tripped it stays tripped, so a keypress during a
    blocking OCR call is never missed.
    """

    def __init__(self, hotkeys=config.ABORT_HOTKEYS) -> None:
        self.hotkeys = (hotkeys,) if isinstance(hotkeys, str) else tuple(hotkeys)
        self._event = threading.Event()
        self._handles: list = []

    @property
    def hotkey(self) -> str:
        return " or ".join(self.hotkeys)

    def arm(self) -> "AbortSwitch":
        if self._handles:
            return self
        for combo in self.hotkeys:
            try:
                self._handles.append(keyboard.add_hotkey(combo, self._event.set))
            except Exception as exc:  # noqa: BLE001 - keyboard raises bare errors
                print(f"WARNING: could not register abort hotkey {combo!r} ({exc}).",
                      file=sys.stderr, flush=True)
        if not self._handles:
            # Running a clicking bot whose kill switch silently does nothing is
            # worse than not running it, so say so loudly.
            print(
                "WARNING: no abort hotkey could be registered.\n"
                "         Ctrl+C in this terminal is the only way to stop the bot.",
                file=sys.stderr,
                flush=True,
            )
        return self

    def disarm(self) -> None:
        for handle in self._handles:
            try:
                keyboard.remove_hotkey(handle)
            except (KeyError, ValueError):
                pass
        self._handles.clear()

    def trip(self) -> None:
        self._event.set()

    @property
    def tripped(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Aborted(f"Aborted via {self.hotkey}")

    def sleep(self, seconds: float, step: float = 0.05) -> None:
        """Sleep that stays responsive to the abort hotkey."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(step, max(0.0, deadline - time.monotonic())))

    def __enter__(self) -> "AbortSwitch":
        return self.arm()

    def __exit__(self, *exc) -> None:
        self.disarm()


class FocusLost(RuntimeError):
    """The game window stopped being foreground mid-operation."""


# --------------------------------------------------------------------------
# Mouse
# --------------------------------------------------------------------------


@dataclass
class Mouse:
    """Clicks in *window-relative* coordinates."""

    window: GameWindow
    abort: AbortSwitch
    dry_run: bool = False
    require_focus: bool = True
    clicks: list[tuple[int, int, str]] = field(default_factory=list)
    _rng: random.Random = field(default_factory=random.Random)

    def _guard(self) -> None:
        self.abort.check()
        if self.require_focus and not self.dry_run and not self.window.is_foreground:
            raise FocusLost("Game window is not in the foreground")

    def move_to(self, x: int, y: int) -> None:
        """Glide the cursor to a window-relative point, then confirm it landed.

        The path matters as much as the destination.  Roblox tracks which
        control is hovered from the stream of mouse-move events, so a cursor
        that teleports and immediately clicks can be pressing before anything
        knows it is there - which is what made gear clicks vanish.  So the move
        is interpolated over many small steps along a slightly bowed path, with
        ease-in-out so it accelerates and settles like a hand would.

        It is still fast: the whole glide is MOVE_DURATION (~35ms) regardless of
        distance, because the step count scales with the distance rather than
        the time.

        Verification matters too: SendInput's absolute coordinates are quantised
        to a 0..65535 grid across the whole virtual desktop, so on a
        multi-monitor layout the rounding can land a pixel or two off.
        """
        self._guard()
        sx, sy = self.window.client_rect.to_screen(x, y)
        if self.dry_run:
            return

        cx, cy = cursor_pos()
        dx, dy = sx - cx, sy - cy
        distance = math.hypot(dx, dy)

        if distance >= 1.0:
            steps = int(
                min(config.MOVE_MAX_STEPS,
                    max(config.MOVE_MIN_STEPS, distance / config.MOVE_PX_PER_STEP))
            )
            # A gentle perpendicular bow, so the path is an arc rather than a
            # ruler-straight line between two points.
            bow = self._rng.uniform(-1.0, 1.0) * min(distance * 0.06, config.MOVE_MAX_BOW_PX)
            ctrl_x = (cx + sx) / 2 - dy / distance * bow
            ctrl_y = (cy + sy) / 2 + dx / distance * bow

            delay = config.MOVE_DURATION / steps
            for i in range(1, steps + 1):
                t = i / steps
                e = t * t * (3 - 2 * t)  # smoothstep: ease in and out
                inv = 1 - e
                px = inv * inv * cx + 2 * inv * e * ctrl_x + e * e * sx
                py = inv * inv * cy + 2 * inv * e * ctrl_y + e * e * sy
                _move_absolute(int(round(px)), int(round(py)))
                time.sleep(delay)

        for _ in range(config.MOVE_CORRECTIONS):
            gx, gy = cursor_pos()
            if abs(gx - sx) <= 1 and abs(gy - sy) <= 1:
                return
            _move_absolute(sx, sy)
            time.sleep(0.003)

    def press_here(self, label: str = "") -> None:
        """Click wherever the cursor already is, with no movement at all.

        Used to cancel the deal animation.  Moving first would cost a ~47ms
        glide at the exact moment the click needs to land, and the cursor is
        deliberately parked on a card between offers screens so that no travel
        is needed either to cancel the animation or, often, to take the card.
        """
        self._guard()
        self.clicks.append((-1, -1, label))
        if self.dry_run:
            return
        _send(_mouse_input(MOUSEEVENTF_LEFTDOWN))
        time.sleep(config.FAST_CLICK_HOLD)
        _send(_mouse_input(MOUSEEVENTF_LEFTUP))

    def park(self, x: int, y: int) -> None:
        """Move the cursor somewhere useful without clicking."""
        self.move_to(x, y)

    def click_burst(self, x: int, y: int, label: str = "", times: int | None = None) -> None:
        """Glide once, then press several times in quick succession.

        The card must be taken inside a short window while it settles, and a
        single press that arrives a frame early is simply lost.  Repeating the
        press covers the whole window instead of betting on one instant.  Extra
        presses after the card is taken land on empty ground, which is
        harmless - the player never moves.
        """
        times = config.CARD_CLICK_BURST if times is None else times
        j = config.CLICK_JITTER_PX
        x += self._rng.randint(-j, j)
        y += self._rng.randint(-j, j)

        self.clicks.append((x, y, label))
        if self.dry_run:
            self.abort.check()
            return

        self.move_to(x, y)
        self._guard()
        time.sleep(config.MOVE_DWELL)
        for i in range(times):
            _send(_mouse_input(MOUSEEVENTF_LEFTDOWN))
            time.sleep(config.BURST_HOLD)
            _send(_mouse_input(MOUSEEVENTF_LEFTUP))
            if i < times - 1:
                time.sleep(config.BURST_GAP)

    def click(self, x: int, y: int, label: str = "", fast: bool = False) -> None:
        """Click a window-relative point.

        The default profile is deliberately unhurried around the button press.
        Roblox samples input per frame and discards a press whose cursor
        arrived in the same tick, which showed up as gear clicks that never
        opened the panel.  So: settle at the target, hold for several frames'
        worth of time, then let the release settle.

        `fast` trims that for clicks whose only job is to dismiss something.
        Nothing depends on them landing on a particular control, and if one is
        dropped the next poll simply sends another - so paying ~200ms of
        settling for each is wasted latency at the moment we most want speed.
        """
        j = config.CLICK_JITTER_PX
        x += self._rng.randint(-j, j)
        y += self._rng.randint(-j, j)

        self.clicks.append((x, y, label))
        if self.dry_run:
            self.abort.check()
            return

        self.move_to(x, y)
        self._guard()
        if fast:
            _send(_mouse_input(MOUSEEVENTF_LEFTDOWN))
            time.sleep(config.FAST_CLICK_HOLD)
            _send(_mouse_input(MOUSEEVENTF_LEFTUP))
            return

        time.sleep(config.MOVE_DWELL)  # let the game register the hover
        _send(_mouse_input(MOUSEEVENTF_LEFTDOWN))
        time.sleep(self._rng.uniform(*config.CLICK_HOLD_RANGE))
        _send(_mouse_input(MOUSEEVENTF_LEFTUP))
        time.sleep(self._rng.uniform(*config.CLICK_DELAY_RANGE))

    def click_box(self, box, label: str = "") -> None:
        """Click the centre of a Box (see vision.geometry)."""
        self.click(int(box.cx), int(box.cy), label or getattr(box, "label", ""))
