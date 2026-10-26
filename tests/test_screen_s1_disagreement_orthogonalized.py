from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4p_conditional_innovation import deterministic_folds  # noqa: E402
from screen_s1_disagreement_orthogonalized import crossfit_residualize  # noqa: E402


def _fixture(n: int = 60):
    ids = [f"{i:011d}" for i in range(n)]
    x1 = np.linspace(0.02, 0.98, n)
    x2 = np.sin(np.arange(n) * 0.31)
    target = 0.12 + 0.25 * x1 - 0.08 * x2 + 0.07 * np.cos(np.arange(n) * 0.83)
    mask = np.ones(n, dtype=bool)
    return ids, x1, x2, target, mask


def test_crossfit_uses_other_folds_only_and_reproduces_manual_ols():
    ids, mx4, lag, target, mask = _fixture()
    pred, residual, reports, folds = crossfit_residualize(ids, "maricopa-az", target, mx4, lag, mask)
    expected_folds = deterministic_folds(ids, "maricopa-az")
    assert np.array_equal(folds, expected_folds)
    assert len(reports) == 5
    for fold in range(5):
        train = folds != fold
        test = folds == fold
        design_train = np.column_stack((np.ones(train.sum()), mx4[train], lag[train]))
        coef, *_ = np.linalg.lstsq(design_train, target[train], rcond=None)
        expected = np.column_stack((np.ones(test.sum()), mx4[test], lag[test])) @ coef
        assert np.allclose(pred[test], expected)
        assert np.allclose(residual[test], target[test] - expected)
        assert reports[fold]["train_defined_ids"] == int(train.sum())
        assert reports[fold]["heldout_defined_ids"] == int(test.sum())


def test_crossfit_preserves_nulls_on_nonjoint_rows():
    ids, mx4, lag, target, mask = _fixture()
    mask[[2, 17, 42]] = False
    target[~mask] = np.nan
    mx4[~mask] = np.nan
    lag[~mask] = np.nan
    pred, residual, _, folds = crossfit_residualize(ids, "northern-ca", target, mx4, lag, mask)
    assert np.isfinite(pred[mask]).all()
    assert np.isfinite(residual[mask]).all()
    assert np.isnan(residual[~mask]).all()
    assert np.all(folds >= 0)


def test_crossfit_rejects_bad_alignment_duplicate_ids_and_null_mask_mismatch():
    ids, mx4, lag, target, mask = _fixture(40)
    with pytest.raises(ValueError, match="align"):
        crossfit_residualize(ids, "maricopa-az", target[:-1], mx4, lag, mask)
    duplicate_ids = ids.copy()
    duplicate_ids[-1] = duplicate_ids[0]
    with pytest.raises(ValueError, match="unique GEOIDs"):
        crossfit_residualize(duplicate_ids, "maricopa-az", target, mx4, lag, mask)
    target[0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        crossfit_residualize(ids, "maricopa-az", target, mx4, lag, mask)


def test_crossfit_requires_supported_folds():
    ids, mx4, lag, target, mask = _fixture(10)
    with pytest.raises(ValueError):
        crossfit_residualize(ids, "maricopa-az", target, mx4, lag, mask, n_folds=15)
