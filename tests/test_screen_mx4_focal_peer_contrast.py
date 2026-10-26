from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_mx4_focal_peer_contrast import (  # noqa: E402
    focal_peer_contrast,
    output_table,
    parquet_bytes,
)


def test_focal_peer_contrast_excludes_focal_and_uses_population_weighted_median():
    official = [("001", "maricopa-az"), ("002", "maricopa-az"), ("003", "maricopa-az")]
    result = focal_peer_contrast(official, ["001", "001", "001"],
                                 np.array([.1, .4, .8]), np.ones(3, dtype=bool),
                                 np.array([100., 1., 1.]))
    assert result["defined"].tolist() == [True, True, True]
    assert np.allclose(result["peer_median"], [.4, .1, .1])
    assert np.allclose(result["contrast"], [-.3, .3, .7])
    assert result["peer_count"].tolist() == [2, 2, 2]
    assert result["county_group_count"] == 1


def test_focal_peer_contrast_preserves_nulls_and_no_peer_counties():
    official = [("001", "r"), ("002", "r"), ("003", "r")]
    result = focal_peer_contrast(official, ["a", "a", "solo"],
                                 np.array([.25, np.nan, .9]), np.array([True, False, True]),
                                 np.array([10., 20., 15.]))
    assert result["defined"].tolist() == [False, False, False]
    assert np.isnan(result["contrast"]).all()
    assert result["peer_count"].tolist() == [0, 0, 0]


def test_focal_peer_contrast_validates_alignment_and_population_weights():
    official = [("001", "r")]
    with pytest.raises(ValueError, match="align"):
        focal_peer_contrast(official, [], np.array([.2]), np.array([True]), np.array([1.]))
    with pytest.raises(ValueError, match="cannot be negative"):
        focal_peer_contrast(official, ["a"], np.array([.2]), np.array([True]), np.array([-1.]))
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        # Invalid input shares are rejected before contrast construction.
        focal_peer_contrast(official, ["a"], np.array([2.0]), np.array([True]), np.array([1.]))


def test_output_table_preserves_undefined_contrast_as_null_and_deterministic():
    official = [("001", "r"), ("002", "r")]
    candidate = {"peer_median": np.array([.4, np.nan]), "contrast": np.array([-.2, np.nan]),
                 "defined": np.array([True, False]), "peer_count": np.array([1, 0]),
                 "peer_population": np.array([10., 0.])}
    predictors = {"mx4_f": np.array([.2, .3]), "s1_lag": np.array([.1, .2])}
    masks = {"mx4_f": np.array([True, True]), "s1_lag": np.array([True, True])}
    joint = np.array([True, False])
    table = output_table(official, ["a", "b"], np.array([10., 10.]),
                         np.array([.2, .3]), np.array([True, True]), candidate,
                         predictors, masks, joint, np.array([-.1, np.nan]),
                         np.array([-.1, np.nan]), np.array([0, -1], dtype=np.int8))
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["focal_peer_contrast"].to_pylist() == [-.2, None]
    assert decoded["peer_weighted_median_mx4_f"].to_pylist() == [.4, None]
    assert decoded["crossfit_residual"].to_pylist() == [-.1, None]
    assert parquet_bytes(table) == payload
