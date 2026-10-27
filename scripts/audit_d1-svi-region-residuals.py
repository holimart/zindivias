#!/usr/bin/env python3
"""Localize the prior D1 SVI-quartile residual pattern by region using county-block intervals."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/"src"))
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379
RESIDUAL_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
PRIOR_SCRIPT=ROOT/"scripts/audit_d1_rural_language_context_residual.py"
PRIOR_REPORT=ROOT/"reports/d1-rural-language-context-residual-audit.json"
BOOTSTRAP_REPLICATES=2000
BOOTSTRAP_SEED=20261106
MIN_CELL_N=30


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def mask_sha256(mask:np.ndarray)->str:
    return hashlib.sha256(np.packbits(np.asarray(mask,dtype=np.uint8),bitorder="little").tobytes()).hexdigest()


def values_mask_sha256(geoids:np.ndarray,regions:np.ndarray,svi:np.ndarray,urban:np.ndarray,
                       residual:np.ndarray,mask:np.ndarray,quartile:np.ndarray)->str:
    rows=[]
    for i in range(len(geoids)):
        rows.append(",".join((str(geoids[i]),str(regions[i]),
          format(float(svi[i]),".17g") if np.isfinite(svi[i]) else "",
          format(float(urban[i]),".17g") if np.isfinite(urban[i]) else "",
          format(float(residual[i]),".17g") if np.isfinite(residual[i]) else "",
          str(int(mask[i])),str(int(quartile[i])))))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def assign_region_quartiles(svi:np.ndarray,regions:np.ndarray,valid_svi:np.ndarray)->tuple[np.ndarray,dict[str,list[float]]]:
    values=np.asarray(svi,dtype=np.float64);region=np.asarray(regions,dtype=object);valid=np.asarray(valid_svi,dtype=bool)
    if values.ndim!=1 or region.shape!=values.shape or valid.shape!=values.shape or np.any(valid&~np.isfinite(values)):
        raise ValueError("SVI, region, and finite-value mask must be aligned")
    quartile=np.full(len(values),-1,dtype=np.int8);cuts_by_region={}
    for name in REGIONS:
        ix=np.flatnonzero((region==name)&valid)
        if len(ix)<4:raise ValueError(f"insufficient provided SVI values for quartiles in {name}")
        cuts=np.quantile(values[ix],[.25,.5,.75])
        cuts_by_region[name]=[float(c) for c in cuts]
        # Match the prior D1 audit's right-sided tie rule, so tied values at a cut enter the higher bin.
        quartile[ix]=np.searchsorted(cuts,values[ix],side="right").astype(np.int8)+1
    return quartile,cuts_by_region


def standardized_mean_difference(values:np.ndarray,cell:np.ndarray)->dict[str,float|int]:
    y=np.asarray(values,dtype=np.float64);g=np.asarray(cell,dtype=bool)
    if y.ndim!=1 or g.shape!=y.shape or not np.isfinite(y).all():raise ValueError("SMD inputs must be aligned and finite")
    a=y[g];b=y[~g];na,nb=len(a),len(b)
    if na<2 or nb<2:raise ValueError("SMD cell and within-region complement each need at least two observations")
    va=float(np.var(a,ddof=1));vb=float(np.var(b,ddof=1))
    pooled=((na-1)*va+(nb-1)*vb)/(na+nb-2)
    if not np.isfinite(pooled) or pooled<=0.:raise ValueError("SMD pooled within-group variance must be positive")
    sd=float(np.sqrt(pooled));difference=float(a.mean()-b.mean())
    return {"cell_n":na,"complement_n":nb,"cell_mean":float(a.mean()),"complement_mean":float(b.mean()),
      "mean_difference":difference,"pooled_within_group_sd":sd,"standardized_mean_difference":difference/sd}


def region_cell_contrasts(residual:np.ndarray,regions:np.ndarray,quartiles:np.ndarray,
                          counties:np.ndarray,mask:np.ndarray)->dict[str,dict[str,dict[str,Any]]]:
    y=np.asarray(residual,dtype=np.float64);reg=np.asarray(regions,dtype=object);q=np.asarray(quartiles,dtype=np.int8)
    county=np.asarray(counties,dtype=object);use=np.asarray(mask,dtype=bool)
    if y.ndim!=1 or any(a.shape!=y.shape for a in (reg,q,county,use)) or np.any(use&~np.isfinite(y)):
        raise ValueError("contrast vectors must align on finite analysis support")
    out={}
    for name in REGIONS:
        region_use=use&(reg==name);out[name]={}
        for level in range(1,5):
            cell=region_use&(q==level)
            cell_in_region=cell[region_use]
            metrics=standardized_mean_difference(y[region_use],cell_in_region)
            metrics.update({"county_count":len(set(county[cell].tolist())),"region_county_count":len(set(county[region_use].tolist())),
              "supported":bool(metrics["cell_n"]>=MIN_CELL_N),"contrast":"quartile cell minus remaining complete-support tracts in the same region, divided by pooled within-group sample SD"})
            out[name][f"q{level}"]=metrics
    return out


def county_block_bootstrap(residual:np.ndarray,regions:np.ndarray,quartiles:np.ndarray,counties:np.ndarray,
                           mask:np.ndarray,*,replicates:int=BOOTSTRAP_REPLICATES,seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    y=np.asarray(residual,dtype=np.float64);reg=np.asarray(regions,dtype=object);q=np.asarray(quartiles,dtype=np.int8)
    county=np.asarray(counties,dtype=object);use=np.asarray(mask,dtype=bool)
    if y.ndim!=1 or any(a.shape!=y.shape for a in (reg,q,county,use)) or np.any(use&~np.isfinite(y)):
        raise ValueError("bootstrap inputs must align on finite analysis support")
    if replicates<100:raise ValueError("at least 100 county-block bootstrap replicates required")
    blocks={}
    for name in REGIONS:
        region_use=use&(reg==name);blocks[name]={c:np.flatnonzero(region_use&(county==c)) for c in sorted(set(county[region_use].tolist()))}
        if len(blocks[name])<2:raise ValueError(f"{name} requires at least two county blocks")
    rng=np.random.default_rng(seed);samples={(r,f"q{qv}"):np.full(replicates,np.nan) for r in REGIONS for qv in range(1,5)}
    max_abs=np.full(replicates,np.nan);invalid=0
    for b in range(replicates):
        idx_by_region={}
        for name in REGIONS:
            county_ids=list(blocks[name]);draw=rng.choice(county_ids,size=len(county_ids),replace=True)
            idx_by_region[name]=np.concatenate([blocks[name][c] for c in draw])
        all_smd=[];valid_rep=True
        for name in REGIONS:
            ix=idx_by_region[name];yy=y[ix];qq=q[ix]
            for qv in range(1,5):
                try:stat=standardized_mean_difference(yy,qq==qv)["standardized_mean_difference"]
                except ValueError:valid_rep=False;continue
                samples[(name,f"q{qv}")][b]=float(stat);all_smd.append(abs(float(stat)))
        if valid_rep and len(all_smd)==16:max_abs[b]=max(all_smd)
        else:invalid+=1
    finite_max=max_abs[np.isfinite(max_abs)]
    if finite_max.size<int(.99*replicates):raise ValueError(f"too many invalid county-block replicates: {invalid}/{replicates}")
    def interval(a:np.ndarray)->list[float]:
        finite=a[np.isfinite(a)]
        if not finite.size:return [float("nan"),float("nan")]
        low,high=np.quantile(finite,[.025,.975]);return [float(low),float(high)]
    cell_ci={}
    for key,a in samples.items():
        cell_ci[f"{key[0]}:{key[1]}"]={"ci_95":interval(a),"finite_replicates":int(np.isfinite(a).sum())}
    return {"replicates":replicates,"seed":seed,
      "resampling":"Paired county-block bootstrap; sample each region's observed counties with replacement, retaining all complete-support tract residuals in each sampled county.",
      "interval":"Percentile 2.5th and 97.5th percentiles; cell statistic is signed SMD, maximum statistic is max absolute SMD over all 16 cells.",
      "cell_smd_ci_95":cell_ci,"max_absolute_smd_ci_95":interval(finite_max),
      "max_absolute_smd_finite_replicates":int(finite_max.size),"invalid_replicates":invalid}


def _load(workspace:Path)->tuple[list[tuple[str,str]],np.ndarray,np.ndarray,np.ndarray,np.ndarray,dict[str,Any]]:
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or len({g for g,_ in official})!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):
        raise ValueError("official IDs must be exactly 9,379 unique GEOID-region pairs across four regions")
    official_hash=hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    positions={pair:i for i,pair in enumerate(official)};n=len(official)
    residual_path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(residual_path)!=RESIDUAL_SHA:raise ValueError("frozen S1 disagreement residual SHA-256 mismatch")
    residual_table=pq.read_table(residual_path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if residual_table.num_rows!=n:raise ValueError("frozen residual artifact row count mismatch")
    residual=np.full(n,np.nan);residual_mask=np.zeros(n,dtype=bool);seen=set()
    for row in residual_table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in positions or pair in seen:raise ValueError(f"unexpected/duplicate frozen residual ID: {pair}")
        seen.add(pair);i=positions[pair]
        residual_mask[i]=row["crossfit_joint_defined"] is True
        if row["crossfit_residual"] is not None:residual[i]=float(row["crossfit_residual"])
    if seen!=set(official) or not residual_mask.all() or not np.isfinite(residual).all():raise ValueError("frozen S1 residual must be finite and defined for all official IDs")
    svi=np.full(n,np.nan);urban=np.full(n,np.nan);sources={}
    for region in REGIONS:
        path=workspace/"defined-flags-inputs"/f"{region}-strata-tract-table.parquet"
        if not path.is_file():raise FileNotFoundError(f"provided D1/SVI/urbanity strata missing: {path}")
        table=pq.read_table(path,columns=["GEOID","svi_minority","pct_urban"])
        expected={g for g,r in official if r==region};seen_local=set()
        for row in table.to_pylist():
            geoid=str(row["GEOID"])
            if geoid not in expected:continue
            if geoid in seen_local:raise ValueError(f"duplicate provided strata ID {geoid}")
            seen_local.add(geoid);i=positions[(geoid,region)]
            if row["svi_minority"] is not None:svi[i]=float(row["svi_minority"])
            if row["pct_urban"] is not None:urban[i]=float(row["pct_urban"])
        if seen_local!=expected:raise ValueError(f"{region} strata IDs do not exactly cover official tracts")
        if np.any(np.isfinite(svi)&(np.asarray([r==region for _,r in official]))&((svi<0.)|(svi>1.))):
            raise ValueError(f"provided svi_minority outside [0,1] in {region}")
        if np.any(np.isfinite(urban)&(np.asarray([r==region for _,r in official]))&((urban<0.)|(urban>1.))):
            raise ValueError(f"provided pct_urban outside [0,1] in {region}")
        sources[region]={"path":str(path),"sha256":sha256_file(path),"columns_read":["GEOID","svi_minority","pct_urban"],
          "official_ids":len(expected),"svi_finite_ids":int(sum(np.isfinite(svi[i]) for i,(g,r) in enumerate(official) if r==region)),
          "urban_finite_ids":int(sum(np.isfinite(urban[i]) for i,(g,r) in enumerate(official) if r==region))}
    return official,residual,svi,urban,residual_mask,{"official_geoid_region_sha256":official_hash,
      "frozen_residual_path":str(residual_path),"frozen_residual_sha256":RESIDUAL_SHA,"strata_by_region":sources}


def run(workspace:Path,report_path:Path)->dict[str,Any]:
    before=json.loads(PRIOR_REPORT.read_text(encoding="utf-8"))
    if (before["measure_before"]["D1_feature_implemented_ids"]!=9330
      or before["measure_after"]["pooled"]["delta_r2"]!=0.0008019065285473953
      or before["measure_after"]["county_block_bootstrap"]["ci_95_percentile"]!=[-0.0053789886697650354,0.004234866317931995]
      or before["measure_after"]["rural_urban_and_svi_quartile_residual_summaries"]["svi_q3"]["heldout_prediction_delta_r2"]!=-0.015889420963861447):
        raise ValueError("frozen D1 measure-before values differ from the assigned source screen")
    official,residual,svi,urban,residual_mask,provenance=_load(workspace)
    if provenance["official_geoid_region_sha256"]!=before["scope"]["official_geoid_region_sha256"]:
        raise ValueError("official IDs differ from the frozen D1 audit")
    geoids=np.asarray([g for g,_ in official],dtype=object);regions=np.asarray([r for _,r in official],dtype=object)
    counties=np.asarray([g[:5] for g in geoids],dtype=object)
    valid_svi=np.isfinite(svi);quartile,cuts=assign_region_quartiles(svi,regions,valid_svi)
    analysis_mask=residual_mask&valid_svi&np.isfinite(urban)
    if int(analysis_mask.sum())!=before["scope"]["feature_defined_ids"]:raise ValueError("complete-case D1/SVI/urbanity support differs from frozen 9,330")
    for r in REGIONS:
        if cuts[r]!=before["measure_after"]["within_region_svi_quartile_cutpoints"][r]:raise ValueError(f"within-region SVI cutpoints changed in {r}")
    observed=region_cell_contrasts(residual,regions,quartile,counties,analysis_mask)
    boot=county_block_bootstrap(residual,regions,quartile,counties,analysis_mask)
    for r in REGIONS:
        for q in range(1,5):
            key=f"{r}:q{q}";cell=observed[r][f"q{q}"];ci=boot["cell_smd_ci_95"][key]["ci_95"]
            cell["county_block_bootstrap_ci_95"]=ci
            cell["bootstrap_finite_replicates"]=boot["cell_smd_ci_95"][key]["finite_replicates"]
            cell["finite_ci"]=bool(len(ci)==2 and all(np.isfinite(ci)))
    cells=[(r,f"q{q}",observed[r][f"q{q}"]) for r in REGIONS for q in range(1,5)]
    worst=max(cells,key=lambda x:abs(x[2]["standardized_mean_difference"]))
    max_abs=float(abs(worst[2]["standardized_mean_difference"]))
    supported=all(c["cell_n"]>=MIN_CELL_N and c["finite_ci"] and c["bootstrap_finite_replicates"]==BOOTSTRAP_REPLICATES for _,_,c in cells)
    analysis_hash=values_mask_sha256(geoids,regions,svi,urban,residual,analysis_mask,quartile)
    report={"schema_version":1,"audit":"Region-by-SVI-quartile localization of the prior D1 rural-language residual screen; descriptive safety audit",
      "measure_before":{"source_audit":"d1-rural-language-context-residual-audit","D1_complete_support":before["scope"]["feature_defined_ids"],
        "pooled_county_heldout_delta_r2":before["measure_after"]["pooled"]["delta_r2"],
        "pooled_county_block_ci_95":before["measure_after"]["county_block_bootstrap"]["ci_95_percentile"],
        "pooled_svi_q3_heldout_delta_r2":before["measure_after"]["rural_urban_and_svi_quartile_residual_summaries"]["svi_q3"]["heldout_prediction_delta_r2"],
        "pooled_svi_q3_support":before["measure_after"]["rural_urban_and_svi_quartile_residual_summaries"]["svi_q3"]["n"],
        "svi_q3_previously_worsened_more_than_0_005":True,"source_audit_sha256":sha256_file(PRIOR_REPORT)},
      "method":{"outcome":"Frozen cross-fitted S1 Queen/Rook disagreement residual, unchanged; no target labels or refit.",
        "provided_inputs":"Only provided svi_minority, pct_urban and GEOID from each official region's strata table, plus frozen residual and official GEOIDs. County blocks are the first five GEOID digits.",
        "quartiles":"Within-region empirical quartiles of finite provided svi_minority across official tracts, matching the prior D1 audit's quantile boundaries and right-sided tie assignment. Analysis cells additionally require finite provided pct_urban and frozen S1 residual.",
        "contrast":"For each region and SVI quartile, compare the cell's frozen-residual mean with the remaining complete-support tracts in that region; divide the difference by the pooled within-group sample SD (Cohen standardized mean difference). Positive means higher residual in the quartile cell.",
        "uncertainty":"2,000 paired county-block bootstrap replicates; resample counties with replacement separately within each region and retain every supported tract residual in each sampled block. Percentile intervals for all 16 signed cell SMDs and the maximum absolute SMD statistic.",
        "acceptance":"A descriptive localization is considered supported only if all 16 region×quartile cells have at least 30 complete-support tracts and finite 95% county-block intervals. This support gate is not a signal or promotion gate.",
        "unregistered":True,"no_refit_or_term_promotion":True,"interpretation_limit":"Label-free descriptive residual contrasts; neither target accuracy nor causal evidence."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":provenance["official_geoid_region_sha256"],
        "frozen_residual_defined_ids":int(residual_mask.sum()),"finite_svi_ids":int(valid_svi.sum()),
        "complete_d1_svi_urbanity_residual_ids":int(analysis_mask.sum()),"analysis_mask_sha256":mask_sha256(analysis_mask),
        "values_masks_and_quartiles_sha256":analysis_hash,"regional_svi_quartile_cutpoints":cuts,
        "region_quartile_supports":{r:{q:observed[r][q]["cell_n"] for q in ("q1","q2","q3","q4")} for r in REGIONS},
        "region_counts":{r:{"complete_support":int(np.sum(analysis_mask&(regions==r))),"counties":len(set(counties[analysis_mask&(regions==r)].tolist()))} for r in REGIONS},
        "forbidden_inputs_read":[],"labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,
        "external_demographics_used":False,"removed_or_equivalent_references_read":False,"leaderboard_feedback_used":False,"uploads":0,"estimator_changed":False},
      "measure_after":{"region_by_svi_quartile_residual_contrasts":observed,
        "max_absolute_standardized_mean_difference":{"value":max_abs,"region":worst[0],"quartile":worst[1],
          "county_block_bootstrap_ci_95":boot["max_absolute_smd_ci_95"],"finite_replicates":boot["max_absolute_smd_finite_replicates"]},
        "county_block_bootstrap":boot,"all_16_cells_supported_and_finite_ci":supported},
      "thresholds":{"minimum_cell_tracts_inclusive":MIN_CELL_N,"county_bootstrap_replicates":BOOTSTRAP_REPLICATES,
        "bootstrap_seed":BOOTSTRAP_SEED,"quartile_cell_comparison":"within-region quartile vs remaining D1-complete-support tracts"},
      "acceptance":{"all_16_region_quartile_cells_at_least_30":all(c["cell_n"]>=MIN_CELL_N for _,_,c in cells),
        "all_16_cells_have_finite_county_block_ci":all(c["finite_ci"] and c["bootstrap_finite_replicates"]==BOOTSTRAP_REPLICATES for _,_,c in cells),
        "diagnostic_supported":supported,"interpretation":"supported descriptive localization" if supported else "underpowered; do not infer stable region-level localization"},
      "provenance":{"frozen_S1_residual_artifact":{"path":provenance["frozen_residual_path"],"sha256":provenance["frozen_residual_sha256"]},
        "frozen_S1_residual_report":{"path":str(RESIDUAL_REPORT),"sha256":sha256_file(RESIDUAL_REPORT)},
        "prior_D1_audit":{"path":str(PRIOR_REPORT),"sha256":sha256_file(PRIOR_REPORT)},
        "prior_D1_script":{"path":str(PRIOR_SCRIPT),"sha256":sha256_file(PRIOR_SCRIPT)},
        "provided_strata_by_region":provenance["strata_by_region"],"analysis_input_values_masks_quartiles_sha256":analysis_hash,
        "analysis_script_sha256":sha256_file(Path(__file__).resolve())},
      "interpretation":"All region×quartile cells have adequate observed support and finite bootstrap intervals; the localization is descriptive only. See the reported max absolute SMD and intervals before interpreting whether the earlier pooled SVI-Q3 regression is geographically concentrated." if supported else
        "At least one region×quartile cell lacks 30 tracts or a finite county-block interval; treat this audit as underpowered."}
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""));parser.add_argument("--out",default="reports/d1-svi-region-attribution.json")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out)
    print(json.dumps({"scope":report["scope"],"max_absolute_standardized_mean_difference":report["measure_after"]["max_absolute_standardized_mean_difference"],
      "region_by_svi_quartile_residual_contrasts":report["measure_after"]["region_by_svi_quartile_residual_contrasts"],
      "acceptance":report["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
