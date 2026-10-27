#!/usr/bin/env python3
"""County-block sign-permutation audit of fixed source/road entropy terms vs frozen S1 residuals."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
from screen_source_road_entropy_interaction import (  # noqa: E402
    EXPECTED_N, FEATURES, N_FOLDS, REGIONS, load_inputs, mask_sha256,
    paired_delta_r2, sha256_file,
)
from screen_source_road_entropy_region_shrinkage import (  # noqa: E402
    RIDGE_ALPHA, crossfit_region_shrinkage,
)

PRIOR_REPORT=ROOT/"reports/source-road-entropy-region-shrinkage.json"
PRIOR_MODEL_SCRIPT=ROOT/"scripts/screen_source_road_entropy_region_shrinkage.py"
EXPECTED_PRIOR_MODEL_SCRIPT_SHA="85a9c5f1d4969bf4c86e93c1c510ebe138ab751c45d0b71b170432f7cf088915"
EXPECTED_PRIOR_REPORT_SHA="c942705291aab182d178215a9b69aea6f47fd76ef56d8c937eec1b4e9a53569d"
EXPECTED_COMPLETE_CASE_MASK="68afdf19c4b82663683a7e7489730b762b883628991a34000d28e00089ad8485"
EXPECTED_FIXED_FOLD_SHA="c8b4f9875dbd127a1627458cba0d824bd743d5e5047b38ef243eb6608c3f5ce5"
EXPECTED_PRIOR_DELTA_R2=-0.0010153704805050529
PERMUTATIONS=1000
SEED=20261103


def county_fips(geoid:str)->str:
    value=str(geoid)
    if len(value)!=11 or not value.isdigit():raise ValueError(f"GEOID must be 11 numeric characters: {value!r}")
    return value[:5]


def hash_feature_matrix(geoids:list[str],regions:np.ndarray,features:np.ndarray,mask:np.ndarray)->str:
    x=np.asarray(features,dtype=np.float64);use=np.asarray(mask,dtype=bool);reg=np.asarray(regions,dtype=object)
    if x.shape!=(len(geoids),len(FEATURES)) or use.shape!=(len(geoids),) or reg.shape!=(len(geoids),):
        raise ValueError("feature hash inputs are misaligned")
    lines=[]
    for i,(geoid,region) in enumerate(zip(geoids,reg)):
        values=",".join(format(float(v),".17g") if np.isfinite(v) else "" for v in x[i])
        lines.append(f"{geoid},{region},{int(use[i])},{values}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def county_block_sign_permutation(geoids:list[str],regions:np.ndarray,outcome:np.ndarray,
                                  rng:np.random.Generator,mask:np.ndarray|None=None)->tuple[np.ndarray,dict[str,int]]:
    """Independently sign-flip region-centered residual blocks by county; never shuffle tracts."""
    y=np.asarray(outcome,dtype=np.float64);reg=np.asarray(regions,dtype=object)
    use=np.ones(len(y),dtype=bool) if mask is None else np.asarray(mask,dtype=bool)
    if len(geoids)!=len(y) or reg.shape!=y.shape or use.shape!=y.shape or not np.isfinite(y).all():
        raise ValueError("county permutation requires aligned finite full-scope residuals and mask")
    if set(reg.tolist())!=set(REGIONS):raise ValueError("all official regions are required")
    county=np.asarray([county_fips(g) for g in geoids],dtype=object)
    permuted=y.copy();counts={}
    for region in REGIONS:
        in_region=reg==region
        county_ids=sorted(set(county[in_region].tolist()))
        if len(county_ids)<2:raise ValueError(f"{region} requires at least two county blocks")
        center_mask=in_region&use
        if not center_mask.any():raise ValueError(f"{region} has no residuals on fixed analysis support")
        regional_center=float(y[center_mask].mean())
        signs=rng.choice(np.array([-1.,1.]),size=len(county_ids))
        for fips,sign in zip(county_ids,signs):
            block=in_region&(county==fips)
            permuted[block]=regional_center+sign*(y[block]-regional_center)
        counts[region]=len(county_ids)
    if not np.isfinite(permuted).all():raise AssertionError("permuted residuals must remain finite")
    return permuted,counts


def fixed_model_delta(geoids:list[str],regions:np.ndarray,folds:np.ndarray,outcome:np.ndarray,
                      features:np.ndarray,mask:np.ndarray)->dict[str,Any]:
    fit=crossfit_region_shrinkage(geoids,regions,folds,outcome,features,mask,alpha=RIDGE_ALPHA)
    pooled=paired_delta_r2(outcome[mask],fit["baseline_oof"][mask],fit["augmented_oof"][mask])
    regional={}
    for region in REGIONS:
        use=mask&(regions==region)
        regional[region]={"n":int(use.sum()),**paired_delta_r2(
            outcome[use],fit["baseline_oof"][use],fit["augmented_oof"][use])}
    return {"pooled":pooled,"regional":regional}


def run(workspace:Path,report_path:Path)->dict[str,Any]:
    prior=json.loads(PRIOR_REPORT.read_text(encoding="utf-8"))
    prior_sha=sha256_file(PRIOR_REPORT)
    if prior_sha!=EXPECTED_PRIOR_REPORT_SHA:
        raise ValueError("frozen source-road entropy region-shrinkage report SHA mismatch")
    if sha256_file(PRIOR_MODEL_SCRIPT)!=EXPECTED_PRIOR_MODEL_SCRIPT_SHA:
        raise ValueError("fixed source-road entropy model implementation SHA mismatch")
    if (not np.isclose(prior["measure_after"]["pooled"]["delta_r2"],EXPECTED_PRIOR_DELTA_R2,rtol=0.,atol=1e-14)
        or prior["scope"]["complete_case_mask_sha256"]!=EXPECTED_COMPLETE_CASE_MASK
        or prior["scope"]["fold_assignment_sha256"]!=EXPECTED_FIXED_FOLD_SHA
        or prior["measure_after"]["positive_regions"]!=0):
        raise ValueError("pre-existing entropy MEASURE-BEFORE or frozen folds/mask changed")
    official,regions,residual,features,joint,folds,provenance=load_inputs(workspace)
    geoids=[g for g,_ in official]
    if len(official)!=EXPECTED_N or int(joint.sum())!=9247:
        raise ValueError("frozen entropy analysis support differs from 9,247 complete cases")
    if mask_sha256(joint)!=EXPECTED_COMPLETE_CASE_MASK:
        raise ValueError("complete-case mask does not match frozen source-road entropy screen")
    fold_sha=hashlib.sha256(np.asarray(folds,dtype=np.int8).tobytes()).hexdigest()
    if fold_sha!=EXPECTED_FIXED_FOLD_SHA:
        raise ValueError("fixed S1 fold assignment changed")
    observed=fixed_model_delta(geoids,regions,folds,residual,features,joint)
    if not np.isclose(observed["pooled"]["delta_r2"],prior["measure_after"]["pooled"]["delta_r2"],rtol=0.,atol=1e-12):
        raise ValueError("recomputed observed ΔR² differs from fixed pre-existing screen")
    for region in REGIONS:
        old=prior["measure_after"]["regional"][region]["delta_r2"]
        if not np.isclose(observed["regional"][region]["delta_r2"],old,rtol=0.,atol=1e-12):
            raise ValueError(f"recomputed observed regional ΔR² differs in {region}")

    rng=np.random.default_rng(SEED);null=np.empty(PERMUTATIONS,dtype=np.float64);perm_support={}
    county=np.asarray([county_fips(g) for g in geoids],dtype=object)
    for iteration in range(PERMUTATIONS):
        y_perm,counts=county_block_sign_permutation(geoids,regions,residual,rng,joint)
        if not perm_support:perm_support=counts
        elif counts!=perm_support:raise AssertionError("county blocks changed during permutation")
        perm_fit=fixed_model_delta(geoids,regions,folds,y_perm,features,joint)
        null[iteration]=float(perm_fit["pooled"]["delta_r2"])
    p_value=float((1+np.count_nonzero(null>=observed["pooled"]["delta_r2"]))/ (PERMUTATIONS+1))
    county_support={}
    for region in REGIONS:
        use=joint&(regions==region)
        county_support[region]={"supported_tracts":int(use.sum()),"fixed_county_blocks":len(set(county[use].tolist())),
          "supported_counties":sorted(set(county[use].tolist()))}
    quantiles={str(q):float(np.quantile(null,q)) for q in (0.,.01,.025,.05,.5,.95,.975,.99,1.)}
    passes=bool(observed["pooled"]["delta_r2"]>0. and p_value<.05)
    report={"schema_version":1,"screen":"County-block sign-permutation audit of fixed source/road entropy terms against frozen S1 residual; unregistered",
      "measure_before":{"source_road_entropy_region_shrinkage_pooled_delta_r2":prior["measure_after"]["pooled"]["delta_r2"],
        "positive_regions":prior["measure_after"]["positive_regions"],"prior_report_sha256":prior_sha,
        "complete_case_ids":int(joint.sum())},
      "method":{"outcome":"Frozen S1 cross-fitted disagreement residual; not refit or reconstructed.",
        "features":"Reuse the exact provided POI source-class entropy, road-class entropy, log1p POI count, frozen Mx4 F, and frozen S1 lag features; no feature transformation or mask change.",
        "model":"Exactly the fixed prior region-interaction ridge screen: region intercepts and entropy main effects; shared entropy product plus sum-zero region deviations with fixed ridge alpha=100, penalty on deviations only.",
        "folds":"The exact five fixed GEOID folds from the frozen S1 artifact, verified by SHA-256; no fold reassignment.",
        "permutation":"For each null replicate, independently draw one Rademacher sign per five-digit county within each official region and multiply the whole county block of region-centered residuals by that sign, adding back its original region mean. County blocks remain intact and in their original tract rows; no tract-level shuffling, feature shuffling, or change in masks/folds.",
        "null_assumption":"The county-block sign-flip null assumes region-centered residual blocks are sign-exchangeable under no feature signal; it preserves each block's tract-level dependence and magnitude but is not an arbitrary-exchangeability test for every possible county distribution.",
        "retuning":"No model or hyperparameter retuning; rerun the same fixed cross-fit and alpha=100 for every permuted outcome.",
        "p_value":"One-sided empirical p=(1 + count(null pooled ΔR² >= observed pooled ΔR²))/(1 + 1000), for positive incremental ΔR².",
        "unregistered":True,"interpretation_limit":"Diagnostic null audit of provided-feature association only; not target accuracy, causality, or estimator-promotion evidence."},
      "scope":{"official_ids":len(official),"official_geoid_region_sha256":provenance["official_geoid_region_sha256"],
        "complete_case_ids":int(joint.sum()),"complete_case_mask_sha256":mask_sha256(joint),
        "frozen_residual_mask_sha256":mask_sha256(np.isfinite(residual)),"fold_assignment_sha256":fold_sha,
        "fold_support":{str(f):int(np.sum(joint&(folds==f))) for f in range(N_FOLDS)},
        "county_block_support":county_support,"labels_read":False,"sample_score_values_read":False,
        "removed_or_equivalent_references_read":False,"external_data_used":[],"leaderboard_feedback_used":False,"uploads":0},
      "measure_after":{"observed":observed,"permutation":{"replicates":PERMUTATIONS,"seed":SEED,
        "null_delta_r2_quantiles":quantiles,"null_delta_r2_values":[float(v) for v in null],
        "permutation_p_value_one_sided":p_value,"null_ge_observed_count":int(np.count_nonzero(null>=observed["pooled"]["delta_r2"])),
        "county_blocks_per_region":perm_support}},
      "thresholds":{"observed_pooled_delta_r2_strictly_positive":True,"one_sided_permutation_p_strictly_below":.05},
      "acceptance":{"observed_delta_r2_positive":bool(observed["pooled"]["delta_r2"]>0.),
        "permutation_p_below_0_05":bool(p_value<.05),"screen_pass":passes},
      "provenance":{**provenance,"prior_region_sign_screen":{"path":str(PRIOR_REPORT),"sha256":prior_sha},
        "fixed_model_script":{"path":str(PRIOR_MODEL_SCRIPT),"sha256":EXPECTED_PRIOR_MODEL_SCRIPT_SHA},
        "feature_values_and_mask_sha256":hash_feature_matrix(geoids,regions,features,joint),
        "analysis_script_sha256":sha256_file(Path(__file__).resolve()),"permutation_seed":SEED,
        "permutation_replicates":PERMUTATIONS},
      "interpretation":"County-block permutation supports a positive fixed entropy signal; descriptive only, not estimator-integration evidence." if passes else
        "Observed fixed entropy ΔR² was not positive and significant under the preregistered county-block null; document no supported residual signal and close this branch."}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/source-road-entropy-region-sign-audit.json")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out)
    print(json.dumps({"measure_before":report["measure_before"],"observed":report["measure_after"]["observed"],
      "permutation":{k:v for k,v in report["measure_after"]["permutation"].items() if k!="null_delta_r2_values"},
      "acceptance":report["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
