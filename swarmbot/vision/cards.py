"""Reading the "UPGRADE OFFERS" screen.

Layout, established from the reference frames in sources/:

    UPGRADE OFFERS                      <- title, centred
    [chip] NEW!   [chip] NEW!   [chip]  <- rarity chip sits on the card's top edge
     Title         Title         Title
     (icon)        (icon)        (icon)
     description   description   description
     Footer stat ^^^                    <- stat cards only; weapons have none

Cards are anchored on the **rarity chips**, not on colour blobs.  The chips OCR
at ~1.0 confidence in every reference frame, they sit exactly on each card's
horizontal centre, and their spacing gives the layout scale - which makes the
whole detector resolution independent for free.  Colour segmentation was tried
first and fails: the offers overlay tints the entire scene with the rarity hue,
so a hue mask floods into the background.

Rarity is still decided by **card body colour**, with the chip label as a
cross-check, because colour cannot be confused by a stylised font.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import config, ocr
from . import fastpath
from .geometry import Box, band, region

# Card box relative to chip spacing (`unit`).  Measured against all five
# reference frames; see runs/ overlays produced by tools/inspect.py.
CARD_W_PER_UNIT = 0.90
CARD_H_PER_UNIT = 1.38
CARD_TOP_PER_UNIT = -0.02  # card top edge relative to chip centre y

CHIP_VOCAB = ("Weapon", "Common", "Rare", "Epic", "Legendary")


@dataclass
class Card:
    index: int
    box: Box
    rarity: str
    rarity_by_colour: str | None
    rarity_by_chip: str | None
    title: str | None
    raw_title: str | None
    is_new: bool
    footer_stat: str | None
    arrows: int
    is_weapon: bool

    @property
    def click_point(self) -> tuple[int, int]:
        return int(self.box.cx), int(self.box.cy)

    @property
    def key(self) -> str:
        return f"{self.name}|{self.rarity}"

    @property
    def name(self) -> str:
        """Best available identity.  Falls back to the slot for provisional
        cards, which are built from colour alone and have no title yet."""
        return self.title or self.raw_title or f"card{self.index}"

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "title": self.title,
            "raw_title": self.raw_title,
            "rarity": self.rarity,
            "rarity_by_colour": self.rarity_by_colour,
            "rarity_by_chip": self.rarity_by_chip,
            "is_new": self.is_new,
            "is_weapon": self.is_weapon,
            "footer_stat": self.footer_stat,
            "arrows": self.arrows,
            "box": self.box.as_dict(),
        }


@dataclass
class Offers:
    title_box: Box
    cards: list[Card]
    unit: float
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "unit": round(self.unit, 1),
            "warnings": self.warnings,
            "cards": [c.as_dict() for c in self.cards],
        }


# --------------------------------------------------------------------------
# Rarity by colour
# --------------------------------------------------------------------------


def classify_rarity_by_colour(frame: np.ndarray, box: Box) -> str | None:
    """Median-HSV classification of the card body.

    Measured medians: weapon H39 S15, common H61 S136, rare H106 S148,
    epic H147 S106, legendary H16 S148.  Saturation separates the grey weapon
    tier; hue separates the rest.
    """
    crop = box.inset(0.12, 0.10).crop(frame)
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    hue, sat, _ = np.median(hsv, axis=0)

    if sat < config.WEAPON_MAX_SATURATION:
        return "weapon"
    for rarity, (lo, hi) in config.RARITY_HUE_RANGES.items():
        if lo <= hue <= hi:
            return rarity
    return None


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def find_title(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> Box | None:
    """Locate the "UPGRADE OFFERS" heading, which gates the whole screen."""
    if items is None:
        items = ocr.read(frame, band(frame, 0.0, 0.45), upscale=ocr.auto_upscale(frame))
    hit = ocr.find_text(items, config.TEXT_UPGRADE_OFFERS, min_score=80)
    return hit.box if hit else None


def _find_chips(items: list[ocr.TextItem]) -> list[tuple[ocr.TextItem, str]]:
    """Locate the three rarity chips, which anchor the whole layout.

    Card *descriptions* contain rarity words too - "Increases weapon attack
    speed", "A ranged weapon that shoots arrows" - so a plain keyword match
    yields phantom chips.  The three real chips are the only ones that form a
    horizontal row, so candidates are clustered by y and the topmost row of
    exactly three wins.
    """
    candidates: list[tuple[ocr.TextItem, str]] = []
    for item in items:
        # Chip text is a single short word; a tight match keeps card titles
        # from masquerading as chips.
        if len(item.text) > 12:
            continue
        label = ocr.match_vocab(item.text, CHIP_VOCAB, min_score=85)
        if label is not None:
            candidates.append((item, config.RARITY_LABELS[label.casefold()]))

    if not candidates:
        return []

    # Cluster into rows and take the topmost.  Card *descriptions* contain
    # rarity words too ("Increases weapon attack speed"), but they sit well
    # below the chips, so the topmost row is the real one.  This also handles
    # screens that deal only one or two cards, which happens late in a run.
    rows: list[list[tuple[ocr.TextItem, str]]] = []
    for entry in sorted(candidates, key=lambda c: c[0].box.cy):
        placed = False
        for row in rows:
            tolerance = max(row[0][0].box.h, entry[0].box.h) * 2.0
            if abs(entry[0].box.cy - row[0][0].box.cy) <= tolerance:
                row.append(entry)
                placed = True
                break
        if not placed:
            rows.append([entry])

    row = min(rows, key=lambda r: min(e[0].box.cy for e in r))
    if not (config.MIN_CARDS <= len(row) <= config.MAX_CARDS):
        return []
    row.sort(key=lambda c: c[0].box.x)
    return row


def _card_box(chip: Box, unit: float, index: int) -> Box:
    w = int(CARD_W_PER_UNIT * unit)
    h = int(CARD_H_PER_UNIT * unit)
    x = int(chip.cx - w / 2)
    y = int(chip.cy + CARD_TOP_PER_UNIT * unit)
    return Box(x, y, w, h, f"card{index}")


def _count_arrows(frame: np.ndarray, footer: Box) -> int:
    """Count the green magnitude arrows in a card footer (0-4)."""
    crop = footer.crop(frame)
    if crop.size == 0:
        return 0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, config.ARROW_HSV_LOW, config.ARROW_HSV_HIGH)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, stats_, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    min_area = max(12, int(footer.area * config.ARROW_MIN_AREA_FRAC))
    count = sum(1 for i in range(1, n) if stats_[i, cv2.CC_STAT_AREA] >= min_area)
    return min(count, 4)


def detect(frame: np.ndarray, items: list[ocr.TextItem] | None = None) -> Offers | None:
    """Parse the offers screen, or return None if it is not showing.

    Returns None when the heading is absent or no chips can be located - the
    caller then treats the frame as "not ready yet" and polls again, rather
    than clicking into a half-dealt animation.

    One to three cards are accepted.  The screen usually deals three, but late
    in a run it deals two or even one, and requiring three made the bot ignore
    those screens completely and stall until a human intervened.

    `items` lets the state classifier hand over a frame it has already read;
    OCR dominates the cost of a poll, so re-reading here would double it.
    """
    # One pass over the offers band only, never two overlapping ones:
    # overlapping reads return the same text twice and the duplicate
    # ("Luck Luck") breaks vocabulary matching.  Restricting to the band and
    # dropping the upscale takes this from ~1.03s to ~0.57s, and the card text
    # is already large enough to read at native size.
    if items is None:
        band_box = region(frame, *config.OFFERS_BAND, "offers-band")
        all_items = ocr.read(frame, band_box, upscale=1.0)
    else:
        all_items = items
    title_box = find_title(frame, all_items)
    if title_box is None:
        return None

    chips = [c for c in _find_chips(all_items) if c[0].box.y > title_box.y]
    if not (config.MIN_CARDS <= len(chips) <= config.MAX_CARDS):
        return None

    xs = [c[0].box.cx for c in chips]
    if len(xs) >= 2:
        unit = (xs[-1] - xs[0]) / (len(xs) - 1)
    else:
        # A single card gives no spacing to measure.  Card size does not change
        # with the number dealt, so fall back to the frame-relative scale.
        unit = frame.shape[1] * config.UNIT_PER_FRAME_WIDTH
    if unit <= 0:
        return None

    # A settled row is evenly spaced and level; mid-deal it is neither, and a
    # layout solved from an animating frame is wrong for the rest of the
    # session - it produced card boxes offset by up to 17px whose titles then
    # never read.  Both checks need at least two chips to mean anything.
    if len(xs) >= 3:
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        if max(gaps) - min(gaps) > config.CHIP_SPACING_TOLERANCE * unit:
            return None
    if len(chips) >= 2:
        ys = [c[0].box.cy for c in chips]
        if max(ys) - min(ys) > config.CHIP_ROW_TOLERANCE * unit:
            return None

    warnings: list[str] = []
    cards: list[Card] = []
    for index, (chip_item, chip_rarity) in enumerate(chips):
        box = _card_box(chip_item.box, unit, index)
        by_colour = classify_rarity_by_colour(frame, box)

        rarity = by_colour or chip_rarity
        if by_colour and by_colour != chip_rarity:
            warnings.append(
                f"card{index}: colour says {by_colour!r}, chip says {chip_rarity!r}; using colour"
            )

        title_raw, title = _read_title(all_items, box, chip_item.box, unit)
        footer_stat, footer_box = _read_footer(all_items, box)
        arrows = _count_arrows(frame, footer_box) if footer_box else 0
        # Colour, not OCR: the badge overhangs the card's right edge and fell
        # outside the OCR band on narrow frames, and this is the same check the
        # fast path uses, so the two can never disagree.
        is_new = fastpath.has_new_badge(frame, box)

        cards.append(
            Card(
                index=index,
                box=box,
                rarity=rarity,
                rarity_by_colour=by_colour,
                rarity_by_chip=chip_rarity,
                title=title,
                raw_title=title_raw,
                is_new=is_new,
                footer_stat=footer_stat,
                arrows=arrows,
                is_weapon=rarity == "weapon",
            )
        )

    return Offers(title_box=title_box, cards=cards, unit=unit, warnings=warnings)


def _read_title(
    items: list[ocr.TextItem], card: Box, chip: Box, unit: float
) -> tuple[str | None, str | None]:
    """The card title is the first text line below the chip."""
    zone = Box(card.x, int(chip.y2), card.w, int(0.24 * unit))
    inside = [
        i
        for i in items
        if zone.contains(i.box.cx, i.box.cy)
        and ocr.match_vocab(i.text, CHIP_VOCAB, 90) is None
        # Titles are set large.  The world behind the translucent card - the
        # player nametag, floating damage numbers - is drawn much smaller, and
        # without this those were read as upgrade names.
        and i.box.h >= config.CARD_TITLE_MIN_HEIGHT * unit
    ]
    if not inside:
        return None, None
    inside.sort(key=lambda i: i.box.y)
    top_y = inside[0].box.cy
    same_line = [i for i in inside if abs(i.box.cy - top_y) <= inside[0].box.h * 0.6]
    same_line.sort(key=lambda i: i.box.x)
    raw = " ".join(i.text for i in same_line).strip()
    # Floating combat text drifts over the cards - a "+7518" damage number was
    # once read as a card title - so anything without real letters in it is not
    # a name.
    if sum(ch.isalpha() for ch in raw) < 2:
        return raw, None
    # The vocabulary corrects OCR slips; it does not gate what may exist.  The
    # game has upgrades we have never seen, and dropping their names to None
    # would silently keep them out of upgrades.json forever.
    return raw, (ocr.match_vocab(raw, config.CARD_TITLE_VOCAB) or raw)


def _read_footer(items: list[ocr.TextItem], card: Box) -> tuple[str | None, Box | None]:
    """The small stat name in the card's lower area, absent on weapon cards.

    The footer wraps onto two lines when it is long ("Movement" / "Speed"), and
    the fragment "Speed" fuzzy-matches "Attack Speed" just as well as it matches
    the truth - so fragments are never matched individually.  Instead the
    left-aligned lines are grouped and the *joined* string is matched once.

    Footers are separated from the description by **alignment, not size**: the
    two are set at the same height (measured: both 26px on uncommon.png), but
    the description is centred in the card while the footer hugs the left
    padding.  So a line qualifies only if it starts at the left padding *and*
    its centre sits well left of the card's centre.
    """
    zone = Box(card.x, int(card.y + card.h * 0.55), card.w, int(card.h * 0.42), "footer")
    inside = [i for i in items if zone.contains(i.box.cx, i.box.cy)]
    if not inside:
        return None, zone

    starts_left = card.x + card.w * 0.20
    min_centre_offset = card.w * 0.10
    left_aligned = [
        i
        for i in inside
        if i.box.x <= starts_left and (card.cx - i.box.cx) >= min_centre_offset
    ]
    if not left_aligned:
        return None, zone

    anchor = max(left_aligned, key=lambda i: i.box.y2)
    group = [i for i in left_aligned if anchor.box.y2 - i.box.y <= 3.0 * anchor.box.h]
    if not group:
        return None, zone

    group.sort(key=lambda i: (i.box.y, i.box.x))
    joined = " ".join(i.text for i in group).strip()
    name = ocr.match_vocab(joined, config.CARD_FOOTER_VOCAB, min_score=80)
    if name is None:
        return None, zone

    top = min(i.box.y for i in group)
    bottom = max(i.box.y2 for i in group)
    pad = anchor.box.h
    footer_line = Box(card.x, top - pad, card.w, (bottom - top) + 2 * pad, "footer")
    return name, footer_line


def safe_side_point(frame: np.ndarray) -> tuple[int, int]:
    """A click target that dismisses the deal animation and hits nothing else.

    The **right** margin, mid-height.  Both obvious alternatives on the left are
    occupied: the Daily Quests panel sits at mid-height, and the Auto Skip and
    Skip Wave controls sit at the bottom.  The bottom-left point used
    previously measured (57, 821) against an Auto Skip button spanning
    x=20..156, y=827..866 - inside its x-range and six pixels above its top
    edge - so every animation-cancel press was toggling Auto Skip off and on
    again.

    Checked against every full-resolution frame on hand: no UI text within
    160x90 px of this point.
    """
    r = region(frame, 0.96, 0.62, 0.02, 0.02)
    return int(r.cx), int(r.cy)
