#!/usr/bin/env python3
"""County-held-out screen of length-weighted p90 supplied OSM way versions on named roads."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa: E402
from audit_mx4p_conditional_innovation import read_emission  # noqa: E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379;EXPECTED_FEATURE_N=9247
M1_SHA="ddc48fc70558cb920659484b870819a9c4be43f3488e7fe3f59eed148b6e9f37"
M1_CHANNEL="covariate:named_mean_version";M1_COMPONENT="R"
M1_REPORT=ROOT/"reports/m1-named-road-mean-version-residual.json"
M1_FAMILY_REPORT=ROOT/"reports/bias60-family-m-summary.json"
M1_SOURCE=ROOT/"src/bias60/ideas/m.py"
M1_HARNESS=ROOT/"reports/ideas-harness.json"
RESIDUAL_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
CONFIG_PATH=ROOT/"config/bias60-harness-preregistered.json"
VERSION_PATTERN=re.compile(r"^w\d+@(\d+)$",re.IGNORECASE)
NAMED_CLASSES=frozenset({"motorway","trunk","primary","secondary"})
TRANSFORMER=Transformer.from_crs("EPSG:4326","EPSG:5070",always_xy=True)
BOOTSTRAP_REPLICATES=2000;BOOTSTRAP_SEED=20261102
MIN_DELTA_R2=.002;MIN_CI_LOWER=0.;MIN_POSITIVE_REGIONS=3


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def county_fips(geoid:str)->str:
    value=str(geoid)
    if len(value)!=11 or not value.isdigit():raise ValueError(f"GEOID must be 11 digits: {value!r}")
    return value[:5]


def data_sha256(geoids:list[str],regions:list[str],values:np.ndarray,defined:np.ndarray)->str:
    if not(len(geoids)==len(regions)==len(values)==len(defined)):raise ValueError("feature hash vectors misaligned")
    rows=(f"{g},{r},{int(ok)},{format(float(v),'.17g') if ok else ''}" for g,r,v,ok in zip(geoids,regions,values,defined))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def mask_sha256(mask:np.ndarray)->str:
    return hashlib.sha256(np.packbits(np.asarray(mask,dtype=np.uint8),bitorder="little").tobytes()).hexdigest()


def source_versions(sources:Any)->tuple[tuple[int,...],int,int]:
    versions=set();osm_ids=non_way=0
    for source in sources or []:
        if not isinstance(source,dict) or str(source.get("dataset") or "").casefold()!="openstreetmap":continue
        record_id=str(source.get("record_id") or "")
        if not record_id:continue
        osm_ids+=1;match=VERSION_PATTERN.fullmatch(record_id)
        if match:versions.add(int(match.group(1)))
        else:non_way+=1
    return tuple(sorted(versions)),osm_ids,non_way


def version_for_sources(sources:Any)->tuple[float|None,int,int]:
    versions,osm_ids,non_way=source_versions(sources)
    return (float(np.mean(versions)) if versions else None),osm_ids,non_way


def weighted_quantile(values:np.ndarray,weights:np.ndarray,q:float)->float:
    v=np.asarray(values,dtype=np.float64);w=np.asarray(weights,dtype=np.float64)
    if v.ndim!=1 or w.shape!=v.shape or not len(v) or not np.isfinite(v).all() or not np.isfinite(w).all():
        raise ValueError("weighted quantile inputs must be aligned finite nonempty vectors")
    if not 0.<q<=1. or np.any(w<=0.):raise ValueError("quantile requires 0<q<=1 and strictly positive weights")
    order=np.argsort(v,kind="mergesort");sv=v[order];sw=w[order];threshold=q*float(sw.sum())
    index=int(np.searchsorted(np.cumsum(sw),threshold,side="left"))
    return float(sv[min(index,len(sv)-1)])


def p90_by_tract(distributions:list[list[tuple[float,float]]])->tuple[np.ndarray,np.ndarray,np.ndarray]:
    values=np.full(len(distributions),np.nan,dtype=np.float64);defined=np.zeros(len(distributions),dtype=bool);total_length=np.zeros(len(distributions),dtype=np.float64)
    for i,items in enumerate(distributions):
        if not items:continue
        versions=np.asarray([v for v,_ in items],dtype=np.float64);lengths=np.asarray([w for _,w in items],dtype=np.float64)
        if np.any(lengths<=0.) or not np.isfinite(lengths).all():raise ValueError("clipped road lengths must be finite and positive")
        values[i]=weighted_quantile(versions,lengths,.9);defined[i]=True;total_length[i]=float(lengths.sum())
    return values,defined,total_length


def aggregate_named_road_p90(inputs:ProvidedInputs)->tuple[np.ndarray,np.ndarray,np.ndarray,dict[str,Any]]:
    n=len(inputs.geoids);tracts=shapely.transform(inputs.tract_geometries(),TRANSFORMER.transform,interleaved=False);tree=shapely.STRtree(tracts)
    distributions:list[list[tuple[float,float]]]=[[] for _ in range(n)]
    rows_scanned=rows_named=rows_with_osm_ids=rows_non_way_ids=rows_with_versions=rows_without_geometry=0
    selected_hash=hashlib.sha256();token_digest=hashlib.sha256();token_count=0
    for batch in inputs.iter_layer_batches("roads",["class","sources","geometry"]):
        classes=batch["class"].to_pylist();sources=batch["sources"].to_pylist();wkbs=batch["geometry"].to_pylist();rows_scanned+=len(classes)
        selected_indices=[];selected_versions=[]
        for index,klass in enumerate(classes):
            if str(klass or "").casefold() not in NAMED_CLASSES:continue
            rows_named+=1;version,osm_count,non_way=version_for_sources(sources[index]);rows_with_osm_ids+=osm_count;rows_non_way_ids+=non_way
            if version is None:continue
            if wkbs[index] is None:rows_without_geometry+=1;continue
            rows_with_versions+=1;selected_indices.append(index);selected_versions.append(version)
            versions,_,_=source_versions(sources[index])
            for token in versions:
                payload=f"{token}\n".encode();token_digest.update(len(payload).to_bytes(8,"big"));token_digest.update(payload);token_count+=1
            selected_hash.update(str(klass).casefold().encode());selected_hash.update(b"\0")
            selected_hash.update(",".join(str(x) for x in versions).encode());selected_hash.update(b"\0")
            selected_hash.update(wkbs[index])
        # Bound geometry materialization while keeping source/version alignment deterministic.
        for start in range(0,len(selected_indices),40000):
            indices=selected_indices[start:start+40000]
            if not indices:continue
            lines=shapely.from_wkb([wkbs[i] for i in indices]);lines=shapely.transform(lines,TRANSFORMER.transform,interleaved=False)
            pairs=tree.query(lines,predicate="intersects")
            if not pairs.size:continue
            pieces=shapely.intersection(lines[pairs[0]],tracts[pairs[1]]);lengths=shapely.length(pieces)
            for line_index,tract_index,length in zip(pairs[0].tolist(),pairs[1].tolist(),lengths.tolist()):
                if not np.isfinite(length) or length<=0.:continue
                distributions[tract_index].append((selected_versions[start+line_index],float(length)))
    values,defined,total_length=p90_by_tract(distributions)
    meta={"provided_layer":"roads","columns_read":["class","sources","geometry"],"rows_scanned":rows_scanned,
      "named_class_rows":rows_named,"named_class_OSM_record_id_tokens":rows_with_osm_ids,"matching_way_version_tokens":token_count,
      "named_class_rows_with_at_least_one_matching_version":rows_with_versions,"nonmatching_nonempty_OSM_record_ids":rows_non_way_ids,
      "versioned_named_rows_without_geometry":rows_without_geometry,"named_class_versioned_road_length_m":float(total_length.sum()),
      "tracts_with_positive_versioned_named_road_length":int(defined.sum()),"feature":"length-weighted empirical p90 of per-segment mean unique supplied OSM way versions",
      "version_parser":"OpenStreetMap source record_id fullmatch ^w\\d+@(\\d+)$, case-insensitive; only supplied record_id values are parsed",
      "multiple_versions_per_geometry":"deduplicate integer way versions and use their arithmetic mean, matching M1 named_mean_version input parsing",
      "geometry_assignment":"transform supplied line geometries and official tract polygons to EPSG:5070; intersect and weight by clipped length",
      "selected_versioned_named_segments_sha256":selected_hash.hexdigest(),"parsed_version_token_multiset_sha256":token_digest.hexdigest()}
    return values,defined,total_length,meta


def fit_one_term(train_y:np.ndarray,train_x:np.ndarray,test_x:np.ndarray)->tuple[np.ndarray,dict[str,Any]]:
    y=np.asarray(train_y,dtype=np.float64);x=np.asarray(train_x,dtype=np.float64);held=np.asarray(test_x,dtype=np.float64)
    if x.ndim!=1 or held.ndim!=1 or y.shape!=x.shape or len(y)<3 or not len(held):raise ValueError("fold inputs require aligned vectors, >=3 training rows, and held-out rows")
    if not(np.isfinite(y).all() and np.isfinite(x).all() and np.isfinite(held).all()):raise ValueError("fold inputs must be finite")
    mean=float(x.mean());sd=float(x.std(ddof=0));scale=sd if sd>0. else 1.;z=(x-mean)/scale;zh=(held-mean)/scale
    design=np.column_stack((np.ones(len(y)),z));coef,_,rank,_=np.linalg.lstsq(design,y,rcond=None)
    if rank!=2:raise ValueError("one-term training design is rank deficient")
    pred=np.column_stack((np.ones(len(held)),zh))@coef
    return pred,{"training_n":len(y),"heldout_n":len(held),"training_feature_mean":mean,"training_feature_sd":sd,
      "training_applied_scale":scale,"intercept":float(coef[0]),"standardized_slope":float(coef[1]),"design_rank":int(rank)}


def score_zero_baseline(y:np.ndarray,pred:np.ndarray)->dict[str,Any]:
    target=np.asarray(y,dtype=np.float64);estimate=np.asarray(pred,dtype=np.float64)
    if target.ndim!=1 or estimate.shape!=target.shape or not len(target) or not(np.isfinite(target).all() and np.isfinite(estimate).all()):raise ValueError("score inputs must be aligned finite vectors")
    baseline=float(target@target);model=float(np.sum((target-estimate)**2))
    if baseline<=0.:raise ValueError("frozen residual baseline SSE must be positive")
    return {"n":int(len(target)),"frozen_s1_baseline_sse":baseline,"corrected_sse":model,"delta_r2":(baseline-model)/baseline}


def county_bootstrap(records:list[dict[str,Any]],replicates:int=BOOTSTRAP_REPLICATES,seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    if replicates<100:raise ValueError("at least 100 county bootstrap replicates required")
    groups={r:[] for r in REGIONS}
    for row in records:groups[row["region"]].append(row)
    if any(not group for group in groups.values()):raise ValueError("each region needs held-out counties")
    rng=np.random.default_rng(seed);pooled=np.empty(replicates);regional={r:np.empty(replicates) for r in REGIONS}
    for b in range(replicates):
        base_total=model_total=0.
        for region in REGIONS:
            rows=groups[region];draw=rng.integers(0,len(rows),size=len(rows))
            base=sum(float(rows[i]["baseline_sse"]) for i in draw);model=sum(float(rows[i]["model_sse"]) for i in draw)
            regional[region][b]=(base-model)/base if base>0. else np.nan;base_total+=base;model_total+=model
        pooled[b]=(base_total-model_total)/base_total if base_total>0. else np.nan
    def ci(values:np.ndarray)->list[float]:
        finite=values[np.isfinite(values)]
        if len(finite)<int(.99*replicates):raise ValueError("too many degenerate county bootstrap draws")
        return [float(np.quantile(finite,.025)),float(np.quantile(finite,.975))]
    return {"replicates":replicates,"seed":seed,"resampling":"paired held-county baseline/model SSE blocks; counties sampled with replacement within each official region",
      "pooled_delta_r2_ci_95":ci(pooled),"regional_delta_r2_ci_95":{r:ci(v) for r,v in regional.items()},"degenerate_replicates":int(np.sum(~np.isfinite(pooled)))}


def load_residuals(workspace:Path,official:list[tuple[str,str]])->tuple[np.ndarray,dict[str,str]]:
    path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(path)!=RESIDUAL_SHA:raise ValueError("frozen S1 residual artifact SHA mismatch")
    report=json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    if report["provenance"]["output_artifact"]["sha256"]!=RESIDUAL_SHA:raise ValueError("frozen residual report hash mismatch")
    table=pq.read_table(path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if table.num_rows!=len(official):raise ValueError("frozen S1 residual row count mismatch")
    positions={pair:i for i,pair in enumerate(official)};seen=set();y=np.full(len(official),np.nan)
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in positions or pair in seen:raise ValueError(f"unexpected/duplicate residual row {pair}")
        seen.add(pair);i=positions[pair]
        if row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:raise ValueError("frozen S1 residual lacks complete support")
        y[i]=float(row["crossfit_residual"])
    if seen!=set(official) or not np.isfinite(y).all():raise ValueError("frozen S1 residual IDs/mask mismatch")
    return y,{"path":str(path),"sha256":RESIDUAL_SHA,"report_sha256":sha256_file(RESIDUAL_REPORT)}


def run(workspace:Path,report_path:Path,manifest_path:Path)->dict[str,Any]:
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)];geoids=[g for g,_ in official];regions=np.asarray([r for _,r in official],dtype=object)
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or len(set(geoids))!=EXPECTED_N or set(regions.tolist())!=set(REGIONS):raise ValueError("official scored scope mismatch")
    mean_report=json.loads(M1_REPORT.read_text(encoding="utf-8"));mean_before=mean_report["measure_after"]
    mean_delta=float(mean_before["pooled"]["delta_r2"]);mean_positive=sum(float(mean_before["regional"][r]["delta_r2"])>0. for r in REGIONS)
    mean_artifact=mean_report["measure_before"]["M1_artifact_sha256"]
    if mean_artifact!=M1_SHA or mean_report["scope"]["paired_residual_support"]!=EXPECTED_FEATURE_N or mean_delta>=MIN_DELTA_R2 or mean_positive!=2:
        raise ValueError("predecessor named-road mean screen does not match dispatched before-state")
    mean_harness=json.loads(M1_HARNESS.read_text(encoding="utf-8"))["per_idea"]["M1"]
    expected_parser="OpenStreetMap source record_id matching ^w<digits>@<version>$; version is parsed only from documented way IDs"
    if mean_harness["sha256"]!=M1_SHA or any(d.get("version_parser")!=expected_parser or not d.get("no_target_or_reference_data_read") for d in mean_harness["diagnostics"].values()):
        raise ValueError("frozen M1 supplied-record-id parser provenance differs")
    mean_family=json.loads(M1_FAMILY_REPORT.read_text(encoding="utf-8"))
    mean_summary=mean_family["metrics"]["M1|R|covariate:named_mean_version"]
    if mean_summary["artifact_sha256"]!=M1_SHA or mean_summary["defined_rows"]!=EXPECTED_FEATURE_N:raise ValueError("frozen M1 family metrics mismatch")
    # Read frozen mean feature and ensure new p90 has the same token-defined tract support.
    mean_values,mean_mask,mean_sha=read_emission(workspace/"ideas/M1.parquet",M1_SHA,"M1","R",M1_CHANNEL,official)
    residual,residual_meta=load_residuals(workspace,official)
    config=load_config(CONFIG_PATH);feature=np.full(EXPECTED_N,np.nan);defined=np.zeros(EXPECTED_N,dtype=bool);length=np.zeros(EXPECTED_N,dtype=np.float64)
    input_meta={};accessed_layers=set()
    for region in REGIONS:
        idx=[i for i,p in enumerate(official) if p[1]==region];ids=[official[i][0] for i in idx];inputs=ProvidedInputs(config,workspace,region,ids)
        values,mask,road_length,meta=aggregate_named_road_p90(inputs)
        if int(mask.sum())!=int(mean_mask[idx].sum()):raise ValueError(f"p90 version-token support differs from frozen named-mean support in {region}")
        local_mean=mean_mask[idx]
        if not np.array_equal(mask,local_mean):raise ValueError(f"p90/mean defined masks differ in {region}")
        if np.any(~np.isfinite(values[mask])) or np.any(values[mask]<0.):raise ValueError("defined p90 version values must be finite and nonnegative")
        feature[idx]=values;defined[idx]=mask;length[idx]=road_length;input_meta[region]=meta;accessed_layers.update(inputs.accessed["parquet_columns"])
    if int(defined.sum())!=EXPECTED_FEATURE_N:raise ValueError("M1 p90 support differs from expected 9,247")
    if not np.isfinite(feature[defined]).all() or not np.isfinite(length[defined]).all() or np.any(length[defined]<=0.):raise ValueError("p90 and support lengths must be finite")
    allowed_road_columns={"class","sources","geometry"}
    valid_access=all(any(f"overture-roads.parquet:{name}" in col for name in allowed_road_columns) or col.endswith("-census-acs-housing:GEOID,geometry") for col in accessed_layers)
    if not valid_access or len(accessed_layers)!=16:
        raise ValueError(f"unexpected provided input columns/layers were accessed: {sorted(accessed_layers)!r}")
    support=defined&np.isfinite(residual)
    if support.sum()!=EXPECTED_FEATURE_N:raise ValueError("paired feature/residual support mismatch")
    county=np.asarray([county_fips(g) for g in geoids],dtype=object);prediction=np.full(EXPECTED_N,np.nan);records=[];manifest_rows=[]
    for region in REGIONS:
        region_mask=support&(regions==region);county_list=sorted(set(county[region_mask].tolist()))
        if len(county_list)<2:raise ValueError(f"too few supported counties in {region}")
        for held in county_list:
            test=region_mask&(county==held);train=region_mask&(county!=held)
            if not test.any() or train.sum()<3:raise ValueError(f"invalid county fold {region}/{held}")
            pred,fit=fit_one_term(residual[train],feature[train],feature[test]);prediction[test]=pred
            base=float(np.dot(residual[test],residual[test]));model=float(np.sum((residual[test]-pred)**2))
            train_counties=sorted(set(county[train].tolist()));train_hash=hashlib.sha256("\n".join(train_counties).encode()).hexdigest()
            records.append({"region":region,"heldout_county_fips":str(held),"heldout_n":int(test.sum()),"training_n":int(train.sum()),
              "training_county_count":len(train_counties),"training_county_fips_sha256":train_hash,"baseline_sse":base,"model_sse":model,
              "delta_r2":(base-model)/base if base else None,"fit":fit})
            for i in np.flatnonzero(test):manifest_rows.append((geoids[i],region,held,train_hash,len(train_counties),int(train.sum())))
    if np.any(support&~np.isfinite(prediction)) or np.any(~support&np.isfinite(prediction)):raise AssertionError("OOF prediction mask mismatch")
    pooled=score_zero_baseline(residual[support],prediction[support]);bootstrap=county_bootstrap(records)
    regional={}
    for region in REGIONS:
        use=support&(regions==region);regional[region]={**score_zero_baseline(residual[use],prediction[use]),
          "county_count":len(set(county[use].tolist())),"official_ids":int(np.sum(regions==region)),
          "version_token_defined_ids":int(np.sum(defined&(regions==region))),"versioned_named_road_length_m":float(length[use].sum()),
          "version_p90_distribution":{"minimum":float(np.min(feature[use])),"p50":float(np.quantile(feature[use],.5)),
            "p90":float(np.quantile(feature[use],.9)),"maximum":float(np.max(feature[use]))}}
    positive_regions=sum(regional[r]["delta_r2"]>0. for r in REGIONS);ci=bootstrap["pooled_delta_r2_ci_95"]
    passed=bool(pooled["delta_r2"]>=MIN_DELTA_R2 and ci[0]>MIN_CI_LOWER and positive_regions>=MIN_POSITIVE_REGIONS)
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    with manifest_path.open("w",encoding="utf-8",newline="") as f:
        writer=csv.writer(f,lineterminator="\n");writer.writerow(["GEOID","region","heldout_county_fips","training_county_fips_sha256","training_county_count","training_support_n"])
        writer.writerows(sorted(manifest_rows,key=lambda row:(row[1],row[0])))
    manifest_sha=sha256_file(manifest_path);feature_hash=data_sha256(geoids,[r for _,r in official],feature,defined)
    token_counts={r:int(input_meta[r]["matching_way_version_tokens"]) for r in REGIONS}
    report={"schema_version":1,"screen":"M1 length-weighted named-road version p90 county-held-out residual screen; descriptive-only unless fixed gates pass",
      "measure_before":{"source_screen":"M1 named-road mean-version residual screen","mean_version_delta_r2":mean_delta,
        "mean_version_pooled_ci_95":mean_before["county_block_bootstrap"]["pooled_delta_r2_ci_95"],"mean_version_positive_regions":mean_positive,
        "mean_version_regional_delta_r2":{r:float(mean_before["regional"][r]["delta_r2"]) for r in REGIONS},
        "mean_version_support":int(mean_mask.sum()),"mean_version_artifact_sha256":mean_sha,"requested_before_condition_met":True},
      "method":{"feature":"For each tract, the empirical 90th percentile of named-road segment OSM way versions, weighted by each segment's geometry length clipped to that tract. Each segment's version equals the arithmetic mean of its distinct matching supplied OSM version tokens, matching M1's feature parsing.",
        "version_parser":"Only supplied Overture roads.sources.record_id values whose source dataset is OpenStreetMap and whose full value matches ^w\\d+@(\\d+)$ (case-insensitive); no OSM history or external versions.",
        "named_classes":sorted(NAMED_CLASSES),"duplicate_tokens":"Deduplicate integer versions within each road geometry before averaging; repeated source memberships do not overweight a segment.",
        "weighted_quantile":"Sort segment-level mean versions ascending and select the first value whose cumulative clipped length is at least 0.90 of total tract named-versioned road length.",
        "geometry":"Provided road lines and official ACS tract polygons projected to EPSG:5070; intersected segment length is weight; segment portions outside tract excluded.",
        "outcome":"Frozen cross-fitted S1 Queen/Rook disagreement residual, read from its pinned artifact and not rebuilt or refit.",
        "validation":"Within each official region, fixed leave-one-five-digit-county-FIPS-out folds; same support and fold definition as the named-road mean screen.",
        "correction":"OLS intercept and one p90 term; fold-specific mean and population SD estimated only on training tracts; held-out features use those training statistics; zero SD uses scale 1.",
        "baseline":"Zero correction of the frozen residual on the identical paired support.",
        "metric":"Pooled and regional county-held-out ΔR²=(baseline SSE - OOF corrected SSE)/baseline SSE; 2,000 paired county-block bootstrap replicates resampled within each region.",
        "unregistered":True,"interpretation_limit":"Provided-feature residual association only, not target accuracy, causal evidence, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"version_token_defined_ids":int(defined.sum()),"paired_residual_support":int(support.sum()),
        "undefined_version_token_ids":int((~defined).sum()),"feature_mask_sha256":mask_sha256(defined),
        "feature_values_and_mask_sha256":feature_hash,"mean_feature_values_and_mask_sha256":data_sha256(geoids,[r for _,r in official],mean_values,mean_mask),
        "official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "versioned_named_road_length_m":float(length[support].sum()),"version_token_counts_by_region":token_counts,
        "version_token_coverage_by_region":{r:{"official_ids":int(np.sum(regions==r)),"version_defined_ids":int(np.sum(defined&(regions==r))),
          "version_defined_fraction":float(np.sum(defined&(regions==r))/np.sum(regions==r)),"parsed_way_version_token_count":token_counts[r],
          "nonmatching_nonempty_OSM_record_ids":int(input_meta[r]["nonmatching_nonempty_OSM_record_ids"]),
          "named_class_rows":int(input_meta[r]["named_class_rows"]),"named_class_rows_with_version":int(input_meta[r]["named_class_rows_with_at_least_one_matching_version"])} for r in REGIONS},
        "labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,"external_inputs_used":False,
        "removed_or_equivalent_references_read":False,"leaderboard_feedback_used":False,"estimator_integration":False,"uploads":0},
      "measure_after":{"pooled":pooled,"county_block_bootstrap":bootstrap,"regional":regional,"county_fold_count":len(records),"county_folds":records,
        "fold_manifest":{"path":str(manifest_path),"sha256":manifest_sha,"rows":len(manifest_rows)}},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_DELTA_R2,"bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,"minimum_positive_regions":MIN_POSITIVE_REGIONS},
      "acceptance":{"pooled_delta_r2_at_least_0_002":bool(pooled["delta_r2"]>=MIN_DELTA_R2),
        "county_block_ci_lower_above_zero":bool(ci[0]>MIN_CI_LOWER),"positive_region_count":positive_regions,
        "positive_in_at_least_3_of_4_regions":bool(positive_regions>=MIN_POSITIVE_REGIONS),"screen_pass":passed},
      "provenance":{"M1_mean_artifact":{"path":str(workspace/"ideas/M1.parquet"),"sha256":mean_sha},
        "M1_mean_report":{"path":str(M1_REPORT),"sha256":sha256_file(M1_REPORT)},"M1_family_summary":{"path":str(M1_FAMILY_REPORT),"sha256":sha256_file(M1_FAMILY_REPORT)},
        "M1_harness_report":{"path":str(M1_HARNESS),"sha256":sha256_file(M1_HARNESS)},"M1_source_definition":{"path":str(M1_SOURCE),"sha256":sha256_file(M1_SOURCE)},
        "frozen_S1_residual":residual_meta,"provided_road_inputs_by_region":input_meta,"config":{"path":str(CONFIG_PATH),"sha256":sha256_file(CONFIG_PATH)},
        "analysis_script_sha256":sha256_file(Path(__file__).resolve()),"bootstrap_seed":BOOTSTRAP_SEED,"bootstrap_replicates":BOOTSTRAP_REPLICATES},
      "interpretation":"The named-road p90 version residual screen met all fixed gates; retain as a descriptive lead pending separate authorization." if passed else
        "The named-road p90 version screen failed one or more fixed acceptance gates; keep it diagnostic-only and do not integrate."}
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/m1-named-version-p90-residual.json");parser.add_argument("--manifest",default="reports/m1-named-version-p90-county-folds.csv")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out,ROOT/args.manifest)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],
      "pooled_ci":report["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"],
      "regional":report["measure_after"]["regional"],"version_token_coverage":report["scope"]["version_token_coverage_by_region"],"acceptance":report["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
