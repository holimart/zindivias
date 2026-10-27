from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_road_entropy_fire_interaction.py"
spec=importlib.util.spec_from_file_location("road_entropy_fire_interaction",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_data():
    rng=np.random.default_rng(711)
    regions=np.repeat(np.asarray(module.REGIONS,dtype=object),40)
    folds=np.tile(np.arange(5),32)
    n=len(regions);baseline=rng.normal(size=(n,4));entropy=rng.normal(size=n)
    fire=np.where(rng.random(n)<.35,rng.uniform(.05,1.,n),0.)
    codes=np.asarray([module.REGIONS.index(r) for r in regions])
    y=.15*baseline[:,0]-.1*baseline[:,1]+np.asarray([-.1,.05,.15,-.05])[codes]+.12*entropy*fire+rng.normal(0,.3,n)
    ids=[f"{10000+i:011d}" for i in range(n)]
    return ids,regions,folds,y,baseline,entropy,fire,np.ones(n,dtype=bool)


def test_crossfit_exact_oof_mask_and_training_only_prediction_invariance():
    ids,regions,folds,y,baseline,entropy,fire,mask=synthetic_data()
    original=module.crossfit_interaction(ids,regions,folds,y,baseline,entropy,fire,mask)
    changed_y=y.copy();changed_y[folds==0]+=1000.
    changed=module.crossfit_interaction(ids,regions,folds,changed_y,baseline,entropy,fire,mask)
    held=folds==0
    assert np.allclose(original["baseline_oof"][held],changed["baseline_oof"][held])
    assert np.allclose(original["augmented_oof"][held],changed["augmented_oof"][held])
    assert np.array_equal(np.isfinite(original["baseline_oof"]),mask)
    assert np.array_equal(np.isfinite(original["augmented_oof"]),mask)
    assert len(original["fold_details"])==module.N_FOLDS
    assert all(f["augmented_design_columns"]==f["baseline_design_columns"]+1 for f in original["fold_details"])


def test_interaction_scalers_are_training_fold_only():
    ids,regions,folds,y,baseline,entropy,fire,mask=synthetic_data()
    result=module.crossfit_interaction(ids,regions,folds,y,baseline,entropy,fire,mask)
    first=result["fold_details"][0]
    train=folds!=0
    assert first["training_entropy_mean"]==pytest.approx(entropy[train].mean())
    assert first["training_burn_share_mean"]==pytest.approx(fire[train].mean())
    assert first["training_product_mean"]==pytest.approx(
        (((entropy[train]-entropy[train].mean())/entropy[train].std())*
         ((fire[train]-fire[train].mean())/fire[train].std())).mean())


def test_paired_delta_and_stratified_tract_bootstrap_are_deterministic():
    ids,regions,folds,y,baseline,entropy,fire,mask=synthetic_data()
    fit=module.crossfit_interaction(ids,regions,folds,y,baseline,entropy,fire,mask)
    groups={r:tuple(v[(regions==r)] for v in (y,fit["baseline_oof"],fit["augmented_oof"])) for r in module.REGIONS}
    metrics=module.delta_r2(y,fit["baseline_oof"],fit["augmented_oof"])
    assert metrics["n"]==len(y)
    first=module.bootstrap_deltas(groups,replicates=200,seed=9)
    assert first==module.bootstrap_deltas(groups,replicates=200,seed=9)
    assert set(first["regional_delta_r2_ci_95"])==set(module.REGIONS)
    assert first["pooled_delta_r2_ci_95"][0]<=first["pooled_delta_r2_ci_95"][1]


def test_scaler_rejects_zero_training_variance():
    with pytest.raises(ValueError,match="zero/nonfinite"):
        module._fit_scaler(np.ones(5))
