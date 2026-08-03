"""Cached screen geometry, so a layout is solved by OCR once and reused.

Finding the three card boxes costs one full OCR pass (~800ms).  Their positions
do not move between level-ups, so paying that on every offers screen is pure
waste.  This caches the solved geometry per window size and hands it to the
fast path, which then validates it by colour in ~2ms.

If validation ever fails - a resized window, a different layout - the cache is
dropped and the next offers screen re-solves it with OCR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .vision.geometry import Box


@dataclass
class OffersLayout:
    size: tuple[int, int]
    cards: list[Box]
    unit: float
    # Where "UPGRADE OFFERS" actually was, found by OCR.  The fast path needs a
    # place to measure white text, and deriving one from the card row assumes
    # the gap scales with the cards - it does not, at other window sizes.
    title: Box | None = None

    def matches(self, frame: np.ndarray) -> bool:
        return self.size == (frame.shape[1], frame.shape[0])


@dataclass
class LayoutCache:
    """Remembered geometry for the current window size."""

    offers: OffersLayout | None = None
    gear: Box | None = None
    resume: Box | None = None
    _size: tuple[int, int] | None = field(default=None)

    def for_frame(self, frame: np.ndarray) -> "LayoutCache":
        """Invalidate everything if the window changed size."""
        size = (frame.shape[1], frame.shape[0])
        if self._size != size:
            self.offers = None
            self.gear = None
            self.resume = None
            self._size = size
        return self

    def remember_offers(
        self,
        frame: np.ndarray,
        cards: list[Box],
        unit: float,
        title: Box | None = None,
    ) -> None:
        self.offers = OffersLayout(
            size=(frame.shape[1], frame.shape[0]), cards=list(cards), unit=unit, title=title
        )

    @property
    def card_boxes(self) -> list[Box]:
        return self.offers.cards if self.offers else []

    @property
    def title_box(self) -> Box | None:
        return self.offers.title if self.offers else None

    def drop_offers(self) -> None:
        self.offers = None
