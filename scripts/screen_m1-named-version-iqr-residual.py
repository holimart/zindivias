#!/usr/bin/env python3
"""County-held-out screen of length-weighted IQR in supplied named-road OSM way versions."""
from __future__ import annotations
import argparse,csv,hashlib,io,json,os,re,sys
from pathlib import Path
from typing import Any
import numpy as np
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa:E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379;EXPECTED_SUPPORT=9247
M1_EMISSION_SHA="ddc48fc70558cb920659484b870819a9c4be43f3488e7fe3f59eed148b6e9f37"
S1_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
M1_MEAN_REPORT=ROOT/"reports/m1-named-road-mean-version-residual.json"
P90_REPORT=ROOT/"reports/m1-named-version-p90-residual.json"
P90_MANIFEST=ROOT/"reports/m1-named-version-p90-county-folds.csv"
M1_SOURCE=ROOT/"src/bias60/ideas/m.py"
P90_SOURCE=ROOT/"scripts/screen_m1-named-version-p90.py"
CONFIG=ROOT/"config/bias60-harness-preregistered.json"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
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
    if len(value)!=11 or not value.isdigit():raise ValueError("GEOID must contain eleven digits")
    return value[:5]


def data_sha256(geoids:list[str],regions:list[str],values:np.ndarray,defined:np.ndarray)->str:
    if not(len(geoids)==len(regions)==len(values)==len(defined)):raise ValueError("hash vectors misaligned")
    rows=(f"{g},{r},{int(ok)},{format(float(v),'.17g') if ok else ''}" for g,r,v,ok in zip(geoids,regions,values,defined))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


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
    versions,n_osm,non_way=source_versions(sources)
    return (float(np.mean(versions)) if versions else None),n_osm,non_way


def weighted_quantile(values:np.ndarray,weights:np.ndarray,q:float)->float:
    v=np.asarray(values,dtype=np.float64);w=np.asarray(weights,dtype=np.float64)
    if v.ndim!=1 or w.shape!=v.shape or not len(v) or not np.isfinite(v).all() or not np.isfinite(w).all():raise ValueError("weighted quantile inputs must be aligned, finite and nonempty")
    if not 0.<q<=1. or np.any(w<=0.):raise ValueError("quantile requires 0<q<=1 and positive weights")
    order=np.argsort(v,kind="mergesort");sv=v[order];sw=w[order]
    index=int(np.searchsorted(np.cumsum(sw),q*float(sw.sum()),side="left"))
    return float(sv[min(index,len(sv)-1)])


def weighted_iqr(values:np.ndarray,weights:np.ndarray)->float:
    """Empirical length-weighted Q75 minus Q25, using the fixed left-continuous quantile rule."""
    low=weighted_quantile(values,weights,.25);high=weighted_quantile(values,weights,.75)
    if high<low:raise ValueError("weighted quantile ordering failed")
    return high-low


def iqr_by_tract(distributions:list[list[tuple[float,float]]])->tuple[np.ndarray,np.ndarray,np.ndarray]:
    values=np.full(len(distributions),np.nan);defined=np.zeros(len(distributions),dtype=bool);total_length=np.zeros(len(distributions))
    for i,items in enumerate(distributions):
        if not items:continue
        versions=np.asarray([v for v,_ in items],dtype=np.float64);lengths=np.asarray([w for _,w in items],dtype=np.float64)
        if not np.isfinite(lengths).all() or np.any(lengths<=0.):raise ValueError("clipped lengths must be finite and positive")
        values[i]=weighted_iqr(versions,lengths);defined[i]=True;total_length[i]=float(lengths.sum())
    return values,defined,total_length


def aggregate_named_iqr(inputs:ProvidedInputs)->tuple[np.ndarray,np.ndarray,np.ndarray,dict[str,Any]]:
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
            selected_hash.update(",".join(str(x) for x in versions).encode());selected_hash.update(b"\0");selected_hash.update(wkbs[index])
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
    values,defined,total_length=iqr_by_tract(distributions)
    meta={"provided_layer":"roads","columns_read":["class","sources","geometry"],"rows_scanned":rows_scanned,
      "named_class_rows":rows_named,"named_class_OSM_record_id_tokens":rows_with_osm_ids,"matching_way_version_tokens":token_count,
      "named_class_rows_with_at_least_one_matching_version":rows_with_versions,"nonmatching_nonempty_OSM_record_ids":rows_non_way_ids,
      "versioned_named_rows_without_geometry":rows_without_geometry,"versioned_named_road_length_m":float(total_length.sum()),
      "tracts_with_positive_versioned_named_road_length":int(defined.sum()),"feature":"length-weighted empirical IQR of per-segment mean unique supplied OSM way versions",
      "version_parser":"OpenStreetMap source record_id fullmatch ^w\\d+@(\\d+)$, case-insensitive; only supplied record_id values are parsed",
      "multiple_versions_per_geometry":"deduplicate integer way versions and use their arithmetic mean, matching M1 named_mean_version input parsing",
      "weighted_quantile":"first sorted value whose cumulative clipped length reaches q times total clipped length; q=.25 and .75, stable sort",
      "geometry_assignment":"provided lines and official tract polygons transformed to EPSG:5070; intersect and weight by clipped segment length",
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
    if target.ndim!=1 or estimate.shape!=target.shape or not len(target) or not(np.isfinite(target).all() and np.isfinite(estimate).all()):raise ValueError("score vectors must be aligned and finite")
    base=float(target@target);model=float(np.sum((target-estimate)**2))
    if base<=0.:raise ValueError("frozen residual baseline SSE must be positive")
    return {"n":len(target),"frozen_s1_baseline_sse":base,"corrected_sse":model,"delta_r2":(base-model)/base}


def county_bootstrap(records:list[dict[str,Any]])->dict[str,Any]:
    groups={r:[] for r in REGIONS}
    for row in records:groups[row["region"]].append(row)
    if any(not x for x in groups.values()):raise ValueError("all regions need county folds")
    rng=np.random.default_rng(BOOTSTRAP_SEED);pooled=np.empty(BOOTSTRAP_REPLICATES);regional={r:np.empty(BOOTSTRAP_REPLICATES) for r in REGIONS}
    for b in range(BOOTSTRAP_REPLICATES):
        base_total=model_total=0.
        for region in REGIONS:
            rows=groups[region];draw=rng.integers(0,len(rows),size=len(rows))
            base=sum(float(rows[i]["baseline_sse"]) for i in draw);model=sum(float(rows[i]["model_sse"]) for i in draw)
            regional[region][b]=(base-model)/base if base>0. else np.nan;base_total+=base;model_total+=model
        pooled[b]=(base_total-model_total)/base_total if base_total>0. else np.nan
    def ci(v:np.ndarray)->list[float]:
        finite=v[np.isfinite(v)]
        if len(finite)<int(.99*BOOTSTRAP_REPLICATES):raise ValueError("too many degenerate county bootstrap replicates")
        return [float(np.quantile(finite,.025)),float(np.quantile(finite,.975))]
    return {"replicates":BOOTSTRAP_REPLICATES,"seed":BOOTSTRAP_SEED,
      "resampling":"paired held-county baseline/model SSE blocks; counties sampled with replacement within each official region",
      "pooled_delta_r2_ci_95":ci(pooled),"regional_delta_r2_ci_95":{r:ci(v) for r,v in regional.items()},"degenerate_replicates":int(np.sum(~np.isfinite(pooled)))}


def read_m1_emission(path:Path,official:list[tuple[str,str]])->tuple[np.ndarray,np.ndarray,str]:
    if sha256_file(path)!=M1_EMISSION_SHA:raise ValueError("frozen M1 emission artifact hash changed")
    table=pq.read_table(path,columns=["GEOID","region","idea","component","channel","value","defined"])
    positions={pair:i for i,pair in enumerate(official)};values=np.full(len(official),np.nan);defined=np.zeros(len(official),dtype=bool);seen=np.zeros(len(official),dtype=bool)
    for row in table.to_pylist():
        if row["idea"]!="M1" or row["component"]!="R" or row["channel"]!="covariate:named_mean_version":continue
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in positions:raise ValueError("M1 emission has an unexpected official ID")
        i=positions[pair]
        if seen[i]:raise ValueError("duplicate M1 mean emission")
        seen[i]=True;defined[i]=bool(row["defined"])
        if row["value"] is not None:values[i]=float(row["value"])
    if not seen.all():raise ValueError("frozen M1 mean emission does not cover every official ID")
    if np.any(defined&~np.isfinite(values)):raise ValueError("defined frozen M1 means must be finite")
    return values,defined,sha256_file(path)


def load_residuals(path:Path,official:list[tuple[str,str]])->tuple[np.ndarray,dict[str,str]]:
    if sha256_file(path)!=S1_SHA:raise ValueError("frozen S1 residual artifact hash changed")
    table=pq.read_table(path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if table.num_rows!=len(official):raise ValueError("frozen S1 residual row count mismatch")
    positions={pair:i for i,pair in enumerate(official)};values=np.full(len(official),np.nan);seen=set()
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in positions or pair in seen:raise ValueError("unexpected or duplicate residual ID")
        if row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:raise ValueError("frozen S1 residual not fully defined")
        values[positions[pair]]=float(row["crossfit_residual"]);seen.add(pair)
    if seen!=set(official) or not np.isfinite(values).all():raise ValueError("frozen S1 residual alignment mismatch")
    return values,{"path":str(path),"sha256":S1_SHA,"report_sha256":sha256_file(RESIDUAL_REPORT)}


def run(workspace:Path,report_path:Path)->dict[str,Any]:
    mean_report=json.loads(M1_MEAN_REPORT.read_text(encoding="utf-8"));mean_after=mean_report["measure_after"]
    if sha256_file(P90_REPORT)!="54e47b8ccff0e4161bd1acbbed32acd7552feadc16bdaab607df4efcd9df1d23":raise ValueError("p90 predecessor report changed")
    p90_report=json.loads(P90_REPORT.read_text(encoding="utf-8"));p90_after=p90_report["measure_after"]
    if p90_report["measure_before"]["mean_version_delta_r2"]!=float(mean_after["pooled"]["delta_r2"]):raise ValueError("mean-version predecessor does not match p90 report")
    p90_values=float(p90_after["pooled"]["delta_r2"]);p90_ci=p90_after["county_block_bootstrap"]["pooled_delta_r2_ci_95"]
    p90_positive=sum(float(p90_after["regional"][r]["delta_r2"])>0. for r in REGIONS)
    if abs(p90_values-(-0.0032012888075074607))>1e-12 or p90_positive!=1:raise ValueError("p90 predecessor results do not match agenda")
    if sha256_file(P90_MANIFEST)!="096eb587159246d5fab6a4f0f40be08f3a291ed56dcf92bbdceb27d8566abe5c":raise ValueError("frozen p90 county-fold manifest changed")
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)];geoids=[g for g,_ in official];regions=np.asarray([r for _,r in official],dtype=object)
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(regions.tolist())!=set(REGIONS):raise ValueError("official scored tract scope mismatch")
    mean_values,mean_mask,mean_file_sha=read_m1_emission(workspace/"ideas/M1.parquet",official)
    residual,residual_meta=load_residuals(workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet",official)
    config=load_config(CONFIG);feature=np.full(EXPECTED_N,np.nan);defined=np.zeros(EXPECTED_N,dtype=bool);length=np.zeros(EXPECTED_N);input_meta={};accessed=set()
    for region in REGIONS:
        idx=[i for i,(_,r) in enumerate(official) if r==region];inputs=ProvidedInputs(config,workspace,region,[official[i][0] for i in idx])
        values,mask,road_length,meta=aggregate_named_iqr(inputs)
        p90_meta=p90_report["provenance"]["provided_road_inputs_by_region"][region]
        for key in ("named_class_rows","named_class_OSM_record_id_tokens","matching_way_version_tokens","named_class_rows_with_at_least_one_matching_version","nonmatching_nonempty_OSM_record_ids","selected_versioned_named_segments_sha256","parsed_version_token_multiset_sha256"):
            if meta[key]!=p90_meta[key]:raise ValueError(f"M1 IQR input/parser differs from p90 on {region}: {key}")
        if not np.array_equal(mask,mean_mask[idx]):raise ValueError(f"IQR support differs from frozen mean support in {region}")
        if np.any(~np.isfinite(values[mask])) or np.any(values[mask]<0.) or np.any(road_length[mask]<=0.):raise ValueError("IQR feature values/length must be finite nonnegative")
        feature[idx]=values;defined[idx]=mask;length[idx]=road_length;input_meta[region]=meta;accessed.update(inputs.accessed["parquet_columns"])
    if int(defined.sum())!=EXPECTED_SUPPORT:raise ValueError("named-version IQR support must be 9,247")
    if len(accessed)!=16 or any(("overture-roads.parquet:" not in col and not col.endswith("-census-acs-housing:GEOID,geometry")) for col in accessed):
        raise ValueError(f"unexpected input layer/columns accessed: {sorted(accessed)!r}")
    paired=defined&np.isfinite(residual)
    if int(paired.sum())!=EXPECTED_SUPPORT:raise ValueError("paired IQR/S1 support mismatch")
    county=np.asarray([county_fips(g) for g in geoids],dtype=object);prediction=np.full(EXPECTED_N,np.nan);records=[];manifest_rows=[]
    for region in REGIONS:
        in_region=paired&(regions==region);county_list=sorted(set(county[in_region].tolist()))
        for held in county_list:
            test=in_region&(county==held);train=in_region&(county!=held)
            if not test.any() or train.sum()<3:raise ValueError(f"invalid county fold {region}/{held}")
            pred,fit=fit_one_term(residual[train],feature[train],feature[test]);prediction[test]=pred
            base=float(residual[test]@residual[test]);model=float(np.sum((residual[test]-pred)**2));training=sorted(set(county[train].tolist()))
            train_hash=hashlib.sha256("\n".join(training).encode()).hexdigest()
            records.append({"region":region,"heldout_county_fips":held,"heldout_n":int(test.sum()),"training_n":int(train.sum()),
              "training_county_count":len(training),"training_county_fips_sha256":train_hash,"baseline_sse":base,"model_sse":model,
              "delta_r2":(base-model)/base if base else None,"fit":fit})
            for i in np.flatnonzero(test):manifest_rows.append((geoids[i],region,held,train_hash,len(training),int(train.sum())))
    manifest_buffer=io.StringIO(newline="");writer=csv.writer(manifest_buffer,lineterminator="\n")
    writer.writerow(["GEOID","region","heldout_county_fips","training_county_fips_sha256","training_county_count","training_support_n"])
    writer.writerows(sorted(manifest_rows,key=lambda row:(row[1],row[0])))
    manifest_sha=hashlib.sha256(manifest_buffer.getvalue().encode()).hexdigest()
    if manifest_sha!=p90_after["fold_manifest"]["sha256"] or manifest_sha!=sha256_file(P90_MANIFEST):raise ValueError("IQR county folds differ from exact named-version p90 folds")
    if not np.isfinite(prediction[paired]).all() or np.any(np.isfinite(prediction[~paired])):raise AssertionError("OOF prediction support mismatch")
    pooled=score_zero_baseline(residual[paired],prediction[paired]);bootstrap=county_bootstrap(records)
    regional={}
    for region in REGIONS:
        use=paired&(regions==region);values=feature[use]
        regional[region]={**score_zero_baseline(residual[use],prediction[use]),"county_count":len(set(county[use].tolist())),
          "official_ids":int(np.sum(regions==region)),"version_iqr_defined_ids":int(np.sum(defined&(regions==region))),
          "versioned_named_road_length_m":float(length[use].sum()),
          "weighted_iqr_distribution":{"minimum":float(values.min()),"p25":float(np.quantile(values,.25)),"p50":float(np.quantile(values,.5)),
            "p75":float(np.quantile(values,.75)),"maximum":float(values.max())},
          "version_token_count":int(input_meta[region]["matching_way_version_tokens"]),
          "parsed_way_version_coverage":{"official_ids":int(np.sum(regions==region)),"version_iqr_defined_ids":int(np.sum(defined&(regions==region))),
            "nonmatching_nonempty_osm_record_ids":int(input_meta[region]["nonmatching_nonempty_OSM_record_ids"])}}
    positive=sum(regional[r]["delta_r2"]>0. for r in REGIONS);ci=bootstrap["pooled_delta_r2_ci_95"]
    passed=bool(pooled["delta_r2"]>=MIN_DELTA_R2 and ci[0]>MIN_CI_LOWER and positive>=MIN_POSITIVE_REGIONS)
    feature_hash=data_sha256(geoids,[r for _,r in official],feature,defined)
    report={"schema_version":1,"screen":"M1 length-weighted named-road version IQR county-held-out residual screen; descriptive-only unless fixed gates pass",
      "measure_before":{"named_road_mean":{"delta_r2":float(mean_after["pooled"]["delta_r2"]),
          "county_block_ci_95":mean_after["county_block_bootstrap"]["pooled_delta_r2_ci_95"],"positive_regions":sum(float(mean_after["regional"][r]["delta_r2"])>0 for r in REGIONS),
          "report_sha256":sha256_file(M1_MEAN_REPORT)},
        "named_road_version_p90":{"delta_r2":p90_values,"county_block_ci_95":p90_ci,"positive_regions":p90_positive,
          "report_sha256":sha256_file(P90_REPORT),"fold_manifest_sha256":sha256_file(P90_MANIFEST)},
        "requested_before_conditions_met":True},
      "method":{"feature":"Within each official tract, length-weighted empirical IQR (weighted Q75 minus weighted Q25) across the segment-level arithmetic mean of distinct matching supplied OSM way-version integers for named road classes. Segment clipped length in EPSG:5070 is the weight.",
        "version_parser":"Only provided Overture roads.sources.record_id values from source dataset OpenStreetMap fully matching ^w\\d+@(\\d+)$, case-insensitive; no external history.",
        "named_classes":sorted(NAMED_CLASSES),"segment_version":"Deduplicate integer versions within each road geometry, then arithmetic mean, exactly matching M1 named_mean_version and the preceding p90 screen.",
        "weighted_quantile":"Sort values stably and select the first segment value whose cumulative clipped length reaches q times total length; IQR=Q75-Q25. Fixed empirical convention; no tuning.",
        "geometry":"Provided road geometries intersected with provided official ACS tract polygons after transformation to EPSG:5070; use within-tract clipped lengths.",
        "outcome":"Pinned frozen S1 cross-fitted Queen/Rook disagreement residual; not rebuilt or refit.",
        "validation":"Fixed within-region leave-one-five-digit-county-FIPS-out folds, exactly matching the preceding named-road mean and p90 screens; same 9,247 paired support.",
        "correction":"One-term OLS with intercept; feature mean and population SD computed on outer-training tracts only; zero SD scale=1.",
        "baseline":"Zero correction of frozen S1 residual on identical support.","metric":"Pooled and regional held-county-out ΔR²=(baseline SSE-OOF model SSE)/baseline SSE; 2,000 paired county-block bootstrap replicates resampled within region.",
        "unregistered":True,"interpretation_limit":"Provided-feature residual association only, not target accuracy, causal evidence, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"version_iqr_defined_ids":int(defined.sum()),"paired_residual_support":int(paired.sum()),
        "undefined_version_iqr_ids":int((~defined).sum()),"feature_mask_sha256":hashlib.sha256(np.packbits(defined.astype(np.uint8),bitorder="little").tobytes()).hexdigest(),
        "feature_values_and_mask_sha256":feature_hash,"mean_feature_values_and_mask_sha256":data_sha256(geoids,[r for _,r in official],mean_values,mean_mask),
        "official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "versioned_named_road_length_m":float(length[paired].sum()),
        "version_token_counts_by_region":{r:int(input_meta[r]["matching_way_version_tokens"]) for r in REGIONS},
        "version_token_coverage_by_region":{r:{"official_ids":int(np.sum(regions==r)),"version_iqr_defined_ids":int(np.sum(defined&(regions==r))),
          "version_iqr_defined_fraction":float(np.sum(defined&(regions==r))/np.sum(regions==r)),
          "parsed_way_version_token_count":int(input_meta[r]["matching_way_version_tokens"]),
          "nonmatching_nonempty_OSM_record_ids":int(input_meta[r]["nonmatching_nonempty_OSM_record_ids"]),
          "named_class_rows":int(input_meta[r]["named_class_rows"]),"named_class_rows_with_version":int(input_meta[r]["named_class_rows_with_at_least_one_matching_version"]),
          "selected_versioned_named_segments_sha256":input_meta[r]["selected_versioned_named_segments_sha256"],
          "parsed_version_token_multiset_sha256":input_meta[r]["parsed_version_token_multiset_sha256"]} for r in REGIONS},
        "labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,"external_inputs_used":False,
        "removed_or_equivalent_references_read":False,"external_OSM_history_fetched":False,"leaderboard_feedback_used":False,"estimator_integration":False,"uploads":0},
      "measure_after":{"pooled":pooled,"county_block_bootstrap":bootstrap,"regional":regional,"county_fold_count":len(records),
        "fold_manifest":{"path":str(P90_MANIFEST),"sha256":manifest_sha,"rows":len(manifest_rows),"identical_to_named_version_p90_manifest":True},
        "county_folds":records},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_DELTA_R2,"bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,"minimum_positive_regions":MIN_POSITIVE_REGIONS},
      "acceptance":{"pooled_delta_r2_at_least_0_002":bool(pooled["delta_r2"]>=MIN_DELTA_R2),"county_block_ci_lower_above_zero":bool(ci[0]>MIN_CI_LOWER),
        "positive_region_count":positive,"positive_in_at_least_3_of_4_regions":bool(positive>=MIN_POSITIVE_REGIONS),"screen_pass":passed},
      "provenance":{"M1_mean_emission":{"path":str(workspace/"ideas/M1.parquet"),"feature_sha256":M1_EMISSION_SHA,"file_sha256":mean_file_sha},
        "M1_mean_report":{"path":str(M1_MEAN_REPORT),"sha256":sha256_file(M1_MEAN_REPORT)},
        "M1_p90_report":{"path":str(P90_REPORT),"sha256":sha256_file(P90_REPORT)},"M1_p90_script":{"path":str(P90_SOURCE),"sha256":sha256_file(P90_SOURCE)},
        "M1_source_definition":{"path":str(M1_SOURCE),"sha256":sha256_file(M1_SOURCE)},"frozen_S1_residual":residual_meta,
        "provided_road_inputs_by_region":input_meta,"config":{"path":str(CONFIG),"sha256":sha256_file(CONFIG)},
        "analysis_script_sha256":sha256_file(Path(__file__).resolve()),"bootstrap_seed":BOOTSTRAP_SEED,"bootstrap_replicates":BOOTSTRAP_REPLICATES},
      "interpretation":"The named-road version IQR residual screen failed one or more fixed gates; retain it as diagnostic-only and do not integrate." if not passed else
        "The named-road version IQR met all fixed diagnostic gates; this is not target-accuracy evidence or authorization to integrate."}
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""));p.add_argument("--out",default="reports/m1-named-version-iqr-residual.json")
    args=p.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():p.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],"pooled_ci":report["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"],
      "regional":{r:v["delta_r2"] for r,v in report["measure_after"]["regional"].items()},"token_coverage":report["scope"]["version_token_coverage_by_region"],"acceptance":report["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
