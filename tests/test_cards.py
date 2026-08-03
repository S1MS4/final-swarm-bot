"""The offers-screen parser, pinned to the five reference frames.

Each frame is transcribed by hand below, so a regression in segmentation,
colour classification, OCR preprocessing, or footer grouping fails loudly
without the game running.
"""

from __future__ import annotations

import pytest

from swarmbot.vision import cards

# (title, rarity, is_new, footer_stat, arrows) per card, left to right.
EXPECTED = {
    "weapon": [
        ("Spike Ball", "weapon", True, None, 0),
        ("Frost Walker", "weapon", True, None, 0),
        ("Bow", "weapon", True, None, 0),
    ],
    "uncommon": [
        ("Damage", "common", True, "Damage", 1),
        ("Health", "common", True, "Max Health", 1),
        ("Attack Speed", "common", True, "Attack Speed", 1),
    ],
    "rare": [
        ("Movement Speed", "rare", True, "Movement Speed", 2),
        ("Thorns", "rare", True, "Thorns", 1),
        ("Regen", "rare", True, "Health Regen", 2),
    ],
    # The one frame with no NEW! badges - the discovery signal must not be
    # hallucinated from the card art.
    "epic": [
        ("Luck", "epic", False, "Luck", 3),
        ("Size", "epic", False, "Size", 2),
        ("Armor", "epic", False, "Armor", 2),
    ],
    "legendary": [
        ("Attack Speed", "legendary", True, "Attack Speed", 4),
        ("Size", "legendary", True, "Size", 3),
        ("Movement Speed", "legendary", True, "Movement Speed", 4),
    ],
}

FRAMES = sorted(EXPECTED)


@pytest.mark.parametrize("name", FRAMES)
def test_offers_screen_is_detected(offers, name):
    assert offers[name] is not None


@pytest.mark.parametrize("name", FRAMES)
def test_three_cards_are_segmented(offers, name):
    assert len(offers[name].cards) == 3


@pytest.mark.parametrize("name", FRAMES)
def test_titles(offers, name):
    got = [c.title for c in offers[name].cards]
    assert got == [e[0] for e in EXPECTED[name]]


@pytest.mark.parametrize("name", FRAMES)
def test_rarity(offers, name):
    got = [c.rarity for c in offers[name].cards]
    assert got == [e[1] for e in EXPECTED[name]]


@pytest.mark.parametrize("name", FRAMES)
def test_colour_and_chip_label_agree(offers, name):
    """Rarity is decided by colour; the chip text is an independent check."""
    assert offers[name].warnings == []
    for card in offers[name].cards:
        assert card.rarity_by_colour == card.rarity_by_chip


@pytest.mark.parametrize("name", FRAMES)
def test_new_badge(offers, name):
    got = [c.is_new for c in offers[name].cards]
    assert got == [e[2] for e in EXPECTED[name]]


@pytest.mark.parametrize("name", FRAMES)
def test_footer_stat(offers, name):
    """Two-line footers must not be matched from a fragment.

    "Movement" / "Speed" wraps onto two lines, and the fragment "Speed" scores
    just as well against "Attack Speed" as against the truth.
    """
    got = [c.footer_stat for c in offers[name].cards]
    assert got == [e[3] for e in EXPECTED[name]]


@pytest.mark.parametrize("name", FRAMES)
def test_arrow_count(offers, name):
    got = [c.arrows for c in offers[name].cards]
    assert got == [e[4] for e in EXPECTED[name]]


@pytest.mark.parametrize("name", FRAMES)
def test_weapon_flag(offers, name):
    for card in offers[name].cards:
        assert card.is_weapon == (card.rarity == "weapon")


@pytest.mark.parametrize("name", FRAMES)
def test_card_boxes_are_disjoint_and_ordered(offers, name):
    boxes = [c.box for c in offers[name].cards]
    assert [b.x for b in boxes] == sorted(b.x for b in boxes)
    for a, b in zip(boxes, boxes[1:]):
        assert a.iou(b) == 0


@pytest.mark.parametrize("name", FRAMES)
def test_click_points_land_inside_their_card(offers, name):
    for card in offers[name].cards:
        x, y = card.click_point
        assert card.box.contains(x, y)


@pytest.mark.parametrize("name", FRAMES)
def test_side_click_avoids_every_card(offers, frames, name):
    x, y = cards.safe_side_point(frames[name])
    for card in offers[name].cards:
        assert not card.box.contains(x, y)


def test_non_offers_frame_is_rejected(frames):
    """The Stats panel must never be mistaken for an offers screen."""
    assert cards.detect(frames["base-stats"]) is None
