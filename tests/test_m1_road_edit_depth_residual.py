from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_m1_road_edit_depth_residual.py"
spec=importlib.util.spec_from_file_location("m1_road_edit_depth_residual",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_county_fips_requires_full_numeric_tract_geoid():
    assert module.county_fips("04013000100")=="04013"
    with pytest.raises(ValueError):module.county_fips("04013")
    with pytest.raises(ValueError):module.county_fips("04013x00100")


def test_one_term_uses_training_only_scaler_and_linear_fit():
    x=np.array([1.,2.,3.,4.,100.]);y=np.array([3.,5.,7.,9.,2.])
    pred,fit=module.fit_one_term(y[:4],x[:4],x[4:])
    assert fit["training_feature_mean"]==pytest.approx(2.5)
    assert fit["training_feature_sd"]==pytest.approx(np.std(x[:4]))
    assert fit["standardized_slope"]==pytest.approx(2*np.std(x[:4]))
    assert pred[0]==pytest.approx(201.)
    with pytest.raises(ValueError,match="finite"):
        module.fit_one_term(y[:4],np.array([1.,np.nan,3.,4.]),x[4:])


def test_delta_r2_is_paired_against_zero_correction():
    result=module.score_zero_baseline(np.array([-2.,-1.,1.,2.]),np.array([-1.,-.5,.5,1.]))
    assert result["frozen_s1_baseline_sse"]==pytest.approx(10.)
    assert result["corrected_sse"]==pytest.approx(2.5)
    assert result["delta_r2"]==pytest.approx(.75)


def test_county_bootstrap_is_deterministic_and_returns_pooled_regional_ci():
    rows=[{"region":region,"baseline_sse":10.,"model_sse":8.} for region in module.REGIONS for _ in (1,2,3)]
    result=module.county_bootstrap(rows,replicates=200,seed=42)
    assert result==module.county_bootstrap(rows,replicates=200,seed=42)
    assert result["pooled_delta_r2_ci_95"]==pytest.approx([.2,.2])
    assert set(result["regional_delta_r2_ci_95"])==set(module.REGIONS)
