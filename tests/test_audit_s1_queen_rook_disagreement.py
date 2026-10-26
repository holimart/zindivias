from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_s1_queen_rook_disagreement import (  # noqa: E402
    absolute_disagreement,
    crossfit_region,
    output_table,
    parquet_bytes,
)


def test_absolute_disagreement_is_aligned_bounded_and_null_preserving():
    queen = np.array([-0.2, 0.3, np.nan])
    queen_defined = np.array([True, True, False])
    rook = np.array([0.1, -0.1, 0.5])
    rook_defined = np.array([True, True, True])
    values, defined = absolute_disagreement(queen, queen_defined, rook, rook_defined)
    assert defined.tolist() == [True, True, False]
    assert np.allclose(values[:2], [0.3, 0.4])
    assert np.isnan(values[2])
    with pytest.raises(ValueError, match="bounded"):
        absolute_disagreement(np.array([-1.1]), np.array([True]), np.array([1.0]), np.array([True]))


def test_absolute_disagreement_rejects_misaligned_and_numeric_undefined_values():
    with pytest.raises(ValueError, match="aligned"):
        absolute_disagreement(np.array([0.]), np.array([True]), np.array([0., 1.]), np.array([True, True]))
    with pytest.raises(ValueError, match="remain null"):
        absolute_disagreement(np.array([0.]), np.array([False]), np.array([0.]), np.array([True]))


def test_crossfit_is_deterministic_and_returns_null_outside_joint_support():
    geoids = [f"g{i:03d}" for i in range(60)]
    idx = np.arange(60, dtype=np.float64)
    mx4 = idx / 60
    queen = np.sin(idx / 5) / 4
    rook = np.cos(idx / 7) / 5
    target = np.abs(queen - rook)
    joint = np.ones(60, dtype=bool)
    joint[7] = False
    target[7] = mx4[7] = queen[7] = rook[7] = np.nan
    one = crossfit_region(geoids, "maricopa-az", target, mx4, queen, rook, joint)
    two = crossfit_region(geoids, "maricopa-az", target, mx4, queen, rook, joint)
    assert np.array_equal(one[3], two[3])
    assert np.allclose(one[0][joint], two[0][joint])
    assert np.allclose(one[1][joint], two[1][joint])
    assert np.isnan(one[0][7]) and np.isnan(one[1][7])
    assert sum(row["heldout_rows"] for row in one[2]) == 59


def test_output_table_preserves_undefined_disagreement_as_null_and_is_deterministic():
    official = [("001", "r"), ("002", "r")]
    mx4 = np.array([.5, .2]); mx4_mask = np.array([True, True])
    queen = np.array([.1, np.nan]); queen_mask = np.array([True, False])
    rook = np.array([.2, .3]); rook_mask = np.array([True, True])
    disagreement = np.array([.1, np.nan]); defined = np.array([True, False])
    prediction = np.array([.08, np.nan]); residual = np.array([.02, np.nan])
    table = output_table(official, mx4, mx4_mask, queen, queen_mask, rook, rook_mask,
                         disagreement, defined, prediction, residual, np.array([0, -1], dtype=np.int8))
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["absolute_Queen_Rook_disagreement"].to_pylist() == [.1, None]
    assert decoded["Queen_innovation"].to_pylist() == [.1, None]
    assert decoded["crossfit_residual"].to_pylist() == [.02, None]
    assert parquet_bytes(table) == payload
