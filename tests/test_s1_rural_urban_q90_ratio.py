from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_s1_rural_urban_q90_ratio.py"
spec = importlib.util.spec_from_file_location("s1_rural_urban_q90_ratio", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_inputs(small_region: str | None = None):
    official = []
    residual = []
    urban = []
    folds = []
    for region_index, region in enumerate(module.REGIONS):
        n = 12 if region == small_region else 50
        for rural in (True, False):
            for i in range(n):
                official.append((f"{region_index}{int(rural)}{i:09d}", region))
                # Rural q90 is exactly half the urban q90 in each region.
                residual.append((i + 1) * (1. if rural else 2.))
                urban.append(.25 if rural else .75)
                folds.append(i % 5)
    return (official, np.asarray(residual), np.ones(len(residual), dtype=bool),
            np.asarray(urban), np.asarray(folds, dtype=np.int8))


def test_region_q90_ratios_and_equal_region_log_ratio():
    args = synthetic_inputs()
    result = module.q90_ratio_by_region(*args)
    assert result["regions_with_at_least_30_per_stratum"] == 4
    assert result["support_gate_pass"] is True
    assert result["equal_region_mean_log_ratio"] == pytest.approx(np.log(.5))
    for region in module.REGIONS:
        row = result["by_region"][region]
        assert row["rural_to_urban_q90_ratio"] == pytest.approx(.5)
        assert row["log_rural_to_urban_q90_ratio"] == pytest.approx(np.log(.5))
        assert row["rural_n"] == row["urban_n"] == 50


def test_support_gate_allows_three_of_four_regions():
    result = module.q90_ratio_by_region(*synthetic_inputs(small_region="northern-ca"))
    assert result["regions_with_at_least_30_per_stratum"] == 3
    assert result["support_gate_pass"] is True
    assert result["by_region"]["northern-ca"]["both_strata_n_at_least_30"] is False


def test_stratified_bootstrap_is_reproducible_and_excludes_zero_for_constant_ratio():
    groups = {region: (np.ones(40), np.full(40, 2.)) for region in module.REGIONS}
    first = module.bootstrap_equal_region_log_ratio(groups, replicates=200, seed=202)
    second = module.bootstrap_equal_region_log_ratio(groups, replicates=200, seed=202)
    assert first == second
    assert first["ci_excludes_zero"] is True
    assert first["ci_95_percentile"] == pytest.approx([np.log(.5), np.log(.5)])


def test_rejects_bad_null_alignment_and_zero_denominator_q90():
    official, residual, defined, urban, folds = synthetic_inputs()
    bad = residual.copy()
    bad[0] = np.nan
    with pytest.raises(ValueError, match="nulls do not match"):
        module.q90_ratio_by_region(official, bad, defined, urban, folds)
    groups = {region: (np.zeros(40), np.ones(40)) for region in module.REGIONS}
    with pytest.raises(ValueError, match="q90 reached zero"):
        module.bootstrap_equal_region_log_ratio(groups, replicates=100, seed=8)
