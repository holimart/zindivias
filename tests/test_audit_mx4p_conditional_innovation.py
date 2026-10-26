from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4p_conditional_innovation import (  # noqa: E402
    crossfit_region,
    deterministic_folds,
    distribution,
    output_table,
    parquet_bytes,
    pearson,
)


def test_folds_are_deterministic_region_local_and_order_invariant():
    ids = [f"0400194{i:04d}" for i in range(100)]
    first = deterministic_folds(ids, "maricopa-az")
    second = deterministic_folds(ids, "maricopa-az")
    permutation = np.arange(len(ids))[::-1]
    reversed_fold = deterministic_folds([ids[i] for i in permutation], "maricopa-az")
    assert np.array_equal(first, second)
    assert np.array_equal(first, reversed_fold[::-1])
    assert set(first.tolist()) == set(range(5))
    assert not np.array_equal(first, deterministic_folds(ids, "northern-ca"))


def test_crossfit_recovers_fixed_linear_relation_without_in_sample_prediction():
    ids = [f"0400194{i:04d}" for i in range(200)]
    x1 = np.linspace(0.0, 1.0, len(ids))
    x2 = np.sin(np.arange(len(ids)) * 0.31)
    y = 1.75 + 0.4 * x1 - 0.2 * x2
    prediction, residual, folds, fold_ids = crossfit_region(
        ids, "maricopa-az", y, x1, x2, np.ones(len(ids), bool))
    assert np.isfinite(prediction).all() and np.isfinite(residual).all()
    assert np.max(np.abs(residual)) < 1e-12
    assert np.allclose(fold_ids, deterministic_folds(ids, "maricopa-az"))
    assert len(folds) == 5
    assert all(row["design_rank"] == 3 and row["heldout_joint_defined"] > 0 for row in folds)
    assert all(row["coefficient_order"] == ["intercept", "Mx4|F|covariate",
                                            "S1|F|covariate:lag_mx4_f_covariate"] for row in folds)


def test_heldout_response_changes_do_not_affect_its_own_fold_predictions():
    ids = [f"0400194{i:04d}" for i in range(125)]
    x1 = np.linspace(0.0, 1.0, len(ids))
    x2 = np.cos(np.arange(len(ids)) * 0.19)
    y = 0.3 + 0.7 * x1 + 0.15 * x2 + np.sin(np.arange(len(ids)) * 0.4) * 0.03
    folds = deterministic_folds(ids, "maricopa-az")
    original_prediction, _, _, _ = crossfit_region(ids, "maricopa-az", y, x1, x2,
                                                   np.ones(len(ids), bool))
    changed_y = y.copy()
    changed_y[folds == 2] += 100.0
    changed_prediction, _, _, _ = crossfit_region(ids, "maricopa-az", changed_y, x1, x2,
                                                  np.ones(len(ids), bool))
    assert np.allclose(original_prediction[folds == 2], changed_prediction[folds == 2])


def test_crossfit_uses_only_complete_cases_and_returns_out_of_fold_values():
    ids = [f"0400194{i:04d}" for i in range(75)]
    x1 = np.linspace(0.0, 1.0, len(ids))
    x2 = np.cos(np.arange(len(ids)) * 0.17)
    y = 0.1 + x1 * x1 + x2 * 0.25
    mask = np.ones(len(ids), bool)
    mask[[2, 11, 31]] = False
    prediction, residual, folds, _ = crossfit_region(ids, "maricopa-az", y, x1, x2, mask)
    assert np.isfinite(prediction[mask]).all() and np.isfinite(residual[mask]).all()
    assert np.isnan(prediction[~mask]).all() and np.isnan(residual[~mask]).all()
    assert sum(row["heldout_joint_defined"] for row in folds) == int(mask.sum())
    assert all(row["train_joint_defined"] + row["heldout_joint_defined"] == int(mask.sum())
               for row in folds)


def test_crossfit_rejects_inconsistent_shapes_and_insufficient_folds():
    with pytest.raises(ValueError, match="align"):
        crossfit_region(["a", "b"], "maricopa-az", np.ones(2), np.ones(1),
                        np.ones(2), np.ones(2, bool))
    ids = [f"id-{i}" for i in range(5)]
    with pytest.raises(ValueError, match="insufficient"):
        crossfit_region(ids, "maricopa-az", np.arange(5.), np.arange(5.),
                        np.arange(5.), np.ones(5, bool))


def test_pearson_and_distribution_cover_constant_and_empty_values():
    assert pearson(np.ones(10), np.arange(10.)) is None
    assert pearson(np.arange(10.), np.arange(10.)) == pytest.approx(1.0)
    assert distribution(np.array([np.nan, 1.0, 3.0]))["variance"] == pytest.approx(1.0)
    assert distribution(np.array([np.nan]))["n"] == 0


def test_residual_artifact_aligns_rows_and_preserves_undefined_masks():
    ids = ["04001944902", "04001945100", "04001945300"]
    regions = ["maricopa-az"] * 3
    fold = np.array([0, 1, 2], np.int8)
    feature_values = {"Mx4|F|covariate": np.array([0.2, 0.3, np.nan])}
    feature_defined = {"Mx4|F|covariate": np.array([True, True, False])}
    joint = np.array([True, True, False])
    prediction = np.array([0.15, 0.25, np.nan])
    residual = np.array([0.05, 0.05, np.nan])
    table = output_table(ids, regions, fold, feature_values, feature_defined,
                         joint, prediction, residual)
    decoded = pq.read_table(pa.BufferReader(parquet_bytes(table)))
    assert decoded["GEOID"].to_pylist() == ids
    assert decoded["jointly_defined"].to_pylist() == [True, True, False]
    assert decoded["crossfit_residual"].to_pylist() == [pytest.approx(0.05), pytest.approx(0.05), None]
    assert decoded["mx4_f_covariate"].to_pylist() == [0.2, 0.3, None]
    assert parquet_bytes(table) == parquet_bytes(table)


def test_residual_artifact_rejects_mismatched_joint_mask():
    with pytest.raises(ValueError, match="null masks"):
        output_table(["a"], ["maricopa-az"], np.array([0]), {}, {},
                     np.array([False]), np.array([0.0]), np.array([0.0]))
