"""Card-picking rules, including the 5-weapon cap."""

from __future__ import annotations

import pytest

from swarmbot.strategy import DiscoverFirstStrategy, RunState
from swarmbot.table import UpgradeTable
from swarmbot.vision.cards import Card
from swarmbot.vision.geometry import Box
from swarmbot.vision.stats import StatDelta, StatValue


def card(index, title, rarity, is_new=False, weapon=False) -> Card:
    return Card(
        index=index,
        box=Box(index * 100, 0, 90, 200, f"card{index}"),
        rarity=rarity,
        rarity_by_colour=rarity,
        rarity_by_chip=rarity,
        title=title,
        raw_title=title,
        is_new=is_new,
        footer_stat=None,
        arrows=0,
        is_weapon=weapon,
    )


@pytest.fixture
def table(tmp_path):
    return UpgradeTable.load(tmp_path / "upgrades.json")


@pytest.fixture
def strategy():
    return DiscoverFirstStrategy()


def test_prefers_the_new_badge(strategy, table):
    cards = [card(0, "Damage", "common"), card(1, "Luck", "epic", is_new=True),
             card(2, "Size", "rare")]
    assert strategy.pick(cards, table, RunState()).index == 1


def test_takes_the_leftmost_when_several_are_badged(strategy, table):
    cards = [card(0, "Damage", "common"), card(1, "Luck", "epic", is_new=True),
             card(2, "Size", "rare", is_new=True)]
    assert strategy.pick(cards, table, RunState()).index == 1


def test_falls_back_to_the_table_when_no_badge_is_present(strategy, table):
    """Matches sources/epic.png, where no card carries a NEW! badge."""
    table.record_stat("Damage", "common", [_d("Damage", 7.0, 7.2)])
    cards = [card(0, "Damage", "common"), card(1, "Luck", "epic"), card(2, "Size", "rare")]
    assert strategy.pick(cards, table, RunState()).index == 1


def test_takes_the_first_card_when_everything_is_known(strategy, table):
    for title, rarity in (("Damage", "common"), ("Luck", "epic"), ("Size", "rare")):
        table.record_stat(title, rarity, [_d(title, 1.0, 2.0)])
    cards = [card(0, "Damage", "common"), card(1, "Luck", "epic"), card(2, "Size", "rare")]
    decision = strategy.pick(cards, table, RunState())
    assert decision.index == 0
    assert "already known" in decision.reason


def test_a_new_weapon_is_skipped_once_five_are_owned(strategy, table):
    run = RunState(weapon_count=5)
    cards = [card(0, "Bow", "weapon", is_new=True, weapon=True),
             card(1, "Damage", "common", is_new=True)]
    assert strategy.pick(cards, table, run).index == 1


def test_an_already_owned_weapon_is_still_selectable_at_the_cap(strategy, table):
    """Upgrading a weapon you already hold does not consume a new slot."""
    run = RunState(weapon_count=5, weapons_owned={"Bow"})
    cards = [card(0, "Bow", "weapon", is_new=True, weapon=True),
             card(1, "Damage", "common")]
    assert strategy.pick(cards, table, run).index == 0


def test_weapons_are_taken_freely_below_the_cap(strategy, table):
    run = RunState(weapon_count=1)
    cards = [card(0, "Bow", "weapon", is_new=True, weapon=True), card(1, "Damage", "common")]
    assert strategy.pick(cards, table, run).index == 0


def test_all_blocked_still_returns_a_choice(strategy, table):
    """Stalling the run is worse than a wasted pick."""
    run = RunState(weapon_count=5)
    cards = [card(i, f"W{i}", "weapon", is_new=True, weapon=True) for i in range(3)]
    decision = strategy.pick(cards, table, run)
    assert decision.index == 0
    assert "blocked" in decision.reason


def test_duplicate_weapon_does_not_double_count_the_cap():
    run = RunState(weapon_count=1)
    run.note_weapon("Bow")
    run.note_weapon("Bow")
    assert run.weapon_count == 2


def test_weapons_full_reflects_the_cap():
    assert not RunState(weapon_count=4).weapons_full
    assert RunState(weapon_count=5).weapons_full


def _d(stat, before, after):
    return StatDelta(
        stat=stat,
        before=StatValue(raw=str(before), value=before, kind="count"),
        after=StatValue(raw=str(after), value=after, kind="count"),
    )


def test_colour_only_cards_do_not_claim_to_be_undiscovered(strategy, table):
    """Fast picks decide from colour, so titles are None.

    `table.has(None, ...)` is always False, which would dress "we have no idea"
    up as "this is undiscovered" in the log.
    """
    cards = [card(i, None, "common") for i in range(3)]
    decision = strategy.pick(cards, table, RunState())
    assert decision.index == 0
    assert "not yet in table" not in decision.reason
    assert "no NEW! badge" in decision.reason
