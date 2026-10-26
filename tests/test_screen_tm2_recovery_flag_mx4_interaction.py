from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from screen_tm2_recovery_flag_mx4_interaction import (  # noqa: E402
    interaction_values,
    make_flag,
    output_table,
    parquet_bytes,
)


def test_recovery_flag_preserves_joint_tm2_tm3_definedness_and_thresholds():
    gap = np.array([0.0, 0.2, 0.3, np.nan])
    gap_defined = np.array([True, True, True, False])
    growth = np.array([0.3, 0.1, 0.099, 0.2])
    growth_defined = np.array([True, True, True, True])
    flag, defined = make_flag(gap, gap_defined, growth, growth_defined)
    assert defined.tolist() == [True, True, True, False]
    assert flag.tolist() == [0, 1, 0, 0]


def test_interaction_uses_clipped_mx4_and_preserves_joint_null_mask():
    flag = np.array([0, 1, 1, np.nan])
    flag_defined = np.array([True, True, True, False])
    mx4 = np.array([0.5, -0.1, 1.2, 0.5])
    mx4_defined = np.ones(4, dtype=bool)
    result = interaction_values(flag, flag_defined, mx4, mx4_defined)
    assert result["defined"].tolist() == [True, True, True, False]
    assert result["clipped_mx4"].tolist()[:3] == [0.5, 0.0, 1.0]
    assert result["interaction"].tolist()[:3] == [0.0, 0.0, 1.0]
    assert np.allclose(result["change"][:3], [0.0, 1.0, 0.0])
    assert np.isnan(result["interaction"][3])
    assert result["mx4_clip_count"] == 2


def test_interaction_rejects_invalid_flag_values_and_masks():
    with pytest.raises(ValueError, match="binary"):
        interaction_values(np.array([2]), np.array([True]), np.array([.5]), np.array([True]))
    with pytest.raises(ValueError, match="null"):
        interaction_values(np.array([0]), np.array([False]), np.array([.5]), np.array([True]))
    with pytest.raises(ValueError, match="finite"):
        interaction_values(np.array([1]), np.array([True]), np.array([np.nan]), np.array([True]))
    with pytest.raises(ValueError, match="align"):
        interaction_values(np.array([0]), np.array([True]), np.array([.5, .2]), np.array([True, True]))


def test_output_table_keeps_undefined_interaction_null_and_serialization_deterministic():
    official = [("001", "r"), ("002", "r")]
    result = {"clipped_mx4": np.array([.5, .5]),
              "interaction": np.array([.5, np.nan]),
              "change": np.array([.5, np.nan]),
              "defined": np.array([True, False])}
    table = output_table(official,
                         np.array([.2, np.nan]), np.array([True, False]),
                         np.array([.1, np.nan]), np.array([True, False]),
                         np.array([1, np.nan]), np.array([True, False]),
                         np.array([.5, .3]), np.array([True, True]), result)
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["interaction"].to_pylist() == [.5, None]
    assert decoded["recovery_flag"].to_pylist() == [1, None]
    assert decoded["interaction_defined"].to_pylist() == [True, False]
    assert parquet_bytes(table) == payload
