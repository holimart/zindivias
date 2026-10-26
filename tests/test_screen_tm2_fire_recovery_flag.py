from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_tm2_fire_recovery_flag import (  # noqa: E402
    make_recovery_flag,
    output_table,
    pq_bytes,
    sparse_flag_gate,
)


def test_flag_uses_strict_positive_gap_inclusive_ten_percent_growth_and_exact_joint_mask():
    gap = np.array([0.0, 0.2, 0.2, 0.2, np.nan, 0.2])
    gap_defined = np.array([True, True, True, True, False, True])
    growth = np.array([0.5, 0.0999, 0.10, 0.3, 0.4, np.nan])
    growth_defined = np.array([True, True, True, True, True, False])
    flag, defined = make_recovery_flag(gap, gap_defined, growth, growth_defined)
    assert defined.tolist() == [True, True, True, True, False, False]
    assert flag.tolist() == [0, 0, 1, 1, 0, 0]
    assert np.isin(flag[defined], [0, 1]).all()


def test_undefined_source_placeholders_are_not_used_as_values():
    gap = np.array([0.1, np.nan, 0.3])
    gap_defined = np.array([True, False, True])
    growth = np.array([0.1, 100.0, np.nan])
    growth_defined = np.array([True, False, False])
    flag, defined = make_recovery_flag(gap, gap_defined, growth, growth_defined)
    assert defined.tolist() == [True, False, False]
    assert flag[0] == 1
    assert flag[1:].tolist() == [0, 0]


def test_flag_rejects_misaligned_nonfinite_defined_or_out_of_range_gap_values():
    with pytest.raises(ValueError, match="aligned"):
        make_recovery_flag(np.array([0.1]), np.array([True]), np.array([0.1, 0.2]),
                           np.array([True, True]))
    with pytest.raises(ValueError, match="finite"):
        make_recovery_flag(np.array([np.nan]), np.array([True]), np.array([0.2]), np.array([True]))
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        make_recovery_flag(np.array([1.1]), np.array([True]), np.array([0.2]), np.array([True]))
    with pytest.raises(ValueError, match="threshold"):
        make_recovery_flag(np.array([0.1]), np.array([True]), np.array([0.2]), np.array([True]), np.nan)


def test_sparse_acceptance_requires_twenty_flags_in_at_least_two_regions():
    assert sparse_flag_gate({"a": 10, "b": 10, "c": 0})["pass"]
    assert not sparse_flag_gate({"a": 19, "b": 0})["pass"]
    assert not sparse_flag_gate({"a": 20, "b": 0})["pass"]
    with pytest.raises(ValueError, match="negative"):
        sparse_flag_gate({"a": -1, "b": 30})


def test_artifact_roundtrip_preserves_official_order_binary_flags_and_nulls():
    official = [("001", "maricopa-az"), ("002", "maricopa-az"),
                ("003", "eastern-ok"), ("004", "eastern-ok")]
    gap = np.array([0.0, 0.2, 0.3, np.nan])
    gap_defined = np.array([True, True, True, False])
    growth = np.array([0.2, 0.1, 0.05, 0.2])
    growth_defined = np.array([True, True, True, True])
    fire = np.array([0.0, 0.2, 0.0, np.nan])
    fire_defined = np.array([True, True, True, False])
    flag, defined = make_recovery_flag(gap, gap_defined, growth, growth_defined)
    table = output_table(official, gap, gap_defined, growth, growth_defined,
                         fire, fire_defined, flag, defined)
    payload = pq_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["GEOID"].to_pylist() == ["001", "002", "003", "004"]
    assert decoded["recovery_flag"].to_pylist() == [0, 1, 0, None]
    assert decoded["recovery_flag_defined"].to_pylist() == [True, True, True, False]
    assert decoded["tm3_acs_housing_growth"].to_pylist() == [0.2, 0.1, 0.05, 0.2]
    assert pq_bytes(table) == payload


def test_output_artifact_rejects_flag_or_joint_mask_mismatch():
    args = ([("x", "maricopa-az")], np.array([0.2]), np.array([True]),
            np.array([0.1]), np.array([True]), np.array([0.0]), np.array([True]))
    with pytest.raises(ValueError, match="threshold rule"):
        output_table(*args, np.array([0]), np.array([True]))
    with pytest.raises(ValueError, match="jointly-defined"):
        output_table(*args, np.array([1]), np.array([False]))
