from __future__ import annotations

import importlib.util
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/refresh_family_t_exact_orthogonality.py"
spec = importlib.util.spec_from_file_location("family_t_exact_orthogonality", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_load_idea_reconciles_all_official_rows_and_masks(tmp_path):
    official = [("00000000001", "maricopa-az"), ("00000000002", "maricopa-az")]
    path = tmp_path / "T1-v2.parquet"
    pq.write_table(pa.table({
        "GEOID": [g for g, _ in official], "region": [r for _, r in official],
        "idea": ["T1-v2"] * 2, "channel": ["gap"] * 2,
        "value": [0.4, 0.], "defined": [True, False],
    }), path)
    data = module.load_idea(path, "T1-v2", official)
    values, defined = data["gap"]
    assert defined.tolist() == [True, False]
    assert values[0] == pytest.approx(.4)
    assert values[1] != values[1]


def test_load_idea_rejects_wrong_official_partition(tmp_path):
    path = tmp_path / "T1-v2.parquet"
    pq.write_table(pa.table({
        "GEOID": ["00000000001"], "region": ["maricopa-az"],
        "idea": ["T1-v2"], "channel": ["gap"], "value": [.2], "defined": [True],
    }), path)
    with pytest.raises(ValueError, match="expected 2"):
        module.load_idea(path, "T1-v2", [("00000000001", "maricopa-az"),
                                          ("00000000002", "maricopa-az")])
