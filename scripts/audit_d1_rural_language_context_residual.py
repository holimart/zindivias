#!/usr/bin/env python3
"""County-held-out screen of provided rural language-vulnerability context vs frozen S1 residual."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from bias60.inputs import official_scored_pairs  # noqa: E402

EXPECTED_N=9379
RESIDUAL_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
BOOTSTRAP_REPLICATES=2000
BOOTSTRAP_SEED=20261031
MIN_POOLED_DELTA_R2=0.002
MIN_CI_LOWER=0.0
MAX_STRATUM_DECLINE=0.005
MIN_STRATUM_N=100


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def mask_sha256(mask:np.ndarray)->str:
    return hashlib.sha256(np.packbits(np.asarray(mask,dtype=np.uint8),bitorder="little").tobytes()).hexdigest()


def fit_region_fold(y:np.ndarray,term:np.ndarray,train:np.ndarray,test:np.ndarray)->dict[str,Any]:
    """Compare train-mean baseline with intercept + one train-standardized fixed term."""
    outcome=np.asarray(y,dtype=np.float64); x=np.asarray(term,dtype=np.float64)
    tr=np.asarray(train,dtype=bool); te=np.asarray(test,dtype=bool)
    if outcome.ndim!=1 or x.shape!=outcome.shape or tr.shape!=outcome.shape or te.shape!=outcome.shape:
        raise ValueError("outcome, term and fold masks must be aligned vectors")
    if np.any(tr&te) or not tr.any() or not te.any() or np.any((tr|te)&(~np.isfinite(outcome)|~np.isfinite(x))):
        raise ValueError("fold must have nonempty disjoint train/test rows with finite values")
    mean=float(x[tr].mean()); sd=float(x[tr].std(ddof=0)); scale=sd if sd>0. else 1.
    z=(x-mean)/scale
    design=np.column_stack((np.ones(int(tr.sum())),z[tr]))
    beta,_,rank,_=np.linalg.lstsq(design,outcome[tr],rcond=None)
    if rank!=2:raise ValueError("training fold interaction design is rank deficient")
    baseline=float(outcome[tr].mean())
    return {"baseline_prediction":np.full(int(te.sum()),baseline),
      "augmented_prediction":np.column_stack((np.ones(int(te.sum())),z[te]))@beta,
      "intercept":float(beta[0]),"standardized_term_coefficient":float(beta[1]),
      "raw_term_coefficient":float(beta[1]/scale),"training_term_mean":mean,
      "training_term_sd":sd,"training_term_scale":scale,"training_n":int(tr.sum()),
      "heldout_n":int(te.sum()),"training_mean_residual":baseline,"design_rank":int(rank)}


def paired_delta_r2(y:np.ndarray,baseline:np.ndarray,augmented:np.ndarray)->dict[str,float]:
    yy=np.asarray(y,dtype=np.float64); b=np.asarray(baseline,dtype=np.float64); a=np.asarray(augmented,dtype=np.float64)
    if yy.ndim!=1 or b.shape!=yy.shape or a.shape!=yy.shape or not yy.size or not(np.isfinite(yy).all() and np.isfinite(b).all() and np.isfinite(a).all()):
        raise ValueError("paired score vectors must be aligned, finite and nonempty")
    sst=float(np.sum((yy-yy.mean())**2))
    if sst<=0.:raise ValueError("heldout outcome has zero variance")
    bs=float(np.sum((yy-b)**2)); ass=float(np.sum((yy-a)**2))
    return {"n":int(len(yy)),"baseline_r2":1.-bs/sst,"augmented_r2":1.-ass/sst,
      "delta_r2":(bs-ass)/sst,"baseline_sse":bs,"augmented_sse":ass,"heldout_sst":sst}


def county_block_bootstrap(y:np.ndarray,baseline:np.ndarray,augmented:np.ndarray,
                           regions:np.ndarray,counties:np.ndarray,*,replicates:int=BOOTSTRAP_REPLICATES,
                           seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    yy=np.asarray(y,dtype=np.float64); b=np.asarray(baseline,dtype=np.float64); a=np.asarray(augmented,dtype=np.float64)
    reg=np.asarray(regions,dtype=object); county=np.asarray(counties,dtype=object)
    if yy.ndim!=1 or any(v.shape!=yy.shape for v in (b,a,reg,county)) or not np.isfinite(np.c_[yy,b,a]).all():
        raise ValueError("bootstrap inputs must be aligned and finite")
    if replicates<100:raise ValueError("at least 100 county-block bootstrap replicates required")
    by_region={r:{c:np.flatnonzero((reg==r)&(county==c)) for c in sorted(set(county[reg==r].tolist()))} for r in REGIONS}
    rng=np.random.default_rng(seed); estimates=np.empty(replicates); degenerate=0
    for k in range(replicates):
        blocks=[]
        for r in REGIONS:
            counties_r=list(by_region[r])
            sampled=rng.choice(counties_r,size=len(counties_r),replace=True)
            blocks.extend(by_region[r][c] for c in sampled)
        ix=np.concatenate(blocks); yb=yy[ix]; sst=float(np.sum((yb-yb.mean())**2))
        if sst<=0.:estimates[k]=np.nan;degenerate+=1
        else:estimates[k]=(float(np.sum((yb-b[ix])**2))-float(np.sum((yb-a[ix])**2)))/sst
    good=estimates[np.isfinite(estimates)]
    if good.size<int(.99*replicates):raise ValueError("too many degenerate county bootstrap samples")
    return {"replicates":replicates,"seed":seed,
      "resampling":"paired leave-one-county-out predictions; counties resampled with replacement within each official region",
      "ci_95_percentile":[float(np.quantile(good,.025)),float(np.quantile(good,.975))],
      "degenerate_replicates":degenerate}


def _summary(y:np.ndarray,mask:np.ndarray)->dict[str,Any]:
    vals=np.asarray(y,dtype=np.float64)[np.asarray(mask,dtype=bool)]
    if not vals.size:return {"n":0,"mean":None,"median":None,"median_absolute":None,"p90_absolute":None}
    return {"n":int(vals.size),"mean":float(vals.mean()),"median":float(np.median(vals)),
      "median_absolute":float(np.median(np.abs(vals))),"p90_absolute":float(np.quantile(np.abs(vals),.9)),
      "min":float(vals.min()),"max":float(vals.max())}


def _load(workspace:Path)->tuple[list[tuple[str,str]],np.ndarray,np.ndarray,np.ndarray,np.ndarray,dict[str,Any]]:
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):
        raise ValueError("official IDs must be an exact four-region 9,379-row partition")
    official_hash=hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    locations={pair:i for i,pair in enumerate(official)}; n=len(official)
    residual_path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(residual_path)!=RESIDUAL_SHA:raise ValueError("frozen S1 residual artifact hash mismatch")
    t=pq.read_table(residual_path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if t.num_rows!=n:raise ValueError("frozen residual row count differs from official scope")
    y=np.full(n,np.nan); resid_defined=np.zeros(n,dtype=bool); seen=set()
    for row in t.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in locations or pair in seen:raise ValueError(f"unexpected/duplicate frozen residual ID {pair}")
        seen.add(pair);i=locations[pair];resid_defined[i]=bool(row["crossfit_joint_defined"])
        if row["crossfit_residual"] is not None:y[i]=float(row["crossfit_residual"])
    if seen!=set(official) or not resid_defined.all() or not np.isfinite(y).all():raise ValueError("frozen residual must align and be defined for all official IDs")
    svi=np.full(n,np.nan); urban=np.full(n,np.nan); inputs={}
    for region in REGIONS:
        path=workspace/"defined-flags-inputs"/f"{region}-strata-tract-table.parquet"
        if not path.is_file():raise ValueError(f"provided strata table missing for {region}")
        tab=pq.read_table(path,columns=["GEOID","svi_minority","pct_urban"])
        region_pairs={g for g,r in official if r==region}; seen_local=set()
        for row in tab.to_pylist():
            geoid=str(row["GEOID"])
            if geoid not in region_pairs:continue
            if geoid in seen_local:raise ValueError(f"duplicate strata GEOID {geoid}")
            seen_local.add(geoid);i=locations[(geoid,region)]
            if row["svi_minority"] is not None:svi[i]=float(row["svi_minority"])
            if row["pct_urban"] is not None:urban[i]=float(row["pct_urban"])
        if seen_local!=region_pairs:raise ValueError(f"{region} strata IDs differ from official list")
        idx=np.flatnonzero(np.asarray([r==region for _,r in official]))
        inputs[region]={"path":str(path),"sha256":sha256_file(path),"columns_read":["GEOID","svi_minority","pct_urban"],
          "official_ids":len(idx),"svi_defined":int(np.isfinite(svi[idx]).sum()),"pct_urban_defined":int(np.isfinite(urban[idx]).sum())}
    if np.any(np.isfinite(svi)&((svi<0.)|(svi>1.))) or np.any(np.isfinite(urban)&((urban<0.)|(urban>1.))):
        raise ValueError("provided svi_minority and pct_urban must lie in [0,1]")
    return official,y,svi,urban,resid_defined,{"official_hash":official_hash,"residual_path":str(residual_path),
      "residual_sha256":RESIDUAL_SHA,"strata_inputs":inputs}


def run(workspace:Path,report_path:Path)->dict[str,Any]:
    source_report=json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    if (source_report["scope"]["official_ids"]!=EXPECTED_N or source_report["scope"]["crossfit_joint_defined_ids"]!=EXPECTED_N
            or source_report["provenance"]["output_artifact"]["sha256"]!=RESIDUAL_SHA):
        raise ValueError("frozen S1 residual report support or artifact hash changed")
    official,y,svi,urban,residual_mask,provenance=_load(workspace)
    if provenance["official_hash"]!=source_report["provenance"]["official_geoid_region_sha256"]:
        raise ValueError("official GEOID/region partition differs from frozen S1 report")
    geoids=np.asarray([g for g,_ in official],dtype=object); regions=np.asarray([r for _,r in official],dtype=object)
    counties=np.asarray([g[:5] for g in geoids],dtype=object)
    feature_defined=np.isfinite(svi)&np.isfinite(urban)&residual_mask
    term=np.full(len(y),np.nan);term[feature_defined]=svi[feature_defined]*(1.-urban[feature_defined])
    if int(feature_defined.sum())!=9330:
        raise ValueError("complete D1 rural-language product support differs from frozen 9,330-tract measure-before")
    if not feature_defined.any():raise ValueError("no complete provided D1 rural-language context support")
    baseline_oof=np.full(len(y),np.nan);augmented_oof=np.full(len(y),np.nan)
    fold_rows=[]; region_coverage={};
    for region in REGIONS:
        in_region=(regions==region); complete=in_region&feature_defined
        region_counties=sorted(set(counties[complete].tolist()))
        if len(region_counties)<2:raise ValueError(f"{region} needs at least two supported counties")
        region_coverage[region]={"official_ids":int(in_region.sum()),"residual_defined_ids":int((in_region&residual_mask).sum()),
          "svi_defined_ids":int((in_region&np.isfinite(svi)).sum()),"pct_urban_defined_ids":int((in_region&np.isfinite(urban)).sum()),
          "term_defined_ids":int(complete.sum()),"heldout_counties":len(region_counties)}
        for county in region_counties:
            test=complete&(counties==county);train=complete&in_region&(counties!=county)
            # Each fit uses only its region's remaining counties; never use the held-out county for scaling or fitting.
            local_fit=fit_region_fold(y[in_region],term[in_region],train[in_region],test[in_region])
            local_idx=np.flatnonzero(in_region);test_local=np.flatnonzero(test[in_region])
            baseline_oof[local_idx[test_local]]=local_fit["baseline_prediction"]
            augmented_oof[local_idx[test_local]]=local_fit["augmented_prediction"]
            fold_rows.append({"region":region,"heldout_county_fips":county,
              "heldout_n":local_fit["heldout_n"],"training_n":local_fit["training_n"],
              "baseline_intercept":local_fit["training_mean_residual"],
              "augmented_intercept":local_fit["intercept"],
              "standardized_term_coefficient":local_fit["standardized_term_coefficient"],
              "raw_term_coefficient":local_fit["raw_term_coefficient"],
              "training_term_mean":local_fit["training_term_mean"],"training_term_sd":local_fit["training_term_sd"],
              "design_rank":local_fit["design_rank"]})
    if np.any(feature_defined&(~np.isfinite(baseline_oof)|~np.isfinite(augmented_oof))) or np.any(~feature_defined&(np.isfinite(baseline_oof)|np.isfinite(augmented_oof))):
        raise AssertionError("county-held-out prediction mask differs from complete feature support")
    pooled=paired_delta_r2(y[feature_defined],baseline_oof[feature_defined],augmented_oof[feature_defined])
    boot=county_block_bootstrap(y[feature_defined],baseline_oof[feature_defined],augmented_oof[feature_defined],
      regions[feature_defined],counties[feature_defined])
    regional={}
    for region in REGIONS:
        use=feature_defined&(regions==region)
        regional[region]={"official_ids":region_coverage[region]["official_ids"],
          "complete_support_ids":int(use.sum()),"heldout_counties":region_coverage[region]["heldout_counties"],
          **paired_delta_r2(y[use],baseline_oof[use],augmented_oof[use])}
    rural=np.full(len(y),False);urban_group=np.full(len(y),False)
    urban_defined=np.isfinite(urban);rural[urban_defined]=urban[urban_defined]<.5;urban_group[urban_defined]=urban[urban_defined]>=.5
    svi_quartile=np.full(len(y),-1,dtype=np.int8);svi_cuts={}
    for region in REGIONS:
        idx=np.flatnonzero((regions==region)&np.isfinite(svi))
        cuts=np.quantile(svi[idx],[.25,.5,.75])
        svi_cuts[region]=[float(v) for v in cuts]
        svi_quartile[idx]=np.searchsorted(cuts,svi[idx],side="right").astype(np.int8)+1
    strata={};stratum_metrics={}
    groups=[("rural",feature_defined&rural),("urban",feature_defined&urban_group)]
    groups.extend((f"svi_q{q}",feature_defined&(svi_quartile==q)) for q in range(1,5))
    for label,mask in groups:
        n_group=int(mask.sum()); summary=_summary(y,mask)
        metric=paired_delta_r2(y[mask],baseline_oof[mask],augmented_oof[mask]) if n_group>=2 and np.var(y[mask])>0 else None
        strata[label]={**summary,"heldout_prediction_delta_r2":metric["delta_r2"] if metric else None,
          "baseline_r2":metric["baseline_r2"] if metric else None,"augmented_r2":metric["augmented_r2"] if metric else None,
          "support_sufficient_for_gate":n_group>=MIN_STRATUM_N}
        if n_group>=MIN_STRATUM_N:
            stratum_metrics[label]=metric["delta_r2"] if metric else None
    worst_stratum=min((v for v in stratum_metrics.values() if v is not None),default=None)
    region_residual_summary={}
    for r in REGIONS:
        use=feature_defined&(regions==r)
        region_residual_summary[r]=_summary(y,use)
    ci_low=boot["ci_95_percentile"][0]
    gate=bool(pooled["delta_r2"]>=MIN_POOLED_DELTA_R2 and ci_low>MIN_CI_LOWER
      and worst_stratum is not None and worst_stratum>=-MAX_STRATUM_DECLINE)
    config={"term":"svi_minority * (1 - pct_urban)","county_holdout":"leave one five-digit GEOID county out within each official region",
      "baseline":"training-region mean S1 residual","augmented":"training-region intercept and one linear term",
      "scaling":"term mean and population SD computed on training tracts from other counties within the same region only",
      "rural_cutoff":"pct_urban < 0.5 rural; >=0.5 urban","svi_quartiles":"within-region empirical quartiles of provided svi_minority; thresholds descriptive and target-independent",
      "minimum_stratum_n":MIN_STRATUM_N,"bootstrap_replicates":BOOTSTRAP_REPLICATES,"bootstrap_seed":BOOTSTRAP_SEED,
      "thresholds":{"pooled_delta_r2":MIN_POOLED_DELTA_R2,"ci_lower_strictly_positive":MIN_CI_LOWER,
        "minimum_stratum_delta_r2":-MAX_STRATUM_DECLINE}}
    config_hash=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    feature_hash=hashlib.sha256("\n".join(f"{g},{r},{format(float(s),'.17g') if np.isfinite(s) else ''},{format(float(u),'.17g') if np.isfinite(u) else ''},{format(float(v),'.17g') if ok else ''},{int(ok)}" for g,r,s,u,v,ok in zip(geoids,regions,svi,urban,term,feature_defined)).encode()).hexdigest()
    report={"schema_version":1,"audit":"County-held-out association of provided rural language-vulnerability context with frozen S1 disagreement residual; diagnostic only",
      "measure_before":{"D1_feature_implemented_ids":9330,"direct_residual_interaction_measured":False,
        "frozen_s1_residual_source_report_sha256":sha256_file(RESIDUAL_REPORT)},
      "method":{"outcome":"Frozen orthogonalized S1 Queen/Rook disagreement residual consumed unchanged; not a target label.",
        "feature":"One preregistered product: provided svi_minority × (1 - provided pct_urban). No D1 RUCA blend, external demographics, or additional interaction terms.",
        "cross_validation":"Leave one supported five-digit county GEOID out at a time within each official region; train/held-out counties are disjoint.",
        "baseline":"Training-region mean residual; augmented model is training-region intercept plus the single linear product term.",
        "scaling":"The interaction is standardized within each official region using training counties only (training tract mean and population SD); held-out county values use those frozen fold moments.",
        "metric":"Pooled county-held-out ΔR²=(baseline OOF SSE-augmented OOF SSE)/shared S1 residual SST.",
        "confidence_interval":"Paired county-block bootstrap resampling held-out county OOF blocks with replacement within each official region.",
        "strata":"Rural/urban uses provided pct_urban <0.5 / >=0.5. SVI quartiles are computed within official region from provided svi_minority, without outcome use; report raw frozen-residual summaries and paired held-out ΔR² by pooled stratum.",
        "minimum_supported_stratum_n":MIN_STRATUM_N,"unregistered":True,
        "interpretation_limit":"Label-free provided-feature association only; not target accuracy, causal evidence, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":provenance["official_hash"],
        "residual_defined_ids":int(residual_mask.sum()),"feature_defined_ids":int(feature_defined.sum()),
        "feature_undefined_ids":int((~feature_defined).sum()),"feature_mask_sha256":mask_sha256(feature_defined),
        "feature_values_and_masks_sha256":feature_hash,"region_coverage":region_coverage,
        "labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,
        "external_demographic_data_used":False,"removed_or_equivalent_references_read":False,
        "leaderboard_feedback_used":False,"estimator_integration":False,"uploads":0},
      "measure_after":{"pooled":pooled,"county_block_bootstrap":boot,"regional":regional,
        "residual_summaries_by_region":region_residual_summary,"rural_urban_and_svi_quartile_residual_summaries":strata,
        "within_region_svi_quartile_cutpoints":svi_cuts,"worst_sufficient_stratum_delta_r2":worst_stratum,
        "county_fold_count":len(fold_rows),"county_folds":fold_rows},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_POOLED_DELTA_R2,
        "bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,"maximum_allowed_sufficient_stratum_decline":MAX_STRATUM_DECLINE,
        "minimum_supported_stratum_n":MIN_STRATUM_N},
      "acceptance":{"pooled_delta_r2_at_least_0_002":bool(pooled["delta_r2"]>=MIN_POOLED_DELTA_R2),
        "county_block_ci_lower_above_zero":bool(ci_low>MIN_CI_LOWER),
        "no_sufficient_stratum_decline_exceeds_0_005":bool(worst_stratum is not None and worst_stratum>=-MAX_STRATUM_DECLINE),
        "screen_pass":gate},
      "provenance":{"frozen_residual_report":{"path":str(RESIDUAL_REPORT),"sha256":sha256_file(RESIDUAL_REPORT)},
        "frozen_residual_artifact":{"path":provenance["residual_path"],"sha256":provenance["residual_sha256"]},
        "provided_strata_inputs":provenance["strata_inputs"],"official_geoid_region_sha256":provenance["official_hash"],
        "config":config,"config_sha256":config_hash,"script_sha256":sha256_file(Path(__file__).resolve())},
      "interpretation":"The single-term county-held-out screen passed every predeclared threshold; keep diagnostic-only pending separate review." if gate else
        "The single-term county-held-out screen failed one or more predeclared thresholds; retain descriptive results only and do not integrate."}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/d1-rural-language-context-residual-audit.json")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],
      "ci":report["measure_after"]["county_block_bootstrap"]["ci_95_percentile"],
      "regional":report["measure_after"]["regional"],"strata":report["measure_after"]["rural_urban_and_svi_quartile_residual_summaries"],
      "acceptance":report["acceptance"],"config_sha256":report["provenance"]["config_sha256"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
