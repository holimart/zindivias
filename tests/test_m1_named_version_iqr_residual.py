from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_m1-named-version-iqr-residual.py"
spec=importlib.util.spec_from_file_location("m1_named_version_iqr_residual",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_parser_matches_only_supplied_osm_way_versions_and_deduplicates_per_segment():
    versions,count,non_way=module.source_versions([
        {"dataset":"OpenStreetMap","record_id":"w1@2"},
        {"dataset":"OpenStreetMap","record_id":"w1@2"},
        {"dataset":"OpenStreetMap","record_id":"w3@8"},
        {"dataset":"OpenStreetMap","record_id":"w3@8x"},
        {"dataset":"Esri","record_id":"w7@9"},
    ])
    assert versions==(2,8) and count==4 and non_way==1
    assert module.version_for_sources([])==(None,0,0)
    assert module.version_for_sources([{"dataset":"OpenStreetMap","record_id":"w1@2"},{"dataset":"OpenStreetMap","record_id":"w2@4"}])[0]==3.


def test_weighted_iqr_uses_fixed_empirical_q25_q75_left_continuous_rule():
    assert module.weighted_quantile(np.array([1.,5.,9.]),np.array([25.,50.,25.]),.25)==1.
    assert module.weighted_quantile(np.array([1.,5.,9.]),np.array([25.,50.,25.]),.75)==5.
    assert module.weighted_iqr(np.array([1.,5.,9.]),np.array([25.,50.,25.]))==4.
    assert module.weighted_iqr(np.array([7.,7.]),np.array([3.,9.]))==0.
    with pytest.raises(ValueError):module.weighted_quantile(np.array([1.]),np.array([0.]),.25)
    with pytest.raises(ValueError):module.weighted_quantile(np.array([1.]),np.array([1.]),0.)


def test_iqr_by_tract_is_weighted_by_clipped_road_length_and_masks_empty_support():
    values,defined,length=module.iqr_by_tract([[(1.,25.),(5.,50.),(9.,25.)],[],[(2.,60.),(8.,40.)]])
    assert defined.tolist()==[True,False,True]
    assert values[0]==4.
    assert np.isnan(values[1])
    assert values[2]==6.
    assert length.tolist()==[100.,0.,100.]


def test_training_scaler_is_fit_only_on_fold_training_rows():
    y=np.array([1.,2.,3.,4.]);x=np.array([1.,2.,3.,100.])
    pred,fit=module.fit_one_term(y[:3],x[:3],x[3:])
    assert fit["training_feature_mean"]==pytest.approx(2.)
    assert fit["training_feature_sd"]==pytest.approx(np.std([1.,2.,3.]))
    assert pred.shape==(1,) and np.isfinite(pred).all()


def test_county_block_bootstrap_repeats_deterministically():
    records=[{"region":r,"baseline_sse":10.,"model_sse":8.} for r in module.REGIONS for _ in range(3)]
    first=module.county_bootstrap(records)
    assert first==module.county_bootstrap(records)
    assert first["pooled_delta_r2_ci_95"]==pytest.approx([.2,.2])
