from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_category_effective_count.py"
spec = importlib.util.spec_from_file_location("poi_category_effective_count", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_effective_count_is_exp_shannon_and_handles_one_or_many_categories():
    counters = [Counter({"one": 10}), Counter({str(i): 1 for i in range(100)}),
                Counter({"a": 4, "b": 6}), Counter({"a": 2})]
    result = module.effective_count_by_tract(counters, np.array([10, 100, 10, 9]))
    assert result["defined"].tolist() == [True, True, True, False]
    assert result["effective_count"][0] == pytest.approx(1.)
    assert result["effective_count"][1] == pytest.approx(100.)
    assert result["effective_count"][2] == pytest.approx(np.exp(-(0.4 * np.log(.4) + .6 * np.log(.6))))
    assert np.isnan(result["effective_count"][3])
    assert result["primary_categorized_place_counts"].tolist() == [10, 100, 10, 2]


def test_effective_count_requires_minimum_unique_places_and_a_primary_category():
    result = module.effective_count_by_tract([Counter(), Counter({"a": 8}), Counter({"a": 10})],
                                             np.array([20, 9, 10]))
    assert result["defined"].tolist() == [False, False, True]
    assert np.isnan(result["effective_count"][0])
    assert result["effective_count"][2] == pytest.approx(1.)


def test_effective_count_rejects_misaligned_or_invalid_counts():
    with pytest.raises(ValueError, match="align"):
        module.effective_count_by_tract([Counter()], np.array([10, 11]))
    with pytest.raises(ValueError, match="nonnegative integers"):
        module.effective_count_by_tract([Counter({"bad": -1})], np.array([10]))


def test_crossfit_includes_log_count_and_preserves_exact_null_support():
    n = 100
    ids = [f"{i:011d}" for i in range(n)]
    region = "maricopa-az"
    log_count = np.linspace(1., 4., n)
    mx4 = np.sin(np.arange(n) / 8.)
    s1 = np.cos(np.arange(n) / 11.)
    target = 1.3 + .2 * log_count + .1 * mx4 - .3 * s1
    defined = np.ones(n, dtype=bool)
    defined[0] = False
    target[0] = np.nan
    prediction, residual, reports, folds = module.crossfit_region(
        ids, region, target, log_count, mx4, s1, defined)
    assert np.array_equal(np.isfinite(residual), defined)
    assert np.array_equal(np.isfinite(prediction), defined)
    assert len(reports) == 5
    assert all(report["coefficient_order"] == ["region_intercept", "log1p_unique_place_count", "Mx4_F", "S1_lagged_Mx4_F"]
               for report in reports)
    assert folds.shape == (n,)
    assert np.all((folds >= 0) & (folds < 5))
    with pytest.raises(ValueError, match="align"):
        module.crossfit_region(ids, region, target[:-1], log_count, mx4, s1, defined)
