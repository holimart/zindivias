from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_tm5_signed_poi_building_version_asymmetry.py"
spec = importlib.util.spec_from_file_location("tm5_signed_version_asymmetry", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_signed_gap_retains_direction_and_exact_joint_mask():
    gap, joint = module.signed_gap(
        np.array([.8, .2, .5, np.nan]), np.array([.3, .7, np.nan, .4]),
        np.array([True, True, True, False]), np.array([True, True, False, True]),
    )
    assert joint.tolist() == [True, True, False, False]
    assert np.allclose(gap[:2], [.5, -.5])
    assert np.isnan(gap[2:]).all()


def test_signed_gap_rejects_mismatched_masks_and_unbounded_ranks():
    with pytest.raises(ValueError, match="joint mask"):
        module.signed_gap(np.array([.5]), np.array([.5]), np.array([True]), np.array([True]), np.array([False]))
    with pytest.raises(ValueError, match="POI rank"):
        module.signed_gap(np.array([1.1]), np.array([.5]), np.array([True]), np.array([True]))


def test_mean_ci_is_deterministic_and_reports_descriptive_method():
    values = np.array([-.5, -.2, .1, .4, .7])
    first = module.bootstrap_mean_ci(values, seed=37, replicates=500)
    second = module.bootstrap_mean_ci(values, seed=37, replicates=500)
    assert first == second
    assert first["mean"] == pytest.approx(.1)
    assert first["ci95_percentile"][0] <= first["mean"] <= first["ci95_percentile"][1]
    assert "not spatial-cluster inference" in first["ci_method"]
