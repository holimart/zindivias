from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/screen_g5-commercial-stack-soft-attenuation.py"
spec=importlib.util.spec_from_file_location("g5_commercial_stack_soft_attenuation",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_half_weight_soft_feature_subtracts_only_overlapping_stack_component():
    base=np.array([.6,.2,np.nan]);overlap=np.array([3.,1.,0.]);places=np.array([10.,10.,0.])
    soft,component,defined=module.soft_feature(base,overlap,places)
    assert defined.tolist()==[True,True,False]
    assert component[:2].tolist()==pytest.approx([.3,.1])
    assert soft[:2].tolist()==pytest.approx([.45,.15])
    assert np.isnan(soft[2])


def test_overlap_component_counts_only_covered_exact_g5_qualifying_stacks():
    names=["a","b","c","d","e","e"]
    stacks={(1.,2.):[(0,name) for name in names],
            (3.,4.):[(0,name) for name in ["a","b","c","d"]],
            (5.,6.):[(1,name) for name in names]}
    result=module.overlap_stack_component(stacks,{(1.,2.)},2)
    assert result["qualifying_stack_count"]==2
    assert result["overlapping_qualifying_stack_count"]==1
    assert result["qualifying_stack_place_count"]==12
    assert result["overlapping_qualifying_stack_place_count"]==6
    assert result["overlapping_stacked_place_counts"].tolist()==[6.,0.]


def test_soft_feature_rejects_overlap_component_exceeding_g5_feature():
    with pytest.raises(ValueError,match="outside"):
        module.soft_feature(np.array([.2]),np.array([3.]),np.array([10.]))


def test_effective_stack_retention_formula_never_deletes_an_overlapping_stack():
    total,overlapped=100,80
    effective=(total-.5*overlapped)/total
    assert effective==pytest.approx(.6)
    assert effective>=module.MIN_EFFECTIVE_STACK_FRACTION
