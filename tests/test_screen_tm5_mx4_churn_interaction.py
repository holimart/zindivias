from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_tm5_mx4_churn_interaction import (  # noqa: E402
    clipped_interaction,
    output_table,
    parquet_bytes,
)


def test_interaction_is_product_of_inputs_clipped_to_unit_interval():
    tm5 = np.array([-0.25, 0.0, 0.5, 1.0, 1.25])
    mx4 = np.array([0.4, 0.8, 0.6, 0.5, 1.5])
    result = clipped_interaction(tm5, np.ones(5, bool), mx4, np.ones(5, bool))
    assert result["tm5_clipped"].tolist() == pytest.approx([0.0, 0.0, 0.5, 1.0, 1.0])
    assert result["mx4_clipped"].tolist() == pytest.approx([0.4, 0.8, 0.6, 0.5, 1.0])
    assert result["interaction"].tolist() == pytest.approx([0.0, 0.0, 0.3, 0.5, 1.0])
    assert result["defined"].all()
    assert result["tm5_clip_count"] == 2
    assert result["mx4_clip_count"] == 1


def test_interaction_preserves_pairwise_nulls_without_zero_filling():
    result = clipped_interaction(
        np.array([0.4, np.nan, 0.2]), np.array([True, False, True]),
        np.array([0.5, 0.8, np.nan]), np.array([True, True, False]))
    assert result["defined"].tolist() == [True, False, False]
    assert result["interaction"][0] == pytest.approx(0.2)
    assert np.isnan(result["interaction"][1:]).all()
    assert np.isnan(result["tm5_clipped"][1])
    assert np.isnan(result["mx4_clipped"][2])


def test_zero_version_zero_share_interaction_is_zero_with_no_false_change():
    tm5 = np.zeros(100)
    mx4 = np.linspace(0, 1, 100)
    result = clipped_interaction(tm5, np.ones(100, bool), mx4, np.ones(100, bool))
    delta = np.abs(result["interaction"] - result["tm5_clipped"])
    assert np.all(result["interaction"] == 0)
    assert np.quantile(delta, .95) == 0.0


def test_output_roundtrip_preserves_ids_masks_bounds_and_deterministic_bytes():
    official = [("a", "maricopa-az"), ("b", "maricopa-az"), ("c", "eastern-ok")]
    tm5 = np.array([0.0, 0.3, np.nan])
    tm5_defined = np.array([True, True, False])
    mx4 = np.array([0.4, 0.8, 0.2])
    mx4_defined = np.ones(3, bool)
    result = clipped_interaction(tm5, tm5_defined, mx4, mx4_defined)
    table = output_table(official, tm5, tm5_defined, mx4, mx4_defined, result)
    encoded = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(encoded))
    assert decoded["GEOID"].to_pylist() == ["a", "b", "c"]
    assert decoded["interaction_defined"].to_pylist() == [True, True, False]
    product = decoded["tm5_mx4_product"].to_pylist()
    assert product[:2] == pytest.approx([0.0, 0.24])
    assert product[2] is None
    assert parquet_bytes(table) == encoded


def test_rejects_bad_shapes_nonfinite_defined_values_and_invalid_bounds():
    with pytest.raises(ValueError, match="aligned"):
        clipped_interaction(np.array([0.2]), np.array([True]),
                            np.array([0.3, 0.4]), np.array([True, True]))
    with pytest.raises(ValueError, match="defined source values"):
        clipped_interaction(np.array([np.nan]), np.array([True]),
                            np.array([0.3]), np.array([True]))
    with pytest.raises(ValueError, match="finite and ordered"):
        clipped_interaction(np.array([0.2]), np.array([True]),
                            np.array([0.3]), np.array([True]), low=1.0, high=0.0)


def test_output_rejects_pairwise_mask_mismatch():
    result = clipped_interaction(np.array([0.2]), np.array([True]),
                                 np.array([0.3]), np.array([True]))
    result["defined"] = np.array([False])
    with pytest.raises(ValueError, match="exact pairwise source mask"):
        output_table([("a", "r")], np.array([0.2]), np.array([True]),
                     np.array([0.3]), np.array([True]), result)
