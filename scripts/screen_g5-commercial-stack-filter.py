#!/usr/bin/env python3
"""County-held-out residual screen of G5 after excluding place stacks inside large commercial buildings."""
from __future__ import annotations

import argparse
import collections
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
sys.path.insert(0,str(ROOT/"src"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa: E402
from bias60.ideas.g import _allowed_building,_allowed_poi,_metric_context,_primary_name  # noqa: E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379;EXPECTED_S1_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
EXPECTED_G5_SHA="3df5cf68ea2bc952380d86bfecb3703dc1db2b15555c2943ceafbfaa11f34e0a"
G5_REPORT=ROOT/"reports/g-family-screen.json";G5_HARNESS=ROOT/"reports/ideas-harness.json"
G_SOURCE=ROOT/"src/bias60/ideas/g.py";CONFIG=ROOT/"config/bias60-harness-preregistered.json"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
AREA_THRESHOLD_M2=5000.0
COMMERCIAL_CLASS=re.compile(r"\b(commercial|industrial|warehouse|retail|shopping|mall)\b",re.IGNORECASE)
TRANSFORMER=Transformer.from_crs("EPSG:4326","EPSG:5070",always_xy=True)
BOOTSTRAP_REPLICATES=2000;BOOTSTRAP_SEED=20261028
MIN_DELTA_R2=.002;MIN_CI_LOWER=0.;MIN_RETAINED_STACK_FRACTION=.5


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def _hash_vector(geoids:list[str],regions:list[str],values:np.ndarray,defined:np.ndarray)->str:
    if not(len(geoids)==len(regions)==len(values)==len(defined)):raise ValueError("feature hash vectors misaligned")
    rows=(f"{g},{r},{int(ok)},{format(float(v),'.17g') if ok else ''}" for g,r,v,ok in zip(geoids,regions,values,defined))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def _source_hash_update(hasher:Any,parts:list[bytes|str])->None:
    for part in parts:
        value=part.encode("utf-8") if isinstance(part,str) else part
        hasher.update(len(value).to_bytes(8,"big"));hasher.update(value)


def qualifying_name_count(members:list[tuple[int,str]])->int:
    return len({str(name).strip().casefold() for _,name in members if isinstance(name,str) and name.strip()})


def is_large_commercial_class(building_class:Any)->bool:
    return bool(COMMERCIAL_CLASS.search(str(building_class or "")))


def large_commercial_polygons(inputs:ProvidedInputs)->tuple[list[Any],dict[str,Any]]:
    """Stream the provided building layer; source-filter before WKB decode and area measurement."""
    cols=["sources","class","geometry"];polygons=[];scan=source_ok=class_ok=area_ok=invalid=0
    digest=hashlib.sha256()
    for batch in inputs.iter_layer_batches("buildings",cols):
        sources=batch["sources"].to_pylist();classes=batch["class"].to_pylist();wkbs=batch["geometry"].to_pylist();scan+=len(wkbs)
        candidates=[]
        for src,cls,wkb in zip(sources,classes,wkbs):
            if not _allowed_building(src) or wkb is None:continue
            source_ok+=1
            cls_text=str(cls or "")
            if not is_large_commercial_class(cls_text):continue
            class_ok+=1
            _source_hash_update(digest,[cls_text,wkb])
            candidates.append(wkb)
        if not candidates:continue
        geometries=shapely.from_wkb(candidates)
        metric=shapely.transform(geometries,TRANSFORMER.transform,interleaved=False)
        valid=shapely.is_valid(metric)&~shapely.is_empty(metric)
        areas=shapely.area(metric)
        for geom,ok,area in zip(metric.tolist(),valid.tolist(),areas.tolist()):
            if not ok or not np.isfinite(area) or area<=0:invalid+=1;continue
            if float(area)>=AREA_THRESHOLD_M2:
                if geom.geom_type not in {"Polygon","MultiPolygon"}:continue
                polygons.append(geom);area_ok+=1
    meta={"provided_layer":"buildings","columns_read":cols,"rows_scanned":scan,"source_allowed_rows_with_geometry":source_ok,
      "class_matching_rows":class_ok,"valid_large_commercial_footprints":area_ok,"invalid_candidate_geometries":invalid,
      "excluded_Microsoft_or_mixed_source_rows_before_geometry_decode":True,"area_crs":"EPSG:5070",
      "fixed_area_minimum_m2_inclusive":AREA_THRESHOLD_M2,"class_rule":"class contains a whole-word commercial, industrial, warehouse, retail, shopping, or mall token",
      "selected_building_geometry_stream_sha256":digest.hexdigest()}
    return polygons,meta


def points_covered_by_footprints(points_xy:np.ndarray,polygons:list[Any])->np.ndarray:
    points=np.asarray(points_xy,dtype=np.float64)
    if points.ndim!=2 or points.shape[1]!=2:raise ValueError("stack points must be an (n,2) coordinate array")
    result=np.zeros(len(points),dtype=bool)
    if not len(points) or not polygons:return result
    metric_points=shapely.transform(shapely.points(points[:,0],points[:,1]),TRANSFORMER.transform,interleaved=False)
    tree=shapely.STRtree(np.asarray(polygons,dtype=object))
    pairs=tree.query(metric_points,predicate="covered_by")
    if pairs.size:result[np.unique(pairs[0])]=True
    return result


def g5_stack_features(stacks:dict[tuple[float,float],list[tuple[int,str]]],n_tracts:int,
                      excluded_coordinates:set[tuple[float,float]]|None=None)->dict[str,Any]:
    """Apply exact G5 coordinate/name threshold, with an optional fixed whole-stack exclusion."""
    if n_tracts<1:raise ValueError("at least one tract required")
    excluded=excluded_coordinates or set();place_counts=np.zeros(n_tracts,dtype=np.float64);stacked=np.zeros(n_tracts,dtype=np.float64)
    retained_stack_count=qualifying_stack_count=removed_stack_count=0
    for coord,members in stacks.items():
        if any(i<0 or i>=n_tracts for i,_ in members):raise ValueError("stack membership tract index out of range")
        qualifies=qualifying_name_count(members)>=5
        coordinate_excluded=coord in excluded
        if qualifies:qualifying_stack_count+=1
        if coordinate_excluded:
            if qualifies:removed_stack_count+=1
            continue
        place_counts+=np.bincount([i for i,_ in members],minlength=n_tracts)
        if qualifies:
            retained_stack_count+=1
            stacked+=np.bincount([i for i,_ in members],minlength=n_tracts)
    defined=place_counts>0;feature=np.zeros(n_tracts,dtype=np.float64)
    feature[defined]=np.clip(stacked[defined]/place_counts[defined],0.,1.)
    return {"feature":feature,"defined":defined,"place_counts":place_counts,"stacked_place_counts":stacked,
      "qualifying_stack_count":qualifying_stack_count,"retained_stack_count":retained_stack_count,
      "removed_stack_count":removed_stack_count,"retained_stack_fraction":retained_stack_count/qualifying_stack_count if qualifying_stack_count else None,
      "excluded_place_count":0}


def load_g5_stacks(inputs:ProvidedInputs)->tuple[dict[tuple[float,float],list[tuple[int,str]]],np.ndarray,dict[str,Any]]:
    """Read exactly the provided POI fields and reproduce G5's unrounded lon/lat key/name rule."""
    parquet=inputs.open_places_parquet(["sources","names","geometry"]);stacks:dict[tuple[float,float],list[tuple[int,str]]]=collections.defaultdict(list)
    place_counts=np.zeros(len(inputs.geoids),dtype=np.float64);scanned=eligible=assigned=excluded=0;digest=hashlib.sha256()
    tracts,tree=_metric_context(inputs)
    for rg in range(parquet.metadata.num_row_groups):
        table=parquet.read_row_group(rg,columns=["sources","names","geometry"])
        sources=table["sources"].to_pylist();names=table["names"].to_pylist();wkbs=table["geometry"].to_pylist();scanned+=len(wkbs)
        keep=[]
        for i,(source,name,wkb) in enumerate(zip(sources,names,wkbs)):
            if not _allowed_poi(source) or wkb is None:
                excluded+=1;continue
            eligible+=1;name_value=_primary_name(name) or "";_source_hash_update(digest,[name_value,wkb]);keep.append((i,name_value))
        if not keep:continue
        geom=shapely.from_wkb([wkbs[i] for i,_ in keep])
        metric=shapely.transform(geom,TRANSFORMER.transform,interleaved=False)
        qi,ti=tree.query(metric,predicate="within")
        for j,tract_i in zip(qi.tolist(),ti.tolist()):
            row_i,name=keep[j];lon,lat=shapely.from_wkb(wkbs[row_i]).coords[0]
            coordinate=(float(lon),float(lat));stacks[coordinate].append((int(tract_i),name));place_counts[tract_i]+=1.;assigned+=1
    meta={"provided_layer":"pois","columns_read":["sources","names","geometry"],"rows_scanned":scanned,
      "eligible_non_Microsoft_rows_with_geometry":eligible,"source_or_geometry_excluded_rows":excluded,"points_assigned_to_official_tracts":assigned,
      "assignment":"point strictly within one provided ACS tract; exact stack coordinate is original lon/lat pair",
      "coordinate_and_name_input_stream_sha256":digest.hexdigest()}
    return dict(stacks),place_counts,meta


def feature_measure(y:np.ndarray,feature:np.ndarray)->dict[str,Any]:
    target=np.asarray(y,dtype=np.float64);x=np.asarray(feature,dtype=np.float64)
    if target.ndim!=1 or x.shape!=target.shape or not target.size or not np.isfinite(target).all() or not np.isfinite(x).all():raise ValueError("score inputs must align and be finite")
    baseline=float(target@target);mean=float(x.mean());sd=float(x.std(ddof=0));scale=sd if sd>0 else 1.
    z=(x-mean)/scale;design=np.column_stack((np.ones(len(z)),z));coef,_,rank,_=np.linalg.lstsq(design,target,rcond=None)
    pred=design@coef;model=float(np.sum((target-pred)**2))
    if baseline<=0:raise ValueError("frozen residual baseline SSE must be positive")
    return {"n":int(len(target)),"baseline_sse":baseline,"model_sse":model,"delta_r2":(baseline-model)/baseline,
      "training_feature_mean":mean,"training_feature_sd":sd,"training_scale":scale,"intercept":float(coef[0]),
      "standardized_coefficient":float(coef[1]),"design_rank":int(rank)}


def fit_one_term(train_y:np.ndarray,train_x:np.ndarray,test_x:np.ndarray)->tuple[np.ndarray,dict[str,Any]]:
    y=np.asarray(train_y,dtype=np.float64);x=np.asarray(train_x,dtype=np.float64);xt=np.asarray(test_x,dtype=np.float64)
    if y.ndim!=1 or x.shape!=y.shape or xt.ndim!=1 or len(y)<3 or not len(xt) or not np.isfinite(y).all() or not np.isfinite(x).all() or not np.isfinite(xt).all():
        raise ValueError("county fold vectors must align, be finite and have adequate support")
    mean=float(x.mean());sd=float(x.std(ddof=0));scale=sd if sd>0 else 1.;z=(x-mean)/scale;zt=(xt-mean)/scale
    train=np.column_stack((np.ones(len(y)),z));coef,_,rank,_=np.linalg.lstsq(train,y,rcond=None)
    pred=np.column_stack((np.ones(len(xt)),zt))@coef
    return pred,{"training_n":len(y),"heldout_n":len(xt),"training_mean":mean,"training_sd":sd,"training_scale":scale,
      "intercept":float(coef[0]),"standardized_coefficient":float(coef[1]),"design_rank":int(rank)}


def county_bootstrap(records:list[dict[str,Any]],replicates:int=BOOTSTRAP_REPLICATES,seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    if replicates<100:raise ValueError("at least 100 county bootstrap replicates required")
    groups={r:[] for r in REGIONS}
    for row in records:groups[row["region"]].append(row)
    if any(not v for v in groups.values()):raise ValueError("all regions need supported county folds")
    rng=np.random.default_rng(seed);result={"unfiltered":np.empty(replicates),"filtered":np.empty(replicates),"filtered_minus_unfiltered":np.empty(replicates)}
    regional={name:{r:np.empty(replicates) for r in REGIONS} for name in result}
    for b in range(replicates):
        sums={name:{"base":0.,"model":0.} for name in result};regional_sums={name:{r:{"base":0.,"model":0.} for r in REGIONS} for name in result}
        for r in REGIONS:
            rows=groups[r];draw=rng.integers(0,len(rows),size=len(rows))
            for idx in draw:
                row=rows[int(idx)]
                for name in ("unfiltered","filtered"):
                    sums[name]["base"]+=row["baseline_sse"]
                    sums[name]["model"]+=row[f"{name}_sse"]
                    regional_sums[name][r]["base"]+=row["baseline_sse"]
                    regional_sums[name][r]["model"]+=row[f"{name}_sse"]
        def d(a:dict[str,float])->float:return (a["base"]-a["model"])/a["base"] if a["base"]>0 else float("nan")
        result["unfiltered"][b]=d(sums["unfiltered"]);result["filtered"][b]=d(sums["filtered"])
        result["filtered_minus_unfiltered"][b]=result["filtered"][b]-result["unfiltered"][b]
        for name in ("unfiltered","filtered"):
            for r in REGIONS:regional[name][r][b]=d(regional_sums[name][r])
        for r in REGIONS:
            regional["filtered_minus_unfiltered"][r][b]=regional["filtered"][r][b]-regional["unfiltered"][r][b]
    def ci(v:np.ndarray)->list[float]:
        good=v[np.isfinite(v)]
        if len(good)<int(.99*replicates):raise ValueError("too many degenerate county bootstrap draws")
        return [float(x) for x in np.quantile(good,[.025,.975])]
    return {"replicates":replicates,"seed":seed,"resampling":"paired held-out county SSE blocks sampled with replacement within each official region",
      "pooled_ci_95":{"unfiltered":ci(result["unfiltered"]),"filtered":ci(result["filtered"]),"filtered_minus_unfiltered":ci(result["filtered_minus_unfiltered"])},
      "regional_ci_95":{name:{r:ci(v) for r,v in regional[name].items()} for name in regional}}


def run(workspace:Path,report_path:Path)->dict[str,Any]:
    harness=json.loads(G5_HARNESS.read_text(encoding="utf-8"));g5_info=harness["per_idea"]["G5"]
    if g5_info["sha256"]!=EXPECTED_G5_SHA:raise ValueError("frozen G5 artifact hash changed")
    g_report=json.loads(G5_REPORT.read_text(encoding="utf-8"));g5_report=g_report["ideas"]["G5"]
    if g5_report["sha256"]!=EXPECTED_G5_SHA or g_report["g5_rule"]!={"exact_coordinate":True,"minimum_distinct_primary_names":5,
      "neighbourhood_radius_m":3000,"exported_mass_allocator":"non-Microsoft OSM/Esri-only building centroid counts"}:
        raise ValueError("frozen G5 report definition/hash mismatch")
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):raise ValueError("official tract partition mismatch")
    geoids=np.asarray([g for g,_ in official],dtype=object);regions=np.asarray([r for _,r in official],dtype=object)
    residual_path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(residual_path)!=EXPECTED_S1_SHA:raise ValueError("frozen S1 residual hash mismatch")
    residual_table=pq.read_table(residual_path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    residual=np.full(EXPECTED_N,np.nan);pos={p:i for i,p in enumerate(official)};seen=set()
    if residual_table.num_rows!=EXPECTED_N:raise ValueError("frozen S1 residual output row count mismatch")
    for row in residual_table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in pos or pair in seen or row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:raise ValueError("invalid frozen S1 residual record")
        seen.add(pair);residual[pos[pair]]=float(row["crossfit_residual"])
    if seen!=set(official) or not np.isfinite(residual).all():raise ValueError("frozen residual alignment/definedness mismatch")
    config=load_config(CONFIG);unfiltered=np.full(EXPECTED_N,np.nan);unfiltered_mask=np.zeros(EXPECTED_N,dtype=bool)
    stacks_by_region={};poi_meta={};building_polygons=[];building_meta={};offset=0
    artifact_path=workspace/"ideas/G5.parquet"
    if sha256_file(artifact_path)!=EXPECTED_G5_SHA:raise ValueError("workspace G5 emission artifact hash mismatch")
    g5_table=pq.read_table(artifact_path,columns=["GEOID","region","idea","component","channel","value","defined"])
    expected_feature_rows=0
    for row in g5_table.to_pylist():
        if row["idea"]!="G5" or row["component"]!="Est" or row["channel"]!="covariate":continue
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in pos or unfiltered_mask[pos[pair]]:raise ValueError("G5 Est feature contains duplicate/unexpected official ID")
        i=pos[pair];unfiltered[i]=float(row["value"]);unfiltered_mask[i]=bool(row["defined"]);expected_feature_rows+=1
    if expected_feature_rows!=EXPECTED_N or np.any(unfiltered_mask&~np.isfinite(unfiltered)):raise ValueError("G5 Est emission support malformed")
    # Read each provided geometry layer through the frozen rule-clean access facade.
    for region in REGIONS:
        idx=[i for i,p in enumerate(official) if p[1]==region];ids=[official[i][0] for i in idx]
        inputs=ProvidedInputs(config,workspace,region,ids)
        stacks,place_counts,pm=load_g5_stacks(inputs)
        current=g5_stack_features(stacks,len(ids))
        local_values=np.full(len(ids),np.nan);local_mask=current["defined"]
        local_values[local_mask]=current["feature"][local_mask]
        output_positions=np.asarray(idx,dtype=np.int64)
        if not np.array_equal(local_mask,unfiltered_mask[output_positions]) or not np.allclose(local_values[local_mask],unfiltered[output_positions][local_mask],rtol=0,atol=1e-14):
            raise ValueError(f"reconstructed G5 unfiltered feature differs from frozen emission in {region}")
        stacks_by_region[region]=(idx,stacks,current);poi_meta[region]=pm
        polygons,bm=large_commercial_polygons(inputs);building_polygons.extend(polygons);building_meta[region]=bm
    # All observed place coordinates are unique stack keys; one spatial lookup removes each whole exact stack.
    stack_coords=list(dict.fromkeys(coord for region in REGIONS for coord in stacks_by_region[region][1]))
    points_xy=np.asarray(stack_coords,dtype=np.float64).reshape((-1,2))
    covered=points_covered_by_footprints(points_xy,building_polygons)
    excluded_coords={coord for coord,is_inside in zip(stack_coords,covered) if is_inside}
    filtered=np.full(EXPECTED_N,np.nan);filtered_mask=np.zeros(EXPECTED_N,dtype=bool);region_support={};all_stack_count=kept_stack_count=removed_stack_count=all_points=excluded_points=0
    for region in REGIONS:
        idx,stacks,base=stacks_by_region[region]
        local_excluded={coord for coord in stacks if coord in excluded_coords}
        current=g5_stack_features(stacks,len(idx),local_excluded)
        positions=np.asarray(idx,dtype=np.int64);local_mask=current["defined"]
        filtered[positions[local_mask]]=current["feature"][local_mask];filtered_mask[positions]=local_mask
        n_all=sum(len(m) for m in stacks.values());n_excluded=sum(len(stacks[c]) for c in local_excluded)
        all_points+=n_all;excluded_points+=n_excluded
        all_stack_count+=current["qualifying_stack_count"];kept_stack_count+=current["retained_stack_count"];removed_stack_count+=current["removed_stack_count"]
        region_support[region]={"official_ids":len(idx),"G5_unfiltered_defined":int(base["defined"].sum()),"filtered_defined":int(local_mask.sum()),
          "unique_coordinates_with_places":len(stacks),"eligible_place_points":int(n_all),"excluded_place_points":int(n_excluded),
          "excluded_place_fraction":n_excluded/n_all if n_all else None,"qualifying_unfiltered_stacks_ge5_names":current["qualifying_stack_count"],
          "retained_qualifying_stacks":current["retained_stack_count"],"removed_qualifying_stacks":current["removed_stack_count"],
          "retained_qualifying_stack_fraction":current["retained_stack_fraction"],
          "filtered_G5_stack_share_distribution":{ "n":int(local_mask.sum()),"mean":float(current["feature"][local_mask].mean()) if local_mask.any() else None,
            "p90":float(np.quantile(current["feature"][local_mask],.9)) if local_mask.any() else None}}
    paired=unfiltered_mask&filtered_mask&np.isfinite(residual)
    if int(paired.sum())<100:raise ValueError("insufficient paired feature/residual support")
    county=np.asarray([g[:5] for g in geoids],dtype=object);unfiltered_pred=np.full(EXPECTED_N,np.nan);filtered_pred=np.full(EXPECTED_N,np.nan);fold_records=[];manifest=[]
    for region in REGIONS:
        regional=paired&(regions==region);county_list=sorted(set(county[regional].tolist()))
        if len(county_list)<2:raise ValueError(f"insufficient supported county blocks for {region}")
        for held in county_list:
            test=regional&(county==held);train=regional&(county!=held)
            if test.sum()<1 or train.sum()<3:raise ValueError(f"invalid county fold {region}/{held}")
            pu,fit_u=fit_one_term(residual[train],unfiltered[train],unfiltered[test])
            pf,fit_f=fit_one_term(residual[train],filtered[train],filtered[test])
            unfiltered_pred[test]=pu;filtered_pred[test]=pf
            baseline=float(np.dot(residual[test],residual[test]));us=float(np.sum((residual[test]-pu)**2));fs=float(np.sum((residual[test]-pf)**2))
            trains=sorted(set(county[train&(regions==region)].tolist()));train_hash=hashlib.sha256("\n".join(trains).encode()).hexdigest()
            fold_records.append({"region":region,"heldout_county_fips":str(held),"heldout_n":int(test.sum()),"training_n":int(train.sum()),
              "training_county_count":len(trains),"training_county_fips_sha256":train_hash,"baseline_sse":baseline,
              "unfiltered_sse":us,"filtered_sse":fs,"unfiltered_delta_r2":(baseline-us)/baseline if baseline else None,
              "filtered_delta_r2":(baseline-fs)/baseline if baseline else None,"unfiltered_fit":fit_u,"filtered_fit":fit_f})
            for i in np.flatnonzero(test):manifest.append(f"{geoids[i]},{region},{held},{train_hash},{len(trains)},{int(train.sum())}\n")
    if np.any(paired&(~np.isfinite(unfiltered_pred)|~np.isfinite(filtered_pred))) or np.any(~paired&(np.isfinite(unfiltered_pred)|np.isfinite(filtered_pred))):raise AssertionError("OOF masks do not match paired support")
    base_sse=float(np.dot(residual[paired],residual[paired]));unfiltered_sse=float(np.sum((residual[paired]-unfiltered_pred[paired])**2));filtered_sse=float(np.sum((residual[paired]-filtered_pred[paired])**2))
    unfiltered_metric={"n":int(paired.sum()),"baseline_sse":base_sse,"model_sse":unfiltered_sse,"delta_r2":(base_sse-unfiltered_sse)/base_sse}
    filtered_metric={"n":int(paired.sum()),"baseline_sse":base_sse,"model_sse":filtered_sse,"delta_r2":(base_sse-filtered_sse)/base_sse}
    bootstrap=county_bootstrap(fold_records)
    retained=kept_stack_count/all_stack_count if all_stack_count else None
    ci=bootstrap["pooled_ci_95"]["filtered"]
    passed=bool(filtered_metric["delta_r2"]>=MIN_DELTA_R2 and ci[0]>MIN_CI_LOWER and retained is not None and retained>=MIN_RETAINED_STACK_FRACTION)
    fold_text="".join(sorted(manifest));fold_sha=hashlib.sha256(fold_text.encode()).hexdigest()
    filtered_defined=filtered_mask&np.isfinite(filtered);removed_count=int(np.sum(covered))
    report={"schema_version":1,"screen":"G5 exact-coordinate excess-stack covariate after fixed provided commercial-footprint exclusion; descriptive-only",
      "measure_before":{"G5_registered":True,"G5_definition":"exact lon/lat coordinate stack; at least five distinct nonempty normalized primary names; Est feature is stacked places in tract / all eligible assigned places in tract",
        "G5_artifact_sha256":EXPECTED_G5_SHA,"filtered_county_heldout_delta_r2_measured":False,"G5_unfiltered_support_ids":int(unfiltered_mask.sum())},
      "method":{"outcome":"Frozen S1 orthogonalized establishment/facility disagreement cross-fit residual; no labels or refit.",
        "feature":"Existing G5 Est|covariate exact-coordinate distinct-name stack share. Filtered variant removes all provided eligible POI points whose exact coordinates are covered by at least one qualifying provided large commercial building footprint, then recomputes the identical G5 numerator and denominator on the remaining points.",
        "footprint_rule":{"sources":"Only provided Overture building records whose source memberships are exclusively OpenStreetMap/OSM/Esri; mixed or Microsoft membership rows are rejected before geometry decode.",
          "class":"whole-word class token: commercial, industrial, warehouse, retail, shopping, or mall","area_crs":"EPSG:5070","minimum_area_m2_inclusive":AREA_THRESHOLD_M2,
          "point_predicate":"covered_by footprint, including boundary points","buffer_m":0.0},
        "G5_stack_rule":"Reuse the exact G5 coordinate key from original POI lon/lat and normalized primary-name rule; qualifying stack has at least five distinct nonempty primary names. No altered distance, fuzzy-coordinate, or alternate-name rule.",
        "validation":"For each official region, hold out each supported five-digit GEOID county as a fixed leave-one-county-out fold. Fit separate one-term residual corrections for unfiltered and filtered features on identical paired support.",
        "standardization":"For each fold and each feature, use training-county mean and population SD only; predict held-out county with training-fitted intercept and coefficient. Zero SD uses scale 1.",
        "metric":"Pooled and regional county-held-out incremental ΔR²=(zero-correction frozen-S1 SSE - OOF corrected SSE)/zero-correction SSE. Paired county-block bootstrap within official regions.",
        "comparisons":"Unfiltered and filtered models share identical outer folds and paired support. The filtered-versus-unfiltered ΔR² difference is reported separately.",
        "unregistered":True,"interpretation_limit":"Feature screen only; not target accuracy, causal inference, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "paired_feature_residual_support":int(paired.sum()),"unfiltered_feature_defined":int(unfiltered_mask.sum()),"filtered_feature_defined":int(filtered_mask.sum()),
        "paired_support_mask_sha256":hashlib.sha256(np.packbits(paired.astype(np.uint8),bitorder="little").tobytes()).hexdigest(),
        "filtered_feature_values_and_mask_sha256":_hash_vector(geoids.tolist(),regions.tolist(),filtered,filtered_defined),
        "unfiltered_feature_values_and_mask_sha256":_hash_vector(geoids.tolist(),regions.tolist(),unfiltered,unfiltered_mask),
        "removed_unique_place_coordinates":removed_count,"removed_place_point_count":excluded_points,"all_eligible_place_points":all_points,
        "removed_place_point_fraction":excluded_points/all_points if all_points else None,"large_commercial_footprint_count":len(building_polygons),
        "qualifying_unfiltered_stack_count":all_stack_count,"retained_qualifying_stack_count":kept_stack_count,"removed_qualifying_stack_count":removed_stack_count,
        "retained_qualifying_stack_fraction":retained,"region_support":region_support,"labels_read":False,"sample_score_values_read":False,
        "target_reconstruction":False,"external_inputs_used":False,"removed_or_equivalent_reference_sources_used":False,
        "leaderboard_feedback_used":False,"uploads":0,"estimator_or_registry_changed":False},
      "measure_after":{"unfiltered_pooled":unfiltered_metric,"filtered_pooled":filtered_metric,
        "filtered_minus_unfiltered_delta_r2":filtered_metric["delta_r2"]-unfiltered_metric["delta_r2"],"paired_county_bootstrap":bootstrap,
        "regional":{},"fold_count":len(fold_records),"fold_manifest":{"rows":int(paired.sum()),"sha256":fold_sha,
          "columns":["GEOID","region","heldout_county_fips","training_county_fips_sha256","training_county_count","training_support_n"]},
        "county_folds":fold_records},
      "thresholds":{"filtered_delta_r2_minimum_inclusive":MIN_DELTA_R2,"filtered_bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,
        "minimum_qualifying_stacks_retained_fraction":MIN_RETAINED_STACK_FRACTION},
      "acceptance":{"filtered_delta_r2_at_least_0_002":bool(filtered_metric["delta_r2"]>=MIN_DELTA_R2),
        "filtered_county_ci_lower_above_zero":bool(ci[0]>MIN_CI_LOWER),"at_least_half_supported_stacks_remain":bool(retained is not None and retained>=MIN_RETAINED_STACK_FRACTION),
        "screen_pass":passed},
      "provenance":{"G5_emission":{"path":str(artifact_path),"sha256":EXPECTED_G5_SHA},
        "G5_family_report":{"path":str(G5_REPORT),"sha256":sha256_file(G5_REPORT)},"G5_harness_report":{"path":str(G5_HARNESS),"sha256":sha256_file(G5_HARNESS)},
        "G5_definition_source":{"path":str(G_SOURCE),"sha256":sha256_file(G_SOURCE)},
        "frozen_S1_residual":{"path":str(residual_path),"sha256":EXPECTED_S1_SHA,"report_sha256":sha256_file(RESIDUAL_REPORT)},
        "provided_POI_inputs_by_region":poi_meta,"provided_building_inputs_by_region":building_meta,
        "analysis_script_sha256":sha256_file(Path(__file__).resolve()),"bootstrap_seed":BOOTSTRAP_SEED,"bootstrap_replicates":BOOTSTRAP_REPLICATES},
      "interpretation":"The commercial-footprint filtered G5 variant failed one or more predeclared acceptance gates; retain the original descriptive G5 feature and do not integrate." if not passed else
        "The filtered G5 feature met all predeclared diagnostic gates; retain as a descriptive lead pending separate authorization."}
    for r in REGIONS:
        use=paired&(regions==r);b=float(np.dot(residual[use],residual[use]));su=float(np.sum((residual[use]-unfiltered_pred[use])**2));sf=float(np.sum((residual[use]-filtered_pred[use])**2))
        report["measure_after"]["regional"][r]={"support_ids":int(use.sum()),"county_count":len(set(county[use].tolist())),
          "unfiltered_delta_r2":(b-su)/b if b else None,"filtered_delta_r2":(b-sf)/b if b else None,
          "filtered_minus_unfiltered_delta_r2":(su-sf)/b if b else None,
          "county_block_ci_95":{"unfiltered":bootstrap["regional_ci_95"]["unfiltered"][r],"filtered":bootstrap["regional_ci_95"]["filtered"][r],
            "filtered_minus_unfiltered":bootstrap["regional_ci_95"]["filtered_minus_unfiltered"][r]}}
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""));parser.add_argument("--out",default="reports/g5-commercial-stack-filter-screen.json")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    result=run(workspace,ROOT/args.out)
    print(json.dumps({"scope":result["scope"],"unfiltered":result["measure_after"]["unfiltered_pooled"],"filtered":result["measure_after"]["filtered_pooled"],
      "pooled_ci":result["measure_after"]["paired_county_bootstrap"]["pooled_ci_95"],"retained_stack_fraction":result["scope"]["retained_qualifying_stack_fraction"],
      "acceptance":result["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
