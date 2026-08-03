"""RapidOCR wrapper.

The game uses a stylised serif display font over animated, semi-transparent
backgrounds, which classic OCR handles badly.  RapidOCR (PaddleOCR via ONNX)
copes far better and installs from pip with no system dependency.

Two rules keep the rest of the codebase honest about OCR being fallible:

  * OCR is never used to decide rarity - that is pure colour (see vision/cards).
  * Recognised words are fuzzy-matched against a known vocabulary rather than
    trusted verbatim, so "Attaek Speecl" still resolves to "Attack Speed".
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

import cv2
import numpy as np
from rapidfuzz import fuzz, process as fuzz_process

from . import config
from .vision.geometry import Box


@dataclass(frozen=True)
class TextItem:
    text: str
    box: Box
    confidence: float

    @property
    def norm(self) -> str:
        return normalize(self.text)


def normalize(text: str) -> str:
    """Casefold and squash whitespace/punctuation for robust comparison."""
    text = text.replace("’", "'").strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


@functools.lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def preprocess(img: np.ndarray, upscale: float = 2.0) -> np.ndarray:
    """Boost small, low-contrast game text before recognition.

    Upscaling matters most: the detector's receptive field is tuned for
    document-sized text, and HUD labels are often only 12-16px tall.
    """
    if img.size == 0:
        return img
    if upscale != 1.0:
        img = cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def isolate_light_text(img: np.ndarray, upscale: float = 1.5) -> np.ndarray:
    """Keep only near-white glyphs, as black-on-white.

    The game's panels are semi-transparent, so world geometry and coloured HUD
    text bleed through and OCR merges them into the panel's own labels - on the
    reference frame this ate the "Luck" row entirely.  Panel text is near-white
    (low saturation, high value) while the bleed-through is coloured, so a
    saturation/value gate removes the interference outright rather than trying
    to out-argue it downstream.
    """
    if img.size == 0:
        return img
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lo = (0, 0, config.LIGHT_TEXT_MIN_VALUE)
    hi = (179, config.LIGHT_TEXT_MAX_SATURATION, 255)
    mask = cv2.inRange(hsv, lo, hi)
    out = 255 - cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    if upscale != 1.0:
        out = cv2.resize(out, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    return out


def auto_upscale(frame: np.ndarray, target_width: int = 2100) -> float:
    """Pick an upscale factor that keeps the OCR input near a size the
    detector was trained for, regardless of the player's window size."""
    width = frame.shape[1]
    if width <= 0:
        return 1.0
    return max(1.0, min(2.5, target_width / width))


def read(
    frame: np.ndarray,
    box: Box | None = None,
    upscale: float = 2.0,
    min_confidence: float | None = None,
    light_text: bool = False,
) -> list[TextItem]:
    """Recognise text, returning boxes in the *frame's* coordinate space.

    `light_text` swaps the contrast-stretch preprocessor for the white-glyph
    isolator, which is what panels over live gameplay need.
    """
    origin_x, origin_y = 0, 0
    img = frame
    if box is not None:
        img = box.crop(frame)
        origin_x, origin_y = box.x, box.y

    if img.size == 0 or min(img.shape[:2]) < 4:
        return []

    prepared = isolate_light_text(img, upscale) if light_text else preprocess(img, upscale)

    # use_cls=False: the angle classifier flips short ambiguous tokens - it read
    # the Damage value "x7.08" as "807X" on the reference frame.  Game text is
    # never rotated, so orientation detection is pure downside here.
    result, _ = _engine()(prepared, use_det=True, use_cls=False, use_rec=True)
    if not result:
        return []

    threshold = config.OCR_MIN_CONFIDENCE if min_confidence is None else min_confidence
    items: list[TextItem] = []
    for entry in result:
        points, text, score = entry[0], entry[1], float(entry[2])
        if score < threshold or not text.strip():
            continue
        pts = np.asarray(points, dtype=np.float32) / upscale
        x1, y1 = pts[:, 0].min(), pts[:, 1].min()
        x2, y2 = pts[:, 0].max(), pts[:, 1].max()
        items.append(
            TextItem(
                text=text.strip(),
                box=Box(
                    int(origin_x + x1),
                    int(origin_y + y1),
                    max(1, int(x2 - x1)),
                    max(1, int(y2 - y1)),
                    text.strip(),
                ),
                confidence=score,
            )
        )
    return items


def find_text(
    items: list[TextItem], target: str, min_score: int | None = None
) -> TextItem | None:
    """Best fuzzy match for `target` among recognised items, or None.

    A containment check comes first because OCR often merges a label with
    neighbouring text ("UPGRADE OFFERS" swallowing a stray glyph).
    """
    if not items:
        return None
    want = normalize(target)
    threshold = config.FUZZY_MATCH_MIN_SCORE if min_score is None else min_score

    contained = [i for i in items if want in i.norm]
    if contained:
        return min(contained, key=lambda i: len(i.norm))

    # Whole-string similarity, never partial.  Partial scorers are actively
    # dangerous here: a stray "e" on the Stats panel scores 90 against
    # "you died" under WRatio, which would fake a death screen mid-run.
    candidates = [i for i in items if len(i.norm) >= 0.6 * len(want)]
    if not candidates:
        return None
    match = fuzz_process.extractOne(
        want, [i.norm for i in candidates], scorer=fuzz.ratio, score_cutoff=threshold
    )
    if match is None:
        return None
    return candidates[match[2]]


def match_vocab(text: str, vocab, min_score: int | None = None) -> str | None:
    """Snap a recognised string to the closest known term, or None.

    Whole-string similarity for the same reason as `find_text`: with a partial
    scorer the title "Luck" matches "Drops Luck" as well as it matches itself.
    """
    if not text:
        return None
    threshold = config.FUZZY_MATCH_MIN_SCORE if min_score is None else min_score
    want = normalize(text)
    match = fuzz_process.extractOne(
        want, [normalize(v) for v in vocab], scorer=fuzz.ratio, score_cutoff=threshold
    )
    if match is None:
        return None
    return list(vocab)[match[2]]


def group_lines(items: list[TextItem], tolerance_frac: float = 0.6) -> list[list[TextItem]]:
    """Cluster items into visual rows by vertical overlap.

    Used by the Stats panel parser to pair each left-hand label with the value
    on its right, which OCR reports as two unrelated boxes.
    """
    if not items:
        return []
    ordered = sorted(items, key=lambda i: i.box.cy)
    lines: list[list[TextItem]] = [[ordered[0]]]
    for item in ordered[1:]:
        row = lines[-1]
        ref = row[-1]
        tolerance = max(ref.box.h, item.box.h) * tolerance_frac
        if abs(item.box.cy - ref.box.cy) <= tolerance:
            row.append(item)
        else:
            lines.append([item])
    for row in lines:
        row.sort(key=lambda i: i.box.x)
    return lines
