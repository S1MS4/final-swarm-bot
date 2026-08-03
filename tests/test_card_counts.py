"""Screens that deal fewer than three cards.

Late in a run - once most upgrades are owned - the offers screen deals two
cards or one.  Every stage assumed exactly three, so `detect` returned None and
the bot ignored those screens entirely, sitting on them until a human stepped
in.
"""

from __future__ import annotations

import numpy as np
import pytest

from swarmbot import config, ocr
from swarmbot.vision import cards, fastpath
from swarmbot.vision.geometry import Box


def chip(text: str, x: int, y: int = 320, w: int = 60, h: int = 21) -> ocr.TextItem:
    return ocr.TextItem(text=text, box=Box(x, y, w, h, text), confidence=1.0)


def test_three_chips_are_found():
    found = cards._find_chips([chip("Epic", 630), chip("Epic", 944), chip("Epic", 1258)])
    assert [c[1] for c in found] == ["epic"] * 3


def test_two_chips_are_found():
    found = cards._find_chips([chip("Rare", 790), chip("Rare", 1104)])
    assert [c[1] for c in found] == ["rare"] * 2


def test_one_chip_is_found():
    found = cards._find_chips([chip("Legendary", 944)])
    assert [c[1] for c in found] == ["legendary"]


def test_no_chips_yields_nothing():
    assert cards._find_chips([]) == []


def test_more_than_three_keeps_only_the_top_row():
    """Card descriptions contain rarity words ("Increases weapon attack
    speed"), but they sit well below the chips."""
    found = cards._find_chips([
        chip("Weapon", 630), chip("Weapon", 944), chip("Weapon", 1258),
        chip("weapon", 700, y=560),        # a description, much lower
    ])
    assert len(found) == 3
    assert all(c[0].box.y == 320 for c in found)


def test_chips_are_returned_left_to_right():
    found = cards._find_chips([chip("Epic", 1258), chip("Epic", 630), chip("Epic", 944)])
    assert [c[0].box.x for c in found] == [630, 944, 1258]


def _vacant(like: Box) -> Box:
    """A slot at the row's height but off the side of the frame, so it reads as
    holding no card - which is what an undealt slot looks like."""
    return Box(-like.w - 50, like.y, like.w, like.h, "vacant")


@pytest.mark.parametrize("occupied", [1, 2, 3])
def test_fast_path_accepts_partially_filled_rows(frames, offers, occupied):
    """The colour gate must not demand all three slots hold a card."""
    frame = frames["epic"]
    boxes = [c.box for c in offers["epic"].cards]
    probe = boxes[:occupied] + [_vacant(boxes[0])] * (3 - occupied)
    assert fastpath.offers_visible(frame, probe) is not None


def test_fast_path_still_rejects_a_frame_with_no_cards(frames, offers):
    boxes = [c.box for c in offers["epic"].cards]
    assert fastpath.offers_visible(frames["epic"], [_vacant(boxes[0])] * 3) is None


def test_cards_are_drawn_ignores_vacant_slots(frames, offers):
    frame = frames["legendary"]
    boxes = [c.box for c in offers["legendary"].cards]
    probe = [boxes[0], _vacant(boxes[0]), _vacant(boxes[0])]
    assert fastpath.cards_are_drawn(frame, probe)


def test_single_card_layout_falls_back_to_frame_scale():
    """One card gives no chip spacing to measure the layout from."""
    width = 1920
    expected = width * config.UNIT_PER_FRAME_WIDTH
    assert 300 < expected < 330, "should land near the measured 314px"


# --- the real two-card frame that stalled a live run ------------------------

@pytest.fixture(scope="module")
def two_card_frame():
    from swarmbot.capture import load_image
    return load_image(config.SOURCES / "offers-two-cards.png")


def test_two_card_screen_parses(two_card_frame):
    parsed = cards.detect(two_card_frame)
    assert parsed is not None
    assert [(c.title, c.rarity) for c in parsed.cards] == [
        ("Movement Speed", "legendary"),
        ("Size", "legendary"),
    ]


def test_two_card_screen_passes_the_gate_with_stale_three_card_boxes(two_card_frame):
    """The stall this frame was captured from.

    Two cards are dealt *centred*, so all three cached slots still catch card
    colour while one sits over a card edge with almost no text (0.001 against a
    0.025 threshold).  Requiring ink in every colour-confident slot let that one
    slot veto the screen, and the bot pressed skip at it forever.
    """
    # The three-card layout the bot would still be holding, at this frame's
    # scale: unit 314px, centred on the frame.
    height, width = two_card_frame.shape[:2]
    unit = width * config.UNIT_PER_FRAME_WIDTH
    box_w, box_h = int(0.90 * unit), int(1.38 * unit)
    stale = [
        Box(int(width / 2 + offset * unit - box_w / 2), 318, box_w, box_h, f"card{i}")
        for i, offset in enumerate((-1, 0, 1))
    ]
    assert fastpath.offers_visible(two_card_frame, stale) is not None


def test_two_card_screen_is_not_confused_with_gameplay(two_card_frame):
    assert fastpath.has_offers_backdrop(two_card_frame)
    parsed = cards.detect(two_card_frame)
    assert len(parsed.cards) == 2
