from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_m2_m3_joint_nonlinear_residual.py"
spec = importlib.util.spec_from_file_location("m2_m3_joint_nonlinear_residual", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_spline_knots_and_scaling_are_learned_only_from_training_rows():
    rng = np.random.default_rng(812)
    train = rng.uniform(0., 1., size=(80, 2))
    test = np.array([[.25, .75], [1e6, -1e6]])
    a_train, a_test, a_meta = module.spline_features_train_only(train, test)
    changed = test.copy(); changed[1] = [-1e8, 1e8]
    b_train, b_test, b_meta = module.spline_features_train_only(train, changed)
    assert np.array_equal(a_train, b_train)
    assert a_meta["knots_standardized_by_feature"] == b_meta["knots_standardized_by_feature"]
    assert a_meta["basis_training_mean"] == b_meta["basis_training_mean"]
    assert a_meta["interaction_terms"] == ["M2_z_x_M3_z"]
    assert a_meta["basis_feature_names"].count("M2_z_x_M3_z") == 1
    assert a_test.shape == (2, len(a_meta["basis_feature_names"]))
    assert b_test.shape == a_test.shape


def test_county_fold_fits_full_fixed_basis_without_heldout_target():
    rng = np.random.default_rng(94)
    x = rng.uniform(0., 1., size=(100, 2))
    y = .2 + .5 * x[:, 0] - .3 * x[:, 1] + .15 * x[:, 0] * x[:, 1] + rng.normal(0., .01, 100)
    train_x, eval_x = x[:80], x[80:]
    pred, detail = module.fit_fold(y[:80], train_x, eval_x)
    pred2, detail2 = module.fit_fold(y[:80], train_x, eval_x.copy())
    assert np.allclose(pred, pred2)
    assert detail["knots_standardized_by_feature"] == detail2["knots_standardized_by_feature"]
    assert detail["interaction_terms"] == ["M2_z_x_M3_z"]
    assert detail["training_rows"] == 80
    assert len(pred) == 20


def test_metric_compares_correction_with_zero_uncorrected_residual():
    y = np.array([1., -1., .5, -.5])
    pred = .25 * y
    result = module.residual_delta(y, pred)
    assert result["uncorrected_s1_baseline_sse"] == pytest.approx(2.5)
    assert result["spline_correction_sse"] == pytest.approx(1.40625)
    assert result["delta_r2_vs_uncorrected_s1"] == pytest.approx(.4375)


def test_county_block_bootstrap_is_deterministic_and_region_stratified():
    rows = []
    for region in module.REGIONS:
        for i in range(3):
            rows.append({"region": region, "baseline_sse": 10. + i, "model_sse": 5. + i})
    first = module.county_block_bootstrap(rows, replicates=200, seed=7)
    second = module.county_block_bootstrap(rows, replicates=200, seed=7)
    assert first == second
    assert first["pooled_delta_r2_ci_95"][0] > 0.
    assert set(first["regional_delta_r2_ci_95"]) == set(module.REGIONS)
