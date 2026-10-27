from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_effective_count_upper_tail.py"
spec = importlib.util.spec_from_file_location("poi_effective_count_upper_tail", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_q90_hinge_uses_training_quantile_only():
    x = np.array([0., 1., 2., 3., 4., 100.])
    train = np.array([True, True, True, True, True, False])
    test = ~train
    hinge, stats = module.q90_hinge_train_only(x, train, test)
    assert stats["training_q90_threshold"] == pytest.approx(3.6)
    assert hinge[-1] == pytest.approx(96.4)
    changed = x.copy(); changed[-1] = -1000.
    _, changed_stats = module.q90_hinge_train_only(changed, train, test)
    assert changed_stats == stats


def test_q90_hinge_rejects_overlapping_masks():
    with pytest.raises(ValueError, match="disjoint"):
        module.q90_hinge_train_only(np.arange(4.), np.array([1, 1, 0, 0], bool),
                                    np.array([0, 1, 1, 0], bool))


def synthetic_fold():
    rng = np.random.default_rng(20261031)
    regions = np.repeat(np.asarray(module.REGIONS, dtype=object), 25)
    folds = np.tile(np.arange(5), 20)
    base = rng.normal(size=(len(regions), 3))
    log_count = rng.normal(size=len(regions))
    hinge = np.maximum(log_count - np.quantile(log_count[folds != 0], .9), 0.)
    y = np.asarray([.1 * module.REGIONS.index(r) for r in regions]) + .2 * base[:, 0] + .3 * hinge + rng.normal(0., .01, len(regions))
    test = folds == 0
    return y, base, log_count, regions, ~test, test


def test_fold_threshold_and_predictions_are_training_only_and_paired():
    y, base, count, regions, train, test = synthetic_fold()
    original = module.fit_fold(y, base, count, regions, train, test)
    changed = count.copy(); changed[test] += 1000.
    changed_y = y.copy(); changed_y[test] -= 1000.
    again = module.fit_fold(changed_y, base, changed, regions, train, test)
    assert original["training_threshold"] == again["training_threshold"]
    assert np.allclose(original["baseline_prediction"][test], again["baseline_prediction"][test])
    # Test feature values change the hinge feature itself, but not fitted coefficients/predictions
    # when held-out predictions use the changed feature; this check isolates outcome leakage.
    only_y_changed = module.fit_fold(changed_y, base, count, regions, train, test)
    assert np.allclose(original["hinge_prediction"][test], only_y_changed["hinge_prediction"][test])
    assert np.array_equal(np.isfinite(original["baseline_prediction"]), test)
    assert np.array_equal(np.isfinite(original["hinge_prediction"]), test)


def test_county_block_bootstrap_resamples_whole_clusters_deterministically():
    county = np.repeat(np.array(["01001", "01003", "04001", "04003"], dtype=object), [8, 7, 6, 9])
    y = np.linspace(-1., 1., len(county))
    base = np.zeros_like(y)
    aug = .2 * y
    a = module.county_block_bootstrap(y, base, aug, county, replicates=200, seed=11)
    b = module.county_block_bootstrap(y, base, aug, county, replicates=200, seed=11)
    assert a == b
    assert a["cluster_count"] == 4
    assert a["ci_95_percentile"][0] > 0.
    with pytest.raises(ValueError, match="at least two counties"):
        module.county_block_bootstrap(y, base, aug, np.repeat("x", len(y)), replicates=200)
