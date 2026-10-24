from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import audit_place_duplicates as audit  # noqa: E402


def _record(x: float, geoid: str, row_id: int):
    return (x, 0.0, 0, geoid, row_id, ("fire",))


def test_name_normalization_is_case_and_whitespace_insensitive():
    assert audit.normalize_primary_name("  North  Star School ") == "north star school"
    assert audit.normalize_primary_name(None) is None
    assert audit.normalize_primary_name("  ") is None


def test_greedy_dedup_includes_exact_50m_but_not_over_50m():
    records = [_record(0, "001", 0), _record(50, "001", 1), _record(100.01, "001", 2)]
    clusters = audit.greedy_name_clusters(records, radius_m=50)
    assert sorted(sorted(cluster) for cluster in clusters) == [[0, 1], [2]]


def test_greedy_dedup_does_not_transitively_merge_long_chains():
    # The middle row is within 50 m of both endpoints, but the endpoints are 80 m apart.
    records = [_record(0, "001", 0), _record(40, "001", 1), _record(80, "001", 2)]
    clusters = audit.greedy_name_clusters(records, radius_m=50)
    assert sorted(sorted(cluster) for cluster in clusters) == [[0, 1], [2]]


def test_greedy_dedup_is_independent_of_input_order():
    records = [_record(49, "001", 1), _record(0, "001", 0), _record(100, "002", 2)]
    clusters = audit.greedy_name_clusters(records, radius_m=50)
    cluster_rows = sorted(sorted(records[index][4] for index in cluster) for cluster in clusters)
    assert cluster_rows == [[0, 1], [2]]


def test_invalid_radius_rejected():
    with pytest.raises(ValueError, match="radius_m"):
        audit.greedy_name_clusters([_record(0, "001", 0)], radius_m=0)


def test_sensitivity_baseline_matches_category_audit_and_keeps_fire_ems_zero_share():
    report = json.loads((REPO / "reports/place-dedup.json").read_text(encoding="utf-8"))
    baseline = json.loads((REPO / "reports/poi-category-audit.json").read_text(encoding="utf-8"))
    for region, values in report["regions"].items():
        for family in ("fire", "ems", "schools"):
            old = baseline["regions"][region]["tract_zero_sensitivity"]["core_v0"][family]
            new = values["by_family"][family]
            assert new["raw_poi_rows"] == old["poi_rows"]
            assert new["raw_zero_tracts"] == old["zero_tracts"]
            if family in {"fire", "ems"}:
                assert new["rows_removed"] == 0
                assert new["deduplicated_zero_share"] == new["raw_zero_share"]
