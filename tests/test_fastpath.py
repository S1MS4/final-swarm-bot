"""The colour-only fast path must agree with OCR, and be much cheaper.

If these drift apart the bot starts clicking the wrong card, so every reference
frame is checked both ways.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from swarmbot.vision import cards, fastpath

FRAMES = ["weapon", "uncommon", "rare", "epic", "legendary"]


@pytest.fixture(scope="module")
def boxes(offers):
    return {n: [c.box for c in offers[n].cards] for n in FRAMES}


@pytest.mark.parametrize("name", FRAMES)
def test_rarity_matches_ocr_based_detection(frames, offers, boxes, name):
    readings = fastpath.offers_visible(frames[name], boxes[name])
    assert readings is not None
    assert [r.rarity for r in readings] == [c.rarity for c in offers[name].cards]


@pytest.mark.parametrize("name", FRAMES)
def test_new_badge_matches_ocr_based_detection(frames, offers, boxes, name):
    got = [fastpath.has_new_badge(frames[name], b) for b in boxes[name]]
    assert got == [c.is_new for c in offers[name].cards]


@pytest.mark.parametrize("name", FRAMES)
def test_card_bodies_are_a_uniform_wash(frames, boxes, name):
    """Coverage is what distinguishes a card from same-hued scenery."""
    for reading in fastpath.offers_visible(frames[name], boxes[name]):
        assert reading.coverage > 0.8


def test_rejects_a_frame_with_no_cards(frames, boxes):
    assert fastpath.offers_visible(frames["base-stats"], boxes["epic"]) is None


def test_rejects_a_bright_desaturated_screen(boxes):
    """A white page is desaturated like a weapon card but far too bright.

    Regression test: without a brightness bound, a browser window behind the
    game read as three flawless weapon cards.
    """
    white = np.full((606, 1018, 3), 255, dtype=np.uint8)
    assert fastpath.offers_visible(white, boxes["weapon"]) is None


def test_rejects_an_empty_black_screen(boxes):
    black = np.zeros((606, 1018, 3), dtype=np.uint8)
    assert fastpath.offers_visible(black, boxes["weapon"]) is None


def test_badge_box_sits_at_the_card_top_right(offers):
    card = offers["legendary"].cards[0].box
    badge = fastpath.badge_box(card)
    assert badge.x > card.cx  # right half
    assert badge.y < card.y + card.h * 0.25  # near the top
    assert badge.x2 > card.x2  # overhangs, as the badge does


def test_fast_path_is_orders_of_magnitude_cheaper_than_ocr(frames, boxes):
    """The whole point: this must be milliseconds, not ~800ms."""
    frame, box = frames["legendary"], boxes["legendary"]
    fastpath.offers_visible(frame, box)  # warm caches

    start = time.perf_counter()
    for _ in range(20):
        fastpath.offers_visible(frame, box)
        [fastpath.has_new_badge(frame, b) for b in box]
    per_call = (time.perf_counter() - start) / 20

    assert per_call < 0.050, f"fast path took {per_call * 1000:.1f}ms per frame"


def test_digest_detects_a_changed_screen(frames):
    a = fastpath.digest(frames["epic"])
    assert not fastpath.changed(a, fastpath.digest(frames["epic"]))
    assert fastpath.changed(a, fastpath.digest(frames["weapon"]))
    assert fastpath.changed(None, a), "no previous frame always counts as changed"


@pytest.mark.parametrize("name", FRAMES)
def test_badge_blobs_survive_layout_drift(frames, offers, boxes, name):
    """The offers row shifts between screens (spacing 313->334, top 317->357).

    Checking a fixed corner of a remembered box then looked at the wrong place
    and reported the wrong card as undiscovered, so badges are located as blobs
    and assigned to the nearest card instead.
    """
    from swarmbot.vision.geometry import Box

    truth = [c.is_new for c in offers[name].cards]
    exact = boxes[name]
    drifted = [Box(b.x - 24, b.y + 40, int(b.w * 1.07), int(b.h * 1.07), b.label) for b in exact]

    assert fastpath.find_new_badges(frames[name], exact) == truth
    assert fastpath.find_new_badges(frames[name], drifted) == truth


def test_find_new_badges_handles_no_cards():
    import numpy as np

    assert fastpath.find_new_badges(np.zeros((10, 10, 3), dtype=np.uint8), []) == []
