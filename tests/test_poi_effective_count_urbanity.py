from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_effective_count_urbanity.py"
spec = importlib.util.spec_from_file_location("poi_effective_count_urbanity", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_fold():
    rng = np.random.default_rng(20261030)
    regions = np.repeat(np.asarray(module.REGIONS, dtype=object), 25)
    folds = np.tile(np.arange(5), 20)
    base = rng.normal(size=(len(regions), 3))
    log_poi = rng.normal(size=len(regions))
    urban = rng.uniform(0., 1., len(regions))
    interaction = log_poi * urban
    y = np.asarray([.1 * module.REGIONS.index(r) for r in regions]) + .2 * base[:, 0] + .1 * log_poi + .15 * urban + .3 * interaction + rng.normal(0., .01, len(regions))
    test = folds == 0
    return y, base, log_poi, urban, regions, ~test, test


def test_effective_count_scaler_uses_training_only_within_each_region():
    x = np.array([1., 3., 90., 10., 14., -80.])
    regions = np.array(["r1", "r1", "r1", "r2", "r2", "r2"], dtype=object)
    train = np.array([True, True, False, True, True, False])
    test = ~train
    scaled, stats = module.scale_poi_within_region_train_only(x, regions, train, test)
    assert stats["r1"]["training_mean"] == 2.
    assert stats["r1"]["training_sd"] == 1.
    assert stats["r2"]["training_mean"] == 12.
    assert scaled[2] == pytest.approx(88.)
    assert scaled[5] == pytest.approx(-46.)
    changed = x.copy(); changed[test] *= 1000.
    _, changed_stats = module.scale_poi_within_region_train_only(changed, regions, train, test)
    assert changed_stats == stats


def test_interaction_design_contains_both_main_effects_and_one_product():
    base = np.array([[1., 2.], [3., 4.]])
    poi = np.array([.5, 1.5])
    urban = np.array([.2, .8])
    product = poi * urban
    regions = np.array([module.REGIONS[0], module.REGIONS[1]], dtype=object)
    code = {name: i for i, name in enumerate(module.REGIONS)}
    main, full = module.make_design(base, poi, urban, product, regions, code)
    assert full.shape[1] == main.shape[1] + 1
    assert np.array_equal(full[:, :-1], main)
    assert np.allclose(full[:, -1], [0.1, 1.2])


def test_nested_fold_predictions_use_no_heldout_outcomes_and_are_paired():
    y, base, poi, urban, regions, train, test = synthetic_fold()
    before = module.fit_fold(y, base, poi, urban, regions, train, test)
    changed = y.copy(); changed[test] += 1000.
    after = module.fit_fold(changed, base, poi, urban, regions, train, test)
    assert np.allclose(before["main_prediction"][test], after["main_prediction"][test])
    assert np.allclose(before["interaction_prediction"][test], after["interaction_prediction"][test])
    assert np.array_equal(np.isfinite(before["main_prediction"]), test)
    assert np.array_equal(np.isfinite(before["interaction_prediction"]), test)


def test_delta_r2_is_added_interaction_sse_reduction():
    y = np.array([-1., 0., 1., 2.])
    main = np.array([-.8, .1, .9, 2.1])
    full = np.array([-1., 0., 1., 2.])
    result = module.delta_r2(y, main, full)
    assert result["delta_r2"] == pytest.approx(
        (np.sum((y - main) ** 2) - np.sum((y - full) ** 2)) / np.sum((y - y.mean()) ** 2))
    assert result["delta_r2"] > 0.


def test_bootstrap_is_deterministic_and_rejects_nonfinite_data():
    y = np.linspace(-1., 1., 120)
    main = np.zeros_like(y)
    full = .2 * y
    a = module.paired_tract_bootstrap(y, main, full, replicates=200, seed=77)
    b = module.paired_tract_bootstrap(y, main, full, replicates=200, seed=77)
    assert a == b
    assert a["ci_95_percentile"][0] > 0.
    with pytest.raises(ValueError, match="finite"):
        module.delta_r2(y, np.full_like(y, np.nan), full)
