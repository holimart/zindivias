from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_tm5_poi_region_rank_mx4_interaction.py"
spec = importlib.util.spec_from_file_location("tm5_region_rank_mx4", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_product_is_rank_times_mx4_on_exact_joint_mask():
    product, defined = module.make_interaction(
        np.array([.2, .75, .5, np.nan]), np.array([True, True, True, False]),
        np.array([.5, .4, np.nan, .7]), np.array([True, True, False, True]),
    )
    assert defined.tolist() == [True, True, False, False]
    assert np.allclose(product[:2], [.1, .3])
    assert np.isnan(product[2:]).all()


def test_interaction_rejects_bad_values_and_mask_mismatch():
    with pytest.raises(ValueError, match="bounded"):
        module.make_interaction(np.array([1.2]), np.array([True]), np.array([.5]), np.array([True]))
    with pytest.raises(ValueError, match="bounded"):
        module.make_interaction(np.array([.5]), np.array([True]), np.array([1.2]), np.array([True]))
    with pytest.raises(ValueError, match="undefined ranks remain null"):
        module.make_interaction(np.array([.5]), np.array([False]), np.array([.5]), np.array([True]))


def test_crossfit_returns_out_of_fold_residuals_and_stable_fold_ids():
    geoids = [f"g{i}" for i in range(25)]
    x1 = np.linspace(0., 1., 25)
    x2 = np.sin(np.arange(25))
    y = .1 + .4 * x1 - .2 * x2 + .05 * np.cos(np.arange(25))
    mask = np.ones(25, dtype=bool)
    pred, residual, folds_report, folds = module.crossfit_region(geoids, "maricopa-az", y, x1, x2, mask)
    assert np.isfinite(pred).all() and np.isfinite(residual).all()
    assert len(folds_report) == 5
    assert np.array_equal(folds, module.deterministic_folds(geoids, "maricopa-az"))
    assert np.allclose(y - pred, residual)
