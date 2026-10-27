from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_d1-svi-region-residuals.py"
spec=importlib.util.spec_from_file_location("d1_svi_region_residuals",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_quartiles_are_region_specific_and_ties_follow_frozen_right_rule():
    regions=np.repeat(np.asarray(module.REGIONS,dtype=object),8)
    svi=np.tile(np.arange(8,dtype=float),4)
    valid=np.ones(32,dtype=bool)
    quartile,cuts=module.assign_region_quartiles(svi,regions,valid)
    assert cuts=={r:[1.75,3.5,5.25] for r in module.REGIONS}
    assert set(quartile)=={1,2,3,4}
    tied=np.full(32,0.5);tied[0]=.25;tied[1]=.75
    q,c=module.assign_region_quartiles(tied,regions,np.ones(32,dtype=bool))
    assert np.isin(q,[1,2,3,4]).all()
    assert len(c)==4


def test_smd_is_cell_minus_region_complement_over_pooled_sd():
    values=np.array([3.,1.,2.,1.,-1.,0.]);cell=np.array([1,1,1,0,0,0],dtype=bool)
    result=module.standardized_mean_difference(values,cell)
    assert result["cell_n"]==3 and result["complement_n"]==3
    assert result["mean_difference"]==pytest.approx(2.)
    assert result["standardized_mean_difference"]==pytest.approx(2.)


def test_all_16_region_quartile_cells_and_county_bootstrap_are_finite():
    rng=np.random.default_rng(17);regions=np.repeat(np.asarray(module.REGIONS,dtype=object),80)
    quartiles=np.tile(np.repeat(np.arange(1,5,dtype=np.int8),20),4)
    counties=np.tile(np.repeat(["01001","01003","01005","01007"],5),16)
    residual=rng.normal(size=320)+.2*(quartiles==3)
    mask=np.ones(320,dtype=bool)
    contrasts=module.region_cell_contrasts(residual,regions,quartiles,counties,mask)
    assert all(contrasts[r][f"q{q}"]["cell_n"]==20 for r in module.REGIONS for q in range(1,5))
    first=module.county_block_bootstrap(residual,regions,quartiles,counties,mask,replicates=200,seed=4)
    again=module.county_block_bootstrap(residual,regions,quartiles,counties,mask,replicates=200,seed=4)
    assert first==again
    assert len(first["cell_smd_ci_95"])==16
    assert all(len(v["ci_95"])==2 and np.isfinite(v["ci_95"]).all() for v in first["cell_smd_ci_95"].values())
    assert np.isfinite(first["max_absolute_smd_ci_95"]).all()


def test_smd_rejects_empty_complement_and_zero_variance():
    with pytest.raises(ValueError,match="complement"):
        module.standardized_mean_difference(np.arange(4.),np.ones(4,dtype=bool))
    with pytest.raises(ValueError,match="variance"):
        module.standardized_mean_difference(np.ones(8),np.array([1,1,1,1,0,0,0,0],dtype=bool))
