from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_named_road_entropy_continuous_heat.py"
spec = importlib.util.spec_from_file_location("named_road_entropy_continuous_heat", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_delta_r2_is_paired_sse_improvement_over_shared_total_sum_squares():
    y = np.array([0., 1., 2., 3., 4.])
    base = np.array([.1, .8, 1.8, 2.7, 3.5])
    augmented = np.array([0., 1., 2., 3., 4.])
    result = module._delta_r2(y, base, augmented)
    assert result["heat_augmented_r2"] == pytest.approx(1.)
    assert result["delta_r2"] == pytest.approx(result["heat_augmented_r2"] - result["baseline_r2"])
    assert result["delta_r2"] > 0.


def test_crossfit_uses_training_only_heat_scaling_and_exact_paired_mask():
    rng = np.random.default_rng(17)
    n = 250
    ids = [f"{i:011d}" for i in range(n)]
    folds = np.arange(n, dtype=np.int8) % 5
    predictors = rng.normal(size=(n, 4))
    heat = np.linspace(2., 17., n)
    entropy = .45 + .08 * predictors[:, 0] + .12 * (heat - heat.mean()) + rng.normal(0, .02, n)
    mask = np.ones(n, dtype=bool)
    mask[[0, 13, 112]] = False
    entropy[~mask] = np.nan
    heat[~mask] = np.nan
    base_prediction, augmented_prediction, reports, slopes, returned_folds = module.crossfit_region(
        ids, "maricopa-az", folds, entropy, heat, predictors, mask)
    assert np.array_equal(returned_folds, folds)
    assert np.array_equal(np.isfinite(base_prediction), mask)
    assert np.array_equal(np.isfinite(augmented_prediction), mask)
    assert len(reports) == 5 and len(slopes) == 5
    assert all(report["training_heat_mean"] == pytest.approx(heat[mask & (folds != report["fold"])].mean())
               for report in reports)
    assert np.all(np.isfinite(slopes)) and np.all(slopes > 0.)
    y = entropy[mask]
    delta = module._delta_r2(y, base_prediction[mask], augmented_prediction[mask])["delta_r2"]
    assert delta > 0.


def test_crossfit_rejects_bad_fold_or_zero_training_heat_variance():
    n = 25
    ids = [f"{i:011d}" for i in range(n)]
    folds = np.arange(n, dtype=np.int8) % 5
    y = np.linspace(0., 1., n)
    x = np.column_stack([np.linspace(0., 1., n), np.sin(y), np.cos(y), y ** 2])
    heat = np.ones(n)
    mask = np.ones(n, dtype=bool)
    with pytest.raises(ValueError, match="zero/nonfinite"):
        module.crossfit_region(ids, "northern-ca", folds, y, heat, x, mask)
    folds[0] = 9
    with pytest.raises(ValueError, match="fold IDs"):
        module.crossfit_region(ids, "northern-ca", folds, y, np.arange(n, dtype=float), x, mask)


def test_region_bootstrap_is_deterministic_and_paired():
    y = np.linspace(0., 1., 80)
    base = .2 + .5 * y
    augmented = y.copy()
    first = module.bootstrap_region_delta(y, base, augmented, seed=42, replicates=200)
    second = module.bootstrap_region_delta(y, base, augmented, seed=42, replicates=200)
    assert np.array_equal(first, second)
    assert np.all(first > 0.)


def test_pooled_bootstrap_resamples_pairs_within_each_region_deterministically():
    data = {}
    for offset, region in enumerate(module.REGIONS):
        y = np.linspace(0., 1., 60) + offset
        data[region] = (y, .25 + .5 * y, y.copy())
    first = module.bootstrap_pooled_delta(data, seed=913, replicates=150)
    second = module.bootstrap_pooled_delta(data, seed=913, replicates=150)
    assert np.array_equal(first, second)
    assert np.all(first > 0.)
    with pytest.raises(ValueError, match="all four regions"):
        module.bootstrap_pooled_delta({"maricopa-az": data["maricopa-az"]}, seed=1, replicates=2)
