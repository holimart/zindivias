from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "screen_t5_attachment_radius", ROOT / "scripts/screen_t5_attachment_radius.py")
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_far_fraction_preserves_assigned_support_and_bounds():
    value, defined = mod.far_fraction(np.array([4, 2, 0]), np.array([1, 2, 0]))
    assert value.tolist() == pytest.approx([0.75, 0.0, 0.0])
    assert defined.tolist() == [True, True, False]


def test_far_fraction_rejects_inconsistent_counts():
    with pytest.raises(ValueError, match="inconsistent"):
        mod.far_fraction(np.array([1]), np.array([2]))


def test_fit_standardizes_from_training_only_and_handles_constant_feature():
    pred, fit = mod.fit_one_term(np.array([1.0, 3.0, 5.0]), np.array([2.0, 4.0, 6.0]), np.array([8.0]))
    assert pred[0] == pytest.approx(7.0)
    assert fit["training_feature_mean"] == pytest.approx(4.0)
    assert fit["training_feature_sd"] == pytest.approx(np.sqrt(8 / 3))
    pred, constant_fit = mod.fit_one_term(np.array([1.0, 2.0, 4.0]), np.ones(3), np.array([1.0, 9.0]))
    assert pred.tolist() == pytest.approx([7 / 3, 7 / 3])
    assert constant_fit["design_rank"] == 1


def test_inner_radius_selection_excludes_outer_held_county_and_is_deterministic():
    counties = np.repeat(np.array(["01001", "01003", "01005", "01007", "01009"]), 3)
    y = np.tile(np.array([-1.0, 0.0, 1.0]), 5)
    x = {
        75: y.copy(),
        150: np.zeros(len(y)),
        300: np.tile(np.array([1.0, 0.0, 1.0]), 5),
        600: np.tile(np.array([0.0, 1.0, 0.0]), 5),
    }
    train_mask = counties != "01009"  # Outer-held county is excluded from all inner folds.
    selected = mod.select_radius_inner(y, x, counties, train_mask)
    assert selected["selected_radius_m"] == 75
    assert selected["inner_heldout_counties"] == ["01001", "01003", "01005", "01007"]
    for radius in mod.RADIUS_CANDIDATES:
        result = selected["inner_scores_by_radius"][str(radius)]
        assert result["inner_county_folds"] == 4
        assert len(result["inner_heldout_counties_sha256"]) == 64
    assert selected == mod.select_radius_inner(y, x, counties, train_mask)


def test_nested_outer_cv_has_exclusive_inner_and_outer_counties_and_complete_oof():
    region_values = []
    county_values = []
    y_values = []
    feature_values = {r: [] for r in mod.RADIUS_CANDIDATES}
    for region_index, region in enumerate(mod.REGIONS):
        for county_index in range(4):
            county = f"{region_index + 1:02d}{county_index + 1:03d}"
            for row_index, value in enumerate((-1.0, 0.0, 1.0)):
                region_values.append(region)
                county_values.append(county)
                y_values.append(value + county_index * 0.1)
                for radius in mod.RADIUS_CANDIDATES:
                    feature_values[radius].append(value if radius == 300 else (row_index + radius / 1000.0) % 1.0)
    y = np.asarray(y_values)
    regions = np.asarray(region_values, dtype=object)
    counties = np.asarray(county_values, dtype=object)
    support = np.ones(len(y), dtype=bool)
    features = {r: np.asarray(v, dtype=np.float64) for r, v in feature_values.items()}
    prediction, records, counts = mod.nested_outer_cv(y, features, regions, counties, support)
    assert np.isfinite(prediction).all()
    assert len(records) == 16
    assert counts == {region: 4 for region in mod.REGIONS}
    for record in records:
        held = record["heldout_county_fips"]
        assert held not in record["inner_selection"]["inner_heldout_counties"]
        assert record["selected_radius_m"] in mod.RADIUS_CANDIDATES
        assert record["heldout_n"] == 3


def test_county_bootstrap_is_paired_and_deterministic():
    records = []
    for region in mod.REGIONS:
        records.extend([
            {"region": region, "baseline_sse": 1.0, "model_sse": 0.5},
            {"region": region, "baseline_sse": 2.0, "model_sse": 1.0},
        ])
    first = mod.county_bootstrap(records)
    second = mod.county_bootstrap(records)
    assert first == second
    assert first["pooled_delta_r2_ci_95"] == pytest.approx([0.5, 0.5])
    assert first["degenerate_replicates"] == 0
