from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_s1_mx4_lagged_innovation import (  # noqa: E402
    decile_codes,
    leave_one_out_bin_mean_residual,
    measure_region_nmi,
    output_table,
    parquet_bytes,
)


def test_leave_one_out_peer_bin_mean_subtracts_own_s1_value():
    ids = ["g0", "g1", "g2", "g3"]
    current = np.array([0.0, 1.0, 2.0, 3.0])
    lagged = np.array([1.0, 3.0, 10.0, 30.0])
    result = leave_one_out_bin_mean_residual(ids, current, np.ones(4, bool),
                                             lagged, np.ones(4, bool), bins=2)
    assert result["current_mx4_decile"].tolist() == [0, 0, 1, 1]
    assert result["peer_count"].tolist() == [1, 1, 1, 1]
    assert result["peer_bin_mean"].tolist() == pytest.approx([3.0, 1.0, 30.0, 10.0])
    assert result["innovation"].tolist() == pytest.approx([-2.0, 2.0, -20.0, 20.0])
    assert result["defined"].all()
    altered = lagged.copy()
    altered[0] += 1000
    result_altered = leave_one_out_bin_mean_residual(ids, current, np.ones(4, bool),
                                                     altered, np.ones(4, bool), bins=2)
    assert result_altered["peer_bin_mean"][0] == result["peer_bin_mean"][0]


def test_pairwise_definedness_and_singleton_bins_stay_null():
    ids = ["a", "b", "c", "d"]
    current = np.array([0.0, 0.1, 0.9, 1.0])
    lagged = np.array([2.0, np.nan, 5.0, np.nan])
    result = leave_one_out_bin_mean_residual(ids, current, np.ones(4, bool),
                                             lagged, np.array([True, False, True, False]), bins=2)
    assert result["pairwise_defined"].tolist() == [True, False, True, False]
    assert not result["defined"].any()
    assert np.isnan(result["innovation"]).all()
    assert np.isnan(result["peer_bin_mean"]).all()


def test_empirical_decile_binning_is_repeatable_and_undefined_codes_are_minus_one():
    values = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, np.nan])
    mask = np.isfinite(values)
    codes, edges = decile_codes(values, mask)
    codes_again, edges_again = decile_codes(values, mask)
    assert np.array_equal(codes, codes_again)
    assert np.array_equal(edges, edges_again)
    assert set(codes[:10].tolist()) == set(range(10))
    assert codes[10] == -1


def test_region_nmi_is_bounded_and_tracks_defined_overlap():
    x = np.tile(np.arange(10, dtype=float), 10)
    residual = x.copy()
    result = measure_region_nmi(x, np.ones(100, bool), residual, np.ones(100, bool))
    assert result["status"] == "measured"
    assert result["joint_defined"] == 100
    assert result["nmi"] == pytest.approx(1.0)
    low = measure_region_nmi(x[:20], np.ones(20, bool), residual[:20], np.zeros(20, bool))
    assert low["joint_defined"] == 0
    assert low["nmi"] is None


def test_output_artifact_roundtrip_keeps_innovation_null_and_pairwise_masks():
    ids = ["g0", "g1", "g2", "g3"]
    regions = ["eastern-ok"] * 4
    current = np.array([0.1, 0.2, 0.8, 0.9])
    current_mask = np.ones(4, bool)
    lagged = np.array([0.25, 0.5, 0.75, np.nan])
    lagged_mask = np.array([True, True, True, False])
    result = leave_one_out_bin_mean_residual(ids, current, current_mask, lagged,
                                             lagged_mask, bins=2)
    table = output_table(ids, regions, current, current_mask, lagged, lagged_mask, result)
    decoded = pq.read_table(pa.BufferReader(parquet_bytes(table)))
    assert decoded["GEOID"].to_pylist() == ids
    assert decoded["S1_lagged_Mx4_F_defined"].to_pylist() == [True, True, True, False]
    assert decoded["innovation_defined"].to_pylist() == [True, True, False, False]
    assert decoded["innovation"].to_pylist()[2:] == [None, None]
    assert parquet_bytes(table) == parquet_bytes(table)


def test_leave_one_out_rejects_duplicate_ids_or_nonfinite_defined_values():
    with pytest.raises(ValueError, match="uniquely"):
        leave_one_out_bin_mean_residual(["a", "a"], np.array([0.0, 1.0]), np.ones(2, bool),
                                        np.array([0.0, 1.0]), np.ones(2, bool), bins=2)
    with pytest.raises(ValueError, match="finite"):
        leave_one_out_bin_mean_residual(["a", "b"], np.array([0.0, np.nan]), np.ones(2, bool),
                                        np.array([0.0, 1.0]), np.ones(2, bool), bins=2)
