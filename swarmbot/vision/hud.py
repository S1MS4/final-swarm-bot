"""Text-driven detectors for everything that is not a card.

The wave counter, the pause panel, and the whole end-of-run sequence
(Auto Skip -> YOU DIED -> GIVE UP -> VICTORY -> AGAIN) are all plain on-screen
text, so they are all read the same way: OCR a band, fuzzy-match a phrase, and
return the box to click.

Each detector takes an optional pre-read `items` list so a single OCR pass can
answer several questions - the state classifier reads the frame once and asks
every detector about it.
"""

from __future__ import annotations

import re

import cv2
import numpy as np

from .. import config, ocr
from .geometry import Box, band, region

WAVE_RE = re.compile(r"wave\s*[:\-]?\s*(\d{1,3})", re.IGNORECASE)


def read_all(frame: np.ndarray, light_text: bool = False) -> list[ocr.TextItem]:
    """One OCR pass over the whole frame, shared by every detector below."""
    return ocr.read(frame, upscale=ocr.auto_upscale(frame), light_text=light_text)


def find_phrase(
    frame: np.ndarray,
    phrase: str,
    items: list[ocr.TextItem] | None = None,
    zone: Box | None = None,
    min_score: int = 80,
) -> Box | None:
    """Locate a phrase and return its clickable box."""
    if items is None:
        items = ocr.read(frame, zone, upscale=ocr.auto_upscale(frame))
    if zone is not None:
        items = [i for i in items if zone.contains(i.box.cx, i.box.cy)]
    hit = ocr.find_text(items, phrase, min_score=min_score)
    return hit.box if hit else None


def read_wave(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> int | None:
    """Current wave number from the "Wave {x}" counter at the top centre."""
    zone = region(frame, 0.25, 0.0, 0.50, 0.18, "wave")
    if items is None:
        items = ocr.read(frame, zone, upscale=ocr.auto_upscale(frame))
    candidates = [i for i in items if zone.contains(i.box.cx, i.box.cy)]

    for item in sorted(candidates, key=lambda i: i.box.y):
        match = WAVE_RE.search(item.text)
        if match:
            return int(match.group(1))

    # OCR sometimes splits "Wave" and "16" into neighbouring boxes.
    for line in ocr.group_lines(candidates):
        joined = " ".join(i.text for i in line)
        match = WAVE_RE.search(joined)
        if match:
            return int(match.group(1))
    return None


# --------------------------------------------------------------------------
# Buttons and screens.  Each returns a Box to click, or None.
# --------------------------------------------------------------------------


def find_resume(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> Box | None:
    return find_phrase(frame, config.TEXT_RESUME, items)


def find_auto_skip(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> Box | None:
    """"Auto Skip" appears at the bottom left after the final wave."""
    return find_phrase(frame, config.TEXT_AUTO_SKIP, items, zone=region(frame, 0.0, 0.55, 0.45, 0.45))


def _ring(frame: np.ndarray, box: Box) -> np.ndarray:
    """The band around the Auto Skip label, where its border is drawn."""
    pad_x = int(box.w * 0.35)
    pad_y = int(box.h * 0.90)
    outer = Box(box.x - pad_x, box.y - pad_y, box.w + 2 * pad_x, box.h + 2 * pad_y)
    return outer.crop(frame)


def auto_skip_green(frame: np.ndarray, box: Box) -> float:
    """Green coverage in the ring around the Auto Skip button.

    Auto Skip is a *toggle*: it is drawn with a green border when enabled.
    Clicking it blindly is therefore as likely to switch it off as on, so the
    border is measured and the click only sent when it is actually needed.
    """
    crop = _ring(frame, box)
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, config.AUTO_SKIP_GREEN_LOW, config.AUTO_SKIP_GREEN_HIGH)
    return float(green.mean()) / 255.0


def auto_skip_is_on(frame: np.ndarray, box: Box) -> bool:
    return auto_skip_green(frame, box) >= config.AUTO_SKIP_ON_GREEN


def auto_skip_chrome(frame: np.ndarray, box: Box) -> float:
    """Coverage of the button's own pale border and lettering in the ring.

    This is the button *itself*, not the world behind it: near-white, barely
    saturated, and drawn on top of whatever the arena happens to be.  Measured
    0.092 on the blue arena and 0.105 on the green one with the toggle off, and
    0.059 in the hub with it on.
    """
    crop = _ring(frame, box)
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    chrome = cv2.inRange(hsv, config.AUTO_SKIP_CHROME_LOW, config.AUTO_SKIP_CHROME_HIGH)
    return float(chrome.mean()) / 255.0


def auto_skip_readable(frame: np.ndarray, box: Box) -> bool:
    """Whether the border colour means anything on this frame.

    At high waves the screen is washed with a saturated overlay that covers the
    button for minutes at a time.  The green border disappears under it, so an
    *enabled* toggle measures 0.000 - indistinguishable from a disabled one.
    Treating that as "off" is what made the bot click a working toggle off.

    This asks for **positive evidence that the button is visible**, rather than
    for the absence of tint.  The first attempt used absolute saturation in the
    ring - measured 24 toggle-on, 40 toggle-off, 160 under the wave-121 overlay
    - which reads the *world behind* a translucent button as much as the button,
    and so is a property of the arena.  On the blue arena an unobstructed button
    measures 109, over the 90 cutoff, and the bot spent every run believing the
    toggle was permanently unreadable: it never checked it, never clicked it and
    never logged a thing, while Auto Skip sat off.

    Either kind of border is proof enough that nothing is covering it - green
    for enabled, pale chrome for disabled - and both are drawn on top of the
    arena rather than tinted by it.
    """
    return (
        auto_skip_green(frame, box) >= config.AUTO_SKIP_ON_GREEN
        or auto_skip_chrome(frame, box) >= config.AUTO_SKIP_MIN_CHROME
    )


def find_enter_portal(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> Box | None:
    """The hub's "Enter Portal" prompt, which starts the next run."""
    return find_phrase(frame, config.TEXT_ENTER_PORTAL, items, min_score=82)


def find_reroll(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> Box | None:
    """The "REROLL (x3)" button under the offers row."""
    return find_phrase(frame, config.TEXT_REROLL, items,
                       zone=region(frame, 0.25, 0.70, 0.50, 0.30), min_score=75)


def find_give_up(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> Box | None:
    return find_phrase(frame, config.TEXT_GIVE_UP, items)


def find_again(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> Box | None:
    return find_phrase(frame, config.TEXT_AGAIN, items, min_score=88)


def has_you_died(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> bool:
    return find_phrase(frame, config.TEXT_YOU_DIED, items, min_score=82) is not None


def has_victory(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> bool:
    return find_phrase(frame, config.TEXT_VICTORY, items, min_score=85) is not None


def has_stats_panel(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> bool:
    """The Stats header confirms the panel is open before we parse it.

    Requires the "Loot" section header too: "Stats" alone is a short word that
    OCR finds in plenty of unrelated HUD text, whereas the pair only co-occurs
    on the real panel.
    """
    if items is None:
        items = ocr.read(frame, band(frame, 0.0, 1.0), upscale=ocr.auto_upscale(frame), light_text=True)
    has_stats = ocr.find_text(items, config.TEXT_STATS_HEADER, min_score=90) is not None
    has_loot = ocr.find_text(items, "Loot", min_score=90) is not None
    return has_stats and has_loot
