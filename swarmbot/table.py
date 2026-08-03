"""The upgrade table: `upgrades.json`.

This file is the point of the whole project, and it is meant to be edited by
hand afterwards, so two properties matter more than convenience:

  * **Merge, never clobber.**  A new measurement appends an observation and
    updates the aggregate.  Keys the user added by hand survive a rewrite.
  * **Keep the evidence.**  Every observation retains the raw before/after
    strings, so a bad sample can be identified and deleted rather than being
    silently baked into an average.

Aggregates use the median, not the mean, because a single mis-OCR'd value would
drag a mean permanently off.
"""

from __future__ import annotations

import copy
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .vision.stats import StatDelta

SCHEMA_VERSION = 1
MAX_OBSERVATIONS = 12
# Consecutive empty diffs before an upgrade is accepted as moving no listed stat.
EFFECT_ONLY_SAMPLES = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _median(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return round(statistics.median(clean), 6)


@dataclass
class UpgradeTable:
    path: Path
    data: dict

    # ---------------------------------------------------------------- io

    @classmethod
    def load(cls, path: Path | None = None) -> "UpgradeTable":
        path = Path(path or config.UPGRADES_JSON)
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            data = {}
        data.setdefault("schema_version", SCHEMA_VERSION)
        data.setdefault("upgrades", {})
        return cls(path=path, data=data)

    def save(self) -> None:
        self.data["updated"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temporary file so an interrupted run cannot truncate a
        # table that took hours of play to build.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
        tmp.replace(self.path)

    # ------------------------------------------------------------ queries

    @property
    def upgrades(self) -> dict:
        return self.data["upgrades"]

    def entry(self, title: str) -> dict | None:
        return self.upgrades.get(title)

    def has(self, title: str | None, rarity: str) -> bool:
        """Has this exact upgrade+rarity combination already been recorded?"""
        if not title:
            return False
        entry = self.upgrades.get(title)
        return bool(entry and rarity in entry.get("rarities", {}))

    def has_measurement(self, title: str | None, rarity: str) -> bool:
        """Is this combination already measured well enough to skip re-reading?

        Only a clean or dual-stat sample counts.  A "no-change" observation
        means the read failed, not that the upgrade does nothing, so those are
        retried; otherwise a single bad read would permanently blacklist an
        upgrade from ever being measured.
        """
        if not title:
            return False
        entry = self.upgrades.get(title)
        if not entry:
            return False
        tier = entry.get("rarities", {}).get(rarity)
        if not tier:
            return False
        if entry.get("type") == "weapon":
            return bool(tier.get("acquired"))
        if tier.get("confidence") == "effect-only":
            # Nothing on the Stats panel to measure; stop paying for the cycle.
            return True
        return bool(tier.get("effects")) and tier.get("confidence") in ("ok", "ambiguous")

    def effects(self, title: str, rarity: str) -> dict | None:
        entry = self.upgrades.get(title)
        if not entry:
            return None
        tier = entry.get("rarities", {}).get(rarity)
        return tier.get("effects") if tier else None

    def known_titles(self) -> list[str]:
        return sorted(self.upgrades)

    def coverage(self) -> dict[str, int]:
        """How many rarities are recorded per upgrade - a progress readout."""
        return {t: len(e.get("rarities", {})) for t, e in sorted(self.upgrades.items())}

    def missing(self) -> dict[str, list[str]]:
        """Rarities that have been *offered* but not yet measured.

        Deliberately not "all four rarities minus what we have": some upgrades
        only exist at a single rarity (Multishot has only ever appeared as
        legendary), so assuming a full four-tier spread reports gaps that can
        never be filled and makes coverage look permanently incomplete.
        Only a rarity the game has actually shown us is a real gap.
        """
        out: dict[str, list[str]] = {}
        for title, entry in sorted(self.upgrades.items()):
            if entry.get("type") == "weapon":
                continue
            gaps = [
                rarity
                for rarity, tier in entry.get("rarities", {}).items()
                if rarity != "weapon" and not self.has_measurement(title, rarity)
            ]
            if gaps:
                out[title] = sorted(gaps, key=lambda r: config.RARITY_ORDER.index(r))
        return out

    def observed_rarities(self, title: str) -> list[str]:
        """Rarities the game has actually offered for this upgrade."""
        entry = self.upgrades.get(title)
        if not entry:
            return []
        return sorted(
            (r for r in entry.get("rarities", {}) if r != "weapon"),
            key=lambda r: config.RARITY_ORDER.index(r),
        )

    def progress(self) -> dict:
        """Coverage measured against what has been seen, not what is assumed."""
        stats = {t: e for t, e in self.upgrades.items() if e.get("type") != "weapon"}
        offered = sum(len(self.observed_rarities(t)) for t in stats)
        measured = sum(
            1
            for t, e in stats.items()
            for r in e.get("rarities", {})
            if r != "weapon" and self.has_measurement(t, r)
        )
        return {
            "upgrades": len(stats),
            "weapons": len(self.upgrades) - len(stats),
            "combos_offered": offered,
            "combos_measured": measured,
            "single_rarity": sorted(t for t in stats if len(self.observed_rarities(t)) == 1),
        }

    # ------------------------------------------------------------ writing

    def _tier(self, title: str, rarity: str, kind: str) -> dict:
        entry = self.upgrades.setdefault(title, {})
        entry.setdefault("type", kind)
        entry.setdefault("rarities", {})
        return entry["rarities"].setdefault(rarity, {})

    def record_weapon(self, title: str, wave: int | None = None) -> None:
        """Weapons move no listed stat, so only acquisition is recorded."""
        tier = self._tier(title, "weapon", "weapon")
        tier["acquired"] = True
        tier["effects"] = None
        tier["samples"] = tier.get("samples", 0) + 1
        tier["last_seen"] = _now()
        if wave is not None:
            tier.setdefault("first_wave", wave)

    def record_stat(
        self,
        title: str,
        rarity: str,
        deltas: list[StatDelta],
        arrows: int = 0,
        footer_stat: str | None = None,
        wave: int | None = None,
    ) -> str:
        """Record one measured pick.  Returns the confidence verdict.

        A pick is expected to move exactly one attribute.  Zero changes usually
        means the panel was read before the game applied the upgrade; more than
        one means an unrelated stat drifted.  Both are stored and flagged rather
        than dropped, because the raw snapshots make them fixable by hand.
        """
        tier = self._tier(title, rarity, "stat")
        entry = self.upgrades[title]
        if footer_stat:
            entry["footer_stat"] = footer_stat
        if arrows:
            tier["arrows"] = arrows
        if wave is not None:
            tier.setdefault("first_wave", wave)

        if not deltas:
            confidence = "no-change"
        elif len(deltas) == 1:
            confidence = "ok"
        else:
            confidence = "ambiguous"

        # A footer that disagrees with the measurement is the strongest
        # available signal that the diff got attributed to the wrong pick.
        if confidence == "ok" and footer_stat:
            expected = config.FOOTER_TO_STAT.get(footer_stat, footer_stat)
            if deltas[0].stat != expected:
                confidence = "footer-mismatch"

        observation = {
            "at": _now(),
            "confidence": confidence,
            "changed": {d.stat: d.as_dict() for d in deltas},
        }
        if wave is not None:
            observation["wave"] = wave

        observations = tier.setdefault("observations", [])
        observations.append(observation)
        del observations[:-MAX_OBSERVATIONS]

        # Some upgrades move no listed stat at all - Multishot, Blaze, Bolt and
        # the weapons behave like this.  A single empty diff means the read
        # failed, but several in a row means there is genuinely nothing on the
        # Stats panel to see, and continuing to re-measure them forever wastes
        # a gear/read/resume cycle every time they are offered.
        recent = [o.get("confidence") for o in observations[-EFFECT_ONLY_SAMPLES:]]
        if (
            confidence == "no-change"
            and len(recent) >= EFFECT_ONLY_SAMPLES
            and all(c == "no-change" for c in recent)
        ):
            confidence = "effect-only"

        tier["samples"] = tier.get("samples", 0) + 1
        tier["confidence"] = confidence
        tier["last_seen"] = _now()
        tier["effects"] = _aggregate(observations)
        return confidence

    def note_seen(self, title: str, rarity: str, is_weapon: bool) -> None:
        """Record that a combination was offered, even if it was not taken."""
        tier = self._tier(title, rarity, "weapon" if is_weapon else "stat")
        tier["offered"] = tier.get("offered", 0) + 1

    def snapshot(self) -> dict:
        return copy.deepcopy(self.data)


def _aggregate(observations: list[dict]) -> dict | None:
    """Median delta/ratio per stat across an upgrade tier's observations."""
    by_stat: dict[str, list[dict]] = {}
    for obs in observations:
        if obs.get("confidence") in ("no-change",):
            continue
        for stat, change in obs.get("changed", {}).items():
            by_stat.setdefault(stat, []).append(change)

    if not by_stat:
        return None

    out: dict[str, dict] = {}
    for stat, changes in by_stat.items():
        out[stat] = {
            "kind": changes[-1].get("kind"),
            "delta": _median([c.get("delta") for c in changes]),
            "ratio": _median([c.get("ratio") for c in changes]),
            "samples": len(changes),
        }
    return out
