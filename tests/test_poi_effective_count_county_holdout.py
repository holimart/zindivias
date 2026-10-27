from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_effective_count_county_holdout.py"
spec = importlib.util.spec_from_file_location("poi_effective_count_county_holdout", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_inputs():
    rng = np.random.default_rng(401)
    regions = np.repeat(np.asarray(module.REGIONS, dtype=object), 70)
    counties = np.concatenate([np.repeat(np.asarray([f"{ri:02d}{ci:03d}" for ci in (1, 2)], dtype=object), 35)
                               for ri in range(1, 5)])
    ids = [f"{i:011d}" for i in range(len(regions))]
    x = rng.normal(size=(len(regions), 4))
    region_shift = np.asarray([.1 * module.REGIONS.index(r) for r in regions])
    y = 1. + region_shift + .4 * x[:, 0] - .2 * x[:, 1] + .1 * x[:, 2] + rng.normal(0., .1, len(regions))
    mask = np.ones(len(regions), dtype=bool)
    return ids, regions, counties, y, x, mask


def test_county_fips_extraction_is_exact_and_rejects_bad_geoids():
    assert module.county_fips_from_geoid("04013000100") == "04013"
    with pytest.raises(ValueError, match="11 numeric characters"):
        module.county_fips_from_geoid("4013000100")
    with pytest.raises(ValueError, match="11 numeric characters"):
        module.county_fips_from_geoid("04013x00100")


def test_standardization_uses_training_rows_only():
    train = np.array([[1., 2.], [3., 6.], [5., 10.]])
    test = np.array([[100., -100.]])
    train_z, test_z, mean, sd = module.train_standardize(train, test)
    changed = module.train_standardize(train, np.array([[-1e9, 1e9]]))
    assert np.allclose(mean, [3., 6.])
    assert np.allclose(sd, [np.sqrt(8. / 3.), np.sqrt(32. / 3.)])
    assert np.allclose(changed[2], mean) and np.allclose(changed[3], sd)
    assert np.allclose(train_z.mean(axis=0), 0.)
    assert np.allclose(test_z, [[(100. - 3.) / sd[0], (-100. - 6.) / sd[1]]])


def test_leave_one_county_out_uses_heldout_training_mean_baseline_and_common_support():
    ids, regions, counties, y, x, mask = synthetic_inputs()
    result = module.county_loco(ids, regions, counties, y, x, mask)
    assert len(result["county_records"]) == 8
    assert result["eligible_counties"] == 8
    assert result["pooled_r2"] == pytest.approx(
        1. - result["pooled_model_sse"] / result["pooled_baseline_sse"])
    assert result["positive_eligible_share"] == pytest.approx(
        result["positive_eligible_counties"] / result["eligible_counties"])
    for row in result["county_records"]:
        held = mask & (counties == row["county_fips"])
        assert row["heldout_n"] == int(held.sum()) == 35
        baseline_sse = np.sum((y[held] - row["training_county_mean"]) ** 2)
        model_sse = np.sum((y[held] - result["model_oof"][held]) ** 2)
        assert row["heldout_baseline_sse"] == pytest.approx(baseline_sse)
        assert row["heldout_model_sse"] == pytest.approx(model_sse)


def test_heldout_outcome_does_not_change_its_loco_fit_or_baseline():
    ids, regions, counties, y, x, mask = synthetic_inputs()
    before = module.county_loco(ids, regions, counties, y, x, mask)
    target_county = sorted(set(counties))[0]
    held = counties == target_county
    changed_y = y.copy(); changed_y[held] += 1000.
    after = module.county_loco(ids, regions, counties, changed_y, x, mask)
    assert before["county_records"][0]["county_fips"] == target_county
    assert before["county_records"][0]["training_county_mean"] == after["county_records"][0]["training_county_mean"]
    assert np.allclose(before["model_oof"][held], after["model_oof"][held])
    assert np.allclose(before["baseline_oof"][held], after["baseline_oof"][held])


def test_county_loco_rejects_nonfinite_complete_cases():
    ids, regions, counties, y, x, mask = synthetic_inputs()
    x[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite outcome and predictors"):
        module.county_loco(ids, regions, counties, y, x, mask)
