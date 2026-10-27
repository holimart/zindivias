from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_category_entropy.py"
spec = importlib.util.spec_from_file_location("poi_category_entropy", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_entropy_is_normalized_over_75_and_requires_ten_unique_places():
    counts = np.zeros((4, 75), dtype=np.int64)
    counts[0, 0] = 10
    counts[1, :] = 1
    counts[2, 0] = 4
    counts[2, 1] = 6
    counts[3, 0] = 5
    result = module.entropy_by_tract(counts, np.array([10, 75, 10, 9]))
    assert result["defined"].tolist() == [True, True, True, False]
    assert result["entropy"][0] == pytest.approx(0.)
    assert result["entropy"][1] == pytest.approx(1.)
    assert result["entropy"][2] == pytest.approx(-(0.4 * np.log(.4) + .6 * np.log(.6)) / np.log(75))
    assert np.isnan(result["entropy"][3])


def test_fixed_75_normalizer_is_not_clipped_when_observed_support_exceeds_75():
    counts = np.ones((1, 100), dtype=np.int64)
    result = module.entropy_by_tract(counts, np.array([100]))
    assert result["entropy"][0] == pytest.approx(np.log(100) / np.log(75))
    assert result["entropy"][0] > 1.0


def test_entropy_keeps_tract_undefined_when_no_primary_category_is_available():
    result = module.entropy_by_tract(np.zeros((1, 75), dtype=np.int64), np.array([12]))
    assert result["defined"].tolist() == [False]
    assert np.isnan(result["entropy"][0])


def test_entropy_rejects_wrong_category_support_or_negative_counts():
    with pytest.raises(ValueError, match="shape"):
        module.entropy_by_tract(np.zeros((1, 0), dtype=np.int64), np.array([10]))
    counts = np.zeros((1, 75), dtype=np.int64)
    counts[0, 0] = -1
    with pytest.raises(ValueError, match="nonnegative"):
        module.entropy_by_tract(counts, np.array([10]))
