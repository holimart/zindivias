#!/usr/bin/env python3
"""Estimate fixed-penalty urban/rural effective-count slope deviations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4p_conditional_innovation import deterministic_folds, parquet_bytes  # noqa: E402
from screen_poi_effective_count_urbanity import (  # noqa: E402
    EFFECTIVE_REPORT, EFFECTIVE_SHA, EXPECTED_N, REGIONS, RESIDUAL_SHA,
    WITHIN_REGION_RATIO, _read_rows, mask_sha256, read_pct_urban, sha256_file,
)
from bias60.inputs import official_scored_pairs  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

N_FOLDS = 5
STRATUM_THRESHOLD = 0.5
RIDGE_ALPHA = 100.0
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261101
MIN_POOLED_DELTA_R2 = 0.005
MIN_CI_LOWER = 0.0
MIN_POSITIVE_REGIONS = 3
MIN_STRATUM_SUPPORT = 500
URBANITY_INTERACTION_REPORT = ROOT / "reports/poi-effective-count-urbanity.json"
EXPECTED_PRIOR_DELTA_R2 = -4.445094831231696e-05


def stratum_sign(pct_urban: np.ndarray, defined: np.ndarray | None = None,
                 threshold: float = STRATUM_THRESHOLD) -> tuple[np.ndarray, np.ndarray]:
    pct = np.asarray(pct_urban, dtype=np.float64)
    if pct.ndim != 1 or not 0. <= threshold <= 1.:
        raise ValueError("pct_urban must be a vector and threshold must be in [0,1]")
    mask = np.isfinite(pct) if defined is None else np.asarray(defined, dtype=bool)
    if mask.shape != pct.shape or np.any(mask & (~np.isfinite(pct) | (pct < 0.) | (pct > 1.))):
        raise ValueError("defined pct_urban values must lie in [0,1]")
    # Official fixed rule: urban at or above 0.5; rural below 0.5.
    urban = mask & (pct >= threshold)
    rural = mask & (pct < threshold)
    sign = np.full(pct.shape, np.nan)
    sign[mask] = np.where(urban[mask], 1., -1.)
    return sign, urban


def standardized_effective_count(values: np.ndarray, regions: np.ndarray,
                                 train: np.ndarray, test: np.ndarray
                                 ) -> tuple[np.ndarray, dict[str, dict[str, float]]]:
    """Standardize log1p effective count within region using training-fold rows only."""
    x, reg = np.asarray(values, dtype=np.float64), np.asarray(regions, dtype=object)
    tr, te = np.asarray(train, dtype=bool), np.asarray(test, dtype=bool)
    if x.ndim != 1 or reg.shape != x.shape or tr.shape != x.shape or te.shape != x.shape:
        raise ValueError("standardization inputs must be aligned vectors")
    if np.any(tr & te) or not tr.any() or not te.any() or np.any((tr | te) & ~np.isfinite(x)):
        raise ValueError("train/test masks must be nonempty, disjoint and finite")
    z = np.full(x.shape, np.nan)
    stats: dict[str, dict[str, float]] = {}
    for region in sorted(set(reg[tr | te].tolist())):
        tr_r, te_r = tr & (reg == region), te & (reg == region)
        if not tr_r.any() or not te_r.any():
            raise ValueError(f"region {region} has empty train or test support")
        mean, sd = float(x[tr_r].mean()), float(x[tr_r].std(ddof=0))
        scale = sd if sd > 0. else 1.
        z[tr_r] = (x[tr_r] - mean) / scale
        z[te_r] = (x[te_r] - mean) / scale
        stats[region] = {"training_mean": mean, "training_sd": sd, "applied_scale": scale,
                         "training_n": int(tr_r.sum()), "heldout_n": int(te_r.sum())}
    return z, stats


def delta_r2(y: np.ndarray, baseline: np.ndarray, augmented: np.ndarray) -> dict[str, float]:
    target, base, full = (np.asarray(v, dtype=np.float64) for v in (y, baseline, augmented))
    if target.ndim != 1 or base.shape != target.shape or full.shape != target.shape or target.size < 2:
        raise ValueError("metric arrays must be aligned and have at least two rows")
    if not (np.isfinite(target).all() and np.isfinite(base).all() and np.isfinite(full).all()):
        raise ValueError("metric arrays must be finite")
    sst = float(np.sum((target - target.mean()) ** 2))
    if sst <= 0.:
        raise ValueError("target must have positive total sum of squares")
    base_sse, full_sse = float(np.sum((target-base)**2)), float(np.sum((target-full)**2))
    return {"baseline_r2": 1. - base_sse/sst, "stratified_model_r2": 1. - full_sse/sst,
            "delta_r2": (base_sse-full_sse)/sst, "baseline_sse": base_sse,
            "stratified_model_sse": full_sse, "heldout_sst": sst}


def county_block_bootstrap(y: np.ndarray, baseline: np.ndarray, augmented: np.ndarray,
                           county_ids: np.ndarray, *, replicates: int = BOOTSTRAP_REPLICATES,
                           seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    target, base, full = (np.asarray(v, dtype=np.float64) for v in (y, baseline, augmented))
    county = np.asarray(county_ids, dtype=object)
    if (target.ndim != 1 or base.shape != target.shape or full.shape != target.shape
            or county.shape != target.shape or target.size < 2 or len(set(county.tolist())) < 2):
        raise ValueError("county bootstrap requires aligned vectors from at least two counties")
    if replicates < 100 or not (np.isfinite(target).all() and np.isfinite(base).all() and np.isfinite(full).all()):
        raise ValueError("bootstrap data must be finite and use at least 100 replicates")
    groups = sorted(set(county.tolist()))
    locations = {group: np.flatnonzero(county == group) for group in groups}
    rng = np.random.default_rng(seed)
    values = np.empty(replicates)
    for b in range(replicates):
        selected = rng.choice(len(groups), size=len(groups), replace=True)
        ix = np.concatenate([locations[groups[i]] for i in selected])
        yb, bb, fb = target[ix], base[ix], full[ix]
        sst = float(np.sum((yb-yb.mean())**2))
        values[b] = ((float(np.sum((yb-bb)**2))-float(np.sum((yb-fb)**2)))/sst) if sst > 0. else np.nan
    finite = values[np.isfinite(values)]
    if finite.size < int(.99*replicates):
        raise ValueError("too many degenerate county bootstrap draws")
    return {"replicates": int(replicates), "seed": int(seed), "cluster_count": len(groups),
            "resampling": "paired whole-county bootstrap with replacement over exact pooled OOF support",
            "ci_95_percentile": [float(np.quantile(finite,.025)), float(np.quantile(finite,.975))],
            "degenerate_replicates": int(replicates-finite.size)}


def fit_fold(target: np.ndarray, base_features: np.ndarray, log_effective: np.ndarray,
             pct_urban: np.ndarray, strata_sign: np.ndarray, regions: np.ndarray,
             train: np.ndarray, test: np.ndarray, *, alpha: float = RIDGE_ALPHA) -> dict[str, Any]:
    y, base = np.asarray(target,float), np.asarray(base_features,float)
    poi, urban = np.asarray(log_effective,float), np.asarray(pct_urban,float)
    sign, reg = np.asarray(strata_sign,float), np.asarray(regions,object)
    tr, te = np.asarray(train,bool), np.asarray(test,bool)
    n = y.size
    if (base.ndim != 2 or base.shape[0] != n or any(v.shape != (n,) for v in (poi,urban,sign,reg,tr,te))
            or np.any(tr&te) or not tr.any() or not te.any()):
        raise ValueError("fold inputs must align with nonempty disjoint train/test masks")
    if np.any((tr|te) & (~np.isfinite(y)|~np.isfinite(poi)|~np.isfinite(urban)|~np.isfinite(sign)|~np.isfinite(base).all(axis=1))):
        raise ValueError("selected fold rows must be finite")
    if alpha != RIDGE_ALPHA:
        raise ValueError("urban/rural deviation ridge alpha is fixed at 100")
    if sorted(set(reg.tolist())) != sorted(REGIONS):
        raise ValueError("all four official regions are required")
    # Same main-effects baseline as the earlier effective-count × urbanity screen.
    base_mean, base_sd = base[tr].mean(axis=0), base[tr].std(axis=0,ddof=0)
    base_scale = np.where(base_sd>0.,base_sd,1.)
    base_z = (base-base_mean)/base_scale
    poi_z, poi_stats = standardized_effective_count(poi,reg,tr,te)
    codes = np.asarray([REGIONS.index(str(r)) for r in reg],dtype=np.int8)
    region_dummies = np.column_stack([(codes==c).astype(float) for c in range(1,len(REGIONS))])
    main = np.column_stack((np.ones(n),region_dummies,base_z,poi_z,urban))
    deviation = poi_z * sign
    full = np.column_stack((main,deviation))
    beta_base,_,rank_base,_ = np.linalg.lstsq(main[tr],y[tr],rcond=None)
    if rank_base != main.shape[1]:
        raise ValueError("main-effects training design is rank deficient")
    # Parameter gamma is the urban/rural deviation: slopes are beta+gamma and beta-gamma.
    # alpha * (delta_urban^2 + delta_rural^2)=2*alpha*gamma^2, with sum-zero deviations.
    xtx,xty=full[tr].T@full[tr],full[tr].T@y[tr]
    penalty=np.zeros(full.shape[1]); penalty[-1]=2.*alpha
    beta_full=np.linalg.solve(xtx+np.diag(penalty),xty)
    beta_unpenalized,_,rank_full,_=np.linalg.lstsq(full[tr],y[tr],rcond=None)
    if rank_full != full.shape[1]:
        raise ValueError("urban/rural deviation training design is rank deficient")
    baseline_pred,full_pred=np.full(n,np.nan),np.full(n,np.nan)
    baseline_pred[te]=main[te]@beta_base
    full_pred[te]=full[te]@beta_full
    return {"baseline_prediction":baseline_pred,"stratified_prediction":full_pred,
            "poi_z":np.where(tr|te,poi_z,np.nan),"urban_rural_deviation_feature":np.where(tr|te,deviation,np.nan),
            "stratum_training_scalers":poi_stats,
            "shared_effective_count_slope":float(beta_full[-3]),
            "urban_deviation":float(beta_full[-1]),
            "urban_slope":float(beta_full[-3]+beta_full[-1]),
            "rural_slope":float(beta_full[-3]-beta_full[-1]),
            "ridge_deviation_coefficient":float(beta_full[-1]),
            "unpenalized_deviation_coefficient":float(beta_unpenalized[-1]),
            "ridge_to_unpenalized_deviation_abs_ratio":(abs(float(beta_full[-1]/beta_unpenalized[-1])) if beta_unpenalized[-1]!=0. else None),
            "penalty":float(alpha),"training_only_base_scaler":{"mean":base_mean.tolist(),"sd":base_sd.tolist(),"applied_scale":base_scale.tolist()}}


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    prior=json.loads(URBANITY_INTERACTION_REPORT.read_text(encoding="utf-8"))
    prior_delta=float(prior["measure_after"]["pooled"]["delta_r2"])
    prior_ci=prior["measure_after"]["bootstrap"]["ci_95_percentile"]
    if not np.isclose(prior_delta,EXPECTED_PRIOR_DELTA_R2,rtol=0.,atol=1e-14) or not (prior_ci[0]<=0.<=prior_ci[1]):
        raise ValueError("prior effective-count × urbanity MEASURE-BEFORE changed")
    effective_report=json.loads(EFFECTIVE_REPORT.read_text(encoding="utf-8"))
    if effective_report["scope"]["effective_count_defined_ids"]!=9327 or not np.isclose(
        effective_report["measure_after"]["crossfit_residual_variance_ratio"],WITHIN_REGION_RATIO,rtol=0.,atol=1e-15):
        raise ValueError("effective-count source MEASURE-BEFORE changed")
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):
        raise ValueError("official IDs are not exact 9,379 GEOID-region pairs")
    geoids=np.asarray([g for g,_ in official],object); regions=np.asarray([r for _,r in official],object)
    official_sha=hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    residual_path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    residual,residual_meta=_read_rows(residual_path,RESIDUAL_SHA,["GEOID","region","fold","crossfit_residual","crossfit_joint_defined"],official)
    effective_path=Path(effective_report["provenance"]["output_artifact"]["path"])
    effective,effective_meta=_read_rows(effective_path,EFFECTIVE_SHA,["GEOID","region","unique_assigned_place_count","effective_primary_category_count","effective_count_defined","Mx4_F","Mx4_F_defined","S1_lagged_Mx4_F","S1_lagged_Mx4_F_defined"],official)
    pct_urban,urban_provenance=read_pct_urban(workspace,official)
    urban_defined=np.isfinite(pct_urban)
    folds=np.full(EXPECTED_N,-1,dtype=np.int8)
    for region in REGIONS:
        ix=np.flatnonzero(regions==region); folds[ix]=deterministic_folds(geoids[ix].tolist(),region,N_FOLDS)
    if not np.array_equal(folds,residual["fold"].astype(np.int8)):
        raise ValueError("frozen residual folds differ from deterministic five GEOID folds")
    residual_mask=residual["crossfit_joint_defined"]; poi_mask=effective["effective_count_defined"]
    for vals,mask,name in ((residual["crossfit_residual"],residual_mask,"residual"),
        (effective["effective_primary_category_count"],poi_mask,"effective count"),
        (effective["Mx4_F"],effective["Mx4_F_defined"],"Mx4"),
        (effective["S1_lagged_Mx4_F"],effective["S1_lagged_Mx4_F_defined"],"S1 lag")):
        if not np.array_equal(np.isfinite(vals),mask): raise ValueError(f"frozen values/mask mismatch: {name}")
    place_count=effective["unique_assigned_place_count"]
    poi_count=effective["effective_primary_category_count"]
    log_poi=np.full(EXPECTED_N,np.nan); log_poi[poi_mask]=np.log1p(poi_count[poi_mask])
    log_place=np.full(EXPECTED_N,np.nan); place_defined=np.isfinite(place_count)&(place_count>=0.)
    log_place[place_defined]=np.log1p(place_count[place_defined])
    strata,urban_mask=stratum_sign(pct_urban,urban_defined)
    support=(residual_mask&poi_mask&urban_defined&place_defined&effective["Mx4_F_defined"]&effective["S1_lagged_Mx4_F_defined"]
             &np.isfinite(log_poi)&np.isfinite(log_place))
    if support.sum()!=9308: raise ValueError(f"paired support changed from prior interaction screen: {int(support.sum())}")
    rural=support&~urban_mask; urban=support&urban_mask
    if rural.sum()<MIN_STRATUM_SUPPORT or urban.sum()<MIN_STRATUM_SUPPORT:
        raise ValueError("fixed urban/rural strata lack required minimum support")
    base=np.column_stack((log_place,effective["Mx4_F"],effective["S1_lagged_Mx4_F"]))
    outcome=residual["crossfit_residual"]
    baseline_oof=np.full(EXPECTED_N,np.nan); stratified_oof=np.full(EXPECTED_N,np.nan)
    z_oof=np.full(EXPECTED_N,np.nan); deviation_oof=np.full(EXPECTED_N,np.nan)
    fold_details=[]
    for fold in range(N_FOLDS):
        test=support&(folds==fold); train=support&(folds!=fold)
        fit=fit_fold(outcome,base,log_poi,pct_urban,strata,regions,train,test)
        baseline_oof[test]=fit["baseline_prediction"][test]; stratified_oof[test]=fit["stratified_prediction"][test]
        z_oof[test]=fit["poi_z"][test]; deviation_oof[test]=fit["urban_rural_deviation_feature"][test]
        fold_details.append({"fold":fold,"train_n":int(train.sum()),"heldout_n":int(test.sum()),
            "training_urban_n":int(np.sum(train&urban_mask)),"training_rural_n":int(np.sum(train&~urban_mask)),
            "heldout_urban_n":int(np.sum(test&urban_mask)),"heldout_rural_n":int(np.sum(test&~urban_mask)),
            **delta_r2(outcome[test],baseline_oof[test],stratified_oof[test]),
            **{k:v for k,v in fit.items() if k not in ("baseline_prediction","stratified_prediction","poi_z","urban_rural_deviation_feature")}})
    if np.any(support&(~np.isfinite(baseline_oof)|~np.isfinite(stratified_oof))) or np.any(~support&(np.isfinite(baseline_oof)|np.isfinite(stratified_oof))):
        raise AssertionError("OOF prediction masks differ from paired support")
    pooled=delta_r2(outcome[support],baseline_oof[support],stratified_oof[support])
    county=np.asarray([g[:5] for g in geoids],object)
    bootstrap=county_block_bootstrap(outcome[support],baseline_oof[support],stratified_oof[support],county[support])
    regional={}
    for region in REGIONS:
        use=support&(regions==region)
        regional[region]={"n":int(use.sum()),**delta_r2(outcome[use],baseline_oof[use],stratified_oof[use])}
    stratum_metrics={"urban": {"n":int(urban.sum()),**delta_r2(outcome[urban],baseline_oof[urban],stratified_oof[urban])},
                     "rural": {"n":int(rural.sum()),**delta_r2(outcome[rural],baseline_oof[rural],stratified_oof[rural])}}
    positive_regions=sum(v["delta_r2"]>0. for v in regional.values())
    ci_low,ci_high=bootstrap["ci_95_percentile"]
    gate=bool(pooled["delta_r2"]>=MIN_POOLED_DELTA_R2 and ci_low>MIN_CI_LOWER
              and min(urban.sum(),rural.sum())>=MIN_STRATUM_SUPPORT and positive_regions>=MIN_POSITIVE_REGIONS)
    table=pa.table({
        "GEOID":pa.array(geoids.tolist(),type=pa.string()),"region":pa.array(regions.tolist(),type=pa.string()),
        "county_fips":pa.array([g[:5] for g in geoids],type=pa.string()),"fold":pa.array(folds,type=pa.int8()),
        "pct_urban":pa.array([float(v) if ok else None for v,ok in zip(pct_urban,urban_defined)],type=pa.float64()),
        "urban_stratum":pa.array([bool(v) if ok else None for v,ok in zip(urban_mask,urban_defined)],type=pa.bool_()),
        "urbanity_defined":pa.array(urban_defined,type=pa.bool_()),
        "effective_primary_category_count":pa.array([float(v) if ok else None for v,ok in zip(poi_count,poi_mask)],type=pa.float64()),
        "log1p_effective_primary_category_count":pa.array([float(v) if ok else None for v,ok in zip(log_poi,poi_mask)],type=pa.float64()),
        "unique_assigned_place_count":pa.array([int(v) if np.isfinite(v) else None for v in place_count],type=pa.int64()),
        "log1p_unique_place_count":pa.array([float(v) if ok else None for v,ok in zip(log_place,place_defined)],type=pa.float64()),
        "frozen_s1_residual":pa.array([float(v) if ok else None for v,ok in zip(outcome,residual_mask)],type=pa.float64()),
        "s1_residual_defined":pa.array(residual_mask,type=pa.bool_()),
        "Mx4_F":pa.array([float(v) if ok else None for v,ok in zip(effective["Mx4_F"],effective["Mx4_F_defined"])],type=pa.float64()),
        "S1_lagged_Mx4_F":pa.array([float(v) if ok else None for v,ok in zip(effective["S1_lagged_Mx4_F"],effective["S1_lagged_Mx4_F_defined"])],type=pa.float64()),
        "paired_screen_defined":pa.array(support,type=pa.bool_()),
        "within_region_training_z_effective_count":pa.array([float(v) if ok else None for v,ok in zip(z_oof,support)],type=pa.float64()),
        "urban_rural_deviation_term":pa.array([float(v) if ok else None for v,ok in zip(deviation_oof,support)],type=pa.float64()),
        "baseline_oof_prediction":pa.array([float(v) if ok else None for v,ok in zip(baseline_oof,support)],type=pa.float64()),
        "stratified_slope_oof_prediction":pa.array([float(v) if ok else None for v,ok in zip(stratified_oof,support)],type=pa.float64())})
    payload=parquet_bytes(table)
    if payload!=parquet_bytes(table): raise ValueError("stratified artifact serialization is nondeterministic")
    artifact_path.parent.mkdir(parents=True,exist_ok=True); artifact_path.write_bytes(payload)
    report={
      "schema_version":1,"screen":"Fixed-alpha urban/rural effective-count slope-deviation screen against frozen S1 residual; unregistered",
      "measure_before":{"global_interaction_delta_r2":prior_delta,"global_interaction_ci_95":prior_ci,
        "global_interaction_positive_folds":prior["measure_after"]["positive_folds"],
        "effective_count_residual_variance_ratio":WITHIN_REGION_RATIO,"prior_interaction_report_sha256":sha256_file(URBANITY_INTERACTION_REPORT)},
      "method":{"outcome":"Frozen cross-fitted orthogonalized S1 disagreement residual; no refitting.",
        "baseline":"Same region-intercept, log1p unique-place count, Mx4 F, S1 lag, within-region training-standardized log1p effective count main effect, and provided pct_urban main effect as the prior urbanity-interaction screen.",
        "strata":"Fixed urban if pct_urban >= 0.5, rural if pct_urban < 0.5; no tuning.",
        "augmented_model":"One common effective-count slope plus urban/rural deviations constrained to sum to zero. With sign +1 for urban and -1 for rural, add one standardized effective-count × sign contrast; resulting slopes are common + gamma (urban) and common - gamma (rural).",
        "penalty":"Fixed alpha=100 on sum of squared urban/rural deviations only, equivalent to a 2*alpha penalty on gamma; common slope and baseline terms unpenalized.",
        "scaling":"Baseline continuous predictors training-standardized per fold; effective-count log1p standardized separately within each official region using training rows only. pct_urban main effect stays on its provided [0,1] scale.",
        "validation":"Exact frozen five GEOID folds; nested models fit on other four folds and evaluated on paired held-out support.",
        "uncertainty":"Paired whole-county bootstrap CI with replacement over pooled OOF support.",
        "metric":"Held-out incremental delta-R2=(baseline SSE-stratified model SSE)/shared residual SST; pooled, stratum, and regional estimates reported.",
        "support":"Exact official-ID intersection of frozen residual, effective count, pct_urban, unique-place count, Mx4 F and S1 lag.",
        "unregistered":True,"interpretation_limit":"Label-free provided-feature association screen only; not target accuracy, causality, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":official_sha,
        "paired_support_ids":int(support.sum()),"urban_defined_ids":int(urban_defined.sum()),
        "paired_urban_n":int(urban.sum()),"paired_rural_n":int(rural.sum()),
        "urbanity_mask_sha256":mask_sha256(urban_defined),"stratum_assignment_sha256":mask_sha256(urban_mask),
        "residual_mask_sha256":mask_sha256(residual_mask),"effective_count_mask_sha256":mask_sha256(poi_mask),
        "paired_mask_sha256":mask_sha256(support),"fold_assignment_sha256":hashlib.sha256(folds.tobytes()).hexdigest(),
        "fold_support":{str(f):int(np.sum(support&(folds==f))) for f in range(N_FOLDS)},
        "labels_read":False,"sample_score_values_read":False,"removed_or_equivalent_references_read":False,
        "external_data_used":[],"changed_folds":False,"posthoc_thresholding":False,
        "leaderboard_feedback_used":False,"uploads":0},
      "measure_after":{"pooled":pooled,"county_block_bootstrap":bootstrap,"paired_pooled_delta_r2_ci_95":bootstrap["ci_95_percentile"],
        "strata":stratum_metrics,"regional":regional,"positive_regions":positive_regions,"folds":fold_details},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_POOLED_DELTA_R2,"bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,
        "minimum_support_per_stratum":MIN_STRATUM_SUPPORT,"minimum_positive_regions":MIN_POSITIVE_REGIONS,"fixed_alpha":RIDGE_ALPHA,"fixed_pct_urban_cut":STRATUM_THRESHOLD},
      "acceptance":{"pooled_delta_r2_at_least_0_005":bool(pooled["delta_r2"]>=MIN_POOLED_DELTA_R2),
        "county_bootstrap_ci_lower_above_zero":bool(ci_low>MIN_CI_LOWER),
        "both_strata_have_at_least_500":bool(min(urban.sum(),rural.sum())>=MIN_STRATUM_SUPPORT),
        "positive_delta_in_at_least_3_of_4_regions":bool(positive_regions>=MIN_POSITIVE_REGIONS),"stratified_slope_screen_pass":gate},
      "provenance":{"official_geoid_region_sha256":official_sha,"frozen_residual_artifact":residual_meta,
        "effective_count_artifact":effective_meta,"pct_urban_by_region":urban_provenance,
        "effective_count_report":{"path":str(EFFECTIVE_REPORT),"sha256":sha256_file(EFFECTIVE_REPORT)},
        "prior_interaction_report":{"path":str(URBANITY_INTERACTION_REPORT),"sha256":sha256_file(URBANITY_INTERACTION_REPORT)},
        "output_artifact":{"path":str(artifact_path),"sha256":hashlib.sha256(payload).hexdigest(),"rows":table.num_rows,"columns":table.column_names,"deterministic_serialization":True},
        "script_sha256":sha256_file(Path(__file__).resolve())},
      "interpretation":"Fixed-alpha urban/rural effective-count slope deviations pass all gates; this remains descriptive-only and does not establish target accuracy or authorize integration." if gate else
        "Fixed-alpha urban/rural effective-count slope deviations failed one or more preregistered gates; retire the stratified interaction and keep the feature descriptive-only."}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/poi-effective-count-urbanity-stratified.json")
    parser.add_argument("--artifact",default=None)
    args=parser.parse_args(); workspace=Path(args.workspace)
    if not workspace.is_dir(): parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact=Path(args.artifact) if args.artifact else workspace/"poi-effective-count-urbanity-stratified/paired-oof-predictions.parquet"
    report=run(workspace,ROOT/args.out,artifact)
    print(json.dumps({"scope":report["scope"],"measure_after":report["measure_after"],"acceptance":report["acceptance"],"output_artifact":report["provenance"]["output_artifact"]},indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
