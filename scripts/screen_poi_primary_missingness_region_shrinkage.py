#!/usr/bin/env python3
"""Five-fold region-varying primary-category missingness slopes with fixed ridge shrinkage."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4p_conditional_innovation import deterministic_folds  # noqa: E402
from bias60.inputs import official_scored_pairs  # noqa: E402
from screen_poi_primary_category_missingness import (  # noqa: E402
    REGIONS, delta_r2, paired_tract_bootstrap, sha256_file,
)

EXPECTED_N = 9379
EXPECTED_SUPPORT = 9327
N_FOLDS = 5
RIDGE_ALPHA = 100.0
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261031
MIN_POOLED_DELTA_R2 = 0.005
MIN_POSITIVE_REGIONS = 3
MAX_REGION_DECLINE = 0.005
SOURCE_REPORT = ROOT / "reports/poi-primary-category-missingness.json"
SOURCE_ARTIFACT_SHA = "74c046c8f93997ae5883d39064b05a99d01a95885772cd381a12baccd61589fa"
SOURCE_FOLD_HASH = "c8b4f9875dbd127a1627458cba0d824bd743d5e5047b38ef243eb6608c3f5ce5"
REGION_LABELS = REGIONS


def mask_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little").tobytes()).hexdigest()


def feature_sha256(geoids: list[str], regions: list[str], values: np.ndarray,
                   defined: np.ndarray) -> str:
    if len(geoids) != len(regions) or len(values) != len(geoids) or len(defined) != len(geoids):
        raise ValueError("feature hashing requires aligned official arrays")
    lines = [f"{g},{r},{int(ok)},{format(float(v),'.17g') if ok else ''}"
             for g,r,v,ok in zip(geoids,regions,values,defined)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def region_slope_design(share_z: np.ndarray, regions: np.ndarray
                        ) -> tuple[np.ndarray, np.ndarray]:
    """Common slope plus three contrasts encoding four sum-zero regional deviations."""
    x=np.asarray(share_z,dtype=np.float64); reg=np.asarray(regions,dtype=object)
    if x.ndim!=1 or reg.shape!=x.shape or not np.isfinite(x).all() or set(reg.tolist())!=set(REGIONS):
        raise ValueError("slope design requires finite aligned values and all official regions")
    reference=REGIONS[-1]
    contrasts=np.column_stack([x*((reg==region).astype(float)-(reg==reference).astype(float))
                               for region in REGIONS[:-1]])
    return np.column_stack((x,contrasts)),contrasts


def deviation_penalty(n_columns:int,contrast_start:int,alpha:float=RIDGE_ALPHA)->np.ndarray:
    if contrast_start<0 or n_columns-contrast_start!=3 or alpha!=RIDGE_ALPHA:
        raise ValueError("fixed penalty requires three contrasts and alpha exactly 100")
    penalty=np.zeros((n_columns,n_columns),dtype=np.float64)
    # Four deltas sum to zero; delta_4=-sum(delta_1..delta_3), so sum(delta_r^2)=d'(I+11')d.
    penalty[contrast_start:,contrast_start:]=alpha*(np.eye(3)+np.ones((3,3)))
    return penalty


def ridge_coefficients(design:np.ndarray,target:np.ndarray,*,contrast_start:int,
                       alpha:float=RIDGE_ALPHA)->np.ndarray:
    x=np.asarray(design,dtype=np.float64); y=np.asarray(target,dtype=np.float64)
    if x.ndim!=2 or y.shape!=(x.shape[0],) or not(np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError("ridge inputs must be aligned finite arrays")
    if alpha!=RIDGE_ALPHA:
        raise ValueError("alpha is frozen at 100; no tuning")
    p=deviation_penalty(x.shape[1],contrast_start,alpha)
    return np.linalg.solve(x.T@x+p,x.T@y)


def region_coefficients(common:float,contrasts:np.ndarray)->dict[str,dict[str,float]]:
    d=np.asarray(contrasts,dtype=np.float64)
    if d.shape!=(3,) or not np.isfinite(d).all() or not np.isfinite(common):
        raise ValueError("expected a common slope and three finite contrast coefficients")
    deviations=np.r_[d,-float(d.sum())]
    return {region:{"deviation":float(deviations[i]),"slope":float(common+deviations[i])}
            for i,region in enumerate(REGIONS)}


def _baseline_design(base_z:np.ndarray,regions:np.ndarray)->np.ndarray:
    z=np.asarray(base_z,dtype=np.float64); reg=np.asarray(regions,dtype=object)
    if z.ndim!=2 or z.shape[1]!=3 or reg.shape!=(len(z),) or set(reg.tolist())!=set(REGIONS):
        raise ValueError("baseline design needs three standardized features and all regions")
    codes=np.asarray([REGIONS.index(r) for r in reg],dtype=np.int8)
    dummies=np.column_stack([(codes==code).astype(float) for code in range(1,len(REGIONS))])
    return np.column_stack((np.ones(len(reg)),dummies,z))


def fit_fold(target:np.ndarray,base_features:np.ndarray,share:np.ndarray,regions:np.ndarray,
             train:np.ndarray,test:np.ndarray,*,alpha:float=RIDGE_ALPHA)->dict[str,Any]:
    """Fit frozen baseline and common-plus-sum-zero region slope deviations on train only."""
    y=np.asarray(target,dtype=np.float64); base=np.asarray(base_features,dtype=np.float64)
    x=np.asarray(share,dtype=np.float64); reg=np.asarray(regions,dtype=object)
    tr=np.asarray(train,dtype=bool); te=np.asarray(test,dtype=bool); n=len(y)
    if (base.shape!=(n,3) or x.shape!=(n,) or reg.shape!=(n,) or tr.shape!=(n,) or te.shape!=(n,)
            or np.any(tr&te) or not tr.any() or not te.any()):
        raise ValueError("fold arrays must align with nonempty disjoint train/test masks")
    if np.any((tr|te)&(~np.isfinite(y)|~np.isfinite(x)|~np.isfinite(base).all(axis=1))):
        raise ValueError("selected fold rows must be complete and finite")
    if alpha!=RIDGE_ALPHA:
        raise ValueError("ridge alpha is fixed at 100")
    mean=base[tr].mean(axis=0); sd=base[tr].std(axis=0,ddof=0)
    scale=np.where(sd>0.,sd,1.)
    base_z=(base-mean)/scale
    share_mean=float(x[tr].mean()); share_sd=float(x[tr].std(ddof=0)); share_scale=share_sd if share_sd>0. else 1.
    share_z=(x-share_mean)/share_scale
    train_base=_baseline_design(base_z[tr],reg[tr]); test_base=_baseline_design(base_z[te],reg[te])
    beta_base,_,rank_base,_=np.linalg.lstsq(train_base,y[tr],rcond=None)
    if rank_base!=train_base.shape[1]: raise ValueError("baseline train design is rank deficient")
    baseline_prediction=test_base@beta_base
    slope_train,_=region_slope_design(share_z[tr],reg[tr])
    slope_test,_=region_slope_design(share_z[te],reg[te])
    train_full=np.column_stack((train_base,slope_train))
    test_full=np.column_stack((test_base,slope_test))
    contrast_start=train_base.shape[1]+1 # common slope is unpenalized; three following columns are penalized
    beta_ridge=ridge_coefficients(train_full,y[tr],contrast_start=contrast_start,alpha=alpha)
    augmented_prediction=test_full@beta_ridge
    # Validate exact replication of the prior frozen S1 baseline design in direct tests.
    return {"baseline_prediction":baseline_prediction,"augmented_prediction":augmented_prediction,
      "baseline_coefficients":beta_base.tolist(),"ridge_coefficients":beta_ridge.tolist(),
      "common_share_slope":float(beta_ridge[train_base.shape[1]]),
      "region_slopes":region_coefficients(float(beta_ridge[train_base.shape[1]]),beta_ridge[contrast_start:]),
      "region_deviation_l2_norm":float(np.sqrt(beta_ridge[contrast_start:]@(np.eye(3)+np.ones((3,3)))@beta_ridge[contrast_start:])),
      "baseline_rank":int(rank_base),"ridge_design_columns":int(train_full.shape[1]),
      "train_scalers":{"baseline_feature_mean":mean.tolist(),"baseline_feature_sd":sd.tolist(),
        "baseline_feature_applied_scale":scale.tolist(),"missing_share_mean":share_mean,
        "missing_share_sd":share_sd,"missing_share_applied_scale":share_scale},
      "train_n":int(tr.sum()),"test_n":int(te.sum())}


def run(workspace:Path,report_path:Path)->dict[str,Any]:
    source_report=json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    source_artifact=Path(source_report["provenance"]["output_artifact"]["path"])
    if source_report["provenance"]["output_artifact"]["sha256"]!=SOURCE_ARTIFACT_SHA or sha256_file(source_artifact)!=SOURCE_ARTIFACT_SHA:
        raise ValueError("frozen primary-category screen artifact/report hash mismatch")
    if (not np.isclose(source_report["measure_after"]["pooled"]["delta_r2"],-1.6161032698766566e-05,rtol=0.,atol=1e-15)
            or source_report["measure_after"]["positive_regions"]!=1):
        raise ValueError("MEASURE-BEFORE global missing-share screen changed")
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):
        raise ValueError("official IDs must be the exact 9,379 four-region set")
    official_sha=hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    table=pq.read_table(source_artifact,columns=["GEOID","region","fold","unique_assigned_place_count",
      "primary_category_missing_share","primary_category_missing_share_defined","frozen_s1_crossfit_residual",
      "s1_residual_defined","Mx4_F","S1_lagged_Mx4_F","paired_screen_defined","baseline_oof_prediction"])
    if table.num_rows!=EXPECTED_N: raise ValueError("source screen artifact row count mismatch")
    positions={pair:i for i,pair in enumerate(official)}
    n=EXPECTED_N
    folds=np.full(n,-1,dtype=np.int8); total_count=np.zeros(n,dtype=np.int64)
    share=np.full(n,np.nan); share_defined=np.zeros(n,dtype=bool)
    target=np.full(n,np.nan); residual_defined=np.zeros(n,dtype=bool)
    mx4=np.full(n,np.nan); lag=np.full(n,np.nan); baseline=np.full(n,np.nan)
    support=np.zeros(n,dtype=bool); seen=set()
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in positions or pair in seen: raise ValueError(f"source artifact has duplicate/unexpected ID {pair}")
        seen.add(pair); i=positions[pair]
        folds[i]=int(row["fold"]); total_count[i]=int(row["unique_assigned_place_count"])
        share_defined[i]=bool(row["primary_category_missing_share_defined"])
        residual_defined[i]=bool(row["s1_residual_defined"]); support[i]=bool(row["paired_screen_defined"])
        for key,dest in (("primary_category_missing_share",share),("frozen_s1_crossfit_residual",target),
                         ("Mx4_F",mx4),("S1_lagged_Mx4_F",lag),("baseline_oof_prediction",baseline)):
            if row[key] is not None: dest[i]=float(row[key])
    if seen!=set(official): raise ValueError("source artifact does not cover exact official IDs")
    if not np.array_equal(np.isfinite(share),share_defined) or not np.array_equal(np.isfinite(target),residual_defined):
        raise ValueError("source artifact feature or residual null masks inconsistent")
    if int(support.sum())!=EXPECTED_SUPPORT or not np.array_equal(support,share_defined&residual_defined&np.isfinite(mx4)&np.isfinite(lag)&np.isfinite(baseline)):
        raise ValueError("frozen paired support changed from source screen")
    regions=np.asarray([r for _,r in official],dtype=object); geoids=[g for g,_ in official]
    for region in REGIONS:
        idx=np.flatnonzero(regions==region)
        expected=deterministic_folds([geoids[i] for i in idx],region,N_FOLDS)
        if not np.array_equal(folds[idx],expected): raise ValueError(f"source fold IDs changed for {region}")
    fold_hash=hashlib.sha256(folds.tobytes()).hexdigest()
    if fold_hash!=SOURCE_FOLD_HASH or fold_hash!=source_report["scope"]["fold_assignment_sha256"]:
        raise ValueError("fold assignment hash differs from frozen source screen")
    baseline_features=np.column_stack((np.log1p(total_count.astype(np.float64)),mx4,lag))
    baseline_oof=np.full(n,np.nan); augmented_oof=np.full(n,np.nan)
    fold_results=[]
    for fold in range(N_FOLDS):
        train=support&(folds!=fold); test=support&(folds==fold)
        if not train.any() or not test.any(): raise ValueError(f"fold {fold} has empty support")
        fit=fit_fold(target,baseline_features,share,regions,train,test,alpha=RIDGE_ALPHA)
        baseline_oof[test]=fit["baseline_prediction"]
        augmented_oof[test]=fit["augmented_prediction"]
        if not np.allclose(baseline_oof[test],baseline[test],rtol=0.,atol=2e-12):
            raise ValueError(f"refitted fold {fold} S1 baseline differs from pinned prior artifact")
        fold_result={"fold":fold,"heldout_n":int(test.sum()),
          **delta_r2(target[test],baseline_oof[test],augmented_oof[test]),
          "coefficients":fit["ridge_coefficients"],"baseline_coefficients":fit["baseline_coefficients"],
          "common_share_slope":fit["common_share_slope"],"region_slopes":fit["region_slopes"],
          "region_deviation_l2_norm":fit["region_deviation_l2_norm"],"train_scalers":fit["train_scalers"],
          "training_n":fit["train_n"],"design_columns":fit["ridge_design_columns"],"baseline_rank":fit["baseline_rank"]}
        fold_results.append(fold_result)
    if np.any(support&(~np.isfinite(baseline_oof)|~np.isfinite(augmented_oof))) or np.any(~support&(np.isfinite(baseline_oof)|np.isfinite(augmented_oof))):
        raise AssertionError("OOF prediction masks differ from pinned support")
    pooled=delta_r2(target[support],baseline_oof[support],augmented_oof[support])
    bootstrap=paired_tract_bootstrap(target[support],baseline_oof[support],augmented_oof[support],
      replicates=BOOTSTRAP_REPLICATES,seed=BOOTSTRAP_SEED)
    regional={}
    for region in REGIONS:
        use=support&(regions==region)
        regional[region]={"n":int(use.sum()),**delta_r2(target[use],baseline_oof[use],augmented_oof[use])}
    positive_regions=sum(v["delta_r2"]>0. for v in regional.values())
    worst_region=min(v["delta_r2"] for v in regional.values())
    gate=bool(pooled["delta_r2"]>=MIN_POOLED_DELTA_R2 and positive_regions>=MIN_POSITIVE_REGIONS
      and worst_region>=-MAX_REGION_DECLINE)
    feature_hash=feature_sha256(geoids,[r for _,r in official],share,share_defined)
    config={"folds":N_FOLDS,"fold_assignment_sha256":fold_hash,"alpha":RIDGE_ALPHA,
      "ridge_penalty":"alpha * sum of squared four region deviations constrained to sum zero; common slope and baseline coefficients unpenalized",
      "baseline":"intercept + three region intercept contrasts + train-standardized log1p POI count, Mx4_F, S1_lagged_Mx4_F",
      "augmented":"baseline + globally train-standardized missing share common slope + four sum-zero region slope deviations",
      "missing_share_feature":"precomputed primary_category_missing_share, defined from >=10 unique assigned provided POIs",
      "bootstrap_replicates":BOOTSTRAP_REPLICATES,"bootstrap_seed":BOOTSTRAP_SEED,
      "thresholds":{"pooled_delta_r2":MIN_POOLED_DELTA_R2,"positive_regions":MIN_POSITIVE_REGIONS,
        "minimum_region_delta_r2":-MAX_REGION_DECLINE}}
    config_hash=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    report={"schema_version":1,"screen":"Five-fold region-specific shrinkage of POI primary-category missing-share slopes; unregistered",
      "measure_before":{"pooled_delta_r2":source_report["measure_after"]["pooled"]["delta_r2"],
        "pooled_ci_95":source_report["measure_after"]["paired_pooled_delta_r2_ci_95"],
        "positive_regions":source_report["measure_after"]["positive_regions"],
        "source_report_sha256":sha256_file(SOURCE_REPORT)},
      "method":{"outcome":"Frozen S1 crossfit residual stored in the source missing-share artifact; no outcome labels.",
        "support":"Exact paired support from prior screen: frozen residual, missing share, Mx4_F and S1 lag all defined (9,327 tracts).",
        "folds":"Exact source screen deterministic five-fold GEOID assignment within each official region; verified from source artifact and digest; no changes.",
        "baseline":"Exact prior nested baseline: intercept and region contrasts plus train-standardized log1p assigned POI count, Mx4_F and S1_lagged_Mx4_F. Refit OOF predictions are checked against pinned prior baseline predictions.",
        "augmentation":"Baseline plus a common train-standardized missing-share slope and four train-estimated region deviations constrained to sum zero. Deviations receive fixed ridge penalty alpha=100; common slope and baseline terms are unpenalized.",
        "penalty":"alpha * sum_r(deviation_r^2); represented by three contrasts where fourth deviation is negative sum of first three.",
        "scaling":"All continuous baseline features and missing-share scaling use each outer fold's training rows only.",
        "metric":"Pooled five-fold held-out incremental delta-R2=(baseline SSE-augmented SSE)/shared held-out residual SST; same support and baseline as source screen.",
        "uncertainty":"Paired with-replacement tract bootstrap on pooled exact OOF support using the original source screen's fixed replicate count and seed.",
        "no_tuning_or_posthoc_selection":True,"unregistered":True,
        "interpretation_limit":"Provided-feature residual association screen only; not target accuracy, causality, or authorization to integrate."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":official_sha,"paired_support_ids":int(support.sum()),
        "paired_support_mask_sha256":mask_sha256(support),"missing_share_defined_ids":int(share_defined.sum()),
        "feature_values_and_mask_sha256":feature_hash,"fold_assignment_sha256":fold_hash,
        "support_by_region":{r:int(np.sum(support&(regions==r))) for r in REGIONS},
        "labels_read":False,"target_claims":False,"removed_or_equivalent_references_read":False,
        "external_inputs":[],"leaderboard_feedback_used":False,"uploads":0},
      "measure_after":{"pooled":pooled,"paired_pooled_delta_r2_ci_95":bootstrap["ci_95_percentile"],
        "bootstrap":bootstrap,"regional":regional,"positive_regions":positive_regions,
        "worst_region_delta_r2":worst_region,"folds":fold_results},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_POOLED_DELTA_R2,
        "minimum_positive_regions":MIN_POSITIVE_REGIONS,"minimum_region_delta_r2_inclusive":-MAX_REGION_DECLINE,
        "ridge_alpha_fixed":RIDGE_ALPHA},
      "acceptance":{"pooled_delta_r2_at_least_0_005":bool(pooled["delta_r2"]>=MIN_POOLED_DELTA_R2),
        "positive_delta_in_at_least_3_of_4_regions":bool(positive_regions>=MIN_POSITIVE_REGIONS),
        "no_region_decline_exceeds_0_005":bool(worst_region>=-MAX_REGION_DECLINE),"screen_pass":gate},
      "provenance":{"source_screen_report":{"path":str(SOURCE_REPORT),"sha256":sha256_file(SOURCE_REPORT)},
        "source_paired_oof_artifact":{"path":str(source_artifact),"sha256":SOURCE_ARTIFACT_SHA,"rows":EXPECTED_N},
        "official_geoid_region_sha256":official_sha,"primary_missing_share_feature_hash":feature_hash,
        "config":config,"config_sha256":config_hash,"script_sha256":sha256_file(Path(__file__).resolve())},
      "interpretation":"The fixed-alpha region shrinkage screen passed all predeclared gates; remain descriptive-only." if gate else
        "The fixed-alpha region shrinkage screen failed one or more predeclared gates; retain the previous non-integrated diagnostic only."}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/poi-primary-missingness-region-shrinkage.json")
    args=parser.parse_args(); workspace=Path(args.workspace)
    if not workspace.is_dir(): parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],
      "ci":report["measure_after"]["paired_pooled_delta_r2_ci_95"],"regional":report["measure_after"]["regional"],
      "positive_regions":report["measure_after"]["positive_regions"],"acceptance":report["acceptance"],
      "config_sha256":report["provenance"]["config_sha256"]},indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
