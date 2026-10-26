from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx5_county_peer_lopo import (  # noqa: E402
    county_median_lopo,
    detail_table,
    hash_mask,
    parquet_bytes,
)


def test_leave_one_donor_out_median_shifts_and_recipient_maxima():
    donor_ids = [f"d{i}" for i in range(5)]
    values = np.array([0.0, 0.2, 0.4, 0.6, 1.0])
    baseline = np.array([0.4, 0.4])
    maxima, shifts = county_median_lopo(donor_ids, values, baseline)
    assert np.allclose(shifts, [0.1, 0.1, 0.1, 0.1, 0.0, 0.0, 0.1, 0.1, 0.1, 0.1], rtol=0, atol=1e-15)
    assert np.allclose(maxima, [0.1, 0.1], rtol=0, atol=1e-15)
    assert len(shifts) == len(donor_ids) * len(baseline)


def test_lopo_requires_four_remaining_donors_and_exact_original_baseline():
    with pytest.raises(ValueError, match="retain"):
        county_median_lopo(["a", "b", "c", "d"], np.array([0.1, 0.2, 0.3, 0.4]), np.array([0.25]))
    with pytest.raises(ValueError, match="baseline"):
        county_median_lopo(["a", "b", "c", "d", "e"],
                           np.array([0.1, 0.2, 0.3, 0.4, 0.5]), np.array([0.0]))


def test_lopo_rejects_duplicate_donors_and_invalid_values():
    with pytest.raises(ValueError, match="unique"):
        county_median_lopo(["a", "a", "c", "d", "e"],
                           np.array([0.1, 0.2, 0.3, 0.4, 0.5]), np.array([0.3]))
    with pytest.raises(ValueError, match="finite"):
        county_median_lopo(["a", "b", "c", "d", "e"],
                           np.array([0.1, 0.2, np.nan, 0.4, 0.5]), np.array([0.3]))


def test_mask_hash_binds_exact_order_and_values():
    ids = [("001", "r1"), ("002", "r2")]
    mask = np.array([True, False])
    assert hash_mask(ids, mask) != hash_mask(ids[::-1], mask)
    assert hash_mask(ids, mask) != hash_mask(ids, np.array([False, True]))


def test_recipient_summary_artifact_is_deterministic():
    rows = [{"GEOID": "001", "region": "r", "COUNTYFP": "001",
             "baseline_county_median": .4, "original_observed_donor_count": 5,
             "minimum_remaining_observed_donors": 4, "donor_deletion_scenarios": 5,
             "maximum_absolute_shift": .1, "recipient_max_shift_at_most_0_10": True}]
    payload = parquet_bytes(detail_table(rows))
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["GEOID"].to_pylist() == ["001"]
    assert decoded["maximum_absolute_shift"].to_pylist() == [.1]
    assert parquet_bytes(detail_table(rows)) == payload
