from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_s1_mx4_nmi_null import (  # noqa: E402
    adjusted_mutual_information,
    bootstrap_nmi,
    expected_mutual_information,
    is_confirmed_redundancy,
    load_one_feature,
    mutual_information,
    normalized_mutual_information,
    permutation_null,
)


def test_exact_expected_mi_and_adjusted_mi_are_finite_and_bounded():
    x = np.repeat(np.arange(3), 40)
    y = np.repeat(np.arange(3)[::-1], 40)
    stats = adjusted_mutual_information(x, y)
    assert stats["mutual_information_nats"] == pytest.approx(np.log(3))
    assert stats["expected_mutual_information_nats"] >= 0
    assert stats["adjusted_mutual_information"] == pytest.approx(1.0)
    from itertools import permutations
    small_x = np.array([0, 0, 1, 1])
    small_y = np.array([0, 0, 1, 1])
    exhaustive_emi = np.mean([mutual_information(small_x, np.asarray(p))[0]
                              for p in permutations(small_y)])
    assert expected_mutual_information(small_x, small_y) == pytest.approx(exhaustive_emi)


def test_mutual_information_matches_source_nmi_entropy_identity():
    x = np.array([0, 0, 1, 1, 2, 2] * 5)
    y = np.array([1, 1, 2, 2, 0, 0] * 5)
    mi, hx, hy = mutual_information(x, y)
    assert 2 * mi / (hx + hy) == pytest.approx(normalized_mutual_information(x, y))


def test_permutation_null_is_deterministic_and_uses_plus_one_p_value():
    x = np.repeat(np.arange(4), 25)
    y = np.repeat(np.arange(4)[::-1], 25)
    first = permutation_null(x, y, replicates=199, seed=8291)
    second = permutation_null(x, y, replicates=199, seed=8291)
    assert first == second
    assert first["replicates"] == 199
    assert first["exceedances_ge_observed"] == 0
    assert first["p_value_plus_one"] == pytest.approx(1 / 200)
    assert first["null_mean_nmi"] < first["observed_raw_nmi"]


def test_paired_bootstrap_is_deterministic_and_finite():
    x = np.repeat(np.arange(3), 30)
    y = np.repeat(np.array([1, 2, 0]), 30)
    first = bootstrap_nmi(x, y, replicates=99, seed=19)
    second = bootstrap_nmi(x, y, replicates=99, seed=19)
    assert first == second
    low, high = first["raw_nmi_interval"]
    assert 0 <= low <= high <= 1
    assert first["replicates"] == 99


def test_feature_loader_preserves_pairwise_undefinedness_and_checks_ids(tmp_path: Path):
    rows = [
        {"GEOID": "00000000001", "region": "eastern-ok", "idea": "Mx4", "family": "Mx",
         "component": "F", "channel": "covariate", "value": 0.0, "defined": True},
        {"GEOID": "00000000002", "region": "eastern-ok", "idea": "Mx4", "family": "Mx",
         "component": "F", "channel": "covariate", "value": 0.0, "defined": False},
    ]
    path = tmp_path / "Mx4.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    values, actual = load_one_feature(path, idea="Mx4", family="Mx", component="F",
                                      channel="covariate",
                                      official_pairs=[("00000000001", "eastern-ok"),
                                                      ("00000000002", "eastern-ok")],
                                      expected_hash=digest)
    assert actual == digest
    assert values["00000000001"] == 0.0
    assert np.isnan(values["00000000002"])
    with pytest.raises(ValueError, match="hash mismatch"):
        load_one_feature(path, idea="Mx4", family="Mx", component="F", channel="covariate",
                         official_pairs=[("00000000001", "eastern-ok"),
                                         ("00000000002", "eastern-ok")],
                         expected_hash="0" * 64)


def test_confirmed_redundancy_requires_both_strict_screen_thresholds():
    assert is_confirmed_redundancy(0.01, 0.50)
    assert not is_confirmed_redundancy(0.010001, 0.90)
    assert not is_confirmed_redundancy(0.001, 0.499999)


def test_permutation_requires_nonempty_aligned_pairwise_codes():
    with pytest.raises(ValueError):
        permutation_null(np.array([0, 1]), np.array([0]), replicates=10, seed=1)
    with pytest.raises(ValueError):
        bootstrap_nmi(np.array([], dtype=int), np.array([], dtype=int), replicates=10, seed=1)
