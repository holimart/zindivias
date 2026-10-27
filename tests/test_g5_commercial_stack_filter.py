from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
import shapely
from pyproj import Transformer

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_g5-commercial-stack-filter.py"
spec=importlib.util.spec_from_file_location("g5_commercial_stack_filter",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_exact_g5_stack_rule_uses_five_distinct_normalized_nonempty_names():
    members=[(0," Alpha "),(0,"alpha"),(0,"Beta"),(0,"Gamma"),(0,"Delta"),(0,"Epsilon"),(0,"")]
    assert module.qualifying_name_count(members)==5
    stacks={(1.,2.):members,(3.,4.):[(0,"only-one")]*8}
    result=module.g5_stack_features(stacks,1)
    assert result["qualifying_stack_count"]==1
    assert result["feature"].tolist()==[pytest.approx(7/15)]


def test_excluding_a_commercial_stack_recomputes_numerator_and_remaining_poi_denominator():
    stack_a=[(0,name) for name in ["a","b","c","d","e"]]
    stack_b=[(1,name) for name in ["a","b","c","d","e"]]
    stacks={(1.,2.):stack_a,(3.,4.):stack_b,(5.,6.):[(0,"ordinary")]}
    before=module.g5_stack_features(stacks,2)
    after=module.g5_stack_features(stacks,2,{(1.,2.)})
    assert before["qualifying_stack_count"]==2
    assert after["retained_stack_count"]==1
    assert after["removed_stack_count"]==1
    assert after["retained_stack_fraction"]==pytest.approx(.5)
    assert after["place_counts"].tolist()==[1,5]
    assert after["feature"].tolist()==[0.,1.]
    assert after["defined"].tolist()==[True,True]


def test_commercial_class_match_is_fixed_to_explicit_whole_word_categories():
    for value in ("commercial","large industrial building","warehouse","retail mall","shopping center"):
        assert module.is_large_commercial_class(value)
    for value in (None,"residential","noncommercial","building"):assert not module.is_large_commercial_class(value)


def test_footprint_filter_covers_interior_and_boundary_points():
    to_wgs=Transformer.from_crs("EPSG:5070","EPSG:4326",always_xy=True)
    metric=np.array([[50.,50.],[100.,50.],[200.,200.]])
    lon,lat=to_wgs.transform(metric[:,0],metric[:,1])
    points=np.column_stack((lon,lat))
    projected=shapely.transform(shapely.points(points[:,0],points[:,1]),module.TRANSFORMER.transform,interleaved=False)
    boundary_x=float(projected[1].x);boundary_y=float(projected[1].y)
    result=module.points_covered_by_footprints(points,[shapely.box(0,0,boundary_x,boundary_y+10)])
    assert result.tolist()==[True,True,False]


def test_training_only_standardization_and_county_bootstrap_reproducibility():
    y=np.array([1.,2.,3.,4.]);x=np.array([10.,20.,30.,40.])
    _,fit=module.fit_one_term(y[:3],x[:3],np.array([1000.]))
    assert fit["training_mean"]==pytest.approx(20.)
    assert fit["training_sd"]==pytest.approx(np.std([10.,20.,30.]))
    records=[]
    for region in module.REGIONS:
        for i in range(2):records.append({"region":region,"baseline_sse":10.,"unfiltered_sse":9.,"filtered_sse":8.})
    a=module.county_bootstrap(records,replicates=200,seed=8)
    b=module.county_bootstrap(records,replicates=200,seed=8)
    assert a==b
    assert a["pooled_ci_95"]["filtered"]==pytest.approx([.2,.2])
    assert a["regional_ci_95"]["filtered_minus_unfiltered"][module.REGIONS[0]]==pytest.approx([.1,.1])


def test_non_osm_esri_building_memberships_are_disallowed_by_exact_g5_source_guard():
    assert module._allowed_building([{"dataset":"OpenStreetMap"}])
    assert module._allowed_building([{"dataset":"Esri"}])
    assert not module._allowed_building([{"dataset":"OpenStreetMap"},{"dataset":"Microsoft Buildings"}])
    assert not module._allowed_building([{"dataset":"Microsoft Buildings"}])
