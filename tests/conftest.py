from __future__ import annotations

import pytest

from swarmbot import config
from swarmbot.capture import load_image


@pytest.fixture(scope="session")
def frames():
    """All reference frames, loaded once - OCR is the slow part, not IO."""
    names = ["base-stats", "weapon", "uncommon", "rare", "epic", "legendary"]
    return {n: load_image(config.SOURCES / f"{n}.png") for n in names}


@pytest.fixture(scope="session")
def offers(frames):
    """Parsed offers screens, computed once and shared across tests."""
    from swarmbot.vision import cards

    return {n: cards.detect(frames[n]) for n in ("weapon", "uncommon", "rare", "epic", "legendary")}
