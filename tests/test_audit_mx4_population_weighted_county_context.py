from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4_population_weighted_county_context import (  # noqa: E402
    _artifact_payload,
    leave_one_out_population_context,
)


def test_population_peer_share_excludes_focal_from_weight_and_weighted_sum():
    result = leave_one_out_population_context(
        ["a", "b", "c"], ["maricopa-az"] * 3, ["013"] * 3,
        np.array([0.0, 1.0, 0.5]), np.array([100.0, 300.0, 0.0]),
        np.array([0.5, 0.5, 0.5]),
    )
    assert result["peer_defined"].tolist() == [True, True, True]
    assert np.allclose(result["peer_population_share"], [1.0, 0.0, 0.75])
    assert np.allclose(result["absolute_change_from_frozen"], [0.5, 0.5, 0.25])
    assert result["county_population_weight"].tolist() == [400.0, 400.0, 400.0]
    assert np.allclose(result["county_population_weighted_share_sum"], [300.0] * 3)


def test_county_codes_are_region_local():
    result = leave_one_out_population_context(
        ["a", "b", "c", "d"],
        ["maricopa-az", "maricopa-az", "northern-ca", "northern-ca"],
        ["001"] * 4, np.array([0.1, 0.9, 0.3, 0.7]), np.array([1.0, 3.0, 5.0, 5.0]),
        np.array([0.5, 0.5, 0.5, 0.5]),
    )
    assert result["county_group_count"] == 2
    assert np.allclose(result["peer_population_share"], [0.9, 0.1, 0.7, 0.3])


def test_zero_population_peer_denominator_remains_undefined_and_null():
    result = leave_one_out_population_context(
        ["a", "b"], ["eastern-ok"] * 2, ["007"] * 2,
        np.array([0.2, 0.8]), np.array([0.0, 0.0]), np.array([0.4, 0.6]),
    )
    assert result["peer_defined"].tolist() == [False, False]
    assert np.isnan(result["peer_population_share"]).all()
    payload = _artifact_payload(["a", "b"], ["eastern-ok"] * 2, ["007"] * 2,
                                np.array([0.0, 0.0]), np.array([0.2, 0.8]),
                                np.array([0.4, 0.6]), result)
    table = pq.read_table(__import__("pyarrow").BufferReader(payload))
    assert table["peer_population_weighted_share"].to_pylist() == [None, None]
    assert table["absolute_change_from_frozen"].to_pylist() == [None, None]


def test_unusable_population_or_share_does_not_enter_peer_aggregate():
    result = leave_one_out_population_context(
        ["no-pop", "no-share", "usable"], ["south-central-tx"] * 3, ["029"] * 3,
        np.array([0.2, np.nan, 0.8]), np.array([100.0, 900.0, 300.0]),
        np.array([0.5, 0.5, 0.5]),
    )
    assert result["usable_share_and_population"].tolist() == [True, False, True]
    assert np.allclose(result["peer_population_share"], [0.8, 0.65, 0.2])


def test_negative_population_is_rejected():
    with np.testing.assert_raises_regex(ValueError, "cannot be negative"):
        leave_one_out_population_context(
            ["a"], ["maricopa-az"], ["001"], np.array([0.2]), np.array([-1.0]), np.array([0.2])
        )
