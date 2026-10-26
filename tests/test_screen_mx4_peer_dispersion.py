from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_mx4_peer_dispersion import (  # noqa: E402
    peer_weighted_iqr,
    parquet_bytes,
    weighted_quantile,
)


def test_weighted_empirical_quantile_uses_population_weights_and_stable_lower_rule():
    assert weighted_quantile(np.array([0.0, 0.5, 1.0]), np.array([1.0, 1.0, 2.0]), .25) == 0.0
    assert weighted_quantile(np.array([0.0, 0.5, 1.0]), np.array([1.0, 1.0, 2.0]), .75) == 1.0
    assert weighted_quantile(np.array([1.0, 0.0, 1.0]), np.array([2.0, 1.0, 1.0]), .5) == 1.0


def test_peer_iqr_excludes_focal_tract_and_uses_same_region_county_only():
    official = [("a", "r1"), ("b", "r1"), ("c", "r1"), ("d", "r2")]
    counties = ["001", "001", "001", "001"]
    shares = np.array([0.0, 0.5, 1.0, 0.0])
    defined = np.ones(4, dtype=bool)
    population = np.array([1.0, 1.0, 2.0, 100.0])
    result = peer_weighted_iqr(official, counties, shares, defined, population)
    assert result["defined"].tolist() == [True, True, True, False]
    assert result["q25"][:3].tolist() == [0.5, 0.0, 0.0]
    assert result["q75"][:3].tolist() == [1.0, 1.0, 0.5]
    assert result["dispersion"][:3].tolist() == [0.5, 1.0, 0.5]
    assert result["peer_count"].tolist() == [2, 2, 2, 0]
    assert np.isnan(result["dispersion"][3])
    assert result["county_group_count"] == 2


def test_missing_or_nonpositive_donors_are_excluded_and_nulls_preserved():
    official = [("a", "r1"), ("b", "r1"), ("c", "r1")]
    counties = ["001", "001", "001"]
    shares = np.array([0.2, 0.8, np.nan])
    defined = np.array([True, True, False])
    population = np.array([10.0, 0.0, 20.0])
    result = peer_weighted_iqr(official, counties, shares, defined, population)
    assert result["defined"].tolist() == [False, True, True]
    assert result["peer_count"].tolist() == [0, 1, 1]
    assert result["dispersion"][1] == 0.0
    assert result["dispersion"][2] == 0.0
    assert np.isnan(result["dispersion"][0])


def test_peer_iqr_rejects_bad_alignment_shares_and_negative_population():
    official = [("a", "r1")]
    with pytest.raises(ValueError, match="align"):
        peer_weighted_iqr(official, [], np.array([0.5]), np.array([True]), np.array([1.0]))
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        peer_weighted_iqr(official, ["001"], np.array([1.2]), np.array([True]), np.array([1.0]))
    with pytest.raises(ValueError, match="negative"):
        peer_weighted_iqr(official, ["001"], np.array([0.5]), np.array([True]), np.array([-1.0]))


def test_parquet_serialization_is_deterministic_and_preserves_null_dispersion():
    table = pa.table({"GEOID": pa.array(["001", "002"]),
                      "iqr": pa.array([0.25, None], type=pa.float64()),
                      "defined": pa.array([True, False])})
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["GEOID"].to_pylist() == ["001", "002"]
    assert decoded["iqr"].to_pylist() == [0.25, None]
    assert parquet_bytes(table) == payload
