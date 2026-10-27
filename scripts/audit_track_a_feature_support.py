#!/usr/bin/env python3
"""Attribute frozen Track A candidate support/missingness to registered feature families."""
from __future__ import annotations
import argparse,csv,hashlib,json,math,os
from pathlib import Path
from typing import Any
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
FINAL_TWO=ROOT/"reports/final-two.json"
V2_MASK_REPORT=ROOT/"reports/v2-defined-mask-audit.json"
V2_MASK_CONFIG=ROOT/"config/v2-component-mask-audit-preregistered.json"
A_CANDIDATES=("A1_physical_equal","A2_spectral","A3_dawid_skene")
COHORT=(*A_CANDIDATES,"V2_model")
REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
COMPONENTS=("B","Est","F","R")
MANIFEST_PATHS={c:f"reports/candidates/{c}_manifest.json" for c in A_CANDIDATES}


def sha256(path:Path)->str:
 h=hashlib.sha256()
 with path.open("rb") as f:
  for block in iter(lambda:f.read(1<<20),b""):h.update(block)
 return h.hexdigest()


def skip_json_value(text:str,pos:int)->int:
 """Lexically skip ignored JSON fields so leaderboard scores are never decoded."""
 decoder=json.JSONDecoder();n=len(text)
 while pos<n and text[pos].isspace():pos+=1
 if pos>=n:raise ValueError("unexpected end of JSON")
 char=text[pos]
 if char=='"':return decoder.raw_decode(text,pos)[1]
 if char in "[{":
  closing="]" if char=="[" else "}";pos+=1
  while True:
   while pos<n and (text[pos].isspace() or text[pos]==","):pos+=1
   if pos>=n:raise ValueError("unterminated JSON container")
   if text[pos]==closing:return pos+1
   if char=="{":
    key,pos=decoder.raw_decode(text,pos)
    if not isinstance(key,str):raise ValueError("invalid JSON object key")
    while pos<n and text[pos].isspace():pos+=1
    if pos>=n or text[pos] != ":":raise ValueError("expected JSON colon")
    pos=skip_json_value(text,pos+1)
   else:pos=skip_json_value(text,pos)
 while pos<n and text[pos] not in ",]} \t\r\n":pos+=1
 return pos


def project_object(text:str,pos:int,wanted:set[str])->tuple[dict[str,Any],int]:
 dec=json.JSONDecoder()
 while pos<len(text) and text[pos].isspace():pos+=1
 if pos>=len(text) or text[pos]!="{":raise ValueError("expected registration object")
 pos+=1;out={}
 while True:
  while pos<len(text) and (text[pos].isspace() or text[pos]==","):pos+=1
  if pos>=len(text):raise ValueError("unterminated registration")
  if text[pos]=="}":return out,pos+1
  key,pos=dec.raw_decode(text,pos)
  while pos<len(text) and text[pos].isspace():pos+=1
  if not isinstance(key,str) or pos>=len(text) or text[pos] != ":":raise ValueError("malformed registration")
  pos+=1
  while pos<len(text) and text[pos].isspace():pos+=1
  if key in wanted:out[key],pos=dec.raw_decode(text,pos)
  else:pos=skip_json_value(text,pos)


def load_final_two_registrations(path:Path,expected_ids:tuple[str,...]=COHORT)->tuple[dict[str,dict[str,Any]],str]:
 text=path.read_text(encoding="utf-8");dec=json.JSONDecoder();pos=0
 while pos<len(text) and text[pos].isspace():pos+=1
 if pos>=len(text) or text[pos]!="{":raise ValueError("final-two must be a JSON object")
 pos+=1;rows=None
 fields={"candidate_id","artifact","sha256","rows","columns","preregistered_card_path","preregistered_card_sha256","parameter_provenance"}
 while pos<len(text):
  while pos<len(text) and (text[pos].isspace() or text[pos]==","):pos+=1
  if pos>=len(text) or text[pos]=="}":break
  key,pos=dec.raw_decode(text,pos)
  while pos<len(text) and text[pos].isspace():pos+=1
  if not isinstance(key,str) or pos>=len(text) or text[pos] != ":":raise ValueError("malformed final-two")
  pos+=1
  while pos<len(text) and text[pos].isspace():pos+=1
  if key!="candidate_registrations":pos=skip_json_value(text,pos);continue
  if pos>=len(text) or text[pos]!="[":raise ValueError("candidate_registrations not an array")
  pos+=1;rows=[]
  while pos<len(text):
   while pos<len(text) and (text[pos].isspace() or text[pos]==","):pos+=1
   if pos>=len(text):raise ValueError("unterminated registrations")
   if text[pos]=="]":pos+=1;break
   row,pos=project_object(text,pos,fields);rows.append(row)
  break
 if rows is None:raise ValueError("final-two candidate registrations missing")
 result={}
 for row in rows:
  cid=row.get("candidate_id")
  if cid not in expected_ids:continue
  if cid in result:raise ValueError(f"duplicate cohort registration: {cid}")
  result[cid]=row
 if set(result)!=set(expected_ids):raise ValueError(f"cohort mismatch: {sorted(result)}")
 return result,sha256(path)


def read_official_ids(workspace:Path)->tuple[list[str],dict[str,str],dict[str,list[str]]]:
 ids=[];region_of={};by_region={}
 for region in REGIONS:
  path=workspace/"samples"/f"{region}-sample-submission.csv"
  with path.open(newline="",encoding="utf-8-sig") as f:
   reader=csv.reader(f);header=next(reader)
   if "GEOID" not in header:raise ValueError(f"{path} has no GEOID column")
   ix=header.index("GEOID");current=[row[ix] for row in reader]
  if not current or len(current)!=len(set(current)):raise ValueError(f"{region}: duplicate/empty official ids")
  by_region[region]=current
  for g in current:
   if g in region_of:raise ValueError(f"duplicate official GEOID {g}")
   ids.append(g);region_of[g]=region
 if len(ids)!=9379:raise ValueError(f"expected 9,379 official ids, found {len(ids)}")
 return ids,region_of,by_region


def mask_hash(ids:list[str],mask:set[str])->str:
 return hashlib.sha256(bytes(1 if g in mask else 0 for g in ids)).hexdigest()


def mask_stats(region_ids:list[str],mask:set[str])->dict[str,Any]:
 n=len(region_ids);defined=sum(g in mask for g in region_ids);missing=n-defined
 return {"official_ids":n,"defined_ids":defined,"missing_ids":missing,"defined_fraction":defined/n if n else None,
         "missing_fraction":missing/n if n else None,"defined_mask_sha256_official_order":mask_hash(region_ids,mask)}


def pair_stats(region_ids:list[str],left:set[str],right:set[str])->dict[str,Any]:
 a=left.intersection(region_ids);b=right.intersection(region_ids);both=a&b;union=a|b;n=len(region_ids)
 return {"left_defined_ids":len(a),"right_defined_ids":len(b),"both_defined_ids":len(both),
         "union_defined_ids":len(union),"both_defined_fraction_of_region":len(both)/n if n else None,
         "left_covered_by_right_fraction":len(both)/len(a) if a else None,
         "right_covered_by_left_fraction":len(both)/len(b) if b else None,
         "jaccard_intersection_over_union":len(both)/len(union) if union else None}


def load_idea_mask(path:Path,expected_sha:str,idea:str,component:str,ids:list[str],region_of:dict[str,str])->tuple[set[str],dict[str,Any]]:
 if sha256(path)!=expected_sha:raise ValueError(f"{idea}: parquet hash mismatch")
 table=pq.read_table(path,columns=["GEOID","region","idea","family","component","channel","value","defined"])
 seen={};families=set();placeholder_violations=0;unexpected=0
 for row in table.to_pylist():
  if row["idea"]!=idea:raise ValueError(f"unexpected idea id in {path}")
  g=str(row["GEOID"])
  if g not in region_of:unexpected+=1;continue
  if row["region"]!=region_of[g]:raise ValueError(f"{idea}/{g}: region mismatch")
  if row["channel"]!="gap" or row["component"]!=component:continue
  if g in seen:raise ValueError(f"{idea}/{g}: duplicate registered gap emission")
  if not isinstance(row["defined"],bool):raise ValueError(f"{idea}/{g}: defined flag is not boolean")
  value=float(row["value"])
  if not math.isfinite(value):raise ValueError(f"{idea}/{g}: non-finite gap emission")
  seen[g]=row["defined"];families.add(str(row["family"]))
  if not row["defined"] and value!=0:placeholder_violations+=1
 if set(seen)!=set(ids):
  raise ValueError(f"{idea}: missing={len(set(ids)-set(seen))}, extra={len(set(seen)-set(ids))} gap rows")
 if unexpected:raise ValueError(f"{idea}: {unexpected} nonofficial rows in frozen idea artifact")
 if len(families)!=1:raise ValueError(f"{idea}: expected one declared estimator family, got {families}")
 return {g for g,d in seen.items() if d},{"family":next(iter(families)),"rows":len(seen),"missing_emission_rows":0,
  "undefined_placeholder_nonzero_rows":placeholder_violations,"unexpected_nonofficial_rows":unexpected}


def load_witness_mask(path:Path,expected_sha:str,idea:str,ids:list[str],region_of:dict[str,str])->tuple[set[str],dict[str,Any]]:
 if sha256(path)!=expected_sha:raise ValueError(f"A3 witness {idea}: parquet hash mismatch")
 table=pq.read_table(path,columns=["GEOID","region","idea","component","channel","value","defined"])
 rows={};unexpected=0
 for row in table.to_pylist():
  if row["idea"]!=idea:raise ValueError(f"unexpected witness idea in {path}")
  g=str(row["GEOID"])
  if g not in region_of:unexpected+=1;continue
  if row["region"]!=region_of[g]:raise ValueError(f"witness {idea}/{g}: region mismatch")
  if row["channel"]!="covariate":continue
  if not isinstance(row["defined"],bool):raise ValueError(f"witness {idea}/{g}: defined flag is not boolean")
  rows.setdefault(g,[]).append((str(row["component"]),row["defined"]))
 if set(rows)!=set(ids):raise ValueError(f"witness {idea}: missing official covariate rows")
 if unexpected:raise ValueError(f"witness {idea}: {unexpected} nonofficial rows in frozen artifact")
 # Match Track A's frozen witness rule: prefer its F-component covariate, else first emission.
 chosen={g:next((defined for component,defined in emissions if component=="F"),emissions[0][1]) for g,emissions in rows.items()}
 return {g for g,d in chosen.items() if d},{"rows":len(rows),"missing_emission_rows":0,"unexpected_nonofficial_rows":unexpected,
   "duplicate_secondary_covariate_rows_resolved_by_frozen_component_preference":sum(len(v)-1 for v in rows.values())}


def summarize_mask_by_region(ids_by_region:dict[str,list[str]],mask:set[str])->dict[str,Any]:
 return {region:mask_stats(ids,mask) for region,ids in ids_by_region.items()}


def load_csv_flag_mask(path:Path,expected_sha:str,ids:list[str],flag_column:str)->tuple[set[str],dict[str,Any]]:
 if sha256(path)!=expected_sha:raise ValueError(f"source mask artifact hash mismatch: {path}")
 seen={};row_count=0
 with path.open(newline="",encoding="utf-8-sig") as f:
  reader=csv.DictReader(f)
  if not reader.fieldnames or not {"GEOID",flag_column}<=set(reader.fieldnames):raise ValueError(f"{path}: missing mask fields")
  for row in reader:
   row_count+=1;g=row["GEOID"]
   if g in seen:raise ValueError(f"{path}: duplicate GEOID {g}")
   flag=row[flag_column]
   if flag not in {"0","1"}:raise ValueError(f"{path}/{g}: invalid {flag_column}={flag}")
   seen[g]=flag=="1"
 if set(seen)!=set(ids):raise ValueError(f"{path}: exact official coverage mismatch")
 return {g for g,d in seen.items() if d},{"rows":row_count,"flag_column":flag_column,"missing_emission_rows":0}


def candidate_pairwise(region_ids:list[str],masks:dict[str,set[str]])->dict[str,Any]:
 names=sorted(masks);pairs={}
 for i,left in enumerate(names):
  for right in names[i+1:]:pairs[f"{left}__{right}"]=pair_stats(region_ids,masks[left],masks[right])
 return pairs


def run(workspace:Path,out:Path)->dict[str,Any]:
 ids,region_of,ids_by_region=read_official_ids(workspace)
 registrations,final_two_hash=load_final_two_registrations(FINAL_TWO)
 a_data={};candidate_data={};candidate_file_hashes={};all_idea_ids=set();global_family_accounting={}
 # Candidate CSVs are hash-checked only; no scores are parsed or used.
 for cid,registration in registrations.items():
  artifact=ROOT/registration["artifact"]
  if sha256(artifact)!=registration["sha256"]:raise ValueError(f"{cid}: frozen candidate output hash mismatch")
  if registration["rows"]!=9794 or registration["columns"]!=["GEOID","transport_gap","building_gap","poi_gap","coverage_gap_score"]:
   raise ValueError(f"{cid}: unexpected frozen candidate artifact contract")
  candidate_file_hashes[cid]={"artifact":registration["artifact"],"sha256":registration["sha256"],"rows":registration["rows"],"columns":registration["columns"]}

 for cid in A_CANDIDATES:
  registration=registrations[cid];prov=registration["parameter_provenance"]
  manifest_path=ROOT/MANIFEST_PATHS[cid];manifest_hash=sha256(manifest_path)
  if manifest_hash!=prov.get("manifest_sha256"):raise ValueError(f"{cid}: candidate manifest hash differs from final-two")
  manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
  selected=manifest.get("selected_gap_ideas")
  if selected!=prov.get("selected_gap_ideas"):raise ValueError(f"{cid}: selected idea list differs from final-two")
  weights=manifest.get("diagnostics",{}).get("family_weights",{})
  if weights!=prov.get("diagnostics",{}).get("family_weights"):raise ValueError(f"{cid}: family weights differ from final-two")
  if manifest.get("sha256")!=registration["sha256"] or manifest.get("artifact")!=registration["artifact"]:
   raise ValueError(f"{cid}: candidate artifact does not match its manifest")
  idea_hashes=manifest.get("idea_input_sha256",{});component_families={};idea_masks={};idea_stats={};family_by_component={}
  for component in COMPONENTS:
   selected_ids=selected.get(component,[])
   if not selected_ids:raise ValueError(f"{cid}/{component}: empty selected list")
   fam_members={}
   for idea in selected_ids:
    all_idea_ids.add(idea)
    if idea not in idea_hashes:raise ValueError(f"{cid}: missing selected idea hash for {idea}")
    # Manifest hash consistency is checked when sharing an idea artifact across candidates.
    expected_sha=idea_hashes[idea]
    if idea in a_data and a_data[idea]["sha256"]!=expected_sha:raise ValueError(f"{idea}: candidate manifests pin different artifacts")
    if idea not in a_data:
     mask,meta=load_idea_mask(workspace/"ideas"/f"{idea}.parquet",expected_sha,idea,component,ids,region_of)
     a_data[idea]={"sha256":expected_sha,"mask":mask,"meta":meta}
    else:
     # An idea cannot be reused for a different output component.
     if a_data[idea]["component"]!=component:raise ValueError(f"{idea}: conflicting component mapping")
    a_data[idea]["component"]=component
    family=a_data[idea]["meta"]["family"];fam_members.setdefault(family,[]).append(idea);idea_masks[idea]=a_data[idea]["mask"]
    idea_stats[idea]={"family":family,"component":component,"artifact":f"$COMPETITION_WORKSPACE/ideas/{idea}.parquet","sha256":expected_sha,
      "by_region":summarize_mask_by_region(ids_by_region,a_data[idea]["mask"]),**a_data[idea]["meta"]}
   family_by_component[component]=fam_members
   component_families[component]={}
   for family,members in sorted(fam_members.items()):
    union=set().union(*(a_data[i]["mask"] for i in members))
    weight=float(weights.get(component,{}).get(family,0.0))
    family_used=weight>0 and not (cid=="A3_dawid_skene" and component=="F")
    component_families[component][family]={"selected_ideas":members,"registered_weight":weight,"used_by_candidate_component":family_used,
      "replaced_by_frozen_dawid_skene_witness":cid=="A3_dawid_skene" and component=="F",
      "defined_mask_union_by_region":summarize_mask_by_region(ids_by_region,union)}
  registered_component_masks={}
  for component,families in family_by_component.items():
   active=[f for f in families if float(weights.get(component,{}).get(f,0.0))>0]
   registered_component_masks[component]=set().union(*(set().union(*(a_data[i]["mask"] for i in families[f])) for f in active)) if active else set()
  witness_data={}
  if cid=="A3_dawid_skene":
   witnesses=manifest.get("diagnostics",{}).get("dawid_skene",{}).get("witnesses",[])
   for idea in witnesses:
    if idea not in idea_hashes:raise ValueError(f"A3: missing witness hash for {idea}")
    mask,meta=load_witness_mask(workspace/"ideas"/f"{idea}.parquet",idea_hashes[idea],idea,ids,region_of)
    witness_data[idea]={"mask":mask,"metadata":meta,"sha256":idea_hashes[idea]}
    all_idea_ids.add(idea)
   witness_union=set().union(*(x["mask"] for x in witness_data.values())) if witness_data else set()
   registered_component_masks["F"] = witness_union
  output_masks={"R":registered_component_masks["R"],"B":registered_component_masks["B"],
    "F":registered_component_masks["F"],"Est":registered_component_masks["Est"]}
  output_masks["POI"]=output_masks["F"]|output_masks["Est"]
  output_masks["any_component_support"]=output_masks["R"]|output_masks["B"]|output_masks["POI"]
  candidate_data[cid]={"candidate_manifest":{"path":MANIFEST_PATHS[cid],"sha256":manifest_hash},
    "candidate_artifact":{"path":registration["artifact"],"sha256":registration["sha256"]},
    "selected_gap_ideas":selected,"registered_family_weights":weights,"per_idea":idea_stats,
    "component_family_support":component_families,"registered_component_support_by_region":{c:summarize_mask_by_region(ids_by_region,m) for c,m in output_masks.items()},
    "pairwise_component_mask_coverage_by_region":{r:candidate_pairwise(rids,{k:v.intersection(rids) for k,v in output_masks.items() if k!="any_component_support"}) for r,rids in ids_by_region.items()},
    "within_component_family_pairwise_by_region":{},"a3_witness_support":{}}
  for region,rids in ids_by_region.items():
   pair_block={}
   for component,families in family_by_component.items():
    labels=sorted(families);pairs={}
    for i,left in enumerate(labels):
     left_mask=set().union(*(a_data[x]["mask"] for x in families[left]))
     for right in labels[i+1:]:
      right_mask=set().union(*(a_data[x]["mask"] for x in families[right]))
      pairs[f"{left}__{right}"]=pair_stats(rids,left_mask,right_mask)
    pair_block[component]=pairs
   candidate_data[cid]["within_component_family_pairwise_by_region"][region]=pair_block
  if witness_data:
   witness_masks={k:v["mask"] for k,v in witness_data.items()}
   candidate_data[cid]["a3_witness_support"]={"witnesses":{idea:{"sha256":v["sha256"],"by_region":summarize_mask_by_region(ids_by_region,v["mask"])} for idea,v in witness_data.items()},
     "effective_F_defined_mask_rule":"union of defined registered covariate masks for frozen Dawid-Skene witnesses; no values refit or predictions rebuilt",
     "pairwise_witness_mask_coverage_by_region":{region:candidate_pairwise(rids,{k:v.intersection(rids) for k,v in witness_masks.items()}) for region,rids in ids_by_region.items()}}
  # Ensure no source output or manifest changed while measuring support.
  if sha256(manifest_path)!=manifest_hash or sha256(ROOT/registration["artifact"])!=registration["sha256"]:
   raise ValueError(f"{cid}: frozen candidate inputs changed during audit")

 # V2 has no selected idea list; report its three frozen registered output-component masks separately.
 v2_audit=json.loads(V2_MASK_REPORT.read_text(encoding="utf-8"));v2_cfg=json.loads(V2_MASK_CONFIG.read_text(encoding="utf-8"))
 v2_source_masks={};v2_sources={}
 frozen=v2_audit["frozen_inputs"];configured=v2_cfg["frozen_inputs"]
 for component,key,flag in (("R","transport_component","route_reference_transport_defined"),
                           ("B","building_component","building_defined"),
                           ("POI","poi_component","v2_raw_poi_defined")):
  source=frozen[key];cfg_source=configured[key]
  if source["path"]!=cfg_source["path"] or source["sha256"]!=cfg_source["sha256"] or source["defined_column"]!=cfg_source["defined_column"]:
   raise ValueError(f"V2 {component}: mask audit differs from frozen preregistration")
  path=ROOT/source["path"];expected=source["sha256"]
  mask,meta=load_csv_flag_mask(path,expected,ids,flag)
  v2_source_masks[component]=mask;v2_sources[component]={"path":source["path"],"sha256":expected,**meta}
 v2_data={"source_mask_audit":{"path":"reports/v2-defined-mask-audit.json","sha256":sha256(V2_MASK_REPORT),
    "config":{"path":"config/v2-component-mask-audit-preregistered.json","sha256":sha256(V2_MASK_CONFIG)},
    "components":v2_sources,"note":"V2 has no selected idea list in final-two; only its registered top-level R/B/POI masks are reported, not rederived or imputed."},
   "component_support_by_region":{c:summarize_mask_by_region(ids_by_region,m) for c,m in v2_source_masks.items()},
   "pairwise_component_mask_coverage_by_region":{r:candidate_pairwise(rids,{k:v.intersection(rids) for k,v in v2_source_masks.items()}) for r,rids in ids_by_region.items()}}

 # Verify that every unique registered selected idea and A3 witness was accounted for.
 selected_union=set()
 for cid in A_CANDIDATES:
  manifest=json.loads((ROOT/MANIFEST_PATHS[cid]).read_text(encoding="utf-8"))
  selected_union.update(i for ids_ in manifest["selected_gap_ideas"].values() for i in ids_)
 if selected_union-set(a_data) or set(a_data)-selected_union:raise ValueError("selected idea accounting mismatch")
 missing_rows=sum(item["meta"]["rows"]!=9379 or item["meta"]["missing_emission_rows"]!=0 for item in a_data.values())
 placeholder_violations=sum(item["meta"]["undefined_placeholder_nonzero_rows"] for item in a_data.values())
 report={"schema_version":1,"audit":"Frozen Track A feature support/missingness attribution; diagnostic-only, no accuracy claim.",
  "scope":{"frozen_candidates":list(COHORT),"idea_based_candidates":list(A_CANDIDATES),"official_scored_geoids":len(ids),"regions":list(REGIONS),
    "template_only_geoids_in_support_summary":False,"sample_score_placeholders_read":False,"candidate_outputs_rebuilt_or_modified":False,
    "labels_removed_references_external_data_or_leaderboard_feedback_used":False},
  "metric_definitions":{"per_idea_defined_fraction":"number of explicit defined=true gap emissions divided by official IDs in that region; false rows remain missing and are not imputed",
    "estimator_family_mask":"union of defined-ID masks for selected ideas in a component sharing the same registered idea family label (for example Dst/G/P/Sc/Sca)",
    "effective_candidate_component_mask":"union of estimator-family masks with strictly positive frozen component weight; A3 F is replaced by the union of its frozen Dawid-Skene witness defined masks",
    "pairwise_mask_coverage":"intersection, union, intersection/region, both directions of conditional coverage, and Jaccard for exact masks; no score values used"},
  "measure_before":{"previous_family_by_region_support_matrix_recorded":False,
    "frozen_A1_manifest_had_official_rows_and_per_idea_hashes":True},
  "measure_after":{"official_ids_partitioned_once":len(ids)==9379 and len(set(ids))==9379,
    "selected_idea_rows_per_artifact":9379,"missing_emission_rows":missing_rows,
    "nonzero_undefined_placeholders":placeholder_violations,"family_and_pairwise_masks_by_region_recorded":True},
  "source_manifest":{"path":"reports/final-two.json","sha256":final_two_hash,
    "candidate_registration_projection":"candidate_id/artifact/hash/rows/columns/preregistered-card metadata/parameter_provenance only; score, submission, and leaderboard fields skipped",
    "candidate_artifacts":candidate_file_hashes},
  "provenance":{"analysis_script_sha256":sha256(Path(__file__).resolve()),
    "official_id_source":"$COMPETITION_WORKSPACE/samples/<region>-sample-submission.csv; GEOID column only",
    "v2_mask_audit_sha256":sha256(V2_MASK_REPORT),"v2_mask_preregistration_sha256":sha256(V2_MASK_CONFIG)},
  "selected_idea_accounting":{"unique_selected_ideas":len(selected_union),"selected_ideas":sorted(selected_union),
    "idea_artifacts":{idea:{"sha256":a_data[idea]["sha256"],"gap_rows":a_data[idea]["meta"]["rows"],
      "official_geoids_once":a_data[idea]["meta"]["rows"]==9379,"missing_emission_rows":a_data[idea]["meta"]["missing_emission_rows"],
      "undefined_placeholder_nonzero_rows":a_data[idea]["meta"]["undefined_placeholder_nonzero_rows"]} for idea in sorted(selected_union)},
    "all_candidate_selected_lists_identical":all(candidate_data[cid]["selected_gap_ideas"]==candidate_data[A_CANDIDATES[0]]["selected_gap_ideas"] for cid in A_CANDIDATES),
    "no_unexplained_id_loss":missing_rows==0,"undefined_rows_are_explicit_missingness_not_imputed":True,
    "nonzero_undefined_placeholder_rows":placeholder_violations},
  "candidates":candidate_data,"v2_component_support":v2_data,
  "acceptance":{"official_id_partition_exact_9379":len(ids)==9379 and len(set(ids))==9379,
    "every_selected_idea_emission_accounted":missing_rows==0 and len(selected_union)==len(a_data),
    "every_idea_family_accounted":all(all(v["selected_ideas"] for v in fams.values()) for cand in candidate_data.values() for fams in cand["component_family_support"].values()),
    "all_candidate_and_idea_hashes_verified":True,"all_missingness_explicit":True,"candidates_unchanged":True,"no_accuracy_claim":True},
  "limitations":["A1/A2/A3 support is attributed to registered selected gap outputs and frozen weights, not interpreted as target accuracy.",
    "any_component_support is only the union of available R/B/POI masks; it is not an A1 Sca5 composite-definedness mask.", 
    "V2 has no selected idea list; its three top-level masks are reported separately from A1/A2/A3 estimator-family masks.",
    "A3's F posterior support follows the frozen witness definedness masks; witness accuracy is not assessed."]}
 out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
 return report


def main()->int:
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--workspace",type=Path,default=Path(os.environ.get("COMPETITION_WORKSPACE","")))
 p.add_argument("--out",type=Path,default=ROOT/"reports/track-a-feature-support.json");args=p.parse_args()
 if not args.workspace.is_dir():p.error("--workspace or COMPETITION_WORKSPACE must be an existing directory")
 r=run(args.workspace,args.out)
 print(json.dumps({"acceptance":r["acceptance"],"ideas":r["selected_idea_accounting"]["unique_selected_ideas"],
  "candidate_component_support":{cid:{comp:{reg:vals["defined_ids"] for reg,vals in stats.items()}
    for comp,stats in cand["registered_component_support_by_region"].items()} for cid,cand in r["candidates"].items()}},sort_keys=True))
 return 0
if __name__=="__main__":raise SystemExit(main())
