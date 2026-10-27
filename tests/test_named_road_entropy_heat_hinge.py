from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_named_road_entropy_heat_hinge.py"
spec = importlib.util.spec_from_file_location("named_road_entropy_heat_hinge", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_inputs():
    rng = np.random.default_rng(1302)
    n = 500
    ids = [f"{i:011d}" for i in range(n)]
    folds = np.arange(n, dtype=np.int8) % 5
    heat = rng.normal(15., 2., n)
    x = rng.normal(size=(n, 4))
    knot = np.quantile(heat, .75)
    entropy = .4 + .08 * x[:, 0] - .04 * x[:, 1] + .03 * x[:, 2] + .02 * x[:, 3]
    entropy += .025 * np.maximum(heat - knot, 0.) + rng.normal(0., .01, n)
    mask = np.ones(n, dtype=bool)
    mask[[2, 19, 231, 404]] = False
    entropy[~mask] = np.nan
    heat[~mask] = np.nan
    return ids, folds, entropy, heat, x, mask


def test_heat_hinge_is_zero_through_training_q75_and_positive_above():
    values = np.array([2., 3., 4., 5.])
    assert np.array_equal(module.heat_hinge(values, 4.), np.array([0., 0., 0., 1.]))
    with pytest.raises(ValueError, match="must be finite"):
        module.heat_hinge(np.array([1., np.nan]), 1.)


def test_crossfit_q75_is_training_only_and_oof_masks_are_exact():
    ids, folds, entropy, heat, x, mask = synthetic_inputs()
    result = module.crossfit_region(ids, "maricopa-az", folds, entropy, heat, x, mask)
    base, hinge, diagnostics, slopes, used_folds, knots = result
    assert np.array_equal(used_folds, folds)
    assert np.array_equal(np.isfinite(base), mask)
    assert np.array_equal(np.isfinite(hinge), mask)
    assert np.array_equal(np.isfinite(knots), mask)
    assert len(diagnostics) == len(slopes) == 5
    for row in diagnostics:
        training = mask & (folds != row["fold"])
        heldout = mask & (folds == row["fold"])
        expected_knot = np.quantile(heat[training], .75)
        assert row["training_heat_q75_knot"] == pytest.approx(expected_knot)
        assert np.allclose(knots[heldout], expected_knot)
        assert row["heldout_heat_above_training_q75_n"] == int(np.count_nonzero(heat[heldout] > expected_knot))
    assert np.all(np.isfinite(slopes))


def test_changing_heldout_heat_cannot_change_its_training_q75_knot():
    ids, folds, entropy, heat, x, mask = synthetic_inputs()
    before = module.crossfit_region(ids, "eastern-ok", folds, entropy, heat, x, mask)
    changed = heat.copy()
    changed[(folds == 0) & mask] += 10_000.
    after = module.crossfit_region(ids, "eastern-ok", folds, entropy, changed, x, mask)
    assert before[2][0]["training_heat_q75_knot"] == after[2][0]["training_heat_q75_knot"]
    assert np.allclose(before[5][folds == 0], after[5][folds == 0], equal_nan=True)


def test_paired_bootstrap_is_reproducible_and_bad_folds_rejected():
    ids, folds, entropy, heat, x, mask = synthetic_inputs()
    base, hinge, _, _, _, _ = module.crossfit_region(ids, "south-central-tx", folds, entropy, heat, x, mask)
    y = entropy[mask]
    first = module.bootstrap_region_delta(y, base[mask], hinge[mask], seed=817, replicates=200)
    second = module.bootstrap_region_delta(y, base[mask], hinge[mask], seed=817, replicates=200)
    assert np.array_equal(first, second)
    bad_folds = folds.copy(); bad_folds[0] = 9
    with pytest.raises(ValueError, match="fold IDs"):
        module.crossfit_region(ids, "south-central-tx", bad_folds, entropy, heat, x, mask)
