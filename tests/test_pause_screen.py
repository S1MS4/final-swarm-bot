"""Regressions from the first live run, pinned to real full-resolution frames.

`pause-screen.png` is the real 1920x991 pause screen (not the cropped panel in
base-stats.png) and `gameplay.png` is a live wave.  Both bugs below silently
corrupted upgrades.json rather than crashing, which is exactly why they get
dedicated tests.
"""

from __future__ import annotations

import pytest

from swarmbot import config
from swarmbot.capture import load_image
from swarmbot.vision import fastpath, stats as sv

# Transcribed by hand from sources/pause-screen.png.
EXPECTED = {
    "Max HP": "5,104",
    "HP Regen": "11",
    "Lifesteal": "15.5%",
    "Armor": "20%",
    "Evasion": "0%",
    "Thorns": "0",
    "Damage": "x8.43",
    "Crit Chance": "20%",
    "Crit Damage": "x2",
    "Attack Speed": "140%",
    "Projectile Count": "0",
    "Size": "x1.55",
    "Luck": "40%",
    "Drops Luck": "x16",
    "Movement Speed": "x1",
    "Jump Height": "10.2",
    "Kills": "1,009",
    "Damage Delt": "5,392,822",
    "Damage Taken": "27,960",
    "Revives": "0",
    "Xp Collected": "3,840",
    "Level": "27",
}


@pytest.fixture(scope="module")
def pause():
    return load_image(config.SOURCES / "pause-screen.png")


@pytest.fixture(scope="module")
def gameplay():
    return load_image(config.SOURCES / "gameplay.png")


@pytest.fixture(scope="module")
def parsed(pause):
    return sv.parse_panel(pause, sv.panel_box(pause))


def test_all_twenty_two_rows_parse(parsed):
    """Regression: parsing the full frame let the Loadout grid's "x1"/"x2"
    tiles be paired as stat values, and only 12 of 22 rows survived."""
    assert len(parsed) == 22
    assert set(parsed) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_row_values(parsed, name):
    assert parsed[name].raw.replace(" ", "") == EXPECTED[name]


def test_full_frame_parse_is_worse_than_region_parse(pause, parsed):
    """Documents *why* panel_box exists, so nobody removes it."""
    assert len(sv.parse_panel(pause)) < len(parsed)


def test_no_scrolling_is_needed(parsed):
    """The whole stat block fits on screen: first and last rows are both here."""
    assert "Max HP" in parsed and "Level" in parsed


def test_attribute_rows_exclude_the_counters(parsed):
    attrs = sv.attributes_only(parsed)
    assert len(attrs) == 16
    assert "Kills" not in attrs and "Damage Delt" not in attrs


def test_dim_check_separates_panel_from_gameplay(pause, gameplay):
    """Regression: a fixed 0.30s delay after the gear click snapped live
    gameplay, which then recorded as "no stat changed"."""
    assert fastpath.is_dimmed(pause)
    assert not fastpath.is_dimmed(gameplay)


def test_gameplay_frame_yields_no_usable_stats(gameplay):
    parsed = sv.parse_panel(gameplay, sv.panel_box(gameplay))
    assert len(parsed) < config.MIN_STAT_ROWS


def test_diff_against_the_cropped_reference_panel(parsed, frames):
    """A real before/after pair: base-stats.png is the same character earlier."""
    before = sv.parse_panel(frames["base-stats"])
    deltas = {d.stat: d for d in sv.diff(before, parsed)}

    assert deltas["Max HP"].delta == pytest.approx(5104 - 3520)
    assert deltas["Damage"].kind == "mult"
    assert deltas["Damage"].ratio == pytest.approx(8.43 / 7.08, rel=1e-3)
    # Counters moved enormously between the two, and must not appear.
    assert "Kills" not in deltas and "Level" not in deltas
