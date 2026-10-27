from __future__ import annotations
import importlib.util
from pathlib import Path
import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_track_a_family_ablation.py"
spec=importlib.util.spec_from_file_location("track_a_family_ablation",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_absolute_change_summary_uses_exact_mask():
    values=np.array([-.1,.2,.4]);mask=np.array([True,False,True])
    result=module.stats(values,mask)
    assert result["support_n"]==2
    assert result["p50_abs_change"]==pytest.approx(.25)
    assert result["p95_abs_change"]==pytest.approx(.385)
    assert result["max_abs_change"]==pytest.approx(.4)


def test_registered_family_weighting_renormalizes_only_over_defined_families():
    ids=["00000000001","00000000002","00000000003"]
    data={
      "G1":{g:{("R","gap"):{"family":"G","value":v,"defined":m}} for g,v,m in zip(ids,[.2,.4,0.],[True,False,False])},
      "P1":{g:{("R","gap"):{"family":"P","value":v,"defined":m}} for g,v,m in zip(ids,[.8,.6,.5],[True,True,False])},
    }
    values,defined=module.family_component_estimate(["G1","P1"],data,ids,"R",{"G":.25,"P":.75})
    assert defined.tolist()==[True,True,False]
    assert values.tolist()==pytest.approx([.65,.6,0.])


def test_poi_half_removal_and_component_composite_renormalization():
    f=np.array([.2,.8,.4]);fm=np.array([True,True,False])
    e=np.array([.6,.3,.5]);em=np.array([True,False,False])
    full,mask=module.poi_from_halves(f,fm,e,em)
    assert full.tolist()==pytest.approx([.4,.8,0.]) and mask.tolist()==[True,True,False]
    r=np.array([.3,.1,.2]);b=np.array([.9,.5,.1]);m=np.array([True,True,False])
    composite,defined=module.reweighted_composite(r,b,full,m,m,mask)
    assert composite.tolist()==pytest.approx([.5333333333333333,.4666666666666667,0.])
    assert defined.tolist()==[True,True,False]
