"""Sub-millisecond frame checks, so OCR never sits on the critical path.

OCR costs ~800ms per call no matter how small the crop, because the recogniser
dominates.  Polling for the offers screen with OCR therefore meant a full second
of latency before the bot could click, and reading the Stats panel meant holding
the game paused for five seconds.

Everything the bot needs *in order to act* is available from colour instead:

    rarity      median HSV of the card body          ~1ms
    NEW! badge  yellow pixel fraction in a corner     ~1ms
    offers up?  all three card boxes look like cards  ~2ms

OCR still runs - card titles and stat rows need it - but only *after* the click
has been sent, while the game is already running again.  This module is what
makes that split possible.

It depends on a cached layout (see `layout.py`): the boxes are found once by
OCR, then reused for as long as they keep validating.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .. import config
from .geometry import Box

# The NEW! badge relative to its card box.  Measured on a live 1920x991 frame:
# card2 box x=1134 w=282 y=317 h=433, badge box x=1345..1434 y=298..341, i.e.
# x 0.75-1.06 and y -0.04..+0.06 of the card.  Padded for safety.
BADGE_X = 0.68
BADGE_W = 0.47
BADGE_Y = -0.10
BADGE_H = 0.22


def badge_box(card: Box) -> Box:
    return Box(
        int(card.x + BADGE_X * card.w),
        int(card.y + BADGE_Y * card.h),
        max(1, int(BADGE_W * card.w)),
        max(1, int(BADGE_H * card.h)),
        "badge",
    )


def has_new_badge(frame: np.ndarray, card: Box) -> bool:
    """Bright saturated yellow in the card's top-right corner."""
    crop = badge_box(card).crop(frame)
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, config.NEW_BADGE_HSV_LOW, config.NEW_BADGE_HSV_HIGH)
    return float(mask.mean()) / 255.0 >= config.NEW_BADGE_MIN_PIXEL_FRAC


def find_new_badges(frame: np.ndarray, cards: list[Box]) -> list[bool]:
    """Which of these cards carries a NEW! badge, tolerant of layout drift.

    Checking a fixed corner of each remembered box is fragile: the offers row
    shifts between screens (spacing 313 to 334, top edge 317 to 357), and a
    box off by 40px looked at the wrong place and called the wrong card
    undiscovered.

    So instead every yellow blob in the card row is found once, and each is
    assigned to whichever card it sits nearest.  Cards are ~314px apart while
    the drift is tens of pixels, so the nearest card is never in doubt.
    """
    if not cards:
        return []

    pad_y = int(max(c.h for c in cards) * 0.25)
    x1 = max(0, min(c.x for c in cards) - 20)
    x2 = min(frame.shape[1], max(c.x2 for c in cards) + 20)
    y1 = max(0, min(c.y for c in cards) - pad_y)
    y2 = min(frame.shape[0], min(c.y for c in cards) + pad_y)
    strip = frame[y1:y2, x1:x2]
    if strip.size == 0:
        return [False] * len(cards)

    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, config.NEW_BADGE_HSV_LOW, config.NEW_BADGE_HSV_HIGH)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)

    width = cards[0].w
    min_area = max(30, int(width * width * config.BADGE_MIN_AREA_FRAC))
    # The badge hangs off the card's top-right, not its centre.
    anchors = [c.cx + config.BADGE_ANCHOR_OFFSET * c.w for c in cards]
    found = [False] * len(cards)

    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            continue
        blob_x = centroids[i][0] + x1
        distances = [abs(blob_x - a) for a in anchors]
        nearest = min(range(len(anchors)), key=lambda k: distances[k])
        if distances[nearest] <= config.BADGE_MAX_OFFSET * width:
            found[nearest] = True
    return found


@dataclass(frozen=True)
class ColourReading:
    rarity: str | None
    coverage: float  # fraction of the body agreeing with `rarity`

    @property
    def confident(self) -> bool:
        return self.rarity is not None and self.coverage >= config.CARD_MIN_COVERAGE


def read_card_colour(frame: np.ndarray, card: Box) -> ColourReading:
    """Classify a card body by colour and report how uniform that colour is.

    Coverage is what separates a real card from a patch of scenery that happens
    to have a matching median hue: a card is a flat wash, so most of its pixels
    agree with the median; grass and mobs do not.
    """
    crop = card.inset(0.12, 0.10).crop(frame)
    if crop.size == 0:
        return ColourReading(None, 0.0)

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    med_hue, med_sat, med_val = float(np.median(hue)), float(np.median(sat)), float(np.median(val))
    total = hue.size

    if med_sat < config.WEAPON_MAX_SATURATION:
        lo_v, hi_v = config.WEAPON_VALUE_RANGE
        if not (lo_v <= med_val <= hi_v):
            return ColourReading(None, 0.0)
        agree = (sat < config.WEAPON_MAX_SATURATION) & (val >= lo_v) & (val <= hi_v)
        return ColourReading("weapon", float(np.count_nonzero(agree)) / total)

    for rarity, (lo, hi) in config.RARITY_HUE_RANGES.items():
        if lo <= med_hue <= hi:
            agree = (hue >= lo) & (hue <= hi) & (sat >= config.WEAPON_MAX_SATURATION)
            return ColourReading(rarity, float(np.count_nonzero(agree)) / total)

    return ColourReading(None, 0.0)


def dark_fraction(frame: np.ndarray) -> float:
    """Share of the frame darker than the dim threshold."""
    value = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 2]
    return float((value < config.DIM_VALUE_THRESHOLD).mean())


def world_brightness(frame: np.ndarray) -> float:
    """Median V of the frame - how brightly lit this screen is overall.

    Median rather than mean so the HUD, the cards and a passing explosion
    cannot move it; what is being measured is the field the player is standing
    on.
    """
    return float(np.median(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 2]))


def has_offers_backdrop(frame: np.ndarray, baseline: float | None = None) -> bool:
    """Is the world dimmed behind an overlay?

    This is what stops **grass** being read as three common cards.  Grass is
    green, flat and uniform, so it sits squarely in the common hue range and
    sails past a coverage test - the bot was picking phantom cards on an empty
    field, which is where the stray clicks and the 19% title-read rate came
    from.  Texture does not separate them (measured Laplacian variance
    overlaps), but dimming does.

    **Dimming is relative, so the test has to be too.**  Counting pixels below
    an absolute V=60 measured 0.008-0.019 on green-arena gameplay against
    0.084-0.368 on its offers screens, which looked like a wide margin and was
    not: the overlay only *scales* brightness (measured ~0.6x), so the dimmed
    world lands at V 61-89 - straddling the threshold rather than sitting under
    it.  On the blue arena, which is lit brighter, the same overlay leaves
    almost nothing below V=60, and the bot pressed skip at a real offers screen
    ten times in a row while logging "offers-closed".  Reproduced from the green
    frames: a 1.15x gain already drops three of five below the 0.05 minimum.

    So when the caller knows what un-dimmed play looks like on *this* arena, the
    question asked is "is the world dimmer than that", which scales with the
    arena on both sides.

    **Until it knows, this abstains rather than guessing.**  Falling back to the
    absolute fraction was tried and is worse than useless: the bot is routinely
    started while an offers screen is already up, so it never sees the un-dimmed
    play it would need to learn the arena - the fallback is not a rare first-frame
    case, it is the whole session.  Measured live on the blue arena, a parsed,
    perfectly readable offers screen gives 0.014 against a 0.05 minimum, and the
    bot sat pressing skip at it.  Abstaining costs nothing, because dimming is
    the weakest of the four signals `offers_visible` requires: the white heading,
    drawn titles and flat card colour reject every non-offers screen on record
    (gameplay, grass, hub, staging, AGAIN and the pause panel) without it.

    Callers that do *not* have those other signals to lean on must supply their
    own - see `offers_emerging`, which fires before any title is drawn and so
    checks the heading itself rather than abstaining into nothing.
    """
    if baseline is not None and baseline > 0:
        return world_brightness(frame) <= config.OFFERS_DIM_RATIO * baseline
    return True


def offers_emerging(
    frame: np.ndarray,
    boxes: list[Box],
    baseline: float | None = None,
    title: Box | None = None,
) -> bool:
    """Have the cards *started* appearing?

    `offers_visible` waits for all three cards to reach full colour, which is
    most of the way through the deal animation.  The side-click that skips that
    animation should not wait for it - so this fires on the first hint of
    rarity colour in any card slot, several hundred milliseconds earlier.

    The heading is checked here and not in `offers_visible`'s stack, because
    this fires before any title is drawn and colour alone is exactly what grass
    fakes.  It is available this early: measured 0.033 on a frame caught with
    the cards still sliding in, against 0.0045 on an open field.  Without it,
    an unknown arena - where the dimming test abstains - put the phantom-card
    bug straight back.
    """
    if not has_offers_backdrop(frame, baseline) or not has_offers_heading(frame, boxes, title):
        return False
    for box in boxes:
        reading = read_card_colour(frame, box)
        if reading.rarity is not None and reading.coverage >= config.CARD_EMERGE_COVERAGE:
            return True
    return False


def white_coverage(frame: np.ndarray, box: Box) -> float:
    """Fraction of a box that is near-white - i.e. UI lettering."""
    crop = box.crop(frame)
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return float(cv2.inRange(hsv, (0, 0, 190), (179, 60, 255)).mean()) / 255.0


def has_offers_heading(
    frame: np.ndarray, boxes: list[Box], title: Box | None = None
) -> bool:
    """Is the big white "UPGRADE OFFERS" heading above the cards?

    Dimming alone is not proof of an offers screen: a large dark area-of-effect
    can darken the field *and* wash the card slots in a matching hue, faking
    both earlier checks at once.  The heading cannot be faked by gameplay - it
    is a slab of near-white text.  Read as pixels, not OCR, so it stays ~1ms.

    **Where to look is the whole difficulty.**  Guessing the position from the
    card row - a band 0.02 to 0.30 card-heights above it - assumes the gap
    between heading and cards scales with the cards, and it does not: the game
    re-lays out its UI at different window sizes rather than scaling it.
    Measured, the heading spans 0.09-0.17 card-heights above the row at
    1920x991 but 0.28-0.39 at 1002x750, so the band's 0.30 ceiling clipped all
    but a sliver of it: 0.0098 against a 0.020 minimum, on a screen whose three
    cards were parsing perfectly.  The bot
    logged `no-cards` 74 times in a row and never took an upgrade.

    So when the caller knows where the heading actually is - `cards.detect`
    locates it by OCR and the layout cache keeps it - this measures exactly
    there, which is both precise and immune to window size.  Measured inside
    that box: real offers 0.144-0.202, gameplay 0.000-0.024.

    The derived band remains for the first screen of a session, before any
    layout has been solved, and now reaches 0.45 card-heights up so that it
    covers small windows too.
    """
    if title is not None:
        pad_y = int(title.h * 0.35)
        pad_x = int(title.w * 0.10)
        padded = Box(title.x - pad_x, title.y - pad_y,
                     title.w + 2 * pad_x, title.h + 2 * pad_y)
        return white_coverage(frame, padded) >= config.OFFERS_MIN_TITLE_WHITE

    if not boxes:
        return False
    top = min(b.y for b in boxes)
    height = max(b.h for b in boxes)
    width = frame.shape[1]
    y1 = max(0, int(top - config.OFFERS_HEADING_BAND_TOP * height))
    y2 = max(y1 + 1, int(top - 0.02 * height))
    band = Box(int(width * 0.25), y1, int(width * 0.50), y2 - y1)
    return white_coverage(frame, band) >= config.OFFERS_MIN_HEADING


def title_ink(frame: np.ndarray, card: Box) -> float:
    """White-text coverage in a card's title zone, just below the rarity chip."""
    crop = card.sub(0.05, 0.04, 0.90, 0.16).crop(frame)
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return float(cv2.inRange(hsv, (0, 0, 190), (179, 60, 255)).mean()) / 255.0


def cards_are_drawn(frame: np.ndarray, boxes: list[Box]) -> bool:  # noqa: D401
    """Have all three cards finished drawing, titles and all?

    Mid-deal the cards are translucent bars whose chips and icons are already
    up but whose *titles are not yet drawn*.  Neither colour coverage nor
    frame-to-frame stability catches that state - coverage reads 0.74-0.93
    either way, and the slide has plateaus that look stable - so picks were
    landing on cards whose names could not be read afterwards.

    Measuring the title text directly is both the cheapest and the most
    honest test, because a readable title is precisely what the caller needs.
    Measured minimum across the three cards: 0.000 while dealing, 0.044-0.093
    once settled.
    """
    # At least one slot with a drawn title is enough.  This is only a cheap
    # "is an OCR read worth attempting" filter - `cards.detect` is what decides
    # correctness, and it already requires every card it finds to be named.
    #
    # Requiring *every* colour-confident slot to have ink was wrong: when the
    # screen deals two cards they are centred, so all three cached slots still
    # catch card colour while one of them sits over a card edge with almost no
    # text (measured 0.001 against a 0.025 threshold).  That single slot vetoed
    # the whole screen and the bot sat on it indefinitely.
    drawn = sum(1 for b in boxes if title_ink(frame, b) >= config.CARD_MIN_TITLE_INK)
    return drawn >= config.MIN_CARDS


def offers_visible(
    frame: np.ndarray,
    boxes: list[Box],
    baseline: float | None = None,
    title: Box | None = None,
) -> list[ColourReading] | None:
    """Is an offers screen up, with at least one card drawn?

    Returns the per-slot colour readings, or None if this is not an offers
    screen.  Costs ~3ms, so it can be polled at 20Hz.

    Three independent things must agree - the world is dimmed, the heading is
    up, and at least one slot reads as a drawn card - because each signal alone
    has been observed to fire on something else.  It is deliberately *at least
    one* rather than all three: the screen deals fewer than three cards late in
    a run, and demanding three made the bot ignore those screens entirely.
    """
    if (
        not boxes
        or not has_offers_backdrop(frame, baseline)
        or not has_offers_heading(frame, boxes, title)
        or not cards_are_drawn(frame, boxes)
    ):
        return None
    readings = [read_card_colour(frame, b) for b in boxes]
    if sum(1 for r in readings if r.confident) < config.MIN_CARDS:
        return None
    return readings


def is_dimmed(frame: np.ndarray) -> bool:
    """Is a modal panel covering the screen?

    The pause screen darkens almost everything behind it, which separates it
    from gameplay by a mile: measured 87% of pixels below V=60 with the panel
    open, versus 0.9% during a wave.  Costs ~1ms, so the bot can wait for the
    panel to actually appear instead of guessing a delay - guessing 0.30s meant
    it snapped live gameplay and recorded nothing.
    """
    value = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 2]
    return float((value < config.DIM_VALUE_THRESHOLD).mean()) >= config.DIM_MIN_FRACTION


def digest(frame: np.ndarray, size: int = 32) -> np.ndarray:
    """A tiny greyscale thumbnail used to notice that the screen changed."""
    small = cv2.resize(frame, (size, size), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)


def changed(a: np.ndarray | None, b: np.ndarray, threshold: float = 6.0) -> bool:
    """Has the screen changed enough to be worth a full (expensive) look?"""
    if a is None:
        return True
    return float(np.abs(a - b).mean()) >= threshold
