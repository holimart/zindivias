from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_tm5_building_p90_mx4_interaction import (  # noqa: E402
    normalize_interaction,
    output_table,
    parquet_bytes,
)


def test_building_p90_interaction_uses_log_normalization_and_clipped_mx4():
    raw = np.array([1.0, 5.0, 3.0])
    raw_defined = np.array([True, True, True])
    mx4 = np.array([0.5, -0.2, 1.2])
    mx4_defined = np.array([True, True, True])
    result = normalize_interaction(raw, raw_defined, mx4, mx4_defined)
    assert np.allclose(result["normalized"], [np.log(2) / np.log(6), 1.0, np.log(4) / np.log(6)])
    assert np.allclose(result["clipped_mx4"], [0.5, 0.0, 1.0])
    assert np.allclose(result["interaction"], result["normalized"] * result["clipped_mx4"])
    assert np.allclose(result["change"], np.abs(result["interaction"] - result["normalized"]))
    assert result["defined"].all()
    assert result["building_clip_count"] == 0
    assert result["mx4_clip_count"] == 2


def test_building_interaction_preserves_joint_null_semantics():
    result = normalize_interaction(np.array([1.0, np.nan]), np.array([True, False]),
                                  np.array([.3, .7]), np.array([True, True]))
    assert result["defined"].tolist() == [True, False]
    assert np.isfinite(result["normalized"][0]) and np.isnan(result["normalized"][1])
    assert np.isfinite(result["interaction"][0]) and np.isnan(result["interaction"][1])
    assert np.isfinite(result["change"][0]) and np.isnan(result["change"][1])


def test_building_interaction_rejects_bad_shapes_values_and_range():
    with pytest.raises(ValueError, match="aligned"):
        normalize_interaction(np.array([1.]), np.array([True]), np.array([.2, .3]), np.array([True, True]))
    with pytest.raises(ValueError, match="nonnegative"):
        normalize_interaction(np.array([-1.]), np.array([True]), np.array([.2]), np.array([True]))
    with pytest.raises(ValueError, match="finite"):
        normalize_interaction(np.array([1.]), np.array([True]), np.array([np.nan]), np.array([True]))
    with pytest.raises(ValueError, match="positive and finite"):
        normalize_interaction(np.array([1.]), np.array([True]), np.array([.2]), np.array([True]), 0)


def test_output_table_keeps_undefined_values_null_and_is_deterministic():
    official = [("001", "r"), ("002", "r")]
    result = normalize_interaction(np.array([1.0, np.nan]), np.array([True, False]),
                                   np.array([.5, .2]), np.array([True, True]))
    table = output_table(official, np.array([1.0, np.nan]), np.array([True, False]),
                         np.array([.5, .2]), np.array([True, True]), result)
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["interaction"].to_pylist() == [float(result["interaction"][0]), None]
    assert decoded["tm5_building_p90_version_normalized"].to_pylist()[1] is None
    assert decoded["interaction_defined"].to_pylist() == [True, False]
    assert parquet_bytes(table) == payload
