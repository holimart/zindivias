from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_m3_address_mismatch_residual.py"
spec = importlib.util.spec_from_file_location("m3_address_mismatch_residual", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_linear_term_and_missingness_flag_distinguish_missing_from_observed_zero():
    x = np.array([0., .2, .4, .6, .8, np.nan, np.nan, np.nan])
    defined = np.isfinite(x)
    y = np.array([0., .1, .2, .3, .4, -.4, -.5, -.6])
    test_x = np.array([0., np.nan])
    test_defined = np.array([True, False])
    pred, fit = module.fit_linear_missingness(x, defined, y, test_x, test_defined)
    assert fit["coefficient_order"] == ["intercept", "standardized_M3_share", "M3_missing_indicator"]
    assert fit["training_observed_feature_n"] == 5
    assert fit["training_missing_feature_n"] == 3
    assert pred[1] != pytest.approx(pred[0])


def test_training_scaler_and_coefficients_do_not_depend_on_heldout_values():
    x = np.array([0., .1, .2, .3, .4, .5, np.nan, .7])
    defined = np.isfinite(x)
    y = np.arange(len(x), dtype=float) / 10.
    pred, fit = module.fit_linear_missingness(x, defined, y, np.array([.25, np.nan]), np.array([True, False]))
    pred2, fit2 = module.fit_linear_missingness(x, defined, y, np.array([100., -100.]), np.array([True, False]))
    assert fit["training_observed_feature_mean"] == fit2["training_observed_feature_mean"]
    assert fit["training_observed_feature_sd"] == fit2["training_observed_feature_sd"]
    assert fit["coefficients"] == fit2["coefficients"]
    assert pred[1] == pytest.approx(pred2[1])
    assert pred[0] != pytest.approx(pred2[0])


def test_paired_delta_r2_uses_zero_uncorrected_baseline():
    y = np.array([1., -1., .5, -.5])
    result = module.metric(y, .5*y)
    assert result["uncorrected_frozen_s1_baseline_sse"] == pytest.approx(2.5)
    assert result["linear_plus_missingness_sse"] == pytest.approx(.625)
    assert result["delta_r2"] == pytest.approx(.75)


def test_county_bootstrap_is_deterministic_and_stratified_by_region():
    records = [{"region": r, "baseline_sse": 10.+i, "model_sse": 4.+i,
                "out_of_county_delta_r2": (6.)/(10.+i)}
               for r in module.REGIONS for i in range(4)]
    first = module.county_block_bootstrap(records, replicates=200, seed=15)
    second = module.county_block_bootstrap(records, replicates=200, seed=15)
    assert first == second
    assert set(first["regional_delta_r2_ci_95"]) == set(module.REGIONS)
    assert first["pooled_delta_r2_ci_95"][0] > 0.
    assert first["mean_county_delta_r2_ci_95"][0] > 0.
