"""Two reported annoyances, and what the code now guarantees about them.

1. Auto Skip did not come on until wave 4 of the bonus round.
2. The bot misbehaves when the window is alt-tabbed or resized.
"""

from __future__ import annotations

import inspect

import cv2
import numpy as np
import pytest

from swarmbot import bot as bot_mod, config
from swarmbot.capture import load_image
from swarmbot.layout import LayoutCache
from swarmbot.vision import cards


# ----------------------------------------------------------- Auto Skip timing


def test_the_first_enable_of_a_run_does_not_wait_for_a_streak():
    """Why it started on wave 4.

    Checks are AUTO_SKIP_RECHECK apart and the streak needed two of them, so the
    first click could not go out for ~16s.  Phase 2 passes a wave roughly every
    5s, so Auto Skip arrived three waves late every single run.

    The streak protects a toggle already known to be on, from a banner that
    suppresses its green.  Before that confirmation there is nothing to protect:
    the toggle resets with the run, so "never seen on" means off.
    """
    source = inspect.getsource(bot_mod.Bot.ensure_auto_skip)
    guarded = source.split("# Below the threshold on every sample.", 1)[1]
    streak_line = guarded.index("self.auto_skip_off_streak += 1")
    condition = guarded.index("if self.run.auto_skip_ok:")
    assert condition < streak_line, (
        "the streak must apply only once the toggle has been confirmed on"
    )


def test_the_streak_still_guards_a_confirmed_toggle():
    """The 23-needless-toggles bug must stay fixed."""
    assert config.AUTO_SKIP_OFF_STREAK >= 2
    source = inspect.getsource(bot_mod.Bot.ensure_auto_skip)
    assert "if self.auto_skip_off_streak < config.AUTO_SKIP_OFF_STREAK:" in source


def test_a_wrong_first_click_is_verified_rather_than_assumed():
    """What makes acting on one reading affordable: the border is re-read after
    the click, so a mistake is recorded instead of believed."""
    source = inspect.getsource(bot_mod.Bot.ensure_auto_skip)
    after_click = source.split('self.click((button.cx, button.cy), "auto-skip")', 1)[1]
    assert "self.auto_skip_best_green(button)" in after_click
    assert 'state="still-off"' in after_click


# -------------------------------------------------------- window size changes


def test_resizing_drops_every_remembered_coordinate():
    frame_big = np.zeros((991, 1920, 3), np.uint8)
    frame_small = np.zeros((600, 1000, 3), np.uint8)

    cache = LayoutCache()
    cache.for_frame(frame_big)
    cache.remember_offers(frame_big, [cards.Box(1, 2, 3, 4)], 313.5)
    cache.gear = cards.Box(5, 6, 7, 8)
    cache.resume = cards.Box(9, 10, 11, 12)
    assert cache.card_boxes

    cache.for_frame(frame_small)
    assert cache.card_boxes == []
    assert cache.gear is None
    assert cache.resume is None


def test_the_grab_path_notices_a_resize_and_forces_a_reclassify():
    """`picked_band` is a digest taken through the old card boxes; comparing it
    against one taken through new boxes is meaningless, so it has to go too."""
    source = inspect.getsource(bot_mod.Bot.grab)
    assert "self.layout.for_frame(frame)" in source
    assert 'self.log.event("window-resized"' in source
    assert "self.picked_band = None" in source
    assert "self.force_classify = True" in source


def test_occlusion_is_recovered_from_rather_than_fatal():
    """Alt-tabbing away must not end an overnight run: capture reads a screen
    region, so a covered window would otherwise be OCR'd as the game."""
    source = inspect.getsource(bot_mod.Bot.grab)
    assert "strict=True" in source, "a covered window must be detected, not read"
    assert "self.window.focus()" in source
    assert 'self.log.event("occluded"' in source


# --------------------------------------------- detection across window sizes


@pytest.mark.parametrize("scale", [1.0, 0.75, 0.6, 0.5, 0.4])
def test_cards_are_found_at_any_window_size(scale):
    """Resolution independence, measured rather than assumed: the layout is
    solved from chip spacing and OCR is upscaled to a fixed target, so a smaller
    window is not why cards go missing.  All three cards and every title survive
    down to 40% of the captured size.
    """
    frame = load_image(config.SOURCES / "offers-blue-arena.png")
    small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    parsed = cards.detect(small)
    assert parsed is not None, f"no offers screen found at {scale:.0%}"
    assert [c.title for c in parsed.cards] == ["Sword", "Frost Walker", "Firestaff"]
    assert [c.rarity for c in parsed.cards] == ["weapon"] * 3


# ------------------------------------------------- the heading at other sizes


@pytest.fixture(scope="module")
def small():
    """A real 1002x750 offers screen the bot dumped while stuck on it.

    Three common cards, all three titles readable, and the bot logged
    `no-cards` 74 times in a row and never took an upgrade.
    """
    return load_image(config.SOURCES / "offers-small-window.png")


def test_the_small_window_frame_parses_perfectly(small):
    """Nothing was wrong with the cards - which is what made this confusing."""
    parsed = cards.detect(small)
    assert parsed is not None
    assert [c.title for c in parsed.cards] == ["Regen", "Attack Speed", "Health"]
    assert all(c.rarity == "common" for c in parsed.cards)


def test_the_heading_gap_does_not_scale_with_the_cards(small):
    """The root cause.  The band was placed 0.02-0.30 card-heights above the
    card row.  Measured, the heading spans 0.09-0.17 card-heights up at
    1920x991 but 0.28-0.39 at 1002x750 - it does not scale with the cards,
    because the game re-lays out its UI at other window sizes instead.  The old
    band's 0.30 ceiling therefore clipped all but a sliver of it."""
    from swarmbot.vision import fastpath

    parsed = cards.detect(small)
    boxes = [c.box for c in parsed.cards]
    top = min(b.y for b in boxes)
    height = max(b.h for b in boxes)
    assert (top - parsed.title_box.y) / height > 0.30, (
        "if the heading ever falls back inside 0.30 the old band would have worked"
    )

    narrow = fastpath.Box(
        int(small.shape[1] * 0.25), max(0, int(top - 0.30 * height)),
        int(small.shape[1] * 0.50), int(0.28 * height),
    )
    assert fastpath.white_coverage(small, narrow) < config.OFFERS_MIN_HEADING


def test_the_heading_is_found_at_a_small_window_size(small):
    from swarmbot.vision import fastpath

    parsed = cards.detect(small)
    boxes = [c.box for c in parsed.cards]

    # With the real title box, which is what the bot caches after one detect.
    assert fastpath.has_offers_heading(small, boxes, parsed.title_box)
    # And without it, on the widened bootstrap band.
    assert fastpath.has_offers_heading(small, boxes)


def test_the_small_window_offers_screen_is_seen(small):
    """End to end: this is the frame the bot sat on for 227 seconds."""
    from swarmbot.vision import fastpath

    parsed = cards.detect(small)
    boxes = [c.box for c in parsed.cards]
    assert fastpath.offers_visible(small, boxes, None, parsed.title_box) is not None
    assert fastpath.offers_emerging(small, boxes, None, parsed.title_box)


def test_the_cached_title_box_still_rejects_other_screens(small, offers):
    """Precision must not cost discrimination: measuring a fixed box is only
    safe if gameplay does not happen to have white text there."""
    from swarmbot.vision import fastpath

    title = cards.detect(small).title_box
    for name in ("gameplay-grass", "again-screen", "pause-screen"):
        frame = load_image(config.SOURCES / f"{name}.png")
        scaled = title.scaled(frame.shape[1] / small.shape[1], frame.shape[0] / small.shape[0])
        assert not fastpath.has_offers_heading(frame, [], scaled), name


def test_the_layout_cache_carries_the_heading(small):
    """The fast path can only use the real box if the cache keeps it."""
    from swarmbot.layout import LayoutCache

    parsed = cards.detect(small)
    cache = LayoutCache()
    cache.for_frame(small)
    cache.remember_offers(small, [c.box for c in parsed.cards], parsed.unit, parsed.title_box)
    assert cache.title_box == parsed.title_box

    cache.for_frame(np.zeros((600, 1000, 3), np.uint8))
    assert cache.title_box is None, "a resize must drop it with everything else"
