from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_d1_rural_language_context_residual.py"
spec=importlib.util.spec_from_file_location("d1_rural_language_context_residual",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_region_fold_scaler_uses_training_rows_only_and_fits_one_slope():
    x=np.array([0.,1.,2.,3.,100.]);y=np.array([0.,1.,2.,3.,7.])
    train=np.array([True,True,True,True,False]);test=~train
    result=module.fit_region_fold(y,x,train,test)
    assert result["training_term_mean"]==pytest.approx(1.5)
    assert result["training_term_sd"]==pytest.approx(np.std([0.,1.,2.,3.]))
    assert result["training_n"]==4 and result["heldout_n"]==1
    changed_y=y.copy();changed_y[test]+=1000.
    changed=module.fit_region_fold(changed_y,x,train,test)
    assert np.allclose(result["augmented_prediction"],changed["augmented_prediction"])
    assert result["raw_term_coefficient"]==pytest.approx(1.)


def test_fold_rejects_overlap_missing_values_and_empty_partitions():
    y=np.array([1.,2.,3.]);x=np.array([0.,1.,2.])
    with pytest.raises(ValueError,match="disjoint"):
        module.fit_region_fold(y,x,np.array([True,False,False]),np.array([True,False,False]))
    with pytest.raises(ValueError,match="finite"):
        module.fit_region_fold(y,np.array([0.,np.nan,2.]),np.array([True,False,False]),np.array([False,True,False]))
    with pytest.raises(ValueError,match="nonempty"):
        module.fit_region_fold(y,x,np.array([True,False,False]),np.array([False,False,False]))


def test_paired_delta_r2_uses_shared_target_variance():
    y=np.linspace(-1.,1.,80);baseline=np.zeros_like(y);augmented=.1*y
    result=module.paired_delta_r2(y,baseline,augmented)
    assert result["delta_r2"]==pytest.approx(.19)
    assert result["augmented_r2"]-result["baseline_r2"]==pytest.approx(result["delta_r2"])


def test_county_bootstrap_is_paired_and_reproducible():
    regions=np.repeat(np.asarray(module.REGIONS),8)
    counties=np.tile(np.repeat(["01001","01003"],4),4)
    y=np.linspace(-1.,1.,32);baseline=np.zeros_like(y);augmented=.2*y
    a=module.county_block_bootstrap(y,baseline,augmented,regions,counties,replicates=200,seed=8)
    b=module.county_block_bootstrap(y,baseline,augmented,regions,counties,replicates=200,seed=8)
    assert a==b
    assert a["ci_95_percentile"][0]>0.
