"""Deciding what to do with the three offered cards.

Two strategies live here:

  * `DiscoverFirstStrategy` maximises *coverage* - it takes whatever is not yet
    in the table.  Useful while `upgrades.json` is still being filled in.
  * `PriorityStrategy` maximises *strength* - it takes the best upgrade on
    offer according to a hand-authored ranking, and spends rerolls to get out
    of a bad weapon draw.

Both return a `Decision`, which is either "take card N" or "reroll".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from . import config, priorities
from .table import UpgradeTable
from .vision.cards import Card

# Ranks are "lower is better", in four tiers:
#   0..n         the preferred list, best first
#   NEUTRAL      anything unlisted - preferred over the tiers below, so a new
#                upgrade is tried rather than a known-weak one
#   LOW          explicitly deprioritised, but still worth more than nothing
#   AVOID        taken only when it is the sole option
NEUTRAL_RANK = 1000
LOW_RANK = 1500
AVOID_RANK = 2000


@dataclass
class RunState:
    """Facts about the run in progress."""

    wave: int | None = None
    weapon_count: int = config.STARTING_WEAPONS
    picks: int = 0
    rerolls_left: int = config.MAX_REROLLS
    # Auto Skip is a persistent toggle, not a per-wave prompt.  Its state is
    # verified by whether waves actually advance, so this counts how many times
    # it has been flipped rather than assuming one flip was enough.
    auto_skip_toggles: int = 0
    # How many times Q was needed.  Needing it at all means Auto Skip is off.
    skips_used: int = 0
    # Set once the Auto Skip border has been seen green, so it is left alone.
    auto_skip_ok: bool = False
    weapons_owned: set[str] = field(default_factory=set)

    @property
    def weapons_full(self) -> bool:
        return self.weapon_count >= config.MAX_WEAPONS

    def note_weapon(self, title: str | None) -> None:
        """A new weapon only counts against the cap the first time it is taken."""
        if title and title in self.weapons_owned:
            return
        if title:
            self.weapons_owned.add(title)
        self.weapon_count += 1


@dataclass(frozen=True)
class Decision:
    index: int | None
    reason: str
    reroll: bool = False

    def as_dict(self) -> dict:
        return {"index": self.index, "reason": self.reason, "reroll": self.reroll}


class PickStrategy(Protocol):
    name: str

    def pick(self, cards: list[Card], table: UpgradeTable, run: RunState) -> Decision: ...


# --------------------------------------------------------------------------
# Ranking helpers
# --------------------------------------------------------------------------


def _canonical(title: str | None) -> str:
    return (title or "").strip().casefold()


def weapon_rank(title: str | None) -> int:
    """Position in the preferred weapon list; NEUTRAL if it is not on it."""
    wanted = _canonical(title)
    for rank, name in enumerate(priorities.current().weapons):
        if _canonical(name) == wanted:
            return rank
    return NEUTRAL_RANK


def upgrade_rank(card: Card) -> int:
    """Position in the preferred upgrade list.

    Some upgrades only earn their ranking at a particular rarity - Multishot is
    worth taking at legendary and unremarkable below it - so those fall back to
    neutral elsewhere rather than being taken on the strength of their name.
    """
    wanted = _canonical(card.title)
    table = priorities.current()

    # The preferred list is consulted first, so naming something there always
    # wins.  Otherwise an entry that also appears in a fallback [low] list
    # would be demoted despite having been asked for explicitly.
    for rank, name in enumerate(table.upgrades):
        if _canonical(name) != wanted:
            continue
        required = table.rarity_locks.get(name)
        if required is not None and card.rarity != required:
            break  # ranked, but not at this rarity - fall through
        return rank

    for name in table.avoid:
        if _canonical(name) == wanted:
            return AVOID_RANK

    for name in table.low:
        if _canonical(name) == wanted:
            return LOW_RANK

    return NEUTRAL_RANK


# --------------------------------------------------------------------------
# Strategies
# --------------------------------------------------------------------------


class DiscoverFirstStrategy:
    """Take whatever we have not measured yet; otherwise take the first card.

    The game's own yellow "NEW!" badge is the primary discovery signal - it is
    exact, costs nothing to read, and cannot be thrown off by a misread title.
    upgrades.json is the fallback and the durable record either way.
    """

    name = "discover-first"

    def pick(self, cards: list[Card], table: UpgradeTable, run: RunState) -> Decision:
        if not cards:
            raise ValueError("No cards to pick from")

        eligible = [c for c in cards if not _blocked(c, run)]
        if not eligible:
            return Decision(cards[0].index, "all-cards-blocked; taking first")

        badged = [c for c in eligible if c.is_new]
        if badged:
            return Decision(badged[0].index, f"NEW! badge on {badged[0].key}")

        named = [c for c in eligible if c.title]
        unrecorded = [c for c in named if not table.has(c.title, c.rarity)]
        if unrecorded:
            return Decision(unrecorded[0].index, f"not yet in table: {unrecorded[0].key}")

        card = eligible[0]
        if not named:
            return Decision(card.index, "no NEW! badge; taking first")
        return Decision(card.index, f"all offers already known; taking first ({card.key})")


class PriorityStrategy:
    """Take the strongest thing on offer, reroll out of a bad weapon draw.

    Weapons and stat upgrades are ranked from separate hand-authored lists.
    Rerolls are a scarce per-run resource (three) and are spent only on weapon
    screens, because a weapon is a permanent slot decision while a stat upgrade
    is one of dozens taken per run.

    Ties are broken towards undiscovered cards, so playing to win still fills
    in `upgrades.json` as a side effect.
    """

    name = "priority"

    def pick(self, cards: list[Card], table: UpgradeTable, run: RunState) -> Decision:
        if not cards:
            raise ValueError("No cards to pick from")

        eligible = [c for c in cards if not _blocked(c, run)]
        if not eligible:
            return Decision(cards[0].index, "all-cards-blocked; taking first")

        if all(c.is_weapon for c in eligible):
            return self._pick_weapon(eligible, run)
        return self._pick_upgrade([c for c in eligible if not c.is_weapon], table)

    def _pick_weapon(self, cards: list[Card], run: RunState) -> Decision:
        ranked = sorted(cards, key=lambda c: (weapon_rank(c.title), c.index))
        best = ranked[0]

        if weapon_rank(best.title) < NEUTRAL_RANK:
            return Decision(best.index, f"best weapon on offer: {best.name}")

        # Nothing worth a permanent slot.  This is the only thing rerolls are
        # spent on.
        if run.rerolls_left > 0:
            offered = ", ".join(c.name for c in cards)
            return Decision(
                None,
                f"no preferred weapon ({offered}); reroll {run.rerolls_left} left",
                reroll=True,
            )
        return Decision(best.index, f"no preferred weapon and no rerolls; taking {best.name}")

    def _pick_upgrade(self, cards: list[Card], table: UpgradeTable) -> Decision:
        def sort_key(card: Card):
            rank = upgrade_rank(card)
            # Among equals, prefer something we have never measured - playing
            # well and filling the table are not in conflict.
            undiscovered = 0 if (card.is_new or not table.has(card.title, card.rarity)) else 1
            return (rank, undiscovered, card.index)

        best = sorted(cards, key=sort_key)[0]
        rank = upgrade_rank(best)
        if rank < NEUTRAL_RANK:
            note = f"priority #{rank + 1}"
        elif rank >= AVOID_RANK:
            note = "only avoid-listed options left"
        elif rank >= LOW_RANK:
            note = "only low-priority options left"
        else:
            note = "no ranked option; best available"
        return Decision(best.index, f"{note}: {best.key}")


class FirstCardStrategy:
    """Always take the leftmost card.  A control for debugging the loop."""

    name = "first-card"

    def pick(self, cards: list[Card], table: UpgradeTable, run: RunState) -> Decision:
        return Decision(cards[0].index, "fixed: first card")


def _blocked(card: Card, run: RunState) -> bool:
    """A new weapon past the cap of 5 is wasted; an owned one is fine."""
    if not card.is_weapon:
        return False
    if card.title and card.title in run.weapons_owned:
        return False
    return run.weapons_full


STRATEGIES: dict[str, type] = {
    PriorityStrategy.name: PriorityStrategy,
    DiscoverFirstStrategy.name: DiscoverFirstStrategy,
    FirstCardStrategy.name: FirstCardStrategy,
}


def get(name: str) -> PickStrategy:
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise SystemExit(f"Unknown strategy {name!r}. Choose from: {', '.join(sorted(STRATEGIES))}")
