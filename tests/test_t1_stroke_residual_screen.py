from __future__ import annotations
import importlib.util
from pathlib import Path
import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_t1-stroke-residual.py"
spec=importlib.util.spec_from_file_location("t1_stroke_residual_screen",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_county_fips_and_one_term_training_only_scaling():
    assert module.county_fips("04013000100")=="04013"
    with pytest.raises(ValueError):module.county_fips("04013")
    y=np.array([1.,2.,3.,4.]);x=np.array([1.,2.,3.,100.])
    pred,fit=module.fit_one_term(y[:3],x[:3],x[3:])
    assert fit["training_feature_mean"]==pytest.approx(2.)
    assert fit["training_feature_sd"]==pytest.approx(np.std([1.,2.,3.]))
    assert pred.shape==(1,) and np.isfinite(pred).all()


def test_zero_baseline_delta_r2_and_group_summary():
    y=np.array([1.,-1.,2.,-2.]);pred=np.array([0.,0.,1.,-1.])
    assert module.score_zero_baseline(y,pred)["delta_r2"]==pytest.approx(.6)
    summary=module.summarize_group(y,pred,np.array([True,True,False,False]))
    assert summary["n"]==2 and summary["mean_absolute_frozen_residual"]==1.
    assert module.summarize_group(y,pred,np.zeros(4,dtype=bool))["support_n"]==0


def test_county_bootstrap_is_deterministic_and_paired():
    records=[{"region":r,"baseline_sse":10.,"model_sse":8.} for r in module.REGIONS for _ in range(3)]
    first=module.county_bootstrap(records)
    assert first==module.county_bootstrap(records)
    assert first["pooled_delta_r2_ci_95"]==pytest.approx([.2,.2])
