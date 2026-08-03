"""Auto Skip must be readable on any arena.

Captured from a live failure: across three full blue-arena runs the bot logged
**zero** auto-skip events of any kind, while the same code on the green arena
logged 38 in one session.  Auto Skip sat off for every run.

The button was found every time - OCR read "Auto Skip" at (18,828) on every
frame probed - and its green coverage was read correctly as 0.000, the toggle
genuinely being off.  What failed was `auto_skip_readable`, the guard that asks
whether the border is visible at all.  It measured median saturation in the ring
and called anything above 90 "covered by the high-wave overlay"; but the ring is
mostly the *translucent world behind* the button, so it measures the arena.
Measured in the ring: 26-40 on the green arena, 109 on the blue one with nothing
covering the button at all.  `ensure_auto_skip` treats unreadable as "no
evidence" and returns without logging, which is why the failure was silent.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from swarmbot import config
from swarmbot.capture import load_image
from swarmbot.vision import hud
from swarmbot.vision.geometry import Box


@pytest.fixture(scope="module")
def blue():
    """A blue-arena wave frame.  Verified by eye: pale grey border, no green,
    i.e. the toggle is off and the button is completely unobstructed."""
    return load_image(config.SOURCES / "auto-skip-blue-arena.png")


@pytest.fixture(scope="module")
def blue_button(blue):
    box = hud.find_auto_skip(blue)
    assert box is not None, "the button was always found; that was never the bug"
    return box


def _ring_saturation(frame, box: Box) -> float:
    pad_x, pad_y = int(box.w * 0.35), int(box.h * 0.90)
    outer = Box(box.x - pad_x, box.y - pad_y, box.w + 2 * pad_x, box.h + 2 * pad_y)
    return float(np.median(cv2.cvtColor(outer.crop(frame), cv2.COLOR_BGR2HSV)[:, :, 1]))


def _paint_ring(frame, box: Box, hsv: tuple[int, int, int]):
    """Fill the whole ring with one HSV colour, clamped to the frame."""
    painted = frame.copy()
    pad_x, pad_y = int(box.w * 0.35), int(box.h * 0.90)
    y1, y2 = max(0, box.y - pad_y), min(frame.shape[0], box.y + box.h + pad_y)
    x1, x2 = max(0, box.x - pad_x), min(frame.shape[1], box.x + box.w + pad_x)
    bgr = cv2.cvtColor(np.uint8([[list(hsv)]]), cv2.COLOR_HSV2BGR)[0][0]
    painted[y1:y2, x1:x2] = bgr
    return painted


def test_the_ring_is_saturated_by_the_arena_not_by_an_overlay(blue, blue_button):
    """The measurement the old guard made, and why it was the wrong one: this
    frame has nothing covering the button, yet it reads well past the old 90."""
    assert _ring_saturation(blue, blue_button) > 90


def test_the_blue_arena_button_is_readable(blue, blue_button):
    assert hud.auto_skip_readable(blue, blue_button)


def test_the_blue_arena_toggle_reads_as_off(blue, blue_button):
    """The reading was always right - the bot just refused to act on it."""
    assert hud.auto_skip_green(blue, blue_button) < config.AUTO_SKIP_ON_GREEN
    assert not hud.auto_skip_is_on(blue, blue_button)


def test_chrome_is_visible_whatever_the_arena(blue, blue_button):
    """The button's own border and lettering are drawn on top of the world, so
    unlike the ring's saturation they do not move with the arena.  Measured
    0.092 here against 0.105 on the green arena."""
    assert hud.auto_skip_chrome(blue, blue_button) >= config.AUTO_SKIP_MIN_CHROME


def test_a_covered_button_is_still_treated_as_unreadable(blue, blue_button):
    """What the guard is for.  A saturated wash over the button hides both
    borders, and the bot must wait rather than read that as "off" and click a
    working toggle off."""
    # A dense magenta wash: neither green border nor pale chrome survives it.
    covered = _paint_ring(blue, blue_button, (150, 200, 140))

    assert not hud.auto_skip_readable(covered, blue_button)
    assert hud.auto_skip_chrome(covered, blue_button) < config.AUTO_SKIP_MIN_CHROME


def test_green_alone_proves_the_button_is_visible(blue, blue_button):
    """An enabled toggle has a green border and little pale chrome, so either
    signal has to be sufficient on its own."""
    on = _paint_ring(blue, blue_button, (55, 98, 158))   # the measured border green

    assert hud.auto_skip_green(on, blue_button) >= config.AUTO_SKIP_ON_GREEN
    assert hud.auto_skip_chrome(on, blue_button) < config.AUTO_SKIP_MIN_CHROME
    assert hud.auto_skip_readable(on, blue_button)
    assert hud.auto_skip_is_on(on, blue_button)
