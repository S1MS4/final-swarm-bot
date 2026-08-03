"""The offers screen must be recognised on any arena, not just the green one.

Captured from a live failure: on the blue arena the bot pressed skip-animation
at a real offers screen ten times in a row, logged "offers-closed - screen went
away before a pick" each time, and only picked ~30s later when the slow OCR
classify finally caught it (runs/20260803-104812/log.jsonl).

`offers-closed` is logged on exactly one condition - `has_offers_backdrop` came
back False - so the failing gate is the dimming check, and the reason is that it
counted pixels below an absolute V=60.  The overlay only *scales* world
brightness (measured ~0.6x), so on the green arena the dimmed world lands at
V 61-89, i.e. straddling that threshold.  Scale the arena up and the gate
collapses: every reference offers screen falls from 0.08-0.30 dark to
0.01-0.17, under the 0.05 minimum.

A brightness gain is the faithful minimal reproduction: it leaves hue and
saturation untouched (S = (max-min)/max is scale-invariant), so rarity colour,
the white heading and the title-ink gate all behave exactly as before, and the
absolute darkness test is the only thing it moves.
"""

from __future__ import annotations

import inspect

import cv2
import numpy as np
import pytest

from swarmbot import bot as bot_mod, config
from swarmbot.capture import load_image
from swarmbot.vision import cards, fastpath

# Enough to clear the gate's margin without saturating the frame.  The real
# blue arena's offset is unknown; what matters is that no gain may break it.
BRIGHTER = 1.30


def brighten(frame: np.ndarray, gain: float = BRIGHTER) -> np.ndarray:
    """The same screen on a brighter arena: value scaled, hue and sat kept."""
    return np.clip(frame.astype(np.float32) * gain, 0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def grass():
    return load_image(config.SOURCES / "gameplay-grass.png")


@pytest.fixture(scope="module")
def baseline(grass):
    """What un-dimmed play looks like on the brightened arena."""
    return fastpath.world_brightness(brighten(grass))


@pytest.mark.parametrize("name", ["uncommon", "rare", "epic", "legendary", "weapon"])
def test_offers_are_seen_on_a_brighter_arena(frames, offers, name, baseline):
    frame = brighten(frames[name])
    boxes = [c.box for c in offers[name].cards]
    assert fastpath.has_offers_backdrop(frame, baseline)
    assert fastpath.offers_visible(frame, boxes, baseline) is not None
    assert fastpath.offers_emerging(frame, boxes, baseline)


def test_the_absolute_gate_is_what_used_to_fail(frames):
    """Guards the diagnosis itself.  This is the measurement the old gate made,
    and on a brighter arena it lands on the wrong side of its own threshold."""
    assert fastpath.dark_fraction(frames["rare"]) >= config.OFFERS_MIN_DARK
    assert fastpath.dark_fraction(brighten(frames["rare"])) < config.OFFERS_MIN_DARK


def test_grass_is_still_not_offers_on_a_brighter_arena(grass, offers, baseline):
    """The dimming gate exists to stop a green field reading as three commons.
    Making it relative must not give that back."""
    frame = brighten(grass)
    boxes = [
        b.scaled(frame.shape[1] / 1005, frame.shape[0] / 572)
        for b in (c.box for c in offers["uncommon"].cards)
    ]
    assert not fastpath.has_offers_backdrop(frame, baseline)
    assert fastpath.offers_visible(frame, boxes, baseline) is None
    assert not fastpath.offers_emerging(frame, boxes, baseline)


@pytest.mark.parametrize("name", ["hub-portal", "wave-staging", "again-screen"])
def test_other_undimmed_screens_are_not_offers_on_a_brighter_arena(name, offers, baseline):
    """These pass the white-heading check on their own (measured 0.088, 0.126,
    both above real offers screens), so the dimming gate is the only thing
    keeping them out - it has to keep working when it goes relative."""
    frame = brighten(load_image(config.SOURCES / f"{name}.png"))
    boxes = [
        b.scaled(frame.shape[1] / 1005, frame.shape[0] / 572)
        for b in (c.box for c in offers["uncommon"].cards)
    ]
    assert fastpath.offers_visible(frame, boxes, baseline) is None


def test_baseline_tracks_the_arena_rather_than_a_fixed_brightness(grass):
    """Both sides of the ratio scale together, which is the whole point."""
    dim = fastpath.world_brightness(load_image(config.SOURCES / "rare.png"))
    lit = fastpath.world_brightness(grass)
    for gain in (1.0, 1.15, 1.3):
        assert (
            fastpath.world_brightness(brighten(load_image(config.SOURCES / "rare.png"), gain))
            <= config.OFFERS_DIM_RATIO * fastpath.world_brightness(brighten(grass, gain))
        ), gain
    assert dim < lit


def test_the_gate_abstains_until_the_arena_is_known(frames, grass):
    """A session can start *on* an offers screen, so "no baseline yet" is not a
    rare first-frame case - it lasted the whole session on the blue arena, and
    an absolute fallback meant the bot never saw a single offers screen."""
    assert fastpath.has_offers_backdrop(brighten(frames["rare"]))
    assert fastpath.has_offers_backdrop(frames["rare"])


def test_grass_is_still_rejected_while_the_arena_is_unknown(grass, offers):
    """What abstaining must not cost.  `offers_emerging` has no titles to lean
    on - it fires before they are drawn - so it checks the heading itself."""
    boxes = [
        b.scaled(grass.shape[1] / 1005, grass.shape[0] / 572)
        for b in (c.box for c in offers["uncommon"].cards)
    ]
    assert not fastpath.offers_emerging(grass, boxes)
    assert fastpath.offers_visible(grass, boxes) is None


# ------------------------------------------------------- the real blue arena
#
# Captured live off the failing session rather than simulated.  Measured on it:
# world H110 (blue) S59 V99, dark 0.014 - against green-arena offers screens at
# V 61-89 and dark 0.08-0.45.


@pytest.fixture(scope="module")
def blue():
    return load_image(config.SOURCES / "offers-blue-arena.png")


def test_the_blue_arena_frame_is_what_the_old_gate_rejected(blue):
    assert fastpath.dark_fraction(blue) < config.OFFERS_MIN_DARK


def test_the_blue_arena_offers_screen_is_seen(blue):
    """The whole bug in one assertion: this frame parses perfectly - three
    named, correctly classified cards - and the bot could not see it."""
    parsed = cards.detect(blue)
    assert parsed is not None
    assert [c.title for c in parsed.cards] == ["Sword", "Frost Walker", "Firestaff"]
    assert [c.rarity for c in parsed.cards] == ["weapon"] * 3

    boxes = [c.box for c in parsed.cards]
    assert fastpath.offers_visible(blue, boxes) is not None
    assert fastpath.offers_emerging(blue, boxes)


def test_blue_arena_cards_are_tinted_by_the_arena(blue):
    """Why hue alone can never gate a card: the cards are translucent, so the
    body picks up the arena.  The same grey weapon card measures H39 on the
    green arena and H110 here - it is *saturation* that identifies it, which is
    what does survive the change."""
    parsed = cards.detect(blue)
    for card in parsed.cards:
        crop = card.box.inset(0.12, 0.10).crop(blue)
        hue, sat, _ = np.median(
            cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3), axis=0
        )
        assert 95 <= hue <= 125, f"{card.title}: expected the blue arena's tint, got H{hue}"
        assert sat < config.WEAPON_MAX_SATURATION


# ---------------------------------------------------------------- the bot side


def _bare_bot():
    """A Bot with only the fields these tests touch - the real one needs a
    window, a mouse and a capturer."""
    bot = bot_mod.Bot.__new__(bot_mod.Bot)
    bot.lit_frames = []
    return bot


def test_no_baseline_until_undimmed_play_is_seen():
    assert _bare_bot().dim_baseline is None


def test_baseline_is_the_brightest_recent_lit_frame(grass):
    bot = _bare_bot()
    for _ in range(3):
        bot.note_lit_frame(grass)
    assert bot.dim_baseline == pytest.approx(fastpath.world_brightness(grass))


def test_baseline_forgets_the_previous_arena(grass):
    """A run can move to a differently lit arena, so the window has to roll -
    a baseline pinned to the first arena is the same bug in a new place."""
    bot = _bare_bot()
    for _ in range(config.DIM_BASELINE_SAMPLES):
        bot.note_lit_frame(grass)
    for _ in range(config.DIM_BASELINE_SAMPLES):
        bot.note_lit_frame(brighten(grass))
    assert len(bot.lit_frames) == config.DIM_BASELINE_SAMPLES
    assert bot.dim_baseline == pytest.approx(fastpath.world_brightness(brighten(grass)))


def test_a_dimmed_frame_cannot_drag_the_baseline_down(grass, frames):
    """The poisoning case, and the reason this is a maximum.

    `classify` labels any frame with a readable wave number IN_WAVE unless
    something else claimed it, so a mid-deal offers screen - dimmed, but not yet
    parseable - gets sampled as if it were lit play.  Averaged in, that pulled
    the baseline down to about the brightness of an offers screen, and every
    subsequent offers screen then read as "not dimmed".
    """
    bot = _bare_bot()
    for _ in range(3):
        bot.note_lit_frame(grass)
    lit = bot.dim_baseline

    for _ in range(5):
        bot.note_lit_frame(frames["rare"])          # a dimmed offers screen
    assert bot.dim_baseline == pytest.approx(lit)
    assert fastpath.has_offers_backdrop(frames["rare"], bot.dim_baseline)


def test_erring_bright_is_the_safe_direction(grass):
    """A flash *does* raise it, deliberately: that only makes the dimming gate
    permissive, and three other checks still have to agree before a click."""
    bot = _bare_bot()
    bot.note_lit_frame(grass)
    bot.note_lit_frame(np.full_like(grass, 255))
    assert bot.dim_baseline > fastpath.world_brightness(grass)


def test_baseline_is_only_sampled_from_undimmed_screens():
    """It must never learn from the overlays it is used to detect, or the gate
    drifts to accept them."""
    from swarmbot.state import GameState

    assert GameState.UPGRADE_OFFERS not in bot_mod.UNDIMMED_STATES
    assert GameState.STATS_PANEL not in bot_mod.UNDIMMED_STATES
    assert GameState.DIED not in bot_mod.UNDIMMED_STATES
    assert GameState.AGAIN not in bot_mod.UNDIMMED_STATES
    assert GameState.IN_WAVE in bot_mod.UNDIMMED_STATES

    source = inspect.getsource(bot_mod.Bot.loop)
    assert "if obs.state in UNDIMMED_STATES:" in source
    assert "self.note_lit_frame(frame)" in source


@pytest.mark.parametrize(
    "method, call",
    [
        ("loop", "fastpath.offers_emerging( frame, boxes, self.dim_baseline"),
        ("_handle_offers", "fastpath.offers_visible( frame, boxes, self.dim_baseline"),
        ("offers_closed_early", "fastpath.has_offers_backdrop(frame, self.dim_baseline"),
    ],
)
def test_every_dimming_check_is_given_the_baseline(method, call):
    """Any one of these left absolute puts the bot back to failing on a
    brighter arena - `offers_closed_early` is the one that actually did."""
    source = " ".join(inspect.getsource(getattr(bot_mod.Bot, method)).split())
    assert call in source
