"""Reads `priority.txt`, the plain-text pick order.

The lists live in a text file rather than in `config.py` so they can be
retuned without touching code.  The format is deliberately forgiving - section
headers in brackets, one name per line, `#` for comments - because it is meant
to be edited between runs by hand.

If the file is missing or a section is empty, the defaults in `config.py` are
used, so a typo can never leave the bot with no idea what to pick.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

SECTION_RE = re.compile(r"^\[(\w+)\]\s*$")
RARITY_RE = re.compile(r"^(.*?)\s*@(\w+)\s*$")


@dataclass(frozen=True)
class Priorities:
    weapons: tuple[str, ...]
    upgrades: tuple[str, ...]
    low: tuple[str, ...]
    avoid: tuple[str, ...]
    rarity_locks: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        return (
            f"{len(self.weapons)} weapons, {len(self.upgrades)} preferred, "
            f"{len(self.low)} low, {len(self.avoid)} avoided"
        )


DEFAULTS = Priorities(
    weapons=config.WEAPON_PRIORITY,
    upgrades=config.UPGRADE_PRIORITY,
    low=config.UPGRADE_LOW_PRIORITY,
    avoid=config.UPGRADE_AVOID,
    rarity_locks=dict(config.PRIORITY_ONLY_AT_RARITY),
)

_cache: Priorities | None = None


def parse(text: str) -> Priorities:
    """Parse the file's contents, falling back per-section when empty."""
    sections: dict[str, list[str]] = {}
    locks: dict[str, str] = {}
    current: str | None = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        header = SECTION_RE.match(line)
        if header:
            current = header.group(1).lower()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue

        match = RARITY_RE.match(line)
        if match:
            name, rarity = match.group(1).strip(), match.group(2).lower()
            if rarity in config.RARITY_ORDER:
                locks[name] = rarity
            line = name
        sections[current].append(line)

    def pick(name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        got = sections.get(name)
        return tuple(got) if got else fallback

    # The locks only fall back alongside the upgrade list they belong to.
    # Inheriting them on their own would silently re-apply a default
    # restriction to a name the file spelled without one.
    upgrades = pick("upgrades", DEFAULTS.upgrades)
    if not sections.get("upgrades"):
        locks = dict(DEFAULTS.rarity_locks)

    return Priorities(
        weapons=pick("weapons", DEFAULTS.weapons),
        upgrades=upgrades,
        low=pick("low", DEFAULTS.low),
        avoid=pick("avoid", DEFAULTS.avoid),
        rarity_locks=locks,
    )


def load(path: Path | None = None) -> Priorities:
    """Read the file, or return the built-in defaults if it is absent."""
    path = Path(path or config.PRIORITY_FILE)
    if not path.exists():
        return DEFAULTS
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError:
        return DEFAULTS


def current() -> Priorities:
    """Cached lists for this process.  Edits apply on the next run."""
    global _cache
    if _cache is None:
        _cache = load()
    return _cache


def reset() -> None:
    """Drop the cache - used by tests."""
    global _cache
    _cache = None
