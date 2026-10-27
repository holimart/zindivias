from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/screen_poi_primary_missingness_region_shrinkage.py"
spec = importlib.util.spec_from_file_location("poi_primary_missingness_region_shrinkage", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_sum_zero_four_region_deviations_and_common_slopes():
    common = .4
    contrasts = np.array([.2, -.1, .05])
    result = module.region_coefficients(common, contrasts)
    deviations = np.array([result[r]["deviation"] for r in module.REGIONS])
    assert deviations.sum() == pytest.approx(0.)
    assert result[module.REGIONS[0]]["slope"] == pytest.approx(.6)
    assert result[module.REGIONS[1]]["slope"] == pytest.approx(.3)
    assert result[module.REGIONS[2]]["slope"] == pytest.approx(.45)
    assert result[module.REGIONS[3]]["slope"] == pytest.approx(.25)


def test_fixed_alpha_penalty_is_only_on_three_deviation_contrasts():
    penalty = module.deviation_penalty(7, 4, 100.)
    assert np.allclose(penalty[:4], 0.)
    assert np.allclose(penalty[4:, :4], 0.)
    assert np.allclose(penalty[4:, 4:], 100.*(np.eye(3)+np.ones((3,3))))
    with pytest.raises(ValueError, match="alpha is frozen"):
        module.ridge_coefficients(np.eye(4), np.ones(4), contrast_start=1, alpha=99.)


def test_ridge_solution_shrinks_deviations_without_penalizing_common_slope():
    rng=np.random.default_rng(63)
    n=160
    regions=np.asarray([module.REGIONS[i%4] for i in range(n)],dtype=object)
    x=rng.normal(size=n)
    base=np.column_stack([np.ones(n),rng.normal(size=n)])
    common=.35
    deltas={module.REGIONS[0]:.3,module.REGIONS[1]:-.2,module.REGIONS[2]:.1,module.REGIONS[3]:-.2}
    y=.2*base[:,1]+(common+np.asarray([deltas[r] for r in regions]))*x+rng.normal(0.,.15,n)
    fit=module.fit_fold(y,np.column_stack((base[:,1],rng.normal(size=(n,2)))),x,regions,
                        np.arange(n)%5!=0,np.arange(n)%5==0)
    assert set(fit["region_slopes"])==set(module.REGIONS)
    assert sum(v["deviation"] for v in fit["region_slopes"].values())==pytest.approx(0.,abs=1e-12)
    assert fit["train_n"]==128 and fit["test_n"]==32
    assert np.isfinite(fit["augmented_prediction"]).all()


def test_fold_fit_does_not_leak_heldout_target_and_baseline_is_nested():
    rng=np.random.default_rng(19)
    n=120
    regions=np.asarray([module.REGIONS[i%4] for i in range(n)],dtype=object)
    base=rng.normal(size=(n,3)); share=rng.normal(size=n)
    y=.1*base[:,0]+.2*share+rng.normal(0.,.05,n)
    test=np.arange(n)%5==0; train=~test
    a=module.fit_fold(y,base,share,regions,train,test)
    changed=y.copy(); changed[test]+=1000.
    b=module.fit_fold(changed,base,share,regions,train,test)
    assert np.allclose(a["baseline_prediction"],b["baseline_prediction"])
    assert np.allclose(a["augmented_prediction"],b["augmented_prediction"])


def test_paired_delta_r2_matches_sse_difference():
    y=np.linspace(-1.,1.,60); base=np.zeros_like(y); aug=.1*y
    result=module.delta_r2(y,base,aug)
    assert result["delta_r2"]==pytest.approx(.19)
