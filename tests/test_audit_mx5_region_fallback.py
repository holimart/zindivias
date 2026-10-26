from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx5_region_fallback import (  # noqa: E402
    apply_region_median_fallback,
    output_table,
    parquet_bytes,
)


def test_region_fallback_uses_only_original_observed_values_and_preserves_county_values():
    geoids = [f"g{i}" for i in range(8)]
    regions = ["maricopa-az"] * 6 + ["northern-ca"] * 2
    observed = np.array([0.1, 0.3, 0.8, np.nan, np.nan, np.nan, 0.2, np.nan])
    observed_defined = np.array([True, True, True, False, False, False, True, False])
    county = np.array([0.1, 0.3, 0.8, 0.77, np.nan, np.nan, 0.2, np.nan])
    county_defined = np.array([True, True, True, True, False, False, True, False])
    final, defined, regional, peers, medians = apply_region_median_fallback(
        geoids, regions, observed, observed_defined, county, county_defined, minimum_peers=3)
    assert final[:4].tolist() == [0.1, 0.3, 0.8, 0.77]
    assert defined.tolist() == [True, True, True, True, True, True, True, False]
    assert regional.tolist() == [False, False, False, False, True, True, False, False]
    assert final[4:6].tolist() == pytest.approx([0.3, 0.3])
    assert peers[4:6].tolist() == [3, 3]
    assert medians[4:6].tolist() == pytest.approx([0.3, 0.3])
    assert peers[7] == 1
    assert np.isnan(final[7]) and not defined[7] and not regional[7]
    # County-imputed 0.77 does not enter the observed donor pool; observed median is 0.3.
    assert final[4] == pytest.approx(float(np.median([0.1, 0.3, 0.8])))


def test_region_fallback_requires_minimum_other_observed_peers():
    geoids = [f"g{i}" for i in range(5)]
    final, defined, regional, peers, _ = apply_region_median_fallback(
        geoids, ["eastern-ok"] * 5,
        np.array([0.0, 0.5, np.nan, np.nan, np.nan]),
        np.array([True, True, False, False, False]),
        np.array([0.0, 0.5, np.nan, np.nan, np.nan]),
        np.array([True, True, False, False, False]), minimum_peers=3)
    assert peers[2:].tolist() == [2, 2, 2]
    assert not regional.any()
    assert not defined[2:].any()
    assert np.isnan(final[2:]).all()


def test_counties_and_regions_are_separate_and_input_semantics_are_validated():
    with pytest.raises(ValueError, match="undefined observed values"):
        apply_region_median_fallback(["a"], ["maricopa-az"], np.array([0.0]),
                                     np.array([False]), np.array([np.nan]), np.array([False]))
    with pytest.raises(ValueError, match="unknown"):
        apply_region_median_fallback(["a"], ["unknown"], np.array([np.nan]),
                                     np.array([False]), np.array([np.nan]), np.array([False]))
    with pytest.raises(ValueError, match="unchanged"):
        apply_region_median_fallback(["a"], ["maricopa-az"], np.array([0.5]),
                                     np.array([True]), np.array([0.4]), np.array([True]))


def test_output_table_roundtrip_preserves_origin_tiers_and_null_semantics():
    geoids = ["g1", "g2", "g3"]
    regions = ["maricopa-az"] * 3
    observed = np.array([0.0, np.nan, np.nan])
    observed_defined = np.array([True, False, False])
    county = np.array([0.0, 0.8, np.nan])
    county_defined = np.array([True, True, False])
    final = np.array([0.0, 0.8, np.nan])
    final_defined = np.array([True, True, False])
    regional = np.array([False, False, False])
    peers = np.array([30, 30, 29])
    medians = np.array([np.nan, np.nan, np.nan])
    table = output_table(geoids, regions, observed, observed_defined, county,
                         county_defined, final, final_defined, regional, peers, medians)
    decoded = pq.read_table(pa.BufferReader(parquet_bytes(table)))
    assert decoded["GEOID"].to_pylist() == geoids
    assert decoded["observed_share"].to_pylist() == [0.0, None, None]
    assert decoded["county_peer_share"].to_pylist() == [0.0, 0.8, None]
    assert decoded["county_imputed"].to_pylist() == [False, True, False]
    assert decoded["final_share"].to_pylist() == [0.0, 0.8, None]
    assert decoded["final_defined"].to_pylist() == [True, True, False]
    assert parquet_bytes(table) == parquet_bytes(table)


def test_output_table_rejects_overwritten_county_values_or_filled_nulls():
    with pytest.raises(ValueError, match="preserve county-imputed"):
        output_table(["g"], ["maricopa-az"], np.array([np.nan]), np.array([False]),
                     np.array([0.4]), np.array([True]), np.array([0.5]), np.array([True]),
                     np.array([False]), np.array([30]), np.array([np.nan]))
    with pytest.raises(ValueError, match="undefined final values must be null"):
        output_table(["g"], ["maricopa-az"], np.array([np.nan]), np.array([False]),
                     np.array([np.nan]), np.array([False]), np.array([0.0]), np.array([False]),
                     np.array([False]), np.array([0]), np.array([np.nan]))
