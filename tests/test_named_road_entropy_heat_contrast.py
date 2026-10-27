from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_named_road_entropy_heat_contrast.py"
spec = importlib.util.spec_from_file_location("named_road_entropy_heat_contrast", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_quartile_ties_are_in_upper_group_and_uncovered_values_are_excluded():
    heat = np.array([0., 1., 2., 3., 3., np.nan])
    covered = np.array([True, True, True, True, True, False])
    threshold, upper, lower = module.upper_quartile(heat, covered)
    assert threshold == pytest.approx(3.)
    assert upper.tolist() == [False, False, False, True, True, False]
    assert lower.tolist() == [True, True, True, False, False, False]
    with pytest.raises(ValueError, match="exactly match"):
        module.upper_quartile(np.array([0., np.nan]), np.array([True, True]))


def test_equal_region_contrast_and_bootstrap_are_deterministic():
    official = []
    entropies, entropy_mask, heats, heat_mask = [], [], [], []
    for region in module.REGIONS:
        for i in range(160):
            official.append((f"{len(official):011d}", region))
            is_upper = i >= 120
            entropies.append(.8 if is_upper else .2)
            entropy_mask.append(True)
            heats.append(float(i))
            heat_mask.append(True)
    arrays = [np.asarray(x) for x in (entropies, entropy_mask, heats, heat_mask)]
    first = module.analyze(official, *arrays)
    second = module.analyze(official, *arrays)
    assert first == second
    assert first["equal_region_mean_difference_upper_minus_remaining"] == pytest.approx(.6)
    assert first["equal_region_bootstrap_95_ci"] == pytest.approx([.6, .6])
    assert first["regions_with_at_least_30_entropy_defined_tracts_in_each_group"] == 4
    assert first["descriptive_screen_gate_pass"] is True
    assert "no causal or accuracy claim" in first["interpretation"]


def _write_inputs(root: Path, *, duplicate_entropy: bool = False, invalid_entropy_mask: bool = False):
    pairs = [(f"{i:011d}", region) for i, region in enumerate(module.REGIONS)]
    module.EXPECTED_ROWS = len(pairs)
    module.official_scored_pairs = lambda _workspace: pairs
    entropy_path = root / module.ENTROPY_RELATIVE_PATH
    entropy_path.parent.mkdir(parents=True)
    rows = []
    for i, (geoid, region) in enumerate(pairs):
        defined = not (i == 1 and not invalid_entropy_mask)
        value = .2 + i * .1 if (defined or invalid_entropy_mask) else None
        rows.append({"GEOID": geoid, "region": region, "class_mix_entropy": value,
                     "entropy_defined": defined})
    if duplicate_entropy:
        rows[-1] = dict(rows[0])
    pq.write_table(pa.Table.from_pylist(rows), entropy_path)
    strata_dir = root / "defined-flags-inputs"
    strata_dir.mkdir()
    for i, (geoid, region) in enumerate(pairs):
        heat = None if i == 2 else float(10 + i)
        table = pa.table({"GEOID": [geoid], "epht_heat_days_summer": [heat],
                          "epht_covered": [heat is not None], "score_placeholder": [777.]})
        pq.write_table(table, strata_dir / f"{region}-strata-tract-table.parquet")
    return pairs


def test_load_inputs_joins_exact_official_ids_and_preserves_null_masks(tmp_path):
    pairs = _write_inputs(tmp_path)
    official, entropy, entropy_defined, heat, heat_covered, provenance = module.load_inputs(tmp_path)
    assert official == pairs
    assert entropy_defined.tolist() == [True, False, True, True]
    assert np.isnan(entropy[1])
    assert heat_covered.tolist() == [True, True, False, True]
    assert np.isnan(heat[2])
    assert provenance["entropy_artifact"]["defined_ids"] == 3
    assert sum(v["official_ids_joined"] for v in provenance["heat_strata_by_region"].values()) == 4


def test_load_inputs_rejects_duplicate_entropy_ids(tmp_path):
    _write_inputs(tmp_path, duplicate_entropy=True)
    with pytest.raises(ValueError, match="unexpected, duplicate"):
        module.load_inputs(tmp_path)


def test_load_inputs_rejects_finite_value_on_undefined_entropy_row(tmp_path):
    _write_inputs(tmp_path, invalid_entropy_mask=True)
    # The fixture forces a finite class entropy with an undefined mask for row 1.
    # Make its mask false while retaining the finite value.
    path = tmp_path / module.ENTROPY_RELATIVE_PATH
    table = pq.read_table(path)
    masks = table["entropy_defined"].to_pylist()
    masks[1] = False
    table = table.set_column(table.schema.get_field_index("entropy_defined"), "entropy_defined", pa.array(masks))
    pq.write_table(table, path)
    with pytest.raises(ValueError, match="undefined entropy must be null"):
        module.load_inputs(tmp_path)
