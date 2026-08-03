"""Multi-scale template matching for icon-only UI elements.

Only used where there is no text to read - chiefly the gear/settings button.
Because the window can be any size, a single-scale match is useless; every
lookup sweeps a range of scales and keeps the best response.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import cv2
import numpy as np

from . import config
from .vision.geometry import Box


@dataclass(frozen=True)
class Match:
    box: Box
    score: float
    scale: float

    def as_dict(self) -> dict:
        return {"box": self.box.as_dict(), "score": round(self.score, 3), "scale": self.scale}


@functools.lru_cache(maxsize=8)
def _load_template(name: str) -> np.ndarray:
    path = config.SOURCES / name
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Template not found: {path}")
    return img


def _prepare(img: np.ndarray) -> np.ndarray:
    """Match on gradient magnitude, not raw pixels.

    The gear sits on a translucent button whose apparent colour changes with
    whatever is behind it; its edge structure does not.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    return cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)


def find(
    frame: np.ndarray,
    template_name: str,
    box: Box | None = None,
    threshold: float | None = None,
    scales=None,
) -> Match | None:
    """Best multi-scale match for a template, or None below threshold."""
    threshold = config.TEMPLATE_MATCH_THRESHOLD if threshold is None else threshold
    scales = config.TEMPLATE_SCALES if scales is None else scales

    origin_x, origin_y = (box.x, box.y) if box else (0, 0)
    haystack_img = box.crop(frame) if box else frame
    if haystack_img.size == 0:
        return None

    haystack = _prepare(haystack_img)
    template = _prepare(_load_template(template_name))
    th, tw = template.shape[:2]
    hh, hw = haystack.shape[:2]

    best: Match | None = None
    for scale in scales:
        sw, sh = int(tw * scale), int(th * scale)
        if sw < 8 or sh < 8 or sw > hw or sh > hh:
            continue
        resized = cv2.resize(template, (sw, sh), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(haystack, resized, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if best is None or max_val > best.score:
            best = Match(
                box=Box(origin_x + max_loc[0], origin_y + max_loc[1], sw, sh, template_name),
                score=float(max_val),
                scale=scale,
            )

    if best is None or best.score < threshold:
        return None
    return best


def find_gear(frame: np.ndarray, threshold: float | None = None) -> Match | None:
    """Locate the settings gear that opens the Stats panel.

    Searched only in the left portion of the screen, where the button lives -
    which both speeds up the sweep and removes false positives from the rest of
    the HUD.
    """
    h, w = frame.shape[:2]
    search = Box(0, 0, int(w * 0.35), h, "gear-search")
    return find(frame, "gear.png", box=search, threshold=threshold)
