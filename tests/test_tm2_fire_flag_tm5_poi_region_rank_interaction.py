from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_tm2-fire-flag_tm5-poi-region-rank-interaction.py"
spec = importlib.util.spec_from_file_location("tm2_flag_tm5_region_rank", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_region_rank_interaction_multiplies_flag_and_preserves_joint_mask():
    result = module.rank_interaction(
        np.array([1., 0., 1., np.nan]), np.array([True, True, True, False]),
        np.array([5., 8., np.nan, 2.]), np.array([True, True, False, True]),
        np.array([.2, .8, np.nan, .4]),
    )
    assert result["defined"].tolist() == [True, True, False, False]
    assert np.allclose(result["interaction"][:2], [.2, 0.])
    assert np.allclose(result["change"][:2], [.8, 0.])
    assert np.isnan(result["interaction"][2:]).all()
    assert np.isnan(result["change"][2:]).all()


def test_rank_interaction_validates_bounds_and_binary_flags():
    with pytest.raises(ValueError, match="binary"):
        module.rank_interaction(np.array([2.]), np.array([True]), np.array([1.]),
                                np.array([True]), np.array([.5]))
    with pytest.raises(ValueError, match=r"rank.*\[0,1\]"):
        module.rank_interaction(np.array([1.]), np.array([True]), np.array([1.]),
                                np.array([True]), np.array([1.1]))
    with pytest.raises(ValueError, match="undefined recovery flags must remain null"):
        module.rank_interaction(np.array([0.]), np.array([False]), np.array([1.]),
                                np.array([True]), np.array([.5]))


def test_region_midpercentile_uses_average_ties_and_keeps_nulls():
    ranks = module.empirical_midpercentile(np.array([1., 2., 2., np.nan]),
                                           np.array([True, True, True, False]))
    assert np.allclose(ranks[:3], [0., .75, .75])
    assert np.isnan(ranks[3])
