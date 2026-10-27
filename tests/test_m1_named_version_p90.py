from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_m1-named-version-p90.py"
spec=importlib.util.spec_from_file_location("m1_named_version_p90",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_only_supplied_openstreetmap_way_record_id_versions_are_parsed_and_deduplicated():
    versions,osm_ids,non_way=module.source_versions([
        {"dataset":"OpenStreetMap","record_id":"w123@2"},
        {"dataset":"OpenStreetMap","record_id":"w123@2"},
        {"dataset":"OpenStreetMap","record_id":"w456@8"},
        {"dataset":"OpenStreetMap","record_id":"r123@99"},
        {"dataset":"Esri","record_id":"w999@999"},
    ])
    assert versions==(2,8)
    assert osm_ids==4
    assert non_way==1
    assert module.version_for_sources([])==(None,0,0)
    assert module.version_for_sources([{"dataset":"OpenStreetMap","record_id":"w1@2"}])[0]==2.


def test_weighted_empirical_quantile_selects_first_cumulative_weight_at_threshold():
    assert module.weighted_quantile(np.array([1.,5.,9.]),np.array([80.,10.,10.]),.9)==5.
    assert module.weighted_quantile(np.array([9.,1.,5.]),np.array([10.,80.,10.]),.9)==5.
    assert module.weighted_quantile(np.array([2.,4.]),np.array([1.,9.]),.9)==4.
    with pytest.raises(ValueError):module.weighted_quantile(np.array([1.]),np.array([0.]),.9)
    with pytest.raises(ValueError):module.weighted_quantile(np.array([1.]),np.array([1.]),0.)


def test_p90_by_tract_uses_clipped_segment_length_and_marks_empty_tract_undefined():
    values,defined,length=module.p90_by_tract([[(1.,80.),(5.,10.),(9.,10.)],[],[(2.,3.),(7.,7.)]])
    assert defined.tolist()==[True,False,True]
    assert values[0]==5.
    assert values[1]!=values[1]
    assert values[2]==7.
    assert length.tolist()==[100.,0.,10.]


def test_county_fips_and_training_fold_scaler_are_exact_and_training_only():
    assert module.county_fips("04013000100")=="04013"
    with pytest.raises(ValueError):module.county_fips("04013")
    y=np.array([3.,5.,7.,9.,2.]);x=np.array([1.,2.,3.,4.,100.])
    pred,fit=module.fit_one_term(y[:4],x[:4],x[4:])
    assert fit["training_feature_mean"]==pytest.approx(2.5)
    assert fit["training_feature_sd"]==pytest.approx(np.std(x[:4]))
    assert pred[0]==pytest.approx(201.)


def test_county_block_bootstrap_is_deterministic_and_uses_paired_sse():
    records=[{"region":r,"baseline_sse":10.,"model_sse":8.} for r in module.REGIONS for _ in range(3)]
    first=module.county_bootstrap(records,replicates=200,seed=42)
    assert first==module.county_bootstrap(records,replicates=200,seed=42)
    assert first["pooled_delta_r2_ci_95"]==pytest.approx([.2,.2])
    assert set(first["regional_delta_r2_ci_95"])==set(module.REGIONS)
