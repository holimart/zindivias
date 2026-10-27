from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import numpy as np

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_t1_outside_share_threshold.py"
spec=importlib.util.spec_from_file_location("t1_outside_share_threshold",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
CONFIG=json.loads((Path(__file__).resolve().parents[1]/"config/t1-outside-share-threshold-preregistered.json").read_text())


def synthetic_nested_data():
    regions=list(module.REGIONS);official=[];by_region={r:[] for r in regions};y=[];features={t:[] for t in module.THRESHOLDS};county_ids=[]
    for ri,region in enumerate(regions):
        for ci in range(5):
            fips=f"{10000+ri*100+ci:05d}"
            for ti in range(4):
                geoid=fips+f"{ti:06d}"
                official.append((geoid,region));by_region[region].append(geoid);county_ids.append(fips)
                outcome=ci+ti/10
                y.append(outcome)
                features[0.5].append(outcome)
                features[0.7].append(float(np.sin((ci+1)*(ti+2)+ri)))
                features[0.9].append(float((ci*ci+ti+ri)/30))
    feature_arrays={t:(np.asarray(values,dtype=float),np.ones(len(official),dtype=bool)) for t,values in features.items()}
    return official,by_region,np.asarray(y,dtype=float),feature_arrays,np.asarray(county_ids,dtype=object)


def test_outer_counties_are_stable_disjoint_and_inner_counties_are_ample():
    official,_,_,_,_=synthetic_nested_data()
    first=module.outer_inner_counties(official,CONFIG)
    assert first==module.outer_inner_counties(official,CONFIG)
    outer,inner,split_hash=first
    assert len(split_hash)==64
    for region in module.REGIONS:
        assert len(outer[region])==1
        assert len(inner[region])==4
        assert not (set(outer[region])&set(inner[region]))
        assert len(set(outer[region]+inner[region]))==5


def test_global_threshold_selection_uses_inner_counties_only():
    official,by_region,y,features,counties=synthetic_nested_data()
    outer,inner,_=module.outer_inner_counties(official,CONFIG)
    first=module.select_inner_threshold(official,by_region,features,y,outer,inner)
    assert first["selected_threshold"]==0.5
    changed_outer_y=y.copy()
    outer_ids={fips for values in outer.values() for fips in values}
    for i,fips in enumerate(counties):
        if fips in outer_ids:changed_outer_y[i]=1e12+i
    second=module.select_inner_threshold(official,by_region,features,changed_outer_y,outer,inner)
    assert second==first
    for metrics in first["threshold_metrics"].values():
        assert all(fold["heldout_county_fips"] in inner[fold["region"]] for fold in metrics["county_folds"])
        assert all(fold["train_counties"]==3 for fold in metrics["county_folds"])


def test_outer_county_bootstrap_is_deterministic_and_paired():
    records=[{"region":region,"baseline_sse":10.,"model_sse":8.} for region in module.REGIONS for _ in range(3)]
    result=module.county_bootstrap(records,200,123)
    assert result==module.county_bootstrap(records,200,123)
    assert result["pooled_delta_r2_ci_95"]==[0.2,0.2]
