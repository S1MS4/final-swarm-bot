"""The priority picker: ranked upgrades, ranked weapons, rerolls for weapons only."""

from __future__ import annotations

import pytest

from swarmbot import config
from swarmbot.strategy import PriorityStrategy, RunState
from swarmbot.table import UpgradeTable
from swarmbot.vision.cards import Card
from swarmbot.vision.geometry import Box


def card(index, title, rarity="rare", weapon=False, is_new=False) -> Card:
    return Card(
        index=index, box=Box(index * 100, 0, 90, 200, f"card{index}"),
        rarity="weapon" if weapon else rarity,
        rarity_by_colour=None, rarity_by_chip=None,
        title=title, raw_title=title, is_new=is_new,
        footer_stat=None, arrows=0, is_weapon=weapon,
    )


@pytest.fixture
def table(tmp_path):
    return UpgradeTable.load(tmp_path / "upgrades.json")


@pytest.fixture
def s():
    return PriorityStrategy()


# --- stat upgrades ----------------------------------------------------------

def test_takes_the_highest_ranked_upgrade(s, table):
    cards = [card(0, "Damage"), card(1, "Luck"), card(2, "Health")]
    assert s.pick(cards, table, RunState()).index == 1        # Luck outranks both


def test_ranking_order_is_respected(s, table):
    cards = [card(0, "Attack Speed"), card(1, "Projectile Count"), card(2, "Damage")]
    assert s.pick(cards, table, RunState()).index == 1


def test_movement_speed_is_the_last_resort(s, table):
    cards = [card(0, "Movement Speed"), card(1, "Some Unknown Thing")]
    assert s.pick(cards, table, RunState()).index == 1, "unknown beats avoid-listed"


def test_movement_speed_taken_only_when_nothing_else(s, table):
    cards = [card(0, "Movement Speed")]
    assert s.pick(cards, table, RunState()).index == 0


def test_multishot_only_ranks_at_legendary(s, table):
    """Worth taking at legendary, unremarkable below it."""
    legendary = [card(0, "Damage"), card(1, "Multishot", rarity="legendary")]
    assert s.pick(legendary, table, RunState()).index == 1

    epic = [card(0, "Damage"), card(1, "Multishot", rarity="epic")]
    assert s.pick(epic, table, RunState()).index == 0, "Damage is ranked, Multishot is not"


def test_ties_prefer_undiscovered(s, table):
    """Playing well and filling the table are not in conflict."""
    table.record_stat("Thorns", "rare", [])
    a, b = card(0, "Thorns"), card(1, "Freeze")
    assert s.pick([a, b], table, RunState()).index == 1


# --- weapons ----------------------------------------------------------------

def test_takes_the_best_ranked_weapon(s, table):
    cards = [card(0, "Axe", weapon=True), card(1, "Ban Hammer", weapon=True),
             card(2, "Missile", weapon=True)]
    assert s.pick(cards, table, RunState()).index == 1


def test_rerolls_when_no_preferred_weapon(s, table):
    cards = [card(i, n, weapon=True) for i, n in enumerate(["Bow", "Sword", "Daggers"])]
    decision = s.pick(cards, table, RunState())
    assert decision.reroll
    assert decision.index is None


def test_does_not_reroll_when_a_preferred_weapon_is_present(s, table):
    cards = [card(0, "Bow", weapon=True), card(1, "Ninja Star", weapon=True)]
    decision = s.pick(cards, table, RunState())
    assert not decision.reroll
    assert decision.index == 1


def test_takes_the_least_bad_weapon_once_rerolls_are_gone(s, table):
    cards = [card(i, n, weapon=True) for i, n in enumerate(["Bow", "Sword"])]
    decision = s.pick(cards, table, RunState(rerolls_left=0))
    assert not decision.reroll
    assert decision.index == 0


def test_rerolls_are_never_spent_on_stat_upgrades(s, table):
    """Three per run: a weapon is a permanent slot, a stat upgrade is one of
    dozens taken per run."""
    cards = [card(0, "Movement Speed"), card(1, "Movement Speed")]
    decision = s.pick(cards, table, RunState(rerolls_left=3))
    assert not decision.reroll


def test_weapon_cap_still_blocks_new_weapons(s, table):
    run = RunState(weapon_count=config.MAX_WEAPONS)
    cards = [card(0, "Ban Hammer", weapon=True), card(1, "Luck")]
    assert s.pick(cards, table, run).index == 1


def test_mixed_offer_prefers_the_ranked_upgrade(s, table):
    cards = [card(0, "Bow", weapon=True), card(1, "Luck")]
    assert s.pick(cards, table, RunState()).index == 1


# --- screens that deal fewer than three cards -------------------------------

def test_single_movement_speed_card_is_taken(s, table):
    """Avoiding Movement Speed means ranking it last, not refusing it.

    Late in a run the screen sometimes deals a single card.  If that card is
    Movement Speed it still has to be taken - there is nothing else, and not
    picking leaves the game stuck on the offers screen forever.
    """
    decision = s.pick([card(0, "Movement Speed")], table, RunState())
    assert decision.index == 0
    assert not decision.reroll


def test_two_cards_still_ranked_normally(s, table):
    assert s.pick([card(0, "Movement Speed"), card(1, "Luck")], table, RunState()).index == 1


def test_single_unranked_card_is_taken(s, table):
    assert s.pick([card(0, "Some New Upgrade")], table, RunState()).index == 0


def test_single_weapon_off_list_still_rerolls(s, table):
    decision = s.pick([card(0, "Sword", weapon=True)], table, RunState())
    assert decision.reroll


def test_single_weapon_off_list_taken_without_rerolls(s, table):
    decision = s.pick([card(0, "Sword", weapon=True)], table, RunState(rerolls_left=0))
    assert decision.index == 0


# --- the low-priority tier --------------------------------------------------

@pytest.mark.parametrize("name", ["Piercing", "Freeze", "Ricochet", "Thorns"])
def test_low_priority_loses_to_anything_unlisted(s, table, name):
    """Deprioritised, but still ahead of the avoid tier."""
    decision = s.pick([card(0, name), card(1, "Some Unknown Upgrade")], table, RunState())
    assert decision.index == 1


@pytest.mark.parametrize("name", ["Piercing", "Freeze", "Ricochet", "Thorns"])
def test_low_priority_still_beats_movement_speed(s, table, name):
    decision = s.pick([card(0, "Movement Speed"), card(1, name)], table, RunState())
    assert decision.index == 1


@pytest.mark.parametrize("name", ["Piercing", "Freeze", "Ricochet", "Thorns"])
def test_low_priority_loses_to_a_ranked_upgrade(s, table, name):
    decision = s.pick([card(0, name), card(1, "Luck")], table, RunState())
    assert decision.index == 1


def test_a_lone_low_priority_card_is_still_taken(s, table):
    assert s.pick([card(0, "Thorns")], table, RunState()).index == 0
