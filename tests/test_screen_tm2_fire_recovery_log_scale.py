from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_tm2_fire_recovery_log_scale import (  # noqa: E402
    RECOVERY_Q95,
    log_recovery_adjustment,
    output_table,
    parquet_bytes,
)


def test_log_formula_uses_fixed_q95_scale_and_caps_large_growth():
    growth = np.array([-0.5, 0.0, RECOVERY_Q95, 1.0, 206.0])
    gap = np.repeat(0.8, len(growth))
    adjusted, defined, discount = log_recovery_adjustment(
        gap, np.ones(5, bool), growth, np.ones(5, bool))
    assert defined.all()
    assert discount[0] == 0.0
    assert discount[1] == 0.0
    assert discount[2] == pytest.approx(1.0)
    assert discount[3] == 1.0
    assert discount[4] == 1.0
    assert adjusted.tolist() == pytest.approx([0.8, 0.8, 0.0, 0.0, 0.0])


def test_log_adjustment_monotone_nonincreasing_and_bounded_for_varied_gaps():
    growth = np.array([-1.0, 0.0, 0.01, 0.05, RECOVERY_Q95 / 2, RECOVERY_Q95, 2.0])
    for base_gap in (0.0, 0.1, 0.7, 1.0):
        gap = np.repeat(base_gap, growth.size)
        adjusted, defined, discount = log_recovery_adjustment(
            gap, np.ones(growth.size, bool), growth, np.ones(growth.size, bool))
        assert defined.all()
        assert np.all(np.diff(adjusted) <= 1e-15)
        assert np.all((adjusted >= 0.0) & (adjusted <= gap))
        assert np.all((discount >= 0.0) & (discount <= 1.0))


def test_undefined_sources_remain_undefined_without_zero_filling():
    adjusted, defined, discount = log_recovery_adjustment(
        np.array([0.4, 0.0, 0.4]), np.array([True, True, False]),
        np.array([0.1, 0.2, 0.3]), np.array([True, False, True]))
    assert defined.tolist() == [True, False, False]
    assert adjusted[0] == pytest.approx(0.4 * (1 - np.log1p(.1) / np.log1p(RECOVERY_Q95)))
    assert np.isnan(adjusted[1:]).all()
    assert np.isnan(discount[1:]).all()


def test_rejects_bad_shapes_normalizer_or_defined_values():
    with pytest.raises(ValueError, match="aligned"):
        log_recovery_adjustment(np.array([0.5]), np.array([True]),
                                np.array([0.1, 0.2]), np.array([True, True]))
    with pytest.raises(ValueError, match="positive and finite"):
        log_recovery_adjustment(np.array([0.5]), np.array([True]),
                                np.array([0.1]), np.array([True]), normalizer=0.0)
    with pytest.raises(ValueError, match="defined Tm3"):
        log_recovery_adjustment(np.array([0.5]), np.array([True]),
                                np.array([np.nan]), np.array([True]))
    with pytest.raises(ValueError, match="defined Tm2"):
        log_recovery_adjustment(np.array([1.1]), np.array([True]),
                                np.array([0.1]), np.array([True]))


def test_output_keeps_exact_id_order_and_nulls_for_undefined_rows():
    official = [("a", "eastern-ok"), ("b", "eastern-ok"), ("c", "eastern-ok")]
    gap = np.array([0.0, 0.5, 0.7])
    gap_defined = np.array([True, True, False])
    growth = np.array([0.1, 0.3, 0.2])
    growth_defined = np.array([True, True, True])
    adjusted, defined, discount = log_recovery_adjustment(gap, gap_defined, growth, growth_defined)
    table = output_table(official, gap, gap_defined, growth, growth_defined,
                         np.array([0.0, 0.2, 0.0]), gap_defined,
                         adjusted, defined, discount)
    decoded = pq.read_table(pa.BufferReader(parquet_bytes(table)))
    assert decoded["GEOID"].to_pylist() == ["a", "b", "c"]
    assert decoded["adjusted_defined"].to_pylist() == [True, True, False]
    assert decoded["log_recovery_adjusted_tm2_gap"].to_pylist()[2] is None
    assert decoded["fire_affected"].to_pylist() == [False, True, False]
    assert parquet_bytes(table) == parquet_bytes(table)


def test_output_rejects_mask_different_from_joint_sources():
    with pytest.raises(ValueError, match="joint source mask"):
        output_table([("a", "r")], np.array([0.2]), np.array([True]),
                     np.array([0.1]), np.array([True]), np.array([0.0]), np.array([True]),
                     np.array([0.18]), np.array([False]), np.array([0.1]))
