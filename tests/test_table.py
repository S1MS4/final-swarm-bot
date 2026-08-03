"""upgrades.json merge semantics and confidence flagging."""

from __future__ import annotations

import json

import pytest

from swarmbot.table import UpgradeTable
from swarmbot.vision.stats import StatDelta, StatValue


def delta(stat: str, before: float, after: float, kind: str = "count") -> StatDelta:
    return StatDelta(
        stat=stat,
        before=StatValue(raw=str(before), value=before, kind=kind),
        after=StatValue(raw=str(after), value=after, kind=kind),
    )


@pytest.fixture
def table(tmp_path):
    return UpgradeTable.load(tmp_path / "upgrades.json")


def test_single_clean_sample_is_ok(table):
    verdict = table.record_stat("Health", "common", [delta("Max HP", 3520, 3872)],
                                arrows=1, footer_stat="Max Health")
    assert verdict == "ok"
    effects = table.effects("Health", "common")
    assert effects["Max HP"]["delta"] == pytest.approx(352.0)
    assert table.has("Health", "common")
    assert not table.has("Health", "rare")


def test_footer_mismatch_is_flagged(table):
    """A diff that contradicts the card's own footer is the clearest sign the
    measurement was attributed to the wrong pick."""
    verdict = table.record_stat("Health", "common", [delta("Armor", 20, 25)],
                                footer_stat="Max Health")
    assert verdict == "footer-mismatch"


def test_footer_alias_is_resolved(table):
    """The card says "Max Health"; the Stats panel says "Max HP"."""
    verdict = table.record_stat("Health", "common", [delta("Max HP", 3520, 3872)],
                                footer_stat="Max Health")
    assert verdict == "ok"


def test_multiple_changes_are_ambiguous_but_still_kept(table):
    verdict = table.record_stat(
        "Damage", "rare",
        [delta("Damage", 7.08, 7.5, "mult"), delta("Armor", 20, 22, "percent")],
    )
    assert verdict == "ambiguous"
    assert set(table.effects("Damage", "rare")) == {"Damage", "Armor"}


def test_no_change_is_flagged_and_contributes_no_effects(table):
    assert table.record_stat("Damage", "epic", []) == "no-change"
    assert table.effects("Damage", "epic") is None


def test_repeat_samples_use_the_median_not_the_mean(table):
    """One mis-OCR'd outlier must not permanently skew the aggregate."""
    for after in (3872, 3870, 99999):
        table.record_stat("Health", "common", [delta("Max HP", 3520, after)])
    assert table.effects("Health", "common")["Max HP"]["delta"] == pytest.approx(352.0)


def test_ratio_is_recorded_for_multiplicative_stats(table):
    table.record_stat("Damage", "common", [delta("Damage", 7.08, 7.44, "mult")])
    effect = table.effects("Damage", "common")["Damage"]
    assert effect["kind"] == "mult"
    assert effect["ratio"] == pytest.approx(7.44 / 7.08, rel=1e-4)


def test_rarities_accumulate_under_one_upgrade(table):
    for rarity in ("common", "rare", "epic"):
        table.record_stat("Health", rarity, [delta("Max HP", 3520, 3872)])
    assert set(table.entry("Health")["rarities"]) == {"common", "rare", "epic"}
    # All three measured, and legendary has never been offered - so there is
    # nothing outstanding.  `missing` reports real gaps, not assumed ones.
    assert "Health" not in table.missing()

    table.note_seen("Health", "legendary", is_weapon=False)
    assert table.missing()["Health"] == ["legendary"]


def test_weapons_record_acquisition_with_no_effects(table):
    table.record_weapon("Spike Ball", wave=3)
    tier = table.entry("Spike Ball")["rarities"]["weapon"]
    assert tier["acquired"] is True
    assert tier["effects"] is None
    assert table.entry("Spike Ball")["type"] == "weapon"


def test_save_and_reload_round_trips(table, tmp_path):
    table.record_stat("Health", "common", [delta("Max HP", 3520, 3872)])
    table.save()
    reloaded = UpgradeTable.load(tmp_path / "upgrades.json")
    assert reloaded.has("Health", "common")
    assert reloaded.effects("Health", "common")["Max HP"]["delta"] == pytest.approx(352.0)


def test_hand_written_keys_survive_a_rewrite(table):
    """The table is meant to be edited by hand, so a rewrite must not clobber."""
    table.record_stat("Health", "common", [delta("Max HP", 3520, 3872)])
    table.entry("Health")["note"] = "verified manually"
    table.entry("Health")["rarities"]["common"]["my_priority"] = 9
    table.save()

    reloaded = UpgradeTable.load(table.path)
    reloaded.record_stat("Health", "common", [delta("Max HP", 3520, 3872)])
    reloaded.save()

    data = json.loads(table.path.read_text(encoding="utf-8"))
    assert data["upgrades"]["Health"]["note"] == "verified manually"
    assert data["upgrades"]["Health"]["rarities"]["common"]["my_priority"] == 9


def test_observations_retain_raw_values_for_manual_review(table):
    table.record_stat("Damage", "common", [delta("Damage", 7.08, 7.44, "mult")])
    obs = table.entry("Damage")["rarities"]["common"]["observations"][-1]
    assert obs["changed"]["Damage"]["before"] == "7.08"
    assert obs["changed"]["Damage"]["after"] == "7.44"


def test_has_measurement_only_after_a_usable_sample(table):
    """Gates the expensive gear/read/resume cycle, so it must be strict."""
    assert not table.has_measurement("Health", "common")

    table.record_stat("Health", "common", [delta("Max HP", 3520, 3872)])
    assert table.has_measurement("Health", "common")
    assert not table.has_measurement("Health", "rare")


def test_a_failed_read_does_not_count_as_measured(table):
    """"no-change" means the panel read failed, not that the upgrade is inert.

    Treating it as measured would blacklist the upgrade forever."""
    table.record_stat("Blaze", "rare", [])
    assert table.entry("Blaze")["rarities"]["rare"]["confidence"] == "no-change"
    assert not table.has_measurement("Blaze", "rare")


def test_dual_stat_upgrades_count_as_measured(table):
    """Real upgrades move two stats (Soul of Swiftness moves both speeds)."""
    table.record_stat(
        "Soul of Swiftness", "epic",
        [delta("Attack Speed", 170, 190), delta("Movement Speed", 2.95, 3.15)],
    )
    assert table.has_measurement("Soul of Swiftness", "epic")


def test_weapons_count_as_measured_once_acquired(table):
    assert not table.has_measurement("Spike Ball", "weapon")
    table.record_weapon("Spike Ball")
    assert table.has_measurement("Spike Ball", "weapon")


def test_has_measurement_handles_missing_title(table):
    assert not table.has_measurement(None, "common")
    assert not table.has_measurement("", "common")


def test_repeated_no_change_becomes_effect_only(table):
    """Multishot, Blaze and Bolt move no listed stat.  One empty diff means the
    read failed, but several in a row means there is nothing to read - and
    retrying forever costs a gear/read/resume cycle every time."""
    assert table.record_stat("Blaze", "rare", []) == "no-change"
    assert not table.has_measurement("Blaze", "rare")

    assert table.record_stat("Blaze", "rare", []) == "effect-only"
    assert table.has_measurement("Blaze", "rare")


def test_a_later_real_measurement_overrides_effect_only(table):
    """If it turns out the reads were failing, a real diff must win."""
    table.record_stat("Blaze", "rare", [])
    table.record_stat("Blaze", "rare", [])
    assert table.has_measurement("Blaze", "rare")

    assert table.record_stat("Blaze", "rare", [delta("Damage", 7.0, 7.2)]) == "ok"
    assert table.effects("Blaze", "rare")["Damage"]["delta"] == pytest.approx(0.2)


def test_missing_only_counts_rarities_the_game_has_offered(table):
    """Some upgrades exist at a single rarity - Multishot has only ever been
    seen as legendary.  Assuming a four-tier spread reports gaps that can never
    be filled and makes coverage look permanently incomplete."""
    table.note_seen("Multishot", "legendary", is_weapon=False)

    assert table.missing()["Multishot"] == ["legendary"]      # offered, unmeasured
    assert table.observed_rarities("Multishot") == ["legendary"]

    table.record_stat("Multishot", "legendary", [delta("Damage", 1.0, 2.0)])
    assert "Multishot" not in table.missing()                  # no phantom gaps


def test_progress_measures_against_what_was_offered(table):
    table.note_seen("Multishot", "legendary", is_weapon=False)
    table.record_stat("Multishot", "legendary", [delta("Damage", 1.0, 2.0)])
    table.note_seen("Health", "common", is_weapon=False)
    table.note_seen("Health", "rare", is_weapon=False)
    table.record_stat("Health", "common", [delta("Max HP", 100, 200)])

    p = table.progress()
    assert p["upgrades"] == 2
    assert p["combos_offered"] == 3        # not 8
    assert p["combos_measured"] == 2
    assert p["single_rarity"] == ["Multishot"]
