from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_tm5_poi_p90_region_rank import (  # noqa: E402
    empirical_midpercentile, region_rank, output_table,
)
from screen_tm5_building_p90_mx4_interaction import parquet_bytes  # noqa: E402


def test_defined_values_get_normalized_midranks_with_ties_and_singleton():
    values = np.array([1., 1., 2., 3., 3., np.nan])
    mask = np.array([True, True, True, True, True, False])
    ranks = empirical_midpercentile(values, mask)
    assert np.allclose(ranks[:5], [.125, .125, .5, .875, .875])
    assert np.isnan(ranks[5])
    assert empirical_midpercentile(np.array([4.]), np.array([True])).tolist() == [.5]
    assert np.isnan(empirical_midpercentile(np.array([np.nan]), np.array([False]))[0])


def test_region_ranks_are_computed_separately_and_preserve_missing():
    pairs = [("001", "maricopa-az"), ("002", "maricopa-az"),
             ("003", "northern-ca"), ("004", "northern-ca"), ("005", "northern-ca"),
             ("006", "eastern-ok"), ("007", "south-central-tx")]
    raw = np.array([1., 13., 2., np.nan, 10., 4., 5.])
    defined = np.array([True, True, True, False, True, True, True])
    ranks = region_rank(pairs, raw, defined)
    assert ranks[0] == 0.0 and ranks[1] == 1.0
    assert ranks[2] == 0.0 and ranks[4] == 1.0
    assert np.isnan(ranks[3])


def test_rejects_undefined_nonnull_and_nonfinite_defined_input():
    with pytest.raises(ValueError, match="remain null"):
        empirical_midpercentile(np.array([1.]), np.array([False]))
    with pytest.raises(ValueError, match="finite"):
        empirical_midpercentile(np.array([np.nan]), np.array([True]))


def test_output_table_keeps_exact_official_order_and_nulls_deterministically():
    pairs = [("001", "maricopa-az"), ("002", "maricopa-az"), ("003", "northern-ca")]
    raw = np.array([1., 13., np.nan])
    defined = np.array([True, True, False])
    rank = np.array([0., 1., np.nan])
    global_norm = np.array([np.log1p(1.) / np.log1p(13.), 1., np.nan])
    change = np.abs(rank - global_norm)
    table = output_table(pairs, raw, defined, global_norm, rank, change)
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["GEOID"].to_pylist() == ["001", "002", "003"]
    assert decoded["within_region_midpercentile"].to_pylist() == [0., 1., None]
    assert decoded["absolute_change_from_global_log_normalized"].to_pylist()[-1] is None
    assert parquet_bytes(table) == payload
    with pytest.raises(ValueError, match="defined outputs"):
        output_table(pairs, raw, defined, global_norm, np.array([0., 1., 0.]), change)
