#!/usr/bin/env python3
"""Audit provided Overture OSM road record-id version tokens by clipped segment length."""
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
import shapely
from pyproj import Transformer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa: E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379;NAMED_CLASSES=frozenset({"motorway","trunk","primary","secondary"});ROAD_CLASSES=NAMED_CLASSES|{"tertiary"}
WAY_VERSION=re.compile(r"^w\d+@(\d+)$",re.IGNORECASE)
TRANSFORMER=Transformer.from_crs("EPSG:4326","EPSG:5070",always_xy=True)
MIN_PARSE_COVERAGE=.95
BEFORE={
 "mean":{"delta_r2":0.0006046291588994236,"pooled_ci":[-0.020082099195197874,0.011336126908809963],"positive_regions":2},
 "edit_depth":{"delta_r2":-0.007231373974276452,"pooled_ci":[-0.024539646027405007,-0.0004478414381743186],"positive_regions":0}}
MEAN_REPORT=ROOT/"reports/m1-named-road-mean-version-residual.json"
DEPTH_REPORT=ROOT/"reports/m1-road-edit-depth-residual-screen.json"
M1_SOURCE=ROOT/"src/bias60/ideas/m.py"
CONFIG=ROOT/"config/bias60-harness-preregistered.json"


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def parse_way_version(record_id:Any)->int|None:
    """Return one integer only for an exact supplied OSM `w<digits>@<version>` ID."""
    value=str(record_id or "")
    match=WAY_VERSION.fullmatch(value)
    return int(match.group(1)) if match else None


def redact_token_shape(record_id:Any)->str:
    """Keep token syntax while replacing every alphanumeric character with its class."""
    value=str(record_id or "")
    chars=[]
    for index,char in enumerate(value[:96]):
        if index==0 and char.casefold()=="w":chars.append("w")
        else:chars.append("#" if char.isdigit() else "A" if char.isalpha() else char)
    return "".join(chars)+("…" if len(value)>96 else "")


def inspect_sources(sources:Any)->dict[str,Any]:
    osm_memberships=non_osm_memberships=missing_ids=0;records=[]
    for source in sources or []:
        if not isinstance(source,dict):continue
        dataset=str(source.get("dataset") or "").casefold()
        if dataset!="openstreetmap":
            non_osm_memberships+=1;continue
        osm_memberships+=1
        record_id=str(source.get("record_id") or "")
        if not record_id:
            missing_ids+=1;continue
        records.append(record_id)
    parsed=[parse_way_version(record) for record in records]
    versions=sorted(set(value for value in parsed if value is not None))
    malformed=[record for record,value in zip(records,parsed) if value is None]
    all_valid=bool(records) and not malformed
    unique=all_valid and len(versions)==1
    return {"osm_memberships":osm_memberships,"non_osm_memberships":non_osm_memberships,"missing_ids":missing_ids,
      "records":records,"versions":versions,"malformed":malformed,"all_valid":all_valid,"unique_version":unique,
      "has_record_id":bool(records),"has_valid":bool(versions),"has_malformed":bool(malformed)}


def _new_stats()->dict[str,Any]:
    return {"road_rows":0,"geometry_rows":0,"clipped_segments":0,"clipped_length_m":0.,
      "osm_source_segments":0,"osm_source_length_m":0.,"eligible_record_id_segments":0,"eligible_record_id_length_m":0.,
      "segments_with_any_valid_token":0,"length_with_any_valid_token_m":0.,
      "segments_all_tokens_valid":0,"length_all_tokens_valid_m":0.,
      "segments_single_unambiguous_version":0,"length_single_unambiguous_version_m":0.,
      "segments_multiple_distinct_versions":0,"length_multiple_distinct_versions_m":0.,
      "segments_with_malformed_tokens":0,"length_with_malformed_tokens_m":0.,
      "segments_without_osm_record_id":0,"length_without_osm_record_id_m":0.,
      "osm_source_memberships":0,"non_osm_source_memberships":0,"missing_osm_record_id_memberships":0,
      "valid_record_id_token_count":0,"malformed_record_id_token_count":0,
      "version_token_count_by_integer":collections.Counter(),"version_token_length_by_integer_m":collections.Counter(),
      "malformed_shape_count":collections.Counter(),"malformed_shape_length_m":collections.Counter(),
      "multi_version_shape_count":collections.Counter(),"multi_version_shape_length_m":collections.Counter()}


def add_segment(stats:dict[str,Any],info:dict[str,Any],length_m:float)->None:
    if length_m<=0. or not np.isfinite(length_m):return
    stats["clipped_segments"]+=1;stats["clipped_length_m"]+=length_m
    stats["osm_source_memberships"]+=info["osm_memberships"]
    stats["non_osm_source_memberships"]+=info["non_osm_memberships"]
    stats["missing_osm_record_id_memberships"]+=info["missing_ids"]
    if info["osm_memberships"]:
        stats["osm_source_segments"]+=1;stats["osm_source_length_m"]+=length_m
    if not info["has_record_id"]:
        if info["osm_memberships"]:
            stats["segments_without_osm_record_id"]+=1;stats["length_without_osm_record_id_m"]+=length_m
        return
    stats["eligible_record_id_segments"]+=1;stats["eligible_record_id_length_m"]+=length_m
    if info["has_valid"]:stats["segments_with_any_valid_token"]+=1;stats["length_with_any_valid_token_m"]+=length_m
    if info["all_valid"]:stats["segments_all_tokens_valid"]+=1;stats["length_all_tokens_valid_m"]+=length_m
    if info["unique_version"]:stats["segments_single_unambiguous_version"]+=1;stats["length_single_unambiguous_version_m"]+=length_m
    if info["has_valid"] and len(info["versions"])>1:
        stats["segments_multiple_distinct_versions"]+=1;stats["length_multiple_distinct_versions_m"]+=length_m
        for record in info["records"]:
            version=parse_way_version(record)
            if version is not None:stats["multi_version_shape_count"][redact_token_shape(record)]+=1;stats["multi_version_shape_length_m"][redact_token_shape(record)]+=length_m
    if info["has_malformed"]:
        stats["segments_with_malformed_tokens"]+=1;stats["length_with_malformed_tokens_m"]+=length_m
        for record in info["malformed"]:
            shape=redact_token_shape(record);stats["malformed_shape_count"][shape]+=1;stats["malformed_shape_length_m"][shape]+=length_m
    for record in info["records"]:
        version=parse_way_version(record)
        if version is None:stats["malformed_record_id_token_count"]+=1
        else:
            stats["valid_record_id_token_count"]+=1
            stats["version_token_count_by_integer"][version]+=1
            stats["version_token_length_by_integer_m"][version]+=length_m


def _feed_hash(hasher:Any,value:bytes|str)->None:
    data=value.encode("utf-8") if isinstance(value,str) else value
    hasher.update(len(data).to_bytes(8,"big"));hasher.update(data)


def scan_region(inputs:ProvidedInputs)->dict[str,Any]:
    tracts=shapely.transform(inputs.tract_geometries(),TRANSFORMER.transform,interleaved=False);tree=shapely.STRtree(tracts)
    stats={name:_new_stats() for name in ("named","tertiary")};scanned=0;input_digest=hashlib.sha256();segment_digest=hashlib.sha256()
    for batch in inputs.iter_layer_batches("roads",["class","sources","geometry"]):
        classes=batch["class"].to_pylist();sources=batch["sources"].to_pylist();wkbs=batch["geometry"].to_pylist();scanned+=len(classes)
        eligible=[];eligible_wkb=[];eligible_class=[];eligible_info=[]
        for klass,source,wkb in zip(classes,sources,wkbs):
            group="named" if str(klass or "").casefold() in NAMED_CLASSES else "tertiary" if str(klass or "").casefold()=="tertiary" else None
            if group is None:continue
            current=stats[group];current["road_rows"]+=1
            if wkb is None:continue
            current["geometry_rows"]+=1;info=inspect_sources(source)
            _feed_hash(input_digest,group);_feed_hash(input_digest,wkb)
            for source_row in source or []:
                if isinstance(source_row,dict):
                    _feed_hash(input_digest,str(source_row.get("dataset") or ""))
                    _feed_hash(input_digest,str(source_row.get("record_id") or ""))
            eligible.append(len(eligible));eligible_wkb.append(wkb);eligible_class.append(group);eligible_info.append(info)
        for start in range(0,len(eligible_wkb),40000):
            end=min(start+40000,len(eligible_wkb));lines=shapely.from_wkb(eligible_wkb[start:end])
            metric_lines=shapely.transform(lines,TRANSFORMER.transform,interleaved=False)
            pairs=tree.query(metric_lines,predicate="intersects")
            if not pairs.size:continue
            pieces=shapely.intersection(metric_lines[pairs[0]],tracts[pairs[1]]);lengths=shapely.length(pieces)
            per_line=np.bincount(pairs[0],weights=np.asarray(lengths,dtype=np.float64),minlength=end-start)
            for j,length in enumerate(per_line.tolist()):
                if length<=0.:continue
                k=start+j;group=eligible_class[k];info=eligible_info[k]
                add_segment(stats[group],info,float(length))
                _feed_hash(segment_digest,group);_feed_hash(segment_digest,format(float(length),".17g"))
                _feed_hash(segment_digest,";".join(str(v) for v in info["versions"]))
                _feed_hash(segment_digest,"|".join(sorted(redact_token_shape(x) for x in info["malformed"])))
    result={}
    for group,values in stats.items():
        c=dict(values)
        for key,value in list(c.items()):
            if isinstance(value,collections.Counter):c[key]=dict(sorted(value.items(),key=lambda item:(str(item[0]))))
        result[group]=c
    return {"rows_scanned":scanned,"source_stream_sha256":input_digest.hexdigest(),"clipped_segment_summary_sha256":segment_digest.hexdigest(),"by_class":result}


def _ratio(numerator:float,denominator:float)->float|None:
    return numerator/denominator if denominator>0. else None


def summarize(stats:dict[str,Any])->dict[str,Any]:
    totals=_new_stats()
    sum_keys=[k for k,v in totals.items() if isinstance(v,(int,float))]
    for item in stats.values():
        for key in sum_keys:totals[key]+=item[key]
        for key in ("version_token_count_by_integer","version_token_length_by_integer_m","malformed_shape_count","malformed_shape_length_m","multi_version_shape_count","multi_version_shape_length_m"):
            totals[key].update(item[key])
    return _metrics(totals)


def _metrics(s:dict[str,Any])->dict[str,Any]:
    eligible=float(s["osm_source_length_m"]);rid=float(s["eligible_record_id_length_m"])
    return {"clipped_segment_count":int(s["clipped_segments"]),"clipped_road_length_m":float(s["clipped_length_m"]),
      "osm_source_segment_count":int(s["osm_source_segments"]),"osm_source_road_length_m":eligible,
      "osm_source_length_share_of_all_named_tertiary":_ratio(eligible,float(s["clipped_length_m"])),
      "OSM_segments_without_nonempty_record_id":int(s["segments_without_osm_record_id"]),"OSM_length_without_nonempty_record_id_m":float(s["length_without_osm_record_id_m"]),
      "eligible_nonempty_record_id_segment_count":int(s["eligible_record_id_segments"]),"eligible_nonempty_record_id_length_m":rid,
      "record_id_coverage_within_OSM_source_length":_ratio(rid,eligible),
      "any_valid_way_token_segment_count":int(s["segments_with_any_valid_token"]),"any_valid_way_token_length_m":float(s["length_with_any_valid_token_m"]),
      "any_valid_way_token_coverage_of_OSM_source_length":_ratio(float(s["length_with_any_valid_token_m"]),eligible),
      "all_record_id_tokens_valid_segment_count":int(s["segments_all_tokens_valid"]),"all_record_id_tokens_valid_length_m":float(s["length_all_tokens_valid_m"]),
      "all_record_id_tokens_valid_coverage_of_eligible_length":_ratio(float(s["length_all_tokens_valid_m"]),eligible),
      "single_distinct_version_segment_count":int(s["segments_single_unambiguous_version"]),"single_distinct_version_length_m":float(s["length_single_unambiguous_version_m"]),
      "single_distinct_version_coverage_of_eligible_length":_ratio(float(s["length_single_unambiguous_version_m"]),eligible),
      "multiple_distinct_versions_segment_count":int(s["segments_multiple_distinct_versions"]),"multiple_distinct_versions_length_m":float(s["length_multiple_distinct_versions_m"]),
      "multiple_distinct_versions_coverage_of_eligible_length":_ratio(float(s["length_multiple_distinct_versions_m"]),eligible),
      "segments_with_malformed_token_count":int(s["segments_with_malformed_tokens"]),"malformed_token_segment_length_m":float(s["length_with_malformed_tokens_m"]),
      "malformed_token_coverage_of_eligible_length":_ratio(float(s["length_with_malformed_tokens_m"]),eligible),
      "valid_record_id_token_count":int(s["valid_record_id_token_count"]),"malformed_record_id_token_count":int(s["malformed_record_id_token_count"]),
      "version_distribution_by_integer":{"weighting_note":"Token count counts individual OSM source record_id values; clipped length is repeated once for each matching record token on a segment and is therefore intentionally non-additive when a segment has multiple tokens.",
        "record_token_count":{str(k):int(v) for k,v in sorted(s["version_token_count_by_integer"].items())},
        "record_token_clipped_length_m":{str(k):float(v) for k,v in sorted(s["version_token_length_by_integer_m"].items())}},
      "malformed_token_shapes_top20":_top_shapes(s["malformed_shape_count"],s["malformed_shape_length_m"]),
      "multi_version_token_shapes_top20":_top_shapes(s["multi_version_shape_count"],s["multi_version_shape_length_m"])}


def _top_shapes(counts:dict[str,int],lengths:dict[str,float])->list[dict[str,Any]]:
    return [{"redacted_shape":shape,"token_count":int(counts[shape]),"source_segment_length_m":float(lengths[shape])}
      for shape in sorted(counts,key=lambda x:(-counts[x],x))[:20]]


def _before_report()->dict[str,Any]:
    mean=json.loads(MEAN_REPORT.read_text(encoding="utf-8"));depth=json.loads(DEPTH_REPORT.read_text(encoding="utf-8"))
    md=float(mean["measure_after"]["pooled"]["delta_r2"]);mi=mean["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"]
    mp=sum(float(mean["measure_after"]["regional"][r]["delta_r2"])>0 for r in REGIONS)
    dd=float(depth["measure_after"]["pooled"]["delta_r2"]);di=depth["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"]
    dp=sum(float(depth["measure_after"]["regional"][r]["delta_r2"])>0 for r in REGIONS)
    if abs(md-BEFORE["mean"]["delta_r2"])>1e-12 or mp!=2 or md>=.002:raise ValueError("named-road mean predecessor metrics differ from dispatched before-state")
    if abs(dd-BEFORE["edit_depth"]["delta_r2"])>1e-12 or dp!=0 or not(di[0]<0 and di[1]<0):raise ValueError("road edit-depth predecessor metrics differ from dispatched before-state")
    return {"named_road_mean":{"delta_r2":md,"county_block_ci_95":mi,"positive_regions":mp,"report_sha256":sha256_file(MEAN_REPORT)},
      "low_version_tertiary_share":{"delta_r2":dd,"county_block_ci_95":di,"positive_regions":dp,"report_sha256":sha256_file(DEPTH_REPORT)}}


def run(workspace:Path,out:Path)->dict[str,Any]:
    before=_before_report();official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):raise ValueError("official GEOID/region scope mismatch")
    official_hash=hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    config=load_config(CONFIG);region_meta={};region_metrics={};all_pass=True
    for region in REGIONS:
        ids=[g for g,r in official if r==region];inputs=ProvidedInputs(config,workspace,region,ids)
        scan=scan_region(inputs);metrics={c:_metrics(s) for c,s in scan["by_class"].items()}
        metrics["named_and_tertiary_combined"]=summarize(scan["by_class"])
        eligible=metrics["named_and_tertiary_combined"]["osm_source_road_length_m"]
        numerator=metrics["named_and_tertiary_combined"]["single_distinct_version_length_m"]
        coverage=_ratio(numerator,eligible);passed=coverage is not None and coverage>=MIN_PARSE_COVERAGE;all_pass=all_pass and passed
        region_metrics[region]={"official_ids":len(ids),"parse_consistency_gate":{"eligible_length_definition":"all clipped named and tertiary road length with at least one OpenStreetMap source membership, including segments without a nonempty record_id",
          "numerator":"clipped length on OSM-sourced segments with at least one nonempty record_id, every nonempty OSM ID fullmatching the exact M1 way-version parser, and exactly one distinct parsed integer version. Missing, nonmatching, or multiple distinct versions are excluded from this conservative unambiguous-support gate.",
          "coverage":coverage,"minimum":MIN_PARSE_COVERAGE,"passed":bool(passed)},"classes":metrics}
        region_meta[region]={"rows_scanned":scan["rows_scanned"],"roads_source_stream_sha256":scan["source_stream_sha256"],
          "clipped_segment_summary_sha256":scan["clipped_segment_summary_sha256"],"accessed_layers":inputs.accessed["layers"],
          "accessed_parquet_columns":inputs.accessed["parquet_columns"]}
    flattened={region:region_metrics[region]["classes"]["named_and_tertiary_combined"] for region in REGIONS}
    result_digest=hashlib.sha256(json.dumps(region_metrics,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    report={"schema_version":1,"audit":"Provided Overture M1 record_id version-token parser and length support audit",
      "measure_before":before,
      "method":{"input":"Only challenge-provided Overture roads fields class, sources, and geometry, plus the official provided ACS tract polygons needed for clipping; no OSM history or outside data.",
        "classes":{"named":sorted(NAMED_CLASSES),"tertiary":["tertiary"]},
        "eligible_length":"Road lines in the registered classes clipped to official scored tract polygons, then restricted for parsing denominators to segments with an OpenStreetMap source membership. Report total class road length and source coverage separately.",
        "version_parser":"The independently unit-tested parser fullmatches the untrimmed stringified OSM sources.record_id against ^w\\d+@(\\d+)$ case-insensitively and returns the captured integer only on full match; this mirrors the existing M1 parser. Non-OSM IDs are not parsed.",
        "segment_consistency":"The acceptance criterion is conservative: a segment is unambiguous only when it has at least one nonempty OSM record_id, every nonempty OSM ID fullmatches the exact M1 parser, and all parsed tokens collapse to one distinct integer version. M1 itself deterministically averages multiple parsed versions, but that aggregation does not count as a unique unambiguous segment version for this audit. The more permissive any-valid and all-token-valid support fractions are reported separately.",
        "geometry":"Transform provided road and official tract geometries from EPSG:4326 to EPSG:5070, intersect, and weight each source segment by the sum of its clipped length inside official tracts.",
        "redaction":"No raw record_id values are written. Malformed and multiple-version examples are reported only as character-shape patterns with digits mapped to # and letters mapped to A.",
        "hashing":"SHA-256 digests cover class/source record_id/WKB input streams, clipped segment summaries, official GEOID/region ordering, and canonical audit metrics.",
        "interpretation_limit":"Parser support is data-quality evidence only; it does not validate M1's association with the hidden target, accuracy, causality, or estimator promotion."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":official_hash,"classes_included":sorted(ROAD_CLASSES),
        "no_labels_or_sample_scores_read":True,"target_reconstruction":False,"external_inputs_used":False,"OSM_history_fetched":False,
        "removed_or_equivalent_references_read":False,"leaderboard_feedback_used":False,"uploads":0,"estimator_or_registry_changed":False},
      "measure_after":{"regional":region_metrics,"canonical_audit_metrics_sha256":result_digest,
        "all_regions_meet_95_percent_unambiguous_parse_coverage":all_pass},
      "acceptance":{"minimum_unambiguous_parsed_version_length_fraction":MIN_PARSE_COVERAGE,
        "all_four_regions_at_or_above_95_percent":all_pass,
        "m1_version_parser_input_support_pass":all_pass,"estimator_integration_authorized":False},

      "provenance":{"provided_road_source_stream_by_region":region_meta,
        "M1_source":{"path":str(M1_SOURCE),"sha256":sha256_file(M1_SOURCE)},
        "preregistered_config":{"path":str(CONFIG),"sha256":sha256_file(CONFIG)},
        "official_id_list_source":"official provided per-region sample GEOID/region list; score fields not read",
        "analysis_script_sha256":sha256_file(Path(__file__).resolve())},
      "interpretation":"Every region passed the 95% unambiguous parser-support gate. This establishes parser-input support only; previously failed M1 residual screens remain diagnostic-only." if all_pass else
        "At least one region failed the 95% unambiguous parser-support gate; treat M1 version statistics as unsupported and do not use them in an estimator."}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""));parser.add_argument("--out",default="reports/m1-record-id-version-parser-audit.json")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out)
    print(json.dumps({"measure_after":report["measure_after"],"acceptance":report["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
