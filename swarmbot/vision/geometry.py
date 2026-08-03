"""Axis-aligned boxes in window-relative pixel space."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int
    label: str = ""

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> int:
        return self.w * self.h

    def crop(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        x1 = max(0, min(self.x, w))
        y1 = max(0, min(self.y, h))
        x2 = max(x1, min(self.x2, w))
        y2 = max(y1, min(self.y2, h))
        return frame[y1:y2, x1:x2]

    def offset(self, dx: int, dy: int) -> "Box":
        return Box(self.x + dx, self.y + dy, self.w, self.h, self.label)

    def scaled(self, fx: float, fy: float | None = None) -> "Box":
        fy = fx if fy is None else fy
        return Box(int(self.x * fx), int(self.y * fy), int(self.w * fx), int(self.h * fy), self.label)

    def inset(self, frac_x: float, frac_y: float | None = None) -> "Box":
        """Shrink towards the centre by a fraction of each dimension."""
        frac_y = frac_x if frac_y is None else frac_y
        dx = int(self.w * frac_x)
        dy = int(self.h * frac_y)
        return Box(self.x + dx, self.y + dy, max(1, self.w - 2 * dx), max(1, self.h - 2 * dy), self.label)

    def sub(self, fx: float, fy: float, fw: float, fh: float, label: str = "") -> "Box":
        """A child box addressed in fractions of this box."""
        return Box(
            int(self.x + self.w * fx),
            int(self.y + self.h * fy),
            max(1, int(self.w * fw)),
            max(1, int(self.h * fh)),
            label or self.label,
        )

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x2 and self.y <= y < self.y2

    def iou(self, other: "Box") -> float:
        ix = max(0, min(self.x2, other.x2) - max(self.x, other.x))
        iy = max(0, min(self.y2, other.y2) - max(self.y, other.y))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union else 0.0

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "label": self.label}


def whole(frame: np.ndarray, label: str = "frame") -> Box:
    h, w = frame.shape[:2]
    return Box(0, 0, w, h, label)


def band(frame: np.ndarray, top_frac: float, bottom_frac: float, label: str = "") -> Box:
    """A full-width horizontal slice, addressed in fractions of frame height."""
    h, w = frame.shape[:2]
    y1 = int(h * top_frac)
    y2 = int(h * bottom_frac)
    return Box(0, y1, w, max(1, y2 - y1), label)


def region(
    frame: np.ndarray,
    x_frac: float,
    y_frac: float,
    w_frac: float,
    h_frac: float,
    label: str = "",
) -> Box:
    h, w = frame.shape[:2]
    return Box(int(w * x_frac), int(h * y_frac), max(1, int(w * w_frac)), max(1, int(h * h_frac)), label)
