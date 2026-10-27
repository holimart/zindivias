#!/usr/bin/env python3
"""County-held-out linear M3 address-mismatch plus missingness residual screen."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4p_conditional_innovation import read_emission, sha256_file  # noqa: E402
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
M3_SHA = "f560064aa9271e6f00f31f16c67242e30d9001c2ca427882bc873f97e0ab5538"
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT = ROOT / "reports/s1-disagreement-orthogonalized.json"
MX4_FEATURE_REPORT = ROOT / "reports/mx4-population-weighted-feature.json"
M3_CHANNEL = "covariate:facility_incoming_address_mismatch_share"
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261031
MIN_POOLED_DELTA_R2 = 0.002
MIN_CI_LOWER = 0.0
MAX_REGION_DECLINE = 0.005


def sha256_local(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def county_fips(geoid: str) -> str:
    value = str(geoid)
    if len(value) != 11 or not value.isdigit():
        raise ValueError(f"GEOID must be exactly eleven numeric characters: {value!r}")
    return value[:5]


def mask_hash(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little").tobytes()).hexdigest()


def aligned_feature_hash(geoids: list[str], regions: list[str], values: np.ndarray,
                         defined: np.ndarray) -> str:
    if len(geoids) != len(regions) or len(geoids) != len(values) or len(values) != len(defined):
        raise ValueError("feature hash inputs must be aligned")
    canonical = [f"{g},{r},{int(bool(ok))},{format(float(v),'.17g') if ok else ''}"
                 for g,r,v,ok in zip(geoids,regions,values,defined)]
    return hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()


def fit_linear_missingness(train_x: np.ndarray, train_defined: np.ndarray, train_y: np.ndarray,
                           test_x: np.ndarray, test_defined: np.ndarray
                           ) -> tuple[np.ndarray, dict[str, Any]]:
    """OLS correction with train-scaled provided share and a prespecified missingness flag."""
    x=np.asarray(train_x,dtype=np.float64); xd=np.asarray(train_defined,dtype=bool)
    y=np.asarray(train_y,dtype=np.float64); tx=np.asarray(test_x,dtype=np.float64)
    td=np.asarray(test_defined,dtype=bool)
    if x.ndim!=1 or xd.shape!=x.shape or y.shape!=x.shape or tx.ndim!=1 or td.shape!=tx.shape or not tx.size:
        raise ValueError("train/test feature, mask and residual vectors must align")
    if not (np.isfinite(y).all() and np.isfinite(x[xd]).all() and np.isfinite(tx[td]).all()):
        raise ValueError("defined feature values and residuals must be finite")
    observed=x[xd]
    if observed.size<2:
        raise ValueError("training fold needs at least two observed M3 values")
    mean=float(observed.mean())
    sd=float(observed.std(ddof=0))
    scale=sd if sd>0. else 1.
    # Missing values are imputed to the fixed semantic zero share; indicator distinguishes them
    # from observed zero shares. Scaling parameters use only observed training-fold values.
    train_filled=np.where(xd,x,0.)
    test_filled=np.where(td,tx,0.)
    train_linear=(train_filled-mean)/scale
    test_linear=(test_filled-mean)/scale
    train_missing=(~xd).astype(np.float64)
    test_missing=(~td).astype(np.float64)
    design=np.column_stack((np.ones(y.size),train_linear,train_missing))
    test_design=np.column_stack((np.ones(tx.size),test_linear,test_missing))
    coef,_,rank,singular=np.linalg.lstsq(design,y,rcond=None)
    prediction=test_design@coef
    if not np.isfinite(prediction).all():
        raise ValueError("M3 correction generated nonfinite predictions")
    return prediction,{"training_n":int(y.size),"training_observed_feature_n":int(xd.sum()),
      "training_missing_feature_n":int((~xd).sum()),"training_observed_feature_mean":mean,
      "training_observed_feature_sd":sd,"training_feature_applied_scale":scale,
      "missing_imputation_value":0.0,"linear_feature_centered_at_observed_train_mean":True,
      "coefficient_order":["intercept","standardized_M3_share","M3_missing_indicator"],
      "coefficients":coef.tolist(),"design_rank":int(rank),"design_columns":int(design.shape[1]),
      "minimum_singular_value":float(singular.min()) if singular.size else None,
      "maximum_singular_value":float(singular.max()) if singular.size else None}


def metric(y: np.ndarray,prediction: np.ndarray)->dict[str,float|int|None]:
    target=np.asarray(y,dtype=np.float64); pred=np.asarray(prediction,dtype=np.float64)
    if target.ndim!=1 or pred.shape!=target.shape or not target.size or not(np.isfinite(target).all() and np.isfinite(pred).all()):
        raise ValueError("metric inputs must be aligned finite nonempty vectors")
    baseline=float(np.dot(target,target)); model=float(np.sum((target-pred)**2))
    return {"n":int(target.size),"uncorrected_frozen_s1_baseline_sse":baseline,
      "linear_plus_missingness_sse":model,"delta_r2":(baseline-model)/baseline if baseline>0. else None}


def county_block_bootstrap(records:list[dict[str,Any]],replicates:int=BOOTSTRAP_REPLICATES,
                           seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    if replicates<100: raise ValueError("at least 100 bootstrap replicates required")
    by_region={r:[] for r in REGIONS}
    for record in records: by_region[record["region"]].append(record)
    if any(not rows for rows in by_region.values()): raise ValueError("every region needs supported counties")
    rng=np.random.default_rng(seed); pooled=np.empty(replicates); mean_county=np.empty(replicates)
    regional={r:np.empty(replicates) for r in REGIONS}
    regional_mean_county={r:np.empty(replicates) for r in REGIONS}
    for b in range(replicates):
        total_base=total_model=0.; all_county_deltas=[]
        for region in REGIONS:
            rows=by_region[region]; indices=rng.integers(0,len(rows),size=len(rows))
            base=sum(float(rows[i]["baseline_sse"]) for i in indices)
            model=sum(float(rows[i]["model_sse"]) for i in indices)
            regional[region][b]=(base-model)/base if base>0. else np.nan
            deltas=[float(rows[i]["out_of_county_delta_r2"]) for i in indices
                    if rows[i]["out_of_county_delta_r2"] is not None]
            regional_mean_county[region][b]=float(np.mean(deltas)) if deltas else np.nan
            all_county_deltas.extend(deltas)
            total_base+=base; total_model+=model
        pooled[b]=(total_base-total_model)/total_base if total_base>0. else np.nan
        mean_county[b]=float(np.mean(all_county_deltas)) if all_county_deltas else np.nan
    def interval(values:np.ndarray)->list[float]:
        finite=values[np.isfinite(values)]
        if finite.size<int(.99*replicates): raise ValueError("too many degenerate bootstrap samples")
        return [float(np.quantile(finite,.025)),float(np.quantile(finite,.975))]
    return {"replicates":int(replicates),"seed":int(seed),
      "resampling":"paired county OOF results; counties sampled with replacement within each official region",
      "pooled_delta_r2_ci_95":interval(pooled),"mean_county_delta_r2_ci_95":interval(mean_county),
      "regional_delta_r2_ci_95":{r:interval(v) for r,v in regional.items()},
      "regional_mean_county_delta_r2_ci_95":{r:interval(v) for r,v in regional_mean_county.items()},
      "degenerate_replicates":int(np.sum(~np.isfinite(pooled)))}


def run(workspace:Path,report_path:Path,manifest_path:Path)->dict[str,Any]:
    before=json.loads(MX4_FEATURE_REPORT.read_text(encoding="utf-8"))
    correlations={r["feature"]:r["pearson_r"] for r in before["pearson_vs_existing_F_covariates"]}
    correlation=correlations.get("M3|F|covariate:facility_incoming_address_mismatch_share")
    if correlation is None or not np.isclose(correlation,-0.010295012637247832,rtol=0.,atol=1e-15):
        raise ValueError("frozen M3/Mx4 MEASURE-BEFORE correlation differs")
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):
        raise ValueError("official scored scope must be 9,379 tract-region IDs across four regions")
    geoids=[g for g,_ in official]; region_values=np.asarray([r for _,r in official],dtype=object)
    counties=np.asarray([county_fips(g) for g in geoids],dtype=object)
    official_sha=hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    m3_path=workspace/"ideas/M3.parquet"
    m3,m3_defined,m3_sha=read_emission(m3_path,M3_SHA,"M3","F",M3_CHANNEL,official)
    if np.any(m3_defined&((m3<0.)|(m3>1.))): raise ValueError("defined M3 share values must be in [0,1]")
    residual_path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    residual_report=json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    if residual_report["provenance"]["output_artifact"]["sha256"]!=RESIDUAL_SHA or sha256_file(residual_path)!=RESIDUAL_SHA:
        raise ValueError("frozen S1 residual artifact/report did not match pinned SHA")
    table=pq.read_table(residual_path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if table.num_rows!=EXPECTED_N: raise ValueError("frozen S1 residual row count mismatch")
    positions={pair:i for i,pair in enumerate(official)}
    y=np.full(EXPECTED_N,np.nan); residual_defined=np.zeros(EXPECTED_N,dtype=bool); seen=set()
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in positions or pair in seen: raise ValueError(f"unexpected or duplicate residual ID {pair}")
        seen.add(pair); i=positions[pair]
        if not isinstance(row["crossfit_joint_defined"],bool): raise ValueError("residual definedness must be boolean")
        residual_defined[i]=row["crossfit_joint_defined"]
        if row["crossfit_residual"] is not None: y[i]=float(row["crossfit_residual"])
    if seen!=set(official) or not np.array_equal(np.isfinite(y),residual_defined):
        raise ValueError("frozen residual IDs/defined mask do not match official partition")
    # Keep every tract with a frozen residual: M3 missingness is explicitly modeled, not dropped.
    support=residual_defined&np.isfinite(y)
    if not support.all(): raise ValueError("frozen S1 residual must cover every official tract for this screen")
    predictions=np.full(EXPECTED_N,np.nan); county_records=[]; manifest_rows=[]
    coverage_by_county=[]
    all_counties=sorted(set(zip(region_values.tolist(),counties.tolist())))
    for region,county in all_counties:
        cmask=(region_values==region)&(counties==county)
        coverage_by_county.append({"region":region,"county_fips":county,"official_tracts":int(cmask.sum()),
          "M3_defined_tracts":int(np.sum(cmask&m3_defined)),"M3_missing_tracts":int(np.sum(cmask&~m3_defined)),
          "missingness_fraction":float(np.mean(~m3_defined[cmask]))})
    fold_records=[]
    for region in REGIONS:
        regional=support&(region_values==region)
        regional_counties=sorted(set(counties[regional].tolist()))
        if len(regional_counties)<2: raise ValueError(f"{region}: insufficient counties for county-held-out validation")
        for held in regional_counties:
            test=regional&(counties==held); train=regional&(counties!=held)
            if not test.any() or train.sum()<4: raise ValueError(f"{region}/{held}: insufficient train/test rows")
            pred,fit=fit_linear_missingness(m3[train],m3_defined[train],y[train],m3[test],m3_defined[test])
            predictions[test]=pred
            training_counties=[str(c) for c in regional_counties if c!=held]
            train_hash=hashlib.sha256("\n".join(sorted(training_counties)).encode()).hexdigest()
            base=float(np.dot(y[test],y[test])); model=float(np.sum((y[test]-pred)**2))
            delta=(base-model)/base if base>0. else None
            record={"region":region,"county_fips":str(held),"heldout_n":int(test.sum()),
              "heldout_M3_defined_n":int(np.sum(test&m3_defined)),"heldout_M3_missing_n":int(np.sum(test&~m3_defined)),
              "training_n":int(train.sum()),"training_M3_defined_n":int(np.sum(train&m3_defined)),
              "training_M3_missing_n":int(np.sum(train&~m3_defined)),"training_county_count":len(training_counties),
              "training_counties_sha256":train_hash,"baseline_sse":base,"model_sse":model,
              "out_of_county_delta_r2":delta,"training_fit":fit}
            county_records.append(record)
            fold_records.append({"region":region,"heldout_county_fips":str(held),
              "training_counties_sha256":train_hash,"training_county_count":len(training_counties),
              "training_support_n":int(train.sum())})
            for geoid in np.asarray(geoids,dtype=object)[test]:
                manifest_rows.append((str(geoid),region,str(held),train_hash,len(training_counties),int(train.sum())))
    if np.any(support&~np.isfinite(predictions)) or np.any(~support&np.isfinite(predictions)):
        raise AssertionError("OOF predictions must exactly match frozen residual support")
    pooled=metric(y[support],predictions[support])
    regional_results={}
    for region in REGIONS:
        mask=support&(region_values==region)
        regional_results[region]={**metric(y[mask],predictions[mask]),"official_tracts":int(np.sum(region_values==region)),
          "M3_defined_tracts":int(np.sum((region_values==region)&m3_defined)),
          "M3_missing_tracts":int(np.sum((region_values==region)&~m3_defined)),
          "missingness_fraction":float(np.mean(~m3_defined[region_values==region])),
          "county_count":len(set(counties[mask].tolist()))}
    bootstrap=county_block_bootstrap(county_records)
    worst_region=min(float(regional_results[r]["delta_r2"]) for r in REGIONS)
    pooled_ci=bootstrap["pooled_delta_r2_ci_95"]
    mean_county_delta=float(np.mean([r["out_of_county_delta_r2"] for r in county_records if r["out_of_county_delta_r2"] is not None]))
    mean_ci=bootstrap["mean_county_delta_r2_ci_95"]
    passed=bool(mean_county_delta>=MIN_POOLED_DELTA_R2 and mean_ci[0]>MIN_CI_LOWER
      and worst_region>=-MAX_REGION_DECLINE)
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    with manifest_path.open("w",encoding="utf-8",newline="") as stream:
        writer=csv.writer(stream,lineterminator="\n")
        writer.writerow(["GEOID","region","heldout_county_fips","training_counties_sha256","training_county_count","training_support_n"])
        writer.writerows(sorted(manifest_rows,key=lambda row:(row[1],row[0])))
    manifest_sha=sha256_local(manifest_path)
    fhash=aligned_feature_hash(geoids,[r for _,r in official],m3,m3_defined)
    config={"idea":"M3","component":"F","channel":M3_CHANNEL,"outcome":"pinned frozen S1 crossfit_residual",
      "feature_transform":"observed-train mean/SD; missing M3 imputed zero before transform; missing indicator is separate binary term",
      "model":"intercept + standardized linear M3 share + M3 missingness indicator; no tuning",
      "fold":"leave one county FIPS out within each official region","baseline":"zero correction to frozen residual",
      "bootstrap_replicates":BOOTSTRAP_REPLICATES,"bootstrap_seed":BOOTSTRAP_SEED,
      "thresholds":{"pooled_delta_r2":MIN_POOLED_DELTA_R2,"pooled_ci_lower_strictly_above":MIN_CI_LOWER,
        "minimum_region_delta_r2":-MAX_REGION_DECLINE}}
    config_sha=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    report={"schema_version":1,"screen":"County-held-out M3 facility address-mismatch linear term plus missingness indicator; unregistered",
      "measure_before":{"M3_vs_Mx4_pearson_r":float(correlation),"residual_predictive_delta_r2_measured":False,
        "source_report_sha256":sha256_local(MX4_FEATURE_REPORT)},
      "method":{"outcome":"Pinned frozen S1 orthogonalized-disagreement residual; no target or model labels.",
        "feature":{"idea":"M3","component":"F","channel":M3_CHANNEL,"meaning":"provided facility incoming-address mismatch share"},
        "design":"intercept + one train-standardized linear M3 share + one binary M3-missingness indicator; OLS via np.linalg.lstsq. Train mean/SD are computed on defined M3 training rows only. Undefined values are imputed to share zero before standardizing and explicitly marked by missingness=1; observed zero remains missingness=0.",
        "validation":"Within each official region, hold one county FIPS out and fit using all other counties only. Every official tract with frozen S1 residual is evaluated once, including M3-undefined rows.",
        "baseline":"Uncorrected frozen S1 residual, prediction correction fixed at zero, on the identical complete S1 residual support.",
        "metric":"Pooled and region-specific held-out delta-R2=(baseline SSE-model SSE)/baseline SSE, baseline SSE=sum(frozen residual^2).",
        "uncertainty":"Paired county-block percentile bootstrap; resample held-out counties with replacement within each region, fixed seed and 2,000 replicates.",
        "missingness":"Not dropped; report feature coverage by region/county and include an indicator so missing rows are distinct from observed zero shares.",
        "unregistered":True,"labels_read":False,"sample_score_values_read":False,"removed_or_equivalent_references_read":False,
        "external_data_used":[],"leaderboard_feedback_used":False,"uploads":0,
        "interpretation_limit":"Provided-feature residual association only; not target accuracy, causal evidence or authorization to promote/integrate."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":official_sha,"residual_defined_ids":int(residual_defined.sum()),
        "M3_defined_ids":int(m3_defined.sum()),"M3_missing_ids":int((~m3_defined).sum()),"paired_support_ids":int(support.sum()),
        "residual_mask_sha256":mask_hash(residual_defined),"M3_defined_mask_sha256":mask_hash(m3_defined),
        "paired_mask_sha256":mask_hash(support),"M3_feature_values_and_mask_sha256":fhash,
        "regions":{r:{"official_tracts":int(np.sum(region_values==r)),"M3_defined_tracts":int(np.sum((region_values==r)&m3_defined)),
          "M3_missing_tracts":int(np.sum((region_values==r)&~m3_defined)),"missingness_fraction":float(np.mean(~m3_defined[region_values==r]))} for r in REGIONS},
        "coverage_by_county":coverage_by_county,"county_count":len(county_records),"labels_read":False,
        "sample_score_values_read":False,"removed_or_equivalent_references_read":False,"external_data_used":[],
        "leaderboard_feedback_used":False,"uploads":0},
      "measure_after":{"pooled":pooled,"mean_county_delta_r2":mean_county_delta,
        "regional":regional_results,"worst_regional_delta_r2":worst_region,"county_block_bootstrap":bootstrap,
        "county_oof_records":county_records},
      "thresholds":{"mean_county_delta_r2_minimum_inclusive":MIN_POOLED_DELTA_R2,"mean_county_ci_lower_strictly_above":MIN_CI_LOWER,
        "minimum_region_delta_r2_inclusive":-MAX_REGION_DECLINE},
      "acceptance":{"mean_county_delta_r2_at_least_0_002":bool(mean_county_delta>=MIN_POOLED_DELTA_R2),
        "mean_county_block_ci_lower_above_zero":bool(mean_ci[0]>MIN_CI_LOWER),
        "no_region_decline_exceeds_0_005":bool(worst_region>=-MAX_REGION_DECLINE),"screen_pass":passed},
      "provenance":{"official_geoid_region_sha256":official_sha,
        "M3_artifact":{"path":str(m3_path),"sha256":m3_sha,"idea":"M3","component":"F","channel":M3_CHANNEL,
          "feature_values_and_mask_sha256":fhash},
        "frozen_S1_residual_artifact":{"path":str(residual_path),"sha256":RESIDUAL_SHA,"rows":EXPECTED_N,
          "report_path":str(RESIDUAL_REPORT),"report_sha256":sha256_local(RESIDUAL_REPORT)},
        "MEASURE_BEFORE_report":{"path":str(MX4_FEATURE_REPORT),"sha256":sha256_local(MX4_FEATURE_REPORT)},
        "config_sha256":config_sha,"config":config,
        "fold_manifest":{"path":str(manifest_path),"sha256":manifest_sha,"rows":len(manifest_rows),
          "columns":["GEOID","region","heldout_county_fips","training_counties_sha256","training_county_count","training_support_n"]},
        "script_sha256":sha256_local(Path(__file__).resolve())},
      "interpretation":"The M3 linear-plus-missingness screen passed; remain descriptive-only." if passed else
        "The M3 linear-plus-missingness screen failed one or more gates; retain the uncorrected frozen S1 residual and do not integrate."}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/m3-address-mismatch-residual-screen.json")
    parser.add_argument("--manifest",default="reports/m3-address-mismatch-residual-fold-manifest.csv")
    args=parser.parse_args(); workspace=Path(args.workspace)
    if not workspace.is_dir(): parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out,ROOT/args.manifest)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],
      "mean_county_delta_r2":report["measure_after"]["mean_county_delta_r2"],"regional":report["measure_after"]["regional"],
      "bootstrap":report["measure_after"]["county_block_bootstrap"],"acceptance":report["acceptance"],
      "feature_hash":report["provenance"]["M3_artifact"]["feature_values_and_mask_sha256"],
      "config_sha256":report["provenance"]["config_sha256"],"manifest":report["provenance"]["fold_manifest"]},indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
