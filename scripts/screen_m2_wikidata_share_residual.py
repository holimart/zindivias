#!/usr/bin/env python3
"""County-held-out one-df monotone spline screen for the provided M2 Wikidata share."""
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
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from audit_mx4p_conditional_innovation import read_emission, sha256_file  # noqa: E402
from bias60.inputs import official_scored_pairs  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
M2_SHA = "88aa794ff46b30824781d933cac2f6f4fb1898654c8c7726990e1ed70e605702"
RESIDUAL_SHA = "437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
RESIDUAL_REPORT = ROOT / "reports/s1-disagreement-orthogonalized.json"
MX4_FEATURE_REPORT = ROOT / "reports/mx4-population-weighted-feature.json"
M2_CHANNEL = "covariate:facility_wikidata_link_share"
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261030
MIN_DELTA_R2 = 0.002
MIN_CI_LOWER = 0.0


def sha256_file_local(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def county_fips(geoid: str) -> str:
    value = str(geoid)
    if len(value) != 11 or not value.isdigit():
        raise ValueError(f"GEOID must be exactly eleven numeric characters: {value!r}")
    return value[:5]


def mask_hash(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(np.asarray(mask, dtype=np.uint8), bitorder="little").tobytes()).hexdigest()


def feature_hash(geoids: list[str], regions: list[str], values: np.ndarray, defined: np.ndarray) -> str:
    if len(geoids) != len(regions) or np.asarray(values).shape != np.asarray(defined).shape or len(geoids) != len(values):
        raise ValueError("feature hash inputs must align")
    rows = []
    for geoid, region, value, is_defined in zip(geoids, regions, values, defined):
        val = format(float(value), ".17g") if is_defined else ""
        rows.append(f"{geoid},{region},{int(bool(is_defined))},{val}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def fit_monotone_spline(train_x: np.ndarray, train_y: np.ndarray,
                        test_x: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Fit one nonnegative hinge coefficient; choose monotone direction using training SSE only.

    The one-df spline is intercept + beta * max(direction * (z - median_train_z), 0),
    beta >= 0. A linear hinge with one train-only median knot is a one-degree-of-freedom
    monotone spline. The direction is chosen between increasing/decreasing on training rows;
    exact ties select increasing. Scaling and knot are calculated from training rows only.
    """
    x = np.asarray(train_x, dtype=np.float64)
    y = np.asarray(train_y, dtype=np.float64)
    test = np.asarray(test_x, dtype=np.float64)
    if x.ndim != 1 or y.shape != x.shape or test.ndim != 1 or len(x) < 3 or not len(test):
        raise ValueError("monotone spline inputs must be aligned nonempty vectors with >=3 train rows")
    if not (np.isfinite(x).all() and np.isfinite(y).all() and np.isfinite(test).all()):
        raise ValueError("monotone spline inputs must be finite")
    mean = float(x.mean())
    sd = float(x.std(ddof=0))
    scale = sd if sd > 0. else 1.
    z = (x - mean) / scale
    z_test = (test - mean) / scale
    knot = float(np.median(z))
    candidates = []
    for direction in (1, -1):
        basis = np.maximum(direction * (z - knot), 0.)
        basis_test = np.maximum(direction * (z_test - knot), 0.)
        basis_mean = float(basis.mean())
        centered = basis - basis_mean
        denominator = float(np.dot(centered, centered))
        coefficient = max(0., float(np.dot(centered, y-y.mean()) / denominator)) if denominator > 0. else 0.
        intercept = float(y.mean() - coefficient * basis_mean)
        fitted = intercept + coefficient * basis
        sse = float(np.sum((y-fitted) ** 2))
        candidates.append((sse, direction, coefficient, intercept, basis_mean, denominator, basis_test))
    chosen = min(candidates, key=lambda row: (row[0], 0 if row[1] == 1 else 1))
    sse, direction, coefficient, intercept, basis_mean, denominator, basis_test = chosen
    prediction = intercept + coefficient * basis_test
    details = {"training_n": int(len(x)), "training_x_mean": mean,
        "training_x_sd": sd, "training_x_applied_scale": scale,
        "knot_training_standardized_median": knot,
        "spline_basis": "max(direction * (z - train_median_z), 0)",
        "direction": "nondecreasing" if direction == 1 else "nonincreasing",
        "direction_selection": "training SSE only; exact tie chooses nondecreasing",
        "spline_coefficient_nonnegative": coefficient, "intercept": intercept,
        "training_basis_mean": basis_mean, "training_basis_centered_ss": denominator,
        "training_sse": sse, "spline_continuous_degrees_of_freedom": 1,
        "coefficient_order": ["intercept", "nonnegative_monotone_hinge_coefficient"]}
    return prediction, details


def metric(y: np.ndarray, pred: np.ndarray) -> dict[str, float | int | None]:
    target, estimate = np.asarray(y, dtype=np.float64), np.asarray(pred, dtype=np.float64)
    if target.ndim != 1 or estimate.shape != target.shape or not target.size or not (np.isfinite(target).all() and np.isfinite(estimate).all()):
        raise ValueError("metric requires aligned finite held-out residuals and predictions")
    base = float(np.dot(target, target))
    model = float(np.sum((target-estimate)**2))
    return {"n": int(target.size), "frozen_s1_residual_baseline_sse": base,
        "monotone_spline_sse": model, "delta_r2": (base-model)/base if base > 0. else None}


def county_bootstrap(records: list[dict[str, Any]], *, replicates: int = BOOTSTRAP_REPLICATES,
                     seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    if replicates < 100:
        raise ValueError("at least 100 county bootstrap replicates are required")
    groups = {r: [] for r in REGIONS}
    for row in records:
        groups[row["region"]].append(row)
    if any(not rows for rows in groups.values()):
        raise ValueError("county bootstrap requires supported counties in every region")
    rng = np.random.default_rng(seed)
    pooled = np.empty(replicates)
    regional = {r: np.empty(replicates) for r in REGIONS}
    for b in range(replicates):
        base_all = model_all = 0.
        for region in REGIONS:
            rows = groups[region]
            sample = rng.integers(0, len(rows), size=len(rows))
            base = sum(float(rows[i]["baseline_sse"]) for i in sample)
            model = sum(float(rows[i]["model_sse"]) for i in sample)
            regional[region][b] = (base-model)/base if base > 0. else np.nan
            base_all += base
            model_all += model
        pooled[b] = (base_all-model_all)/base_all if base_all > 0. else np.nan
    def ci(values: np.ndarray) -> list[float]:
        x = values[np.isfinite(values)]
        if len(x) < int(.99*replicates):
            raise ValueError("degenerate county-bootstrap replicates")
        return [float(np.quantile(x, .025)), float(np.quantile(x, .975))]
    return {"replicates": int(replicates), "seed": int(seed),
        "resampling": "paired held-county SSEs; counties sampled with replacement separately within each official region",
        "pooled_delta_r2_ci_95": ci(pooled), "regional_delta_r2_ci_95": {r: ci(x) for r,x in regional.items()},
        "degenerate_replicates": int(np.sum(~np.isfinite(pooled)))}


def run(workspace: Path, report_path: Path, manifest_path: Path) -> dict[str, Any]:
    mx4_report = json.loads(MX4_FEATURE_REPORT.read_text(encoding="utf-8"))
    m2_corr = {row["feature"]: row["pearson_r"] for row in mx4_report["pearson_vs_existing_F_covariates"]}.get(
        "M2|F|covariate:facility_wikidata_link_share")
    if m2_corr is None or not np.isclose(m2_corr, -0.00153621867611425, rtol=0., atol=1e-15):
        raise ValueError("frozen MEASURE-BEFORE M2/Mx4 correlation differs from -0.001536")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N or set(r for _,r in official) != set(REGIONS):
        raise ValueError("official scored scope must be 9,379 IDs across the four official regions")
    geoids = [g for g,_ in official]
    regions = np.asarray([r for _,r in official], dtype=object)
    counties = np.asarray([county_fips(g) for g in geoids], dtype=object)
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    m2_path = workspace / "ideas/M2.parquet"
    m2, m2_defined, m2_file_sha = read_emission(m2_path, M2_SHA, "M2", "F", M2_CHANNEL, official)
    residual_path = workspace / "s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    residual_report = json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    if residual_report["provenance"]["output_artifact"]["sha256"] != RESIDUAL_SHA or sha256_file(residual_path) != RESIDUAL_SHA:
        raise ValueError("frozen S1 residual artifact/report failed pinned SHA check")
    table = pq.read_table(residual_path, columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if table.num_rows != EXPECTED_N:
        raise ValueError("frozen residual artifact has wrong row count")
    positions = {pair:i for i,pair in enumerate(official)}
    y = np.full(EXPECTED_N, np.nan)
    residual_defined = np.zeros(EXPECTED_N,dtype=bool)
    seen=set()
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in positions or pair in seen: raise ValueError(f"duplicate/unexpected frozen residual ID {pair}")
        seen.add(pair); i=positions[pair]
        if not isinstance(row["crossfit_joint_defined"],bool): raise ValueError("residual definedness must be boolean")
        residual_defined[i]=row["crossfit_joint_defined"]
        if row["crossfit_residual"] is not None: y[i]=float(row["crossfit_residual"])
    if seen!=set(official) or not np.array_equal(np.isfinite(y),residual_defined):
        raise ValueError("frozen residual values/mask do not align exactly with official IDs")
    support=residual_defined&m2_defined&np.isfinite(y)&np.isfinite(m2)
    if not support.any(): raise ValueError("no complete S1/M2 paired support")
    prediction=np.full(EXPECTED_N,np.nan)
    county_records=[]
    manifest_rows=[]
    coverage_by_county=[]
    all_counties=sorted(set(zip(regions.tolist(),counties.tolist())))
    for region,county in all_counties:
        official_mask=(regions==region)&(counties==county)
        feature_mask=official_mask&m2_defined
        residual_mask=official_mask&residual_defined
        paired=official_mask&support
        coverage_by_county.append({"region":region,"county_fips":county,"official_tracts":int(official_mask.sum()),
          "M2_defined_tracts":int(feature_mask.sum()),"S1_residual_defined_tracts":int(residual_mask.sum()),
          "paired_support_tracts":int(paired.sum()),"paired_coverage_fraction":float(paired.sum()/official_mask.sum())})
    for region in REGIONS:
        regional=support&(regions==region)
        regional_counties=sorted(set(counties[regional].tolist()))
        if len(regional_counties)<2: raise ValueError(f"{region}: insufficient counties for leave-one-county-out")
        for held in regional_counties:
            test=regional&(counties==held)
            train=regional&(counties!=held)
            if int(train.sum())<3 or not test.any(): raise ValueError(f"{region}/{held}: insufficient paired support")
            pred,fit=fit_monotone_spline(m2[train],y[train],m2[test])
            prediction[test]=pred
            training_counties=[str(c) for c in regional_counties if c!=held]
            training_hash=hashlib.sha256("\n".join(sorted(training_counties)).encode()).hexdigest()
            base=float(np.dot(y[test],y[test])); model=float(np.sum((y[test]-pred)**2))
            delta=(base-model)/base if base>0. else None
            record={"region":region,"county_fips":str(held),"heldout_n":int(test.sum()),
              "training_n":int(train.sum()),"training_county_count":len(training_counties),
              "training_counties_sha256":training_hash,"baseline_sse":base,"model_sse":model,
              "out_of_county_delta_r2":delta,"training_fit":fit}
            county_records.append(record)
            for geoid in np.asarray(geoids,dtype=object)[test]:
                manifest_rows.append((str(geoid),region,str(held),training_hash,len(training_counties),int(train.sum())))
    if np.any(support&~np.isfinite(prediction)) or np.any(~support&np.isfinite(prediction)):
        raise AssertionError("county-held-out prediction mask differs from paired support")
    overall=metric(y[support],prediction[support])
    regional_results={}
    for region in REGIONS:
        mask=support&(regions==region)
        regional_results[region]={**metric(y[mask],prediction[mask]),
          "official_tracts":int(np.sum(regions==region)),"M2_defined_tracts":int(np.sum((regions==region)&m2_defined)),
          "paired_support_tracts":int(mask.sum()),"paired_coverage_fraction":float(mask.sum()/np.sum(regions==region)),
          "county_count":len(set(counties[mask].tolist()))}
    bootstrap=county_bootstrap(county_records)
    passed_regions=[]
    for region in REGIONS:
        delta=regional_results[region]["delta_r2"]
        ci_lower=bootstrap["regional_delta_r2_ci_95"][region][0]
        if delta is not None and delta>=MIN_DELTA_R2 and ci_lower>MIN_CI_LOWER:
            passed_regions.append(region)
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    with manifest_path.open("w",encoding="utf-8",newline="") as stream:
        writer=csv.writer(stream,lineterminator="\n")
        writer.writerow(["GEOID","region","heldout_county_fips","training_counties_sha256","training_county_count","training_support_n"])
        writer.writerows(sorted(manifest_rows,key=lambda row:(row[1],row[0])))
    manifest_sha=sha256_file_local(manifest_path)
    fhash=feature_hash(geoids,[r for _,r in official],m2,m2_defined)
    report={
      "schema_version":1,"screen":"County-held-out one-degree-of-freedom monotone M2 Wikidata-share spline on frozen S1 residual; unregistered",
      "measure_before":{"M2_vs_Mx4_pearson_r":float(m2_corr),"county_heldout_residual_delta_r2_measured":False,
        "correlation_report_sha256":sha256_file_local(MX4_FEATURE_REPORT)},
      "method":{"outcome":"Pinned frozen cross-fitted S1 orthogonalized-disagreement residual; never recomputed or refit.",
        "feature":{"idea":"M2","component":"F","channel":M2_CHANNEL,"meaning":"provided facility Wikidata-link share"},
        "spline":"One-degree-of-freedom monotone linear hinge spline: intercept + beta*max(direction*(z - train-median(z)),0), beta constrained nonnegative. z uses training-only mean and SD; median knot is training-only. No internal knots beyond this single median hinge knot.",
        "direction":"Among nondecreasing and nonincreasing hinge directions, choose lower training SSE only; exact ties select nondecreasing. No held-out residuals influence direction, scaling, knot or coefficient.",
        "validation":"Within each official region, hold one county FIPS out; fit only on paired-support tracts in all other counties of that region; predict every paired held-out tract once.",
        "baseline":"Uncorrected frozen S1 residual represented by zero correction prediction; both baseline and spline evaluated on identical held-out support.",
        "metric":"County-held-out delta-R2=(baseline SSE-spline SSE)/baseline SSE, with baseline SSE=sum(frozen residual^2). Pooled and each region reported.",
        "uncertainty":"Paired county-block percentile bootstrap with replacement within each region; 2,000 draws, fixed seed.",
        "support":"Intersection of frozen S1 residual definedness and provided M2 F feature definedness; no imputation.",
        "no_labels_or_target_reconstruction":True,"no_removed_or_equivalent_references":True,"external_data_used":[],
        "leaderboard_feedback_used":False,"uploads":0,"unregistered":True,
        "interpretation_limit":"Provided-feature residual association only; does not establish hidden-target accuracy, causality, or authorize estimator integration."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":official_sha,
        "residual_defined_ids":int(residual_defined.sum()),"M2_defined_ids":int(m2_defined.sum()),
        "paired_support_ids":int(support.sum()),"paired_undefined_ids":int((~support).sum()),
        "residual_mask_sha256":mask_hash(residual_defined),"M2_defined_mask_sha256":mask_hash(m2_defined),
        "paired_mask_sha256":mask_hash(support),"feature_values_and_mask_sha256":fhash,
        "regions":{r:{"official_tracts":int(np.sum(regions==r)),"M2_defined_tracts":int(np.sum((regions==r)&m2_defined)),
          "residual_defined_tracts":int(np.sum((regions==r)&residual_defined)),"paired_support_tracts":int(np.sum(support&(regions==r)))} for r in REGIONS},
        "coverage_by_county":coverage_by_county,"county_count_with_paired_support":len(county_records),
        "labels_read":False,"sample_score_values_read":False,"removed_or_equivalent_references_read":False,
        "external_data_used":[],"leaderboard_feedback_used":False,"uploads":0},
      "measure_after":{"pooled":overall,"regional":regional_results,"county_block_bootstrap":bootstrap,
        "regions_meeting_both_thresholds":passed_regions,"county_oof_records":county_records},
      "thresholds":{"regional_delta_r2_minimum_inclusive":MIN_DELTA_R2,"regional_ci_lower_strictly_above":MIN_CI_LOWER,
        "minimum_regions_meeting_both":3},
      "acceptance":{"regions_meeting_delta_and_ci":len(passed_regions),
        "at_least_three_of_four_regions_pass":len(passed_regions)>=3,
        "screen_pass":len(passed_regions)>=3},
      "provenance":{"official_geoid_region_sha256":official_sha,
        "M2_artifact":{"path":str(m2_path),"sha256":m2_file_sha,"idea":"M2","component":"F","channel":M2_CHANNEL,
          "feature_values_and_mask_sha256":fhash},
        "frozen_S1_residual_artifact":{"path":str(residual_path),"sha256":RESIDUAL_SHA,"rows":EXPECTED_N,
          "report_path":str(RESIDUAL_REPORT),"report_sha256":sha256_file_local(RESIDUAL_REPORT)},
        "MEASURE_BEFORE_report":{"path":str(MX4_FEATURE_REPORT),"sha256":sha256_file_local(MX4_FEATURE_REPORT)},
        "fold_manifest":{"path":str(manifest_path),"sha256":manifest_sha,"rows":len(manifest_rows),
          "columns":["GEOID","region","heldout_county_fips","training_counties_sha256","training_county_count","training_support_n"]},
        "script_sha256":sha256_file_local(Path(__file__).resolve())},
      "interpretation":"The M2 monotone spline met its county-held-out regional criteria; retain descriptive-only." if len(passed_regions)>=3 else
        "The M2 monotone spline failed the county-held-out regional criteria; retain frozen uncorrected S1 and do not integrate."}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""))
    parser.add_argument("--out",default="reports/m2-wikidata-share-residual-screen.json")
    parser.add_argument("--manifest",default="reports/m2-wikidata-share-residual-fold-manifest.csv")
    args=parser.parse_args(); workspace=Path(args.workspace)
    if not workspace.is_dir(): parser.error("COMPETITION_WORKSPACE must be an existing directory")
    result=run(workspace,ROOT/args.out,ROOT/args.manifest)
    print(json.dumps({"scope":{"official_ids":result["scope"]["official_ids"],"paired_support_ids":result["scope"]["paired_support_ids"],"regions":result["scope"]["regions"]},
      "pooled":result["measure_after"]["pooled"],"regional":result["measure_after"]["regional"],
      "bootstrap":result["measure_after"]["county_block_bootstrap"],"acceptance":result["acceptance"],
      "feature_hash":result["provenance"]["M2_artifact"]["feature_values_and_mask_sha256"],
      "manifest":result["provenance"]["fold_manifest"]},indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
