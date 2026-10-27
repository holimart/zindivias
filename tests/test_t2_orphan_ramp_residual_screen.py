from __future__ import annotations
import importlib.util
from pathlib import Path
import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_t2-orphan-ramp-residual.py"
spec=importlib.util.spec_from_file_location("t2_orphan_ramp_residual_screen",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_one_term_uses_only_training_fold_scaling():
    y=np.array([1.,2.,4.,3.]);x=np.array([1.,2.,5.,100.])
    prediction,fit=module.fit_one_term(y[:3],x[:3],x[3:])
    assert fit["training_feature_mean"]==pytest.approx(8/3)
    assert fit["training_feature_sd"]==pytest.approx(np.std([1.,2.,5.]))
    assert fit["heldout_n"]==1 and np.isfinite(prediction).all()


def test_county_key_scoring_and_empty_stratum_behavior():
    assert module.county_fips("04013000100")=="04013"
    with pytest.raises(ValueError):module.county_fips("04013")
    y=np.array([1.,-1.,2.,-2.]);pred=np.array([0.,0.,1.,-1.])
    assert module.score(y,pred)["delta_r2"]==pytest.approx(.6)
    assert module.group_summary(y,pred,np.array([True,True,False,False]))["n"]==2
    assert module.group_summary(y,pred,np.zeros(4,dtype=bool))["n"]==0


def test_county_bootstrap_is_deterministic_and_region_blocked():
    rows=[{"region":r,"baseline_sse":10.,"model_sse":8.} for r in module.REGIONS for _ in range(3)]
    result=module.county_bootstrap(rows)
    assert result==module.county_bootstrap(rows)
    assert result["pooled_delta_r2_ci_95"]==pytest.approx([.2,.2])
    assert set(result["regional_delta_r2_ci_95"])==set(module.REGIONS)
