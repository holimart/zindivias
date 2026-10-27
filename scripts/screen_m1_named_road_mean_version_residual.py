#!/usr/bin/env python3
"""County-held-out one-term screen of supplied M1 named-road mean version vs frozen S1 residual."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa: E402
from audit_mx4p_conditional_innovation import read_emission  # noqa: E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379
EXPECTED_FEATURE_N=9247
M1_SHA="ddc48fc70558cb920659484b870819a9c4be43f3488e7fe3f59eed148b6e9f37"
M1_REPORT=ROOT/"reports/ideas-harness.json"
M1_FAMILY_REPORT=ROOT/"reports/bias60-family-m-summary.json"
M1_SOURCE=ROOT/"src/bias60/ideas/m.py"
M1_CHANNEL="covariate:named_mean_version"
M1_COMPONENT="R"
RESIDUAL_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
CONFIG_PATH=ROOT/"config/bias60-harness-preregistered.json"
BOOTSTRAP_REPLICATES=2000
BOOTSTRAP_SEED=20261101
MIN_DELTA_R2=0.002
MIN_CI_LOWER=0.0
MIN_POSITIVE_REGIONS=3


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def county_fips(geoid:str)->str:
    value=str(geoid)
    if len(value)!=11 or not value.isdigit():raise ValueError(f"GEOID must be 11 digits: {value!r}")
    return value[:5]


def mask_sha256(mask:np.ndarray)->str:
    return hashlib.sha256(np.packbits(np.asarray(mask,dtype=np.uint8),bitorder="little").tobytes()).hexdigest()


def data_sha256(geoids:list[str],regions:list[str],values:np.ndarray,defined:np.ndarray)->str:
    if not(len(geoids)==len(regions)==len(values)==len(defined)):raise ValueError("hash arrays are misaligned")
    payload="\n".join(f"{g},{r},{int(ok)},{format(float(x),'.17g') if ok else ''}" for g,r,x,ok in zip(geoids,regions,values,defined))
    return hashlib.sha256(payload.encode()).hexdigest()


def fit_one_term(train_y:np.ndarray,train_x:np.ndarray,test_x:np.ndarray)->tuple[np.ndarray,dict[str,Any]]:
    """OLS intercept plus one term, standardized with training rows only."""
    y=np.asarray(train_y,dtype=np.float64);x=np.asarray(train_x,dtype=np.float64);held=np.asarray(test_x,dtype=np.float64)
    if x.ndim!=1 or held.ndim!=1 or y.shape!=x.shape or len(y)<3 or not len(held):
        raise ValueError("fold inputs require aligned vectors, >=3 training rows, and held-out rows")
    if not(np.isfinite(y).all() and np.isfinite(x).all() and np.isfinite(held).all()):raise ValueError("fold inputs must be finite")
    mean=float(x.mean());sd=float(x.std(ddof=0));scale=sd if sd>0. else 1.
    z=(x-mean)/scale;zheld=(held-mean)/scale
    design=np.column_stack((np.ones(len(y)),z));coef,_,rank,_=np.linalg.lstsq(design,y,rcond=None)
    if rank!=2:raise ValueError("one-term training design is rank deficient")
    pred=np.column_stack((np.ones(len(held)),zheld))@coef
    return pred,{"training_n":int(len(y)),"heldout_n":int(len(held)),"training_feature_mean":mean,
      "training_feature_sd":sd,"training_applied_scale":scale,"intercept":float(coef[0]),
      "standardized_slope":float(coef[1]),"design_rank":int(rank)}


def score_zero_baseline(y:np.ndarray,pred:np.ndarray)->dict[str,float|int]:
    target=np.asarray(y,dtype=np.float64);estimate=np.asarray(pred,dtype=np.float64)
    if target.ndim!=1 or estimate.shape!=target.shape or not len(target) or not(np.isfinite(target).all() and np.isfinite(estimate).all()):
        raise ValueError("score inputs must be aligned finite nonempty vectors")
    baseline=float(np.dot(target,target));model=float(np.sum((target-estimate)**2))
    if baseline<=0.:raise ValueError("uncorrected residual SSE must be positive")
    return {"n":int(len(target)),"frozen_s1_baseline_sse":baseline,"corrected_sse":model,"delta_r2":(baseline-model)/baseline}


def county_bootstrap(records:list[dict[str,Any]],*,replicates:int=BOOTSTRAP_REPLICATES,seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    if replicates<100:raise ValueError("at least 100 bootstrap replicates required")
    groups={r:[] for r in REGIONS}
    for row in records:groups[row["region"]].append(row)
    if any(not rows for rows in groups.values()):raise ValueError("each region needs held-out counties")
    rng=np.random.default_rng(seed);pooled=np.empty(replicates);regional={r:np.empty(replicates) for r in REGIONS}
    for b in range(replicates):
        total_base=total_model=0.
        for region in REGIONS:
            rows=groups[region];draw=rng.integers(0,len(rows),size=len(rows))
            base=sum(float(rows[i]["baseline_sse"]) for i in draw);model=sum(float(rows[i]["model_sse"]) for i in draw)
            regional[region][b]=(base-model)/base if base>0. else np.nan
            total_base+=base;total_model+=model
        pooled[b]=(total_base-total_model)/total_base if total_base>0. else np.nan
    def ci(values:np.ndarray)->list[float]:
        values=values[np.isfinite(values)]
        if len(values)<int(.99*replicates):raise ValueError("too many degenerate bootstrap draws")
        return [float(np.quantile(values,.025)),float(np.quantile(values,.975))]
    return {"replicates":replicates,"seed":seed,"resampling":"paired held-county baseline/model SSEs; counties sampled with replacement within each official region",
      "pooled_delta_r2_ci_95":ci(pooled),"regional_delta_r2_ci_95":{r:ci(v) for r,v in regional.items()},
      "degenerate_replicates":int(np.sum(~np.isfinite(pooled)))}


def load_residuals(workspace:Path,official:list[tuple[str,str]])->tuple[np.ndarray,dict[str,str]]:
    path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(path)!=RESIDUAL_SHA:raise ValueError("frozen S1 residual artifact SHA mismatch")
    report=json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    if report["provenance"]["output_artifact"]["sha256"]!=RESIDUAL_SHA:raise ValueError("frozen residual report hash mismatch")
    table=pq.read_table(path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if table.num_rows!=len(official):raise ValueError("S1 residual row count mismatch")
    pos={pair:i for i,pair in enumerate(official)};seen=set();y=np.full(len(official),np.nan)
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in pos or pair in seen:raise ValueError(f"duplicate or unexpected S1 residual row {pair}")
        seen.add(pair);i=pos[pair]
        if row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:raise ValueError("expected all-tract frozen S1 residual support")
        y[i]=float(row["crossfit_residual"])
    if seen!=set(official) or not np.isfinite(y).all():raise ValueError("frozen residual IDs/mask do not match official scope")
    return y,{"path":str(path),"sha256":RESIDUAL_SHA,"report_sha256":sha256_file(RESIDUAL_REPORT)}


def load_urbanity(workspace:Path,official:list[tuple[str,str]],config:dict[str,Any])->tuple[np.ndarray,dict[str,Any]]:
    urban=np.full(len(official),np.nan);paths={}
    for region in REGIONS:
        idx=[i for i,pair in enumerate(official) if pair[1]==region];ids=[official[i][0] for i in idx]
        inp=ProvidedInputs(config,workspace,region,ids);values=inp.strata(["pct_urban"])["pct_urban"]
        urban[idx]=np.asarray(values,dtype=np.float64)
        path=workspace/config["inputs"]["strata_cache_template"].format(region=region)
        paths[str(path)]={"sha256":sha256_file(path),"columns_read":["GEOID","pct_urban"]}
    finite=np.isfinite(urban)
    return urban,{"strata_cache_files":paths,"pct_urban_values_and_mask_sha256":data_sha256(
      [g for g,_ in official],[r for _,r in official],urban,finite),"pct_urban_defined":int(finite.sum())}


def summarize_group(y:np.ndarray,pred:np.ndarray,mask:np.ndarray)->dict[str,Any]:
    if not np.any(mask):return {"support_n":0,"delta_r2":None,"mean_residual":None}
    score=score_zero_baseline(y[mask],pred[mask])
    return {"support_n":int(mask.sum()),"delta_r2":score["delta_r2"],"mean_residual":float(y[mask].mean())}


def run(workspace:Path,report_path:Path,manifest_path:Path)->dict[str,Any]:
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):
        raise ValueError("official tract scope must be exactly 9,379 unique GEOID/region pairs")
    geoids=[g for g,_ in official];regions=np.asarray([r for _,r in official],dtype=object)
    if len(set(geoids))!=EXPECTED_N:raise ValueError("official GEOIDs must be unique")
    m1_path=workspace/"ideas/M1.parquet"
    m1,feature_defined,m1_sha=read_emission(m1_path,M1_SHA,"M1",M1_COMPONENT,M1_CHANNEL,official)
    if int(feature_defined.sum())!=EXPECTED_FEATURE_N:raise ValueError("M1 named mean version coverage differs from frozen 9,247")
    if np.any(feature_defined&~np.isfinite(m1)):raise ValueError("defined M1 feature values must be finite")
    family=json.loads(M1_FAMILY_REPORT.read_text(encoding="utf-8"))
    m1_summary=family["metrics"]["M1|R|covariate:named_mean_version"]
    if m1_summary["artifact_sha256"]!=M1_SHA or m1_summary["defined_rows"]!=EXPECTED_FEATURE_N:
        raise ValueError("frozen M1 family summary does not match feature artifact/support")
    harness=json.loads(M1_REPORT.read_text(encoding="utf-8"))
    harness_m1=harness["per_idea"]["M1"]
    if harness_m1["sha256"]!=M1_SHA:raise ValueError("M1 harness report artifact digest mismatch")
    expected_version_parser="OpenStreetMap source record_id matching ^w<digits>@<version>$; version is parsed only from documented way IDs"
    for region,diagnostics in harness_m1["diagnostics"].items():
        if diagnostics.get("version_parser")!=expected_version_parser or not diagnostics.get("no_target_or_reference_data_read"):
            raise ValueError(f"M1 {region} provenance does not confirm supplied OSM record_id-only parsing")
    accessed=harness_m1["accessed_inputs"]
    if set(accessed["layers"])!={"roads","census-acs-housing"} or any(
            not column.endswith(":class") and not column.endswith(":geometry") and not column.endswith(":sources")
            and not column.endswith(":GEOID,geometry") for column in accessed["parquet_columns"]):
        raise ValueError("M1 harness inputs differ from provided roads and ACS geometry")
    y,residual_meta=load_residuals(workspace,official)
    support=feature_defined&np.isfinite(y)
    if int(support.sum())!=EXPECTED_FEATURE_N:raise ValueError("paired residual-feature support mismatch")
    config=load_config(CONFIG_PATH);pct_urban,urban_meta=load_urbanity(workspace,official,config)
    counties=np.asarray([county_fips(g) for g in geoids],dtype=object)
    prediction=np.full(EXPECTED_N,np.nan);records=[];manifest_rows=[];folds_by_region={}
    for region in REGIONS:
        region_mask=support&(regions==region);county_list=sorted(set(counties[region_mask].tolist()))
        if len(county_list)<2:raise ValueError(f"{region} has fewer than two supported counties")
        folds_by_region[region]=len(county_list)
        for held in county_list:
            test=region_mask&(counties==held);train=region_mask&(counties!=held)
            if test.sum()<1 or train.sum()<3:raise ValueError(f"invalid fixed county fold {region}/{held}")
            pred,fit=fit_one_term(y[train],m1[train],m1[test]);prediction[test]=pred
            base=float(np.dot(y[test],y[test]));model=float(np.sum((y[test]-pred)**2))
            train_counties=[c for c in county_list if c!=held]
            train_hash=hashlib.sha256("\n".join(train_counties).encode()).hexdigest()
            records.append({"region":region,"heldout_county_fips":held,"heldout_n":int(test.sum()),"training_counties":len(train_counties),
              "training_county_fips_sha256":train_hash,"training_n":int(train.sum()),"baseline_sse":base,"model_sse":model,
              "delta_r2":(base-model)/base if base>0. else None,"fit":fit})
            for i in np.flatnonzero(test):manifest_rows.append((geoids[i],region,held,train_hash,len(train_counties),int(train.sum())))
    if np.any(support&~np.isfinite(prediction)) or np.any(~support&np.isfinite(prediction)):raise AssertionError("OOF prediction mask mismatch")
    pooled=score_zero_baseline(y[support],prediction[support]);bootstrap=county_bootstrap(records)
    regional={}
    for region in REGIONS:
        use=support&(regions==region)
        regional[region]={**score_zero_baseline(y[use],prediction[use]),"county_count":folds_by_region[region],
          "feature_support":int(use.sum()),"official_ids":int(np.sum(regions==region))}
    rural=np.isfinite(pct_urban)&(pct_urban<.5);urban=np.isfinite(pct_urban)&(pct_urban>=.5)
    urbanity={"cutoff":.5,"rural_support_n":int(np.sum(support&rural)),"urban_support_n":int(np.sum(support&urban)),
      "pct_urban_missing_support_n":int(np.sum(support&~np.isfinite(pct_urban))),
      "rural":summarize_group(y,prediction,support&rural),"urban":summarize_group(y,prediction,support&urban)}
    positive_regions=sum(regional[r]["delta_r2"]>0. for r in REGIONS)
    ci=bootstrap["pooled_delta_r2_ci_95"]
    passed=bool(pooled["delta_r2"]>=MIN_DELTA_R2 and ci[0]>MIN_CI_LOWER and positive_regions>=MIN_POSITIVE_REGIONS)
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    with manifest_path.open("w",encoding="utf-8",newline="") as f:
        writer=csv.writer(f,lineterminator="\n")
        writer.writerow(["GEOID","region","heldout_county_fips","training_county_fips_sha256","training_county_count","training_support_n"])
        writer.writerows(sorted(manifest_rows,key=lambda row:(row[1],row[0])))
    manifest_sha=sha256_file(manifest_path)
    feature_hash=data_sha256(geoids,[r for _,r in official],m1,feature_defined)
    report={"schema_version":1,"screen":"M1 named-road mean version county-held-out residual correction; unregistered",
      "measure_before":{"M1_named_mean_version_coverage":EXPECTED_FEATURE_N,"official_ids":EXPECTED_N,
        "M1_distinct_defined_values":m1_summary["distribution_defined_only"]["distinct_values"],"county_heldout_residual_delta_r2_measured":False,
        "M1_artifact_sha256":M1_SHA},
      "method":{"feature":"Frozen M1|R|covariate:named_mean_version aggregate. M1 parses only the supplied sources.record_id OSM way version token with ^w[0-9]+@([0-9]+)$ for named-class roads (motorway/trunk/primary/secondary), deduplicating IDs per geometry and weighting by tract-clipped length.",
        "source_guard":"No OSM history retrieval or other version/vintage source. Only the pinned M1 artifact is read for the feature; M1 provenance is checked against its source code and harness reports.",
        "outcome":"Pinned frozen cross-fitted S1 Queen/Rook disagreement residual; neither recomputed nor refit.",
        "validation":"For each official region, hold out each supported five-digit county FIPS as a fixed leave-one-county-out fold; fit on all other supported counties in that region. Every supported tract is predicted exactly once.",
        "correction":"OLS intercept plus one linear term; standardize the feature with training-fold mean and population SD only (zero SD scale becomes 1). No threshold search or additional predictors.",
        "baseline":"Unmodified frozen S1 residual (zero correction), scored on identical held-out support.",
        "metric":"Incremental ΔR²=(zero-correction baseline SSE - county-held-out corrected SSE)/baseline SSE, pooled and by region; paired county bootstrap within each region.",
        "urban_rural":"Report support and analogous paired correction deltas using provided pct_urban <0.5 rural, >=0.5 urban; strata are descriptive only.",
        "unregistered":True,"interpretation_limit":"Provided-feature residual association only, not target accuracy, causal evidence, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"M1_defined_ids":int(feature_defined.sum()),"paired_residual_support":int(support.sum()),
        "undefined_feature_ids":int((~feature_defined).sum()),"feature_mask_sha256":mask_sha256(feature_defined),
        "feature_values_and_mask_sha256":feature_hash,"frozen_residual_defined_ids":EXPECTED_N,
        "official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,"external_inputs_used":False,
        "removed_or_equivalent_references_read":False,"leaderboard_feedback_used":False,"estimator_integration":False,"uploads":0},
      "measure_after":{"pooled":pooled,"county_block_bootstrap":bootstrap,"regional":regional,"urban_rural_support_and_deltas":urbanity,
        "county_fold_count":len(records),"county_folds":records,"fold_manifest":{"path":str(manifest_path),"sha256":manifest_sha,"rows":len(manifest_rows)}},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_DELTA_R2,"bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,
        "minimum_positive_regions":MIN_POSITIVE_REGIONS},
      "acceptance":{"pooled_delta_r2_at_least_0_002":bool(pooled["delta_r2"]>=MIN_DELTA_R2),
        "county_block_ci_lower_above_zero":bool(ci[0]>MIN_CI_LOWER),"positive_region_count":positive_regions,
        "positive_in_at_least_3_of_4_regions":bool(positive_regions>=MIN_POSITIVE_REGIONS),"screen_pass":passed},
      "provenance":{"M1_artifact":{"path":str(m1_path),"sha256":m1_sha},
        "M1_harness_report":{"path":str(M1_REPORT),"sha256":sha256_file(M1_REPORT)},
        "M1_family_summary":{"path":str(M1_FAMILY_REPORT),"sha256":sha256_file(M1_FAMILY_REPORT)},
        "M1_idea_source":{"path":str(M1_SOURCE),"sha256":sha256_file(M1_SOURCE)},
        "frozen_S1_residual":residual_meta,"provided_urbanity":urban_meta,
        "analysis_script_sha256":sha256_file(Path(__file__).resolve()),
        "bootstrap_seed":BOOTSTRAP_SEED,"bootstrap_replicates":BOOTSTRAP_REPLICATES},
      "interpretation":"The named-road mean-version feature passed the preregistered held-out gates; remain descriptive-only pending separate review." if passed else
        "One or more preregistered held-out gates failed; retain descriptive-only and do not integrate."}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/m1-named-road-mean-version-residual.json")
    parser.add_argument("--manifest",default="reports/m1-named-road-mean-version-county-folds.csv")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out,ROOT/args.manifest)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],
      "pooled_ci":report["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"],
      "regional":report["measure_after"]["regional"],"urban_rural":report["measure_after"]["urban_rural_support_and_deltas"],
      "acceptance":report["acceptance"],"manifest_sha256":report["measure_after"]["fold_manifest"]["sha256"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
