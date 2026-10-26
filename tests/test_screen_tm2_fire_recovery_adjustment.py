from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_tm2_fire_recovery_adjustment import (  # noqa: E402
    output_table,
    parquet_bytes,
    recovery_adjustment,
)


def test_formula_applies_nonnegative_growth_clipped_to_unit_interval():
    gap = np.array([0.8, 0.8, 0.8, 0.8, 0.8])
    growth = np.array([-0.5, 0.0, 0.25, 1.0, 206.0])
    adjusted, defined, capped = recovery_adjustment(gap, np.ones(5, bool),
                                                    growth, np.ones(5, bool))
    assert defined.tolist() == [True] * 5
    assert capped.tolist() == [0.0, 0.0, 0.25, 1.0, 1.0]
    assert adjusted.tolist() == pytest.approx([0.8, 0.8, 0.6, 0.0, 0.0])


def test_adjustment_is_monotone_nonincreasing_in_growth_and_never_exceeds_tm2_gap():
    growth = np.array([0.0, 0.2, 0.4, 1.0])
    for base_gap in (0.0, 0.1, 0.7, 1.0):
        gap = np.repeat(base_gap, growth.size)
        adjusted, defined, _ = recovery_adjustment(gap, np.ones(4, bool),
                                                   growth, np.ones(4, bool))
        assert defined.all()
        assert np.all(np.diff(adjusted) <= 1e-15)
        assert np.all(adjusted >= 0.0)
        assert np.all(adjusted <= gap)
    vals, _, _ = recovery_adjustment(np.repeat(0.7, 5), np.ones(5, bool),
                                     np.linspace(-1, 2, 5), np.ones(5, bool))
    assert np.all(np.diff(vals) <= 1e-15)


def test_undefined_sources_remain_undefined_and_are_not_zero_filled():
    adjusted, defined, capped = recovery_adjustment(
        np.array([0.4, 0.0, 0.4]), np.array([True, True, False]),
        np.array([0.5, 0.0, 0.1]), np.array([True, False, True]))
    assert defined.tolist() == [True, False, False]
    assert adjusted[0] == pytest.approx(0.2)
    assert np.isnan(adjusted[1:]).all()
    assert capped[0] == pytest.approx(0.5)
    assert np.isnan(capped[1:]).all()


def test_rejects_nonfinite_defined_growth_or_out_of_range_defined_tm2_gap():
    with pytest.raises(ValueError, match="defined Tm3"):
        recovery_adjustment(np.array([0.5]), np.array([True]), np.array([np.nan]), np.array([True]))
    with pytest.raises(ValueError, match="defined Tm2"):
        recovery_adjustment(np.array([1.1]), np.array([True]), np.array([0.0]), np.array([True]))


def test_output_keeps_exact_geoid_order_and_nulls_for_unadjustable_rows():
    ids = ["id-a", "id-b", "id-c"]
    regions = ["r"] * 3
    gap = np.array([0.0, 0.5, 0.7])
    gap_defined = np.array([True, True, False])
    growth = np.array([0.1, 0.3, 0.2])
    growth_defined = np.array([True, True, True])
    adjusted, adjusted_defined, capped = recovery_adjustment(
        gap, gap_defined, growth, growth_defined)
    fire = np.array([0.0, 0.2, 0.0])
    fire_defined = gap_defined.copy()
    table = output_table(ids, regions, gap, gap_defined, growth, growth_defined, fire,
                         fire_defined, adjusted, adjusted_defined, capped)
    result = pq.read_table(pa.BufferReader(parquet_bytes(table)))
    assert result["GEOID"].to_pylist() == ids
    assert result["recovery_adjusted_tm2_gap"].to_pylist() == [0.0, pytest.approx(0.35), None]
    assert result["adjusted_defined"].to_pylist() == [True, True, False]
    assert result["fire_affected"].to_pylist() == [False, True, False]
    assert parquet_bytes(table) == parquet_bytes(table)


def test_wrong_adjusted_defined_mask_is_rejected():
    with pytest.raises(ValueError, match="Tm2/Tm3 intersection"): 
        output_table(["a"], ["r"], np.array([0.2]), np.array([True]),
                     np.array([0.1]), np.array([True]), np.array([0.0]), np.array([True]),
                     np.array([0.18]), np.array([False]), np.array([0.1]))
