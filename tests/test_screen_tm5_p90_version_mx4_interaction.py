from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_tm5_p90_version_mx4_interaction import (  # noqa: E402
    normalize_interaction,
    output_table,
    parquet_bytes,
)


def test_normalization_and_product_follow_preregistered_formula_and_bounds():
    p90 = np.array([0.0, 1.0, 13.0, 100.0, 2.0])
    p90_defined = np.array([True, True, True, True, False])
    mx4 = np.array([0.5, 0.0, 0.5, 1.0, 0.3])
    mx4_defined = np.ones(5, dtype=bool)
    result = normalize_interaction(p90, p90_defined, mx4, mx4_defined)
    v = result["normalized_p90_version"]
    assert v[0] == 0.0
    assert np.isclose(v[1], np.log1p(1.0) / np.log1p(13.0))
    assert v[2] == 1.0 and v[3] == 1.0
    assert result["interaction"][:4].tolist() == [0.0, 0.0, 0.5, 1.0]
    assert np.isnan(result["interaction"][4])
    assert result["defined"].tolist() == [True, True, True, True, False]
    assert result["p90_clip_count"] == 1
    assert result["mx4_clip_count"] == 0


def test_mx4_interaction_is_clipped_and_nulls_are_preserved():
    p90 = np.array([13.0, 13.0, 13.0, np.nan])
    p90_defined = np.array([True, True, True, False])
    mx4 = np.array([-0.2, 0.25, 1.4, 0.7])
    mx4_defined = np.ones(4, dtype=bool)
    result = normalize_interaction(p90, p90_defined, mx4, mx4_defined)
    assert result["interaction"][:3].tolist() == [0.0, 0.25, 1.0]
    assert np.isnan(result["interaction"][3])
    assert result["mx4_clip_count"] == 2


def test_normalizer_rejects_misalignment_invalid_version_and_nonfinite_defined_values():
    with pytest.raises(ValueError, match="aligned"):
        normalize_interaction(np.array([1.0]), np.array([True]), np.array([1.0, 2.0]),
                              np.array([True, True]))
    with pytest.raises(ValueError, match="nonnegative"):
        normalize_interaction(np.array([-1.0]), np.array([True]), np.array([0.5]), np.array([True]))
    with pytest.raises(ValueError, match="finite"):
        normalize_interaction(np.array([1.0]), np.array([True]), np.array([np.inf]), np.array([True]))
    with pytest.raises(ValueError, match="positive and finite"):
        normalize_interaction(np.array([1.0]), np.array([True]), np.array([0.5]), np.array([True]), 0.0)


def test_artifact_preserves_order_pairwise_mask_and_nullable_values():
    official = [("001", "maricopa-az"), ("002", "northern-ca"), ("003", "eastern-ok")]
    p90 = np.array([1.0, 13.0, 2.0])
    p90_defined = np.array([True, True, False])
    mx4 = np.array([0.5, 0.25, 0.8])
    mx4_defined = np.array([True, True, True])
    result = normalize_interaction(p90, p90_defined, mx4, mx4_defined)
    table = output_table(official, p90, p90_defined, mx4, mx4_defined, result)
    blob = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(blob))
    assert decoded["GEOID"].to_pylist() == ["001", "002", "003"]
    assert decoded["interaction_defined"].to_pylist() == [True, True, False]
    assert decoded["interaction"].to_pylist() == [pytest.approx(np.log1p(1.0) / np.log1p(13.0) * 0.5), 0.25, None]
    assert parquet_bytes(table) == blob


def test_output_rejects_joint_mask_mismatch():
    official = [("001", "maricopa-az")]
    p90 = np.array([13.0])
    p90_mask = np.array([True])
    mx4 = np.array([0.5])
    mx4_mask = np.array([True])
    result = normalize_interaction(p90, p90_mask, mx4, mx4_mask)
    result["defined"] = np.array([False])
    with pytest.raises(ValueError, match="pairwise source mask"):
        output_table(official, p90, p90_mask, mx4, mx4_mask, result)
