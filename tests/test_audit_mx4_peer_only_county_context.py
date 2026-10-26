from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4_peer_only_county_context import peer_only_county_context  # noqa: E402


def test_peer_only_values_remove_the_focal_tract_and_preserve_grouping():
    ids = ["a", "b", "c", "d"]
    regions = ["maricopa-az", "maricopa-az", "northern-ca", "northern-ca"]
    counties = ["001", "001", "001", "001"]
    shares = np.array([0.2, 0.8, 0.1, 0.9])
    weights = np.array([1.0, 3.0, 4.0, 2.0])
    existing = np.array([0.65, 0.65, 0.36666666666666664, 0.36666666666666664])

    result = peer_only_county_context(ids, regions, counties, shares, weights, existing)

    assert result["county_group_count"] == 2  # COUNTYFP is region-local, as in Mx4.
    assert result["peer_defined"].tolist() == [True] * 4
    assert np.allclose(result["peer_only_share"], [0.8, 0.2, 0.9, 0.1])
    assert np.allclose(result["absolute_change"], [0.15, 0.45, 0.5333333333333333, 0.26666666666666666])
    assert np.allclose(result["reproduced_existing_share"], existing)


def test_county_singleton_is_undefined_when_peer_denominator_is_zero():
    result = peer_only_county_context(
        ["solo", "peer"], ["eastern-ok", "south-central-tx"], ["007", "007"],
        np.array([0.7, 0.4]), np.array([5.0, 2.0]), np.array([0.7, 0.4]),
    )
    assert result["peer_defined"].tolist() == [False, False]
    assert np.isnan(result["peer_only_share"]).all()
    assert np.isnan(result["absolute_change"]).all()


def test_unusable_focal_has_zero_self_contribution_but_peer_context_if_available():
    result = peer_only_county_context(
        ["missing", "valid"], ["south-central-tx"] * 2, ["021"] * 2,
        np.array([np.nan, 0.4]), np.array([7.0, 2.0]), np.array([0.4, 0.4]),
    )
    assert result["usable_focal"].tolist() == [False, True]
    assert result["peer_defined"].tolist() == [True, False]
    assert result["peer_only_share"][0] == 0.4
    assert np.isnan(result["peer_only_share"][1])


def test_existing_county_values_must_reproduce_from_declared_inputs():
    with np.testing.assert_raises_regex(ValueError, "do not reproduce"):
        peer_only_county_context(
            ["a", "b"], ["maricopa-az"] * 2, ["001"] * 2,
            np.array([0.2, 0.8]), np.array([1.0, 3.0]), np.array([0.5, 0.5]),
        )
