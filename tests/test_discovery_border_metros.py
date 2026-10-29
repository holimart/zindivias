from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/discovery/border_metros.py"
SPEC = importlib.util.spec_from_file_location("border_metros", SCRIPT)
assert SPEC and SPEC.loader
border_metros = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(border_metros)


def test_group_summary_keeps_tract_means_separate_from_footprint_weighting():
    rows = [
        {"buildings": 100, "other_share": 0.5, "osm_share": 0.0,
         "ms_only_share": 0.4, "population": 1000, "uhe_days": 50.0},
        {"buildings": 300, "other_share": 0.1, "osm_share": 0.2,
         "ms_only_share": 0.6, "population": 3000, "uhe_days": None},
    ]
    summary = border_metros.summarize(rows)
    assert summary["tracts"] == 2
    assert summary["population"] == 4000
    assert summary["osm_share_mean_unweighted_tracts"] == 0.1
    assert summary["microsoft_only_share_mean_unweighted_tracts"] == 0.5
    assert summary["other_source_footprints_rounded"] == 80
    assert summary["other_source_weighted_share"] == 0.2
    assert summary["osm_below_5pct_tracts"] == 1
    assert summary["population_in_osm_below_5pct"] == 1000
    assert summary["uhe_wbgt30_mean_days"] == 50.0
    assert summary["uhe_tracts"] == 1


def test_tract_bootstrap_is_seeded_and_difference_direction_is_group1_minus_group0():
    a = [0.1, 0.2, 0.3]
    b = [0.5, 0.6, 0.7]
    result = border_metros.bootstrap_difference_ci(a, b, replicates=100, seed=7)
    assert np.isclose(result["difference"], -0.4)
    assert result == border_metros.bootstrap_difference_ci(a, b, replicates=100, seed=7)
    assert result["ci_95"][0] <= result["difference"] <= result["ci_95"][1]


def test_county_bootstrap_and_clustered_ols_return_expected_shapes():
    rows = []
    for i in range(12):
        border = i < 6
        rows.append({"county_fips": f"{i // 2:05d}", "is_border": border,
                     "osm_share": (0.1 if border else 0.6) + (i % 2) * 0.01,
                     "density": 1.0 + i, "log_density": np.log1p(1.0 + i),
                     "pct_urban": 0.5 + (i % 3) * 0.1,
                     "svi_overall": 0.2 + (i % 4) * 0.1,
                     "ep_hisp": 10.0 + i})
    cluster = border_metros.cluster_bootstrap_difference(rows, replicates=100, seed=3)
    assert cluster["cluster_count"] == 6
    assert cluster["difference"] < 0
    ols = border_metros.cluster_ols(rows, ("pct_urban", "log_density"))
    assert ols["n"] == 12
    assert ols["counties"] == 6
    assert ols["border_coefficient"] < 0
    assert ols["county_clustered_se"] >= 0
