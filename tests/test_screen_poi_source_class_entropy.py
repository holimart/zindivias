from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_source_class_entropy.py"
spec = importlib.util.spec_from_file_location("poi_source_class_entropy", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_five_class_entropy_is_normalized_and_preserves_zero_count_null():
    shares = np.array([[1., 0., 0., 0., 0.], [.2, .2, .2, .2, .2],
                       [.5, .5, 0., 0., 0.], [np.nan] * 5])
    entropy, defined = module.entropy_from_shares(shares, np.array([10., 20., 4., 0.]))
    assert defined.tolist() == [True, True, True, False]
    assert entropy[0] == pytest.approx(0.)
    assert entropy[1] == pytest.approx(1.)
    assert entropy[2] == pytest.approx(np.log(2.) / np.log(5.))
    assert np.isnan(entropy[3])


def test_entropy_rejects_invalid_class_partitions_or_mask_mismatch():
    with pytest.raises(ValueError, match="sum to one"):
        module.entropy_from_shares(np.array([[.4, .4, 0., 0., 0.]]), np.array([5.]))
    with pytest.raises(ValueError, match="positive POI-count support"):
        module.entropy_from_shares(np.array([[np.nan] * 5]), np.array([5.]))
    with pytest.raises(ValueError, match="positive finite POI count"):
        module.entropy_from_shares(np.array([[1., 0., 0., 0., 0.]]), np.array([0.]))


def test_crossfit_is_deterministic_and_preserves_undefined_rows():
    n = 60
    geoids = [f"{i:011d}" for i in range(n)]
    x = np.column_stack((np.linspace(0, 4, n), np.sin(np.arange(n)), np.cos(np.arange(n))))
    y = np.clip(.1 + .12 * x[:, 1] + .05 * x[:, 2], 0., 1.)
    mask = np.ones(n, dtype=bool)
    mask[[3, 8]] = False
    y[[3, 8]] = np.nan
    p1, r1, f1, folds1 = module.crossfit_region(geoids, "maricopa-az", y, x, mask)
    p2, r2, f2, folds2 = module.crossfit_region(geoids, "maricopa-az", y, x, mask)
    assert np.array_equal(folds1, folds2)
    assert np.array_equal(np.isfinite(r1), mask)
    assert np.array_equal(np.isfinite(p1), mask)
    assert np.allclose(p1[mask], p2[mask]) and np.allclose(r1[mask], r2[mask])
    assert np.array_equal(f1, f2) and len(folds1) == len(folds2) == 5
