#!/usr/bin/env python3
"""County-held-out screen of G5 with fixed half-weight on qualifying stacks overlapping commercial footprints."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,sys
from pathlib import Path
from typing import Any
import numpy as np
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa:E402

HELPER_PATH=ROOT/"scripts/screen_g5-commercial-stack-filter.py"
spec=importlib.util.spec_from_file_location("g5_commercial_stack_filter_helpers",HELPER_PATH)
if spec is None or spec.loader is None:raise RuntimeError("cannot load frozen G5 filter helpers")
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
REGIONS=helpers.REGIONS;EXPECTED_N=helpers.EXPECTED_N;BOOTSTRAP_REPLICATES=helpers.BOOTSTRAP_REPLICATES;BOOTSTRAP_SEED=helpers.BOOTSTRAP_SEED
EXPECTED_G5_SHA=helpers.EXPECTED_G5_SHA;EXPECTED_S1_SHA=helpers.EXPECTED_S1_SHA
G5_FILTER_REPORT=ROOT/"reports/g5-commercial-stack-filter-screen.json"
G5_FILTER_SCRIPT=HELPER_PATH;G5_EMISSION=ROOT/"reports/g-family-screen.json"
G5_HARNESS=ROOT/"reports/ideas-harness.json";G_SOURCE=ROOT/"src/bias60/ideas/g.py"
CONFIG=ROOT/"config/bias60-harness-preregistered.json"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
MIN_DELTA_R2=.002;MIN_CI_LOWER=0.;MIN_EFFECTIVE_STACK_FRACTION=.5


def sha256_file(path:Path)->str:return helpers.sha256_file(path)


def overlap_stack_component(stacks:dict,covered_coordinates:set[tuple[float,float]],n_tracts:int)->dict[str,Any]:
    """Return qualifying G5 stack-point counts attenuable by the overlap rule."""
    counts=np.zeros(n_tracts,dtype=np.float64);all_qualifying=overlapping=all_stack_points=overlap_stack_points=0
    for coord,members in stacks.items():
        if any(i<0 or i>=n_tracts for i,_ in members):raise ValueError("stack membership tract index out of range")
        if helpers.qualifying_name_count(members)<5:continue
        all_qualifying+=1;all_stack_points+=len(members)
        if coord not in covered_coordinates:continue
        overlapping+=1;overlap_stack_points+=len(members)
        counts+=np.bincount([i for i,_ in members],minlength=n_tracts)
    return {"overlapping_stacked_place_counts":counts,"qualifying_stack_count":all_qualifying,
      "overlapping_qualifying_stack_count":overlapping,"qualifying_stack_place_count":all_stack_points,
      "overlapping_qualifying_stack_place_count":overlap_stack_points}


def soft_feature(unfiltered:np.ndarray,overlap_stack_counts:np.ndarray,place_counts:np.ndarray,attenuation:float=.5)->tuple[np.ndarray,np.ndarray,np.ndarray]:
    base=np.asarray(unfiltered,dtype=np.float64);overlap=np.asarray(overlap_stack_counts,dtype=np.float64);places=np.asarray(place_counts,dtype=np.float64)
    if base.ndim!=1 or overlap.shape!=base.shape or places.shape!=base.shape or not 0<=attenuation<=1:raise ValueError("invalid soft feature inputs")
    defined=np.isfinite(base)&(places>0)
    component=np.zeros(base.shape,dtype=np.float64);component[defined]=overlap[defined]/places[defined]
    if np.any(component< -1e-12) or np.any(component-base>1e-12):raise ValueError("overlap component is outside the unfiltered G5 feature")
    result=np.full(base.shape,np.nan);result[defined]=base[defined]-attenuation*component[defined]
    if np.any(result[defined]<-1e-12) or np.any(result[defined]>1+1e-12):raise ValueError("soft G5 values outside [0,1]")
    result[defined]=np.clip(result[defined],0.,1.)
    return result,component,defined


def bootstrap(records:list[dict[str,Any]],replicates:int=BOOTSTRAP_REPLICATES,seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    groups={r:[] for r in REGIONS}
    for row in records:groups[row["region"]].append(row)
    if any(not x for x in groups.values()):raise ValueError("all regions require county folds")
    rng=np.random.default_rng(seed);metrics={"unfiltered":np.empty(replicates),"soft":np.empty(replicates),"soft_minus_unfiltered":np.empty(replicates)}
    regional={m:{r:np.empty(replicates) for r in REGIONS} for m in metrics}
    def delta(base:float,model:float)->float:return (base-model)/base if base>0 else float("nan")
    for b in range(replicates):
        totals={name:[0.,0.] for name in ("unfiltered","soft")};by_region={name:{r:[0.,0.] for r in REGIONS} for name in totals}
        for region in REGIONS:
            rows=groups[region];draw=rng.integers(0,len(rows),size=len(rows))
            for j in draw:
                row=rows[int(j)]
                for name in totals:
                    totals[name][0]+=row["baseline_sse"];totals[name][1]+=row[f"{name}_sse"]
                    by_region[name][region][0]+=row["baseline_sse"];by_region[name][region][1]+=row[f"{name}_sse"]
        for name in totals:metrics[name][b]=delta(*totals[name])
        metrics["soft_minus_unfiltered"][b]=metrics["soft"][b]-metrics["unfiltered"][b]
        for name in totals:
            for region in REGIONS:regional[name][region][b]=delta(*by_region[name][region])
        for region in REGIONS:regional["soft_minus_unfiltered"][region][b]=regional["soft"][region][b]-regional["unfiltered"][region][b]
    def ci(values:np.ndarray)->list[float]:
        good=values[np.isfinite(values)]
        if len(good)<int(.99*replicates):raise ValueError("too many degenerate county bootstrap replicates")
        return [float(x) for x in np.quantile(good,[.025,.975])]
    return {"replicates":replicates,"seed":seed,"resampling":"paired held-out county SSE blocks sampled with replacement within each official region",
      "pooled_ci_95":{name:ci(v) for name,v in metrics.items()},"regional_ci_95":{name:{r:ci(v) for r,v in values.items()} for name,values in regional.items()}}


def run(workspace:Path,out:Path)->dict[str,Any]:
    if sha256_file(G5_FILTER_REPORT)!="d6b53234890b30c8af401b349d7b3a78814551561bb6644d1394c0bd58d6eda1":raise ValueError("hard-filter predecessor report changed")
    prior=json.loads(G5_FILTER_REPORT.read_text(encoding="utf-8"))
    if prior["scope"]["paired_support_mask_sha256"]!="3f2c3b32c223690813bd0a9d510e9f89a747096ea73933adad1bfaf24f020578" or prior["measure_after"]["fold_manifest"]["sha256"]!="7e3b7fc4ca2801ecd4748d5255c14a34e96092bc4cea621fcbf1e7750bd21313":
        raise ValueError("previous G5 screen support/fold contract changed")
    if sha256_file(G5_FILTER_SCRIPT)!="4c02e4a0b80b124f71f0db71fcd790fc27a9e6821a008a8160adb59194311834":raise ValueError("frozen G5 filter helper source changed")
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):raise ValueError("official scored tract partition mismatch")
    geoids=np.asarray([g for g,_ in official],dtype=object);regions=np.asarray([r for _,r in official],dtype=object);pos={p:i for i,p in enumerate(official)}
    residual_path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(residual_path)!=EXPECTED_S1_SHA:raise ValueError("frozen S1 residual artifact hash mismatch")
    tab=pq.read_table(residual_path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if tab.num_rows!=EXPECTED_N:raise ValueError("frozen residual row count mismatch")
    residual=np.full(EXPECTED_N,np.nan);seen=set()
    for row in tab.to_pylist():
        key=(str(row["GEOID"]),str(row["region"]))
        if key not in pos or key in seen or row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:raise ValueError("invalid frozen residual row")
        residual[pos[key]]=float(row["crossfit_residual"]);seen.add(key)
    if seen!=set(official) or not np.isfinite(residual).all():raise ValueError("frozen residual alignment failed")
    config=load_config(CONFIG);g5_path=workspace/"ideas/G5.parquet"
    if sha256_file(g5_path)!=EXPECTED_G5_SHA:raise ValueError("workspace G5 feature artifact hash mismatch")
    table=pq.read_table(g5_path,columns=["GEOID","region","idea","component","channel","value","defined"])
    unfiltered=np.full(EXPECTED_N,np.nan);unfiltered_mask=np.zeros(EXPECTED_N,dtype=bool)
    for row in table.to_pylist():
        if row["idea"]!="G5" or row["component"]!="Est" or row["channel"]!="covariate":continue
        key=(str(row["GEOID"]),str(row["region"]))
        if key not in pos or unfiltered_mask[pos[key]]:raise ValueError("duplicate or unexpected G5 feature ID")
        i=pos[key];unfiltered[i]=float(row["value"]);unfiltered_mask[i]=bool(row["defined"])
    if unfiltered_mask.sum()!=prior["scope"]["unfiltered_feature_defined"] or np.any(unfiltered_mask&~np.isfinite(unfiltered)):raise ValueError("G5 baseline feature support invalid")
    regional_data={};poi_meta={};building_meta={};all_polygons=[];stacks_by_region={}
    for region in REGIONS:
        idx=[i for i,(g,r) in enumerate(official) if r==region];inputs=ProvidedInputs(config,workspace,region,[official[i][0] for i in idx])
        stacks,place_counts,pmeta=helpers.load_g5_stacks(inputs);base=helpers.g5_stack_features(stacks,len(idx))
        if not np.array_equal(base["place_counts"],place_counts):raise ValueError(f"G5 place-count vectors disagree in {region}")
        if not np.array_equal(base["defined"],unfiltered_mask[idx]) or not np.allclose(base["feature"][base["defined"]],unfiltered[idx][base["defined"]],rtol=0,atol=1e-14):raise ValueError(f"recomputed G5 differs from frozen artifact in {region}")
        polygons,bmeta=helpers.large_commercial_polygons(inputs);all_polygons.extend(polygons)
        stacks_by_region[region]={"indices":idx,"stacks":stacks,"base":base,"place_counts":place_counts}
        poi_meta[region]=pmeta;building_meta[region]=bmeta
    all_coords=list(dict.fromkeys(coord for region in REGIONS for coord in stacks_by_region[region]["stacks"]))
    coords=np.asarray(all_coords,dtype=np.float64).reshape((-1,2));coverage=helpers.points_covered_by_footprints(coords,all_polygons)
    covered={coord for coord,flag in zip(all_coords,coverage) if flag}
    soft=np.full(EXPECTED_N,np.nan);overlap_component=np.full(EXPECTED_N,np.nan);soft_mask=np.zeros(EXPECTED_N,dtype=bool)
    counts={"all_eligible_points":0,"overlapping_eligible_points":0,"all_qualifying_stacks":0,"overlapping_qualifying_stacks":0,
      "all_qualifying_stack_points":0,"overlapping_qualifying_stack_points":0}
    region_support={}
    for region in REGIONS:
        entry=stacks_by_region[region];idx=entry["indices"];stacks=entry["stacks"];base=entry["base"]
        overlap=overlap_stack_component(stacks,covered,len(idx));local_soft,local_overlap,mask=soft_feature(base["feature"],overlap["overlapping_stacked_place_counts"],entry["place_counts"])
        if not np.array_equal(mask,base["defined"]):raise ValueError("soft and unfiltered support masks differ")
        soft[idx]=local_soft;overlap_component[idx]=local_overlap;soft_mask[idx]=mask
        all_points=sum(len(members) for members in stacks.values());overlap_points=sum(len(stacks[coord]) for coord in stacks if coord in covered)
        all_q_points=overlap["qualifying_stack_place_count"];overlap_q_points=overlap["overlapping_qualifying_stack_place_count"]
        counts["all_eligible_points"]+=all_points;counts["overlapping_eligible_points"]+=overlap_points
        counts["all_qualifying_stacks"]+=overlap["qualifying_stack_count"];counts["overlapping_qualifying_stacks"]+=overlap["overlapping_qualifying_stack_count"]
        counts["all_qualifying_stack_points"]+=all_q_points;counts["overlapping_qualifying_stack_points"]+=overlap_q_points
        nstacks=overlap["qualifying_stack_count"];ncovered=overlap["overlapping_qualifying_stack_count"]
        effective_stack_fraction=(nstacks-.5*ncovered)/nstacks if nstacks else None
        region_support[region]={"official_ids":len(idx),"G5_defined":int(mask.sum()),"eligible_place_points":all_points,
          "overlapping_place_points":overlap_points,"overlap_fraction_of_all_eligible_places":overlap_points/all_points if all_points else None,
          "qualifying_g5_stacks":nstacks,"overlapping_qualifying_stacks":ncovered,
          "overlap_fraction_of_qualifying_stacks":ncovered/nstacks if nstacks else None,
          "qualifying_stack_place_count":all_q_points,"overlapping_qualifying_stack_place_count":overlap_q_points,
          "overlap_fraction_of_qualifying_stack_place_mass":overlap_q_points/all_q_points if all_q_points else None,
          "effective_stack_count_at_half_weight":nstacks-.5*ncovered,"effective_stack_retained_fraction":effective_stack_fraction,
          "soft_feature_distribution":{"n":int(mask.sum()),"mean":float(local_soft[mask].mean()) if mask.any() else None,
            "p90":float(np.quantile(local_soft[mask],.9)) if mask.any() else None},
          "overlap_component_distribution":{"mean":float(local_overlap[mask].mean()) if mask.any() else None,
            "p90":float(np.quantile(local_overlap[mask],.9)) if mask.any() else None}}
    if counts["all_eligible_points"]!=prior["scope"]["all_eligible_place_points"] or counts["overlapping_eligible_points"]!=prior["scope"]["removed_place_point_count"]:
        raise ValueError("provided POI overlap support differs from frozen hard-filter screen")
    if counts["all_qualifying_stacks"]!=prior["scope"]["qualifying_unfiltered_stack_count"] or counts["overlapping_qualifying_stacks"]!=prior["scope"]["removed_qualifying_stack_count"]:
        raise ValueError("qualifying stack overlap differs from frozen hard-filter screen")
    paired=unfiltered_mask&soft_mask&np.isfinite(residual)
    if not np.array_equal(unfiltered_mask,soft_mask) or paired.sum()!=prior["scope"]["paired_feature_residual_support"]:raise ValueError("soft G5 support differs from exact baseline support")
    county=np.asarray([g[:5] for g in geoids],dtype=object);unfiltered_pred=np.full(EXPECTED_N,np.nan);soft_pred=np.full(EXPECTED_N,np.nan);records=[];manifest=[]
    for region in REGIONS:
        regional=paired&(regions==region);counties=sorted(set(county[regional].tolist()))
        for held in counties:
            test=regional&(county==held);train=regional&(county!=held)
            if test.sum()<1 or train.sum()<3:raise ValueError(f"invalid county fold {region}/{held}")
            pu,_=helpers.fit_one_term(residual[train],unfiltered[train],unfiltered[test]);ps,fit=helpers.fit_one_term(residual[train],soft[train],soft[test])
            unfiltered_pred[test]=pu;soft_pred[test]=ps;base_sse=float(residual[test]@residual[test])
            us=float(np.sum((residual[test]-pu)**2));ss=float(np.sum((residual[test]-ps)**2))
            training=sorted(set(county[train&(regions==region)].tolist()));train_hash=hashlib.sha256("\n".join(training).encode()).hexdigest()
            records.append({"region":region,"heldout_county_fips":str(held),"heldout_n":int(test.sum()),"training_n":int(train.sum()),
              "training_county_count":len(training),"training_county_fips_sha256":train_hash,"baseline_sse":base_sse,
              "unfiltered_sse":us,"soft_sse":ss,"unfiltered_delta_r2":(base_sse-us)/base_sse if base_sse else None,
              "soft_delta_r2":(base_sse-ss)/base_sse if base_sse else None,"soft_fit":fit})
            for i in np.flatnonzero(test):manifest.append(f"{geoids[i]},{region},{held},{train_hash},{len(training)},{int(train.sum())}\n")
    fold_sha=hashlib.sha256("".join(sorted(manifest)).encode()).hexdigest()
    if fold_sha!=prior["measure_after"]["fold_manifest"]["sha256"]:raise ValueError("county fold manifest differs from registered G5 hard-filter screen")
    baseline_sse=float(residual[paired]@residual[paired]);unfiltered_sse=float(np.sum((residual[paired]-unfiltered_pred[paired])**2));soft_sse=float(np.sum((residual[paired]-soft_pred[paired])**2))
    unfiltered_metric={"n":int(paired.sum()),"baseline_sse":baseline_sse,"model_sse":unfiltered_sse,"delta_r2":(baseline_sse-unfiltered_sse)/baseline_sse}
    soft_metric={"n":int(paired.sum()),"baseline_sse":baseline_sse,"model_sse":soft_sse,"delta_r2":(baseline_sse-soft_sse)/baseline_sse}
    boot=bootstrap(records);soft_ci=boot["pooled_ci_95"]["soft"]
    retained=(counts["all_qualifying_stacks"]-.5*counts["overlapping_qualifying_stacks"])/counts["all_qualifying_stacks"] if counts["all_qualifying_stacks"] else None
    passed=bool(soft_metric["delta_r2"]>=MIN_DELTA_R2 and soft_ci[0]>MIN_CI_LOWER and retained is not None and retained>=MIN_EFFECTIVE_STACK_FRACTION)
    report={"schema_version":1,"screen":"G5 exact-coordinate collision feature with fixed half-weight attenuation for place stacks overlapping large commercial footprints; descriptive-only",
      "measure_before":{"G5_unfiltered_delta_r2":prior["measure_after"]["unfiltered_pooled"]["delta_r2"],
        "hard_filter_delta_r2":prior["measure_after"]["filtered_pooled"]["delta_r2"],"hard_filter_pooled_ci_95":prior["measure_after"]["paired_county_bootstrap"]["pooled_ci_95"]["filtered"],
        "hard_filter_retained_stack_fraction":prior["scope"]["retained_qualifying_stack_fraction"],"hard_filter_report_sha256":sha256_file(G5_FILTER_REPORT)},
      "method":{"outcome":"Frozen S1 orthogonalized establishment/facility disagreement cross-fit residual; no labels or refit.",
        "G5_definition":"Exact source lon/lat coordinate stack, at least five distinct stripped/casefolded nonempty primary names, stacked place count in tract divided by all eligible assigned place count.",
        "soft_definition":"G5_soft = G5_unfiltered - 0.5 * G5_overlapping_large_footprints. The overlap component is qualifying-stack POI count inside a qualifying footprint divided by all eligible assigned POI count; its denominator is unchanged. A stack is attenuated as a whole because its members share its exact coordinate.",
        "footprint_rule":"Reuse the hard-filter screen's exact provided-only OSM/Esri-exclusive building source guard, whole-word commercial/industrial/warehouse/retail/shopping/mall class tokens, EPSG:5070 area >=5000 m2, and covered_by point predicate; no buffers or tuning.",
        "fit":"One-term OLS correction to frozen S1 residual; leave-one-five-digit-county-out separately within each official region; training-only feature mean and population SD; zero SD scale=1.",
        "bootstrap":"2000 paired county-block resamples within region, same seed and held-county SSE blocks as G5 hard-filter screen.",
        "effective_stack_retention":"(all qualifying stacks - 0.5 * overlapping qualifying stacks) / all qualifying stacks; each overlapping stack retained at half weight, not deleted.",
        "interpretation_limit":"Provided-feature residual association only; not target accuracy, causality, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "paired_feature_residual_support":int(paired.sum()),"G5_unfiltered_defined":int(unfiltered_mask.sum()),"G5_soft_defined":int(soft_mask.sum()),
        "overlap_component_values_and_mask_sha256":helpers._hash_vector(geoids.tolist(),regions.tolist(),overlap_component,soft_mask),
        "soft_feature_values_and_mask_sha256":helpers._hash_vector(geoids.tolist(),regions.tolist(),soft,soft_mask),
        "unfiltered_feature_values_and_mask_sha256":helpers._hash_vector(geoids.tolist(),regions.tolist(),unfiltered,unfiltered_mask),
        "overlapping_unique_coordinates":int(len(covered)),"overlapping_eligible_place_points":counts["overlapping_eligible_points"],
        "all_eligible_place_points":counts["all_eligible_points"],"overlap_fraction_of_all_eligible_points":counts["overlapping_eligible_points"]/counts["all_eligible_points"],
        "qualifying_g5_stacks":counts["all_qualifying_stacks"],"overlapping_qualifying_stacks":counts["overlapping_qualifying_stacks"],
        "effective_stack_count_at_half_weight":counts["all_qualifying_stacks"]-.5*counts["overlapping_qualifying_stacks"],
        "effective_stack_retained_fraction":retained,"qualifying_stack_place_count":counts["all_qualifying_stack_points"],
        "overlapping_qualifying_stack_place_count":counts["overlapping_qualifying_stack_points"],"region_support":region_support,
        "labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,"external_inputs_used":False,
        "removed_or_equivalent_reference_sources_used":False,"leaderboard_feedback_used":False,"uploads":0,"estimator_or_registry_changed":False},
      "measure_after":{"unfiltered_pooled":unfiltered_metric,"soft_pooled":soft_metric,
        "soft_minus_unfiltered_delta_r2":soft_metric["delta_r2"]-unfiltered_metric["delta_r2"],"paired_county_bootstrap":boot,
        "regional":{},"fold_count":len(records),"fold_manifest":{"rows":int(paired.sum()),"sha256":fold_sha,
          "columns":["GEOID","region","heldout_county_fips","training_county_fips_sha256","training_county_count","training_support_n"]},"county_folds":records},
      "thresholds":{"soft_delta_r2_minimum_inclusive":MIN_DELTA_R2,"soft_bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,
        "minimum_effective_stack_retained_fraction":MIN_EFFECTIVE_STACK_FRACTION},
      "acceptance":{"soft_delta_r2_at_least_0_002":bool(soft_metric["delta_r2"]>=MIN_DELTA_R2),
        "soft_county_ci_lower_above_zero":bool(soft_ci[0]>MIN_CI_LOWER),
        "at_least_half_effective_stacks_retained":bool(retained is not None and retained>=MIN_EFFECTIVE_STACK_FRACTION),"screen_pass":passed},
      "provenance":{"G5_emission":{"path":str(g5_path),"sha256":EXPECTED_G5_SHA},
        "G5_definition_source":{"path":str(G_SOURCE),"sha256":sha256_file(G_SOURCE)},
        "hard_filter_helper":{"path":str(G5_FILTER_SCRIPT),"sha256":sha256_file(G5_FILTER_SCRIPT)},
        "hard_filter_report":{"path":str(G5_FILTER_REPORT),"sha256":sha256_file(G5_FILTER_REPORT)},
        "frozen_S1_residual":{"path":str(residual_path),"sha256":EXPECTED_S1_SHA,"report_sha256":sha256_file(RESIDUAL_REPORT)},
        "provided_POI_inputs_by_region":poi_meta,"provided_building_inputs_by_region":building_meta,
        "analysis_script_sha256":sha256_file(Path(__file__).resolve()),"bootstrap_seed":BOOTSTRAP_SEED,"bootstrap_replicates":BOOTSTRAP_REPLICATES},
      "interpretation":"Fixed half-weight attenuation failed one or more preregistered gates; retain the original G5 feature as descriptive-only and do not integrate." if not passed else
        "The soft G5 screen met all preregistered diagnostic gates; this does not itself authorize estimator integration."}
    for region in REGIONS:
        use=paired&(regions==region);base=float(residual[use]@residual[use]);us=float(np.sum((residual[use]-unfiltered_pred[use])**2));ss=float(np.sum((residual[use]-soft_pred[use])**2))
        support=region_support[region]
        report["measure_after"]["regional"][region]={"support_ids":int(use.sum()),"county_count":len(set(county[use].tolist())),
          "unfiltered_delta_r2":(base-us)/base if base else None,"soft_delta_r2":(base-ss)/base if base else None,
          "soft_minus_unfiltered_delta_r2":(us-ss)/base if base else None,
          "county_block_ci_95":{"unfiltered":boot["regional_ci_95"]["unfiltered"][region],"soft":boot["regional_ci_95"]["soft"][region],
            "soft_minus_unfiltered":boot["regional_ci_95"]["soft_minus_unfiltered"][region]},
          "overlap_fraction_of_eligible_places":support["overlap_fraction_of_all_eligible_places"],
          "overlap_fraction_of_qualifying_stacks":support["overlap_fraction_of_qualifying_stacks"],
          "effective_stack_retained_fraction":support["effective_stack_retained_fraction"]}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""));p.add_argument("--out",default="reports/g5-commercial-stack-soft-attenuation.json")
    args=p.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():p.error("COMPETITION_WORKSPACE must be an existing directory")
    r=run(workspace,ROOT/args.out)
    print(json.dumps({"soft_pooled":r["measure_after"]["soft_pooled"],"soft_ci":r["measure_after"]["paired_county_bootstrap"]["pooled_ci_95"]["soft"],
      "overlap_fraction":r["scope"]["overlap_fraction_of_all_eligible_points"],"effective_stack_retained_fraction":r["scope"]["effective_stack_retained_fraction"],
      "acceptance":r["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
