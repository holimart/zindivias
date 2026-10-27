from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_d3-svi-q3-partial-pooling.py"
spec=importlib.util.spec_from_file_location("d3_svi_q3_partial_pooling",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_data():
    rng=np.random.default_rng(810)
    regions=np.repeat(np.asarray(module.REGIONS,dtype=object),6*16)
    counties=np.concatenate([np.repeat([f"{i:05d}" for i in range(1,7)],16) for _ in module.REGIONS]).astype(object)
    quartile=np.tile(np.repeat(np.arange(1,5),4),24)
    x=rng.normal(size=len(regions));q3=quartile==3
    region_intercept=np.asarray([-.1,.05,.15,-.05])[[module.REGIONS.index(r) for r in regions]]
    y=region_intercept+.3*x+.25*x*q3+rng.normal(0,.15,len(x))
    return y,x,regions,counties,quartile


def test_partial_pooling_shrinks_only_q3_deviation_toward_global_slope():
    y,x,regions,counties,quartile=synthetic_data();held=(regions==module.REGIONS[0])&(counties=="00001");train=~held
    _,weak=module.fit_partial_pool(y[train],x[train],regions[train],quartile[train]==3,
      x[held],regions[held],quartile[held]==3,0.)
    _,strong=module.fit_partial_pool(y[train],x[train],regions[train],quartile[train]==3,
      x[held],regions[held],quartile[held]==3,1e6)
    assert abs(strong["Q3_deviation_coefficient"])<abs(weak["Q3_deviation_coefficient"])
    assert strong["Q3_slope"]==pytest.approx(strong["global_D3_coefficient"],abs=1e-5)
    assert strong["design_columns"]==6


def test_inner_county_folds_are_exclusive_and_cover_outer_training_counties():
    y,x,regions,counties,quartile=synthetic_data()
    folds=module._inner_fold_ids(regions,counties,np.ones(len(y),dtype=bool))
    assert set(folds)==set(range(module.INNER_FOLDS))
    for r in module.REGIONS:
        group={c:set(folds[(regions==r)&(counties==c)]) for c in set(counties[regions==r])}
        assert all(len(v)==1 for v in group.values())
        assert len({next(iter(v)) for v in group.values()})==module.INNER_FOLDS
    selected=module.select_shrinkage_inner(y,x,regions,quartile==3,counties)
    assert selected["selected_shrinkage"] in module.LAMBDA_GRID
    assert selected["inner_heldout_n"]==len(y)
    assert len(selected["inner_fold_diagnostics"])==module.INNER_FOLDS


def test_nested_fit_is_invariant_to_outer_heldout_outcome_changes():
    y,x,regions,counties,quartile=synthetic_data();held=(regions==module.REGIONS[2])&(counties=="00003");train=~held
    a=module.select_shrinkage_inner(y[train],x[train],regions[train],quartile[train]==3,counties[train])
    changed=y.copy();changed[held]+=10000.
    b=module.select_shrinkage_inner(changed[train],x[train],regions[train],quartile[train]==3,counties[train])
    assert a["selected_shrinkage"]==b["selected_shrinkage"]
    assert a["inner_mean_squared_error_by_shrinkage"]==b["inner_mean_squared_error_by_shrinkage"]


def test_paired_county_bootstrap_and_svi_quartiles_are_deterministic():
    y,x,regions,counties,quartile=synthetic_data();q,qcuts=module.assign_svi_quartiles(np.tile(np.repeat([.1,.3,.6,.9],24),4),regions)
    assert set(q)=={1,2,3,4} and set(qcuts)==set(module.REGIONS)
    base=np.zeros_like(y);partial=.1*x*(quartile==3)
    mask=np.ones(len(y),dtype=bool);urban=np.tile(np.repeat([.2,.8],48),4)
    a=module.county_block_bootstrap(y,base,partial,regions,counties,q,urban,mask,replicates=200,seed=14)
    b=module.county_block_bootstrap(y,base,partial,regions,counties,q,urban,mask,replicates=200,seed=14)
    assert a==b
    assert len(a["delta_r2_ci_95"]["partial_pooling_vs_zero_correction"]["regional"])==4
    assert set(a["delta_r2_ci_95"]["partial_pooling_vs_zero_correction"]["svi_quartile_and_urbanity_strata"])=={"rural","urban"}


def test_d3_feature_is_defined_only_when_inputs_valid():
    feature,defined=module.log_housing_building_intensity(np.array([4.,0.,8.,2.]),np.array([2.,1.,0.,1.]),np.array([1.,1.,2.,11.]))
    assert defined.tolist()==[True,False,False,False]
    assert feature[0]==pytest.approx(np.log(2.))
    assert np.isnan(feature[1:]).all()
