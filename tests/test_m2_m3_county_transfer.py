from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_m2_m3_county_transfer.py"
spec = importlib.util.spec_from_file_location("m2_m3_county_transfer", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_county_fips_uses_exact_tract_geoid_prefix():
    assert module.county_fips("04013000100") == "04013"
    assert module.county_fips("35023970000") == "35023"
    with pytest.raises(ValueError, match="eleven numeric"):
        module.county_fips("4013000100")


def test_train_only_scaling_and_two_covariate_fit():
    x_train = np.array([[0., 2.], [2., 1.], [4., 3.], [6., 0.], [7., 2.]])
    y = 2. + .4 * x_train[:, 0] - .25 * x_train[:, 1]
    x_test = np.array([[3., 1.5], [1000., -1000.]])
    pred, fit = module.fit_county(y, x_train, x_test)
    assert fit["design_rank"] == 3
    assert fit["training_feature_mean"] == pytest.approx(x_train.mean(axis=0).tolist())
    assert pred[0] == pytest.approx(2. + .4 * 3. - .25 * 1.5)
    changed_test = x_test.copy(); changed_test[1] *= -100.
    _, changed_fit = module.fit_county(y, x_train, changed_test)
    assert changed_fit["training_feature_mean"] == fit["training_feature_mean"]
    assert changed_fit["coefficients_intercept_m2_m3"] == fit["coefficients_intercept_m2_m3"]


def test_uncorrected_baseline_metric_is_paired_against_zero_correction():
    y = np.array([1., -1., .5, -.5])
    prediction = .5 * y
    score = module.score_vs_zero(y, prediction)
    assert score["uncorrected_baseline_sse"] == pytest.approx(2.5)
    assert score["two_covariate_model_sse"] == pytest.approx(.625)
    assert score["delta_r2_vs_uncorrected_zero_correction"] == pytest.approx(.75)


def test_county_bootstrap_is_deterministic_and_reports_region_intervals():
    records = []
    for region in module.REGIONS:
        for c in range(3):
            records.append({"region": region, "baseline_sse": 10. + c,
                            "model_sse": 4. + c})
    a = module.county_block_bootstrap(records, replicates=200, seed=13)
    b = module.county_block_bootstrap(records, replicates=200, seed=13)
    assert a == b
    assert len(a["regional_delta_r2_ci_95"]) == 4
    assert a["pooled_delta_r2_ci_95"][0] > 0.
