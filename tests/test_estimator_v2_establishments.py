from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from audit_establishments_sensitivity import (  # noqa: E402
    DEFAULT_ANALYSIS_CBP_K,
    SENSITIVITY_KS,
    composite_from_v0_parts,
)
from estimator_v2_establishments import (  # noqa: E402
    combine_poi_halves_v2,
    estimate_establishments_component_v2,
    facility_half_v2,
)


def test_establishments_component_uses_explicit_rate_and_clips() -> None:
    assert estimate_establishments_component_v2(1, 100, 0.02) == {
        "establishments_gap": 0.5,
        "establishments_defined": 1,
    }
    assert estimate_establishments_component_v2(0, 100, 0.02) == {
        "establishments_gap": 1.0,
        "establishments_defined": 1,
    }
    assert estimate_establishments_component_v2(5, 100, 0.02) == {
        "establishments_gap": 0.0,
        "establishments_defined": 1,
    }


def test_zero_population_edge_is_explicit_and_bounded() -> None:
    assert estimate_establishments_component_v2(0, 0, 0.024) == {
        "establishments_gap": 0.0,
        "establishments_defined": 0,
    }
    assert estimate_establishments_component_v2(1, 0, 0.024) == {
        "establishments_gap": 0.0,
        "establishments_defined": 1,
    }


@pytest.mark.parametrize(
    "poi,pop,k",
    [(-1, 1, 0.024), (1, -1, 0.024), (0, 1, 0), (0, 1, -0.01), (0, 1, float("nan"))],
)
def test_invalid_establishment_inputs_raise(poi: float, pop: float, k: float) -> None:
    with pytest.raises(ValueError):
        estimate_establishments_component_v2(poi, pop, k)


def test_defined_facilities_and_poi_halves_keep_equal_weighting() -> None:
    facility = facility_half_v2([(0.2, 1), (0.9, 0), (0.4, 1)])
    assert facility == {"facility_gap": pytest.approx(0.3), "facility_defined": 1}
    assert combine_poi_halves_v2(0.3, 1, 0.5, 1) == {
        "poi_gap": pytest.approx(0.4), "poi_defined": 1,
    }
    assert combine_poi_halves_v2(0.3, 1, 0.0, 0) == {
        "poi_gap": 0.3, "poi_defined": 1,
    }
    assert facility_half_v2([(0.5, 0)]) == {"facility_gap": 0.0, "facility_defined": 0}


def test_sensitivity_audit_replaces_only_establishments_half() -> None:
    base = {
        "GEOID": "01001020100",
        "transport_gap": "0.1", "transport_defined": "1",
        "building_gap": "0.2", "building_defined": "1",
        "fire_gap": "0.2", "fire_defined": "1",
        "ems_gap": "0.9", "ems_defined": "0",
        "schools_gap": "0.4", "schools_defined": "1",
        "cbp_gap": "0.8", "cbp_defined": "1",
    }
    result = composite_from_v0_parts(base, poi_all=1, population=100, cbp_k=0.02)
    assert result["facility_gap"] == pytest.approx(0.3)
    assert result["establishments_gap"] == pytest.approx(0.5)
    assert result["poi_gap"] == pytest.approx(0.4)
    assert result["coverage_gap_score"] == pytest.approx((0.1 + 0.2 + 0.4) / 3)


def test_sensitivity_default_and_grid_are_documented() -> None:
    assert DEFAULT_ANALYSIS_CBP_K == 0.024
    assert SENSITIVITY_KS == (0.015, 0.024, 0.04)
