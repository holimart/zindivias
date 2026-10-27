from __future__ import annotations
import importlib.util
from pathlib import Path
import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_t5-building-road-residual.py"
spec=importlib.util.spec_from_file_location("t5_building_road_residual_screen",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_training_fold_only_scaling_and_prediction():
    y=np.array([1.,2.,4.,3.]);x=np.array([1.,2.,5.,100.])
    pred,fit=module.fit_one_term(y[:3],x[:3],x[3:])
    assert fit["training_feature_mean"]==pytest.approx(8/3)
    assert fit["training_feature_sd"]==pytest.approx(np.std([1.,2.,5.]))
    assert pred.shape==(1,) and np.isfinite(pred).all()


def test_county_scoring_and_empty_strata():
    assert module.county_fips("04013000100")=="04013"
    with pytest.raises(ValueError):module.county_fips("04013")
    y=np.array([1.,-1.,2.,-2.]);pred=np.array([0.,0.,1.,-1.])
    assert module.score(y,pred)["delta_r2"]==pytest.approx(.6)
    assert module.group_summary(y,pred,np.array([True,True,False,False]))["n"]==2
    assert module.group_summary(y,pred,np.zeros(4,dtype=bool))["n"]==0


def test_county_bootstrap_determinism_and_area_quartile_cuts_are_fixed():
    rows=[{"region":r,"baseline_sse":10.,"model_sse":8.} for r in module.REGIONS for _ in range(3)]
    result=module.county_bootstrap(rows)
    assert result==module.county_bootstrap(rows)
    assert result["pooled_delta_r2_ci_95"]==pytest.approx([.2,.2])
    cuts=np.quantile(np.arange(1.,9.),module.AREA_QUANTILE_CUTS,method="linear")
    assert cuts.tolist()==[2.75,4.5,6.25]
    assert module.MAX_URBAN_RURAL_DECLINE==.005 and module.AREA_QUANTILE_CUTS==(.25,.5,.75)
