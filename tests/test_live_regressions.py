"""Regressions captured from live runs, each one a real failure.

These frames are why the offers detector has loose geometric tolerances and a
dimming gate.  Without them a future "tidy-up" would tighten the bounds again
and silently reintroduce bugs that cost most of a session's measurements.
"""

from __future__ import annotations

import pytest

from swarmbot import config
from swarmbot.capture import load_image
from swarmbot.vision import cards, fastpath


@pytest.fixture(scope="module")
def grass():
    """Plain gameplay: a green field, no offers screen anywhere."""
    return load_image(config.SOURCES / "gameplay-grass.png")


@pytest.fixture(scope="module")
def mid_deal():
    """A real offers screen caught mid-animation, cards still sliding in."""
    return load_image(config.SOURCES / "offers-mid-deal.png")


@pytest.fixture(scope="module")
def uneven():
    """A settled, perfectly readable screen whose cards are not evenly spaced."""
    return load_image(config.SOURCES / "offers-uneven-row.png")


@pytest.fixture(scope="module")
def card_boxes(offers):
    return [c.box for c in offers["uncommon"].cards]


def test_grass_is_not_mistaken_for_cards(grass, card_boxes):
    """Grass is green, flat and uniform, so it sits in the common hue range and
    sails past a coverage test.  The bot was picking phantom cards on an empty
    field - the single biggest cause of lost measurements."""
    boxes = [b.scaled(grass.shape[1] / 1005, grass.shape[0] / 572) for b in card_boxes]
    assert fastpath.offers_visible(grass, boxes) is None
    assert not fastpath.offers_emerging(grass, boxes)
    assert cards.detect(grass) is None


def test_gameplay_lacks_the_dimmed_backdrop(grass, mid_deal, uneven):
    """The gate that separates the two.

    Asked *relative to gameplay*, which is the only form of the question that
    survives a change of arena: an absolute "below V=60" answered 0.008-0.019
    on green-arena gameplay and 0.084-0.368 on its offers screens, but 0.014 on
    a real blue-arena offers screen - on the wrong side of its own threshold.
    """
    lit = fastpath.world_brightness(grass)
    assert not fastpath.has_offers_backdrop(grass, lit)
    assert fastpath.has_offers_backdrop(mid_deal, lit)
    assert fastpath.has_offers_backdrop(uneven, lit)
    assert fastpath.dark_fraction(grass) < config.OFFERS_MIN_DARK
    assert fastpath.dark_fraction(uneven) >= config.OFFERS_MIN_DARK


def test_unevenly_spaced_row_still_parses(uneven):
    """Cards settle at slightly different heights and gaps (measured 317 vs 300
    px, chip y 325/323/349).  Tight geometric bounds rejected this frame even
    though every title on it is perfectly legible."""
    parsed = cards.detect(uneven)
    assert parsed is not None
    assert [c.title for c in parsed.cards] == ["Health", "Soul of Swiftness", "Damage"]
    assert [c.rarity for c in parsed.cards] == ["epic"] * 3


def test_mid_deal_frame_still_reads_its_rarities(mid_deal):
    """Mid-deal cards already show full colour coverage (~0.9), which is why
    coverage alone cannot gate a pick - card-band stability does that."""
    parsed = cards.detect(mid_deal)
    if parsed is not None:
        assert [c.rarity for c in parsed.cards] == ["common"] * 3


@pytest.mark.parametrize("name", ["offers-uneven-row", "offers-mid-deal"])
def test_real_offers_frames_are_recognised_as_offers(name):
    frame = load_image(config.SOURCES / f"{name}.png")
    assert fastpath.has_offers_backdrop(frame)


def test_heading_check_separates_offers_from_gameplay(grass, uneven, mid_deal, card_boxes):
    """A large area-of-effect can dim the field and wash the card slots in a
    matching hue, faking both the backdrop and colour checks at once.  The
    white "UPGRADE OFFERS" heading is what gameplay cannot fake."""
    boxes = [b.scaled(grass.shape[1] / 1005, grass.shape[0] / 572) for b in card_boxes]
    assert not fastpath.has_offers_heading(grass, boxes)

    for frame in (uneven, mid_deal):
        parsed = cards.detect(frame)
        own = [c.box for c in parsed.cards] if parsed else boxes
        assert fastpath.has_offers_heading(frame, own)


def test_heading_check_handles_no_boxes(grass):
    assert not fastpath.has_offers_heading(grass, [])


def test_a_settled_screen_passes_the_ink_gate(uneven):
    drawn = cards.detect(uneven)
    assert drawn is not None
    assert fastpath.cards_are_drawn(uneven, [c.box for c in drawn.cards])


def test_mid_deal_cards_are_never_picked(mid_deal):
    """Mid-deal the chips and icons are up but the titles are not yet drawn.

    The cheap ink gate may now admit such a frame - it only asks whether an OCR
    read is worth attempting, because demanding ink in every slot broke screens
    that deal fewer than three cards.  The guarantee that matters is
    end-to-end: `detect` yields cards with no title, and the caller requires
    every card to be named before it will pick.
    """
    dealing = cards.detect(mid_deal)
    if dealing is None:
        return
    assert not all(c.title for c in dealing.cards), (
        "a mid-deal frame must not present itself as fully named"
    )


def test_cards_are_drawn_handles_no_boxes(grass):
    assert not fastpath.cards_are_drawn(grass, [])


def test_settled_reference_frames_all_pass_the_ink_gate(frames, offers):
    """Every hand-verified offers screen must be accepted."""
    for name in ("weapon", "uncommon", "rare", "epic", "legendary"):
        boxes = [c.box for c in offers[name].cards]
        assert fastpath.cards_are_drawn(frames[name], boxes), name
