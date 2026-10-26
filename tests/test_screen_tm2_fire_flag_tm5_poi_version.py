from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/screen_tm2-fire-flag_tm5-poi-version.py"
spec = importlib.util.spec_from_file_location("tm2_fire_flag_tm5_poi_version", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
recovery_version_interaction = module.recovery_version_interaction
output_table = module.output_table
parquet_bytes = module.parquet_bytes


def test_product_uses_log_normalized_version_and_binary_flag_with_flag_baseline_change():
    flag = np.array([0., 1., 1., 0.])
    flag_mask = np.ones(4, dtype=bool)
    p90 = np.array([1., 1., 13., 100.])
    p90_mask = np.ones(4, dtype=bool)
    result = recovery_version_interaction(flag, flag_mask, p90, p90_mask)
    expected_v = np.clip(np.log1p(p90) / np.log1p(13.), 0., 1.)
    assert np.allclose(result["normalized_version"], expected_v)
    assert np.allclose(result["interaction"], flag * expected_v)
    assert np.allclose(result["change_from_flag"], np.abs(flag * expected_v - flag))
    assert result["interaction"].min() >= 0.0 and result["interaction"].max() <= 1.0
    assert result["version_clip_count"] == 1


def test_joint_mask_preserves_flag_and_tm5_nulls():
    flag = np.array([0., 1., np.nan, 1.])
    flag_mask = np.array([True, True, False, True])
    p90 = np.array([2., 3., 5., np.nan])
    p90_mask = np.array([True, True, True, False])
    result = recovery_version_interaction(flag, flag_mask, p90, p90_mask)
    assert result["defined"].tolist() == [True, True, False, False]
    assert result["interaction"].tolist()[:2] == [0.0, pytest.approx(np.log1p(3.) / np.log1p(13.))]
    assert np.isnan(result["interaction"][2:]).all()
    assert np.isnan(result["change_from_flag"][2:]).all()


def test_rejects_nonbinary_or_misaligned_flags_and_invalid_defined_versions():
    with pytest.raises(ValueError, match="align"):
        recovery_version_interaction(np.array([1.]), np.array([True]),
                                     np.array([1., 2.]), np.array([True, True]))
    with pytest.raises(ValueError, match="binary"):
        recovery_version_interaction(np.array([2.]), np.array([True]), np.array([1.]), np.array([True]))
    with pytest.raises(ValueError, match="remain null"):
        recovery_version_interaction(np.array([0.]), np.array([False]), np.array([1.]), np.array([True]))
    with pytest.raises(ValueError, match="nonnegative"):
        recovery_version_interaction(np.array([1.]), np.array([True]), np.array([-1.]), np.array([True]))
    with pytest.raises(ValueError, match="positive and finite"):
        recovery_version_interaction(np.array([1.]), np.array([True]), np.array([1.]), np.array([True]), 0.)


def test_output_table_checks_exact_joint_mask_and_preserves_null_values():
    official = [("001", "maricopa-az"), ("002", "northern-ca"), ("003", "eastern-ok")]
    flag = np.array([1., 0., np.nan])
    flag_mask = np.array([True, True, False])
    p90 = np.array([1., np.nan, 2.])
    p90_mask = np.array([True, False, True])
    result = recovery_version_interaction(flag, flag_mask, p90, p90_mask)
    table = output_table(official, flag, flag_mask, p90, p90_mask, result)
    payload = parquet_bytes(table)
    decoded = pq.read_table(pa.BufferReader(payload))
    assert decoded["interaction_defined"].to_pylist() == [True, False, False]
    assert decoded["recovery_flag_x_normalized_version"].to_pylist() == [pytest.approx(np.log1p(1.) / np.log1p(13.)), None, None]
    assert decoded["interaction_change_from_binary_flag"].to_pylist()[1:] == [None, None]
    assert parquet_bytes(table) == payload
    invalid = dict(result)
    invalid["defined"] = np.array([False, False, False])
    with pytest.raises(ValueError, match="exact binary-flag/Tm5 joint mask"):
        output_table(official, flag, flag_mask, p90, p90_mask, invalid)
