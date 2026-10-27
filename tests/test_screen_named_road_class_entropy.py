from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_named_road_class_entropy.py"
spec = importlib.util.spec_from_file_location("named_road_class_entropy", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_normalized_four_class_entropy_and_positive_length_mask():
    lengths = np.array([[0., 0., 0., 0.], [8., 0., 0., 0.], [1., 1., 1., 1.], [2., 2., 0., 0.]])
    entropy, defined, total = module.entropy_from_class_lengths(lengths)
    assert defined.tolist() == [False, True, True, True]
    assert np.isnan(entropy[0])
    assert entropy[1] == pytest.approx(0.)
    assert entropy[2] == pytest.approx(1.)
    assert entropy[3] == pytest.approx(np.log(2.) / np.log(4.))
    assert total.tolist() == [0., 8., 4., 4.]


def test_entropy_rejects_invalid_lengths_or_wrong_class_count():
    with pytest.raises(ValueError, match="shape"):
        module.entropy_from_class_lengths(np.ones((2, 3)))
    with pytest.raises(ValueError, match="finite and nonnegative"):
        module.entropy_from_class_lengths(np.array([[1., -1., 0., 0.]]))
    with pytest.raises(ValueError, match="finite and nonnegative"):
        module.entropy_from_class_lengths(np.array([[1., np.nan, 0., 0.]]))


def test_crossfit_is_region_local_and_preserves_null_rows():
    n = 50
    geoids = [f"{i:011d}" for i in range(n)]
    predictors = np.column_stack((np.linspace(0., 1., n), np.linspace(1., 0., n),
                                  np.sin(np.arange(n)), np.cos(np.arange(n))))
    target = np.clip(.2 + .2 * predictors[:, 0] + .1 * predictors[:, 2], 0., 1.)
    mask = np.ones(n, dtype=bool)
    mask[0] = False
    target[0] = np.nan
    prediction, residual, folds, report = module.crossfit_region(geoids, "maricopa-az", target, predictors, mask)
    assert np.array_equal(np.isfinite(residual), mask)
    assert np.array_equal(np.isfinite(prediction), mask)
    assert folds.shape == (n,) and np.all((folds >= 0) & (folds < 5))
    assert len(report) == 5
    assert np.isnan(residual[0])
    with pytest.raises(ValueError, match="region"):
        module.crossfit_region(geoids, "not-a-region", target, predictors, mask)
