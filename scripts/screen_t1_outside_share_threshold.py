#!/usr/bin/env python3
"""Nested county screen of T1's outside-named-share threshold; diagnostic only."""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa:E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
THRESHOLDS=(0.5,0.7,0.9)
CONFIG_PATH=ROOT/"config/t1-outside-share-threshold-preregistered.json"
HARNESS_CONFIG=ROOT/"config/bias60-harness-preregistered.json"
T1_SOURCE=ROOT/"src/bias60/ideas/t.py"
T1_WORKSPACE_SHA="96faeb4ce17ffc4558ed40c1750f723fa653ef289af7a8567dcf2b3d6d1fd23c"
BOOTSTRAP_REPLICATES=2000
BOOTSTRAP_SEED=20261104


def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def module_from_script(path:Path,name:str):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise ImportError(f"cannot load {path}")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


_SCREEN_HELPER=None

def screen_helpers():
    global _SCREEN_HELPER
    if _SCREEN_HELPER is None:
        _SCREEN_HELPER=module_from_script(ROOT/"scripts/screen_t1-stroke-residual.py","t1_frozen_screen_helpers")
    return _SCREEN_HELPER


def ids_and_regions(workspace:Path)->tuple[list[tuple[str,str]],dict[str,list[str]]]:
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=9379 or len(set(official))!=9379 or len({g for g,_ in official})!=9379:
        raise ValueError("official GEOID scope is not exactly 9,379 unique tracts")
    if set(r for _,r in official)!=set(REGIONS):raise ValueError("official region set mismatch")
    by_region={r:[g for g,rr in official if rr==r] for r in REGIONS}
    return official,by_region


def geoids_sha(geoids:list[str])->str:
    return hashlib.sha256("\n".join(geoids).encode()).hexdigest()


def data_sha(geoids:list[str],region:str,values:np.ndarray,defined:np.ndarray)->str:
    payload="\n".join(f"{g},{region},{int(ok)},{format(float(v),'.17g') if ok else ''}"
                       for g,v,ok in zip(geoids,values,defined))
    return hashlib.sha256(payload.encode()).hexdigest()


def cache_paths(cache_dir:Path,region:str,threshold:float)->tuple[Path,Path]:
    key=f"{threshold:.1f}"
    return cache_dir/f"{region}-outside-{key}.npz",cache_dir/f"{region}-outside-{key}.json"


def build_feature(workspace:Path,cache_dir:Path,region:str,threshold:float,
                  official:list[tuple[str,str]],by_region:dict[str,list[str]])->dict[str,Any]:
    if threshold not in THRESHOLDS:raise ValueError("unregistered threshold")
    cache_dir.mkdir(parents=True,exist_ok=True)
    data_path,meta_path=cache_paths(cache_dir,region,threshold)
    geoids=by_region[region]
    config=load_config(HARNESS_CONFIG)
    config=copy.deepcopy(config)
    config["seeds"]["T1"]["outside_named_share_threshold"]=threshold
    # The only changed seed is the preregistered threshold; T1 identity/classes remain frozen.
    inputs=ProvidedInputs(config,workspace,region,geoids)
    from bias60.ideas import IMPLEMENTATIONS
    output=IMPLEMENTATIONS["T1"](inputs)
    if output.geoids!=geoids or output.region!=region:raise ValueError("T1 output order/region mismatch")
    emission=next(e for e in output.emissions if e.component=="R" and e.channel=="gap")
    values=np.asarray(emission.values,dtype=np.float64)
    defined=np.asarray(emission.defined,dtype=bool)
    if values.shape!=(len(geoids),) or defined.shape!=values.shape:raise ValueError("T1 emission shape mismatch")
    if not np.isfinite(values).all() or np.any(values<0) or np.any(values>1):raise ValueError("T1 threshold emitted invalid gap values")
    if "roads-unfiltered" not in inputs.accessed["layers"]:raise ValueError("T1 did not read the provided unfiltered road layer")
    allowed_layers={"roads-unfiltered","census-acs-housing"}
    if not set(inputs.accessed["layers"]).issubset(allowed_layers):raise ValueError("T1 read an undeclared layer")
    feature_hash=data_sha(geoids,region,values,defined)
    frozen_match=None
    frozen_sha=None
    max_abs_diff=None
    if threshold==0.7:
        helper=screen_helpers()
        frozen_path=workspace/"ideas/T1.parquet"
        frozen, frozen_defined, frozen_sha=helper.read_emission(frozen_path,T1_WORKSPACE_SHA,"T1","R","gap",official)
        positions={g:i for i,(g,r) in enumerate(official) if r==region}
        region_values=np.asarray([frozen[positions[g]] for g in geoids],dtype=np.float64)
        region_defined=np.asarray([frozen_defined[positions[g]] for g in geoids],dtype=bool)
        valid=defined&region_defined
        max_abs_diff=float(np.max(np.abs(values[valid]-region_values[valid]),initial=0.))
        frozen_match=bool(np.array_equal(defined,region_defined) and np.allclose(values[valid],region_values[valid],rtol=0.,atol=1e-12))
        if frozen_sha!=T1_WORKSPACE_SHA or not frozen_match:
            mask_mismatch=int(np.sum(defined!=region_defined));value_mismatch=int(np.sum(np.abs(values[valid]-region_values[valid])>1e-12))
            raise ValueError(f"0.7 recomputation differs from registered frozen T1 gap: artifact_hash_match={frozen_sha==T1_WORKSPACE_SHA}, mask_mismatch={mask_mismatch}, value_mismatch={value_mismatch}, max_abs_diff={max_abs_diff}")
    tmp=data_path.with_suffix(".npz.tmp")
    with tmp.open("wb") as stream:
        np.savez_compressed(stream,geoids=np.asarray(geoids,dtype="U11"),values=values,defined=defined)
    tmp.replace(data_path)
    meta={"schema_version":1,"region":region,"threshold":threshold,"geoids_sha256":geoids_sha(geoids),
          "feature_values_and_mask_sha256":feature_hash,"defined_ids":int(defined.sum()),
          "official_ids":len(geoids),"t1_source_sha256":sha256(T1_SOURCE),
          "harness_config_sha256":sha256(HARNESS_CONFIG),"preregistered_config_sha256":sha256(CONFIG_PATH),
          "accessed_layers":inputs.accessed["layers"],"accessed_columns":inputs.accessed["parquet_columns"],
          "frozen_0_7_match":frozen_match,"frozen_0_7_artifact_sha256":frozen_sha,
          "frozen_0_7_max_abs_difference":max_abs_diff,
          "diagnostics":output.diagnostics}
    meta_path.write_text(json.dumps(meta,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    return meta


def load_feature_cache(cache_dir:Path,region:str,threshold:float,geoids:list[str])->tuple[np.ndarray,np.ndarray,dict[str,Any]]:
    data_path,meta_path=cache_paths(cache_dir,region,threshold)
    if not data_path.is_file() or not meta_path.is_file():raise FileNotFoundError(f"missing feature cache for {region}, threshold {threshold}; run --build-feature")
    meta=json.loads(meta_path.read_text(encoding="utf-8"))
    if meta["region"]!=region or meta["threshold"]!=threshold or meta["geoids_sha256"]!=geoids_sha(geoids):raise ValueError("feature cache metadata/official mask mismatch")
    with np.load(data_path,allow_pickle=False) as cache:
        cache_geoids=[str(g) for g in cache["geoids"].tolist()]
        values=np.asarray(cache["values"],dtype=np.float64)
        defined=np.asarray(cache["defined"],dtype=bool)
    if cache_geoids!=geoids or values.shape!=(len(geoids),) or defined.shape!=values.shape:raise ValueError("feature cache ID/order/shape mismatch")
    if int(defined.sum())!=meta["defined_ids"] or len(geoids)!=meta["official_ids"]:raise ValueError("feature cache support counts mismatch")
    if data_sha(geoids,region,values,defined)!=meta["feature_values_and_mask_sha256"]:raise ValueError("feature cache semantic hash mismatch")
    return values,defined,meta


def outer_inner_counties(official:list[tuple[str,str]],config:dict[str,Any])->tuple[dict[str,list[str]],dict[str,list[str]],str]:
    by_region={r:sorted({g[:5] for g,rr in official if rr==r}) for r in REGIONS}
    outer={};inner={};prefix=config["outer_split"]["split_seed_prefix"];fraction=float(config["outer_split"]["fraction"])
    for region,counties in by_region.items():
        ranked=sorted(counties,key=lambda fips:hashlib.sha256(f"{prefix}|{region}|{fips}".encode()).hexdigest())
        n_outer=max(1,math.ceil(fraction*len(counties)))
        if len(counties)-n_outer<3:raise ValueError(f"{region}: insufficient inner counties for nested CV")
        outer[region]=ranked[:n_outer];inner[region]=ranked[n_outer:]
    split_hash=hashlib.sha256(json.dumps({"outer":outer,"inner":inner},sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return outer,inner,split_hash


def fit_predict(train_y:np.ndarray,train_x:np.ndarray,test_x:np.ndarray)->tuple[np.ndarray,dict[str,Any]]:
    helper=screen_helpers()
    return helper.fit_one_term(train_y,train_x,test_x)


def sse_pair(y:np.ndarray,pred:np.ndarray)->tuple[float,float]:
    baseline=float(np.dot(y,y));model=float(np.sum((y-pred)**2))
    return baseline,model


def delta(base:float,model:float)->float:
    if base<=0:raise ValueError("zero-correction baseline SSE must be positive")
    return (base-model)/base


def select_inner_threshold(official:list[tuple[str,str]],geoids_by_region:dict[str,list[str]],
                           features:dict[float,tuple[np.ndarray,np.ndarray]],
                           residual:np.ndarray,outer:dict[str,list[str]],inner:dict[str,list[str]])->dict[str,Any]:
    all_geoids=[g for g,_ in official];positions={g:i for i,g in enumerate(all_geoids)}
    county=np.asarray([g[:5] for g in all_geoids],dtype=object);regions=np.asarray([r for _,r in official],dtype=object)
    threshold_metrics={}
    for threshold in THRESHOLDS:
        base_total=model_total=0.;folds=[];regional={}
        for region in REGIONS:
            region_ids=geoids_by_region[region];feature,defined=features[threshold]
            local_positions=np.asarray([positions[g] for g in region_ids],dtype=int)
            region_mask=np.zeros(len(all_geoids),dtype=bool);region_mask[local_positions]=True
            inner_mask=region_mask&np.isin(county,inner[region])&defined
            region_base=region_model=0.;region_n=0
            for held_fips in inner[region]:
                test=inner_mask&(county==held_fips);train=inner_mask&(county!=held_fips)
                if int(train.sum())<3 or not np.any(test):raise ValueError(f"inner fold has insufficient paired support: {region}/{held_fips}")
                prediction,fit=fit_predict(residual[train],feature[train],feature[test])
                b,m=sse_pair(residual[test],prediction);base_total+=b;model_total+=m;region_base+=b;region_model+=m;region_n+=int(test.sum())
                folds.append({"region":region,"heldout_county_fips":held_fips,"train_counties":len(inner[region])-1,
                              "training_n":int(train.sum()),"heldout_n":int(test.sum()),"baseline_sse":b,"model_sse":m,
                              "fit_rank":fit["design_rank"]})
            regional[region]={"n":region_n,"baseline_sse":region_base,"model_sse":region_model,
                              "delta_r2":delta(region_base,region_model)}
        threshold_metrics[str(threshold)]={"n":sum(x["n"] for x in regional.values()),
            "baseline_sse":base_total,"model_sse":model_total,"pooled_delta_r2":delta(base_total,model_total),
            "regional":regional,"inner_county_fold_count":len(folds),"county_folds":folds,
            "defined_ids_by_region":{r:int(np.sum(features[threshold][1]&(regions==r))) for r in REGIONS}}
    scored=sorted(THRESHOLDS,key=lambda t:(-threshold_metrics[str(t)]["pooled_delta_r2"],abs(t-0.7),t))
    chosen=scored[0]
    return {"selection_scope":"one global threshold selected by pooled inner county OOF delta_R2; outer county residuals are never read for selection",
            "selected_threshold":chosen,"selection_tie_break":"maximize pooled inner delta_R2; exact ties prefer nearest 0.7 then smaller threshold",
            "threshold_metrics":threshold_metrics}


def county_bootstrap(records:list[dict[str,Any]],replicates:int,seed:int)->dict[str,Any]:
    groups={region:[] for region in REGIONS}
    for record in records:groups[record["region"]].append(record)
    if any(not group for group in groups.values()):raise ValueError("outer county bootstrap requires counties in every region")
    rng=np.random.default_rng(seed);pooled=np.empty(replicates);regional={r:np.empty(replicates) for r in REGIONS}
    for b in range(replicates):
        base_total=model_total=0.
        for region in REGIONS:
            rows=groups[region];draw=rng.integers(0,len(rows),size=len(rows));base=sum(rows[i]["baseline_sse"] for i in draw);model=sum(rows[i]["model_sse"] for i in draw)
            if base<=0:raise ValueError("degenerate outer county bootstrap baseline")
            regional[region][b]=(base-model)/base;base_total+=base;model_total+=model
        pooled[b]=(base_total-model_total)/base_total
    def ci(values:np.ndarray)->list[float]:return [float(x) for x in np.quantile(values,[0.025,0.975])]
    return {"replicates":replicates,"seed":seed,"resampling":"paired outer-county baseline/model SSE blocks, counties sampled with replacement within each official region",
            "pooled_delta_r2_ci_95":ci(pooled),"regional_delta_r2_ci_95":{r:ci(vals) for r,vals in regional.items()},
            "pooled_bootstrap_mean":float(pooled.mean())}


def evaluate_outer(official:list[tuple[str,str]],geoids_by_region:dict[str,list[str]],
                   features:dict[float,tuple[np.ndarray,np.ndarray]],
                   residual:np.ndarray,outer:dict[str,list[str]],inner:dict[str,list[str]],threshold:float)->dict[str,Any]:
    all_geoids=[g for g,_ in official];county=np.asarray([g[:5] for g in all_geoids],dtype=object)
    regions=np.asarray([r for _,r in official],dtype=object);feature,defined=features[threshold]
    prediction=np.full(len(official),np.nan);records=[];regional={};support={}
    for region in REGIONS:
        region_mask=regions==region
        train=region_mask&np.isin(county,inner[region])&defined
        test=region_mask&np.isin(county,outer[region])&defined
        if int(train.sum())<3 or not np.any(test):raise ValueError(f"outer split has insufficient support in {region}")
        pred,fit=fit_predict(residual[train],feature[train],feature[test]);prediction[test]=pred
        regional_records=[]
        for fips in outer[region]:
            county_mask=test&(county==fips)
            if not county_mask.any():raise ValueError(f"fixed outer county has no threshold-defined support: {region}/{fips}")
            base,model=sse_pair(residual[county_mask],prediction[county_mask])
            record={"region":region,"county_fips":fips,"n":int(county_mask.sum()),"baseline_sse":base,"model_sse":model,
                    "delta_r2":delta(base,model)}
            records.append(record);regional_records.append(record)
        base=sum(r["baseline_sse"] for r in regional_records);model=sum(r["model_sse"] for r in regional_records)
        regional[region]={"n":sum(r["n"] for r in regional_records),"outer_county_count":len(regional_records),
                          "baseline_sse":base,"model_sse":model,"delta_r2":delta(base,model),
                          "outer_counties":regional_records,"fit":fit}
        support[region]={"official_ids":int(region_mask.sum()),"threshold_defined_ids":int(defined.sum()),
                         "inner_defined_ids":int(train.sum()),"outer_defined_ids":int(test.sum()),
                         "undefined_ids":int((~defined).sum())}
    base=sum(x["baseline_sse"] for x in regional.values());model=sum(x["model_sse"] for x in regional.values())
    return {"selected_threshold":threshold,"pooled":{"n":sum(x["n"] for x in regional.values()),"baseline_sse":base,
            "model_sse":model,"delta_r2":delta(base,model)},"regional":regional,"support":support,
            "outer_counties":records}


def run_screen(workspace:Path,cache_dir:Path,report_path:Path)->dict[str,Any]:
    official,geoids_by_region=ids_and_regions(workspace);helper=screen_helpers()
    residual,residual_meta=helper.load_s1_residuals(workspace,official)
    if not np.isfinite(residual).all():raise ValueError("frozen S1 residuals are incomplete")
    prereg=json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if tuple(float(x) for x in prereg.get("thresholds",[]))!=THRESHOLDS:raise ValueError("threshold list differs from preregistration")
    outer,inner,split_hash=outer_inner_counties(official,prereg)
    features:dict[float,tuple[np.ndarray,np.ndarray]]={}
    cache_metadata={}
    positions={g:i for i,(g,_) in enumerate(official)}
    for threshold in THRESHOLDS:
        values_full=np.zeros(len(official),dtype=np.float64);defined_full=np.zeros(len(official),dtype=bool)
        cache_metadata[str(threshold)]={}
        for region in REGIONS:
            values,defined,meta=load_feature_cache(cache_dir,region,threshold,geoids_by_region[region])
            if meta.get("t1_source_sha256")!=sha256(T1_SOURCE) or meta.get("harness_config_sha256")!=sha256(HARNESS_CONFIG):
                raise ValueError("feature cache source/config hash differs from current frozen inputs")
            if meta.get("preregistered_config_sha256")!=sha256(CONFIG_PATH):raise ValueError("feature cache preregistration hash mismatch")
            if threshold==0.7 and (meta.get("frozen_0_7_match") is not True or meta.get("frozen_0_7_artifact_sha256")!=T1_WORKSPACE_SHA):
                raise ValueError("cached threshold 0.7 was not verified against frozen T1")
            idx=np.asarray([positions[g] for g in geoids_by_region[region]],dtype=int)
            values_full[idx]=values;defined_full[idx]=defined
            cache_metadata[str(threshold)][region]=meta
        features[threshold]=(values_full,defined_full)
    inner_result=select_inner_threshold(official,geoids_by_region,features,residual,outer,inner)
    selected=float(inner_result["selected_threshold"])
    outer_result=evaluate_outer(official,geoids_by_region,features,residual,outer,inner,selected)
    bootstrap=county_bootstrap(outer_result["outer_counties"],int(prereg["outer_evaluation"]["bootstrap"]["replicates"]),int(prereg["outer_evaluation"]["bootstrap"]["seed"]))
    pooled_delta=outer_result["pooled"]["delta_r2"];ci=bootstrap["pooled_delta_r2_ci_95"]
    minimum_region=min(x["delta_r2"] for x in outer_result["regional"].values())
    acceptance={"pooled_outer_delta_r2_strictly_positive":pooled_delta>0.,
                "pooled_outer_county_bootstrap_ci_lower_strictly_positive":ci[0]>0.,
                "no_region_below_minus_0_005":minimum_region>=-0.005,
                "selected_threshold_passes_all_gates":bool(pooled_delta>0. and ci[0]>0. and minimum_region>=-0.005)}
    feature_hashes={str(t):{r:cache_metadata[str(t)][r]["feature_values_and_mask_sha256"] for r in REGIONS} for t in THRESHOLDS}
    prior_path=ROOT/"reports/t1-stroke-residual-screen.json"
    prior_report=json.loads(prior_path.read_text(encoding="utf-8"))
    prior_deltas={region:prior_report["measure_after"]["regional"][region]["delta_r2"] for region in REGIONS}
    report={"schema_version":1,"screen":"Nested county-held-out sensitivity of T1 outside-named-share threshold; diagnostic only unless all fixed gates pass.",
      "measure_before":{"registered_threshold":0.7,"fixed_threshold_prior_county_heldout_deltas":prior_deltas,
        "fixed_threshold_prior_source":"reports/t1-stroke-residual-screen.json; no labels used","source_sha256":sha256(prior_path)},
      "preregistration":{"path":str(CONFIG_PATH.relative_to(ROOT)),"sha256":sha256(CONFIG_PATH),"specification":prereg},
      "method":{"thresholds_tested":list(THRESHOLDS),"threshold_selection":"global pooled inner county OOF delta_R2; selection result uses inner counties only",
        "outer_county_rule":prereg["outer_split"]["method"],"inner_county_rule":prereg["inner_selection"]["method"],
        "outer_evaluation_rule":prereg["outer_evaluation"]["method"],"fit":"same one-term OLS residual correction as the registered T1 screen; fit and standardize on training counties only",
        "baseline":"frozen S1 residual with zero correction, on identical threshold-defined support",
        "bootstrap":prereg["outer_evaluation"]["bootstrap"],"identity_rule":"unchanged frozen T1 _identity function (casefolded names.primary; fallback route network/ref)",
        "0.7_frozen_reproduction":"the recomputed 0.7 T1 gap and defined mask were checked against the registered T1.parquet before use"},
      "scope":{"official_ids":len(official),"regions":list(REGIONS),"labels_read":False,"sample_score_values_read":False,
        "removed_or_equivalent_references_read":False,"external_data_or_features_used":False,"leaderboard_feedback_used":False,
        "held_out_threshold_selection":False,"candidate_artifact_modified":False,"candidate_uploads":0,
        "official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "outer_inner_counties":{r:{"outer":outer[r],"inner":inner[r]} for r in REGIONS},"outer_inner_split_sha256":split_hash,
        "defined_support_by_threshold":{"thresholds":{str(t):{r:{"official_ids":len(geoids_by_region[r]),"defined_ids":cache_metadata[str(t)][r]["defined_ids"],
          "defined_fraction":cache_metadata[str(t)][r]["defined_ids"]/len(geoids_by_region[r]),"feature_values_and_mask_sha256":cache_metadata[str(t)][r]["feature_values_and_mask_sha256"]} for r in REGIONS} for t in THRESHOLDS}},
        "all_threshold_feature_values_and_mask_sha256":feature_hashes},
      "inner_selection":inner_result,
      "outer_evaluation":{"selected_threshold":selected,"paired_outer_county_delta_r2":outer_result,"county_bootstrap_95":bootstrap,
        "minimum_regional_delta_r2":minimum_region},
      "acceptance":acceptance,
      "interpretation":"Outer results estimate the selected inner threshold only. If any gate fails, retain T1 as diagnostic-only; this residual association is not target accuracy or evidence for candidate promotion.",
      "provenance":{"T1_source_sha256":sha256(T1_SOURCE),"registered_T1_artifact":{"path":"ideas/T1.parquet","sha256":T1_WORKSPACE_SHA},
        "S1_residual_artifact":residual_meta,"threshold_feature_cache":str(cache_dir),
        "feature_cache_metadata":cache_metadata,"analysis_script_sha256":sha256(Path(__file__).resolve()),
        "frozen_screen_helper_sha256":sha256(ROOT/"scripts/screen_t1-stroke-residual.py"),
        "prior_fixed_threshold_screen_sha256":sha256(ROOT/"reports/t1-stroke-residual-screen.json"),
        "test_file_sha256":sha256(ROOT/"tests/test_t1_outside_share_threshold.py"),
        "harness_config_sha256":sha256(HARNESS_CONFIG),"bootstrap_seed":BOOTSTRAP_SEED}}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",type=Path,default=Path(os.environ["COMPETITION_WORKSPACE"]) if os.environ.get("COMPETITION_WORKSPACE") else None)
    parser.add_argument("--cache-dir",type=Path,default=None)
    parser.add_argument("--build-feature",action="store_true",help="build one threshold/region feature cache only")
    parser.add_argument("--threshold",type=float,choices=THRESHOLDS)
    parser.add_argument("--region",choices=REGIONS)
    parser.add_argument("--out",type=Path,default=ROOT/"reports/t1-outside-share-threshold.json")
    args=parser.parse_args()
    if args.workspace is None or not args.workspace.is_dir():parser.error("workspace does not exist; pass --workspace or set COMPETITION_WORKSPACE")
    cache_dir=(args.cache_dir or args.workspace/"t1-outside-share-threshold-cache").resolve()
    try:cache_dir.relative_to(args.workspace.resolve())
    except ValueError:parser.error("--cache-dir must remain inside COMPETITION_WORKSPACE")
    if args.build_feature:
        if args.threshold is None or args.region is None:parser.error("--build-feature requires --threshold and --region")
        official,by_region=ids_and_regions(args.workspace)
        result=build_feature(args.workspace,cache_dir,args.region,args.threshold,official,by_region)
        print(json.dumps({"region":args.region,"threshold":args.threshold,"defined_ids":result["defined_ids"],
                          "feature_sha256":result["feature_values_and_mask_sha256"],"frozen_0_7_match":result["frozen_0_7_match"]},sort_keys=True))
        return 0
    report=run_screen(args.workspace,cache_dir,args.out)
    print(json.dumps({"selected_inner_threshold":report["inner_selection"]["selected_threshold"],
        "outer_pooled_delta_r2":report["outer_evaluation"]["paired_outer_county_delta_r2"]["pooled"]["delta_r2"],
        "outer_ci_95":report["outer_evaluation"]["county_bootstrap_95"]["pooled_delta_r2_ci_95"],
        "regional_delta_r2":{r:x["delta_r2"] for r,x in report["outer_evaluation"]["paired_outer_county_delta_r2"]["regional"].items()},
        "acceptance":report["acceptance"],"output":str(args.out)},sort_keys=True))
    return 0

if __name__=="__main__":raise SystemExit(main())
