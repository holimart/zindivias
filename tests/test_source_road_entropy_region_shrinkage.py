from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_source_road_entropy_region_shrinkage.py"
spec = importlib.util.spec_from_file_location("source_road_entropy_region_shrinkage", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_four_region_deviation_parameterization_is_sum_zero():
    product = np.array([1., 2., 3., 4.])
    regions = np.asarray(module.REGIONS, dtype=object)
    design, contrasts = module.deviation_design(product, regions)
    assert design.shape == (4, 4)
    assert np.allclose(design[:, 0], product)
    assert contrasts.shape == (4, 3)
    coefficients = np.array([.2, -.1, .05])
    slopes = module.region_slopes(.4, coefficients)
    assert sum(slopes.values()) == pytest.approx(4 * .4)
    deviations = np.array([slopes[r] - .4 for r in module.REGIONS])
    assert deviations.sum() == pytest.approx(0.)


def test_fixed_penalty_applies_only_to_three_deviation_contrasts():
    penalty = module.deviation_penalty_matrix(11, 8, alpha=100.)
    assert np.allclose(penalty[:8], 0.)
    assert np.allclose(penalty[8:, 8:], 100. * (np.eye(3) + np.ones((3, 3))))
    assert np.linalg.eigvalsh(penalty[8:, 8:]).min() > 0.
    ids, regions, folds, y, features, mask = synthetic_inputs()
    with pytest.raises(ValueError, match="fixed at 100"):
        module.crossfit_region_shrinkage(ids, regions, folds, y, features, mask, alpha=1.)


def synthetic_inputs():
    rng = np.random.default_rng(241)
    regions = np.repeat(np.asarray(module.REGIONS, dtype=object), 50)
    folds = np.tile(np.arange(5), 40)
    features = rng.normal(size=(len(regions), 5))
    # Distinct region slopes force the fit to use the four-region interaction parameterization.
    product = features[:, 3] * features[:, 4]
    slopes = np.asarray([.5, -.3, .15, -.1])
    codes = np.asarray([module.REGIONS.index(r) for r in regions])
    y = .3 * features[:, 0] + slopes[codes] * product + rng.normal(0., .02, len(regions))
    geoid = [f"{i:011d}" for i in range(len(regions))]
    return geoid, regions, folds, y, features, np.ones(len(regions), dtype=bool)


def test_oof_predictions_ignore_heldout_outcomes_and_keep_paired_support():
    ids, regions, folds, y, x, mask = synthetic_inputs()
    original = module.crossfit_region_shrinkage(ids, regions, folds, y, x, mask)
    changed_y = y.copy(); changed_y[folds == 0] += 1000.
    changed = module.crossfit_region_shrinkage(ids, regions, folds, changed_y, x, mask)
    test = folds == 0
    assert np.allclose(original["baseline_oof"][test], changed["baseline_oof"][test])
    assert np.allclose(original["augmented_oof"][test], changed["augmented_oof"][test])
    assert np.array_equal(np.isfinite(original["baseline_oof"]), mask)
    assert np.array_equal(np.isfinite(original["augmented_oof"]), mask)
    assert len(original["fold_metrics"]) == 5
    assert all(row["alpha"] == 100. for row in original["fold_coefficients"])


def test_ridge_deviations_shrink_against_unpenalized_fit():
    ids, regions, folds, y, x, mask = synthetic_inputs()
    result = module.crossfit_region_shrinkage(ids, regions, folds, y, x, mask)
    for fold in result["fold_coefficients"]:
        assert fold["ridge_deviation_l2_norm"] <= fold["unpenalized_deviation_l2_norm"] + 1e-10
        assert 0. <= fold["ridge_to_unpenalized_deviation_norm_ratio"] <= 1. + 1e-10
