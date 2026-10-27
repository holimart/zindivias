from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_source_road_entropy_interaction.py"
spec = importlib.util.spec_from_file_location("source_road_entropy_interaction", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_data():
    rng = np.random.default_rng(771)
    regions = np.repeat(np.asarray(module.REGIONS, dtype=object), 250)
    ids = [f"{i:011d}" for i in range(len(regions))]
    folds = np.tile(np.arange(5, dtype=np.int8), len(module.REGIONS) * 50)
    x = rng.normal(size=(len(regions), 5))
    for region_index, region in enumerate(module.REGIONS):
        ix = np.flatnonzero(regions == region)
        x[ix, 0] = 2. + .3 * x[ix, 0]
        x[ix, 1] = .1 * x[ix, 1]
        x[ix, 2] = .1 * x[ix, 2]
        x[ix, 3] = .5 + .1 * region_index + .12 * x[ix, 3]
        x[ix, 4] = .4 - .04 * region_index + .15 * x[ix, 4]
    centered_poi = np.empty(len(regions)); centered_road = np.empty(len(regions))
    for region in module.REGIONS:
        ix = regions == region
        centered_poi[ix] = (x[ix, 3] - x[ix, 3].mean()) / x[ix, 3].std()
        centered_road[ix] = (x[ix, 4] - x[ix, 4].mean()) / x[ix, 4].std()
    region_effect = np.asarray([.02 * module.REGIONS.index(r) for r in regions])
    y = (region_effect + .12 * x[:, 0] + .4 * x[:, 1] - .2 * x[:, 2]
         + 1.2 * centered_poi * centered_road + rng.normal(0., .03, len(regions)))
    mask = np.ones(len(regions), dtype=bool)
    return ids, regions, folds, y, x, mask


def test_paired_delta_r2_uses_common_support_and_sst():
    y = np.array([0., 1., 2., 3., 4.])
    baseline = np.array([.5, .8, 1.9, 2.7, 4.2])
    interaction = np.array([0., 1., 2., 3., 4.])
    metrics = module.paired_delta_r2(y, baseline, interaction)
    assert metrics["interaction_r2"] == pytest.approx(1.)
    assert metrics["delta_r2"] == pytest.approx(metrics["interaction_r2"] - metrics["baseline_r2"])


def test_crossfit_interaction_has_region_specific_training_only_entropy_scaling():
    ids, regions, folds, y, x, mask = synthetic_data()
    result = module.crossfit_interaction(ids, regions, folds, y, x, mask)
    assert len(result["fold_metrics"]) == 5
    assert all(row["delta_r2"] > 0. for row in result["fold_metrics"])
    assert all(np.isfinite(result[k]).all() for k in
               ("baseline_oof", "interaction_oof", "poi_z_oof", "road_z_oof", "product_oof"))
    assert np.allclose(result["product_oof"], result["poi_z_oof"] * result["road_z_oof"])
    for detail in result["fold_model_details"]:
        assert len(detail["training_only_within_region_entropy_scalers"]) == 4
        assert detail["interaction_design_columns"] == detail["baseline_design_columns"] + 1
        assert detail["baseline_design_rank"] == detail["baseline_design_columns"]
        assert detail["interaction_design_rank"] == detail["interaction_design_columns"]


def test_heldout_entropy_does_not_change_its_fold_scalers():
    ids, regions, folds, y, x, mask = synthetic_data()
    before = module.crossfit_interaction(ids, regions, folds, y, x, mask)
    heldout = folds == 0
    x_changed = x.copy()
    x_changed[heldout, 3] += 1e5
    x_changed[heldout, 4] -= 1e5
    after = module.crossfit_interaction(ids, regions, folds, y, x_changed, mask)
    a = before["fold_model_details"][0]["training_only_within_region_entropy_scalers"]
    b = after["fold_model_details"][0]["training_only_within_region_entropy_scalers"]
    assert a == b


def test_crossfit_rejects_nonfixed_folds_and_nonfinite_complete_cases():
    ids, regions, folds, y, x, mask = synthetic_data()
    bad_folds = folds.copy(); bad_folds[0] = 7
    with pytest.raises(ValueError, match="fixed fold IDs"):
        module.crossfit_interaction(ids, regions, bad_folds, y, x, mask)
    x[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite outcome and predictors"):
        module.crossfit_interaction(ids, regions, folds, y, x, mask)
