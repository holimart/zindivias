from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_s_family_nonlinear_orthogonality import (  # noqa: E402
    candidate_pairs,
    encode_feature,
    measure_pair_nmi,
    normalized_mutual_information,
    serialize_pair_measurements,
)


def test_binary_flags_remain_categories():
    codes, metadata = encode_feature(np.array([0.0, 1.0, 1.0, np.nan, 0.0]))
    assert metadata["encoding"] == "category_integer_low_cardinality"
    assert metadata["categories_or_bins"] == 2
    assert codes.tolist() == [0, 1, 1, -1, 0]


def test_continuous_values_use_deterministic_empirical_deciles():
    values = np.arange(100, dtype=np.float64)
    values[5] = np.nan
    left, left_meta = encode_feature(values)
    right, right_meta = encode_feature(values)
    assert left_meta == right_meta
    assert np.array_equal(left, right)
    assert left_meta["encoding"] == "empirical_decile_bins"
    assert left_meta["defined"] == 99
    assert np.all((left[values == values] >= 0) & (left[values == values] <= 9))
    assert left[5] == -1


def test_nmi_is_finite_bounded_and_identifies_perfect_nonlinear_association():
    x = np.repeat(np.arange(10), 10)
    y = np.repeat(np.arange(10)[::-1], 10)
    assert normalized_mutual_information(x, y) == 1.0
    assert normalized_mutual_information(np.zeros(30, dtype=int), np.ones(30, dtype=int)) == 0.0
    independent_like = normalized_mutual_information(np.tile([0, 1], 30), np.repeat([0, 1], 30))
    assert independent_like < 1e-12


def test_pairwise_overlap_threshold_and_defined_mask_are_enforced():
    left = np.arange(30, dtype=np.int16)
    right = left.copy()
    left[0] = -1
    measured = measure_pair_nmi(left, right)
    assert measured["status"] == "excluded_below_minimum_overlap"
    assert measured["joint_defined"] == 29
    assert measured["nmi"] is None
    left[0] = 0
    measured = measure_pair_nmi(left, right)
    assert measured["status"] == "measured"
    assert measured["joint_defined"] == 30
    assert np.isfinite(measured["nmi"])
    assert 0.0 <= measured["nmi"] <= 1.0


def test_pair_measurement_sidecar_is_csv_deterministic_and_complete():
    rows = [[0, 2, 1, 55, 0.8125], [3, 4, 2, 30, 0.0]]
    payload = serialize_pair_measurements(rows)
    assert payload == serialize_pair_measurements(rows)
    assert payload.decode().splitlines() == [
        "left_feature_index,right_feature_index,region_index,joint_defined,nmi",
        "0,2,1,55,0.8125",
        "3,4,2,30,0.0",
    ]


def test_candidate_pairs_are_unique_cross_idea_pairs_including_s_family_pairs():
    keys = ["S1|F|x", "S1|R|y", "S2|F|z", "M1|R|q"]
    meta = {"S1|F|x": {"idea": "S1"}, "S1|R|y": {"idea": "S1"},
            "S2|F|z": {"idea": "S2"}, "M1|R|q": {"idea": "M1"}}
    pairs = candidate_pairs(keys, meta)
    assert len(pairs) == len(set(pairs))
    assert ("S1|F|x", "S2|F|z") in pairs
    assert ("M1|R|q", "S2|F|z") in pairs
    assert ("S1|F|x", "S1|R|y") not in pairs  # same-idea emissions are not redundancy pairs
