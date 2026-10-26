from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_mx4_population_weighted_median import (  # noqa: E402
    crossfit_region,
    deterministic_folds,
    leave_one_out_weighted_median,
    output_table,
    parquet_bytes,
    pearson,
    weighted_median,
)


def test_weighted_median_uses_first_value_reaching_half_weight():
    assert weighted_median(np.array([0.8, 0.0, 0.2]), np.array([50.0, 10.0, 30.0])) == pytest.approx(0.8)
    assert weighted_median(np.array([0.0, 1.0]), np.array([1.0, 1.0])) == 0.0
    assert weighted_median(np.array([0.3, 0.9]), np.array([0.0, 4.0])) == 0.9
    assert weighted_median(np.array([0.3]), np.array([0.0])) is None


def test_weighted_median_rejects_bad_alignment_and_out_of_range_share():
    with pytest.raises(ValueError, match="aligned"):
        weighted_median(np.array([0.2]), np.array([1.0, 2.0]))
    with pytest.raises(ValueError, match=r"in \[0,1\]"):
        weighted_median(np.array([1.01]), np.array([1.0]))


def test_leave_one_out_excludes_focal_population_and_keeps_only_peer_defined_output():
    official = [(f"id-{i}", "maricopa-az") for i in range(4)]
    counties = np.array(["001", "001", "001", "999"], dtype=object)
    population = np.array([10.0, 30.0, 50.0, 10.0])
    source = np.array([0.0, 0.2, 0.8, 0.9])
    source_defined = np.ones(4, bool)
    result, defined, counts = leave_one_out_weighted_median(
        official, counties, population, source, source_defined)
    assert defined.tolist() == [True, True, True, False]
    assert result[:3].tolist() == pytest.approx([0.8, 0.8, 0.2])
    assert np.isnan(result[3])
    assert counts.tolist() == [2, 2, 2, 0]


def test_invalid_focal_does_not_donate_but_can_receive_peer_median():
    official = [(f"id-{i}", "eastern-ok") for i in range(4)]
    counties = np.array(["001"] * 4, dtype=object)
    population = np.array([10.0, 30.0, 50.0, 100.0])
    source = np.array([0.0, 0.2, 0.8, np.nan])
    defined = np.array([True, True, True, False])
    result, mask, counts = leave_one_out_weighted_median(official, counties, population, source, defined)
    assert mask.all()
    assert result.tolist() == pytest.approx([0.8, 0.8, 0.2, 0.8])
    assert counts.tolist() == [2, 2, 2, 3]


def test_deterministic_crossfit_is_out_of_fold_and_null_preserving():
    ids = [f"id-{i}" for i in range(100)]
    x1 = np.linspace(0, 1, 100)
    x2 = np.sin(np.arange(100) * 0.2)
    y = 0.3 + 0.4 * x1 - 0.2 * x2
    defined = np.ones(100, bool)
    defined[[4, 10, 50]] = False
    pred, residual, folds, fold_ids = crossfit_region(ids, "maricopa-az", y, (x1, x2), defined)
    assert len(folds) == 5
    assert np.allclose(residual[defined], 0.0, atol=1e-12)
    assert np.isnan(pred[~defined]).all() and np.isnan(residual[~defined]).all()
    assert np.array_equal(fold_ids, deterministic_folds(ids, "maricopa-az"))
    assert pearson(y[defined], x1[defined]) is not None


def test_output_serialization_preserves_exact_pairwise_nulls_and_bounds():
    official = [("00000000001", "maricopa-az"), ("00000000002", "maricopa-az")]
    candidate = np.array([0.4, np.nan]); candidate_defined = np.array([True, False])
    mx4b = candidate.copy(); mx4b_mask = candidate_defined.copy()
    mx4f = np.array([0.2, 0.3]); mx4f_mask = np.ones(2, bool)
    lag = np.array([0.5, 0.7]); lag_mask = np.ones(2, bool)
    joint = np.array([True, False]); fold = np.array([0, 1], np.int8)
    prediction = np.array([0.35, np.nan]); residual = np.array([0.05, np.nan])
    table = output_table(official, candidate, candidate_defined, np.array([3, 0]),
                         mx4b, mx4b_mask, mx4f, mx4f_mask, lag, lag_mask,
                         fold, joint, prediction, residual)
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["GEOID"].to_pylist() == ["00000000001", "00000000002"]
    assert decoded["peer_population_weighted_median_share"].to_pylist() == [0.4, None]
    assert decoded["crossfit_residual"].to_pylist() == [pytest.approx(0.05), None]
    assert decoded["jointly_defined"].to_pylist() == [True, False]
    assert parquet_bytes(table) == payload


def test_output_rejects_mismatched_joint_mask_and_nonbounded_median():
    pairs = [("a", "maricopa-az")]
    with pytest.raises(ValueError, match="joint support"):
        output_table(pairs, np.array([0.2]), np.array([True]), np.array([1]),
                     np.array([0.2]), np.array([True]), np.array([0.3]), np.array([True]),
                     np.array([0.4]), np.array([True]), np.array([0]), np.array([False]),
                     np.array([np.nan]), np.array([np.nan]))
    with pytest.raises(ValueError, match=r"within \[0,1\]"):
        output_table(pairs, np.array([1.1]), np.array([True]), np.array([1]),
                     np.array([0.2]), np.array([True]), np.array([0.3]), np.array([True]),
                     np.array([0.4]), np.array([True]), np.array([0]), np.array([True]),
                     np.array([0.5]), np.array([0.6]))
