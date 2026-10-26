from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_tm5_poi_building_version_discordance import (  # noqa: E402
    empirical_midpercentile,
    version_discordance,
    output_table,
    parquet_bytes,
)


def test_midrank_percentiles_map_ties_and_extrema_to_normalized_range():
    values = np.array([1., 1., 2., 3., 3.])
    result = empirical_midpercentile(values, np.ones(5, dtype=bool))
    assert np.allclose(result, [.125, .125, .5, .875, .875])
    assert empirical_midpercentile(np.array([8.]), np.array([True])).tolist() == [.5]


def test_region_local_percentile_ranks_and_joint_nulls_are_preserved():
    ids = [(f"{i:03d}", "maricopa-az") for i in range(4)] + [(f"{i+4:03d}", "northern-ca") for i in range(4)]
    poi = np.array([1., 1., 3., np.nan, 10., 20., 30., 40.])
    poi_mask = np.array([True, True, True, False, True, True, True, True])
    building = np.array([1., 2., 2., 4., 1., 1., 2., 2.])
    building_mask = np.ones(8, dtype=bool)
    result = version_discordance(ids, poi, poi_mask, building, building_mask)
    # Percentile values are computed independently within each region/channel.
    assert np.allclose(result["poi_percentile"][:3], [.25, .25, 1.])
    assert np.allclose(result["building_percentile"][:4], [0., .5, .5, 1.])
    assert np.allclose(result["poi_percentile"][4:], [0., 1/3, 2/3, 1.])
    assert result["defined"].tolist() == [True, True, True, False, True, True, True, True]
    assert np.isnan(result["discordance"][3])
    assert np.all((result["discordance"][result["defined"]] >= 0.) &
                  (result["discordance"][result["defined"]] <= 1.))


def test_version_discordance_rejects_misalignment_bad_regions_and_nonfinite_defined_values():
    official = [("001", "maricopa-az")]
    with pytest.raises(ValueError, match="align"):
        version_discordance(official, np.array([1., 2.]), np.array([True, True]),
                            np.array([1.]), np.array([True]))
    with pytest.raises(ValueError, match="finite"):
        version_discordance(official, np.array([np.nan]), np.array([True]),
                            np.array([1.]), np.array([True]))
    with pytest.raises(ValueError, match="remain null"):
        version_discordance(official, np.array([1.]), np.array([False]),
                            np.array([1.]), np.array([True]))
    with pytest.raises(ValueError, match="nonnegative"):
        version_discordance(official, np.array([-1.]), np.array([True]),
                            np.array([1.]), np.array([True]))


def test_output_table_retains_exact_joint_mask_and_nulls_deterministically():
    official = [("001", "maricopa-az"), ("002", "maricopa-az"), ("003", "northern-ca")]
    poi = np.array([1., 2., 3.])
    building = np.array([1., 3., np.nan])
    result = version_discordance(official, poi, np.array([True, True, True]),
                                 building, np.array([True, True, False]))
    table = output_table(official, poi, building, result)
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["joint_defined"].to_pylist() == [True, True, False]
    assert decoded["absolute_cross_layer_percentile_difference"].to_pylist()[-1] is None
    assert decoded["building_within_region_midpercentile"].to_pylist()[-1] is None
    assert parquet_bytes(table) == payload
    corrupted = dict(result)
    corrupted["defined"] = np.array([True, False, True])
    with pytest.raises(ValueError, match="exact joint source-defined mask"):
        output_table(official, poi, building, corrupted)
