from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_s1_orthogonalized_rural_strata import rural_interaction  # noqa: E402


def test_rural_interaction_uses_pct_urban_threshold_and_absolute_median_gap():
    result = rural_interaction(
        np.array([-.004, .002, .001, -.003, .5]),
        np.array([True, True, True, True, True]),
        np.array([.1, .2, .8, .5, np.nan]),
    )
    assert result["rural"].tolist() == [True, True, False, False, False]
    assert result["urban"].tolist() == [False, False, True, True, False]
    assert result["groups"]["rural"]["n"] == 2
    assert result["groups"]["urban"]["n"] == 2
    assert result["groups"]["rural"]["median_absolute_residual"] == pytest.approx(.003)
    assert result["groups"]["urban"]["median_absolute_residual"] == pytest.approx(.002)
    assert result["absolute_median_gap"] == pytest.approx(.001)
    assert np.allclose(result["interaction"][:4], [-.004, .002, 0., 0.])
    assert np.isnan(result["interaction"][4])


def test_rural_interaction_preserves_nulls_and_excludes_missing_mapping():
    result = rural_interaction(
        np.array([.2, np.nan, -.1, .4]),
        np.array([True, False, True, True]),
        np.array([.1, .1, np.nan, .9]),
    )
    assert result["joint_defined"].tolist() == [True, False, False, True]
    assert np.allclose(result["interaction"][[0, 3]], [.2, 0.])
    assert np.isnan(result["interaction"][[1, 2]]).all()
    assert result["groups"]["rural"]["n"] == 1
    assert result["groups"]["urban"]["n"] == 1
    assert result["residual_defined_without_urbanity"] == 1
    assert result["urbanity_defined_without_residual"] == 1


def test_rural_interaction_validates_alignment_and_residual_masks():
    with pytest.raises(ValueError, match="align"):
        rural_interaction(np.array([.1]), np.array([True]), np.array([.1, .2]))
    with pytest.raises(ValueError, match="defined residuals must be finite"):
        rural_interaction(np.array([np.nan]), np.array([True]), np.array([.1]))
    with pytest.raises(ValueError, match="undefined residuals must remain null"):
        rural_interaction(np.array([.1]), np.array([False]), np.array([.1]))
    with pytest.raises(ValueError, match="pct_urban values must be in \\[0,1\\]"):
        rural_interaction(np.array([.1]), np.array([True]), np.array([1.2]))
