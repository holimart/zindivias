#!/usr/bin/env python3
"""County-held-out transfer screen for provided M2/M3 POI metadata covariates."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4p_conditional_innovation import read_emission, sha256_file  # noqa: E402
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
M2_SHA = "88aa794ff46b30824781d933cac2f6f4fb1898654c8c7726990e1ed70e605702"
M3_SHA = "f560064aa9271e6f00f31f16c67242e30d9001c2ca427882bc873f97e0ab5538"
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT = ROOT / "reports/s1-disagreement-orthogonalized.json"
MX4_FEATURE_REPORT = ROOT / "reports/mx4-population-weighted-feature.json"
M2_CHANNEL = "covariate:facility_wikidata_link_share"
M3_CHANNEL = "covariate:facility_incoming_address_mismatch_share"
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261028
MIN_POOLED_DELTA_R2 = 0.002
MIN_POSITIVE_REGIONS = 3


def county_fips(geoid: str) -> str:
    value = str(geoid)
    if len(value) != 11 or not value.isdigit():
        raise ValueError(f"GEOID must be exactly eleven numeric characters: {value!r}")
    return value[:5]


def mask_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little").tobytes()).hexdigest()


def fit_county(train_y: np.ndarray, train_x: np.ndarray, test_x: np.ndarray
               ) -> tuple[np.ndarray, dict[str, Any]]:
    """OLS residual correction with intercept and two train-standardized features."""
    y, x, held = (np.asarray(a, dtype=np.float64) for a in (train_y, train_x, test_x))
    if x.ndim != 2 or x.shape[1] != 2 or held.ndim != 2 or held.shape[1] != 2 or y.shape != (x.shape[0],):
        raise ValueError("county regression requires aligned two-covariate train/test matrices")
    if y.size < 4 or held.shape[0] < 1 or not (np.isfinite(y).all() and np.isfinite(x).all() and np.isfinite(held).all()):
        raise ValueError("county regression requires finite data and adequate train/test rows")
    means = x.mean(axis=0)
    sds = x.std(axis=0, ddof=0)
    scales = np.where(sds > 0., sds, 1.)
    xz, heldz = (x-means)/scales, (held-means)/scales
    design = np.column_stack((np.ones(y.size), xz))
    held_design = np.column_stack((np.ones(held.shape[0]), heldz))
    coef, _, rank, singular = np.linalg.lstsq(design, y, rcond=None)
    if rank < 1:
        raise ValueError("county training design has no estimable intercept")
    pred = held_design @ coef
    return pred, {"training_n": int(y.size), "training_feature_mean": means.tolist(),
                  "training_feature_sd": sds.tolist(), "applied_scale": scales.tolist(),
                  "coefficients_intercept_m2_m3": coef.tolist(), "design_rank": int(rank),
                  "design_columns": int(design.shape[1]),
                  "minimum_singular_value": float(singular.min()) if singular.size else None,
                  "maximum_singular_value": float(singular.max()) if singular.size else None}


def score_vs_zero(y: np.ndarray, prediction: np.ndarray) -> dict[str, float | int]:
    target, pred = np.asarray(y, dtype=np.float64), np.asarray(prediction, dtype=np.float64)
    if target.ndim != 1 or pred.shape != target.shape or target.size < 1 or not (np.isfinite(target).all() and np.isfinite(pred).all()):
        raise ValueError("score arrays must be aligned finite nonempty vectors")
    baseline_sse = float(np.dot(target, target))  # no correction: original S1 residual stays unchanged
    model_sse = float(np.sum((target-pred)**2))
    delta = (baseline_sse-model_sse)/baseline_sse if baseline_sse > 0. else None
    return {"n": int(target.size), "uncorrected_baseline_sse": baseline_sse,
            "two_covariate_model_sse": model_sse, "delta_r2_vs_uncorrected_zero_correction": delta}


def county_block_bootstrap(county_records: list[dict[str, Any]], *,
                           replicates: int = BOOTSTRAP_REPLICATES,
                           seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """Paired bootstrap of held-county OOF residuals, resampling counties within each region."""
    if replicates < 100:
        raise ValueError("at least 100 bootstrap replicates are required")
    by_region: dict[str, list[dict[str, Any]]] = {r: [] for r in REGIONS}
    for row in county_records:
        by_region[row["region"]].append(row)
    if any(not rows for rows in by_region.values()):
        raise ValueError("county bootstrap requires at least one supported county per region")
    rng = np.random.default_rng(seed)
    pooled_values = np.empty(replicates)
    macro_values = np.empty(replicates)
    regional_values = {r: np.empty(replicates) for r in REGIONS}
    for b in range(replicates):
        total_base = total_model = 0.
        region_scores = []
        for region in REGIONS:
            rows = by_region[region]
            draw = rng.integers(0, len(rows), size=len(rows))
            base = sum(float(rows[i]["baseline_sse"]) for i in draw)
            model = sum(float(rows[i]["model_sse"]) for i in draw)
            total_base += base; total_model += model
            region_delta = (base-model)/base if base > 0. else np.nan
            regional_values[region][b] = region_delta
            region_scores.append(region_delta)
        pooled_values[b] = (total_base-total_model)/total_base if total_base > 0. else np.nan
        macro_values[b] = float(np.mean(region_scores)) if np.isfinite(region_scores).all() else np.nan
    def ci(values: np.ndarray) -> list[float]:
        finite = values[np.isfinite(values)]
        if finite.size < int(.99*replicates):
            raise ValueError("too many degenerate county-bootstrap replicates")
        return [float(np.quantile(finite,.025)), float(np.quantile(finite,.975))]
    return {"replicates": int(replicates), "seed": int(seed),
            "resampling": "paired held-county OOF predictions; sample supported counties with replacement within each official region, preserving county counts per region",
            "pooled_delta_r2_ci_95": ci(pooled_values),
            "macro_mean_region_delta_r2_ci_95": ci(macro_values),
            "regional_delta_r2_ci_95": {r: ci(v) for r,v in regional_values.items()},
            "degenerate_replicates": int(np.sum(~np.isfinite(pooled_values)))}


def run(workspace: Path, report_path: Path, manifest_path: Path) -> dict[str, Any]:
    mx4_report = json.loads(MX4_FEATURE_REPORT.read_text(encoding="utf-8"))
    correlations = {row["feature"]: row["pearson_r"] for row in mx4_report["pearson_vs_existing_F_covariates"]}
    expected_correlations = {"M2|F|covariate:facility_wikidata_link_share": -0.00153621867611425,
                             "M3|F|covariate:facility_incoming_address_mismatch_share": -0.010295012637247832}
    for name, expected in expected_correlations.items():
        if name not in correlations or not np.isclose(correlations[name], expected, rtol=0., atol=1e-15):
            raise ValueError(f"declared M2/M3 redundancy MEASURE-BEFORE changed: {name}")
    official = [(str(g), str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N or set(r for _,r in official) != set(REGIONS):
        raise ValueError("official ID-region partition is not exactly 9,379 rows and four regions")
    geoids = np.asarray([g for g,_ in official], dtype=object)
    regions = np.asarray([r for _,r in official], dtype=object)
    county = np.asarray([county_fips(g) for g in geoids], dtype=object)
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    m2_path = workspace / "ideas/M2.parquet"
    m3_path = workspace / "ideas/M3.parquet"
    m2, m2_mask, m2_sha = read_emission(m2_path, M2_SHA, "M2", "F", M2_CHANNEL, official)
    m3, m3_mask, m3_sha = read_emission(m3_path, M3_SHA, "M3", "F", M3_CHANNEL, official)
    if m2_sha != M2_SHA or m3_sha != M3_SHA:
        raise ValueError("M2/M3 provided covariate artifacts differ from frozen harness hashes")
    residual_path = workspace / "s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    residual_report = json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    if residual_report["provenance"]["output_artifact"]["sha256"] != RESIDUAL_SHA:
        raise ValueError("frozen S1 residual report does not identify the pinned artifact")
    if sha256_file(residual_path) != RESIDUAL_SHA:
        raise ValueError("frozen S1 residual artifact hash mismatch")
    import pyarrow.parquet as pq
    residual_table = pq.read_table(residual_path, columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if residual_table.num_rows != EXPECTED_N:
        raise ValueError("frozen S1 residual artifact has wrong row count")
    pos = {pair:i for i,pair in enumerate(official)}
    y = np.full(EXPECTED_N,np.nan); residual_mask=np.zeros(EXPECTED_N,bool); seen=set()
    for row in residual_table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in pos or pair in seen: raise ValueError(f"duplicate or unexpected frozen residual row: {pair}")
        seen.add(pair); i=pos[pair]
        defined=row["crossfit_joint_defined"]
        if not isinstance(defined,bool): raise ValueError("frozen residual mask must be boolean")
        residual_mask[i]=defined
        if row["crossfit_residual"] is not None: y[i]=float(row["crossfit_residual"])
    if seen != set(official) or not np.array_equal(np.isfinite(y),residual_mask):
        raise ValueError("frozen residual does not exactly align with official IDs/mask")
    support=residual_mask&m2_mask&m3_mask&np.isfinite(y)&np.isfinite(m2)&np.isfinite(m3)
    if not support.any(): raise ValueError("no paired complete-case county-transfer support")
    pred=np.full(EXPECTED_N,np.nan); baseline=np.full(EXPECTED_N,np.nan)
    county_records=[]; manifest_rows=[]
    for region in REGIONS:
        region_use=support&(regions==region)
        region_counties=sorted(set(county[region_use].tolist()))
        if len(region_counties)<2: raise ValueError(f"{region}: fewer than two counties with paired support")
        for held in region_counties:
            test=region_use&(county==held); train=region_use&(county!=held)
            if test.sum()<1 or train.sum()<4: raise ValueError(f"{region}/{held}: inadequate LOCO support")
            model_pred,fit=fit_county(y[train],np.column_stack((m2[train],m3[train])),np.column_stack((m2[test],m3[test])))
            pred[test]=model_pred; baseline[test]=0.
            held_counties=[str(c) for c in region_counties if c!=held]
            training_counties_sha=hashlib.sha256("\n".join(held_counties).encode()).hexdigest()
            ytest=y[test]; ptest=model_pred
            base_sse=float(np.dot(ytest,ytest)); model_sse=float(np.sum((ytest-ptest)**2))
            delta=(base_sse-model_sse)/base_sse if base_sse>0. else None
            county_records.append({"region":region,"county_fips":str(held),"heldout_n":int(test.sum()),
                "training_n":int(train.sum()),"training_county_count":len(held_counties),
                "training_counties_sha256":training_counties_sha,"baseline_sse":base_sse,
                "model_sse":model_sse,"out_of_county_delta_r2":delta,
                "training_fit":fit})
            for geoid in geoids[test]:
                manifest_rows.append((str(geoid),region,str(held),training_counties_sha,len(held_counties),int(train.sum())))
    if np.any(support&(~np.isfinite(pred)|~np.isfinite(baseline))) or np.any(~support&(np.isfinite(pred)|np.isfinite(baseline))):
        raise AssertionError("county OOF predictions must preserve paired support exactly")
    pooled=score_vs_zero(y[support],pred[support])
    region_scores={}
    for region in REGIONS:
        use=support&(regions==region)
        region_scores[region]={**score_vs_zero(y[use],pred[use]),"paired_support":int(use.sum()),
            "county_count":len(set(county[use].tolist()))}
    macro_delta=float(np.mean([region_scores[r]["delta_r2_vs_uncorrected_zero_correction"] for r in REGIONS]))
    positive_regions=sum(region_scores[r]["delta_r2_vs_uncorrected_zero_correction"]>0. for r in REGIONS)
    bootstrap=county_block_bootstrap(county_records)
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    with manifest_path.open("w",encoding="utf-8",newline="") as stream:
        writer=csv.writer(stream,lineterminator="\n")
        writer.writerow(["GEOID","region","heldout_county_fips","training_counties_sha256","training_county_count","training_support_n"])
        writer.writerows(sorted(manifest_rows,key=lambda row:(row[1],row[0])))
    manifest_sha=sha256_file(manifest_path)
    gate=bool(pooled["delta_r2_vs_uncorrected_zero_correction"] is not None
        and pooled["delta_r2_vs_uncorrected_zero_correction"]>=MIN_POOLED_DELTA_R2
        and positive_regions>=MIN_POSITIVE_REGIONS)
    report={
      "schema_version":1,"screen":"Leave-one-county-out two-covariate residual correction using provided M2/M3 facility metadata; unregistered",
      "measure_before":{"M2_F_vs_Mx4_pearson_r":expected_correlations["M2|F|covariate:facility_wikidata_link_share"],
        "M3_F_vs_Mx4_pearson_r":expected_correlations["M3|F|covariate:facility_incoming_address_mismatch_share"],
        "transfer_metric_measured":False,"Mx4_feature_report_sha256":sha256_file(MX4_FEATURE_REPORT)},
      "method":{"outcome":"Frozen cross-fitted S1 orthogonalized-disagreement residual; source artifact is verified and never refit.",
        "corrections":"M2 F facility_wikidata_link_share and M3 F facility_incoming_address_mismatch_share, selected because they are the M2/M3 features in the frozen Mx4 redundancy audit.",
        "validation":"Within each official region, hold one FIPS county out; fit intercept and both feature coefficients using supported tracts from all other counties in that region; predict the held county. Every supported tract is predicted exactly once.",
        "scaling":"Each covariate is centered/scaled on the training counties only; zero training SD uses scale 1. OLS coefficients are estimated with np.linalg.lstsq; no tuning or labels.",
        "baseline":"Uncorrected S1 residual: correction prediction is fixed at zero; baseline SSE=sum(residual^2) on exactly the same held-out tracts.",
        "metric":"Per-county and per-region delta-R2=1-model SSE / baseline SSE (equivalently (baseline SSE-model SSE)/baseline SSE). Pooled delta uses all paired OOF rows; macro mean gives equal weight to four region deltas.",
        "uncertainty":"Paired county bootstrap with replacement, resampling supported held-out county records within each region; report pooled, macro-mean and per-region percentile 95% intervals.",
        "county_assignment":"First five numeric characters of the official 11-digit tract GEOID (county FIPS).",
        "support":"One paired mask requiring frozen residual and both provided M2/M3 covariates defined; no imputation or outcome-dependent exclusions.",
        "unregistered":True,"interpretation_limit":"Label-free provided-feature residual association; not hidden-target accuracy, causal evidence, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "paired_support_ids":int(support.sum()),"paired_undefined_ids":int((~support).sum()),
        "residual_defined_ids":int(residual_mask.sum()),"M2_defined_ids":int(m2_mask.sum()),"M3_defined_ids":int(m3_mask.sum()),
        "residual_mask_sha256":mask_sha256(residual_mask),"M2_mask_sha256":mask_sha256(m2_mask),"M3_mask_sha256":mask_sha256(m3_mask),
        "paired_mask_sha256":mask_sha256(support),"county_cluster_count":len(set(county[support].tolist())),
        "region_support":{r:int(np.sum(support&(regions==r))) for r in REGIONS},
        "labels_read":False,"sample_score_values_read":False,"removed_or_equivalent_references_read":False,
        "external_data_used":[],"leaderboard_feedback_used":False,"uploads":0},
      "measure_after":{"pooled":pooled,"macro_mean_region_delta_r2":macro_delta,"positive_regions":positive_regions,
        "regions":region_scores,"counties":county_records,"paired_county_bootstrap":bootstrap},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_POOLED_DELTA_R2,"minimum_positive_regions":MIN_POSITIVE_REGIONS},
      "acceptance":{"pooled_delta_r2_at_least_0_002":bool(pooled["delta_r2_vs_uncorrected_zero_correction"] is not None and pooled["delta_r2_vs_uncorrected_zero_correction"]>=MIN_POOLED_DELTA_R2),
        "positive_macro_region_gains_in_at_least_3_of_4":bool(positive_regions>=MIN_POSITIVE_REGIONS),"county_transfer_screen_pass":gate},
      "provenance":{"official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "M2_artifact":{"path":str(m2_path),"sha256":m2_sha,"idea":"M2","component":"F","channel":M2_CHANNEL},
        "M3_artifact":{"path":str(m3_path),"sha256":m3_sha,"idea":"M3","component":"F","channel":M3_CHANNEL},
        "frozen_S1_residual_artifact":{"path":str(residual_path),"sha256":RESIDUAL_SHA,"rows":EXPECTED_N,
          "report_path":str(RESIDUAL_REPORT),"report_sha256":sha256_file(RESIDUAL_REPORT)},
        "Mx4_feature_audit":{"path":str(MX4_FEATURE_REPORT),"sha256":sha256_file(MX4_FEATURE_REPORT)},
        "fold_manifest":{"path":str(manifest_path),"sha256":manifest_sha,"rows":len(manifest_rows),
          "columns":["GEOID","region","heldout_county_fips","training_counties_sha256","training_county_count","training_support_n"]},
        "script_sha256":sha256_file(Path(__file__).resolve())},
      "interpretation":"The two-covariate correction passed county-transfer thresholds; retain as label-free residual association only." if gate else
        "The two-covariate correction failed one or more county-transfer thresholds; retain the unweighted frozen S1 residual and do not integrate the correction."}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/m2-m3-county-transfer.json")
    parser.add_argument("--manifest",default="reports/m2-m3-county-transfer-fold-manifest.csv")
    args=parser.parse_args(); workspace=Path(args.workspace)
    if not workspace.is_dir(): parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out,ROOT/args.manifest)
    print(json.dumps({"scope":report["scope"],"measure_after":{"pooled":report["measure_after"]["pooled"],
      "macro_mean_region_delta_r2":report["measure_after"]["macro_mean_region_delta_r2"],
      "positive_regions":report["measure_after"]["positive_regions"],"regions":report["measure_after"]["regions"],
      "bootstrap":report["measure_after"]["paired_county_bootstrap"]},"acceptance":report["acceptance"],
      "fold_manifest":report["provenance"]["fold_manifest"]},indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
