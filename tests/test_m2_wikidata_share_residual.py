from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_m2_wikidata_share_residual.py"
spec = importlib.util.spec_from_file_location("m2_wikidata_share_residual", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_one_df_hinge_is_monotone_and_scaling_knot_train_only():
    x = np.linspace(0., 1., 80)
    y = .2 + .8 * np.maximum(x - .5, 0.) + np.sin(x * 30.) * .001
    eval_a = np.array([.1, .4, .5, .7, .9])
    pred_a, detail_a = module.fit_monotone_spline(x, y, eval_a)
    eval_b = eval_a.copy(); eval_b[-1] = 1e6
    pred_b, detail_b = module.fit_monotone_spline(x, y, eval_b)
    assert detail_a["spline_continuous_degrees_of_freedom"] == 1
    assert detail_a["direction"] == "nondecreasing"
    assert detail_a["spline_coefficient_nonnegative"] >= 0.
    assert detail_a["training_x_mean"] == detail_b["training_x_mean"]
    assert detail_a["training_x_sd"] == detail_b["training_x_sd"]
    assert detail_a["knot_training_standardized_median"] == detail_b["knot_training_standardized_median"]
    assert np.all(np.diff(pred_a) >= -1e-12)
    assert pred_a[:-1] == pytest.approx(pred_b[:-1])


def test_negative_association_selects_nonincreasing_train_only():
    x = np.linspace(0., 1., 60)
    y = 2. * np.maximum(.5 - x, 0.)
    pred, detail = module.fit_monotone_spline(x, y, np.linspace(0., 1., 100))
    assert detail["direction"] == "nonincreasing"
    assert detail["spline_coefficient_nonnegative"] >= 0.
    assert np.all(np.diff(pred) <= 1e-12)


def test_metric_compares_paired_residual_sse_to_zero_correction():
    y = np.array([1., -1., .5, -.5])
    result = module.metric(y, .5*y)
    assert result["frozen_s1_residual_baseline_sse"] == pytest.approx(2.5)
    assert result["monotone_spline_sse"] == pytest.approx(.625)
    assert result["delta_r2"] == pytest.approx(.75)


def test_county_bootstrap_is_deterministic_and_has_all_region_intervals():
    rows = [{"region": region, "baseline_sse": 10.+i, "model_sse": 5.+i}
            for region in module.REGIONS for i in range(3)]
    a = module.county_bootstrap(rows, replicates=200, seed=123)
    b = module.county_bootstrap(rows, replicates=200, seed=123)
    assert a == b
    assert set(a["regional_delta_r2_ci_95"]) == set(module.REGIONS)
    assert a["pooled_delta_r2_ci_95"][0] > 0.
