from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx5_county_peer_imputation import (  # noqa: E402
    build_output_table,
    parquet_bytes,
    peer_median_impute,
)


def test_leave_one_out_median_uses_only_other_observed_county_peers():
    geoids = [f"0000000000{i}" for i in range(1, 7)]
    regions = ["eastern-ok"] * 6
    counties = ["001"] * 6
    observed = np.array([0.1, 0.2, 0.3, 0.4, 0.9, np.nan])
    observed_defined = np.array([True] * 5 + [False])
    values, defined, imputed, peer_count = peer_median_impute(
        geoids, regions, counties, observed, observed_defined)
    assert defined.tolist() == [True] * 6
    assert imputed.tolist() == [False] * 5 + [True]
    assert peer_count.tolist() == [4, 4, 4, 4, 4, 5]
    assert values[:5].tolist() == observed[:5].tolist()
    assert values[5] == pytest.approx(0.3)


def test_fewer_than_five_peers_stays_null_and_undefined_not_zero_filled():
    values, defined, imputed, peer_count = peer_median_impute(
        ["a", "b", "c", "d"], ["r"] * 4, ["001"] * 4,
        np.array([0.0, 0.25, 1.0, np.nan]), np.array([True, True, True, False]))
    assert peer_count[-1] == 3
    assert not defined[-1]
    assert not imputed[-1]
    assert np.isnan(values[-1])


def test_county_groups_are_region_local_and_imputations_never_become_donors():
    geoids = [f"g{i}" for i in range(9)]
    regions = ["r1"] * 7 + ["r2"] * 2
    counties = ["001"] * 7 + ["001"] * 2
    observed = np.array([0.0, 0.1, 0.2, 0.3, 0.4, np.nan, np.nan, 1.0, np.nan])
    mask = np.isfinite(observed)
    values, defined, imputed, peer_count = peer_median_impute(
        geoids, regions, counties, observed, mask)
    assert imputed[5:7].tolist() == [True, True]
    assert peer_count[5:7].tolist() == [5, 5]
    assert values[5] == pytest.approx(0.2)
    assert values[6] == pytest.approx(0.2)
    assert not defined[8] and not imputed[8]
    assert peer_count[8] == 1
    assert np.isnan(values[8])


def test_output_parquet_preserves_observed_zero_and_nulls_remaining_undefined():
    observed = np.array([0.0, 0.5, np.nan])
    observed_defined = np.array([True, True, False])
    values, defined, imputed, peers = peer_median_impute(
        ["a", "b", "c"], ["r"] * 3, ["001"] * 3, observed, observed_defined)
    table = build_output_table(["a", "b", "c"], ["r"] * 3, ["001"] * 3,
                               observed, observed_defined, values, defined, imputed, peers)
    decoded = pq.read_table(__import__("pyarrow").BufferReader(parquet_bytes(table)))
    assert decoded["observed_share"].to_pylist() == [0.0, 0.5, None]
    assert decoded["county_peer_mx5_share"].to_pylist() == [0.0, 0.5, None]
    assert decoded["defined"].to_pylist() == [True, True, False]
    assert decoded["imputed"].to_pylist() == [False, False, False]
    assert parquet_bytes(table) == parquet_bytes(table)


def test_invalid_source_undefined_numeric_placeholder_is_rejected():
    with pytest.raises(ValueError, match="undefined pooled Mx5 rows"):
        peer_median_impute(["a"], ["r"], ["001"], np.array([0.0]), np.array([False]))


def test_imputed_output_requires_five_peers_and_keeps_observations_unchanged():
    observed = np.array([0.2, 0.4, 0.6, 0.8, 1.0, np.nan])
    observed_defined = np.array([True] * 5 + [False])
    values, defined, imputed, peers = peer_median_impute(
        [f"g{i}" for i in range(6)], ["r"] * 6, ["013"] * 6,
        observed, observed_defined)
    altered = values.copy()
    altered[0] += 0.1
    with pytest.raises(ValueError, match="observed pooled Mx5 values must be preserved"):
        build_output_table([f"g{i}" for i in range(6)], ["r"] * 6, ["013"] * 6,
                           observed, observed_defined, altered, defined, imputed, peers)
