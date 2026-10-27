from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_named_road_entropy_leave_region_out.py"
spec = importlib.util.spec_from_file_location("named_road_entropy_leave_region_out", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_fold_imputation_scaling_and_regression_are_training_only():
    train_x = np.column_stack([np.arange(20.), np.arange(20.) * 2, np.sin(np.arange(20.)), np.arange(20.) ** 2])
    train_x[2, 1] = np.nan
    train_y = 0.2 + .03 * train_x[:, 0] + .1 * np.nan_to_num(train_x[:, 1], nan=15.)
    test_x = np.array([[np.nan, 1e8, 0.2, 1.], [35., np.nan, -0.3, 9.]])
    prediction, baseline, diagnostics = module.fit_training_fold(train_x, train_y, test_x)
    assert prediction.shape == (2,)
    assert baseline == pytest.approx(train_y.mean())
    assert diagnostics["training_missing_by_predictor"]["pct_urban"] == 1
    assert diagnostics["heldout_missing_by_predictor"]["log_total_named_road_km"] == 1
    assert diagnostics["heldout_missing_by_predictor"]["pct_urban"] == 1
    assert diagnostics["training_only_medians"]["log_total_named_road_km"] == pytest.approx(np.median(train_x[:, 0]))
    expected_imputed_mean = np.mean(np.where(np.isnan(train_x[:, 1]), np.nanmedian(train_x[:, 1]), train_x[:, 1]))
    assert diagnostics["training_only_means_after_imputation"]["pct_urban"] == pytest.approx(expected_imputed_mean)


def test_held_region_values_cannot_change_training_preprocessing_or_fit():
    rng = np.random.default_rng(110)
    train_x = rng.normal(size=(80, 4))
    train_y = .5 + train_x @ np.array([.1, -.2, .3, .05]) + rng.normal(0, .01, 80)
    test_x = rng.normal(size=(15, 4))
    _, baseline_a, diag_a = module.fit_training_fold(train_x, train_y, test_x)
    test_x_extreme = test_x.copy()
    test_x_extreme[0, :] = [1e12, -1e12, 1e10, -1e10]
    _, baseline_b, diag_b = module.fit_training_fold(train_x, train_y, test_x_extreme)
    assert baseline_a == baseline_b
    for key in ("training_only_medians", "training_only_means_after_imputation", "training_only_scales_after_imputation",
                "standardized_ols_coefficients_including_intercept"):
        assert diag_a[key] == diag_b[key]


def test_r2_is_relative_to_training_mean_not_heldout_mean():
    y = np.array([2., 3., 5.])
    prediction = np.array([2., 3., 4.])
    result = module.r2_against_training_mean(y, prediction, training_mean=1.)
    expected = 1. - np.sum((y - prediction) ** 2) / np.sum((y - 1.) ** 2)
    assert result["r2_vs_training_mean"] == pytest.approx(expected)
    assert result["r2_vs_training_mean"] < 1.


def test_fold_rejects_no_training_values_for_a_feature_and_bad_shapes():
    train_x = np.ones((12, 4))
    train_x[:, 2] = np.nan
    y = np.linspace(0., 1., 12)
    with pytest.raises(ValueError, match="entirely missing"):
        module.fit_training_fold(train_x, y, np.ones((3, 4)))
    with pytest.raises(ValueError, match="invalid training or held-region"):
        module.fit_training_fold(np.ones((2, 4)), np.ones(2), np.ones((3, 4)))
