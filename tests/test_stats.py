"""The Stats panel parser, pinned to the known contents of base-stats.png."""

from __future__ import annotations

import pytest

from swarmbot.vision import stats

# Every row visible in sources/base-stats.png, transcribed by hand.
EXPECTED = {
    "Max HP": ("count", 3520.0),
    "HP Regen": ("count", 5.0),
    "Lifesteal": ("percent", 15.0),
    "Armor": ("percent", 20.0),
    "Evasion": ("percent", 0.0),
    "Thorns": ("count", 0.0),
    "Damage": ("mult", 7.08),
    "Crit Chance": ("percent", 18.0),
    "Crit Damage": ("mult", 2.0),
    "Attack Speed": ("percent", 135.0),
    "Projectile Count": ("count", 0.0),
    "Size": ("mult", 1.0),
    "Luck": ("percent", 20.0),
    "Drops Luck": ("mult", 16.0),
    "Movement Speed": ("mult", 1.0),
    "Jump Height": ("count", 10.2),
    "Kills": ("count", 0.0),
    "Damage Delt": ("count", 0.0),
    "Damage Taken": ("count", 0.0),
    "Revives": ("count", 0.0),
    "Xp Collected": ("count", 0.0),
    "Level": ("count", 1.0),
}


@pytest.fixture(scope="module")
def parsed(frames):
    return stats.parse_panel(frames["base-stats"])


def test_every_row_is_found(parsed):
    assert set(parsed) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_row_value_and_kind(parsed, name):
    kind, value = EXPECTED[name]
    assert parsed[name].kind == kind
    assert parsed[name].value == pytest.approx(value)


def test_counters_are_excluded_from_attributes(parsed):
    attrs = stats.attributes_only(parsed)
    assert "Kills" not in attrs
    assert "Level" not in attrs
    assert "Xp Collected" not in attrs
    assert "Damage" in attrs
    assert len(attrs) == 16


@pytest.mark.parametrize(
    "text,kind,value",
    [
        ("3,520", "count", 3520.0),
        ("15%", "percent", 15.0),
        ("x7.08", "mult", 7.08),
        ("10.2", "count", 10.2),
        ("X1", "mult", 1.0),
        ("0", "count", 0.0),
    ],
)
def test_parse_value_formats(text, kind, value):
    parsed_value = stats.parse_value(text)
    assert parsed_value is not None
    assert parsed_value.kind == kind
    assert parsed_value.value == pytest.approx(value)


@pytest.mark.parametrize("text", ["Max HP", "", "Attack Speed", "--"])
def test_parse_value_rejects_labels(text):
    assert stats.parse_value(text) is None or not stats.looks_like_value(text)


def test_diff_reports_only_changed_attributes(parsed):
    before = parsed
    after = dict(parsed)
    after["Damage"] = stats.StatValue(raw="x7.50", value=7.50, kind="mult")
    after["Kills"] = stats.StatValue(raw="99", value=99.0, kind="count")

    deltas = stats.diff(before, after)

    assert [d.stat for d in deltas] == ["Damage"]  # Kills is a counter, ignored
    assert deltas[0].delta == pytest.approx(0.42)
    assert deltas[0].ratio == pytest.approx(7.50 / 7.08)


def test_diff_of_identical_snapshots_is_empty(parsed):
    assert stats.diff(parsed, dict(parsed)) == []
