from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_shrink_diagnosis_h1.py"
spec = importlib.util.spec_from_file_location("audit_shrink_diagnosis_h1", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_distribution_reports_clipping_mass_and_interpolated_quantiles():
    out = module.distribution([0.0, 0.25, 0.5, 1.0])
    assert out["n"] == 4
    assert out["exact_zero"] == 1
    assert out["exact_one"] == 1
    assert out["median"] == 0.375
    assert out["fraction_at_zero"] == 0.25
    assert out["fraction_at_one"] == 0.25


def test_region_summary_compares_only_aggregate_definedness_and_preserves_source_overlap():
    template = {
        "transport_gap": "0.0", "transport_defined": "1",
        "building_gap": "0.5", "building_defined": "1",
        "poi_gap": "1.0", "poi_defined": "0",
        "fire_gap": "0.2", "fire_defined": "1",
        "ems_gap": "0.3", "ems_defined": "1",
        "schools_gap": "0.4", "schools_defined": "1",
        "cbp_gap": "0.5", "cbp_defined": "1",
        "coverage_gap_score": "0.25",
    }
    rows = [template, {**template, "poi_defined": "1"}]
    source = {"source_inputs": {
        "buildings": {"assigned_to_official_sample": 10, "dataset_membership_counts": {"openstreetmap": 8, "microsoft": 7}},
        "pois": {"assigned_to_official_sample": 20, "dataset_membership_counts": {"meta": 15}},
    }}
    result = module.summarize_region(rows, 0.5, {"sum_L_named_over_sum_L_route": 1.1}, source)
    assert result["definedness_comparison"]["our_any_top_level_component_undefined_rate"] == 0.5
    assert result["definedness_comparison"]["ours_minus_published_rate"] == 0.0
    assert result["components"]["composite"]["defined_rows"] == 2
    assert result["components"]["poi"]["undefined_rows"] == 1
    assert result["feature_source_mix"]["building"]["memberships_overlap"] is True
    assert result["feature_source_mix"]["building"]["membership_count_over_assigned_records"]["openstreetmap"] == 0.8


def test_out_of_range_gap_is_rejected():
    template = {
        "transport_gap": "1.01", "transport_defined": "1",
        "building_gap": "0", "building_defined": "1",
        "poi_gap": "0", "poi_defined": "1",
        "fire_gap": "0", "fire_defined": "1",
        "ems_gap": "0", "ems_defined": "1",
        "schools_gap": "0", "schools_defined": "1",
        "cbp_gap": "0", "cbp_defined": "1",
        "coverage_gap_score": "0",
    }
    source = {"source_inputs": {t: {"assigned_to_official_sample": 1, "dataset_membership_counts": {}}
                                for t in ("buildings", "pois")}}
    try:
        module.summarize_region([template], 0.5, {"sum_L_named_over_sum_L_route": 1.0}, source)
    except ValueError as exc:
        assert "out-of-range" in str(exc)
    else:
        raise AssertionError("out-of-range gap should be rejected")
