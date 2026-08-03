"""Parsing the Stats panel into comparable numbers.

The panel is a two-column `Label ....... Value` list.  OCR reports the label and
the value as two unrelated boxes, so they are paired by row, and each label is
snapped to the canonical stat vocabulary rather than trusted verbatim.

Values come in four formats, which must stay distinguishable because they
combine differently:

    3,520   count      additive
    5       count      additive
    15%     percent    additive (percentage points)
    x7.08   mult       multiplicative
    10.2    count      additive
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .. import config, ocr
from .geometry import Box, region, whole

VALUE_RE = re.compile(r"^[xX]?\s*-?[\d,]+(?:\.\d+)?\s*%?$")


@dataclass(frozen=True)
class StatValue:
    raw: str
    value: float
    kind: str  # "count" | "percent" | "mult"

    def __sub__(self, other: "StatValue") -> float:
        return self.value - other.value

    def as_dict(self) -> dict:
        return {"raw": self.raw, "value": self.value, "kind": self.kind}


def parse_value(text: str) -> StatValue | None:
    """Turn a value cell into a typed number, or None if it is not one."""
    cleaned = text.strip().replace(" ", "").replace(",", "")
    if not cleaned:
        return None

    kind = "count"
    body = cleaned
    if body[:1] in "xX":
        kind = "mult"
        body = body[1:]
    elif body.endswith("%"):
        kind = "percent"
        body = body[:-1]

    # OCR routinely confuses these in numeric cells.
    body = body.translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5"}))

    try:
        value = float(body)
    except ValueError:
        return None
    return StatValue(raw=text.strip(), value=value, kind=kind)


def looks_like_value(text: str) -> bool:
    return bool(VALUE_RE.match(text.strip().replace(" ", "")))


def panel_box(frame: np.ndarray) -> Box:
    """The Stats column of the pause screen.

    Restricting to it is not an optimisation, it is required for correctness.
    The pause screen also carries a Loadout/Upgrades grid on the right whose
    tiles are labelled "x1"/"x2"; parsing the full frame let those be paired as
    the *value* of a stat row on the same line, and only 12 of 22 rows survived.
    Measured stable for widths 0.26-0.45 of the frame, so 0.38 leaves margin for
    other window sizes without reaching the card area.
    """
    return region(frame, 0.0, 0.03, 0.38, 0.90, "stats-panel")


def parse_panel(frame: np.ndarray, box: Box | None = None) -> dict[str, StatValue]:
    """Read every stat row visible in `frame`.

    Returns canonical stat names (from config.STAT_VOCAB) mapped to typed
    values.  Rows whose label cannot be resolved are dropped rather than
    guessed - a mislabelled row would silently poison the upgrade table.
    """
    box = box or whole(frame, "stats-panel")
    items = ocr.read(frame, box, upscale=1.5, light_text=True)
    return _rows_to_stats(items)


def _rows_to_stats(items: list[ocr.TextItem]) -> dict[str, StatValue]:
    stats: dict[str, StatValue] = {}
    for row in ocr.group_lines(items):
        if len(row) < 2:
            continue
        # Rightmost value-shaped cell is the value; everything left of it is the
        # label, which OCR sometimes splits ("Move ment Speed").
        value_idx = None
        for idx in range(len(row) - 1, 0, -1):
            if looks_like_value(row[idx].text):
                value_idx = idx
                break
        if value_idx is None:
            continue

        value = parse_value(row[value_idx].text)
        if value is None:
            continue

        label_text = " ".join(i.text for i in row[:value_idx])
        name = ocr.match_vocab(label_text.replace(" ", ""), config.STAT_VOCAB)
        if name is None:
            name = ocr.match_vocab(label_text, config.STAT_VOCAB)
        if name is None or name in stats:
            continue
        stats[name] = value
    return stats


def attributes_only(stats: dict[str, StatValue]) -> dict[str, StatValue]:
    """Drop the running counters, which change for reasons unrelated to picks."""
    return {k: v for k, v in stats.items() if k.casefold() not in config.STAT_DENYLIST}


@dataclass(frozen=True)
class StatDelta:
    stat: str
    before: StatValue
    after: StatValue

    @property
    def delta(self) -> float:
        return round(self.after.value - self.before.value, 6)

    @property
    def ratio(self) -> float | None:
        if self.before.value == 0:
            return None
        return round(self.after.value / self.before.value, 6)

    @property
    def kind(self) -> str:
        return self.after.kind

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "delta": self.delta,
            "ratio": self.ratio,
            "before": self.before.raw,
            "after": self.after.raw,
        }


def diff(
    before: dict[str, StatValue],
    after: dict[str, StatValue],
    epsilon: float = 1e-6,
) -> list[StatDelta]:
    """Attribute rows that actually moved, ignoring counters."""
    a = attributes_only(before)
    b = attributes_only(after)
    out: list[StatDelta] = []
    for stat, new in b.items():
        old = a.get(stat)
        if old is None:
            continue
        if abs(new.value - old.value) > epsilon:
            out.append(StatDelta(stat=stat, before=old, after=new))
    return sorted(out, key=lambda d: d.stat)
