"""The fast path must always be able to hand back to the full classifier.

The colour fast path fires on *stale card boxes over any dimmed screen*.  The
end-of-run screens are dimmed too, so it fired on those, found no cards,
returned, and immediately re-entered itself - an infinite loop in which the
classify that would have recognised the screen was never reached.  The bot
pressed skip at the AGAIN screen indefinitely.
"""

from __future__ import annotations

import inspect

import pytest

from swarmbot import bot as bot_mod, config
from swarmbot.capture import load_image
from swarmbot.state import GameState, classify
from swarmbot.vision import cards, fastpath


@pytest.fixture(scope="module")
def again_frame():
    return load_image(config.SOURCES / "again-screen.png")


def test_again_screen_is_classified_correctly(again_frame):
    obs = classify(again_frame)
    assert obs.state is GameState.AGAIN
    assert obs.button("again") is not None


def test_again_screen_is_dimmed_like_an_offers_screen(again_frame):
    """Why the fast path fires on it at all - dimming alone cannot separate
    the two."""
    assert fastpath.has_offers_backdrop(again_frame)


def test_again_screen_has_no_cards(again_frame):
    assert cards.detect(again_frame) is None


def test_no_card_path_forces_a_full_classify():
    """The loop-breaker.  Every exit from the offers handler that failed to
    pick must set `force_classify`, or the fast path re-enters itself forever.
    """
    source = inspect.getsource(bot_mod.Bot._handle_offers)
    no_cards = source.index('self.log.event("no-cards"')
    preceding = source[:no_cards]
    assert "self.force_classify = True" in preceding.rsplit("if offers is None:", 1)[-1], (
        "the no-cards exit must force the slow path on the next iteration"
    )


def test_fast_path_is_gated_on_force_classify():
    source = inspect.getsource(bot_mod.Bot.loop)
    assert "not self.force_classify" in source
    assert "self.force_classify = False" in source, "and it must be cleared after classifying"


def test_layout_is_not_re_solved_when_already_cached():
    """Solving the offers layout waits for the screen to settle and then runs a
    full OCR pass.  Doing that on every offers screen ate the pick window: the
    screen closed before the bot could act, which forced the slow path again,
    which re-solved the layout - a loop in which nothing was ever picked.
    """
    source = inspect.getsource(bot_mod.Bot.loop)
    marker = "self.solve_offers_layout()"
    assert marker in source
    line = next(l for l in source.splitlines() if marker in l)
    assert "self.layout.card_boxes" in line, (
        "the slow path must skip solving when a layout is already cached"
    )
