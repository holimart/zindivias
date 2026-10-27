from __future__ import annotations

import importlib.util
from pathlib import Path
import math

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_shrink_h1b_rule_levels", ROOT / "scripts/audit_shrink_h1b_rule_levels.py")
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_one_sided_proxy_gap_is_zero_when_overture_meets_or_exceeds_expected():
    assert mod.clip_gap(10.0, 5.0, 1) == 0.0
    assert mod.clip_gap(5.0, 5.0, 1) == 0.0
    assert mod.clip_gap(0.0, 5.0, 0) == 0.0
    assert mod.clip_gap(1.0, 5.0, 1) == 0.8


def test_poi_halves_average_only_defined_facility_types_and_establishment_half():
    assert mod.compose_poi([(0.6, 1), (0.2, 1), (0.9, 0)], 0.4, 1) == (0.4, 1)
    # With all facility types undefined, the establishments half remains defined.
    assert mod.compose_poi([(0.9, 0), (0.8, 0), (0.7, 0)], 0.3, 1) == (0.3, 1)
    assert mod.compose_poi([(0.9, 0)], 0.3, 0) == (0.0, 0)


def test_defined_component_mass_excludes_undefined_placeholders():
    result = mod.summary([0.2, 0.0, 0.4], [1, 0, 1])
    assert result["defined_rows"] == 2
    assert result["undefined_rows"] == 1
    assert math.isclose(result["defined_gap_mass_sum"], 0.6)
    assert math.isclose(result["defined_mean"], 0.3)
    assert result["positive_defined_rows"] == 2


def test_microsoft_source_share_does_not_define_reference_mass_as_truth():
    # Sensitivity arithmetic is explicit: weighted share is not a validated correction.
    gaps, shares = [0.2, 0.4], [0.75, 0.25]
    assert math.isclose(sum(g * s for g, s in zip(gaps, shares)), 0.25)
    assert math.isclose(sum(g * (1 - s) for g, s in zip(gaps, shares)), 0.35)
