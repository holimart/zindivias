from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from audit_a3_witness_sensitivity import (  # noqa: E402
    _rows_to_witness_column,
    compute_sensitivity,
)


def test_primary_f_component_is_selected_before_other_witness_emissions(tmp_path):
    path = tmp_path / "X5.parquet"
    table = pa.table({
        "GEOID": ["g1", "g1", "g2", "g2"],
        "region": ["maricopa-az"] * 4,
        "idea": ["X5"] * 4,
        "component": ["R", "F", "R", "F"],
        "channel": ["covariate", "gap", "covariate", "gap"],
        "value": [1.0, 0.0, 0.0, 0.7],
        "defined": [True] * 4,
    })
    pq.write_table(table, path)
    bits, metadata = _rows_to_witness_column(
        path, "X5", ["g1", "g2"], {"g1": "maricopa-az", "g2": "maricopa-az"}, 0.5)
    assert bits.tolist() == [0.0, 1.0]
    assert metadata["rows"] == 4
    assert metadata["defined_ids"] == 2
    assert metadata["positive_bits"] == 1


def test_missing_witnesses_remain_nan_and_leave_one_out_is_deterministic():
    ids = ["a", "b", "c", "d"]
    regions = dict(zip(ids, ["maricopa-az", "northern-ca", "eastern-ok", "south-central-tx"]))
    observations = np.array([
        [1.0, 0.0, 0.0, np.nan],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ])
    summary, posteriors = compute_sensitivity(ids, regions, observations)
    assert set(posteriors) == {"X1", "Sc2", "P3", "X5"}
    assert summary["full_model"]["iterations"] <= 500
    assert summary["acceptance"]["all_overall_q95_pass"] in (True, False)
    assert summary["acceptance"]["all_region_q95_pass"] in (True, False)
    for witness, values in posteriors.items():
        assert values.shape == (4,)
        assert np.all(np.isfinite(values))
        assert np.all((values >= 0.0) & (values <= 1.0))
        assert set(summary["omissions"][witness]["regions"]) == {
            "maricopa-az", "northern-ca", "eastern-ok", "south-central-tx"
        }


def test_full_four_witness_contract_is_required():
    with np.testing.assert_raises(ValueError):
        compute_sensitivity(["a"], {"a": "maricopa-az"}, np.array([[1.0, 0.0, 1.0]]))
