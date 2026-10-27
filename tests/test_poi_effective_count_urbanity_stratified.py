from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_effective_count_urbanity_stratified.py"
spec = importlib.util.spec_from_file_location("poi_effective_count_urbanity_stratified", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_fixed_urban_rural_threshold_and_null_support():
    pct = np.array([0., .4999, .5, 1., np.nan])
    defined = np.isfinite(pct)
    sign, urban = module.stratum_sign(pct, defined)
    assert np.array_equal(urban[defined], [False, False, True, True])
    assert np.array_equal(sign[defined], [-1., -1., 1., 1.])
    assert np.isnan(sign[~defined]).all()


def test_effective_count_scaler_uses_training_only_region_moments():
    x = np.array([0., 2., 200., 10., 14., -100.])
    regions = np.array(["a", "a", "a", "b", "b", "b"], dtype=object)
    train = np.array([True, True, False, True, True, False])
    test = ~train
    z, stats = module.standardized_effective_count(x, regions, train, test)
    assert stats["a"]["training_mean"] == 1.
    assert stats["b"]["training_mean"] == 12.
    changed = x.copy(); changed[test] *= 100.
    _, changed_stats = module.standardized_effective_count(changed, regions, train, test)
    assert changed_stats == stats
    assert z[2] == pytest.approx(199.)


def synthetic_fold():
    rng = np.random.default_rng(515)
    regions = np.repeat(np.asarray(module.REGIONS, dtype=object), 50)
    folds = np.tile(np.arange(5), 40)
    n = len(regions)
    base = rng.normal(size=(n, 3))
    poi = rng.normal(size=n)
    urban = rng.uniform(0., 1., n)
    sign, urban_flag = module.stratum_sign(urban)
    slopes = np.where(urban_flag, .3, -.2)
    y = .1 * base[:, 0] + .2 * urban + slopes * poi + rng.normal(0., .01, n)
    test = folds == 0
    return y, base, poi, urban, sign, regions, ~test, test


def test_nested_ridge_fold_uses_only_training_targets_and_preserves_strata_slopes():
    y, base, poi, urban, sign, regions, train, test = synthetic_fold()
    fit = module.fit_fold(y, base, poi, urban, sign, regions, train, test)
    changed_y = y.copy(); changed_y[test] += 1000.
    again = module.fit_fold(changed_y, base, poi, urban, sign, regions, train, test)
    assert np.allclose(fit["baseline_prediction"][test], again["baseline_prediction"][test])
    assert np.allclose(fit["stratified_prediction"][test], again["stratified_prediction"][test])
    assert fit["urban_slope"] == pytest.approx(fit["shared_effective_count_slope"] + fit["urban_deviation"])
    assert fit["rural_slope"] == pytest.approx(fit["shared_effective_count_slope"] - fit["urban_deviation"])
    assert fit["ridge_to_unpenalized_deviation_abs_ratio"] <= 1. + 1e-10
    assert np.array_equal(np.isfinite(fit["baseline_prediction"]), test)
    assert np.array_equal(np.isfinite(fit["stratified_prediction"]), test)
    with pytest.raises(ValueError, match="fixed at 100"):
        module.fit_fold(y, base, poi, urban, sign, regions, train, test, alpha=50.)


def test_county_bootstrap_is_paired_deterministic_and_clustered():
    county = np.repeat(np.array(["01001", "01003", "04001"], dtype=object), [8, 8, 8])
    y = np.linspace(-1., 1., len(county))
    base = np.zeros_like(y)
    full = .2 * y
    one = module.county_block_bootstrap(y, base, full, county, replicates=200, seed=31)
    two = module.county_block_bootstrap(y, base, full, county, replicates=200, seed=31)
    assert one == two
    assert one["cluster_count"] == 3
    assert one["ci_95_percentile"][0] > 0.
