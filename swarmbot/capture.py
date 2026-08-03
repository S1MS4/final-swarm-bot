"""Screen capture of the game window.

Produces plain BGR uint8 ndarrays so every detector can treat a live frame and a
PNG loaded from sources/ identically.
"""

from __future__ import annotations

import cv2
import mss
import numpy as np

from .window import GameWindow, Rect


class Occluded(RuntimeError):
    """Something is covering the game window."""


class Capturer:
    """Grabs the client area of a window.

    mss keeps a per-thread handle, so one Capturer belongs to one thread.
    """

    def __init__(self, window: GameWindow) -> None:
        self.window = window
        self._sct = mss.mss()

    def grab(self, strict: bool = False) -> np.ndarray:
        """Capture the window client area as BGR.

        This reads a *screen region*, not the window's own back buffer, so
        anything stacked on top of the game is captured instead of the game.
        Without `strict` that failure is silent and the bot cheerfully OCRs
        someone's browser; with it, the caller finds out.
        """
        if strict and not self.window.is_foreground:
            raise Occluded(
                f"{self.window.title!r} is not the foreground window; "
                "the capture would show whatever is covering it"
            )
        return self.grab_rect(self.window.client_rect)

    def grab_rect(self, rect: Rect) -> np.ndarray:
        raw = self._sct.grab(
            {"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height}
        )
        frame = np.asarray(raw)  # BGRA
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    def close(self) -> None:
        self._sct.close()

    def __enter__(self) -> "Capturer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def load_image(path) -> np.ndarray:
    """Load a PNG as BGR, raising rather than returning None like cv2 does."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return img


def save_image(path, frame: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), frame):
        raise OSError(f"Could not write image: {path}")
