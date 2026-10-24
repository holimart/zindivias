from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from audit_v2_component_masks import (  # noqa: E402
    ARITH_TOL,
    ArtifactTable,
    ComponentAudit,
    CONFIG,
    at_artifact_precision,
    combine_poi,
    composite_mean,
    exact_defined_mean,
    facility_half,
    presence_probability,
    read_table,
    rederive_building,
    rederive_establishments,
    rederive_family,
    rederive_transport,
)


def strata(population: float = 1000.0, land_m2: float = 2_000_000.0,
           pct_urban: float | None = 0.6, pop_urban: float | None = 600.0) -> dict:
    return {"pop_total": population, "pop_urban": pop_urban,
            "pct_urban": pct_urban, "ALAND": land_m2}


def poi_row(fire: float = 0.0, ems: float = 0.0, schools: float = 0.0,
            unclassified: float = 0.0, all_pois: float = 0.0) -> dict:
    return {"GEOID": "01001", "raw_fire_count": str(fire), "raw_ems_count": str(ems),
            "raw_schools_count": str(schools), "unclassified_category_count": str(unclassified),
            "all_overture_poi_count": str(all_pois)}


def test_transport_defined_only_with_positive_route_length() -> None:
    assert rederive_transport({"GEOID": "a", "overture_route_km": "0",
                               "overture_named_road_km_5070": "0"}) == (0.0, 0)
    gap, defined = rederive_transport({"GEOID": "a", "overture_route_km": "10",
                                       "overture_named_road_km_5070": "4"})
    assert (gap, defined) == (0.6, 1)
    over_named = rederive_transport({"GEOID": "a", "overture_route_km": "10",
                                     "overture_named_road_km_5070": "14"})
    assert over_named == (0.0, 1)  # clipped: over-covered route reference is not a negative gap


def test_building_component_is_the_fixed_prior_or_undefined_placeholder() -> None:
    assert rederive_building({"GEOID": "a", "overture_building_count": "0"}) == (0.0, 0)
    assert rederive_building({"GEOID": "a", "overture_building_count": "3"}) == (0.01, 1)


def test_establishments_half_zero_population_edge_cases() -> None:
    assert rederive_establishments(poi_row(all_pois=0.0), strata(population=0.0)) == (0.0, 0)
    gap, defined = rederive_establishments(poi_row(all_pois=5.0), strata(population=0.0))
    assert (gap, defined) == (0.0, 1)  # positive count at zero population is a clipped zero, not undefined
    gap, defined = rederive_establishments(poi_row(all_pois=24.0), strata(population=100.0))
    assert defined == 1 and gap == pytest.approx(0.0, abs=1e-15)


def test_facility_family_definition_and_zero_count_exact_gap() -> None:
    row = poi_row(fire=0.0, ems=0.0, schools=0.0, unclassified=0.0)
    gap, defined, probability, zero_count = rederive_family("fire", row, strata())
    assert defined == 1 and zero_count is True
    assert gap == pytest.approx(probability, abs=1e-15)
    nonzero = rederive_family("fire", poi_row(fire=4.0), strata())
    assert nonzero[1] == 1 and nonzero[3] is False
    assert nonzero[0] < probability  # more observed Overture counts shrink the expected miss


def test_facility_half_and_poi_combiner_average_defined_only() -> None:
    assert facility_half([(0.2, 0), (0.4, 1)]) == (0.4, 1)
    assert facility_half([(0.2, 0), (0.4, 0)]) == (0.0, 0)
    assert combine_poi(0.2, 1, 0.6, 0) == (0.2, 1)
    assert combine_poi(0.2, 0, 0.6, 1) == (0.6, 1)
    assert combine_poi(0.2, 1, 0.6, 1) == (0.4, 1)
    assert combine_poi(0.2, 0, 0.6, 0) == (0.0, 0)


def test_composite_excludes_undefined_components_in_every_implementation() -> None:
    components = [(0.9, 0), (0.3, 1), (0.5, 1)]
    assert composite_mean(components) == (0.4, 1)
    assert exact_defined_mean(components)[0] == pytest.approx(0.4, abs=1e-16)
    assert composite_mean([(0.9, 0), (0.1, 0), (0.7, 0)]) == (0.0, 0)
    assert exact_defined_mean([(0.9, 0)]) == (None, 0)
    # a placeholder in an undefined component must never move the composite
    polluted = [(0.9, 0), (0.3, 1), (0.5, 1)]
    assert composite_mean(polluted)[0] == composite_mean([(0.0, 0), (0.3, 1), (0.5, 1)])[0]


def test_component_audit_flags_placeholder_leak_and_out_of_range() -> None:
    audit = ComponentAudit("demo", "gap", "defined")
    audit.add("g1", emitted_gap=0.25, emitted_flag=0, rederived_gap=0.0, rederived_flag=0,
              emitted_gap_text="0.25")
    summary = audit.summary()
    assert summary["undefined_rows_with_nonzero_placeholder"] == 1
    audit.add("g2", emitted_gap=1.5, emitted_flag=1, rederived_gap=1.5, rederived_flag=1,
              emitted_gap_text="1.5")
    summary = audit.summary()
    assert summary["emitted_rows_outside_zero_one"] == 1
    assert summary["defined_rows_outside_zero_one"] == 1
    audit.add("g3", emitted_gap=0.4, emitted_flag=1, rederived_gap=0.4, rederived_flag=0,
              emitted_gap_text="0.4")
    assert audit.summary()["flag_mismatch_vs_recomputation"] == 1


def test_component_audit_serialization_floor_accounting() -> None:
    audit = ComponentAudit("demo", "gap", "defined")
    audit.add("exact", emitted_gap=0.123456789012, emitted_flag=1,
              rederived_gap=0.123456789012, rederived_flag=1,
              emitted_gap_text=format(0.123456789012, ".12g"))
    audit.add("near", emitted_gap=0.5, emitted_flag=1, rederived_gap=0.5 + 4e-12,
              rederived_flag=1, emitted_gap_text="0.5")
    audit.add("beyond", emitted_gap=0.5, emitted_flag=1, rederived_gap=0.5 + 3e-9,
              rederived_flag=1, emitted_gap_text="0.5")
    summary = audit.summary()
    assert summary["rederivation_exact_string_match_rows"] == 1
    assert summary["rederivation_last_digit_near_miss_rows"] == 1
    assert summary["rederivation_floor_violation_rows"] == 1
    assert summary["rederivation_within_serialization_floor"] is False
    assert at_artifact_precision(0.0, "0.0") is True


def test_read_table_detects_duplicate_ids_and_missing_columns(tmp_path: Path) -> None:
    duplicate = tmp_path / "dup.csv"
    with duplicate.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["GEOID", "gap"])
        writer.writerow(["1", "0.1"])
        writer.writerow(["1", "0.2"])
    with pytest.raises(ValueError, match="duplicate GEOID"):
        read_table(duplicate, ["GEOID", "gap"])

    good = tmp_path / "good.csv"
    with good.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["GEOID", "gap"])
        writer.writerow(["2", "0.1"])
        writer.writerow(["3", "0.2"])
    table = read_table(good, ["GEOID", "gap"])
    assert isinstance(table, ArtifactTable)
    facts = table.summary(["2", "3"])
    assert facts["missing_official_ids"] == 0 and facts["duplicate_ids"] == 0
    assert facts["row_order_matches_official_order"] is True
    reversed_facts = table.summary(["3", "2"])
    assert reversed_facts["row_order_matches_official_order"] is False
    assert table.summary(["2", "3", "4"])["missing_official_ids"] == 1
    with pytest.raises(ValueError, match="lacks required columns"):
        read_table(good, ["GEOID", "absent_column"])


def test_registered_gates_and_rules_are_conservative() -> None:
    assert ARITH_TOL <= 1e-12
    assert CONFIG["gates"]["max_zero_defined_rows"] == 0
    assert CONFIG["frozen_rules"]["composite"]["components"] == ["transport", "building", "poi"]
    assert CONFIG["frozen_rules"]["composite"]["zero_defined_rule"] is None
    assert CONFIG["prohibitions"]["labels_used"] is False
    assert CONFIG["prohibitions"]["sample_score_placeholders_read"] is False
    assert CONFIG["prohibitions"]["leaderboard_derived_parameters_used"] is False
    assert CONFIG["prohibitions"]["upload_attempted"] is False
    probability = presence_probability("fire", strata(population=0.0, land_m2=1_000_000.0))
    assert 0.0 < probability < 1.0
