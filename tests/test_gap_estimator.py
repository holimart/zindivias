"""No-label invariants for the Bias Bounty component/composite estimator."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import estimate_gaps as estimator  # noqa: E402

FEATURE_KEYS = {
    "transport": "overture_road_km",
    "building": "overture_buildings",
    "poi": "overture_poi_all",
    "fire": "overture_poi_fire",
    "ems": "overture_poi_ems",
    "schools": "overture_poi_schools",
    "cbp": "overture_poi_all",
}
FLAG_KEYS = {name: f"{name}_defined" for name in FEATURE_KEYS}
GAP_KEYS = {name: f"{name}_gap" for name in FEATURE_KEYS}


def _inputs(component: str, defined: bool, case: int) -> tuple[dict, dict]:
    features = {
        "overture_road_km": 0.0,
        "overture_buildings": 1.0,
        "overture_poi_all": 1.0,
        "overture_poi_fire": 0.0,
        "overture_poi_ems": 0.0,
        "overture_poi_schools": 0.0,
        "overture_poi_unclassified": 0.0,
        "overture_unclassified_fire_share": 0.01,
        "overture_unclassified_ems_share": 0.01,
        "overture_unclassified_schools_share": 0.02,
    }
    covariates = {"pop_total": 0.0, "ALAND": 10_000.0,
                  "pop_urban": 0.0, "pct_urban": 0.0}
    observed = 1.0 + case / 15.0
    if component == "transport":
        # Only the expected-road proxy determines this flag.
        covariates["pop_total"] = 10_000.0 if defined else 0.0
        features[FEATURE_KEYS[component]] = case / 15.0
    elif component == "building":
        features[FEATURE_KEYS[component]] = observed if defined else 0.0
    elif component == "poi":
        features[FEATURE_KEYS[component]] = observed if defined else 0.0
        if not defined:
            features["overture_poi_fire"] = 0.0
            features["overture_poi_ems"] = 0.0
            features["overture_poi_schools"] = 0.0
    elif component in ("fire", "ems", "schools"):
        features[FEATURE_KEYS[component]] = observed if defined else 0.0
    elif component == "cbp":
        features[FEATURE_KEYS[component]] = observed if defined else 0.0
        if not defined:
            # Keep the facility half undefined too, independently testing CBP.
            features["overture_poi_fire"] = 0.0
            features["overture_poi_ems"] = 0.0
            features["overture_poi_schools"] = 0.0
    return features, covariates


def _authoritative_fixture() -> tuple[list[str], list[str]]:
    workspace = os.environ.get("COMPETITION_WORKSPACE")
    if not workspace:
        raise RuntimeError("COMPETITION_WORKSPACE is required for exact-ID fixture")
    data_root = Path(workspace) / "defined-flags-inputs"
    expected: list[str] = []
    for region in estimator.REGIONS:
        sample = data_root / f"{region}-sample-submission.csv"
        with sample.open(newline="", encoding="utf-8-sig") as stream:
            expected.extend(row["GEOID"] for row in csv.DictReader(stream))
    output = REPO / "reports/gap-estimates.csv"
    with output.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        assert reader.fieldnames == estimator.OUTPUT_COLUMNS
    actual = [row["GEOID"] for row in rows]
    for row in rows:
        assert all(row[column] != "" for column in estimator.OUTPUT_COLUMNS)
        for column in estimator.OUTPUT_COLUMNS[1:]:
            value = float(row[column])
            assert 0.0 <= value <= 1.0
            if column.endswith("_defined"):
                assert value in (0.0, 1.0)
        defined_scores = [float(row[f"{name}_gap"]) for name in
                          ("transport", "building", "poi")
                          if row[f"{name}_defined"] == "1"]
        assert defined_scores  # production rows must have a composite denominator
        assert float(row["coverage_gap_score"]) == pytest.approx(
            sum(defined_scores) / len(defined_scores), abs=1e-9)
    checks = {"finite_bounded_outputs": True, "null_free": True,
              "columns_match_estimator": True, "composite_matches_defined_mean": True}
    return expected, actual, checks


def run_invariant_cases() -> dict:
    """Run the counted matrix used by pytest and the durable audit report."""
    component_counts: dict[str, dict[str, int]] = {}
    component_passed: dict[str, dict[str, int]] = {}
    for component in FEATURE_KEYS:
        component_counts[component] = {"defined": 16, "undefined": 16}
        component_passed[component] = {"defined": 0, "undefined": 0}
        for defined in (True, False):
            label = "defined" if defined else "undefined"
            for case in range(16):
                features, covariates = _inputs(component, defined, case)
                result = estimator.estimate_one(features, covariates)
                assert result[FLAG_KEYS[component]] == int(defined), (component, defined, case, result)
                for field in estimator.OUTPUT_COLUMNS[1:]:
                    if field.endswith("_defined"):
                        assert result[field] in (0, 1), (component, defined, case, field)
                    else:
                        value = result[field]
                        if field == "coverage_gap_score" and value is None:
                            # No composite is the correct all-undefined result.
                            assert not any(result[f"{name}_defined"] for name in
                                           ("transport", "building", "poi"))
                        else:
                            assert value is not None and 0.0 <= float(value) <= 1.0, (
                                component, defined, case, field, value)
                component_passed[component][label] += 1

    poi_half_cases = 0
    for case in range(16):
        facility = [((case % 11) / 10.0, True), ((case % 7) / 6.0, case % 2 == 0),
                    (((case + 3) % 13) / 12.0, case % 3 == 0)]
        cbp = ((case % 9) / 8.0)
        for mode in ("both", "facility", "cbp", "none"):
            facilities = [(value, mode in ("both", "facility") and defined)
                          for value, defined in facility]
            if mode in ("both", "facility") and not any(flag for _, flag in facilities):
                facilities[0] = (facility[0][0], True)
            cbp_defined = mode in ("both", "cbp")
            actual, defined = estimator.combine_poi_gap(facilities, cbp, int(cbp_defined))
            facility_values = [value for value, is_defined in facilities if is_defined]
            halves = []
            if facility_values:
                halves.append(sum(facility_values) / len(facility_values))
            if cbp_defined:
                halves.append(cbp)
            expected = sum(halves) / len(halves) if halves else None
            assert defined == int(expected is not None)
            assert actual == pytest.approx(expected) if expected is not None else actual is None
            if actual is not None:
                assert 0.0 <= actual <= 1.0
            poi_half_cases += 1

    composite_cases = 0
    for mask in range(8):
        for variant in range(2):
            components = [(((index + 1) * (variant + 1)) / 8.0,
                           bool(mask & (1 << index))) for index in range(3)]
            selected = [value for value, defined in components if defined]
            actual = estimator.composite_mean(components)
            expected = sum(selected) / len(selected) if selected else None
            assert actual == pytest.approx(expected) if expected is not None else actual is None
            if actual is not None:
                assert 0.0 <= actual <= 1.0
            composite_cases += 1

    # Explicit end-to-end all-undefined case: no score is fabricated.
    empty_features, empty_covariates = _inputs("poi", False, 0)
    empty_features["overture_buildings"] = 0.0
    for key in ("overture_poi_all", "overture_poi_fire", "overture_poi_ems",
                "overture_poi_schools", "overture_poi_unclassified"):
        empty_features[key] = 0.0
    empty_covariates.update(pop_total=0.0, ALAND=10_000.0, pop_urban=0.0, pct_urban=0.0)
    all_undefined = estimator.estimate_one(empty_features, empty_covariates)
    assert all_undefined["coverage_gap_score"] is None
    assert not any(all_undefined[f"{name}_defined"] for name in
                   ("transport", "building", "poi"))

    expected_ids, actual_ids, output_checks = _authoritative_fixture()
    estimator.validate_exact_geoid_coverage(actual_ids, expected_ids)
    assert len(expected_ids) == 9379
    coverage_cases = 1
    bad_inputs = {
        "missing": actual_ids[:-1],
        "extra": actual_ids + ["00000000000"],
        "duplicate": actual_ids[:-1] + [actual_ids[-2]],
        "reordered": actual_ids[1:] + actual_ids[:1],
    }
    for bad in bad_inputs.values():
        with pytest.raises(ValueError):
            estimator.validate_exact_geoid_coverage(bad, expected_ids)
        coverage_cases += 1

    case_count = sum(sum(branches.values()) for branches in component_counts.values())
    case_count += poi_half_cases + composite_cases + coverage_cases
    passed_count = (sum(sum(branches.values()) for branches in component_passed.values())
                    + poi_half_cases + composite_cases + coverage_cases)
    return {
        "case_count": case_count,
        "passed_count": passed_count,
        "component_cases": component_counts,
        "component_passed": component_passed,
        "poi_half_cases": {"count": poi_half_cases, "passed": poi_half_cases},
        "composite_cases": {"count": composite_cases, "passed": composite_cases,
                            "all_undefined_returns_none": True},
        "coverage": {"case_count": coverage_cases, "passed": coverage_cases,
                     "authoritative_rows": len(expected_ids), "output_rows": len(actual_ids),
                     "exact_ordered_ids": True, "duplicates": 0,
                     **output_checks},
    }


def test_gap_estimator_invariants():
    result = run_invariant_cases()
    assert result["case_count"] == result["passed_count"]
    assert result["case_count"] >= 16
    assert result["coverage"]["authoritative_rows"] == 9379

