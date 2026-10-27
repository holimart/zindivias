from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_d3_housing_intensity_residual.py"
spec=importlib.util.spec_from_file_location("d3_housing_intensity_residual",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_log_housing_building_covariate_preserves_d3_support_rules():
    feature,defined=module.log_housing_building_intensity(
        np.array([100.,50.,0.,20.,10.]),np.array([20.,0.,4.,2.,1.]),np.array([1.,4.,3.,10.,11.]))
    assert np.array_equal(defined,[True,False,False,True,False])
    assert feature[0]==pytest.approx(np.log(5.))
    assert feature[3]==pytest.approx(np.log(10.))
    assert np.isnan(feature[1:3]).all() and np.isnan(feature[4])


def test_log_feature_rejects_misaligned_inputs():
    with pytest.raises(ValueError,match="align"):
        module.log_housing_building_intensity(np.ones(2),np.ones(1),np.ones(2))


def test_one_term_fit_standardizes_only_training_data_and_uses_one_slope():
    x=np.array([1.,2.,3.,4.,100.]);y=np.array([2.,4.,6.,8.,7.])
    pred,fit=module.fit_one_term(y[:4],x[:4],x[4:])
    assert fit["training_feature_mean"]==pytest.approx(2.5)
    assert fit["training_feature_sd"]==pytest.approx(np.std([1.,2.,3.,4.]))
    assert fit["raw_log_ratio_coefficient"]==pytest.approx(2.)
    changed,_=module.fit_one_term(y[:4],x[:4],x[4:]*1000)
    assert not np.allclose(pred,changed)
    with pytest.raises(ValueError,match="finite"):
        module.fit_one_term(y[:4],np.array([1.,np.nan,3.,4.]),x[4:])


def test_frozen_residual_delta_r2_is_paired_against_zero_correction():
    y=np.array([-2.,-1.,1.,2.]);prediction=.5*y
    result=module.score_vs_frozen_s1(y,prediction)
    assert result["frozen_s1_baseline_sse"]==pytest.approx(10.)
    assert result["corrected_sse"]==pytest.approx(2.5)
    assert result["delta_r2"]==pytest.approx(.75)


def test_county_bootstrap_is_deterministic_and_paired():
    rows=[]
    for region in module.REGIONS:
        for county in ("001","003","005"):
            rows.append({"region":region,"baseline_sse":10.,"model_sse":4.})
    first=module.county_block_bootstrap(rows,replicates=200,seed=12)
    again=module.county_block_bootstrap(rows,replicates=200,seed=12)
    assert first==again
    assert first["pooled_delta_r2_ci_95"]==pytest.approx([.6,.6])
