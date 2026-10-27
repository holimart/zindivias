from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import shapely

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_s2_facility_mask_evidence", ROOT / "scripts/audit_s2_facility_mask_evidence.py")
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_c2_expected_counts_match_fixed_v0_formulas_and_poisson_bridge():
    counts, urban, source = mod.expected_counts({"pop_total": 1000, "ALAND": 10_000_000,
                                                  "pop_urban": 800, "pct_urban": 0.6})
    assert urban == pytest.approx(0.6)
    assert source == "pct_urban"
    assert counts["fire"] == pytest.approx(0.4533333333333333)
    assert counts["ems"] == pytest.approx(0.445)
    assert counts["schools"] == pytest.approx(0.72)
    p_present, p_zero = mod.poisson_presence(counts["fire"])
    assert p_present == pytest.approx(1 - __import__("math").exp(-counts["fire"]))
    assert p_present + p_zero == pytest.approx(1.0)


def test_s2b_fixed_half_boundary_drops_only_strictly_below_and_only_zero_raw_count():
    assert not mod.retain_facility_type(0, 0.499999, 0)
    assert mod.retain_facility_type(0, 0.5, 1)
    assert mod.retain_facility_type(0, 0.8, 1)
    assert mod.retain_facility_type(1, 0.1, 1)
    with pytest.raises(ValueError, match="defined flag"):
        mod.retain_facility_type(0, 0.8, 0)


def test_shared_boundary_adjacency_excludes_point_only_touch():
    polys = [shapely.box(0, 0, 1, 1), shapely.box(1, 0, 2, 1), shapely.box(2, 1, 3, 2)]
    neighbors = mod.shared_boundary_neighbors(polys)
    assert neighbors == [[1], [0], []]


def test_numeric_summary_empty_and_fixed_quantiles():
    assert mod.numeric_summary([])["n"] == 0
    result = mod.numeric_summary([0.0, 1.0, 2.0, 3.0])
    assert result["median"] == pytest.approx(1.5)
    assert result["p95"] == pytest.approx(2.85)


def test_expected_presence_rejects_invalid_values():
    with pytest.raises(ValueError):
        mod.poisson_presence(-0.1)
    with pytest.raises(ValueError):
        mod.poisson_presence(float("nan"))
