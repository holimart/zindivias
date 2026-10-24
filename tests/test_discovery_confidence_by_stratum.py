from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import discovery_confidence_by_stratum as audit  # noqa: E402


def test_quartile_assignment_is_deterministic_and_ties_move_up() -> None:
    assigned, cuts = audit.assign_quartiles({
        "a": 0.0, "b": 0.0, "c": 0.0, "d": 0.25,
        "e": 0.5, "f": 0.75, "g": 0.75, "h": 1.0,
    })
    assert cuts == [0.0, 0.375, 0.75]
    assert assigned["a"] == 2  # tied at q25, assigned above the cut
    assert assigned["h"] == 4


def test_per_tract_summary_retains_empty_sample_tracts_and_quartiles() -> None:
    rows, summary = audit.tract_summary_records(
        "unit-region",
        ["a", "b", "c"],
        [np.asarray([0, 0, 1, 2, -1])],
        [np.asarray([0.1, 0.9, 0.3, 0.8, 0.7])],
        [np.asarray([False, False, False, False, False])],
        [np.asarray([False, False, False, False, False])],
        {
            "a": {"svi_overall": 0.1, "pct_urban": 0.1, "svi_covered": True},
            "b": {"svi_overall": 0.5, "pct_urban": 0.8, "svi_covered": True},
            "c": {"svi_overall": None, "pct_urban": None, "svi_covered": True},
        },
    )
    assert [row["GEOID"] for row in rows] == ["a", "b", "c"]
    assert rows[0]["poi_count"] == 2
    assert rows[0]["confidence_below_0_5_count"] == 1
    assert rows[0]["confidence_below_0_5_share"] == 0.5
    assert rows[0]["confidence_q50"] == 0.5
    assert rows[2]["poi_count"] == 1 and rows[2]["svi_quartile"] is None
    assert summary["unmatched_poi_rows"] == 1
    assert summary["tracts_missing_svi_overall"] == 1
    assert summary["overall_confidence_quantiles_q10_q25_q50_q75_q90"]["q50"] == 0.55


def test_cluster_bootstrap_share_is_seeded_and_bounded() -> None:
    low = np.asarray([1, 0, 2, 0], dtype=np.int64)
    total = np.asarray([2, 1, 3, 2], dtype=np.int64)
    first = audit.bootstrap_share(low, total, seed=71, replicates=200)
    second = audit.bootstrap_share(low, total, seed=71, replicates=200)
    assert np.array_equal(first, second)
    assert np.all(np.isfinite(first))
    assert np.all((first >= 0) & (first <= 1))
