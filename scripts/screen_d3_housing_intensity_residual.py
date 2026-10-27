#!/usr/bin/env python3
"""County-held-out one-term screen of provided ACS housing/building intensity vs frozen S1 residual."""
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa: E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379
EXPECTED_D3_SUPPORT=9299
D3_ARTIFACT_SHA="5a254127dd2adcd0f36e080a21b54fe9d9def46aeb93c068b8bf80c03359b36c"
D3_HARNESS_REPORT=ROOT/"reports/ideas-harness.json"
D3_CONFIG=ROOT/"config/bias60-harness-preregistered.json"
RESIDUAL_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
BOOTSTRAP_REPLICATES=2000
BOOTSTRAP_SEED=20261031
MIN_DELTA_R2=0.002
MIN_CI_LOWER=0.0
MAX_STRATUM_DECLINE=0.005
MIN_STRATUM_N=100
MIN_SVI_STRATUM_N=100


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def mask_sha256(mask:np.ndarray)->str:
    return hashlib.sha256(np.packbits(np.asarray(mask,dtype=np.uint8),bitorder="little").tobytes()).hexdigest()


def vector_sha256(geoids:list[str],regions:list[str],values:np.ndarray,defined:np.ndarray)->str:
    if not(len(geoids)==len(regions)==len(values)==len(defined)):raise ValueError("feature hash arrays misaligned")
    data="\n".join(f"{g},{r},{int(ok)},{format(float(v),'.17g') if ok else ''}" for g,r,v,ok in zip(geoids,regions,values,defined))
    return hashlib.sha256(data.encode()).hexdigest()


def log_housing_building_intensity(housing:np.ndarray,buildings:np.ndarray,ruca:np.ndarray)->tuple[np.ndarray,np.ndarray]:
    hu=np.asarray(housing,dtype=np.float64);b=np.asarray(buildings,dtype=np.float64);ru=np.asarray(ruca,dtype=np.float64)
    if hu.ndim!=1 or b.shape!=hu.shape or ru.shape!=hu.shape:raise ValueError("D3 input vectors must align")
    valid_ruca=np.isfinite(ru)&(ru>=1.)&(ru<=10.)
    defined=np.isfinite(hu)&(hu>0.)&np.isfinite(b)&(b>0.)&valid_ruca
    feature=np.full(hu.shape,np.nan)
    feature[defined]=np.log(hu[defined]/b[defined])
    if np.any(defined&~np.isfinite(feature)):raise ValueError("defined log housing/building values must be finite")
    return feature,defined


def fit_one_term(train_y:np.ndarray,train_x:np.ndarray,test_x:np.ndarray)->tuple[np.ndarray,dict[str,Any]]:
    """Fit intercept + one standardized covariate; standardization uses training rows only."""
    y=np.asarray(train_y,dtype=np.float64);x=np.asarray(train_x,dtype=np.float64);held=np.asarray(test_x,dtype=np.float64)
    if x.ndim!=1 or held.ndim!=1 or y.shape!=x.shape or len(y)<3 or not len(held):
        raise ValueError("one-term fold inputs require aligned vectors, >=3 train rows, and heldout rows")
    if not(np.isfinite(y).all() and np.isfinite(x).all() and np.isfinite(held).all()):
        raise ValueError("one-term fold inputs must be finite")
    mean=float(x.mean());sd=float(x.std(ddof=0));scale=sd if sd>0. else 1.
    z=(x-mean)/scale;zheld=(held-mean)/scale
    design=np.column_stack((np.ones(len(y)),z))
    coef,_,rank,_=np.linalg.lstsq(design,y,rcond=None)
    if rank!=2:raise ValueError("training one-term regression is rank deficient")
    pred=np.column_stack((np.ones(len(held)),zheld))@coef
    return pred,{"intercept":float(coef[0]),"standardized_coefficient":float(coef[1]),
      "raw_log_ratio_coefficient":float(coef[1]/scale),"training_feature_mean":mean,
      "training_feature_sd":sd,"training_applied_scale":scale,"training_n":int(len(y)),
      "heldout_n":int(len(held)),"design_rank":int(rank)}


def score_vs_frozen_s1(y:np.ndarray,prediction:np.ndarray)->dict[str,float|int]:
    target=np.asarray(y,dtype=np.float64);pred=np.asarray(prediction,dtype=np.float64)
    if target.ndim!=1 or pred.shape!=target.shape or not target.size or not(np.isfinite(target).all() and np.isfinite(pred).all()):
        raise ValueError("metric inputs must be aligned finite nonempty vectors")
    base=float(np.dot(target,target));model=float(np.sum((target-pred)**2))
    if base<=0.:raise ValueError("frozen residual baseline SSE must be positive")
    return {"n":int(len(target)),"frozen_s1_baseline_sse":base,"corrected_sse":model,
      "delta_r2":(base-model)/base}


def county_block_bootstrap(records:list[dict[str,Any]],*,replicates:int=BOOTSTRAP_REPLICATES,
                           seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    if replicates<100:raise ValueError("at least 100 bootstrap replicates required")
    by_region={r:[] for r in REGIONS}
    for row in records:by_region[row["region"]].append(row)
    if any(not rows for rows in by_region.values()):raise ValueError("each region needs supported counties")
    rng=np.random.default_rng(seed);pooled=np.empty(replicates);regional={r:np.empty(replicates) for r in REGIONS}
    for b in range(replicates):
        base_all=model_all=0.
        for region in REGIONS:
            rows=by_region[region];draw=rng.integers(0,len(rows),size=len(rows))
            base=sum(float(rows[i]["baseline_sse"]) for i in draw)
            model=sum(float(rows[i]["model_sse"]) for i in draw)
            regional[region][b]=(base-model)/base if base>0. else np.nan
            base_all+=base;model_all+=model
        pooled[b]=(base_all-model_all)/base_all if base_all>0. else np.nan
    def ci(values:np.ndarray)->list[float]:
        good=values[np.isfinite(values)]
        if len(good)<int(.99*replicates):raise ValueError("too many degenerate bootstrap samples")
        return [float(np.quantile(good,.025)),float(np.quantile(good,.975))]
    return {"replicates":replicates,"seed":seed,
      "resampling":"paired leave-one-county-out SSEs; resample counties with replacement within each official region",
      "pooled_delta_r2_ci_95":ci(pooled),"regional_delta_r2_ci_95":{r:ci(v) for r,v in regional.items()},
      "degenerate_replicates":int(np.sum(~np.isfinite(pooled)))}


def _residuals(workspace:Path,official:list[tuple[str,str]])->tuple[np.ndarray,dict[str,Any]]:
    path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(path)!=RESIDUAL_SHA:raise ValueError("frozen S1 residual artifact hash mismatch")
    table=pq.read_table(path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if table.num_rows!=len(official):raise ValueError("frozen residual rows differ from official ID scope")
    pos={pair:i for i,pair in enumerate(official)};y=np.full(len(official),np.nan);seen=set()
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in pos or pair in seen:raise ValueError(f"duplicate/unexpected residual pair {pair}")
        seen.add(pair);i=pos[pair]
        if row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:
            raise ValueError("expected frozen S1 residual definedness on all official IDs")
        y[i]=float(row["crossfit_residual"])
    if seen!=set(official) or not np.isfinite(y).all():raise ValueError("residual alignment/support mismatch")
    report=json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    if report["provenance"]["output_artifact"]["sha256"]!=RESIDUAL_SHA:raise ValueError("residual report hash reference mismatch")
    return y,{"path":str(path),"sha256":RESIDUAL_SHA,"report_path":str(RESIDUAL_REPORT),"report_sha256":sha256_file(RESIDUAL_REPORT)}


def _strata_and_feature(workspace:Path,official:list[tuple[str,str]],config:dict[str,Any]
                        )->tuple[np.ndarray,np.ndarray,np.ndarray,np.ndarray,dict[str,Any]]:
    n=len(official);position={pair:i for i,pair in enumerate(official)}
    housing=np.full(n,np.nan);buildings=np.full(n,np.nan);ruca=np.full(n,np.nan)
    pct_urban=np.full(n,np.nan);svi=np.full(n,np.nan);access={};files={}
    for region in REGIONS:
        idx=[i for i,pair in enumerate(official) if pair[1]==region]
        ids=[official[i][0] for i in idx]
        inp=ProvidedInputs(config,workspace,region,ids)
        hu=inp.acs_housing_units()
        b=inp.features([config["seeds"]["D3"]["building_count_column"]])[config["seeds"]["D3"]["building_count_column"]]
        st=inp.strata([config["seeds"]["D3"]["ruca_column"],"pct_urban","svi_minority"])
        housing[idx]=np.asarray(hu,dtype=np.float64);buildings[idx]=np.asarray(b,dtype=np.float64)
        ruca[idx]=np.asarray(st[config["seeds"]["D3"]["ruca_column"]],dtype=np.float64)
        pct_urban[idx]=np.asarray(st["pct_urban"],dtype=np.float64);svi[idx]=np.asarray(st["svi_minority"],dtype=np.float64)
        access[region]={"accessed":inp.accessed,"provided_acs_object":inp.polygon_layer_path(config["inputs"]["polygon_layer"])}
        feature_path=workspace/config["inputs"]["feature_cache_template"].format(region=region)
        strata_path=workspace/config["inputs"]["strata_cache_template"].format(region=region)
        files[str(feature_path)]={"sha256":sha256_file(feature_path),"columns_read":["GEOID","overture_buildings"]}
        files[str(strata_path)]={"sha256":sha256_file(strata_path),
          "columns_read":["GEOID","ruca_primary","pct_urban","svi_minority"]}
    feature,defined=log_housing_building_intensity(housing,buildings,ruca)
    ruca_valid=np.isfinite(ruca)&(ruca>=1.)&(ruca<=10.)
    if int(defined.sum())!=EXPECTED_D3_SUPPORT:raise ValueError(f"raw D3 support changed: {int(defined.sum())}")
    # D3 artifact is read only to verify its frozen support and provenance; its full-data RUCA z-scores are not used.
    d3_path=workspace/"ideas/D3.parquet"
    if sha256_file(d3_path)!=D3_ARTIFACT_SHA:raise ValueError("frozen D3 feature artifact hash mismatch")
    t=pq.read_table(d3_path,columns=["GEOID","region","idea","component","channel","defined"])
    if t.num_rows!=n:raise ValueError("D3 artifact row count mismatch")
    artifact_mask=np.zeros(n,dtype=bool);seen=set()
    for row in t.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in position or pair in seen:raise ValueError(f"D3 artifact ID mismatch {pair}")
        seen.add(pair);artifact_mask[position[pair]]=bool(row["defined"])
    if seen!=set(official) or not np.array_equal(artifact_mask,defined):raise ValueError("raw D3 support differs from frozen D3 emitted mask")
    return feature,defined,pct_urban,svi,{"housing_units_defined":int(np.isfinite(housing).sum()),
      "building_count_defined":int(np.isfinite(buildings).sum()),"ruca_valid_ids":int(ruca_valid.sum()),
      "D3_support_mask_sha256":mask_sha256(defined),"provided_inputs_access":access,
      "provided_cache_files":files,"input_values_and_masks_sha256":{
        "ACS_housing_units":vector_sha256([g for g,_ in official],[r for _,r in official],housing,np.isfinite(housing)),
        "Overture_building_count":vector_sha256([g for g,_ in official],[r for _,r in official],buildings,np.isfinite(buildings)),
        "RUCA":vector_sha256([g for g,_ in official],[r for _,r in official],ruca,np.isfinite(ruca))},
      "D3_artifact":{"path":str(d3_path),"sha256":D3_ARTIFACT_SHA,"support_rows":int(artifact_mask.sum())},
      "provided_values_and_feature_mask_sha256":vector_sha256([g for g,_ in official],[r for _,r in official],feature,defined)}


def _summary(y:np.ndarray,mask:np.ndarray)->dict[str,Any]:
    x=np.asarray(y,dtype=np.float64)[np.asarray(mask,dtype=bool)]
    if not len(x):return {"n":0,"mean":None,"median_absolute":None,"p90_absolute":None}
    return {"n":int(len(x)),"mean":float(x.mean()),"median_absolute":float(np.median(np.abs(x))),
      "p90_absolute":float(np.quantile(np.abs(x),.9))}


def run(workspace:Path,report_path:Path)->dict[str,Any]:
    config=load_config(D3_CONFIG)
    d3_meta=json.loads(D3_HARNESS_REPORT.read_text(encoding="utf-8"))["per_idea"]["D3"]
    if d3_meta["sha256"]!=D3_ARTIFACT_SHA or d3_meta["emissions"][0]["defined_rows"]!=EXPECTED_D3_SUPPORT:
        raise ValueError("D3 frozen MEASURE-BEFORE artifact/support changed")
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):
        raise ValueError("official tract scope must be exactly 9,379 GEOID-region pairs")
    official_hash=hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    geoids=np.asarray([g for g,_ in official],dtype=object);regions=np.asarray([r for _,r in official],dtype=object)
    counties=np.asarray([g[:5] for g in geoids],dtype=object)
    y,residual_meta=_residuals(workspace,official)
    x,defined,pct_urban,svi,feature_meta=_strata_and_feature(workspace,official,config)
    support=defined&np.isfinite(y)
    if int(support.sum())!=EXPECTED_D3_SUPPORT:raise ValueError("D3/S1 paired support differs from 9,299 frozen tracts")
    pred=np.full(EXPECTED_N,np.nan);county_records=[]
    coverage={};
    for region in REGIONS:
        regional=support&(regions==region);region_counties=sorted(set(counties[regional].tolist()))
        if len(region_counties)<2:raise ValueError(f"{region} needs at least two supported counties")
        coverage[region]={"official_ids":int(np.sum(regions==region)),"D3_defined_ids":int(regional.sum()),
          "D3_undefined_ids":int(np.sum((regions==region)&~defined)),"heldout_counties":len(region_counties)}
        for county in region_counties:
            test=regional&(counties==county);train=regional&(counties!=county)
            local_train=y[train];train_x=x[train];test_x=x[test]
            county_prediction,fit=fit_one_term(local_train,train_x,test_x)
            pred[test]=county_prediction
            baseline_sse=float(np.dot(y[test],y[test]));model_sse=float(np.sum((y[test]-county_prediction)**2))
            county_records.append({"region":region,"county_fips":str(county),"heldout_n":int(test.sum()),
              "training_n":int(train.sum()),"baseline_sse":baseline_sse,"model_sse":model_sse,
              "delta_r2":(baseline_sse-model_sse)/baseline_sse if baseline_sse>0. else None,
              **fit})
    if np.any(support&~np.isfinite(pred)) or np.any(~support&np.isfinite(pred)):raise AssertionError("OOF prediction support mismatch")
    pooled=score_vs_frozen_s1(y[support],pred[support]);boot=county_block_bootstrap(county_records)
    regional_results={}
    for region in REGIONS:
        use=support&(regions==region)
        regional_results[region]={**score_vs_frozen_s1(y[use],pred[use]),"official_ids":coverage[region]["official_ids"],
          "D3_support_ids":int(use.sum()),"county_count":coverage[region]["heldout_counties"]}
    # Strata definitions are fixed from provided fields only: pct_urban cutoff .5 and within-region SVI quartiles.
    rural=np.isfinite(pct_urban)&(pct_urban<.5);urban=np.isfinite(pct_urban)&(pct_urban>=.5)
    svi_quartile=np.full(EXPECTED_N,-1,dtype=np.int8);svi_cuts={}
    for region in REGIONS:
        ix=np.flatnonzero((regions==region)&np.isfinite(svi))
        cut=np.quantile(svi[ix],[.25,.5,.75]);svi_cuts[region]=[float(v) for v in cut]
        svi_quartile[ix]=np.searchsorted(cut,svi[ix],side="right").astype(np.int8)+1
    strata={};gate_group_deltas=[]
    groups=[("rural",support&rural),("urban",support&urban)]
    groups.extend((f"svi_q{q}",support&(svi_quartile==q)) for q in range(1,5))
    for name,mask in groups:
        n=int(mask.sum());score=score_vs_frozen_s1(y[mask],pred[mask]) if n and np.dot(y[mask],y[mask])>0. else None
        strata[name]={**_summary(y,mask),"support_ids":n,"heldout_delta_r2":score["delta_r2"] if score else None,
          "sufficient_support_for_gate":n>=MIN_STRATUM_N}
        if n>=MIN_STRATUM_N and score is not None:gate_group_deltas.append(score["delta_r2"])
    worst=min(gate_group_deltas) if gate_group_deltas else None
    regional_feature_counts={r:coverage[r]["D3_defined_ids"] for r in REGIONS}
    ci=boot["pooled_delta_r2_ci_95"]
    passed=bool(pooled["delta_r2"]>=MIN_DELTA_R2 and ci[0]>MIN_CI_LOWER and worst is not None and worst>=-MAX_STRATUM_DECLINE)
    config_measure={"feature":"ln(provided ACS housing_units / provided Overture overture_buildings)",
      "support":"ACS_HU>0, Overture buildings>0, valid provided RUCA in [1,10]; exact emitted D3 mask, 9,299 tracts",
      "validation":"leave one five-digit county GEOID out within each official region",
      "correction":"intercept + one term, fit in training counties only; term standardized by training-fold mean and population SD",
      "baseline":"no correction to frozen S1 residual; baseline SSE=sum(y^2) on the exact paired OOF support",
      "strata":{"rural":"pct_urban <0.5","urban":"pct_urban >=0.5","svi_quartiles":"within-region empirical quartiles of provided svi_minority"},
      "minimum_stratum_support":MIN_STRATUM_N,"bootstrap_replicates":BOOTSTRAP_REPLICATES,"bootstrap_seed":BOOTSTRAP_SEED,
      "acceptance":{"pooled_delta_r2_min":MIN_DELTA_R2,"ci_lower_strictly_above":MIN_CI_LOWER,
        "max_stratum_decline":MAX_STRATUM_DECLINE}}
    config_hash=hashlib.sha256(json.dumps(config_measure,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    report={"schema_version":1,"screen":"County-held-out one-term D3 housing-unit intensity residual correction; descriptive-only",
      "measure_before":{"D3_defined_ids":EXPECTED_D3_SUPPORT,"official_ids":EXPECTED_N,
        "D3_defined_share":EXPECTED_D3_SUPPORT/EXPECTED_N,"county_heldout_delta_r2_measured":False,
        "D3_harness_report_sha256":sha256_file(D3_HARNESS_REPORT)},
      "method":{"feature":"Raw log ratio ln(ACS housing_units / Overture building count) from provided D3 inputs, with the emitted D3 mask; D3's globally RUCA-standardized emitted values are read only to verify support, not used for fitting.",
        "housing_unit_role":"Covariate only. ACS housing units are never used as an official building-gap denominator, floor, replacement reference, or target proxy.",
        "outcome":"Pinned frozen cross-fitted S1 Queen/Rook disagreement residual; no labels and no refit.",
        "validation":"Within each official region, leave one supported county out; fit intercept and one feature coefficient on all other supported counties in that region. Each supported tract is predicted once.",
        "scaling":"For each county fold, standardize the single raw log-ratio term with mean and population SD computed from training counties only; zero SD uses scale 1.",
        "baseline":"Unmodified frozen S1 residual (zero correction); compare paired OOF corrected SSE with sum of squared frozen residuals on identical support.",
        "metric":"Pooled county-held-out incremental ΔR²=(baseline SSE-corrected OOF SSE)/baseline SSE; region/stratum analogues use the same zero-correction baseline.",
        "uncertainty":"Paired county-block bootstrap of OOF baseline/model SSEs, counties sampled with replacement within each region.",
        "strata":"Report frozen-residual summaries and held-out correction ΔR² for pct_urban<0.5 rural, >=0.5 urban, and within-region provided SVI quartiles; minimum n=100 per pooled stratum for degradation gate.",
        "no_tuning_or_posthoc_thresholds":True,"unregistered":True,
        "interpretation_limit":"Provided-feature residual association only; not target accuracy, causality, or estimator promotion."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":official_hash,"frozen_residual_defined_ids":EXPECTED_N,
        "D3_defined_ids":int(support.sum()),"D3_undefined_ids":int((~support).sum()),
        "D3_support_mask_sha256":mask_sha256(support),"feature_values_and_mask_sha256":feature_meta["provided_values_and_feature_mask_sha256"],
        "coverage_by_region":coverage,"labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,
        "external_inputs_used":False,"removed_or_equivalent_references_read":False,"ACS_used_as_gap_denominator_or_floor":False,
        "leaderboard_feedback_used":False,"estimator_integration":False,"uploads":0},
      "measure_after":{"pooled":pooled,"county_block_bootstrap":boot,"regional":regional_results,
        "equity_stratum_residual_summaries_and_deltas":strata,"within_region_svi_quartile_cutpoints":svi_cuts,
        "worst_sufficient_stratum_delta_r2":worst,"county_fold_count":len(county_records),"county_folds":county_records},
      "thresholds":{"delta_r2_minimum_inclusive":MIN_DELTA_R2,"ci_lower_strictly_above":MIN_CI_LOWER,
        "maximum_urban_rural_or_svi_stratum_decline":MAX_STRATUM_DECLINE,"minimum_stratum_support":MIN_STRATUM_N},
      "acceptance":{"pooled_delta_r2_at_least_0_002":bool(pooled["delta_r2"]>=MIN_DELTA_R2),
        "county_block_ci_lower_above_zero":bool(ci[0]>MIN_CI_LOWER),
        "no_urban_rural_or_svi_degradation_exceeds_0_005":bool(worst is not None and worst>=-MAX_STRATUM_DECLINE),"screen_pass":passed},
      "provenance":{"frozen_residual":residual_meta,"D3_artifact":feature_meta["D3_artifact"],
        "D3_harness_report":{"path":str(D3_HARNESS_REPORT),"sha256":sha256_file(D3_HARNESS_REPORT)},
        "provided_ACS_and_feature_inputs":feature_meta["provided_inputs_access"],
        "provided_cache_files":feature_meta["provided_cache_files"],"raw_feature_support":{k:v for k,v in feature_meta.items() if k not in ("provided_inputs_access","provided_cache_files","D3_artifact")},
        "config":config_measure,"config_sha256":config_hash,"script_sha256":sha256_file(Path(__file__).resolve())},
      "interpretation":"The county-held-out screen passed all preregistered thresholds; still diagnostic pending separate estimator review." if passed else
        "The county-held-out screen failed one or more predeclared gates; retain descriptive-only and do not integrate."}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/d3-housing-intensity-residual-screen.json")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],
      "ci":report["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"],
      "regional":report["measure_after"]["regional"],"strata":report["measure_after"]["equity_stratum_residual_summaries_and_deltas"],
      "acceptance":report["acceptance"],"config_sha256":report["provenance"]["config_sha256"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
