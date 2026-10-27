#!/usr/bin/env python3
"""County-held-out screen of registered T5 building-to-road distance feature vs frozen S1 residual."""
from __future__ import annotations
import argparse,csv,hashlib,io,json,os,sys
from pathlib import Path
from typing import Any
import numpy as np
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa:E402
from audit_mx4p_conditional_innovation import read_emission  # noqa:E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379;EXPECTED_T5_SUPPORT=9377
T5_SHA="feb7af5722fabacf90ed8915c2842ba5c5edddad8bad5d75028277f88af80140"
T5_HARNESS_REPORT=ROOT/"reports/ideas-harness.json"
T5_FAMILY_REPORT=ROOT/"reports/bias60-family-t-summary.json"
T5_SOURCE=ROOT/"src/bias60/ideas/t.py"
T5_SOURCE_SHA="40ff5d36e4787f3d77ba586259d992753d9e21bc6e05e691d40ce6587f0598c2"
T5_CONFIG=ROOT/"config/bias60-harness-preregistered.json"
T5_CONFIG_SHA="12a15e11cd0459299e407e5f1f7056fa16a6a8ddad5d7bfd826b20dbe476d558"
S1_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
S1_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
BOOTSTRAP_REPLICATES=2000;BOOTSTRAP_SEED=20261105
MIN_DELTA_R2=.002;MIN_CI_LOWER=0.;MAX_URBAN_RURAL_DECLINE=.005
AREA_QUANTILE_CUTS=(.25,.5,.75)


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def data_sha256(geoids:list[str],regions:list[str],values:np.ndarray,defined:np.ndarray)->str:
    if not(len(geoids)==len(regions)==len(values)==len(defined)):raise ValueError("feature hash vectors misaligned")
    payload="\n".join(f"{g},{r},{int(ok)},{format(float(v),'.17g') if ok else ''}" for g,r,v,ok in zip(geoids,regions,values,defined))
    return hashlib.sha256(payload.encode()).hexdigest()


def mask_sha256(mask:np.ndarray)->str:
    return hashlib.sha256(np.packbits(np.asarray(mask,dtype=np.uint8),bitorder="little").tobytes()).hexdigest()


def county_fips(geoid:str)->str:
    value=str(geoid)
    if len(value)!=11 or not value.isdigit():raise ValueError("GEOID must contain eleven digits")
    return value[:5]


def fit_one_term(train_y:np.ndarray,train_x:np.ndarray,test_x:np.ndarray)->tuple[np.ndarray,dict[str,Any]]:
    y=np.asarray(train_y,dtype=np.float64);x=np.asarray(train_x,dtype=np.float64);held=np.asarray(test_x,dtype=np.float64)
    if x.ndim!=1 or held.ndim!=1 or y.shape!=x.shape or len(y)<3 or not len(held):raise ValueError("fold needs aligned vectors, >=3 training rows and held-out rows")
    if not(np.isfinite(y).all() and np.isfinite(x).all() and np.isfinite(held).all()):raise ValueError("fold values must be finite")
    mean=float(x.mean());sd=float(x.std(ddof=0));scale=sd if sd>0. else 1.;z=(x-mean)/scale;zh=(held-mean)/scale
    design=np.column_stack((np.ones(len(y)),z));coef,_,rank,_=np.linalg.lstsq(design,y,rcond=None)
    if rank!=2:raise ValueError("one-term training design is rank deficient")
    pred=np.column_stack((np.ones(len(held)),zh))@coef
    return pred,{"training_n":int(len(y)),"heldout_n":int(len(held)),"training_feature_mean":mean,
      "training_feature_sd":sd,"training_applied_scale":scale,"intercept":float(coef[0]),"standardized_slope":float(coef[1]),"design_rank":int(rank)}


def score(y:np.ndarray,pred:np.ndarray)->dict[str,Any]:
    target=np.asarray(y,dtype=np.float64);estimate=np.asarray(pred,dtype=np.float64)
    if target.ndim!=1 or estimate.shape!=target.shape or not len(target) or not(np.isfinite(target).all() and np.isfinite(estimate).all()):raise ValueError("score vectors must align and be finite")
    base=float(target@target);model=float(np.sum((target-estimate)**2))
    if base<=0.:raise ValueError("frozen S1 residual baseline SSE must be positive")
    return {"n":int(len(target)),"frozen_s1_baseline_sse":base,"corrected_sse":model,"delta_r2":(base-model)/base}


def group_summary(y:np.ndarray,pred:np.ndarray,mask:np.ndarray)->dict[str,Any]:
    if not np.any(mask):return {"n":0,"delta_r2":None,"mean_frozen_residual":None,"mean_absolute_frozen_residual":None}
    return {**score(y[mask],pred[mask]),"mean_frozen_residual":float(y[mask].mean()),"mean_absolute_frozen_residual":float(np.abs(y[mask]).mean())}


def county_bootstrap(records:list[dict[str,Any]])->dict[str,Any]:
    groups={r:[] for r in REGIONS}
    for row in records:groups[row["region"]].append(row)
    if any(not rows for rows in groups.values()):raise ValueError("each region requires held-out counties")
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
      "resampling":"paired held-county baseline/model SSE blocks; county sampled with replacement within each official region",
      "pooled_delta_r2_ci_95":ci(pooled),"regional_delta_r2_ci_95":{r:ci(v) for r,v in regional.items()},"degenerate_replicates":int(np.sum(~np.isfinite(pooled)))}


def load_s1(workspace:Path,official:list[tuple[str,str]])->tuple[np.ndarray,dict[str,str]]:
    path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(path)!=S1_SHA:raise ValueError("pinned S1 residual SHA mismatch")
    report=json.loads(S1_REPORT.read_text(encoding="utf-8"))
    if report["provenance"]["output_artifact"]["sha256"]!=S1_SHA:raise ValueError("S1 residual report/artifact hash mismatch")
    table=pq.read_table(path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if table.num_rows!=len(official):raise ValueError("S1 residual row count mismatch")
    positions={pair:i for i,pair in enumerate(official)};seen=set();values=np.full(len(official),np.nan)
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in positions or pair in seen:raise ValueError("unexpected or duplicate S1 residual row")
        if row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:raise ValueError("S1 residual incomplete")
        values[positions[pair]]=float(row["crossfit_residual"]);seen.add(pair)
    if seen!=set(official) or not np.isfinite(values).all():raise ValueError("S1 residual partition/mask mismatch")
    return values,{"path":str(path),"sha256":S1_SHA,"report_sha256":sha256_file(S1_REPORT)}


def load_descriptive_strata(workspace:Path,official:list[tuple[str,str]],config:dict[str,Any])->tuple[np.ndarray,np.ndarray,dict[str,Any]]:
    pct=np.full(len(official),np.nan);area=np.full(len(official),np.nan);files={}
    projector=Transformer.from_crs("EPSG:4326","EPSG:5070",always_xy=True)
    for region in REGIONS:
        idx=[i for i,(_,r) in enumerate(official) if r==region];geos=[official[i][0] for i in idx]
        inputs=ProvidedInputs(config,workspace,region,geos)
        pct[idx]=np.asarray(inputs.strata(["pct_urban"])["pct_urban"],dtype=np.float64)
        polygons=inputs.tract_geometries();metric=shapely.transform(polygons,projector.transform,interleaved=False)
        area[idx]=np.asarray(shapely.area(metric),dtype=np.float64)/1_000_000.
        cache=workspace/config["inputs"]["strata_cache_template"].format(region=region)
        files[region]={"provided_strata_cache":{"path":str(cache),"sha256":sha256_file(cache),"columns_read":["GEOID","pct_urban"]},
          "provided_tract_polygon_columns":["GEOID","geometry"],"tract_ids":len(idx)}
    if np.any(np.isfinite(area)&(area<=0.)) or not np.isfinite(area).all():raise ValueError("provided ACS tract polygon areas must be finite and positive")
    return pct,area,{"urbanity_source":"provided pct_urban; descriptive only","urbanity_cutoff":.5,
      "area_source":"provided ACS tract polygons projected to EPSG:5070; area in km²; used only for descriptive quartiles",
      "urbanity_defined_ids":int(np.isfinite(pct).sum()),"urbanity_values_and_mask_sha256":data_sha256([g for g,_ in official],[r for _,r in official],pct,np.isfinite(pct)),
      "area_values_sha256":data_sha256([g for g,_ in official],[r for _,r in official],area,np.isfinite(area)),"inputs_by_region":files}


def run(workspace:Path,report_path:Path,manifest_path:Path)->dict[str,Any]:
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)];geoids=[g for g,_ in official];regions=np.asarray([r for _,r in official],dtype=object)
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or len(set(geoids))!=EXPECTED_N or set(regions.tolist())!=set(REGIONS):raise ValueError("official scored universe mismatch")
    if sha256_file(T5_SOURCE)!=T5_SOURCE_SHA or sha256_file(T5_CONFIG)!=T5_CONFIG_SHA:raise ValueError("frozen T5 source/config changed")
    config=load_config(T5_CONFIG);seed=config["seeds"]["T5"]
    if seed["building_layer"]!="buildings" or seed["road_layer"]!="roads-unfiltered" or seed["distance_m"]!=150.0:raise ValueError("registered T5 layer or 150m threshold changed")
    feature_path=workspace/"ideas/T5.parquet";feature,defined,t5_sha=read_emission(feature_path,T5_SHA,"T5","C","covariate:buildings_over_150m_from_road",official)
    if int(defined.sum())!=EXPECTED_T5_SUPPORT:raise ValueError("registered T5 support changed")
    if np.any(defined&(~np.isfinite(feature)|(feature<0.)|(feature>1.))):raise ValueError("T5 fractions must be finite and within [0,1]")
    harness=json.loads(T5_HARNESS_REPORT.read_text(encoding="utf-8"));harness_t5=harness["per_idea"]["T5"]
    family=json.loads(T5_FAMILY_REPORT.read_text(encoding="utf-8"));family_t5=family["outputs"]["T5"]
    if harness_t5["sha256"]!=T5_SHA or harness_t5["path"]!="ideas/T5.parquet" or family_t5["sha256"]!=T5_SHA:raise ValueError("T5 registered artifact provenance mismatch")
    emissions=harness_t5["emissions"]
    if emissions!=[{"channel":"covariate:buildings_over_150m_from_road","component":"C","defined_rows":9377,"rows":9379,"value_max":.52,"value_min":0.0}]:
        raise ValueError("T5 registered emission contract changed")
    diagnostics=harness_t5["diagnostics"]
    if set(diagnostics)!=set(REGIONS):raise ValueError("T5 diagnostics missing region")
    for region in REGIONS:
        d=diagnostics[region]
        if d["distance_m"]!=150.0 or d["distance_crs"]!="EPSG:5070" or d["building_assignment"]!="centroid strictly within provided ACS tract" or d["no_target_or_reference_data_read"] is not True or d["microsoft_reference_used"] is not False:
            raise ValueError(f"T5 fixed recipe or prohibition check failed in {region}")
    s1,residual_meta=load_s1(workspace,official);support=defined&np.isfinite(s1)
    if int(support.sum())!=EXPECTED_T5_SUPPORT:raise ValueError("T5/S1 paired support mismatch")
    pct_urban,tract_area,descriptive_meta=load_descriptive_strata(workspace,official,config)
    county=np.asarray([county_fips(g) for g in geoids],dtype=object);prediction=np.full(EXPECTED_N,np.nan);records=[];manifest_rows=[];fold_counts={}
    for region in REGIONS:
        region_mask=support&(regions==region);county_list=sorted(set(county[region_mask].tolist()));fold_counts[region]=len(county_list)
        if len(county_list)<2:raise ValueError(f"insufficient county folds in {region}")
        for held in county_list:
            test=region_mask&(county==held);train=region_mask&(county!=held)
            if not test.any() or train.sum()<3:raise ValueError(f"invalid county fold {region}/{held}")
            pred,fit=fit_one_term(s1[train],feature[train],feature[test]);prediction[test]=pred
            baseline=float(s1[test]@s1[test]);model=float(np.sum((s1[test]-pred)**2));training=sorted(set(county[train].tolist()))
            train_hash=hashlib.sha256("\n".join(training).encode()).hexdigest()
            records.append({"region":region,"heldout_county_fips":held,"heldout_n":int(test.sum()),"training_n":int(train.sum()),
              "training_county_count":len(training),"training_county_fips_sha256":train_hash,"baseline_sse":baseline,"model_sse":model,
              "delta_r2":(baseline-model)/baseline if baseline else None,"fit":fit})
            for i in np.flatnonzero(test):manifest_rows.append((geoids[i],region,held,train_hash,len(training),int(train.sum())))
    if np.any(support&~np.isfinite(prediction)) or np.any(~support&np.isfinite(prediction)):raise AssertionError("OOF prediction support mismatch")
    pooled=score(s1[support],prediction[support]);bootstrap=county_bootstrap(records);regional={}
    for region in REGIONS:
        use=support&(regions==region);v=feature[use]
        regional[region]={**score(s1[use],prediction[use]),"county_count":fold_counts[region],"official_ids":int(np.sum(regions==region)),
          "feature_defined_ids":int(np.sum(defined&(regions==region))),"feature_distribution":{"minimum":float(v.min()),"p25":float(np.quantile(v,.25)),
          "p50":float(np.quantile(v,.5)),"p75":float(np.quantile(v,.75)),"maximum":float(v.max())}}
    rural=np.isfinite(pct_urban)&(pct_urban<.5);urban=np.isfinite(pct_urban)&(pct_urban>=.5)
    urban_rural={"definition":{"rural":"provided pct_urban < 0.5","urban":"provided pct_urban >= 0.5","missing":"excluded"},
      "rural":group_summary(s1,prediction,support&rural),"urban":group_summary(s1,prediction,support&urban),"missing_pct_urban_support_n":int(np.sum(support&~np.isfinite(pct_urban))),"by_region":{}}
    for region in REGIONS:
        mask=regions==region
        urban_rural["by_region"][region]={"rural":group_summary(s1,prediction,support&mask&rural),"urban":group_summary(s1,prediction,support&mask&urban),
          "missing_pct_urban_support_n":int(np.sum(support&mask&~np.isfinite(pct_urban)))}
    area_values=tract_area[support];area_cuts=np.quantile(area_values,AREA_QUANTILE_CUTS,method="linear");area_index=np.digitize(area_values,area_cuts,right=True)
    area_groups={};support_indices=np.flatnonzero(support)
    for idx,label in enumerate(("Q1_smallest_area","Q2","Q3","Q4_largest_area")):
        mask=np.zeros(EXPECTED_N,dtype=bool);mask[support_indices[area_index==idx]]=True
        area_groups[label]={**group_summary(s1,prediction,mask),"area_km2_lower_exclusive_upper_inclusive":
          [None if idx==0 else float(area_cuts[idx-1]),None if idx==3 else float(area_cuts[idx])]}
    positive=sum(regional[r]["delta_r2"]>0. for r in REGIONS);ci=bootstrap["pooled_delta_r2_ci_95"]
    supported_urban_rural={name:entry for name,entry in (("rural",urban_rural["rural"]),("urban",urban_rural["urban"])) if entry["n"]>0}
    largest_ur_decline=max([max(0.,-entry["delta_r2"]) for entry in supported_urban_rural.values() if entry["delta_r2"] is not None],default=0.)
    passed=bool(pooled["delta_r2"]>=MIN_DELTA_R2 and ci[0]>MIN_CI_LOWER and largest_ur_decline<=MAX_URBAN_RURAL_DECLINE)
    buffer=io.StringIO(newline="");writer=csv.writer(buffer,lineterminator="\n")
    writer.writerow(["GEOID","region","heldout_county_fips","training_county_fips_sha256","training_county_count","training_support_n"])
    writer.writerows(sorted(manifest_rows,key=lambda row:(row[1],row[0])));manifest_sha=hashlib.sha256(buffer.getvalue().encode()).hexdigest()
    manifest_path.parent.mkdir(parents=True,exist_ok=True);manifest_path.write_text(buffer.getvalue(),encoding="utf-8")
    feature_hash=data_sha256(geoids,[r for _,r in official],feature,defined);by_region={}
    for region in REGIONS:
        ix=regions==region;by_region[region]={"official_ids":int(ix.sum()),"feature_defined_ids":int(np.sum(defined&ix)),
          "feature_defined_fraction":float(np.sum(defined&ix)/np.sum(ix)),"feature_values_and_mask_sha256":data_sha256([geoids[i] for i in np.flatnonzero(ix)],[region]*int(ix.sum()),feature[ix],defined[ix]),
          "buildings_read":diagnostics[region]["buildings_read"],"roads_read":diagnostics[region]["roads_read"]}
    report={"schema_version":1,"screen":"Registered T5 building-to-road distance county-held-out residual screen; descriptive-only unless fixed gates pass",
      "measure_before":{"T5_registered":True,"feature":"T5|C|covariate:buildings_over_150m_from_road","feature_defined_ids":int(defined.sum()),
        "county_heldout_T5_residual_delta_r2_previously_measured":False,"T5_artifact_sha256":T5_SHA,"fixed_distance_m":150.0,
        "official_ids":EXPECTED_N},
      "method":{"feature":"Use the frozen T5 emission only: fraction of provided Overture building centroids assigned within the ACS tract and more than 150 m from any provided Overture road.",
        "feature_formula":"1 - buildings with nearest provided-road distance <=150m / assigned provided buildings; denominator-zero tracts are undefined. 150m and centroid-within assignment remain fixed.",
        "outcome":"Pinned frozen S1 cross-fitted estimator-disagreement residual; no target or reference layer read.",
        "validation":"Within each official region, hold out each supported five-digit county FIPS in turn; fit on all other supported counties in that region.",
        "correction":"OLS intercept plus one T5 term; feature mean and population SD computed only on training counties; held-out features use training statistics; zero SD scale=1.",
        "baseline":"Zero correction of frozen S1 residual on identical T5-defined support.",
        "metric":"Pooled and regional county-held-out ΔR²=(baseline SSE - OOF model SSE)/baseline SSE; 2,000 paired county-block bootstrap replicates within each region.",
        "rurality_sensitivity":"Descriptive OOF residual fit by provided pct_urban (<0.5 rural, >=0.5 urban); fixed cut, no outcome-based tuning.",
        "area_sensitivity":"Descriptive OOF residual fit across four pooled equal-frequency quartiles of provided ACS tract polygon area in EPSG:5070 (km²); cut points from feature support only, no outcome-based tuning.",
        "registered_feature":"T5|C|covariate:buildings_over_150m_from_road","unregistered_screen":True,
        "interpretation_limit":"Residual association is not target accuracy, causal evidence, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"T5_feature_defined_ids":int(defined.sum()),"paired_residual_support":int(support.sum()),
        "undefined_T5_ids":int((~defined).sum()),"T5_feature_mask_sha256":mask_sha256(defined),"T5_feature_values_and_mask_sha256":feature_hash,
        "T5_defined_ids_by_region":by_region,"frozen_residual_defined_ids":EXPECTED_N,
        "official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,"external_inputs_used":False,
        "removed_or_equivalent_references_read":False,"external_imagery_or_data_read":False,"leaderboard_feedback_used":False,"estimator_integration":False,"uploads":0},
      "measure_after":{"pooled":pooled,"county_block_bootstrap":bootstrap,"regional":regional,"urban_rural_support_and_deltas":urban_rural,
        "tract_area_quartiles":{"cut_method":"pooled paired-support quantiles; linear interpolation; fixed descriptive bins, no tuning","cutpoints_km2":[float(x) for x in area_cuts],"groups":area_groups},
        "county_fold_count":len(records),"county_fold_counts_by_region":fold_counts,"county_folds":records,
        "fold_manifest":{"path":str(manifest_path),"sha256":manifest_sha,"rows":len(manifest_rows)}},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_DELTA_R2,"bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,
        "maximum_supported_urban_or_rural_stratum_decline_inclusive":MAX_URBAN_RURAL_DECLINE},
      "acceptance":{"pooled_delta_r2_at_least_0_002":bool(pooled["delta_r2"]>=MIN_DELTA_R2),"county_block_ci_lower_above_zero":bool(ci[0]>MIN_CI_LOWER),
        "supported_urban_rural_delta_r2":{k:v["delta_r2"] for k,v in supported_urban_rural.items()},"largest_supported_urban_rural_decline":largest_ur_decline,
        "no_supported_urban_rural_stratum_decline_gt_0_005":bool(largest_ur_decline<=MAX_URBAN_RURAL_DECLINE),"screen_pass":passed},
      "provenance":{"T5_artifact":{"path":str(feature_path),"sha256":t5_sha},"T5_harness_report":{"path":str(T5_HARNESS_REPORT),"sha256":sha256_file(T5_HARNESS_REPORT)},
        "T5_family_summary":{"path":str(T5_FAMILY_REPORT),"sha256":sha256_file(T5_FAMILY_REPORT)},"T5_source_definition":{"path":str(T5_SOURCE),"sha256":sha256_file(T5_SOURCE)},
        "T5_registered_config":{"path":str(T5_CONFIG),"sha256":sha256_file(T5_CONFIG)},"frozen_S1_residual":residual_meta,
        "descriptive_rurality_and_area_inputs":descriptive_meta,"analysis_script_sha256":sha256_file(Path(__file__).resolve()),
        "bootstrap_seed":BOOTSTRAP_SEED,"bootstrap_replicates":BOOTSTRAP_REPLICATES},
      "interpretation":"The T5 feature failed one or more fixed pooled/CI/urban-rural gates; keep descriptive-only and do not integrate." if not passed else
        "The T5 feature passed fixed residual-association gates; this does not establish target accuracy or authorize integration."}
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/t5-building-road-residual-screen.json");parser.add_argument("--manifest",default="reports/t5-building-road-residual-county-folds.csv")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out,ROOT/args.manifest)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],
      "pooled_ci":report["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"],
      "regional":report["measure_after"]["regional"],"urban_rural":report["measure_after"]["urban_rural_support_and_deltas"],
      "area_groups":report["measure_after"]["tract_area_quartiles"],"acceptance":report["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
