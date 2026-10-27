from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_tm5_signed_version_urbanity.py"
spec = importlib.util.spec_from_file_location("tm5_signed_urbanity", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_signed_gap_preserves_joint_mask_and_sign():
    poi = np.array([.8, .1, np.nan])
    building = np.array([.3, .7, np.nan])
    joint = np.array([True, True, False])
    gap = module.signed_gap_from_ranks(poi, building, joint)
    assert gap[:2].tolist() == pytest.approx([.5, -.6])
    assert np.isnan(gap[2])
    bad_gap = gap.copy()
    bad_gap[2] = 0.
    with pytest.raises(ValueError, match="nulls must exactly"):
        module.summarize_groups([(str(i), module.REGIONS[0]) for i in range(3)], bad_gap,
                                np.array([True, True, False]), np.array([.1, .9, .5]))


def test_regional_stratum_means_and_max_absolute_contrast():
    official = []
    gap = []
    urbanity = []
    joint = []
    contrasts = [(.2, -.1), (.1, .0), (-.2, .05), (.4, -.3)]
    for region, (rural_mean, urban_mean) in zip(module.REGIONS, contrasts):
        for i in range(80):
            official.append((f"{len(official):011d}", region))
            rural = i < 40
            gap.append(rural_mean if rural else urban_mean)
            urbanity.append(.2 if rural else .8)
            joint.append(True)
    result = module.summarize_groups(official, np.array(gap), np.array(joint), np.array(urbanity))
    assert result["max_region"] == module.REGIONS[3]
    assert result["max_absolute_contrast"] == pytest.approx(.7)
    assert result["max_region_signed_contrast"] == pytest.approx(.7)
    assert result["regions_meeting_support"] == list(module.REGIONS)


def test_bootstrap_uses_seed_and_rejects_incomplete_region_set():
    groups = {region: (np.linspace(.1, .3, 40), np.linspace(-.2, .1, 45)) for region in module.REGIONS}
    a = module.bootstrap_contrasts(groups, replicates=250, seed=7)
    b = module.bootstrap_contrasts(groups, replicates=250, seed=7)
    assert a == b
    assert len(a["max_absolute_contrast_ci95_percentile"]) == 2
    assert set(a["regional_signed_contrast_ci95_percentile"]) == set(module.REGIONS)
    with pytest.raises(ValueError, match="all four official regions"):
        module.bootstrap_contrasts({module.REGIONS[0]: groups[module.REGIONS[0]]}, replicates=250)
