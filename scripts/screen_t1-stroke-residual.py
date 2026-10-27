#!/usr/bin/env python3
"""County-held-out one-term residual screen of registered T1 named-stroke class-drop gap."""
from __future__ import annotations
import argparse,csv,hashlib,json,os,sys
from pathlib import Path
from typing import Any
import numpy as np
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa:E402
from audit_mx4p_conditional_innovation import read_emission  # noqa:E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379;EXPECTED_T1_SUPPORT=9347
T1_SHA="96faeb4ce17ffc4558ed40c1750f723fa653ef289af7a8567dcf2b3d6d1fd23c"
T1_HARNESS_REPORT=ROOT/"reports/ideas-harness.json"
T1_FAMILY_REPORT=ROOT/"reports/bias60-family-t-summary.json"
T1_SOURCE=ROOT/"src/bias60/ideas/t.py"
T1_RECIPE_REPORT=ROOT/"reports/bias60-family-t-exact-reconciliation.json"
S1_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
S1_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
CONFIG_PATH=ROOT/"config/bias60-harness-preregistered.json"
BOOTSTRAP_REPLICATES=2000;BOOTSTRAP_SEED=20261103
MIN_DELTA_R2=.002;MIN_CI_LOWER=0.;MIN_POSITIVE_REGIONS=3


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def county_fips(geoid:str)->str:
    value=str(geoid)
    if len(value)!=11 or not value.isdigit():raise ValueError("GEOID must contain eleven digits")
    return value[:5]


def mask_sha256(mask:np.ndarray)->str:
    return hashlib.sha256(np.packbits(np.asarray(mask,dtype=np.uint8),bitorder="little").tobytes()).hexdigest()


def data_sha256(geoids:list[str],regions:list[str],values:np.ndarray,defined:np.ndarray)->str:
    if not(len(geoids)==len(regions)==len(values)==len(defined)):raise ValueError("feature hash vectors misaligned")
    payload="\n".join(f"{g},{r},{int(ok)},{format(float(v),'.17g') if ok else ''}" for g,r,v,ok in zip(geoids,regions,values,defined))
    return hashlib.sha256(payload.encode()).hexdigest()


def fit_one_term(train_y:np.ndarray,train_x:np.ndarray,test_x:np.ndarray)->tuple[np.ndarray,dict[str,Any]]:
    y=np.asarray(train_y,dtype=np.float64);x=np.asarray(train_x,dtype=np.float64);held=np.asarray(test_x,dtype=np.float64)
    if x.ndim!=1 or held.ndim!=1 or y.shape!=x.shape or len(y)<3 or not len(held):raise ValueError("fold needs aligned vectors, >=3 training rows and held-out rows")
    if not(np.isfinite(y).all() and np.isfinite(x).all() and np.isfinite(held).all()):raise ValueError("fold values must be finite")
    mean=float(x.mean());sd=float(x.std(ddof=0));scale=sd if sd>0. else 1.
    z=(x-mean)/scale;zh=(held-mean)/scale;design=np.column_stack((np.ones(len(y)),z))
    coef,_,rank,_=np.linalg.lstsq(design,y,rcond=None)
    if rank!=2:raise ValueError("one-term training design is rank deficient")
    pred=np.column_stack((np.ones(len(held)),zh))@coef
    return pred,{"training_n":int(len(y)),"heldout_n":int(len(held)),"training_feature_mean":mean,
      "training_feature_sd":sd,"training_applied_scale":scale,"intercept":float(coef[0]),"standardized_slope":float(coef[1]),"design_rank":int(rank)}


def score_zero_baseline(y:np.ndarray,pred:np.ndarray)->dict[str,Any]:
    target=np.asarray(y,dtype=np.float64);estimate=np.asarray(pred,dtype=np.float64)
    if target.ndim!=1 or estimate.shape!=target.shape or not len(target) or not(np.isfinite(target).all() and np.isfinite(estimate).all()):raise ValueError("score vectors must align and be finite")
    base=float(target@target);model=float(np.sum((target-estimate)**2))
    if base<=0.:raise ValueError("frozen S1 residual baseline SSE must be positive")
    return {"n":int(len(target)),"frozen_s1_baseline_sse":base,"corrected_sse":model,"delta_r2":(base-model)/base}


def summarize_group(y:np.ndarray,pred:np.ndarray,mask:np.ndarray)->dict[str,Any]:
    if not np.any(mask):return {"support_n":0,"delta_r2":None,"mean_frozen_residual":None,"mean_absolute_frozen_residual":None}
    return {**score_zero_baseline(y[mask],pred[mask]),"mean_frozen_residual":float(y[mask].mean()),
      "mean_absolute_frozen_residual":float(np.abs(y[mask]).mean())}


def county_bootstrap(records:list[dict[str,Any]])->dict[str,Any]:
    groups={r:[] for r in REGIONS}
    for row in records:groups[row["region"]].append(row)
    if any(not rows for rows in groups.values()):raise ValueError("each region must have held-out counties")
    rng=np.random.default_rng(BOOTSTRAP_SEED);pooled=np.empty(BOOTSTRAP_REPLICATES);regional={r:np.empty(BOOTSTRAP_REPLICATES) for r in REGIONS}
    for b in range(BOOTSTRAP_REPLICATES):
        base_total=model_total=0.
        for region in REGIONS:
            rows=groups[region];draw=rng.integers(0,len(rows),size=len(rows))
            base=sum(float(rows[i]["baseline_sse"]) for i in draw);model=sum(float(rows[i]["model_sse"]) for i in draw)
            regional[region][b]=(base-model)/base if base>0. else np.nan;base_total+=base;model_total+=model
        pooled[b]=(base_total-model_total)/base_total if base_total>0. else np.nan
    def ci(values:np.ndarray)->list[float]:
        finite=values[np.isfinite(values)]
        if len(finite)<int(.99*BOOTSTRAP_REPLICATES):raise ValueError("too many degenerate county bootstrap draws")
        return [float(np.quantile(finite,.025)),float(np.quantile(finite,.975))]
    return {"replicates":BOOTSTRAP_REPLICATES,"seed":BOOTSTRAP_SEED,
      "resampling":"paired held-county baseline/model SSE blocks; counties sampled with replacement within each official region",
      "pooled_delta_r2_ci_95":ci(pooled),"regional_delta_r2_ci_95":{r:ci(v) for r,v in regional.items()},
      "degenerate_replicates":int(np.sum(~np.isfinite(pooled)))}


def load_s1_residuals(workspace:Path,official:list[tuple[str,str]])->tuple[np.ndarray,dict[str,str]]:
    path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(path)!=S1_SHA:raise ValueError("frozen S1 residual artifact SHA mismatch")
    report=json.loads(S1_REPORT.read_text(encoding="utf-8"))
    if report["provenance"]["output_artifact"]["sha256"]!=S1_SHA:raise ValueError("frozen S1 residual report hash mismatch")
    table=pq.read_table(path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if table.num_rows!=len(official):raise ValueError("S1 residual row count mismatch")
    positions={pair:i for i,pair in enumerate(official)};seen=set();values=np.full(len(official),np.nan)
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in positions or pair in seen:raise ValueError("unexpected or duplicate S1 residual row")
        if row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:raise ValueError("frozen S1 residual support incomplete")
        values[positions[pair]]=float(row["crossfit_residual"]);seen.add(pair)
    if seen!=set(official) or not np.isfinite(values).all():raise ValueError("S1 residual IDs/mask mismatch")
    return values,{"path":str(path),"sha256":S1_SHA,"report_sha256":sha256_file(S1_REPORT)}


def load_pct_urban(workspace:Path,official:list[tuple[str,str]],config:dict[str,Any])->tuple[np.ndarray,dict[str,Any]]:
    pct=np.full(len(official),np.nan);paths={}
    for region in REGIONS:
        idx=[i for i,(_,r) in enumerate(official) if r==region];inputs=ProvidedInputs(config,workspace,region,[official[i][0] for i in idx])
        pct[idx]=np.asarray(inputs.strata(["pct_urban"])["pct_urban"],dtype=np.float64)
        path=workspace/config["inputs"]["strata_cache_template"].format(region=region)
        paths[str(path)]={"sha256":sha256_file(path),"columns_read":["GEOID","pct_urban"]}
    known=np.isfinite(pct)
    return pct,{"source":"provided pct_urban, read only for descriptive rural/urban breakdown","cutoff":.5,
      "pct_urban_values_and_mask_sha256":data_sha256([g for g,_ in official],[r for _,r in official],pct,known),
      "defined_ids":int(known.sum()),"strata_cache_files":paths}


def run(workspace:Path,report_path:Path,manifest_path:Path)->dict[str,Any]:
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)];geoids=[g for g,_ in official];regions=np.asarray([r for _,r in official],dtype=object)
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or len(set(geoids))!=EXPECTED_N or set(regions.tolist())!=set(REGIONS):raise ValueError("official 9,379 tract scope mismatch")
    t1_path=workspace/"ideas/T1.parquet"
    feature,defined,t1_sha=read_emission(t1_path,T1_SHA,"T1","R","gap",official)
    if int(defined.sum())!=EXPECTED_T1_SUPPORT:raise ValueError("registered T1 gap support mismatch")
    if np.any(defined&(~np.isfinite(feature)|(feature<0.)|(feature>1.))):raise ValueError("defined T1 gap values must be finite and in [0,1]")
    harness=json.loads(T1_HARNESS_REPORT.read_text(encoding="utf-8"));harness_t1=harness["per_idea"]["T1"]
    family=json.loads(T1_FAMILY_REPORT.read_text(encoding="utf-8"));family_t1=family["outputs"]["T1"]
    if harness_t1["sha256"]!=T1_SHA or harness_t1["path"]!="ideas/T1.parquet" or family_t1["sha256"]!=T1_SHA:raise ValueError("registered T1 artifact provenance mismatch")
    if harness_t1["emissions"]!=[{"channel":"covariate","component":"R","defined_rows":9347,"rows":9379,"value_max":237.3875063182366,"value_min":0.0},{"channel":"gap","component":"R","defined_rows":9347,"rows":9379,"value_max":1.0,"value_min":0.0}]:raise ValueError("T1 registered emission contract changed")
    if harness_t1["accessed_inputs"]["layers"]!=["census-acs-housing","roads-unfiltered"]:raise ValueError("T1 harness input layers changed")
    s1,residual_meta=load_s1_residuals(workspace,official);support=defined&np.isfinite(s1)
    if int(support.sum())!=EXPECTED_T1_SUPPORT:raise ValueError("T1/S1 paired support mismatch")
    config=load_config(CONFIG_PATH);pct_urban,urban_meta=load_pct_urban(workspace,official,config)
    counties=np.asarray([county_fips(g) for g in geoids],dtype=object);prediction=np.full(EXPECTED_N,np.nan);records=[];manifest_rows=[];fold_counts={}
    for region in REGIONS:
        region_mask=support&(regions==region);county_list=sorted(set(counties[region_mask].tolist()))
        if len(county_list)<2:raise ValueError(f"insufficient county folds in {region}")
        fold_counts[region]=len(county_list)
        for held in county_list:
            test=region_mask&(counties==held);train=region_mask&(counties!=held)
            if not test.any() or train.sum()<3:raise ValueError(f"invalid fold {region}/{held}")
            pred,fit=fit_one_term(s1[train],feature[train],feature[test]);prediction[test]=pred
            base=float(s1[test]@s1[test]);model=float(np.sum((s1[test]-pred)**2));train_counties=sorted(set(counties[train].tolist()))
            train_hash=hashlib.sha256("\n".join(train_counties).encode()).hexdigest()
            records.append({"region":region,"heldout_county_fips":held,"heldout_n":int(test.sum()),"training_n":int(train.sum()),
              "training_county_count":len(train_counties),"training_county_fips_sha256":train_hash,"baseline_sse":base,"model_sse":model,
              "delta_r2":(base-model)/base if base else None,"fit":fit})
            for i in np.flatnonzero(test):manifest_rows.append((geoids[i],region,held,train_hash,len(train_counties),int(train.sum())))
    if np.any(support&~np.isfinite(prediction)) or np.any(~support&np.isfinite(prediction)):raise AssertionError("OOF prediction mask mismatch")
    pooled=score_zero_baseline(s1[support],prediction[support]);bootstrap=county_bootstrap(records);regional={}
    for region in REGIONS:
        use=support&(regions==region);regional[region]={**score_zero_baseline(s1[use],prediction[use]),
          "county_count":fold_counts[region],"official_ids":int(np.sum(regions==region)),"T1_defined_ids":int(np.sum(defined&(regions==region))),
          "T1_feature_distribution":{"minimum":float(feature[use].min()),"p25":float(np.quantile(feature[use],.25)),"p50":float(np.quantile(feature[use],.5)),
            "p75":float(np.quantile(feature[use],.75)),"maximum":float(feature[use].max())}}
    rural=np.isfinite(pct_urban)&(pct_urban<.5);urban=np.isfinite(pct_urban)&(pct_urban>=.5)
    urban_rural={"definition":{"rural":"provided pct_urban < 0.5","urban":"provided pct_urban >= 0.5","missing":"excluded"},
      "rural":summarize_group(s1,prediction,support&rural),"urban":summarize_group(s1,prediction,support&urban),
      "missing_pct_urban_support_n":int(np.sum(support&~np.isfinite(pct_urban))),"by_region":{}}
    for region in REGIONS:
        in_region=regions==region;urban_rural["by_region"][region]={
          "rural":summarize_group(s1,prediction,support&in_region&rural),"urban":summarize_group(s1,prediction,support&in_region&urban),
          "missing_pct_urban_support_n":int(np.sum(support&in_region&~np.isfinite(pct_urban)))}
    positive=sum(regional[r]["delta_r2"]>0. for r in REGIONS);ci=bootstrap["pooled_delta_r2_ci_95"]
    passed=bool(pooled["delta_r2"]>=MIN_DELTA_R2 and ci[0]>MIN_CI_LOWER and positive>=MIN_POSITIVE_REGIONS)
    manifest_buffer=__import__("io").StringIO(newline="");writer=csv.writer(manifest_buffer,lineterminator="\n")
    writer.writerow(["GEOID","region","heldout_county_fips","training_county_fips_sha256","training_county_count","training_support_n"])
    writer.writerows(sorted(manifest_rows,key=lambda row:(row[1],row[0])));manifest_sha=hashlib.sha256(manifest_buffer.getvalue().encode()).hexdigest()
    manifest_path.parent.mkdir(parents=True,exist_ok=True);manifest_path.write_text(manifest_buffer.getvalue(),encoding="utf-8")
    feature_hash=data_sha256(geoids,[r for _,r in official],feature,defined)
    by_region={}
    for region in REGIONS:
        ix=regions==region;by_region[region]={"official_ids":int(ix.sum()),"T1_defined_ids":int(np.sum(defined&ix)),
          "T1_defined_fraction":float(np.sum(defined&ix)/np.sum(ix)),"T1_feature_sha256":data_sha256([geoids[i] for i in np.flatnonzero(ix)],[region]*int(ix.sum()),feature[ix],defined[ix])}
    report={"schema_version":1,"screen":"Registered T1 named-stroke class-drop gap county-held-out residual screen; diagnostic-only unless fixed gates pass",
      "measure_before":{"T1_registered":True,"T1_gap_definition":"Registered T1|R|gap from the pinned official T1 artifact; one linear term, not the T1 covariate emission.",
        "county_heldout_T1_residual_delta_r2_previously_measured":False,"official_ids":EXPECTED_N,"T1_defined_ids":int(defined.sum()),
        "S1_residual_artifact_sha256":S1_SHA,"T1_artifact_sha256":T1_SHA},
      "method":{"feature":"Use the frozen registered T1|R|gap output only. Its upstream harness provenance is checked; the screen does not reread or rebuild Overture roads.",
        "outcome":"Pinned frozen S1 cross-fitted estimator-disagreement residual; no target or reference layer read.",
        "validation":"Within each official region, hold out each supported five-digit county FIPS in turn; fit on other supported counties in that region.",
        "correction":"OLS intercept plus one T1 gap term; training-fold feature mean and population SD only, zero-SD scale=1.",
        "baseline":"Zero correction of frozen S1 residual on identical paired support.",
        "metric":"Pooled and regional county-held-out ΔR²=(baseline SSE - OOF model SSE)/baseline SSE; 2,000 paired county-block bootstrap replicates resampled within region.",
        "urban_rural":"Descriptive diagnostics on OOF residual predictions using only provided pct_urban for groups: rural <0.5, urban >=0.5; missing excluded.",
        "registered_feature":"T1|R|gap","unregistered_screen":True,
        "interpretation_limit":"Association with estimator disagreement only; not target accuracy, causal evidence, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"T1_defined_ids":int(defined.sum()),"paired_residual_support":int(support.sum()),
        "undefined_T1_ids":int((~defined).sum()),"T1_feature_mask_sha256":mask_sha256(defined),"T1_feature_values_and_mask_sha256":feature_hash,
        "T1_defined_ids_by_region":by_region,"frozen_residual_defined_ids":EXPECTED_N,
        "official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,"external_inputs_used":False,
        "removed_or_equivalent_references_read":False,"external_road_history_read":False,"leaderboard_feedback_used":False,"estimator_integration":False,"uploads":0},
      "measure_after":{"pooled":pooled,"county_block_bootstrap":bootstrap,"regional":regional,"urban_rural_support_and_deltas":urban_rural,
        "county_fold_count":len(records),"county_fold_counts_by_region":fold_counts,"county_folds":records,
        "fold_manifest":{"path":str(manifest_path),"sha256":manifest_sha,"rows":len(manifest_rows)}},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_DELTA_R2,"bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,"minimum_positive_regions":MIN_POSITIVE_REGIONS},
      "acceptance":{"pooled_delta_r2_at_least_0_002":bool(pooled["delta_r2"]>=MIN_DELTA_R2),"county_block_ci_lower_above_zero":bool(ci[0]>MIN_CI_LOWER),
        "positive_region_count":positive,"positive_in_at_least_3_of_4_regions":bool(positive>=MIN_POSITIVE_REGIONS),"screen_pass":passed},
      "provenance":{"T1_artifact":{"path":str(t1_path),"sha256":t1_sha},"T1_harness_report":{"path":str(T1_HARNESS_REPORT),"sha256":sha256_file(T1_HARNESS_REPORT)},
        "T1_family_summary":{"path":str(T1_FAMILY_REPORT),"sha256":sha256_file(T1_FAMILY_REPORT)},"T1_source_definition":{"path":str(T1_SOURCE),"sha256":sha256_file(T1_SOURCE)},
        "T1_exact_recipe_reconciliation":{"path":str(T1_RECIPE_REPORT),"sha256":sha256_file(T1_RECIPE_REPORT)},"frozen_S1_residual":residual_meta,
        "provided_urbanity":urban_meta,"config":{"path":str(CONFIG_PATH),"sha256":sha256_file(CONFIG_PATH)},
        "analysis_script_sha256":sha256_file(Path(__file__).resolve()),"bootstrap_seed":BOOTSTRAP_SEED,"bootstrap_replicates":BOOTSTRAP_REPLICATES},
      "interpretation":"The single-term registered T1 residual screen failed one or more fixed acceptance gates; retain diagnostic-only and do not integrate." if not passed else
        "The single-term T1 screen passed its fixed residual-association gates; this does not establish target accuracy or authorize estimator integration."}
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/t1-stroke-residual-screen.json");parser.add_argument("--manifest",default="reports/t1-stroke-residual-county-folds.csv")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out,ROOT/args.manifest)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],
      "pooled_ci":report["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"],
      "regional":report["measure_after"]["regional"],"urban_rural":report["measure_after"]["urban_rural_support_and_deltas"],"acceptance":report["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
