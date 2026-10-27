from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_primary_category_missingness.py"
spec = importlib.util.spec_from_file_location("poi_primary_category_missingness", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_fold():
    rng = np.random.default_rng(20261031)
    regions = np.repeat(np.asarray(module.REGIONS, dtype=object), 25)
    folds = np.tile(np.arange(5), 20)
    base = rng.normal(size=(len(regions), 3))
    share = rng.uniform(0., 1., len(regions))
    y = np.asarray([.05 * module.REGIONS.index(r) for r in regions]) + .2 * base[:, 0] + .25 * share + rng.normal(0., .01, len(regions))
    test = folds == 0
    return y, base, share, regions, ~test, test


def test_missing_share_counts_null_primary_per_unique_place_with_minimum_support():
    result = module.primary_missing_share(np.array([10, 12, 9, 10]), np.array([7, 0, 8, 10]))
    assert np.array_equal(result["defined"], [True, True, False, True])
    assert np.array_equal(result["missing_primary_count"], [3, 12, 1, 0])
    assert np.allclose(result["missing_primary_share"][result["defined"]], [.3, 1., 0.])
    assert np.isnan(result["missing_primary_share"][2])


def test_missing_share_rejects_inconsistent_counts():
    with pytest.raises(ValueError, match="categorized <= total"):
        module.primary_missing_share(np.array([3]), np.array([4]))
    with pytest.raises(ValueError, match="minimum places"):
        module.primary_missing_share(np.array([10]), np.array([5]), min_places=0)


def test_single_term_standardization_uses_training_rows_only():
    x = np.array([0., 2., 100., 10., 14., -50.])
    train = np.array([True, True, False, True, True, False])
    test = ~train
    z, stats = module.standardize_train_only(x, train, test)
    assert stats["training_mean"] == pytest.approx(6.5)
    assert stats["training_sd"] == pytest.approx(np.std([0., 2., 10., 14.]))
    changed = x.copy(); changed[test] *= 1000.
    _, changed_stats = module.standardize_train_only(changed, train, test)
    assert changed_stats == stats
    assert np.allclose(z[train].mean(), 0.)


def test_fold_models_are_nested_paired_and_do_not_train_on_test_targets():
    y, base, share, regions, train, test = synthetic_fold()
    before = module.fit_fold(y, base, share, regions, train, test)
    changed_y = y.copy(); changed_y[test] += 1000.
    after = module.fit_fold(changed_y, base, share, regions, train, test)
    assert np.allclose(before["baseline_prediction"][test], after["baseline_prediction"][test])
    assert np.allclose(before["augmented_prediction"][test], after["augmented_prediction"][test])
    assert np.array_equal(np.isfinite(before["baseline_prediction"]), test)
    assert np.array_equal(np.isfinite(before["augmented_prediction"]), test)


def test_delta_r2_and_bootstrap_are_paired_and_reproducible():
    y = np.linspace(-1., 1., 80)
    base = np.zeros_like(y)
    augmented = .1 * y
    result = module.delta_r2(y, base, augmented)
    assert result["delta_r2"] == pytest.approx(.19)
    a = module.paired_tract_bootstrap(y, base, augmented, replicates=200, seed=9)
    b = module.paired_tract_bootstrap(y, base, augmented, replicates=200, seed=9)
    assert a == b
    assert a["ci_95_percentile"][0] > 0.
