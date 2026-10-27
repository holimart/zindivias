from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_s1_orthogonalized_rural_q90.py"
spec = importlib.util.spec_from_file_location("s1_rural_q90", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_region_q90_gaps_are_signed_and_equal_region_weighted():
    official = []
    residual = []
    pct_urban = []
    folds = []
    for region, rural_abs, urban_abs, n in ((module.REGIONS[0], 2., 1., 80),
                                            (module.REGIONS[1], 1., 3., 120),
                                            (module.REGIONS[2], 4., 2., 160),
                                            (module.REGIONS[3], 1., 1., 200)):
        for i in range(n):
            official.append((f"{len(official):011d}", region))
            is_rural = i < n // 2
            residual.append(rural_abs if is_rural else -urban_abs)
            pct_urban.append(.1 if is_rural else .9)
            folds.append(i % 5)
    result = module.q90_gap_by_region(official, np.array(residual), np.ones(len(official), bool),
                                      np.array(pct_urban), np.array(folds))
    expected = (1. + -2. + 2. + 0.) / 4.
    assert result["equal_region_signed_gap"] == pytest.approx(expected)
    assert all(item["both_strata_n_at_least_30"] for item in result["by_region"].values())
    assert result["by_region"][module.REGIONS[0]]["fixed_fold_counts"] == {str(i): 16 for i in range(5)}


def test_missing_urbanity_is_excluded_and_residual_nulls_are_preserved():
    official = []
    residual = []
    urbanity = []
    folds = []
    for region in module.REGIONS:
        for i in range(80):
            official.append((f"{len(official):011d}", region))
            residual.append((i - 40) / 4000.)
            urbanity.append(np.nan if i == 79 else (.1 if i < 40 else .9))
            folds.append(i % 5)
    residual = np.asarray(residual)
    urbanity = np.asarray(urbanity)
    folds = np.asarray(folds)
    mask = np.ones(len(official), bool)
    result = module.q90_gap_by_region(official, residual, mask, urbanity, folds)
    assert all(item["pct_urban_defined_ids"] == 79 for item in result["by_region"].values())
    bad = residual.copy()
    bad[0] = np.nan
    with pytest.raises(ValueError, match="residual nulls"):
        module.q90_gap_by_region(official, bad, mask, urbanity, folds)


def test_bootstrap_is_deterministic_and_rejects_incomplete_regions():
    groups = {region: (np.linspace(.1, .2, 40), np.linspace(.05, .15, 45)) for region in module.REGIONS}
    a = module.bootstrap_equal_region_gap(groups, replicates=200, seed=19)
    b = module.bootstrap_equal_region_gap(groups, replicates=200, seed=19)
    assert a == b
    assert len(a["ci_95_percentile"]) == 2
    with pytest.raises(ValueError, match="all four official regions"):
        module.bootstrap_equal_region_gap({module.REGIONS[0]: groups[module.REGIONS[0]]}, replicates=200)
