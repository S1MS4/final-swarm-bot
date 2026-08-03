"""Structural checks on the bot object itself.

Three methods were silently deleted by careless edits and only surfaced as an
AttributeError mid-run, after the bot had already been farming for minutes.
A crash on a rarely-taken branch - the reroll path fires only on a bad weapon
draw - can hide for a long time, so it is worth catching statically.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from swarmbot.bot import Bot


SOURCE = Path(__file__).resolve().parent.parent / "swarmbot" / "bot.py"


def test_every_method_the_bot_calls_on_itself_exists():
    """Catches methods removed by an edit that missed a call site."""
    called = set(re.findall(r"self\.([a-z_][a-z0-9_]*)\(", SOURCE.read_text(encoding="utf-8")))
    missing = sorted(name for name in called if not hasattr(Bot, name))
    assert not missing, f"Bot calls methods it does not define: {missing}"


@pytest.mark.parametrize(
    "name",
    [
        "grab", "look", "click", "loop",
        "handle_offers", "_handle_offers", "solve_offers_layout",
        "do_reroll",                       # only fires on a bad weapon draw
        "read_stats_panel", "open_panel",
        "record_measurement", "finish_pending_measurement",
        "advance_from_staging", "press_skip_wave",
        "handle_end_of_run", "start_new_run",
        "offers_closed_early", "park_cursor",
    ],
)
def test_required_method_is_present(name):
    assert hasattr(Bot, name), f"{name} is missing"


def test_every_strategy_decision_has_a_handler():
    """A Decision can say "take card N" or "reroll"; both need handling."""
    source = SOURCE.read_text(encoding="utf-8")
    assert "decision.reroll" in source
    assert "self.do_reroll(" in source
    assert "decision.index" in source


def test_bot_module_imports_cleanly():
    import importlib

    import swarmbot.bot as module

    importlib.reload(module)
