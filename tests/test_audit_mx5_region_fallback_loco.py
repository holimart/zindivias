from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx5_region_fallback_loco import (  # noqa: E402
    leave_one_county_out,
    median,
    output_table,
    parquet_bytes,
    summarize,
)


def test_county_deletion_recomputes_only_from_remaining_original_donors():
    donor_ids = [f"donor-{i}" for i in range(30)]
    counties = [f"{i // 10:03d}" for i in range(30)]
    values = np.array([0.1] * 10 + [0.5] * 10 + [0.9] * 10)
    targets = ["missing-a", "missing-b"]
    base = np.array([0.5, 0.5])
    rows = leave_one_county_out(donor_ids, counties, values, targets, base)
    assert len(rows) == 6
    by_county = {row["dropped_COUNTYFP"]: row for row in rows if row["GEOID"] == "missing-a"}
    assert by_county["000"]["remaining_observed_donor_count"] == 20
    assert by_county["000"]["recomputed_region_median"] == pytest.approx(0.7)
    assert by_county["001"]["recomputed_region_median"] == pytest.approx(0.5)
    assert by_county["002"]["recomputed_region_median"] == pytest.approx(0.3)
    assert not by_county["000"]["stable_recomputation"]
    assert by_county["001"]["stable_recomputation"]
    assert not by_county["002"]["stable_recomputation"]


def test_dropping_influential_county_flags_unstable_value_and_records_shift():
    donor_ids = [f"donor-{i}" for i in range(30)]
    counties = ["001"] * 10 + ["002"] * 20
    values = np.array([0.9] * 10 + [0.1] * 20)
    rows = leave_one_county_out(donor_ids, counties, values, ["missing"], np.array([0.1]))
    dropped_001 = next(row for row in rows if row["dropped_COUNTYFP"] == "001")
    assert dropped_001["remaining_observed_donor_count"] == 20
    assert dropped_001["recomputed_region_median"] == pytest.approx(0.1)
    assert dropped_001["absolute_shift"] == pytest.approx(0.0)
    dropped_002 = next(row for row in rows if row["dropped_COUNTYFP"] == "002")
    assert dropped_002["recomputed_region_median"] == pytest.approx(0.9)
    assert dropped_002["absolute_shift"] == pytest.approx(0.8)
    assert not dropped_002["stable_recomputation"]


def test_every_recomputation_must_leave_twenty_observed_donors():
    donor_ids = [f"donor-{i}" for i in range(25)]
    counties = ["001"] * 6 + [f"{i:03d}" for i in range(2, 21)]
    values = np.array([0.4] * 25)
    rows = leave_one_county_out(donor_ids, counties, values, ["missing"], np.array([0.4]))
    drop_large = next(row for row in rows if row["dropped_COUNTYFP"] == "001")
    assert drop_large["remaining_observed_donor_count"] == 19
    assert not drop_large["at_least_20_observed_donors_remain"]
    assert not drop_large["stable_recomputation"]
    assert drop_large["absolute_shift_at_most_0_10"]  # Equal medians pass shift; donor count still fails.


def test_median_rejects_invalid_values_and_summarize_ignores_nulls():
    assert median(np.array([0.1, 0.5, 0.9])) == pytest.approx(0.5)
    assert median(np.array([], dtype=float)) is None
    with pytest.raises(ValueError, match=r"in \[0,1\]"):
        median(np.array([1.1]))
    assert summarize(np.array([0.0, np.nan, 1.0]))["n"] == 2


def test_detail_artifact_roundtrip_retains_region_county_and_null_recomputations():
    rows = [{"GEOID": "g1", "region": "eastern-ok", "dropped_COUNTYFP": "001",
             "baseline_region_median": 0.5, "original_observed_donor_count": 25,
             "dropped_observed_donor_count": 6, "remaining_observed_donor_count": 19,
             "recomputed_region_median": 0.5, "absolute_shift": 0.0,
             "at_least_20_observed_donors_remain": False,
             "absolute_shift_at_most_0_10": True, "stable_recomputation": False},
            {"GEOID": "g1", "region": "eastern-ok", "dropped_COUNTYFP": "002",
             "baseline_region_median": 0.5, "original_observed_donor_count": 25,
             "dropped_observed_donor_count": 19, "remaining_observed_donor_count": 6,
             "recomputed_region_median": None, "absolute_shift": None,
             "at_least_20_observed_donors_remain": False,
             "absolute_shift_at_most_0_10": False, "stable_recomputation": False}]
    table = output_table(rows)
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["region"].to_pylist() == ["eastern-ok", "eastern-ok"]
    assert decoded["dropped_COUNTYFP"].to_pylist() == ["001", "002"]
    assert decoded["recomputed_region_median"].to_pylist() == [0.5, None]
    assert parquet_bytes(table) == payload


def test_invalid_or_mismatched_inputs_fail_closed():
    with pytest.raises(ValueError, match="align"):
        leave_one_county_out(["a"], ["001", "002"], np.array([0.2]), ["z"], np.array([0.2]))
    with pytest.raises(ValueError, match="equal median"):
        leave_one_county_out(["a", "b"], ["001", "002"], np.array([0.1, 0.9]),
                             ["z"], np.array([0.3]))
    with pytest.raises(ValueError, match="unique"):
        leave_one_county_out(["a", "a"], ["001", "002"], np.array([0.2, 0.2]),
                             ["z"], np.array([0.2]))
