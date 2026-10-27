#!/usr/bin/env python3
"""Diagnostic output-level B/Est/F/R ablations for the frozen Track A A1/A2/A3 cohort."""
from __future__ import annotations
import argparse,csv,hashlib,io,json,math,os,sys
from pathlib import Path
from typing import Any
import numpy as np
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
from bias60.inputs import official_scored_pairs  # noqa:E402
from build_track_a import dawid_skene  # noqa:E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
CANDIDATES={
 "A1_physical_equal":{"artifact":"reports/candidates/A1_physical_equal.csv","sha":"6af088b83ff3481d420e11b14d903fc510c2b4be2cea285c762ca12d500862de","manifest":"reports/candidates/A1_physical_equal_manifest.json","manifest_sha":"89a666ae66b2428188dd1448053e57876b7bcc29d7ddbe8ba5b72bdf611ce2d4","registration_manifest_sha":"89a666ae66b2428188dd1448053e57876b7bcc29d7ddbe8ba5b72bdf611ce2d4"},
 "A2_spectral":{"artifact":"reports/candidates/A2_spectral.csv","sha":"f88573996566a1975726845a49f42fb2cd3781d5f0e83d55944fc65647692997","manifest":"reports/candidates/A2_spectral_manifest.json","manifest_sha":"4193304de0e886810f9930b87dcbcdeaa01ccb16cd25ab4e2bd3cd53053b0d57","registration_manifest_sha":"4193304de0e886810f9930b87dcbcdeaa01ccb16cd25ab4e2bd3cd53053b0d57"},
 "A3_dawid_skene":{"artifact":"reports/candidates/A3_dawid_skene.csv","sha":"6d588f2f7a45a58b128860a049ac19995b21902f7f98d3b2ab39d1d9fbb073b3","manifest":"reports/candidates/A3_dawid_skene_manifest.json","manifest_sha":"3bb36fc7e0977886d2f73d4e5a05d52ca9eb1ca0f6693c2659d60a97c4b6873f","registration_manifest_sha":"3bb36fc7e0977886d2f73d4e5a05d52ca9eb1ca0f6693c2659d60a97c4b6873f"}
}
ABLATIONS=("B","Est","F","R")
COMPONENTS=("R","B","F","Est")
FIELD={"R":"transport_gap","B":"building_gap","POI":"poi_gap"}
CARD=ROOT/"reports/final-candidate-cards/track-a-cohort-2026-10-26.json"
CARD_SHA="8f90b25e5c83fa92b3959fdeaf5a953a977e5025f1f17afd148159e6010ffafb"
TRACK_CONFIG=ROOT/"config/track-a-preregistered.json"


def sha256(path:Path)->str:
 h=hashlib.sha256()
 with path.open("rb") as f:
  for block in iter(lambda:f.read(1<<20),b""):h.update(block)
 return h.hexdigest()


def stats(values:np.ndarray,mask:np.ndarray)->dict[str,Any]:
 x=np.asarray(values,dtype=np.float64);m=np.asarray(mask,dtype=bool)
 if x.shape!=m.shape:raise ValueError("metric values and mask must align")
 selected=np.abs(x[m])
 if not len(selected):return {"support_n":0,"p50_abs_change":None,"p95_abs_change":None,"max_abs_change":None,"nonfinite_count":0}
 if not np.isfinite(selected).all():raise ValueError("non-finite change on reported support")
 return {"support_n":int(len(selected)),"p50_abs_change":float(np.quantile(selected,.5)),"p95_abs_change":float(np.quantile(selected,.95)),
   "max_abs_change":float(selected.max()),"nonfinite_count":0}


def family_component_estimate(idea_ids:list[str],idea_data:dict[str,dict[str,dict[str,Any]]],
                              ids:list[str],component:str,weights:dict[str,float])->tuple[np.ndarray,np.ndarray]:
 n=len(ids);families={}
 for idea in idea_ids:
  sample=next(iter(idea_data[idea].values()))
  family=next(iter(sample.values()))["family"]
  values=np.zeros(n);defined=np.zeros(n,dtype=bool)
  for i,g in enumerate(ids):
   item=idea_data[idea][g].get((component,"gap"))
   if item is not None:values[i]=item["value"];defined[i]=item["defined"]
  families.setdefault(family,[]).append((values,defined))
 fam_value={};fam_mask={}
 for family,members in families.items():
  values=np.vstack([v for v,_ in members]);masks=np.vstack([m for _,m in members]);count=masks.sum(axis=0)
  fam_value[family]=np.divide((values*masks).sum(axis=0),count,out=np.zeros(n),where=count>0);fam_mask[family]=count>0
 numerator=np.zeros(n);denominator=np.zeros(n)
 for family,weight in weights.items():
  if family not in fam_value:raise ValueError(f"registered weight family {family} absent for {component}")
  valid=fam_mask[family];numerator[valid]+=float(weight)*fam_value[family][valid];denominator[valid]+=float(weight)
 mask=denominator>0
 result=np.divide(numerator,denominator,out=np.zeros(n),where=mask)
 return result,mask


def poi_from_halves(f_value:np.ndarray,f_mask:np.ndarray,est_value:np.ndarray,est_mask:np.ndarray)->tuple[np.ndarray,np.ndarray]:
 count=f_mask.astype(np.int8)+est_mask.astype(np.int8)
 value=np.divide(f_value*f_mask+est_value*est_mask,count,out=np.zeros(len(count)),where=count>0)
 return value,count>0


def reweighted_composite(r:np.ndarray,b:np.ndarray,poi:np.ndarray,mr:np.ndarray,mb:np.ndarray,mp:np.ndarray)->tuple[np.ndarray,np.ndarray]:
 values=np.column_stack((r,b,poi));masks=np.column_stack((mr,mb,mp));count=masks.sum(axis=1)
 out=np.divide((values*masks).sum(axis=1),count,out=np.zeros(len(count)),where=count>0)
 return out,count>0


def load_idea(artifact:Path,expected_sha:str,ids:list[str],region_of:dict[str,str],idea:str)->dict[str,dict[tuple[str,str],dict[str,Any]]]:
 if sha256(artifact)!=expected_sha:raise ValueError(f"idea artifact hash mismatch: {idea}")
 table=pq.read_table(artifact,columns=["GEOID","region","idea","family","component","channel","value","defined"])
 result={}
 for row in table.to_pylist():
  g=str(row["GEOID"])
  if g not in region_of:continue
  if row["region"]!=region_of[g] or row["idea"]!=idea:raise ValueError(f"idea row partition mismatch for {idea}/{g}")
  key=(str(row["component"]),str(row["channel"]))
  result.setdefault(g,{})[key]={"family":str(row["family"]),"value":float(row["value"]),"defined":bool(row["defined"])}
 if set(result)!=set(ids):raise ValueError(f"idea {idea} does not cover exact official GEOIDs")
 return result


def load_candidate_csv(path:Path,expected_sha:str,ids:list[str])->dict[str,np.ndarray]:
 if sha256(path)!=expected_sha:raise ValueError(f"frozen candidate artifact hash mismatch: {path}")
 wanted=set(ids);rows={}
 with path.open(newline="",encoding="utf-8-sig") as stream:
  reader=csv.DictReader(stream)
  if reader.fieldnames!=["GEOID","transport_gap","building_gap","poi_gap","coverage_gap_score"]:raise ValueError("candidate columns changed")
  for row in reader:
   g=row["GEOID"]
   if g not in wanted:continue
   if g in rows:raise ValueError(f"duplicate candidate GEOID {g}")
   values=[float(row[name]) for name in reader.fieldnames[1:]]
   if not all(math.isfinite(v) and 0.<=v<=1. for v in values):raise ValueError(f"invalid frozen output at {g}")
   rows[g]=values
 if set(rows)!=wanted:raise ValueError("candidate misses official IDs")
 return {name:np.asarray([rows[g][i] for g in ids],dtype=np.float64) for i,name in enumerate(("transport_gap","building_gap","poi_gap","coverage_gap_score"))}


def run(workspace:Path,report_path:Path,mask_path:Path)->dict[str,Any]:
 official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)];ids=[g for g,_ in official];region_of=dict(official)
 if len(ids)!=9379 or len(set(ids))!=len(ids) or set(region_of.values())!=set(REGIONS):raise ValueError("official scoped IDs changed")
 if sha256(CARD)!=CARD_SHA:raise ValueError("frozen A1/A2/A3 cohort card hash changed")
 cards=json.loads(CARD.read_text());card_by_id={x["candidate_id"]:x for x in cards["cards"]}
 idea_data={};candidate_data={};source_hashes={};masks_record=[];results={}
 for candidate,expected in CANDIDATES.items():
  manifest_path=ROOT/expected["manifest"];manifest_sha=sha256(manifest_path);card=card_by_id[candidate]
  if manifest_sha!=expected["manifest_sha"] or card["build_manifest_sha256"]!=manifest_sha:
   raise ValueError(f"{candidate}: frozen source manifest binding mismatch")
  if card["sha256"]!=expected["sha"] or card["artifact"]!=expected["artifact"]:raise ValueError(f"{candidate}: frozen card artifact binding mismatch")
  if sha256(ROOT/expected["artifact"])!=expected["sha"]:raise ValueError(f"{candidate}: frozen output hash mismatch")
  manifest=json.loads(manifest_path.read_text())
  if manifest["candidate_id"]!=candidate or manifest["sha256"]!=expected["sha"] or manifest["selected_gap_ideas"]!=card["component_formula_and_parameters"]["selected_gap_ideas"]:
   raise ValueError(f"{candidate}: frozen candidate manifest/idea list mismatch")
  weights=manifest["diagnostics"]["family_weights"]
  if weights!=card["component_formula_and_parameters"]["diagnostics"]["family_weights"]:raise ValueError(f"{candidate}: candidate weights differ from frozen card")
  artifacts=manifest["idea_input_sha256"]
  need=set(sum((manifest["selected_gap_ideas"][c] for c in COMPONENTS),[]))
  witnesses=manifest["diagnostics"].get("dawid_skene",{}).get("witnesses",[]) if candidate=="A3_dawid_skene" else []
  need.update(witnesses)
  for idea in sorted(need):
   if idea not in idea_data:
    if idea not in artifacts:raise ValueError(f"{candidate}: missing frozen idea hash for {idea}")
    idea_data[idea]=load_idea(workspace/"ideas"/f"{idea}.parquet",artifacts[idea],ids,region_of,idea)
  out=load_candidate_csv(ROOT/expected["artifact"],expected["sha"],ids)
  comp_values={};comp_masks={}
  for component in COMPONENTS:
   comp_values[component],comp_masks[component]=family_component_estimate(manifest["selected_gap_ideas"][component],idea_data,ids,component,weights[component])
  if candidate=="A3_dawid_skene":
   registered_witnesses=card["component_formula_and_parameters"]["diagnostics"]["dawid_skene"]["witnesses"]
   if witnesses!=registered_witnesses:raise ValueError("A3 frozen Dawid-Skene witness list mismatch")
   witness_cols=[]
   for idea in witnesses:
    column=np.full(len(ids),np.nan)
    for i,g in enumerate(ids):
     emissions=idea_data[idea][g];item=next((v for (c,_),v in emissions.items() if c=="F"),next(iter(emissions.values())))
     if item["defined"]:column[i]=float(item["value"]>=.5)
    witness_cols.append(column)
   if len(witness_cols)>=2:
    observed=np.column_stack(witness_cols);posterior,_=dawid_skene(observed,alpha=1.0,max_iter=500,tolerance=1e-10)
    comp_values["F"]=posterior;comp_masks["F"]=np.isfinite(observed).sum(axis=1)>0
  fval,fmask=comp_values["F"],comp_masks["F"];eval_,emask=comp_values["Est"],comp_masks["Est"]
  poival,poimask=poi_from_halves(fval,fmask,eval_,emask)
  checks=(("R","transport_gap",out["transport_gap"]),("B","building_gap",out["building_gap"]))
  for component,field,actual in checks:
   if not np.allclose(comp_values[component],actual,rtol=0,atol=2e-14):raise ValueError(f"{candidate}: {field} does not reproduce from frozen manifest inputs")
  if not np.allclose(poival,out["poi_gap"],rtol=0,atol=2e-14):raise ValueError(f"{candidate}: POI output does not reproduce from registered F/Est mappings")
  if candidate in ("A2_spectral","A3_dawid_skene"):
   baseline,base_mask=reweighted_composite(out["transport_gap"],out["building_gap"],out["poi_gap"],comp_masks["R"],comp_masks["B"],poimask)
   if not np.allclose(baseline,out["coverage_gap_score"],rtol=0,atol=2e-14):raise ValueError(f"{candidate}: frozen composite does not reproduce registered equal-defined mean")
  else:
   baseline,base_mask=reweighted_composite(out["transport_gap"],out["building_gap"],out["poi_gap"],comp_masks["R"],comp_masks["B"],poimask)
  source_hashes[candidate]={"artifact":{"path":expected["artifact"],"sha256":sha256(ROOT/expected["artifact"])},
   "manifest":{"path":expected["manifest"],"sha256":manifest_sha},"selected_idea_list":manifest["selected_gap_ideas"],
   "registered_weights":weights,"idea_artifacts":{idea:artifacts[idea] for idea in sorted(need)},"frozen_output_coverage_mask_sha256":hashlib.sha256(np.packbits(np.ones(len(ids),dtype=np.uint8),bitorder="little").tobytes()).hexdigest()}
  original_values={"R":out["transport_gap"],"B":out["building_gap"],"POI":out["poi_gap"]}
  original_masks={"R":comp_masks["R"],"B":comp_masks["B"],"POI":poimask}
  out_results={};candidate_mask_rows=[]
  for region in REGIONS:
   region_mask=np.asarray([r==region for r in region_of.values()],dtype=bool)
   # IDs and region_of are constructed in the same official ordering.
   row_idx=np.flatnonzero(region_mask)
   out_results[region]={}
  full_recomp,full_recomp_mask=reweighted_composite(out["transport_gap"],out["building_gap"],out["poi_gap"],comp_masks["R"],comp_masks["B"],poimask)
  for ablation in ABLATIONS:
   vals={name:arr.copy() for name,arr in original_values.items()};masks={name:mask.copy() for name,mask in original_masks.items()}
   if ablation=="R":vals["R"][:]=0.;masks["R"][:]=False
   elif ablation=="B":vals["B"][:]=0.;masks["B"][:]=False
   elif ablation=="F":vals["POI"],masks["POI"]=eval_.copy(),emask.copy()
   elif ablation=="Est":vals["POI"],masks["POI"]=fval.copy(),fmask.copy()
   # The removed direct component is represented by zero only as its undefined CSV placeholder;
   # the mask is false and the composite excludes it. F/Est removal retains the other POI half.
   new_recomp,new_recomp_mask=reweighted_composite(vals["R"],vals["B"],vals["POI"],masks["R"],masks["B"],masks["POI"])
   composite_delta=new_recomp-full_recomp
   counterfactual_frozen=out["coverage_gap_score"]+composite_delta
   for name,field in (("R","transport_gap"),("B","building_gap"),("POI","poi_gap")):
    base=original_values[name];new=vals[name];comparison=original_masks[name]
    # For R/B removed, compare against zero storage placeholder on baseline-defined IDs;
    # for POI, compare the recomputed surviving half on original POI-defined IDs.
    change=np.abs(new-base)
    result={**stats(change,comparison),"field":field,"comparison_mask_sha256":hashlib.sha256(np.packbits(comparison.astype(np.uint8),bitorder="little").tobytes()).hexdigest(),
      "after_defined_support_n":int(masks[name].sum()),"after_mask_sha256":hashlib.sha256(np.packbits(masks[name].astype(np.uint8),bitorder="little").tobytes()).hexdigest()}
    for region in REGIONS:
     idx=np.asarray([r==region for r in region_of.values()],dtype=bool)
     result_region={**stats(change,comparison&idx),"field":field,"comparison_mask_sha256":hashlib.sha256(np.packbits((comparison&idx).astype(np.uint8),bitorder="little").tobytes()).hexdigest(),
       "after_defined_support_n":int(np.sum(masks[name]&idx)),"after_mask_sha256":hashlib.sha256(np.packbits((masks[name]&idx).astype(np.uint8),bitorder="little").tobytes()).hexdigest()}
     out_results[region].setdefault(ablation,{})[field]=result_region
   for region in REGIONS:
    idx=np.asarray([r==region for r in region_of.values()],dtype=bool)
    comparison=np.ones(len(ids),dtype=bool)&idx
    comp={**stats(composite_delta,comparison),"comparison_mask_sha256":hashlib.sha256(np.packbits(comparison.astype(np.uint8),bitorder="little").tobytes()).hexdigest(),
      "baseline_reweighted_vs_frozen_p95_abs":float(np.quantile(np.abs(full_recomp[comparison]-out["coverage_gap_score"][comparison]),.95)),
      "counterfactual_frozen_candidate_composite_p50":float(np.quantile(counterfactual_frozen[comparison],.5)),
      "counterfactual_frozen_candidate_composite_p95":float(np.quantile(counterfactual_frozen[comparison],.95)),
      "counterfactual_frozen_candidate_composite_max":float(np.max(counterfactual_frozen[comparison])),
      "counterfactual_out_of_unit_interval_n":int(np.sum((counterfactual_frozen[comparison]<0)|(counterfactual_frozen[comparison]>1)))}
    out_results[region][ablation]["coverage_gap_score"]=comp
   for i,g in enumerate(ids):
    row={"GEOID":g,"region":region_of[g],"candidate_id":candidate,
      "baseline_R_defined":int(comp_masks["R"][i]),"baseline_B_defined":int(comp_masks["B"][i]),
      "baseline_F_defined":int(comp_masks["F"][i]),"baseline_Est_defined":int(comp_masks["Est"][i]),"baseline_POI_defined":int(poimask[i]),
      "baseline_composite_defined":int(base_mask[i])}
    for ablation in ABLATIONS:
     if ablation=="R":mR=False;mB=bool(comp_masks["B"][i]);mF=bool(comp_masks["F"][i]);mEst=bool(comp_masks["Est"][i]);mPoi=bool(comp_masks["Est"][i])
     elif ablation=="B":mR=bool(comp_masks["R"][i]);mB=False;mF=bool(comp_masks["F"][i]);mEst=bool(comp_masks["Est"][i]);mPoi=bool(poimask[i])
     elif ablation=="F":mR=bool(comp_masks["R"][i]);mB=bool(comp_masks["B"][i]);mF=False;mEst=bool(comp_masks["Est"][i]);mPoi=mEst
     else:mR=bool(comp_masks["R"][i]);mB=bool(comp_masks["B"][i]);mF=bool(comp_masks["F"][i]);mEst=False;mPoi=mF
     # composite exists if any retained R/B/POI output is defined
     mComp=bool(mR or mB or mPoi)
     for name,value in (("R",mR),("B",mB),("F",mF),("Est",mEst),("POI",mPoi),("composite",mComp)):
      row[f"after_{ablation}_{name}_defined"]=int(value)
    candidate_mask_rows.append(row)
  results[candidate]={"source":source_hashes[candidate],"composite_baseline":{"registered_frozen_formula":manifest["method"],
    "frozen_score_matches_equal_defined_component_mean":candidate!="A1_physical_equal","reweighted_baseline_vs_frozen_by_region":{
      r:{"support_n":int(np.sum(np.asarray([x==r for x in region_of.values()])&base_mask)),"p50_abs_difference":float(np.quantile(np.abs(full_recomp[np.asarray([x==r for x in region_of.values()])]-out["coverage_gap_score"][np.asarray([x==r for x in region_of.values()])]),.5)),
      "p95_abs_difference":float(np.quantile(np.abs(full_recomp[np.asarray([x==r for x in region_of.values()])]-out["coverage_gap_score"][np.asarray([x==r for x in region_of.values()])]),.95))} for r in REGIONS}},
    "ablation_metrics_by_region":out_results}
  masks_record.extend(candidate_mask_rows)
  # Verify frozen source candidate files still match their manifest after this read-only audit.
  for candidate,expected in CANDIDATES.items():
   if sha256(ROOT/expected["artifact"])!=expected["sha"] or sha256(ROOT/expected["manifest"])!=expected["manifest_sha"]:raise AssertionError("source candidate changed during audit")
 mask_buffer=io.StringIO(newline="");writer=csv.DictWriter(mask_buffer,fieldnames=list(masks_record[0]),lineterminator="\n");writer.writeheader();writer.writerows(masks_record)
 mask_bytes=mask_buffer.getvalue().encode();mask_path.parent.mkdir(parents=True,exist_ok=True);mask_path.write_bytes(mask_bytes)
 all_metrics=[entry for candidate_data in results.values() for region_data in candidate_data["ablation_metrics_by_region"].values() for ablation in ABLATIONS for entry in region_data[ablation].values()]
 if any(metric["nonfinite_count"] for metric in all_metrics):raise ValueError("non-finite reported ablation metric")
 report={"schema_version":1,"audit":"Frozen Track A A1/A2/A3 output-level family ablation sensitivity; diagnostic-only",
  "scope":{"candidate_ids":list(CANDIDATES),"official_ids":len(ids),"regions":list(REGIONS),"ablations":list(ABLATIONS),
    "family_component_mapping":{"R":"transport_gap","B":"building_gap","F":"facility half of poi_gap","Est":"establishments half of poi_gap"},
    "candidate_files_modified":False,"candidate_registered_or_uploaded":False,"labels_or_score_placeholders_read":False,"leaderboard_feedback_used":False},
  "method":{"source":"Verify the frozen final-two registrations, preregistered cohort card, candidate CSVs and candidate source manifests. Recompute R/B/F/Est selected-idea component estimates and masks from their hash-verified idea artifacts using each candidate's already registered family weights; A3 F uses only its frozen Dawid-Skene witnesses/parameters.",
    "family_ablation":"Drop exactly one of R/B/F/Est. R/B are marked undefined with zero storage placeholder; for F/Est ablations, poi_gap is the remaining defined half (undefined if neither is available). Component weights/mappings are otherwise unchanged.",
    "composite":"Counterfactual uses the registered Track-A equal mean of the remaining defined R, B, and POI output components, renormalized per GEOID. Reported composite change is the difference between this all-family and ablated reweighted composition, added as a delta to the frozen candidate composite solely to express an output-level sensitivity. This is diagnostic and does not change a candidate.",
    "A1_limitation":"A1's frozen composite is the registered Sca5 expected-definedness composite with region-level Dst5 anchoring, not the equal-defined component mean used by A2/A3. This audit does not refit/recompute Sca5; it reports the A1 frozen-composite versus all-family reweighted-output difference separately and applies only the component-recomposition delta. Thus A1 ablation is an output-level sensitivity, not a replay of Sca5 internals.",
    "metric":"Per region, p50/p95/max of absolute changes in each output gap component and composite. Component metrics use original component-defined GEOIDs; composite metrics use all official candidate rows. Exact masks are in the companion CSV and mask hashes are included in metric entries.",
    "interpretation":"Finite descriptive sensitivity only; it is not estimator accuracy, candidate selection, new weights, or an authorization to change/submit candidates."},
  "measure_before":{"candidate_components_and_hashes_recorded":True,"family_ablation_table_recorded":False,"candidate_artifacts":{c:{"path":CANDIDATES[c]["artifact"],"sha256":CANDIDATES[c]["sha"]} for c in CANDIDATES}},
  "measure_after":{"candidates":results,"exact_geoID_masks":{"path":str(mask_path),"sha256":hashlib.sha256(mask_bytes).hexdigest(),"rows":len(masks_record),"columns":list(masks_record[0]),
    "mask_values":"0/1 defined indicators per official GEOID, candidate, baseline component, ablation, and post-ablation component/composite."}},
  "provenance":{"preregistered_cohort_card":{"path":str(CARD),"sha256":sha256(CARD)}, 
    "track_a_config":{"path":str(TRACK_CONFIG),"sha256":sha256(TRACK_CONFIG)},"source_candidate_manifests":{c:{"path":CANDIDATES[c]["manifest"],"sha256":CANDIDATES[c]["manifest_sha"]} for c in CANDIDATES},
    "source_candidate_artifacts_unchanged":True,"analysis_script_sha256":sha256(Path(__file__).resolve())},
  "acceptance":{"all_four_families_covered":set(ABLATIONS)=={"B","Est","F","R"},"all_four_regions_covered":all(set(x["ablation_metrics_by_region"])==set(REGIONS) for x in results.values()),
    "all_reported_metrics_finite":True,"candidate_artifacts_unchanged":True,"all_results_diagnostic_only":True}}
 report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
 return report


def main()->int:
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
 parser.add_argument("--out",default="reports/track-a-family-ablation.json");parser.add_argument("--masks",default="reports/track-a-family-ablation-masks.csv")
 args=parser.parse_args();workspace=Path(args.workspace)
 if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
 report=run(workspace,ROOT/args.out,ROOT/args.masks)
 print(json.dumps({"acceptance":report["acceptance"],"mask_manifest":report["measure_after"]["exact_geoID_masks"],
  "candidate_metrics":{c:{r:{a:{k:{"n":v["support_n"],"p95_abs_change":v["p95_abs_change"],"max_abs_change":v["max_abs_change"]} for k,v in m.items()} for a,m in regions.items()} for r,regions in x["ablation_metrics_by_region"].items()} for c,x in report["measure_after"]["candidates"].items()}},indent=2))
 return 0
if __name__=="__main__":raise SystemExit(main())
