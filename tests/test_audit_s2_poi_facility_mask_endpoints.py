from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_s2_poi_facility_mask_endpoints",
    ROOT / "scripts/audit_s2_poi_facility_mask_endpoints.py",
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _estimates():
    return {
        "fire_gap": 0.2, "fire_defined": 1,
        "ems_gap": 0.8, "ems_defined": 1,
        "schools_gap": 0.6, "schools_defined": 1,
        "cbp_gap": 0.4, "cbp_defined": 1,
        "transport_defined": 1, "building_defined": 1,
    }


def test_frozen_mask_drops_zero_counts_but_endpoint_retains_defined_types():
    counts = {"fire": 0.0, "ems": 2.0, "schools": 0.0}
    frozen_gap, frozen_defined, frozen_included = mod.poi_variant(
        _estimates(), counts, retain_zero=False)
    endpoint_gap, endpoint_defined, endpoint_included = mod.poi_variant(
        _estimates(), counts, retain_zero=True)
    # Frozen facility half=(EMS .8); endpoint facility half=(.2+.8+.6)/3.
    assert frozen_gap == pytest.approx((0.8 + 0.4) / 2)
    assert endpoint_gap == pytest.approx(((0.2 + 0.8 + 0.6) / 3 + 0.4) / 2)
    assert frozen_defined == endpoint_defined == 1
    assert frozen_included == {"fire": False, "ems": True, "schools": False}
    assert endpoint_included == {"fire": True, "ems": True, "schools": True}


def test_undefined_zero_count_facility_is_not_reintroduced():
    estimates = _estimates()
    estimates["fire_defined"] = 0
    estimates["cbp_defined"] = 0
    counts = {"fire": 0.0, "ems": 0.0, "schools": 0.0}
    gap, defined, included = mod.poi_variant(estimates, counts, retain_zero=True)
    assert gap == pytest.approx(0.7)
    assert defined == 1  # EMS and schools remain frozen-defined and are retained.
    assert included == {"fire": False, "ems": True, "schools": True}


def test_retained_zero_mask_changes_only_composite_via_poi_value_and_mask():
    estimates = _estimates()
    counts = {"fire": 0.0, "ems": 2.0, "schools": 0.0}
    frozen_poi, frozen_flag, _ = mod.poi_variant(estimates, counts, retain_zero=False)
    endpoint_poi, endpoint_flag, _ = mod.poi_variant(estimates, counts, retain_zero=True)
    s2 = {"transport_gap": 0.3, "building_gap": 0.5}
    base = mod.composite_score(s2, estimates, frozen_poi, frozen_flag)
    endpoint = mod.composite_score(s2, estimates, endpoint_poi, endpoint_flag)
    assert base == pytest.approx((0.3 + 0.5 + frozen_poi) / 3)
    assert endpoint == pytest.approx((0.3 + 0.5 + endpoint_poi) / 3)
    assert endpoint != base
    assert s2 == {"transport_gap": 0.3, "building_gap": 0.5}


def test_zero_count_retention_can_change_poi_definedness_and_composite_divisor():
    estimates = {
        "fire_gap": 0.7, "fire_defined": 1,
        "ems_gap": 0.0, "ems_defined": 0,
        "schools_gap": 0.0, "schools_defined": 0,
        "cbp_gap": 0.0, "cbp_defined": 0,
        "transport_defined": 1, "building_defined": 1,
    }
    counts = {"fire": 0.0, "ems": 0.0, "schools": 0.0}
    frozen_poi, frozen_flag, _ = mod.poi_variant(estimates, counts, retain_zero=False)
    endpoint_poi, endpoint_flag, _ = mod.poi_variant(estimates, counts, retain_zero=True)
    s2 = {"transport_gap": 0.2, "building_gap": 0.4}
    assert (frozen_poi, frozen_flag) == (0.0, 0)
    assert (endpoint_poi, endpoint_flag) == (0.7, 1)
    assert mod.composite_score(s2, estimates, frozen_poi, frozen_flag) == pytest.approx(0.3)
    assert mod.composite_score(s2, estimates, endpoint_poi, endpoint_flag) == pytest.approx((0.2 + 0.4 + 0.7) / 3)


def test_delta_summary_and_vector_hash_are_deterministic():
    summary = mod.summarize_delta([0.0, 0.02, -0.05, 0.10])
    assert summary["median_absolute_delta"] == pytest.approx(0.035)
    assert summary["p95_absolute_delta"] == pytest.approx(0.0925)
    assert summary["maximum_absolute_delta"] == pytest.approx(0.10)
    assert summary["tract_share_absolute_delta_gt_0_05"] == pytest.approx(0.25)
    ids = ["00000000001", "00000000002"]
    vals = [0.123, 0.456]
    digest = mod.score_vector_hash(ids, vals)
    assert digest == mod.score_vector_hash(ids, vals)
    assert digest != mod.score_vector_hash(ids[::-1], vals)


def test_rejects_misaligned_vector_hash_inputs():
    with pytest.raises(ValueError, match="length mismatch"):
        mod.score_vector_hash(["00000000001"], [])
