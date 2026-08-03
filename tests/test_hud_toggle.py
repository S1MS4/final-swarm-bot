"""Auto Skip is a toggle, not a button.

It is drawn with a green border when enabled, so a blind click is as likely to
switch it off as on - which would leave the boss fight running unattended for
the full poll timeout.  No reference frame of the enabled state exists yet, so
these use synthetic frames to pin the *logic*; the live threshold is calibrated
from the green coverage logged on every boss transition.
"""

from __future__ import annotations

import numpy as np
import pytest

from swarmbot import config
from swarmbot.vision import hud
from swarmbot.vision.geometry import Box


def frame_with_border(colour) -> np.ndarray:
    """A dark frame with a button-sized ring drawn around the text box."""
    frame = np.full((300, 500, 3), 20, dtype=np.uint8)
    if colour is not None:
        frame[110:190, 80:320] = colour          # filled ring area
        frame[130:170, 110:290] = (20, 20, 20)   # hollow it out, leaving a border
    return frame


BUTTON = Box(110, 130, 180, 40, "Auto Skip")
# The real border colour, measured from a live frame: HSV(55, 98, 158).
GREEN = (97, 158, 107)   # BGR
GREY = (90, 90, 90)


def test_green_border_reads_as_enabled():
    frame = frame_with_border(GREEN)
    assert hud.auto_skip_green(frame, BUTTON) > config.AUTO_SKIP_ON_GREEN
    assert hud.auto_skip_is_on(frame, BUTTON)


def test_no_border_reads_as_disabled():
    frame = frame_with_border(None)
    assert hud.auto_skip_green(frame, BUTTON) == pytest.approx(0.0, abs=1e-6)
    assert not hud.auto_skip_is_on(frame, BUTTON)


def test_grey_border_reads_as_disabled():
    """A border that is present but not green must not count as enabled."""
    frame = frame_with_border(GREY)
    assert not hud.auto_skip_is_on(frame, BUTTON)


def test_offscreen_button_is_safe():
    frame = frame_with_border(GREEN)
    assert hud.auto_skip_green(frame, Box(5000, 5000, 10, 10)) == 0.0


# --- real hub frame, captured live -----------------------------------------

@pytest.fixture(scope="module")
def hub():
    from swarmbot.capture import load_image
    from swarmbot import config as cfg
    return load_image(cfg.SOURCES / "hub-portal.png")


def test_hub_frame_detects_both_prompts(hub):
    """Both prompts are found; neither is acted on while a wave is running.

    An earlier version reported this as AUTO_SKIP and blocked for the full boss
    timeout waiting for a fight that was never going to start.
    """
    from swarmbot.state import classify

    obs = classify(hub)
    assert obs.button("portal") is not None
    assert obs.button("auto_skip") is not None


def test_live_auto_skip_border_reads_as_on(hub):
    """Calibration check against the real thing.

    Measured live: 0.000 with the toggle off, 0.032 with it on.  The original
    guess of 0.06 sat above the true "on" value - this test pins the threshold
    to the observation rather than the guess.
    """
    from swarmbot.state import classify

    button = classify(hub).button("auto_skip")
    green = hud.auto_skip_green(hub, button)
    assert green > config.AUTO_SKIP_ON_GREEN
    assert hud.auto_skip_is_on(hub, button)


def test_portal_prompt_does_not_outrank_an_active_wave(hub):
    """The player fights from the platform, so "Enter Portal" and the Auto Skip
    countdown stay on screen throughout an ordinary wave.  Ranking them above
    IN_WAVE meant every combat frame read as "waiting at the staging area" and
    the bot pressed Q all run instead of watching for upgrade offers - waves 29
    to 44 passed with no experience and not a single pick.
    """
    from swarmbot.state import GameState, classify

    obs = classify(hub)
    assert obs.state is GameState.IN_WAVE
    assert obs.button("portal") is not None, "still detected, just not acted on"


@pytest.fixture(scope="module")
def staging():
    """The between-waves staging area, captured from a live stall."""
    from swarmbot.capture import load_image
    from swarmbot import config as cfg
    return load_image(cfg.SOURCES / "wave-staging.png")


def test_staging_frame_with_a_wave_counter_is_in_wave(staging):
    """Waves advance on their own after a short countdown, so this is a normal
    gameplay frame to wait through, not a screen to act on."""
    from swarmbot.state import GameState, classify

    obs = classify(staging)
    assert obs.state is GameState.IN_WAVE
    assert obs.wave is not None


def test_narrow_range_separates_the_border_from_grass(staging, hub):
    """A broad green range matched the grass behind the button and reported a
    disabled toggle as enabled - the false reading that led to the portal being
    clicked.  Measured: border hue 55 / sat 98, grass hue 45 / sat 145.
    """
    from swarmbot.state import classify

    off = classify(staging).button("auto_skip")
    on = classify(hub).button("auto_skip")
    assert off is not None and on is not None

    assert not hud.auto_skip_is_on(staging, off), "grass must not read as enabled"
    assert hud.auto_skip_is_on(hub, on), "the real border must read as enabled"
    assert hud.auto_skip_green(staging, off) < hud.auto_skip_green(hub, on)


def test_the_portal_is_never_clicked():
    """Entering the portal *finishes the run* and stops wave generation.

    It looks like progress - the screen changes and the bot moves on - which is
    what makes it dangerous: it was added twice on the reasoning that a screen
    showing "Enter Portal" must want to be entered.
    """
    import inspect

    from swarmbot import bot as bot_mod

    source = inspect.getsource(bot_mod)
    assert "enter_portal" not in source
    assert "enter-portal" not in source


def test_skip_wave_is_a_recovery_action_not_a_routine_one():
    """The portal is visible from the arena during ordinary play, so this state
    fires constantly.  Pressing Q on every sighting drove a run from wave 1 to
    wave 30 in about a hundred seconds without fighting anything - no farming,
    no upgrades offered.  Q only goes out once the screen has genuinely stalled.
    """
    import inspect

    from swarmbot import bot as bot_mod, config as cfg

    assert cfg.KEY_SKIP_WAVE == "q"
    assert cfg.STAGING_PATIENCE >= 8.0, "must exceed a normal wave transition"

    source = inspect.getsource(bot_mod.Bot.advance_from_staging)
    assert "STAGING_PATIENCE" in source
    # The patience check has to come before the keypress, not after it.
    assert source.index("STAGING_PATIENCE") < source.index("press_skip_wave")


def test_auto_skip_is_enabled_from_stalls_not_from_its_colour():
    """The toggle *is* clicked, but only on behavioural evidence.

    Its green border is unreadable against the grassy staging area - measured,
    the border is hue 55 / sat 98 and the grass is hue 45 / sat 145 - and a
    disabled toggle already read as enabled once, which is what led to the
    destructive portal click.  Needing Q repeatedly is a reliable signal that
    the toggle is off, because when it is on the staging code never runs.
    """
    import inspect

    from swarmbot import bot as bot_mod, config as cfg

    source = inspect.getsource(bot_mod.Bot.advance_from_staging)
    assert "skips_used" in source
    assert "SKIPS_BEFORE_ENABLING_AUTO" in source
    assert cfg.SKIPS_BEFORE_ENABLING_AUTO >= 2, "one slow transition is not evidence"
    assert cfg.STAGING_PATIENCE >= 30.0, (
        "Q skips the wave without fighting it, so it must never fire during "
        "normal play - waves advance on their own within a couple of seconds"
    )

    # The decision must not consult the border colour.
    assert "auto_skip_green" not in source
    assert "auto_skip_is_on" not in source


def test_a_washed_out_button_is_unreadable_not_off(hub, staging):
    """The high-wave overlay covers the button for minutes at a time.

    Under it an *enabled* toggle measures 0.000 green - identical to a disabled
    one - so "off" is not a conclusion the frame supports.  Reading it as off is
    what made the bot click a working toggle off.  Measured on the median
    saturation of the button ring: 24 toggle-on, 40 toggle-off, 160 under the
    wave-121 overlay.
    """
    from swarmbot.state import classify

    for frame in (hub, staging):
        button = classify(frame).button("auto_skip")
        assert hud.auto_skip_readable(frame, button), (
            "an ordinary frame must stay readable, on or off"
        )

    # A saturated overlay over the whole frame, as the boss waves draw.
    washed = np.full_like(hub, 0, dtype=np.uint8)
    washed[:, :, 2] = 200          # heavy red wash, BGR
    button = classify(hub).button("auto_skip")
    assert not hud.auto_skip_readable(washed, button)


def test_an_unreadable_frame_is_never_treated_as_off():
    """Unknown is its own answer - it must not fall through to the click."""
    import inspect

    from swarmbot import bot as bot_mod

    sample = inspect.getsource(bot_mod.Bot.auto_skip_best_green)
    assert "auto_skip_readable" in sample, "unreadable samples must be dropped"
    assert "return best" in sample and "best = None" in sample, (
        "all-unreadable must return None, not 0.0"
    )

    decide = inspect.getsource(bot_mod.Bot.ensure_auto_skip)
    none_check = decide.index("green is None")
    assert none_check < decide.index("auto_skip_off_streak += 1"), (
        "the unknown case must return before the off-streak is incremented"
    )


def test_a_single_off_reading_never_clicks_the_toggle():
    """The toggle was switched off 23 times in one session by its own guard.

    The border is a narrow green and the wave-transition banner tints the whole
    screen, so coverage collapses while the toggle is still on.  Measured over
    40 consecutive frames with the toggle physically untouched: 0.000 to 0.030,
    below the threshold on 16 of them.  Every one of those dips produced a click
    that switched a working toggle off.

    So the click must be gated on consecutive checks disagreeing with "on",
    and the streak must be cleared as soon as one check reads on.
    """
    import inspect

    from swarmbot import bot as bot_mod, config as cfg

    assert cfg.AUTO_SKIP_OFF_STREAK >= 2, "one reading is a banner, not evidence"

    source = inspect.getsource(bot_mod.Bot.ensure_auto_skip)
    assert "AUTO_SKIP_OFF_STREAK" in source
    # The streak gate has to come before the click, not after it.
    assert source.index("AUTO_SKIP_OFF_STREAK") < source.index('"auto-skip")'), (
        "the streak must be checked before the toggle is pressed"
    )
    assert "self.auto_skip_off_streak = 0" in source, "an 'on' reading clears it"


def test_the_toggle_is_read_from_several_frames_not_one():
    """A banner lasts a second or two, so one grab can land entirely inside it."""
    import inspect

    from swarmbot import bot as bot_mod, config as cfg

    assert cfg.AUTO_SKIP_SAMPLES >= 3
    source = inspect.getsource(bot_mod.Bot.auto_skip_best_green)
    assert "max(" in source, "the strongest sample wins - suppression is one-way"

    decide = inspect.getsource(bot_mod.Bot.ensure_auto_skip)
    assert "auto_skip_best_green" in decide
    assert "hud.auto_skip_is_on(frame" not in decide, "no single-frame verdicts"


def test_enabling_the_toggle_re_parks_the_cursor():
    """The click leaves the cursor on the toggle, and the animation-cancel press
    fires wherever the cursor is - so the next offers screen pressed it straight
    back off.  Same fault safe_side_point fixed, reached by another route.
    """
    import inspect

    from swarmbot import bot as bot_mod

    source = inspect.getsource(bot_mod.Bot.ensure_auto_skip)
    click = source.index('"auto-skip")')
    assert "self.park_cursor()" in source[click:], (
        "the cursor must leave the toggle before the next animation-cancel press"
    )


def test_skip_press_does_not_land_on_the_auto_skip_toggle(staging, hub):
    """The animation-cancel press fires on every offers screen, so anything it
    overlaps gets toggled repeatedly.

    The old bottom-left point measured (57, 821) against a button spanning
    x=20..156, y=827..866 - inside its x-range, six pixels above its top edge -
    which quietly switched Auto Skip off and on again with every pick.
    """
    from swarmbot.state import classify
    from swarmbot.vision import cards

    for frame in (staging, hub):
        button = classify(frame).button("auto_skip")
        assert button is not None
        px, py = cards.safe_side_point(frame)
        clear_x = px < button.x - 100 or px > button.x2 + 100
        clear_y = py < button.y - 100 or py > button.y2 + 100
        assert clear_x or clear_y, f"press ({px},{py}) is too close to {button}"
