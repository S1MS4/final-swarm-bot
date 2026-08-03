"""`priority.txt` — the plain-text pick order.

Kept out of `config.py` so it can be retuned between runs without touching
code, which means it is parsed from hand-written text and must tolerate the
kinds of mistakes hand-written text contains.
"""

from __future__ import annotations

import pytest

from swarmbot import config, priorities


def parse(text: str) -> priorities.Priorities:
    return priorities.parse(text)


def test_reads_each_section_in_order():
    p = parse("""
        [weapons]
        Axe
        Bow

        [upgrades]
        Luck
        Damage

        [low]
        Thorns

        [avoid]
        Movement Speed
    """)
    assert p.weapons == ("Axe", "Bow")
    assert p.upgrades == ("Luck", "Damage")
    assert p.low == ("Thorns",)
    assert p.avoid == ("Movement Speed",)


def test_comments_and_blank_lines_are_ignored():
    p = parse("""
        # a heading comment
        [upgrades]

        Luck        # trailing comment
        # Damage    <- commented out, so not picked
        Health
    """)
    assert p.upgrades == ("Luck", "Health")


def test_rarity_lock_is_parsed_off_the_name():
    p = parse("[upgrades]\nMultishot @legendary\nLuck\n")
    assert p.upgrades == ("Multishot", "Luck")
    assert p.rarity_locks == {"Multishot": "legendary"}


def test_unknown_rarity_lock_is_ignored_not_fatal():
    p = parse("[upgrades]\nMultishot @mythic\n")
    assert p.upgrades == ("Multishot",)
    assert "Multishot" not in p.rarity_locks


def test_missing_section_falls_back_to_the_defaults():
    """A typo in one section must not leave the bot with no idea what to pick."""
    p = parse("[upgrades]\nLuck\n")
    assert p.upgrades == ("Luck",)
    assert p.weapons == priorities.DEFAULTS.weapons
    assert p.avoid == priorities.DEFAULTS.avoid


def test_lines_before_any_header_are_ignored():
    p = parse("Luck\nDamage\n[low]\nThorns\n")
    assert p.low == ("Thorns",)
    assert p.upgrades == priorities.DEFAULTS.upgrades


def test_missing_file_uses_defaults(tmp_path):
    assert priorities.load(tmp_path / "nope.txt") is priorities.DEFAULTS


def test_the_shipped_file_parses_and_matches_the_defaults():
    p = priorities.load(config.PRIORITY_FILE)
    assert p.weapons == config.WEAPON_PRIORITY
    assert p.upgrades == config.UPGRADE_PRIORITY
    assert p.low == config.UPGRADE_LOW_PRIORITY
    assert p.avoid == config.UPGRADE_AVOID
    assert p.rarity_locks == config.PRIORITY_ONLY_AT_RARITY


def test_edits_to_the_file_change_what_gets_picked(tmp_path, monkeypatch):
    """The whole point: retune by editing text, not code."""
    from swarmbot.strategy import PriorityStrategy, RunState
    from swarmbot.table import UpgradeTable
    from swarmbot.vision.cards import Card
    from swarmbot.vision.geometry import Box

    path = tmp_path / "priority.txt"
    path.write_text("[upgrades]\nThorns\nLuck\n", encoding="utf-8")
    monkeypatch.setattr(config, "PRIORITY_FILE", path)
    priorities.reset()

    def card(i, title):
        return Card(i, Box(i * 100, 0, 90, 200), "rare", None, None,
                    title, title, False, None, 0, False)

    decision = PriorityStrategy().pick(
        [card(0, "Luck"), card(1, "Thorns")],
        UpgradeTable.load(tmp_path / "u.json"),
        RunState(),
    )
    assert decision.index == 1, "Thorns was listed first, so it should win"
    priorities.reset()


@pytest.fixture(autouse=True)
def _clean_cache():
    priorities.reset()
    yield
    priorities.reset()
