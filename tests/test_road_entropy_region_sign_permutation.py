from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/audit_road_entropy_region_sign_permutation.py"
spec=importlib.util.spec_from_file_location("road_entropy_region_sign_permutation",SCRIPT)
assert spec is not None and spec.loader is not None
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_blocks():
    regions=[];geoids=[];y=[]
    for rid,region in enumerate(module.REGIONS):
        for county in range(2):
            prefix=f"{10000+rid*10+county:05d}"
            for tract in range(3):
                regions.append(region);geoids.append(prefix+f"{tract:06d}")
                y.append(10.+rid+county*2.+tract*.25)
    return geoids,np.asarray(regions,dtype=object),np.asarray(y)


def test_county_blocks_are_flipped_whole_and_never_reordered():
    ids,regions,y=synthetic_blocks()
    permuted,counts=module.county_block_sign_permutation(ids,regions,y,np.random.default_rng(91))
    county=np.asarray([module.county_fips(g) for g in ids])
    assert counts=={r:2 for r in module.REGIONS}
    for region in module.REGIONS:
        local=regions==region;center=y[local].mean()
        assert len(set(county[local]))==2
        for fips in set(county[local]):
            block=local&(county==fips)
            ratios=(permuted[block]-center)/(y[block]-center)
            assert np.allclose(ratios,ratios[0])
            assert ratios[0] in (-1.,1.)


def test_county_fips_rejects_malformed_geoid():
    assert module.county_fips("04013000100")=="04013"
    with pytest.raises(ValueError):module.county_fips("04013")
    with pytest.raises(ValueError):module.county_fips("04013X00100")


def test_feature_hash_changes_when_feature_or_mask_changes():
    ids,regions,y=synthetic_blocks();x=np.arange(len(ids)*len(module.FEATURES),dtype=float).reshape(len(ids),-1)
    mask=np.ones(len(ids),dtype=bool);original=module.hash_feature_matrix(ids,regions,x,mask)
    changed=x.copy();changed[0,0]+=1.
    assert module.hash_feature_matrix(ids,regions,changed,mask)!=original
    mask[0]=False
    assert module.hash_feature_matrix(ids,regions,x,mask)!=original


def test_signflip_permutation_is_seeded():
    ids,regions,y=synthetic_blocks()
    first,_=module.county_block_sign_permutation(ids,regions,y,np.random.default_rng(17))
    second,_=module.county_block_sign_permutation(ids,regions,y,np.random.default_rng(17))
    assert np.array_equal(first,second)
