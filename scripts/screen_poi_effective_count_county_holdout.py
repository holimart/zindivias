#!/usr/bin/env python3
"""Leave-one-county-out transport screen for provided POI effective category count."""
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
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from audit_mx4p_conditional_innovation import parquet_bytes, read_emission  # noqa: E402

REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
EXPECTED_N = 9379
EFFECTIVE_REPORT = ROOT / "reports/poi-category-effective-count.json"
EFFECTIVE_ARTIFACT_SHA = "74c35567243ad8ea3c00cf5117ef641e49148b63f89f7f07616ac9dcf33b8d6e"
EXPECTED_WITHIN_REGION_RATIO = 0.18096672540982753
MX4_SHA = "8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA = "632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"
MIN_COUNTY_N = 30
MIN_ELIGIBLE_COUNTIES = 8
MIN_POSITIVE_SHARE = .75
MIN_POOLED_R2 = .05


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def county_fips_from_geoid(geoid: str) -> str:
    value = str(geoid)
    if len(value) != 11 or not value.isdigit():
        raise ValueError(f"official GEOID must be exactly 11 numeric characters: {value!r}")
    return value[:5]


def train_standardize(train: np.ndarray, test: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Standardize columns using training rows only; constant columns use unit scale."""
    tr = np.asarray(train, dtype=np.float64)
    te = np.asarray(test, dtype=np.float64)
    if tr.ndim != 2 or te.ndim != 2 or tr.shape[1] != te.shape[1] or not tr.shape[0]:
        raise ValueError("train/test scaler arrays must be nonempty matrices with aligned columns")
    if not np.isfinite(tr).all() or not np.isfinite(te).all():
        raise ValueError("train/test scaler arrays must be finite")
    mean = tr.mean(axis=0)
    sd = tr.std(axis=0, ddof=0)
    safe_sd = np.where(sd > 0., sd, 1.)
    return (tr - mean) / safe_sd, (te - mean) / safe_sd, mean, safe_sd


def county_loco(geoids: list[str], regions: np.ndarray, county_codes: np.ndarray,
                log_effective_count: np.ndarray, predictors: np.ndarray, mask: np.ndarray
                ) -> dict[str, Any]:
    """Fit one global region-intercept OLS per held county; never fit on held rows."""
    n = len(geoids)
    reg = np.asarray(regions, dtype=object)
    counties = np.asarray(county_codes, dtype=object)
    y = np.asarray(log_effective_count, dtype=np.float64)
    x = np.asarray(predictors, dtype=np.float64)
    use = np.asarray(mask, dtype=bool)
    if (len(set(geoids)) != n or reg.shape != (n,) or counties.shape != (n,)
            or y.shape != (n,) or x.ndim != 2 or x.shape[0] != n or use.shape != (n,)):
        raise ValueError("county holdout arrays must have exact aligned shapes and unique GEOIDs")
    if set(reg.tolist()) != set(REGIONS):
        raise ValueError("county holdout requires the four official regions")
    if np.any(use & (~np.isfinite(y) | ~np.isfinite(x).all(axis=1))):
        raise ValueError("complete-case rows must have finite outcome and predictors")
    if not use.any():
        raise ValueError("county holdout has no complete-case rows")

    sorted_counties = sorted(set(counties[use].tolist()))
    baseline_oof = np.full(n, np.nan, dtype=np.float64)
    model_oof = np.full(n, np.nan, dtype=np.float64)
    records: list[dict[str, Any]] = []
    region_code = {region: i for i, region in enumerate(REGIONS)}
    for county in sorted_counties:
        test = use & (counties == county)
        train = use & (counties != county)
        if not test.any() or not train.any():
            raise ValueError(f"county {county} has empty train or held-out support")
        x_train_z, x_test_z, _, _ = train_standardize(x[train], x[test])
        train_rows, test_rows = np.flatnonzero(train), np.flatnonzero(test)
        train_codes = np.asarray([region_code[r] for r in reg[train_rows]], dtype=np.int8)
        test_codes = np.asarray([region_code[r] for r in reg[test_rows]], dtype=np.int8)
        train_dummies = np.column_stack([(train_codes == code).astype(np.float64)
                                         for code in range(1, len(REGIONS))])
        test_dummies = np.column_stack([(test_codes == code).astype(np.float64)
                                        for code in range(1, len(REGIONS))])
        design_train = np.column_stack((np.ones(len(train_rows)), train_dummies, x_train_z))
        design_test = np.column_stack((np.ones(len(test_rows)), test_dummies, x_test_z))
        if train_rows.size <= design_train.shape[1]:
            raise ValueError(f"county {county} leaves insufficient training rows")
        beta, _, _, _ = np.linalg.lstsq(design_train, y[train], rcond=None)
        train_mean = float(y[train].mean())
        baseline_oof[test_rows] = train_mean
        model_oof[test_rows] = design_test @ beta
        baseline_sse = float(np.sum((y[test] - train_mean) ** 2))
        model_sse = float(np.sum((y[test] - model_oof[test_rows]) ** 2))
        county_r2 = (1. - model_sse / baseline_sse) if baseline_sse > 0. else None
        records.append({"county_fips": str(county), "heldout_n": int(test.sum()),
                        "training_n": int(train.sum()), "eligible_n_ge_30": bool(test.sum() >= MIN_COUNTY_N),
                        "training_county_mean": train_mean, "heldout_baseline_sse": baseline_sse,
                        "heldout_model_sse": model_sse, "heldout_r2_vs_training_county_mean": county_r2})
    if np.any(use & (~np.isfinite(baseline_oof) | ~np.isfinite(model_oof))) or np.any(~use &
            (np.isfinite(baseline_oof) | np.isfinite(model_oof))):
        raise AssertionError("LOCO predictions must exactly preserve the complete-case mask")
    baseline_sse = float(np.sum((y[use] - baseline_oof[use]) ** 2))
    model_sse = float(np.sum((y[use] - model_oof[use]) ** 2))
    pooled_r2 = 1. - model_sse / baseline_sse if baseline_sse > 0. else None
    eligible = [row for row in records if row["eligible_n_ge_30"] and row["heldout_r2_vs_training_county_mean"] is not None]
    positives = sum(row["heldout_r2_vs_training_county_mean"] > 0. for row in eligible)
    positive_share = positives / len(eligible) if eligible else None
    return {"baseline_oof": baseline_oof, "model_oof": model_oof,
            "county_records": records, "pooled_r2": pooled_r2,
            "eligible_counties": int(len(eligible)), "positive_eligible_counties": int(positives),
            "positive_eligible_share": positive_share,
            "pooled_baseline_sse": baseline_sse, "pooled_model_sse": model_sse}


def _read_artifact(path: Path, expected_sha: str, official: list[tuple[str, str]]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha:
        raise ValueError("frozen effective-count artifact hash mismatch")
    columns = ["GEOID", "region", "unique_assigned_place_count", "primary_categorized_place_count",
               "effective_primary_category_count", "effective_count_defined", "Mx4_F", "Mx4_F_defined",
               "S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined", "joint_defined"]
    table = pq.read_table(path, columns=columns)
    if table.num_rows != len(official):
        raise ValueError("effective-count artifact row count differs from official IDs")
    locations = {pair: i for i, pair in enumerate(official)}
    seen: set[tuple[str, str]] = set()
    numeric_names = ["unique_assigned_place_count", "primary_categorized_place_count",
                     "effective_primary_category_count", "Mx4_F", "S1_lagged_Mx4_F"]
    values = {name: np.full(len(official), np.nan, dtype=np.float64) for name in numeric_names}
    values.update({name: np.zeros(len(official), dtype=bool) for name in
                   ("effective_count_defined", "Mx4_F_defined", "S1_lagged_Mx4_F_defined", "joint_defined")})
    for row in table.to_pylist():
        pair = (str(row["GEOID"]), str(row["region"]))
        if pair not in locations or pair in seen:
            raise ValueError(f"unexpected or duplicate effective-count pair: {pair}")
        seen.add(pair); i = locations[pair]
        for name in numeric_names:
            if row[name] is not None:
                values[name][i] = float(row[name])
        for name in ("effective_count_defined", "Mx4_F_defined", "S1_lagged_Mx4_F_defined", "joint_defined"):
            if not isinstance(row[name], bool):
                raise ValueError(f"effective-count artifact mask {name} must be boolean")
            values[name][i] = row[name]
    if seen != set(official):
        raise ValueError("effective-count artifact is not an exact official ID-region partition")
    for field, mask_name in (("effective_primary_category_count", "effective_count_defined"),
                             ("Mx4_F", "Mx4_F_defined"), ("S1_lagged_Mx4_F", "S1_lagged_Mx4_F_defined")):
        if not np.array_equal(np.isfinite(values[field]), values[mask_name]):
            raise ValueError(f"effective-count artifact mask mismatch for {field}")
    expected_joint = values["effective_count_defined"] & values["Mx4_F_defined"] & values["S1_lagged_Mx4_F_defined"]
    if not np.array_equal(values["joint_defined"], expected_joint):
        raise ValueError("frozen effective-count artifact joint mask is inconsistent")
    return values, {"path": str(path), "sha256": actual_sha, "rows": table.num_rows,
                    "columns_read": columns, "exact_official_pair_alignment": True}


def _read_pct_urban(workspace: Path, official: list[tuple[str, str]]) -> tuple[np.ndarray, dict[str, Any]]:
    result = np.full(len(official), np.nan, dtype=np.float64)
    locations = {pair: i for i, pair in enumerate(official)}
    metadata: dict[str, Any] = {}
    for region in REGIONS:
        path = workspace / "defined-flags-inputs" / f"{region}-strata-tract-table.parquet"
        table = pq.read_table(path, columns=["GEOID", "pct_urban"])
        expected = {g for g, r in official if r == region}
        found: set[str] = set()
        for geoid_value, value in zip(table.column("GEOID").to_pylist(), table.column("pct_urban").to_pylist()):
            geoid = str(geoid_value)
            pair = (geoid, region)
            if pair not in locations:
                continue
            if geoid in found:
                raise ValueError(f"duplicate pct_urban GEOID in {region}: {geoid}")
            found.add(geoid)
            if value is not None:
                result[locations[pair]] = float(value)
        if found != expected:
            raise ValueError(f"provided pct_urban IDs do not exactly match official IDs in {region}")
        idx = [locations[(geoid, region)] for geoid in expected]
        metadata[region] = {"path": str(path), "sha256": sha256_file(path),
                            "columns_read": ["GEOID", "pct_urban"], "official_ids": len(found),
                            "pct_urban_defined_ids": int(np.isfinite(result[idx]).sum())}
    if np.any(np.isfinite(result) & ((result < 0.) | (result > 1.))):
        raise ValueError("provided pct_urban must lie in [0,1]")
    return result, metadata


def run(workspace: Path, report_path: Path, artifact_path: Path) -> dict[str, Any]:
    source_report = json.loads(EFFECTIVE_REPORT.read_text(encoding="utf-8"))
    if (source_report["scope"]["official_ids"] != EXPECTED_N
            or source_report["scope"]["effective_count_defined_ids"] != 9327
            or source_report["scope"]["joint_crossfit_ids"] != 9327
            or not np.isclose(source_report["measure_after"]["crossfit_residual_variance_ratio"],
                              EXPECTED_WITHIN_REGION_RATIO, rtol=0., atol=1e-15)):
        raise ValueError("within-region MEASURE-BEFORE does not match the declared source report")
    official = [(str(g), str(r)) for g, r in official_scored_pairs(workspace)]
    if (len(official) != EXPECTED_N or len(set(official)) != EXPECTED_N
            or len({g for g, _ in official}) != EXPECTED_N or set(r for _, r in official) != set(REGIONS)):
        raise ValueError("official IDs are not an exact 9,379-row/four-region partition")
    official_sha = hashlib.sha256("\n".join(f"{g},{r}" for g, r in official).encode()).hexdigest()
    effective_path = Path(source_report["provenance"]["output_artifact"]["path"])
    effective, artifact_meta = _read_artifact(effective_path, EFFECTIVE_ARTIFACT_SHA, official)
    count = effective["unique_assigned_place_count"]
    category_count = effective["primary_categorized_place_count"]
    target_count = effective["effective_primary_category_count"]
    effective_mask = effective["effective_count_defined"]
    if np.any(effective_mask & ((count < 10.) | (category_count <= 0.) | (target_count < 1.))):
        raise ValueError("defined effective-count outcome violates source eligibility")

    mx4_path, s1_path = workspace / "ideas/Mx4.parquet", workspace / "ideas/S1.parquet"
    mx4, mx4_mask, mx4_sha = read_emission(mx4_path, MX4_SHA, "Mx4", "F", "covariate", official)
    s1, s1_mask, s1_sha = read_emission(s1_path, S1_SHA, "S1", "F", "covariate:lag_mx4_f_covariate", official)
    if (not np.array_equal(mx4, effective["Mx4_F"], equal_nan=True)
            or not np.array_equal(s1, effective["S1_lagged_Mx4_F"], equal_nan=True)
            or not np.array_equal(mx4_mask, effective["Mx4_F_defined"])
            or not np.array_equal(s1_mask, effective["S1_lagged_Mx4_F_defined"])):
        raise ValueError("frozen Mx4/S1 emissions differ from the source effective-count artifact")
    pct_urban, urban_meta = _read_pct_urban(workspace, official)
    log_target = np.full(EXPECTED_N, np.nan)
    log_target[effective_mask] = np.log1p(target_count[effective_mask])
    log_count = np.full(EXPECTED_N, np.nan)
    valid_count = np.isfinite(count) & (count >= 0.)
    log_count[valid_count] = np.log1p(count[valid_count])
    regions = np.asarray([r for _, r in official], dtype=object)
    county_codes = np.asarray([county_fips_from_geoid(g) for g, _ in official], dtype=object)
    predictors = np.column_stack((log_count, pct_urban, mx4, s1))
    support = (effective_mask & np.isfinite(log_target) & np.isfinite(predictors).all(axis=1)
               & mx4_mask & s1_mask)
    if not support.any():
        raise ValueError("no complete cases for county-held-out effective-count model")
    fitted = county_loco([g for g, _ in official], regions, county_codes, log_target, predictors, support)
    eligible_counties = fitted["eligible_counties"]
    positive_share = fitted["positive_eligible_share"]
    positive_share_pass = positive_share is not None and positive_share >= MIN_POSITIVE_SHARE
    min_counties_pass = eligible_counties >= MIN_ELIGIBLE_COUNTIES
    pooled_pass = fitted["pooled_r2"] is not None and fitted["pooled_r2"] >= MIN_POOLED_R2
    gate = bool(pooled_pass and min_counties_pass and positive_share_pass)

    county_by_geoid = county_codes
    output = pa.table({
        "GEOID": pa.array([g for g, _ in official], type=pa.string()),
        "region": pa.array(regions.tolist(), type=pa.string()),
        "county_fips_from_geoid": pa.array(county_by_geoid.tolist(), type=pa.string()),
        "unique_assigned_place_count": pa.array([int(v) if np.isfinite(v) else None for v in count], type=pa.int64()),
        "primary_categorized_place_count": pa.array([int(v) if np.isfinite(v) else None for v in category_count], type=pa.int64()),
        "effective_primary_category_count": pa.array([float(v) if ok else None for v, ok in zip(target_count, effective_mask)], type=pa.float64()),
        "log1p_effective_primary_category_count": pa.array([float(v) if ok else None for v, ok in zip(log_target, effective_mask)], type=pa.float64()),
        "log1p_unique_place_count": pa.array([float(v) if np.isfinite(v) else None for v in log_count], type=pa.float64()),
        "pct_urban": pa.array([float(v) if np.isfinite(v) else None for v in pct_urban], type=pa.float64()),
        "Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(mx4, mx4_mask)], type=pa.float64()),
        "Mx4_F_defined": pa.array(mx4_mask, type=pa.bool_()),
        "S1_lagged_Mx4_F": pa.array([float(v) if ok else None for v, ok in zip(s1, s1_mask)], type=pa.float64()),
        "S1_lagged_Mx4_F_defined": pa.array(s1_mask, type=pa.bool_()),
        "pct_urban_defined": pa.array(np.isfinite(pct_urban), type=pa.bool_()),
        "effective_count_defined": pa.array(effective_mask, type=pa.bool_()),
        "county_loco_joint_defined": pa.array(support, type=pa.bool_()),
        "training_county_mean_prediction": pa.array([float(v) if ok else None for v, ok in zip(fitted["baseline_oof"], support)], type=pa.float64()),
        "county_loco_model_prediction": pa.array([float(v) if ok else None for v, ok in zip(fitted["model_oof"], support)], type=pa.float64()),
    })
    for col, mask in (("effective_primary_category_count", effective_mask),
                      ("log1p_effective_primary_category_count", effective_mask),
                      ("log1p_unique_place_count", np.isfinite(log_count)),
                      ("pct_urban", np.isfinite(pct_urban)), ("Mx4_F", mx4_mask), ("S1_lagged_Mx4_F", s1_mask),
                      ("training_county_mean_prediction", support), ("county_loco_model_prediction", support)):
        arr = np.asarray(output[col].to_numpy(zero_copy_only=False), dtype=np.float64)
        if not np.array_equal(np.isfinite(arr), mask):
            raise AssertionError(f"output artifact null mask mismatch for {col}")
    payload = parquet_bytes(output)
    if payload != parquet_bytes(output):
        raise ValueError("county holdout output serialization is nondeterministic")
    artifact_path_out = Path(artifact_path)
    artifact_path_out.parent.mkdir(parents=True, exist_ok=True)
    artifact_path_out.write_bytes(payload)

    county_fold_counts = [{"county_fips": row["county_fips"], "heldout_n": row["heldout_n"],
                           "training_n": row["training_n"], "eligible_n_ge_30": row["eligible_n_ge_30"]}
                          for row in fitted["county_records"]]
    eligible_county_scores = [{"county_fips": row["county_fips"], "heldout_n": row["heldout_n"],
                               "heldout_r2_vs_training_county_mean": row["heldout_r2_vs_training_county_mean"]}
                              for row in fitted["county_records"] if row["eligible_n_ge_30"]]
    county_summary = {"official_counties": int(len(set(county_codes.tolist()))),
                      "counties_with_complete_cases": int(len(fitted["county_records"])),
                      "eligible_counties_n_ge_30": eligible_counties,
                      "positive_eligible_counties": fitted["positive_eligible_counties"],
                      "positive_eligible_county_share": positive_share,
                      "minimum_county_support": MIN_COUNTY_N,
                      "county_fold_counts": county_fold_counts}
    report = {
        "schema_version": 1,
        "screen": "Leave-one-county-out transport of log1p provided POI effective primary-category count; label-free unregistered diagnostic",
        "measure_before": {"within_region_crossfit_residual_variance_ratio": EXPECTED_WITHIN_REGION_RATIO,
                           "within_region_joint_ids": 9327, "official_coverage_fraction": 0.9944556989018019,
                           "county_heldout_transport_measured": False,
                           "source_effective_count_report_sha256": sha256_file(EFFECTIVE_REPORT)},
        "method": {"outcome": "log1p of the frozen effective primary-category count computed from provided Overture primary-category counts; only rows where that count is defined are eligible.",
                   "predictors": ["log1p provided unique assigned POI count", "provided pct_urban", "frozen Mx4 F", "frozen S1 lagged Mx4 F"],
                   "model": "For each county, fit pooled OLS with intercept, official-region intercept indicators, and globally standardized predictors on all other complete-case counties. Standardization means and population SDs are fit on training counties only; zero-SD predictors use scale 1.",
                   "baseline": "For each held county, predict the arithmetic mean of log1p effective count over all complete-case tracts in the other training counties; this mean is fitted without the held county.",
                   "county_fold": "County FIPS is the first five characters of each exact 11-digit official tract GEOID. Each county is held out once; no county-level target or feature statistics enter its model fit.",
                   "score": "County R2 = 1 - held-county model SSE / held-county training-mean baseline SSE. Pooled R2 uses the sums of held-county model SSE and fold-specific training-mean baseline SSE across all complete-case held-out tracts.",
                   "support": "One frozen complete-case mask: effective count, log1p unique place count, pct_urban, Mx4 F, and S1 lag all finite. No imputation, post-result exclusions, or alternative support.",
                   "unregistered": True,
                   "interpretation_limit": "Provided-feature transportability diagnostic only; not hidden-target accuracy, causal evidence, or estimator-promotion evidence."},
        "scope": {"official_ids": EXPECTED_N, "official_geoid_region_sha256": official_sha,
                  "effective_count_defined_ids": int(effective_mask.sum()), "pct_urban_defined_ids": int(np.isfinite(pct_urban).sum()),
                  "Mx4_F_defined_ids": int(mx4_mask.sum()), "S1_lagged_Mx4_F_defined_ids": int(s1_mask.sum()),
                  "county_loco_joint_ids": int(support.sum()), "county_loco_undefined_ids": int((~support).sum()),
                  "complete_case_mask_sha256": hashlib.sha256(np.packbits(support.astype(np.uint8), bitorder="little").tobytes()).hexdigest(),
                  "county_coverage": county_summary,
                  "labels_read": False, "sample_score_values_read": False,
                  "removed_or_equivalent_references_read": False, "external_data_used": [],
                  "leaderboard_feedback_used": False, "post_result_exclusions": False,
                  "estimator_or_registry_changed": False, "uploads": 0},
        "measure_after": {"pooled_heldout_r2_vs_training_county_mean": fitted["pooled_r2"],
                          "pooled_heldout_baseline_sse": fitted["pooled_baseline_sse"],
                          "pooled_heldout_model_sse": fitted["pooled_model_sse"],
                          "counties_with_positive_r2_and_n_ge_30": fitted["positive_eligible_counties"],
                          "eligible_counties_n_ge_30": eligible_counties,
                          "positive_r2_share_of_eligible_counties": positive_share,
                          "eligible_county_scores": eligible_county_scores},
        "thresholds": {"pooled_heldout_r2_minimum_inclusive": MIN_POOLED_R2,
                       "minimum_eligible_counties": MIN_ELIGIBLE_COUNTIES,
                       "county_minimum_n_inclusive": MIN_COUNTY_N,
                       "positive_eligible_county_share_minimum_inclusive": MIN_POSITIVE_SHARE},
        "acceptance": {"pooled_r2_at_least_0_05": bool(pooled_pass),
                       "at_least_8_eligible_counties_n_ge_30": bool(min_counties_pass),
                       "positive_r2_in_at_least_75_percent_eligible_counties": bool(positive_share_pass),
                       "county_transport_gate_pass": gate},
        "provenance": {"official_geoid_region_sha256": official_sha,
                       "effective_count_artifact": artifact_meta,
                       "effective_count_report": {"path": str(EFFECTIVE_REPORT), "sha256": sha256_file(EFFECTIVE_REPORT)},
                       "pct_urban_strata_by_region": urban_meta,
                       "frozen_Mx4_artifact": {"path": str(mx4_path), "sha256": mx4_sha},
                       "frozen_S1_artifact": {"path": str(s1_path), "sha256": s1_sha},
                       "output_artifact": {"path": str(artifact_path_out), "sha256": hashlib.sha256(payload).hexdigest(),
                                           "rows": output.num_rows, "columns": output.column_names,
                                           "deterministic_serialization": True},
                       "script_sha256": sha256_file(Path(__file__).resolve())},
        "interpretation": "County-held-out transport met its preregistered pooled-R2, positive-county-share, and support gates; this remains a provided-feature transportability diagnostic only." if gate else
                         "County-held-out transport did not pass every preregistered pooled-R2, positive-county-share, and support gate; keep effective count descriptive/local and make no accuracy or causal claim.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", ""))
    parser.add_argument("--out", default="reports/poi-effective-count-county-holdout.json")
    parser.add_argument("--artifact", default=None)
    args = parser.parse_args()
    workspace = Path(args.workspace)
    if not workspace.is_dir():
        parser.error("COMPETITION_WORKSPACE must be an existing directory")
    artifact = Path(args.artifact) if args.artifact else workspace / "poi-effective-count-county-holdout/county-loco-predictions.parquet"
    report = run(workspace, ROOT / args.out, artifact)
    print(json.dumps({"scope": report["scope"], "measure_after": report["measure_after"],
                      "acceptance": report["acceptance"], "output_artifact": report["provenance"]["output_artifact"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
