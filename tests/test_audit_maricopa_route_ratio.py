from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from audit_maricopa_route_ratio import (  # noqa: E402
    BAND,
    DROP_REASONS,
    NO_ENTRY_BUCKET,
    OUT_DEFAULT,
    TOLERANCE,
    classify_network,
)

REPORT = json.loads(OUT_DEFAULT.read_text(encoding="utf-8")) if OUT_DEFAULT.is_file() else None

CASES = [
    ("US:I", "interstate", "kept"),
    ("US:I:05", "interstate", "kept"),
    ("us:i", "interstate", "kept"),
    ("US:US", "us", "kept"),
    ("US:US:ALTERNATE", "us", "kept"),
    ("US:US:BYPASS", "us", "kept"),
    ("US:US:HISTORIC", None, "excluded_historic_business_or_scenic"),
    ("US:US:BUSINESS", None, "excluded_historic_business_or_scenic"),
    ("US:I:BUSINESS:LOOP", None, "excluded_historic_business_or_scenic"),
    ("US:NHT", None, "excluded_historic_business_or_scenic"),
    ("US:NFSR:TRINITY:NF", None, "excluded_historic_business_or_scenic"),
    ("US:NFRS:COCONINO:FR", None, "excluded_historic_business_or_scenic"),
    ("US:AZ:SCENIC", None, "excluded_historic_business_or_scenic"),
    ("US:TX:THC:TEXAS FOREST TRAIL", None, "excluded_historic_business_or_scenic"),
    ("US:AB:NSB", None, "excluded_historic_business_or_scenic"),
    ("US:NHS", None, "excluded_non_route_highway_system"),
    ("US:NHS:HPC", None, "excluded_non_route_highway_system"),
    ("US:NHS:STRAHNET", None, "excluded_non_route_highway_system"),
    ("US:AZ", "state", "kept"),
    ("US:CA", "state", "kept"),
    ("US:AZ:LOOP", "state", "kept"),
    ("US:TX:SPUR", "state", "kept"),
    ("US:TX:BELTWAY", "state", "kept"),
    ("US:AZ:FUTURE", "state", "kept"),
    ("US:US:OLD", "us", "kept"),
    ("US:CA:CR", "county", "kept"),
    ("us: ca : cr", None, "unsupported_us_network"),  # internal spaces are not normalized away
    ("US:CA:CR", "county", "kept"),
    ("US:TX:FM", "fm_rm", "kept"),
    ("US:TX:RM", "fm_rm", "kept"),
    ("US:OK:FM", "fm_rm", "kept"),
    ("US:AZ:MARICOPA", None, "unsupported_us_network"),
    ("US:AZ:MOHAVE", None, "unsupported_us_network"),
    ("US:OK:WASHITA", None, "unsupported_us_network"),
    ("US:AZ:SR", "state", "kept"),
    ("US:AZ:89", None, "unsupported_us_network"),
    ("US:BIA", None, "unsupported_us_network"),
    ("US:ZZ", None, "unsupported_us_network"),
    ("US:NM", "state", "kept"),
    ("I-10", None, "non_us_or_malformed"),
    ("TEXAS TRUNK SYSTEM", None, "non_us_or_malformed"),
    ("US:", None, "unsupported_us_network"),  # empty second token: dropped by both implementations
    ("", None, "missing_network"),
    ("   ", None, "missing_network"),
    (None, None, "missing_network"),
    (123, None, "missing_network"),
]


def test_independent_classifier_decisions_are_documented_case_by_case() -> None:
    for network, family, reason in CASES:
        assert classify_network(network) == (family, reason), network


def test_independent_classifier_agrees_with_the_frozen_classifier_on_every_observed_string() -> None:
    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    import estimate_gaps  # noqa: E402  (frozen classifier, used only for the comparison)

    strings = [case[0] for case in CASES]
    if REPORT is not None:
        for region in REPORT["regions"].values():
            strings.extend(region["network_string_agreement"]["decisions"][index]["network_string"]
                           for index in range(len(region["network_string_agreement"]["decisions"])))
    disagreements = []
    for value in strings:
        mine = classify_network(value)
        theirs = estimate_gaps.classify_route_network(value)
        if mine != theirs:  # exact agreement required: family and drop-reason label
            disagreements.append({"network": value, "independent": mine, "frozen": theirs})
    assert not disagreements, disagreements


def test_report_exists_with_frozen_band_and_no_relaxation() -> None:
    assert REPORT is not None, "run scripts/audit_maricopa_route_ratio.py to produce the report"
    assert REPORT["backlog_item"] == "bias-maricopa-route-ratio-diagnostic"
    assert REPORT["preregistration"]["band"] == [0.6, 1.7] == list(BAND)
    assert REPORT["preregistration"]["band_relaxed"] is False
    assert REPORT["preregistration"]["absolute_tolerance"] == TOLERANCE == 1e-9
    assert REPORT["acceptance"]["all_passed"] is True
    for region in REPORT["regions"].values():
        assert region["band"]["preregistered_band"] == [0.6, 1.7]
        assert region["band"]["relaxed"] is False
    for name, value in REPORT["prohibitions"].items():
        assert value is False, name


def test_measure_after_matches_the_prior_audit_to_absolute_tolerance() -> None:
    prior = json.loads((REPO / "reports/transport-route-ref-audit.json").read_text(encoding="utf-8"))
    assert REPORT["preregistration"]["measure_before"]["sha256"]
    for region, data in REPORT["regions"].items():
        reference = prior["regions"][region]
        for row in data["aggregates"]["comparison_to_prior_audit"]:
            assert row["within_1e-9"], (region, row)
        assert data["band"]["ratio"] == pytest.approx(reference["sum_L_named_over_sum_L_route"], abs=1e-9)
        assert data["length_km"]["named_all"]["np_sum_km"] == pytest.approx(
            reference["L_named_km_sum_epsg5070"], abs=1e-9)
        assert data["length_km"]["route"]["np_sum_km"] == pytest.approx(
            reference["L_route_km_sum_epsg5070"], abs=1e-9)
        for value in data["aggregates"]["independent_vs_frozen_cache_max_abs_km"].values():
            assert value <= TOLERANCE


def test_disjoint_classes_explain_the_ratio_and_the_partition_is_additive() -> None:
    for region, data in REPORT["regions"].items():
        A = data["length_km"]["A_named_and_route"]
        B = data["length_km"]["B_named_without_route"]
        C = data["length_km"]["C_route_without_named"]
        assert data["aggregates"]["ratio_identity_abs_difference"] <= 1e-12, region
        assert data["band"]["ratio"] == pytest.approx((A + B) / (A + C), abs=1e-12)
        assert (data["band"]["ratio"] > 1.0) == (B > C), region
        assert data["numerator_only_attribution"]["sole_partition_abs_sum_error"] <= 1e-9, region
        buckets = data["numerator_only_attribution"]["by_sole_drop_reason_additive_km"]
        assert set(buckets) == {*DROP_REASONS, NO_ENTRY_BUCKET, "multiple_drop_reasons"}
        assert all(value >= -1e-12 for value in buckets.values())
        overlapping = data["numerator_only_attribution"]["by_drop_reason_overlapping_km"]
        assert set(overlapping) == set(DROP_REASONS)
        for reason, value in overlapping.items():
            assert buckets[reason] <= value + 1e-9, (region, reason)


def test_maricopa_is_out_of_band_and_marked_unresolved_without_rule_relaxation() -> None:
    maricopa = REPORT["regions"]["maricopa-az"]
    assert maricopa["band"]["pass"] is False
    assert maricopa["band"]["ratio"] > BAND[1]
    assert REPORT["diagnosis"]["maricopa_status"] == "unresolved_out_of_band"
    assert REPORT["diagnosis"]["unexplained_rule_or_implementation_mismatches"] == 0
    assert REPORT["acceptance"]["band_evaluated_and_not_relaxed"] is True
    # the failure is genuine: widening the band would be the only way to pass, and it is not adopted
    assert maricopa["band"]["ratio"] > 1.7
    for name, variant in maricopa["counterfactuals_diagnostic_only"].items():
        assert variant["adopted"] is False, name
    for region in REPORT["regions"].values():
        assert region["network_string_agreement"]["classification_mismatch_count"] == 0
        assert not region["network_string_agreement"]["strings_missing_from_prior_audit"]
        assert not region["network_string_agreement"]["strings_missing_from_independent_scan"]
        for decision in region["network_string_agreement"]["decisions"]:
            assert decision["classification_agrees"] is True
            assert decision["independent_family"] is not None or decision["independent_drop_reason"] in (
                DROP_REASONS)


def test_every_region_documents_membership_dedup_and_a_reasoned_verdict() -> None:
    for region, data in REPORT["regions"].items():
        verdicts = REPORT["measure_after"]["reasoned_pass_fail_per_rule"][region]
        assert set(verdicts) == {"ratio_in_band", "aggregates_reproduced", "membership_rules",
                                 "deduplication", "aggregation_formula", "numerator_only_share"}
        assert verdicts["aggregates_reproduced"].startswith("PASS")
        assert verdicts["membership_rules"].startswith("PASS")
        assert verdicts["deduplication"].startswith("PASS")
        assert verdicts["aggregation_formula"].startswith("PASS")
        expected = "PASS" if data["band"]["pass"] else "FAIL"
        assert verdicts["ratio_in_band"].startswith(expected)
        assert data["scan"]["route_eligible_segment_rows"] > 0
        assert data["scan"]["duplicate_segment_ids"] >= 0
        assert data["scan"]["clip_crs"] == "EPSG:5070"
        assert data["sample"]["score_placeholder_columns_read"] is False
        assert data["sample"]["columns_read"] == ["GEOID"]
        assert data["scan"]["acs"]["columns_read"] == ["GEOID", "geometry"]
    rule_sets = {item["label"]: item["identical"] for item in REPORT["rule_constants"]["sets"]}
    assert all(rule_sets.values()), rule_sets
