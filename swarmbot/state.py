"""Classifying a frame into one game state.

Ordering matters: the checks run most-specific first, because several screens
share text with the ordinary HUD.  One OCR pass feeds every check, so
classifying a frame costs one recognition, not eight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from . import ocr
from .vision import cards, hud
from .vision.geometry import Box


class GameState(str, Enum):
    UNKNOWN = "unknown"
    IN_WAVE = "in-wave"
    UPGRADE_OFFERS = "upgrade-offers"
    STATS_PANEL = "stats-panel"
    AUTO_SKIP = "auto-skip"
    DIED = "died"
    VICTORY = "victory"
    AGAIN = "again"
    PORTAL = "portal"


@dataclass
class Observation:
    """What a single frame said, with the boxes needed to act on it."""

    state: GameState
    wave: int | None = None
    offers: cards.Offers | None = None
    buttons: dict[str, Box] = field(default_factory=dict)
    items: list[ocr.TextItem] = field(default_factory=list)

    def button(self, name: str) -> Box | None:
        return self.buttons.get(name)

    def as_dict(self) -> dict:
        return {
            "state": self.state.value,
            "wave": self.wave,
            "buttons": sorted(self.buttons),
            "cards": [c.as_dict() for c in self.offers.cards] if self.offers else [],
        }


def classify(frame: np.ndarray) -> Observation:
    """Read a frame once and decide what screen it is."""
    items = hud.read_all(frame)
    wave = hud.read_wave(frame, items)
    buttons: dict[str, Box] = {}

    # End-of-run screens first: they are modal and unambiguous.
    if hud.has_you_died(frame, items):
        give_up = hud.find_give_up(frame, items)
        if give_up is not None:
            buttons["give_up"] = give_up
        return Observation(GameState.DIED, wave, None, buttons, items)

    if hud.has_victory(frame, items):
        again = hud.find_again(frame, items)
        if again is not None:
            buttons["again"] = again
        return Observation(GameState.VICTORY, wave, None, buttons, items)

    again = hud.find_again(frame, items)
    if again is not None:
        buttons["again"] = again
        return Observation(GameState.AGAIN, wave, None, buttons, items)

    # The Stats panel is translucent, so accurate row parsing needs the
    # light-text pass - but that pass costs as much as the first one, so it only
    # runs when the ordinary pass has already seen the panel's headers.
    if hud.has_stats_panel(frame, items):
        panel_items = hud.read_all(frame, light_text=True)
        resume = hud.find_resume(frame, panel_items) or hud.find_resume(frame, items)
        if resume is not None:
            buttons["resume"] = resume
        return Observation(GameState.STATS_PANEL, wave, None, buttons, panel_items)

    offers = cards.detect(frame, items)
    if offers is not None:
        return Observation(GameState.UPGRADE_OFFERS, wave, offers, buttons, items)

    auto_skip = hud.find_auto_skip(frame, items)
    if auto_skip is not None:
        buttons["auto_skip"] = auto_skip
    portal = hud.find_enter_portal(frame, items)
    if portal is not None:
        buttons["portal"] = portal

    # A visible wave counter outranks both prompts.  The player fights from the
    # platform, so "Enter Portal" and the Auto Skip countdown stay on screen
    # throughout an ordinary wave - ranking them above IN_WAVE meant every
    # combat frame was read as "waiting at the staging area", and the bot spent
    # the run pressing Q instead of watching for upgrade offers.
    if wave is not None:
        return Observation(GameState.IN_WAVE, wave, None, buttons, items)

    if portal is not None:
        return Observation(GameState.PORTAL, wave, None, buttons, items)

    if auto_skip is not None:
        return Observation(GameState.AUTO_SKIP, wave, None, buttons, items)

    return Observation(GameState.UNKNOWN, wave, None, buttons, items)
