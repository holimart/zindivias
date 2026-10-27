from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_s2_building_overlap_sensitivity",
    ROOT / "scripts/audit_s2_building_overlap_sensitivity.py",
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_share_offsets_are_clipped_and_only_change_building_component():
    components = {"transport_gap": 0.2, "building_gap": 0.3, "poi_gap": 0.4}
    defined = {"transport": 1, "building": 1, "poi": 1}
    # Frozen base is C2 building 0.5 * (1 - 0.4) = 0.3.
    base = mod.scenario_score(0.5, 0.4, 0.0, components, defined)
    assert base == pytest.approx(0.3)
    low_clipped = mod.scenario_score(0.5, 0.05, -0.10, components, defined)
    high_clipped = mod.scenario_score(0.5, 0.95, 0.10, components, defined)
    assert low_clipped == pytest.approx((0.2 + 0.5 + 0.4) / 3)
    assert high_clipped == pytest.approx((0.2 + 0.0 + 0.4) / 3)
    # All non-building values remain frozen.
    assert components["transport_gap"] == 0.2 and components["poi_gap"] == 0.4


def test_missing_share_preserves_frozen_c2_building_value_for_every_offset():
    components = {"transport_gap": 0.2, "building_gap": 0.0, "poi_gap": 0.4}
    defined = {"transport": 1, "building": 1, "poi": 1}
    expected = (0.2 + 0.5 + 0.4) / 3
    for offset in mod.OFFSETS:
        assert mod.scenario_score(0.5, None, offset, components, defined) == pytest.approx(expected)


def test_undefined_building_does_not_change_other_component_mean():
    components = {"transport_gap": 0.2, "building_gap": 0.0, "poi_gap": 0.4}
    defined = {"transport": 1, "building": 0, "poi": 1}
    for offset in mod.OFFSETS:
        assert mod.scenario_score(0.5, 0.5, offset, components, defined) == pytest.approx(0.3)


def test_poi_defined_mask_matches_retained_nonzero_facility_or_cpb_rule():
    mask = {"fire_defined": 1, "ems_defined": 0, "schools_defined": 1, "cbp_defined": 0}
    assert mod.derive_poi_defined(mask, {"poi_fire": 0.0, "poi_ems": 2.0, "poi_schools": 0.0}) == 0
    assert mod.derive_poi_defined(mask, {"poi_fire": 1.0, "poi_ems": 0.0, "poi_schools": 0.0}) == 1
    mask["cbp_defined"] = 1
    assert mod.derive_poi_defined(mask, {"poi_fire": 0.0, "poi_ems": 0.0, "poi_schools": 0.0}) == 1


def test_quantiles_and_threshold_fraction_are_deterministic_and_strict():
    deltas = [0.0, 0.02, 0.05, 0.10]
    summary = mod.summarize_deltas(deltas)
    assert summary["median_abs_delta"] == pytest.approx(0.035)
    assert summary["p95_abs_delta"] == pytest.approx(0.0925)
    assert summary["max_abs_delta"] == pytest.approx(0.10)
    assert summary["share_abs_delta_gt_0_05"] == pytest.approx(0.25)
    document = {"summary": summary, "offsets": list(mod.OFFSETS)}
    first = (json.dumps(document, sort_keys=True, allow_nan=False) + "\n").encode()
    second = (json.dumps(document, sort_keys=True, allow_nan=False) + "\n").encode()
    assert first == second


def test_rejects_nonfinite_delta_and_invalid_scores():
    with pytest.raises(ValueError, match="finite"):
        mod.scenario_score(0.5, 0.5, float("nan"), {}, {})
    with pytest.raises(ValueError, match=r"finite \[0,1\]"):
        mod.scenario_score(0.5, 0.5, 0.0,
                           {"transport_gap": 2.0, "building_gap": 0.0, "poi_gap": 0.0},
                           {"transport": 1, "building": 0, "poi": 0})
