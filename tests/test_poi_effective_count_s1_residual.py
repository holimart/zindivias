from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_effective_count_s1_residual.py"
spec = importlib.util.spec_from_file_location("poi_effective_count_s1_residual", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def toy_fold():
    rng = np.random.default_rng(20261029)
    regions = np.repeat(np.asarray(module.REGIONS, dtype=object), 20)
    fold = np.tile(np.arange(5), 16)
    x = rng.normal(size=(len(regions), 3))
    poi = rng.normal(size=len(regions))
    y = np.asarray([.01 * module.REGIONS.index(r) for r in regions]) + .2 * x[:, 0] - .1 * x[:, 1] + .15 * poi + rng.normal(0, .01, len(regions))
    test = fold == 0
    train = ~test
    return y, x, poi, regions, train, test


def test_within_region_scaling_fits_only_training_rows():
    values = np.array([1., 3., 100., 10., 14., -100.])
    regions = np.array(["a", "a", "a", "b", "b", "b"], dtype=object)
    train = np.array([True, True, False, True, True, False])
    test = ~train
    z, stats = module.within_region_train_standardize(values, regions, train, test)
    assert stats["a"]["training_mean"] == 2.
    assert stats["a"]["training_sd"] == 1.
    assert stats["b"]["training_mean"] == 12.
    assert z[2] == pytest.approx(98.)
    assert z[5] == pytest.approx(-56.)
    altered = values.copy(); altered[test] *= 1000.
    _, stats2 = module.within_region_train_standardize(altered, regions, train, test)
    assert stats2 == stats


def test_fold_models_are_paired_and_heldout_target_is_not_used_for_fitting():
    y, x, poi, regions, train, test = toy_fold()
    before = module.fit_predict_fold(y, x, poi, regions, train, test)
    changed = y.copy(); changed[test] += 10_000.
    after = module.fit_predict_fold(changed, x, poi, regions, train, test)
    assert np.allclose(before["baseline_prediction"][test], after["baseline_prediction"][test])
    assert np.allclose(before["augmented_prediction"][test], after["augmented_prediction"][test])
    assert np.array_equal(np.isfinite(before["baseline_prediction"]), test)
    assert np.array_equal(np.isfinite(before["augmented_prediction"]), test)
    assert all(x["training_n"] > 0 for x in before["poi_training_scaler"].values())


def test_delta_r2_is_nested_paired_sse_difference():
    y = np.array([0., 1., 2., 3.])
    base = np.array([0.2, 1.2, 1.8, 2.8])
    aug = np.array([0., 1., 2., 3.])
    result = module.delta_r2(y, base, aug)
    assert result["delta_r2"] == pytest.approx((np.sum((y - base) ** 2) - np.sum((y - aug) ** 2)) / np.sum((y - y.mean()) ** 2))
    assert result["delta_r2"] > 0.


def test_paired_tract_bootstrap_is_reproducible():
    y = np.linspace(-1., 1., 100)
    base = np.zeros_like(y)
    aug = .1 * y
    a = module.paired_tract_bootstrap(y, base, aug, replicates=200, seed=31)
    b = module.paired_tract_bootstrap(y, base, aug, replicates=200, seed=31)
    assert a == b
    assert a["replicates"] == 200 and a["seed"] == 31
    assert a["ci_95_percentile"][0] > 0.


def test_frozen_folds_only_select_test_rows_and_all_inputs_align():
    y, x, poi, regions, train, test = toy_fold()
    bad_train = train.copy(); bad_train[0] = True
    with pytest.raises(ValueError, match="nonempty disjoint train/test masks"):
        module.fit_predict_fold(y, x, poi, regions, bad_train, test)
    with pytest.raises(ValueError, match="finite"):
        module.delta_r2(y, np.full_like(y, np.nan), np.zeros_like(y))
